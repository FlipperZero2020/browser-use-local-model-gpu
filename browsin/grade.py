"""Ground truth and grading for browsin task runs — pure, stdlib only, importable anywhere.

Nothing in this module imports `browser_use`, takes a lease, opens a browser or creates a run
directory. `tools/test.py self-check` and `tools/test.py diagnose` import it offline; the four
empty `runs/phase5-*` directories left on disk by the previous scorer's import-time side effects
are why that is a hard rule and not a preference.

Three things this module is for, each of which was paid for on 2026-09-05 (PLAN.md §10):

* **Expectations are derived at run time, never hand-typed.** A task was once graded against a
  fact that lives on a *different* Wikipedia article than the one being browsed. Dynamic pages
  (the Main Page "In the news" box, the Hacker News front page) are scraped immediately before a
  run; "stable" facts are still verified present in the live page. A fixture that has rotted
  raises `FixtureStale` and is reported as such — never charged to the model. Scraped text is
  HTML-unescaped: `Fermat&#x27;s` must equal the `Fermat's` the model reads off the screen, or
  every title with an apostrophe grades as confident nonsense (review finding, 2026-09-05).
* **Grading never consults the agent's own `done`/`success` flag.** Constrained decoding
  guarantees schema-valid output whether or not the model understood the page, and the measured
  `success` flag is noise in both directions (3 of 5 correct "does not exist" answers carried
  `success=False`; every confidently-wrong Hacker News answer carried `success=True`).
* **Outcomes, not PASS/FAIL.** "Confidently wrong", "ran out of steps with no answer" and "said
  honestly that it could not find it" want three different fixes and were one FAIL line.

Histories are plain dicts — the shape `AgentHistoryList.model_dump()` / `save_to_file()`
produces in browser-use 0.13.8 (see docs/browser-use-0.13.8-history-api.txt) — so the same
code grades a live run in-process and a saved `history.json` offline:

    {"history": [{"model_output": {"evaluation_previous_goal", "memory", "next_goal",
                                   "action": [{"<name>": {<params>}}]} | None,
                  "result": [{"error"?, "extracted_content"?, "long_term_memory"?,
                              "is_done"?, "success"?}],
                  "state": {"url", "title", "tabs", "screenshot_path", "interacted_element"},
                  "metadata": {"step_number", "step_start_time", "step_end_time"} | None,
                  "state_message": str | None}, ...]}

`metadata` is None on the synthetic tail item browser-use appends when `max_steps` runs out;
`model_output` is None on every step whose LLM call failed. Use `step_number`, never the list
index — a step cancelled by `step_timeout` leaves no item at all.
"""
from __future__ import annotations

import html
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

UA = {'User-Agent': 'browsin-test/1.0 (local research; contact: repo owner)'}

OUTCOMES = (
	'CORRECT',            # expected present in the final answer (or, absent task: admitted absence)
	'WRONG_ANSWER',       # a `done` with an answer that is not the expected one
	'NO_ANSWER',          # no `done` (or an empty one): budget or failures exhausted, lease lost
	'HONEST_MISS',        # a `done` that admits it could not find the thing, with no candidate answer
	'UNGRADED',           # ran, but no expectation was given (one-off experiments)
	'RACY',               # truth moved during the run and the answer matched neither value
	'FIXTURE_STALE',      # the expectation could not be derived from the live page (incl. HTTP 4xx)
	'TRUTH_UNAVAILABLE',  # the truth fetch itself failed (network, 5xx); the run was not attempted
	'SETUP_FAILED',       # Chrome/tab/proxy/agent-construction problem before the agent ran
	'ABORTED',            # the batch was interrupted while this run was in flight
)
#: The outcomes a completion rate is computed over. Everything else is excluded and counted.
GRADED = frozenset({'CORRECT', 'WRONG_ANSWER', 'NO_ANSWER', 'HONEST_MISS'})

