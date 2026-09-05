#!/usr/bin/env python3
"""Phase 5: real tasks, externally verified, scored by machine.

    export WARDEN_URL=http://192.168.1.111:8130
    export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
    venv/bin/python tools/phase5_gate.py --reps 3

§5 Phase 5 asks for "3-5 fixed tasks with **machine-checkable** success conditions ... Never
the agent's own `done` action", and §10 keeps re-learning that a single measurement is not a
distribution. `tools/browse.py` is the demonstration path and cannot answer either point: it
runs one task once and prints what the model said. This runs the whole table N times and
grades every run against ground truth **this process fetched itself**.

Three design points, each of which exists because eyeballing logs got it wrong first:

* **Every expectation is derived at run time, never hand-typed.** The 2026-09-05 checklist
  wasted a run grading `Sinking_of_the_Titanic` against a fact that lives on a *different*
  article. Static fixtures here are still *verified present* in the live page before the
  browser run is graded against them, so a rotted fixture reports as `FIXTURE-STALE` rather
  than as a model failure. Dynamic pages (the ITN box, the Hacker News front page) are
  scraped immediately before each run, so the expectation cannot go stale at all.

* **`had_then_lost` is the detector this gate exists for.** The one hard failure on
  2026-09-05 was not a reading failure: on Hacker News the model put the *correct* answer in
  `model_output.memory` at step 1, took an unnecessary `input` action, then replaced it at
  step 2 with a real-but-wrong story and called `done(success=True)`. Nothing about that run
  is distinguishable from a correct one without ground truth — but "the expected string
  appeared in some step's memory and is absent from the final answer" is mechanically
  checkable, and it separates "never understood the page" from "understood it and then threw
  it away". Those two want completely different fixes.

* **One lease for the whole batch, a fresh Chrome per run.** A cold acquire is ~18 s and a
  warm one is 0.1 s, so leasing per run would spend more card time on acquisition than on
  work. Chrome *is* restarted per run, through `B.stop()` / `B.start(url)`, because
  `tools/browse.py`'s attach path adopts whatever tab is already open on the same host and a
  previous run's leftover tab silently contaminates the next one (§10, 2026-09-05, open).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
RUN_ID = time.strftime('%Y%m%d-%H%M%S')
RUN_DIR = REPO / 'runs' / f'phase5-{RUN_ID}'
SCRATCH = RUN_DIR / 'tmp'
SCRATCH.mkdir(parents=True, exist_ok=True)
(SCRATCH / 'downloads').mkdir(exist_ok=True)
os.environ['TMPDIR'] = str(SCRATCH)
tempfile.tempdir = str(SCRATCH)
os.environ['BROWSER_USE_CONFIG_DIR'] = str(RUN_DIR / 'config')

sys.path.insert(0, str(REPO))

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import urllib.request  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Callable  # noqa: E402

import browsin  # noqa: E402,F401  — zero-cloud env, before browser_use
from browsin import browser as B  # noqa: E402
from browsin.agent import build_agent, build_llm, build_session, build_tools  # noqa: E402
from browsin.interlock import Interlock, card_preflight  # noqa: E402
from browsin.lease import hold  # noqa: E402
from browsin.proxy import Proxy  # noqa: E402

WORKLOAD = 'ollama:qwen2.5vl-32k:7b'
MODEL_TAG = 'qwen2.5vl-32k:7b'
NUM_CTX = 32768

UA = {'User-Agent': 'browsin-phase5-gate/1.0 (local research; contact: repo owner)'}


class FixtureStale(Exception):
	"""The expected answer is no longer present in the live page. Not a model failure."""


def _get(url: str, timeout_s: float = 20.0) -> str:
	req = urllib.request.Request(url, headers=UA)
	with urllib.request.urlopen(req, timeout=timeout_s) as r:
		return r.read().decode('utf-8', errors='replace')


def _strip_tags(html: str) -> str:
	return re.sub(r'<[^>]+>', '', html)


# ── ground truth, fetched by this process, never hand-typed ────────────────────────────

def wikipedia_itn_lead() -> list[str]:
	"""The bolded link text in the first bullet of the Main Page "In the news" box.

	Scraped rather than hardcoded because it changes daily — the whole point of §5's
	"machine-checkable" is that the expectation cannot be older than the run it grades.
	"""
	html = _get('https://en.wikipedia.org/wiki/Main_Page')
	m = re.search(r'id="mp-itn".*?<ul>(.*?)</ul>', html, re.S)
	if not m:
		raise FixtureStale('could not locate the "In the news" list on the Main Page')
	first_bullet = re.split(r'</li>', m.group(1))[0]
	bold = re.search(r'<b>\s*<a[^>]*>(.*?)</a>\s*</b>', first_bullet, re.S)
	if not bold:
		raise FixtureStale('first ITN bullet has no bolded link')
	return [_strip_tags(bold.group(1)).strip()]


def hn_story(position: int) -> Callable[[], list[str]]:
	"""The title at `position` on the Hacker News front page, scraped just before the run."""

	def fetch() -> list[str]:
		html = _get('https://news.ycombinator.com/')
		titles = [_strip_tags(t).strip()
		          for t in re.findall(r'<span class="titleline"><a[^>]*>(.*?)</a>', html, re.S)]
		if len(titles) < position:
			raise FixtureStale(f'front page returned only {len(titles)} titles, need {position}')
		return [titles[position - 1]]

	return fetch


def wikipedia_contains(page: str, expected: list[str]) -> Callable[[], list[str]]:
	"""A stable historical fact — still verified present in the live article before grading.

	This is the guard the 2026-09-05 checklist did not have: a fixture that has silently
	stopped being true must report as stale, not as the model getting it wrong.
	"""

	def fetch() -> list[str]:
		text = _strip_tags(_get(f'https://en.wikipedia.org/wiki/{page}'))
		for want in expected:
			if want.lower() not in text.lower():
				raise FixtureStale(f'{want!r} no longer appears in {page}')
		return expected

	return fetch


def nothing_to_find() -> list[str]:
	"""No expectation: this task passes by *admitting absence*. See `Task.absent`."""
	return []


ABSENT_OK = re.compile(r"does not exist|doesn't exist|no such|not exist|could not find|"
                       r"couldn't find|not found|there is no|no section|no box", re.I)


@dataclass
class Task:
	name: str
	url: str
	prompt: str
	expect: Callable[[], list[str]]
	max_steps: int = 8
	#: True for the honesty check: the correct answer is an admission that the thing is not
	#: there. Graded on the admission, and on *not* naming something real instead.
	absent: bool = False
	#: Substrings that must NOT appear in the final answer — for `absent`, the real sections
	#: it would substitute if it were guessing.
	forbid: list[str] = field(default_factory=list)
	#: The task text forbids interaction, so any click/input is a wasted action.
	read_only: bool = False


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


def _norm(s: str) -> str:
	return re.sub(r'\s+', ' ', (s or '')).strip().lower()


def grade(task: Task, expected: list[str], history) -> dict:
	"""Score one run. Never consults the agent's own `done`/`success` flag — §5's rule."""
	final = history.final_result() or ''
	steps = history.history
	memories = [(h.model_output.memory or '') for h in steps if h.model_output]
	actions: list[str] = []
	for h in steps:
		for a in (h.model_output.action if h.model_output else []):
			actions.append(list(json.loads(a.model_dump_json(exclude_none=True)))[0])

	nf, nmem = _norm(final), [_norm(m) for m in memories]

	if task.absent:
		admitted = bool(ABSENT_OK.search(final))
		substituted = [f for f in task.forbid if _norm(f) in nf]
		correct = admitted and not substituted
		found_in_memory = False
	else:
		correct = all(_norm(w) in nf for w in expected)
		# The detector this gate exists for: did the answer pass through `memory` and then
		# get dropped from the final answer?
		found_in_memory = any(all(_norm(w) in m for w in expected) for m in nmem)
		substituted = []

	wasted = sum(1 for a in actions if a in ('input', 'click')) if task.read_only else 0

	return {
		'correct': correct,
		'had_then_lost': (not correct) and found_in_memory,
		'steps': len(steps),
		'actions': actions,
		'wasted_actions': wasted,
		'final': final.strip(),
		'expected': expected,
		'substituted': substituted,
		'agent_said_done': history.is_done(),
	}


async def run_one(task: Task, expected: list[str], card, proxy_url: str, run_dir: pathlib.Path):
	B.stop()  # a fresh Chrome per run — the attach path would adopt a stale tab (§10)
	await asyncio.sleep(2)
	chrome = B.start(task.url)

	llm = build_llm(host=proxy_url, model=MODEL_TAG, num_ctx=NUM_CTX)
	session = build_session(cdp_url=chrome.cdp_url, downloads_path=str(SCRATCH / 'downloads'))
	agent = build_agent(task=task.prompt, llm=llm, browser_session=session,
	                    tools=build_tools(),
	                    save_conversation_path=str(run_dir / 'conversation'))
	await session.start()
	try:
		tabs = await session.get_tabs()
		host = task.url.split('/')[2]
		chosen = next((t for t in tabs if host in t.url), None)
		if chosen is not None:
			from browser_use.browser.events import SwitchTabEvent
			await session.event_bus.dispatch(SwitchTabEvent(target_id=chosen.target_id))
		t0 = time.monotonic()
		history = await agent.run(max_steps=task.max_steps)
		elapsed = time.monotonic() - t0
	finally:
		try:
			await asyncio.wait_for(session.stop(), timeout=30)
		except Exception:
			pass

	row = grade(task, expected, history)
	row['seconds'] = round(elapsed, 1)
	return row


async def main() -> int:
	ap = argparse.ArgumentParser()
	ap.add_argument('--reps', type=int, default=3, help='repetitions per task')
	ap.add_argument('--only', action='append', help='task name(s) to run; repeatable')
	ap.add_argument('--evict', action='store_true')
	args = ap.parse_args()

	tasks = [t for t in TASKS if not args.only or t.name in args.only]
	if not tasks:
		print(f'no task matches {args.only}; known: {[t.name for t in TASKS]}')
		return 2

	try:
		await card_preflight(evict=args.evict)
	except Interlock as exc:
		print(f'\nREFUSED TO START\n  {exc}\n')
		return 2

	results: dict[str, list[dict]] = {t.name: [] for t in tasks}
	stale: dict[str, str] = {}

	t0 = time.monotonic()
	async with hold(WORKLOAD, reason='phase5', num_ctx=NUM_CTX, ttl_s=180) as card:
		print(f'lease granted in {time.monotonic() - t0:.1f}s  served num_ctx={card.num_ctx}',
		      flush=True)
		with Proxy(card.endpoint, RUN_DIR / 'proxy.jsonl') as proxy:
			for rep in range(1, args.reps + 1):
				for task in tasks:
					if task.name in stale:
						continue
					try:
						expected = task.expect()
					except (FixtureStale, Exception) as exc:  # noqa: B014 — report, never grade
						if isinstance(exc, FixtureStale):
							stale[task.name] = str(exc)
							print(f'[{task.name} rep{rep}] FIXTURE-STALE: {exc}', flush=True)
							continue
						raise
					run_dir = RUN_DIR / f'{task.name}-rep{rep}'
					row = await run_one(task, expected, card, proxy.url, run_dir)
					results[task.name].append(row)
					mark = 'PASS' if row['correct'] else 'FAIL'
					extra = ''
					if row['had_then_lost']:
						extra = '  << HAD-THEN-LOST: correct answer was in memory, dropped'
					if row['substituted']:
						extra = f"  << SUBSTITUTED {row['substituted']}"
					print(f'[{task.name} rep{rep}] {mark} {row["steps"]}st '
					      f'{row["seconds"]}s waste={row["wasted_actions"]} '
					      f'-> {row["final"][:80]!r}{extra}', flush=True)

	# ── report ────────────────────────────────────────────────────────────────────────
	print('\n' + '=' * 78)
	print('PHASE 5 — task completion, graded against independently fetched ground truth')
	print('=' * 78)
	total = passed = lost = 0
	for task in tasks:
		rows = results[task.name]
		if task.name in stale:
			print(f'  {task.name:<20} FIXTURE-STALE — {stale[task.name]}')
			continue
		if not rows:
			continue
		ok = sum(1 for r in rows if r['correct'])
		hl = sum(1 for r in rows if r['had_then_lost'])
		steps = sum(r['steps'] for r in rows) / len(rows)
		waste = sum(r['wasted_actions'] for r in rows) / len(rows)
		total += len(rows)
		passed += ok
		lost += hl
		flag = f'  had-then-lost x{hl}' if hl else ''
		print(f'  {task.name:<20} {ok}/{len(rows)} correct   '
		      f'avg {steps:.1f} steps, {waste:.1f} wasted actions{flag}')

	if total:
		print(f'\n  completion rate: {passed}/{total} = {100 * passed / total:.0f}%')
		print(f'  of the {total - passed} failure(s), {lost} had the correct answer in memory '
		      f'and dropped it')
	print(f'\n  run dir: {RUN_DIR}')
	print('  NOTE: graded on ground truth, never on the agent\'s own done/success flag.')

	(RUN_DIR / 'results.json').write_text(json.dumps(
		{'reps': args.reps, 'stale': stale, 'results': results}, indent=1))
	return 0 if total and passed == total else 1


if __name__ == '__main__':
	code = asyncio.run(main())
	sys.stdout.flush()
	os._exit(code)