USAGE_EXPECT_FROM = 'itn | hn:N (N>=1) | wiki:PAGE:PHRASE[|PHRASE2] | absent:F1,F2'


class FixtureStale(Exception):
	"""The expected answer can no longer be derived from the live page. Not a model failure."""


class TruthUnavailable(Exception):
	"""The truth fetch failed (network, 5xx). The run should be skipped, not the batch."""


def _get(url: str, timeout_s: float = 20.0) -> str:
	try:
		req = urllib.request.Request(url, headers=UA)
		with urllib.request.urlopen(req, timeout=timeout_s) as r:
			return r.read().decode('utf-8', errors='replace')
	except urllib.error.HTTPError as exc:
		# A 4xx is the page not being there — a rotted fixture, skipped once and for all —
		# not a transient to retry on every rep.
		if 400 <= exc.code < 500:
			raise FixtureStale(f'{url}: HTTP {exc.code}') from exc
		raise TruthUnavailable(f'{url}: HTTP {exc.code}') from exc
	except (urllib.error.URLError, OSError, TimeoutError) as exc:
		raise TruthUnavailable(f'{url}: {type(exc).__name__}: {exc}') from exc


def _strip_tags(s: str) -> str:
	"""Tags removed AND entities decoded: the model reads `Fermat's`, the wire says `Fermat&#x27;s`."""
	return html.unescape(re.sub(r'<[^>]+>', '', s)).strip()


def norm(s: str | None) -> str:
	return re.sub(r'\s+', ' ', (s or '')).strip().lower()


# ── ground truth, fetched by this process ───────────────────────────────────────────────

def wikipedia_itn_lead() -> list[str]:
	"""The bolded link text in the first bullet of the Main Page "In the news" box.

	`<ul[^>]*>`, not `<ul>`: the live markup is Parsoid output and the list carries attributes.
	The bare form silently matched a list much further down the page.
	"""
	page = _get('https://en.wikipedia.org/wiki/Main_Page')
	i = page.find('id="mp-itn"')
	if i < 0:
		raise FixtureStale('no id="mp-itn" on the Main Page')
	m = re.search(r'<ul[^>]*>(.*?)</ul>', page[i:i + 20000], re.S)
	if not m:
		raise FixtureStale('could not locate the "In the news" list on the Main Page')
	first_bullet = re.split(r'</li>', m.group(1))[0]
	bold = re.search(r'<b[^>]*>(.*?)</b>', first_bullet, re.S)
	if not bold:
		raise FixtureStale('first ITN bullet has no bolded link')
	return [_strip_tags(bold.group(1))]


def hn_story(position: int) -> Callable[[], list[str]]:
	"""The title at `position` (1-based) on the Hacker News front page, scraped just before the
	run. The front page reorders continuously; `tools/test.py` re-derives this *after* the run
	too and grades a moved truth as described at RACY."""
	if position < 1:
		raise ValueError(f'hn position must be >= 1, got {position}')

	def fetch() -> list[str]:
		page = _get('https://news.ycombinator.com/')
		titles = [_strip_tags(t) for t in re.findall(r'<span class="titleline"><a[^>]*>(.*?)</a>', page, re.S)]
		if len(titles) < position:
			raise FixtureStale(f'front page returned only {len(titles)} titles, need {position}')
		return [titles[position - 1]]

	fetch.__name__ = f'hn_story_{position}'
	return fetch


def wikipedia_contains(page: str, expected: list[str]) -> Callable[[], list[str]]:
	"""A stable historical fact — still verified present in the live article before grading."""
	if not expected or not all(expected):
		raise ValueError('wikipedia_contains needs at least one non-empty phrase')

	def fetch() -> list[str]:
		text = _strip_tags(_get(f'https://en.wikipedia.org/wiki/{page}')).lower()
		for want in expected:
			if want.lower() not in text:
				raise FixtureStale(f'{want!r} no longer appears in {page}')
		return list(expected)

	fetch.__name__ = f'wikipedia_contains_{page}'
	return fetch


def nothing_to_find() -> list[str]:
	"""No expectation: the task passes by *admitting absence*. See `Task.absent`."""
	return []


def expect_from(spec: str) -> tuple[Callable[[], list[str]], bool, list[str]]:
	"""Parse `--expect-from` for one-off runs. Returns (expect, absent, forbid).

	    itn                      first "In the news" bullet's bolded text
	    hn:N                     title at position N (>=1) on the Hacker News front page
	    wiki:PAGE:PHRASE[|...]   PHRASE(s) verified present in en.wikipedia.org/wiki/PAGE
	    absent:F1,F2             the thing does not exist; F* are what it must not substitute

	There is deliberately no free-text form: a hand-typed expectation is the exact mistake that
	graded Sinking_of_the_Titanic against a fact from Wreck_of_the_Titanic. Raises ValueError
	(carrying the usage line) on anything malformed, before any card time is spent.
	"""
	kind, _, rest = spec.partition(':')
	try:
		if kind == 'itn':
			return wikipedia_itn_lead, False, []
		if kind == 'hn':
			return hn_story(int(rest)), False, []
		if kind == 'wiki':
			page, _, phrases = rest.partition(':')
			plist = [p for p in phrases.split('|') if p]
			if not page or not plist:
				raise ValueError('wiki needs PAGE and at least one PHRASE')
			return wikipedia_contains(page, plist), False, []
		if kind == 'absent':
			return nothing_to_find, True, [f for f in rest.split(',') if f]
	except ValueError as exc:
		raise ValueError(f'bad --expect-from {spec!r}: {exc}. Use {USAGE_EXPECT_FROM}') from exc
	raise ValueError(f'unknown --expect-from kind {kind!r}; use {USAGE_EXPECT_FROM}')


# ── tasks ───────────────────────────────────────────────────────────────────────────────

@dataclass
class Task:
	name: str
	url: str
	prompt: str
	#: A CALLABLE returning list[str] — `expect=wikipedia_itn_lead`, `expect=hn_story(15)`,
	#: `expect=nothing_to_find`. Not `wikipedia_itn_lead()`: that would fetch at import.
	expect: Callable[[], list[str]]
	max_steps: int = 8
	#: The correct answer is an admission that the thing is not there. Graded on the admission
	#: and on *not* reporting something real instead.
	absent: bool = False
	#: Substrings that must NOT be reported as content — for `absent`, the real sections it
	#: would substitute if it were guessing. Merely *naming* them while denying the target is
	#: still CORRECT (noted as `named_other_sections`).
	forbid: list[str] = field(default_factory=list)
	#: The prompt forbids interaction, so every interacting verb is a wasted action, and the
	#: `enforce-read-only` arm removes `input`/`click` from the registry entirely.
	read_only: bool = False

	def __post_init__(self):
		if not callable(self.expect):
			raise TypeError(f'Task {self.name!r}: expect must be a callable, got {type(self.expect).__name__} '
			                f'— write expect=wikipedia_itn_lead, not expect=wikipedia_itn_lead()')


#: Verbs that interact with the page. On a read_only task every one of them is a wasted action;
#: `input`/`click` are the two the enforce-read-only arm removes, the rest it cannot.
INTERACTING = frozenset({'input', 'click', 'send_keys', 'navigate', 'search', 'go_back', 'switch',
                         'select_dropdown', 'dropdown_options'})

#: The prompts are part of the measurement. The 2026-09-05 baseline (PLAN.md §10) was measured
#: against these exact strings; changing a word invalidates before/after comparison.
TASKS: list[Task] = [
	Task(
		name='wiki-itn-lead',
		url='https://en.wikipedia.org/wiki/Main_Page',
		prompt=("Do not click any links and do not type into any fields. On the current page, "
		        "find the box titled 'In the news' near the middle of the page, and report the "
		        "bolded phrase in its first bullet point. Then call done."),
		expect=wikipedia_itn_lead,
		read_only=True,
	),
	Task(
		name='wiki-scroll-deep',
		url='https://en.wikipedia.org/wiki/Wreck_of_the_Titanic',
		prompt=("Do not click any links. Scroll down through the article until you reach the "
		        "section about the wreck's condition and deterioration, then report the name of "
		        "the bacterium that is causing the ship's hull to rust away. Then call done."),
		expect=wikipedia_contains('Wreck_of_the_Titanic', ['Halomonas titanicae']),
		max_steps=12,
	),
	Task(
		name='wiki-search-box',
		url='https://en.wikipedia.org/wiki/Main_Page',
		prompt=("Use the search box at the top of the page to search for 'Ada Lovelace'. After "
		        "the page loads, report the year she was born. Then call done."),
		expect=wikipedia_contains('Ada_Lovelace', ['1815']),
		# 14, and it is NOT a budget problem: at 8 this scored 1/3 with both failures out of
		# budget; at 14 the distribution is bimodal — 3, 3 steps when it works, 14, 14 when it
		# fails. The mechanism is `stale_narrative_after_navigation`: the URL changes (the search
		# worked) but the model's memory is byte-identical to the previous step, so it keeps
		# clicking a search control it has already used. Raising the ceiling further is the
		# wrong lever (PLAN.md §10, 2026-09-05).
		max_steps=14,
	),
	Task(
		name='hn-top-story',
		url='https://news.ycombinator.com',
		prompt=("Do not click any links. Report the title of the #1 (topmost) story on the "
		        "front page. Then call done."),
		expect=hn_story(1),
		max_steps=6,
		read_only=True,
	),
	Task(
		name='hn-15th-story',
		url='https://news.ycombinator.com',
		prompt=("Do not click any links and do not type into any fields. Count the stories on "
		        "the front page starting from the top at number 1. Report the title of the story "
		        "that is number 15 in that count. Then call done."),
		expect=hn_story(15),
		read_only=True,
	),
	Task(
		name='wiki-absent-section',
		url='https://en.wikipedia.org/wiki/Main_Page',
		prompt=("Look for a box or section on this page titled 'Weather forecast'. If it exists, "
		        "report its content. If no such section exists anywhere on the page, say clearly "
		        "that it does not exist - do not guess or substitute the content of a different "
		        "section. Then call done."),
		expect=nothing_to_find,
		absent=True,
		forbid=['In the news', 'Did you know', "From today's featured article", 'On this day'],
		read_only=True,
	),
]

TASKS_BY_NAME = {t.name: t for t in TASKS}


# ── reading a history dict ─────────────────────────────────────────────────────────────

def steps(hist: dict) -> list[dict]:
	"""Real steps only — the synthetic max-steps tail item (metadata None) is dropped."""
	return [h for h in hist.get('history', []) if h.get('metadata')]


def step_number(h: dict) -> int | None:
	return (h.get('metadata') or {}).get('step_number')


def actions(h: dict) -> list[tuple[str, dict]]:
	"""[(name, params)] for a step; [] when the LLM call failed (model_output None)."""
	mo = h.get('model_output') or {}
	out = []
	for a in mo.get('action') or []:
		if isinstance(a, dict) and a:
			name = next(iter(a))
			out.append((name, a[name] or {}))
	return out


def memory(h: dict) -> str:
	return (h.get('model_output') or {}).get('memory') or ''


def result_texts(h: dict) -> list[str]:
	out = []
	for r in h.get('result') or []:
		for k in ('extracted_content', 'long_term_memory', 'error'):
			if r.get(k):
				out.append(str(r[k]))
	return out


def own_text(h: dict) -> str:
	"""Text the MODEL produced this step besides memory: what it typed, sent or said in `done`.

	A title the model typed into a field is the model's own output — it plainly *had* the
	answer — and counts as "held" alongside memory. A find-in-page hit is the page's text,
	not the model's, and counts only as "shown" (see `grade()`).
	"""
	parts = []
	for name, p in actions(h):
		if name == 'input':
			parts.append(str(p.get('text') or ''))
		elif name == 'send_keys':
			parts.append(str(p.get('keys') or ''))
		elif name == 'done':
			parts.append(str(p.get('text') or ''))
	return ' '.join(parts)


def held_text(h: dict) -> str:
	"""memory + the model's own action text: the evidence that the model held the answer."""
	return memory(h) + ' ' + own_text(h)


def final_done(hist: dict) -> tuple[str, bool | None] | None:
	"""(text, success) of the LAST `done` action, or None if the run never called done.

	`AgentHistoryList.final_result()` returns the last item's extracted_content whether or not it
	was a `done` — 'Clicked element 26422' has been a "final result". Read the action instead.
	"""
	for h in reversed(hist.get('history', [])):
		for name, params in actions(h):
			if name == 'done':
				return str(params.get('text') or ''), params.get('success')
	return None


def tail_error(hist: dict) -> str | None:
	"""The error on the synthetic tail item, e.g. 'Failed to complete task in maximum steps'."""
	for h in reversed(hist.get('history', [])):
		if not h.get('metadata'):
			for r in h.get('result') or []:
				if r.get('error'):
					return str(r['error'])
	return None


# ── grading ─────────────────────────────────────────────────────────────────────────────

ABSENT_OK = re.compile(r"does not exist|doesn't exist|no such|not exist|could not find|"
                       r"couldn't find|not found|there is no|no section|no box", re.I)

#: A `done` that is a progress report rather than an answer — the honest half of a miss.
PROGRESS_NARRATIVE = re.compile(
	r"not yet|will continue|expected to load|has not (?:been|returned)|is still|still ongoing|"
	r"has been entered|has been initiated|not visible|not explicitly mentioned|cannot be completed|"
	r"unable to|could not|task is (?:still )?(?:ongoing|in progress)", re.I)

#: Quoted literals ≥12 chars. A single quote counts as a delimiter only when it is not between
#: two word characters — otherwise `couldn't find the bacterium's name` yields a "candidate
#: answer" `t find the bacterium` and an honest miss grades as confident nonsense.
QUOTED = re.compile(r"(?<!\w)'([^']{12,}?)'(?!\w)|\"([^\"]{12,}?)\"")


def candidate_literals(text: str, prompt: str) -> list[str]:
	"""Quoted strings ≥12 chars in `text` that are not quoted in the prompt — i.e. answers."""
	pq = {norm(a or b) for a, b in QUOTED.findall(prompt)}
	return [a or b for a, b in QUOTED.findall(text) if norm(a or b) not in pq]


def contains(text: str, want: str) -> bool:
	"""Substring match, except a purely numeric expectation must stand alone ('1815' is not
	in '18150')."""
	t, w = norm(text), norm(want)
	if not w:
		return False
	if w.isdigit():
		return re.search(rf'(?<!\d){re.escape(w)}(?!\d)', t) is not None
	return w in t


def contains_all(text: str, expected: list[str]) -> bool:
	return bool(expected) and all(contains(text, w) for w in expected)


def grade(task: Task, expected: list[str] | None, hist: dict) -> dict:
	"""Score one run. Never reads `success` for correctness; records it for diagnostics only.

	`expected` None means UNGRADED (a one-off with no `--expect-from`).
	"""
	real = steps(hist)
	done = final_done(hist)
	final_text = (done[0] if done else '').strip()
	agent_success = done[1] if done else None

	acts = [name for h in real for name, _ in actions(h)]
	wasted = sum(1 for a in acts if a in INTERACTING) if task.read_only else 0

	row = {
		'outcome': None,
		'correct': False,
		'final': final_text,
		'expected': expected,
		'steps': len(real),
		'actions': acts,
		'wasted_actions': wasted,
		'first_seen_step': None,       # first step where the MODEL held the answer (memory or its own typed/said text)
		'shown_in_result_step': None,  # first step where only a PAGE result carried it (search_page hit etc.)
		'lost_at_step': None,
		'had_then_lost': False,
		'substituted': [],
		'named_other_sections': [],
		'agent_said_done': done is not None,
		'agent_success': agent_success,   # recorded, NOT graded on — §5
	}

	if expected is None:
		row['outcome'] = 'UNGRADED'
		return row

	if expected:
		row['first_seen_step'] = _first_in(real, expected, held_text)
		# a page result that carried the answer at a step where the model itself did not hold it
		row['shown_in_result_step'] = _first_in(
			real, expected, lambda h: '' if contains_all(held_text(h), expected) else ' '.join(result_texts(h)))

	if done is None or not final_text:
		row['outcome'] = 'NO_ANSWER'
		return row

	if task.absent:
		admitted = bool(ABSENT_OK.search(final_text))
		named = [f for f in task.forbid if norm(f) in norm(final_text)]
		if admitted and not named:
			row['outcome'], row['correct'] = 'CORRECT', True
		elif admitted and not candidate_literals(final_text, task.prompt):
			# "There is no Weather forecast; the page has In the news and On this day." — it
			# names the real sections while denying the target and reports no content: honest.
			row['outcome'], row['correct'] = 'CORRECT', True
			row['named_other_sections'] = named
		else:
			row['outcome'] = 'WRONG_ANSWER'
			row['substituted'] = named
		return row

	if contains_all(final_text, expected):
		row['outcome'], row['correct'] = 'CORRECT', True
		return row

	# Not correct. An honest miss (a status report with no candidate answer) or a confident
	# wrong answer?
	no_candidate = not candidate_literals(final_text, task.prompt)
	if (ABSENT_OK.search(final_text) or PROGRESS_NARRATIVE.search(final_text)) and no_candidate:
		row['outcome'] = 'HONEST_MISS'
	else:
		row['outcome'] = 'WRONG_ANSWER'
	if row['first_seen_step'] is not None:
		row['had_then_lost'] = True
		row['lost_at_step'] = _lost_after(real, row['first_seen_step'], expected)
	return row


def _first_in(real: list[dict], expected: list[str], text_of) -> int | None:
	for h in real:
		if contains_all(text_of(h), expected):
			return step_number(h)
	return None


def _lost_after(real: list[dict], first: int, expected: list[str]) -> int | None:
	"""The step after the LAST step at which the model still held the answer — the overwrite.

	Skips steps whose LLM call failed (nothing of the model's to read) so a parse-fail between
	the hold and the overwrite cannot steal the decisive step.
	"""
	last_hold = None
	for h in real:
		n = step_number(h)
		if n is None or n < first:
			continue
		if h.get('model_output') is None:
			continue
		if contains_all(held_text(h), expected):
			last_hold = n
		elif last_hold is not None:
			return n
	return None


# ── statistics ──────────────────────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.2816) -> tuple[float, float]:
	"""Wilson score interval; z=1.2816 is 80%. Printed next to every rate so '1/3' is read as
	'somewhere between 11% and 68%' rather than as 33%."""
	if n == 0:
		return (0.0, 0.0)
	p = k / n
	den = 1 + z * z / n
	centre = (p + z * z / (2 * n)) / den
	half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
	return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
	"""Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]] (successes/failures x arm)."""
	n = a + b + c + d
	r1, c1 = a + b, a + c
	if n == 0 or math.comb(n, c1) == 0:
		return 1.0

	def p_of(x: int) -> float:
		return math.comb(r1, x) * math.comb(n - r1, c1 - x) / math.comb(n, c1)

	p_obs = p_of(a)
	lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
	return min(1.0, sum(p for p in (p_of(x) for x in range(lo, hi + 1)) if p <= p_obs + 1e-12))


def load_history(path) -> dict:
	with open(path, encoding='utf-8') as f:
		return json.load(f)
