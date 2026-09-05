"""Automatic diagnosis of a browsin run — pure, stdlib only, works on saved `history.json`.

The valuable part of the 2026-09-05 work was never the pass rate. It was reading the per-step
log of a failed run and seeing the mechanism: the model's memory said "scrolled down" while the
action sent `down=False`; the URL changed but the memory was byte-identical to the previous
step; the correct answer sat in memory at step 2 and a different, real-but-wrong title replaced
it at step 3 after a stray `input` scrolled row 1 off-screen. Every real fix came from that,
and none of it is visible in a PASS/FAIL line.

This module does that reading mechanically. The detectors come from a census of 64 real runs
(docs/failure-census-2026-09-05.txt: 21 patterns, each with the run dirs and step numbers it
occurred in). Three are instrumentation rather than census entries — `invented_element_index`,
`stuck_narrative` and `aborted_llm_calls` — and are labelled so; `slow_llm_call` is a counter,
not a mechanism. `tools/test.py self-check` gives every detector a positive that must fire and
a negative that must stay silent, because a detector that cannot fail is the eighth
unfalsifiable gate this project would have written.

Rows are built from the history object (or its saved JSON), never from `conversation_*.txt`:
a step whose output failed to parse has no conversation file — step 1 of every had-then-lost
run so far — but it does have a screenshot and a result error. The browser-state wording the
viewport reader parses is browser-use 0.13.8's exact `<page_info>N.N pages above, N.N pages
below` (agent/prompts.py), verified against a real state_message; an earlier draft matched
"pixels above", which the library never emits.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from browsin.grade import (
	GRADED, INTERACTING, Task, actions, contains_all, fisher_two_sided, held_text, memory, norm,
	result_texts, step_number, steps, tail_error, wilson,
)

#: Patterns that are outcomes or counters rather than fixable mechanisms — never the "most
#: frequent pattern" a NEXT footer should chase. Shared by rollup() and next_footer() so the two
#: lines of one summary cannot name different things.
NOT_A_TARGET = frozenset({'scroll_pages_gt1', 'done_only_when_forced', 'steps_after_first_seen',
                          'honest_miss', 'slow_llm_call'})


# ── small readers ───────────────────────────────────────────────────────────────────────

def url_nofrag(u: str | None) -> str:
	if not u:
		return ''
	p = urlsplit(u)
	return p._replace(fragment='').geturl()


def short_url(u: str | None, width: int = 34) -> str:
	if not u:
		return '-'
	p = urlsplit(u)
	s = (p.netloc.removeprefix('www.') + p.path + (('?' + p.query) if p.query else ''))
	if p.fragment:
		s += '#' + p.fragment[:12]
	return s if len(s) <= width else s[:width - 1] + '…'


PAGE_INFO = re.compile(r'<page_info>\s*([\d.]+) pages above, ([\d.]+) pages below')


def page_pos(h: dict) -> tuple[float, float] | None:
	"""(pages_above, pages_below) from the browser_state the model saw, or None if absent."""
	m = PAGE_INFO.search(h.get('state_message') or '')
	if not m:
		return None
	try:
		return float(m.group(1)), float(m.group(2))
	except ValueError:
		return None


def viewport(h: dict) -> str:
	"""TOP / MID / BOTTOM / ALL / ? from the real `<page_info>` numbers. A blank first state
	('empty page', no element list) still carries page_info, so it classifies like any other."""
	pos = page_pos(h)
	if pos is None:
		return '?'
	above, below = pos
	if above == 0 and below == 0:
		return 'ALL'
	if above == 0:
		return 'TOP'
	if below == 0:
		return 'BOTTOM'
	return 'MID'


def eval_text(h: dict) -> str:
	return (h.get('model_output') or {}).get('evaluation_previous_goal') or ''


def next_goal(h: dict) -> str:
	return (h.get('model_output') or {}).get('next_goal') or ''


def first_error(h: dict) -> str:
	for r in h.get('result') or []:
		if r.get('error'):
			return str(r['error'])
	return ''


def interacted(h: dict, k: int) -> dict | None:
	els = (h.get('state') or {}).get('interacted_element') or []
	return els[k] if k < len(els) else None


def element_label(el: dict | None) -> str:
	if not el:
		return ''
	tag = el.get('node_name') or el.get('tag_name') or ''
	attrs = el.get('attributes') or {}
	text = el.get('ax_name') or el.get('node_value') or attrs.get('aria-label') or attrs.get('title') or attrs.get('href') or ''
	text = re.sub(r'\s+', ' ', str(text)).strip()
	return f'<{tag.lower()}>{text[:28]}' if tag else text[:28]


def action_label(name: str, params: dict, width: int = 44) -> str:
	if name == 'scroll':
		s = f"scroll {'down' if params.get('down', True) else 'UP'}"
		if _pages(params) != 1.0:
			s += f" x{params.get('pages')}"
		return s
	if name == 'input':
		return f"input[{params.get('index')}] \"{str(params.get('text', ''))[:width - 12]}\""
	if name == 'click':
		return f"click[{params.get('index')}]"
	if name == 'done':
		return f"done \"{str(params.get('text', ''))[:width - 16]}\" success={params.get('success')}"
	if name == 'navigate':
		return f"navigate {str(params.get('url', ''))[:width - 9]}"
	if name == 'wait':
		return f"wait {params.get('seconds', 3)}s"
	if name == 'search_page':
		return f"search_page \"{str(params.get('pattern', ''))[:width - 14]}\""
	if name == 'send_keys':
		return f"send_keys \"{str(params.get('keys', ''))[:width - 12]}\""
	return (name + ' ' + ', '.join(f'{k}={v}' for k, v in params.items()))[:width]


def _pages(params: dict) -> float:
	try:
		return float(params.get('pages') if params.get('pages') is not None else 1.0)
	except (TypeError, ValueError):
		return 1.0


WORD_DOWN = re.compile(r'\bdown\b')
WORD_UP = re.compile(r'\b(?:up|upward|upwards|back|backward|backwards)\b')


# ── detectors ───────────────────────────────────────────────────────────────────────────
#
# Each returns None when silent, or a short evidence dict when it fires. Names match the
# census (docs/failure-census-2026-09-05.txt) so a rollup line can be read back against it.

def _consecutive_runs(items: list, key) -> list[tuple[object, int, int]]:
	"""[(key_value, start_idx, length)] for runs of equal key over `items`."""
	out, i = [], 0
	while i < len(items):
		k = key(items[i])
		j = i
		while j + 1 < len(items) and key(items[j + 1]) == k:
			j += 1
		out.append((k, i, j - i + 1))
		i = j + 1
	return out


def classify_llm_failure(err: str) -> str:
	"""empty_action | malformed_action | llm_timeout | parse_fail, from the result error text.

	Signatures (docs/browser-use-0.13.8-history-api.txt): a retried-and-still-empty action list
	says 'ActionModelUnion … PydanticUndefined'; an `[{}]` action fails with 'Field required …
	input_value={}'; a non-empty but malformed action also says 'Field required' (for the members
	it is not) but with a real input_value; a truncated body is 'Invalid JSON'.
	"""
	if 'ActionModelUnion' in err or 'forgot to return an action' in err:
		return 'empty_action'
	if 'Field required' in err:
		return 'empty_action' if 'input_value={}' in err else 'malformed_action'
	if 'timed out' in err:
		return 'llm_timeout'
	return 'parse_fail'


def detect(task: Task, expected: list[str] | None, hist: dict, row: dict,
           proxy_records: list[dict] | None = None, max_steps: int | None = None) -> dict:
	real = steps(hist)
	found: dict[str, dict] = {}
	prompt_n = norm(task.prompt)
	max_steps = max_steps or task.max_steps

	# — the detector this whole thing exists for —
	if row.get('had_then_lost'):
		found['had_then_lost'] = {'first': row.get('first_seen_step'), 'lost': row.get('lost_at_step')}
	elif row.get('shown_in_result_step') is not None and row.get('first_seen_step') is None \
	     and row.get('outcome') in ('WRONG_ANSWER', 'HONEST_MISS', 'NO_ANSWER'):
		found['shown_but_never_held'] = {'step': row['shown_in_result_step']}

	# — interaction on a read-only task, and whether it typed the answer —
	if task.read_only:
		strays, typed_answer = 0, False
		for h in real:
			mem = norm(memory(h))
			for name, p in actions(h):
				if name in INTERACTING:
					strays += 1
					typed = norm(p.get('text') if name == 'input' else p.get('keys') if name == 'send_keys' else None)
					if typed and typed in mem:
						typed_answer = True
		if strays:
			found['stray_input_on_read_only'] = {'n': strays, 'typed_the_answer': typed_answer}
	else:
		# answer retyped into a field even when typing is allowed (the 83-occurrence habit)
		retyped = 0
		for h in real:
			for name, p in actions(h):
				t = norm(p.get('text')) if name == 'input' else ''
				if t and t not in prompt_n and t in norm(memory(h)):
					retyped += 1
		if retyped:
			found['answer_retyped_into_input'] = {'n': retyped}

	# — viewport moved right after an input, with no scroll (the overwrite mechanism):
	#   the census threshold is a jump of >= 0.3 pages on the same URL —
	for a, b in zip(real, real[1:]):
		names = [n for n, _ in actions(a)]
		if 'input' in names and 'scroll' not in names:
			pa, pb = page_pos(a), page_pos(b)
			if pa and pb and abs(pb[0] - pa[0]) >= 0.3 and url_nofrag(a['state'].get('url')) == url_nofrag(b['state'].get('url')):
				found['viewport_moved_after_input'] = {'step': step_number(a), 'from': f'{pa[0]:.1f}↑', 'to': f'{pb[0]:.1f}↑'}
				break

	# — URL changed on a read-only task (click drift / input side-effect navigation) —
	if task.read_only:
		for a, b in zip(real, real[1:]):
			ua, ub = a['state'].get('url') or '', b['state'].get('url') or ''
			if ua and ub and url_nofrag(ua) != url_nofrag(ub):
				found['url_changed_on_read_only'] = {'step': step_number(a), 'from': short_url(ua), 'to': short_url(ub)}
				break
	# — an input alone that navigated (side effect), any task —
	for a, b in zip(real, real[1:]):
		names = [n for n, _ in actions(a)]
		ua, ub = a['state'].get('url') or '', b['state'].get('url') or ''
		if names == ['input'] and ua and ub and ua != ub:
			found.setdefault('input_side_effect_navigation', {'step': step_number(a), 'to': short_url(ub)})
			break

	# — repeated identical action (same name + params) ≥3 in a row; scrolling is exempt —
	sig = [tuple((n, tuple(sorted((k, str(v)) for k, v in p.items()))) for n, p in actions(h)) for h in real]
	for k, start, length in _consecutive_runs(sig, lambda s: s):
		if length >= 3 and k and k[0][0] != 'scroll':
			found['repeated_action'] = {'action': k[0][0], 'n': length, 'from_step': step_number(real[start])}
			break

	# — stale narrative: URL changed but memory + eval identical to the previous step —
	stale = []
	for a, b in zip(real, real[1:]):
		if url_nofrag(a['state'].get('url')) != url_nofrag(b['state'].get('url')) and memory(b) and \
		   norm(memory(a)) == norm(memory(b)) and norm(eval_text(a)) == norm(eval_text(b)):
			stale.append(step_number(b))
	if stale:
		found['stale_narrative_after_navigation'] = {'steps': stale}
	# — stuck narrative (instrumentation): identical memory ≥4 consecutive steps —
	for k, start, length in _consecutive_runs([norm(memory(h)) for h in real], lambda s: s):
		if length >= 4 and k:
			found['stuck_narrative'] = {'n': length, 'from_step': step_number(real[start])}
			break

	# — stale element index re-issued after "not available" (within the next two steps) —
	for i, a in enumerate(real):
		m = re.search(r'Element index (\d+) not available', ' '.join(result_texts(a)))
		if m:
			idx = m.group(1)
			for b in real[i + 1:i + 3]:
				if any(str(p.get('index')) == idx for _, p in actions(b)):
					found['stale_element_index_retry'] = {'index': int(idx), 'step': step_number(b)}
					break
		if 'stale_element_index_retry' in found:
			break

	# — invented element index (instrumentation): an index absent from the DOM shown —
	invented = 0
	for h in real:
		for k, (name, p) in enumerate(actions(h)):
			if name in ('click', 'input', 'select_dropdown', 'dropdown_options') and p.get('index') is not None \
			   and interacted(h, k) is None and (h.get('state') or {}).get('interacted_element') is not None:
				invented += 1
	if invented:
		found['invented_element_index'] = {'n': invented}

	# — LLM-call failures —
	buckets: dict[str, list] = {}
	for h in hist.get('history', []):
		if h.get('model_output') is None and h.get('metadata'):
			err = first_error(h)
			if err:
				buckets.setdefault(classify_llm_failure(err), []).append(step_number(h))
	for k, v in buckets.items():
		found[k] = {'steps': v}
	# — narrated completion without a done (only visible when the step parsed) —
	for h in real:
		ng = norm(next_goal(h) + ' ' + eval_text(h))
		if ('task is complete' in ng or 'no further action' in ng) and 'done' not in [n for n, _ in actions(h)]:
			found.setdefault('declared_complete_without_done', {'step': step_number(h)})
			break

	# — the proxy's view: runaway generation needs a RESPONSE saying so; a call that never came
	#   back is an aborted call; a slow normal call is a counter, not a mechanism —
	if proxy_records:
		runaway, slow, aborted = [], [], []
		for r in proxy_records:
			resp = r.get('response') or {}
			el = r.get('elapsed_s') or 0
			if r.get('status') in ('CLIENT_ABORTED', 'UPSTREAM_ERROR') or not resp:
				aborted.append(r.get('seq'))
			elif resp.get('done_reason') == 'length' or (resp.get('eval_count') or 0) >= 1024:
				runaway.append({'seq': r.get('seq'), 'tokens': resp.get('eval_count'), 's': round(el, 1)})
			elif el > 60:
				slow.append({'seq': r.get('seq'), 'tokens': resp.get('eval_count'), 's': round(el, 1)})
		if runaway:
			found['runaway_generation'] = {'calls': runaway}
		if slow:
			found['slow_llm_call'] = {'calls': slow}
		if aborted:
			found['aborted_llm_calls'] = {'seq': aborted}

	# — budget: done only when forced at the last step, or no done at all —
	last = real[-1] if real else None
	if last is not None and step_number(last) == max_steps:
		if not row.get('agent_said_done'):
			found['budget_exhausted'] = {'max_steps': max_steps, 'tail': (tail_error(hist) or '')[:60]}
		elif len(real) > 1:
			found['done_only_when_forced'] = {'max_steps': max_steps}
	if row.get('first_seen_step') is not None and last is not None:
		after = (step_number(last) or 0) - row['first_seen_step']
		if after > 0:
			found['steps_after_first_seen'] = {'n': after}

	# — honesty —
	if row.get('outcome') == 'HONEST_MISS':
		found['honest_miss'] = {}

	# — scroll adherence counters (exist so a prompt edit can be shown to change behaviour) —
	scrolls = [(h, p) for h in real for n, p in actions(h) if n == 'scroll']
	if scrolls:
		inverted = 0
		for h, p in scrolls:
			if p.get('down') is False:
				words = norm(memory(h) + ' ' + next_goal(h) + ' ' + eval_text(h))
				if WORD_DOWN.search(words) and not WORD_UP.search(words):
					inverted += 1
		if inverted:
			found['scroll_direction_inverted'] = {'k': inverted, 'n': len(scrolls)}
		gt1 = sum(1 for _, p in scrolls if _pages(p) > 1.0)
		found['scroll_pages_gt1'] = {'k': gt1, 'n': len(scrolls)}
		if len(scrolls) >= 6 and gt1 == 0:
			found['scroll_step_too_small'] = {'n': len(scrolls)}

	# — first state blank on a fresh Chrome (HN captured before render) —
	if real:
		sm0 = real[0].get('state_message') or ''
		m0 = norm(memory(real[0]) + ' ' + eval_text(real[0]))
		if 'Page appears empty' in sm0 or 'empty page' in sm0 or 'page is empty' in m0 or 'no content' in m0:
			found['blank_first_state'] = {'reaction': [n for n, _ in actions(real[0])] or ['(no action)']}

	# — typed the requested query, then never submitted —
	if not task.read_only:
		for i, h in enumerate(real):
			hit = False
			for name, p in actions(h):
				if name == 'input' and norm(p.get('text')) and norm(p.get('text')) in prompt_n:
					later = [n for hh in real[i + 1:] for n, _ in actions(hh)]
					urls = {url_nofrag(hh['state'].get('url')) for hh in real[i:]}
					if later and not ({'click', 'send_keys', 'search', 'navigate'} & set(later)) and len(urls) == 1:
						found['typed_but_never_submitted'] = {'step': step_number(h), 'later': dict(Counter(later))}
					hit = True
					break
			if hit:
				break

	return found


def decisive_step(found: dict, hist: dict, row: dict) -> tuple[int | None, str]:
	"""The step whose screenshot to look at first, and the rule that chose it."""
	real = steps(hist)
	if 'had_then_lost' in found and found['had_then_lost'].get('lost'):
		return found['had_then_lost']['lost'], 'lost_at'
	if 'viewport_moved_after_input' in found:
		return found['viewport_moved_after_input']['step'], 'viewport moved after input'
	if 'url_changed_on_read_only' in found:
		return found['url_changed_on_read_only']['step'], 'url changed on read-only'
	if 'stale_narrative_after_navigation' in found:
		return found['stale_narrative_after_navigation']['steps'][0], 'stale narrative'
	if 'repeated_action' in found:
		return found['repeated_action']['from_step'], 'first repeat'
	if 'scroll_direction_inverted' in found:
		for h in real:
			for n, p in actions(h):
				if n == 'scroll' and p.get('down') is False:
					return step_number(h), 'first inverted scroll'
	if real:
		return step_number(real[-1]), 'last step'
	return None, 'no steps'


def screenshot_for(hist: dict, step: int | None) -> str | None:
	if step is None:
		return None
	for h in hist.get('history', []):
		if step_number(h) == step:
			return (h.get('state') or {}).get('screenshot_path')
	return None


MECHANISM_TEMPLATES = {
	'had_then_lost': "the model uses `input` as \"report\"; browser-use scrolls the typed-into element "
	                 "into view, row 1 leaves the viewport, and the next read takes the topmost VISIBLE row as #1",
	'stale_narrative_after_navigation': "the click worked and the URL changed, but memory/eval are byte-identical to "
	                                    "the previous step — the model never registered the navigation and keeps "
	                                    "re-clicking a control it already used",
	'scroll_direction_inverted': "memory says 'scroll down' but the action sent down=False; from the top of a page "
	                             "that is a no-op and the screenshot never changes",
	'typed_but_never_submitted': "the query was typed correctly but no click/Enter followed; waiting or "
	                             "find-in-page cannot make results appear",
	'budget_exhausted': "ran out of steps with no `done` — read the trace for what it spent them on",
	'blank_first_state': "the first browser state was captured before the page rendered (fresh Chrome, "
	                     "Hacker News); the model reacted to an empty page",
	'runaway_generation': "one LLM call generated to the num_predict cap with malformed JSON; the truncation "
	                      "is a parse failure the retry clears",
	'llm_timeout': "an LLM call hit llm_timeout (600 s) and was abandoned; the box kept generating",
	'honest_miss': "the model said it could not find the answer rather than inventing one — the GOOD "
	               "failure; keep it when tightening prompts",
	'stray_input_on_read_only': "the prompt forbade interaction and the model interacted anyway — the "
	                            "precondition for every had-then-lost in the corpus",
	'shown_but_never_held': "the answer appeared in an action result (e.g. a find-in-page hit) but never "
	                        "entered the model's memory — it read past it",
}

RANK = ['had_then_lost', 'stale_narrative_after_navigation', 'typed_but_never_submitted',
        'scroll_direction_inverted', 'runaway_generation', 'llm_timeout', 'blank_first_state',
        'budget_exhausted', 'url_changed_on_read_only', 'viewport_moved_after_input',
        'shown_but_never_held', 'stray_input_on_read_only', 'repeated_action', 'stuck_narrative',
        'honest_miss']


def headline(found: dict, row: dict) -> str:
	for name in RANK:
		if name in found and name in MECHANISM_TEMPLATES:
			ev = found[name]
			if name == 'had_then_lost':
				return f"had the answer at step {ev.get('first')}, lost it at step {ev.get('lost')}"
			if name == 'stale_narrative_after_navigation':
				return f"URL changed at step {ev['steps'][0]} but the narrative did not"
			if name == 'scroll_direction_inverted':
				return f"{ev['k']} of {ev['n']} scrolls went the wrong way"
			return name.replace('_', ' ')
	return (row.get('outcome') or '').lower().replace('_', ' ')


def fmt_ev(name: str, ev: dict) -> str:
	if not ev:
		return name
	if name in ('runaway_generation', 'slow_llm_call'):
		return f"{name}(" + '; '.join(f"seq {c['seq']} {c['tokens']} tok {c['s']}s" for c in ev['calls']) + ')'
	return f"{name}(" + ', '.join(f'{k}={v}' for k, v in ev.items()) + ')'


def render(task: Task, rep: int, arm: str, row: dict, found: dict, hist: dict,
           proxy_records: list[dict] | None, run_dir: str, truth_note: str = '',
           max_steps: int | None = None) -> str:
	real = steps(hist)
	max_steps = max_steps or row.get('max_steps') or task.max_steps
	out = []
	out.append(f"DIAGNOSIS {task.name} rep{rep} [{arm}]  {row['outcome']} — {headline(found, row)}   {run_dir}")
	exp = row.get('expected')
	exp_txt = exp if exp not in (None, []) else ('(absent: must admit it is not there)' if task.absent else '(ungraded)')
	out.append(f"  expected : {exp_txt}{('  ' + truth_note) if truth_note else ''}")
	final = row.get('final') or ''
	out.append(f"  final    : {final[:300]!r}{' …' if len(final) > 300 else ''}  (agent success={row.get('agent_success')} — not graded on this)")
	ended = 'done' if row.get('agent_said_done') else ('max_steps' if 'budget_exhausted' in found else 'no done')
	fails = sorted(n for k in ('parse_fail', 'empty_action', 'malformed_action', 'llm_timeout')
	               for n in (found.get(k, {}).get('steps') or []))
	out.append(f"  ended_by : {ended} at step {step_number(real[-1]) if real else '-'}/{max_steps}   {row.get('seconds', '?')} s"
	           + (f"   LLM-failure steps {fails}" if fails else ''))
	if proxy_records:
		el = sorted(r.get('elapsed_s') or 0 for r in proxy_records)
		med = el[len(el) // 2] if el else 0
		out.append(f"  gpu      : {len(proxy_records)} calls  median {med:.1f} s  max {el[-1] if el else 0:.1f} s"
		           f"  runaway {len(found.get('runaway_generation', {}).get('calls', []))}"
		           f"  slow {len(found.get('slow_llm_call', {}).get('calls', []))}"
		           f"  aborted {len(found.get('aborted_llm_calls', {}).get('seq', []))}")
	out.append('  trace')
	first, lost = row.get('first_seen_step'), row.get('lost_at_step')
	expected = row.get('expected') or []
	for h in real:
		n = step_number(h)
		acts = actions(h)
		if not acts:
			out.append(f"    {n:>2}  — LLM-FAIL {first_error(h)[:60]!r}")
			continue
		name, p = acts[0]
		el = element_label(interacted(h, 0))
		# YES = the model held it (memory or its own typed/said text); res = only a page result had it
		in_held = bool(expected) and contains_all(held_text(h), expected)
		in_res = bool(expected) and not in_held and any(contains_all(t, expected) for t in result_texts(h))
		has = 'YES' if in_held else ('res' if in_res else ('NO ' if expected else '   '))
		mark = ' ← first seen' if n == first else (' ← LOST' if n == lost else '')
		err = first_error(h)
		out.append(f"    {n:>2}  {action_label(name, p):<46} {('→ ' + el) if el else '':<24} {short_url((h.get('state') or {}).get('url')):<34} {viewport(h):<6} {has}{mark}"
		           + (f"   ! {err[:40]}" if err else ''))
	if found:
		out.append('  patterns : ' + ' · '.join(fmt_ev(k, v) for k, v in found.items()))
	step, rule = decisive_step(found, hist, row)
	if step is not None:
		prev = screenshot_for(hist, step - 1)
		out.append(f"  decisive : step {step} ({rule})   {screenshot_for(hist, step) or '(no screenshot)'}")
		if prev:
			out.append(f"             compare  {prev}")
		last_shot = screenshot_for(hist, step_number(real[-1])) if real else None
		if last_shot and last_shot != screenshot_for(hist, step):
			out.append(f"             last     {last_shot}")
	for name in RANK:
		if name in found and name in MECHANISM_TEMPLATES:
			out.append(f"  typical  : TEMPLATE, not a finding — {MECHANISM_TEMPLATES[name]}")
			break
	return '\n'.join(out)


# ── batch-level ─────────────────────────────────────────────────────────────────────────

def is_near_miss(row: dict, median_steps: float | None) -> bool:
	"""A CORRECT run worth diagnosing anyway: wasted actions, or more than twice the median
	steps of its CORRECT peers (and more than 2 steps in absolute terms)."""
	if row.get('outcome') != 'CORRECT':
		return False
	if row.get('wasted_actions'):
		return True
	if median_steps and row.get('steps', 0) > 2 * median_steps and row.get('steps', 0) > 2:
		return True
	return False


def median_correct_steps(rows: list[dict], task: str) -> float | None:
	same = sorted(r['steps'] for r in rows if r.get('task') == task and r.get('outcome') == 'CORRECT')
	if not same:
		return None
	return same[len(same) // 2]


def _targets(rows: list[dict]) -> Counter:
	c: Counter = Counter()
	for r in rows:
		if r.get('outcome') in GRADED:
			for k in (r.get('patterns') or {}):
				if k not in NOT_A_TARGET:
					c[k] += 1
	return c


def rollup(rows: list[dict]) -> str:
	"""Pattern → count/runs per task, across a batch. `rows` carry 'task', 'arm', 'patterns'."""
	by_task: dict[str, list[dict]] = {}
	for r in rows:
		by_task.setdefault(r['task'], []).append(r)
	lines = ['ROLLUP — patterns per task (count of runs the pattern fired in / graded runs)']
	for task, rs in by_task.items():
		graded = [r for r in rs if r.get('outcome') in GRADED]
		c: Counter = Counter()
		for r in graded:
			for k in (r.get('patterns') or {}):
				if k != 'scroll_pages_gt1':
					c[k] += 1
		if c:
			lines.append(f"  {task:<20} " + ', '.join(f"{k} {v}/{len(graded)}" for k, v in c.most_common()))
	sc = [r['patterns']['scroll_pages_gt1'] for r in rows if 'scroll_pages_gt1' in (r.get('patterns') or {})]
	if sc:
		k = sum(x['k'] for x in sc)
		n = sum(x['n'] for x in sc)
		lines.append(f"  adherence: scroll pages>1 used in {k}/{n} scrolls; "
		             f"scroll direction inverted in {sum(1 for r in rows if 'scroll_direction_inverted' in (r.get('patterns') or {}))} runs")
	overall = _targets(rows)
	if overall:
		top = overall.most_common(1)[0]
		lines.append(f"  most frequent fixable pattern across the batch: {top[0]} ({top[1]} runs)")
	return '\n'.join(lines)


ARM_FOR = {
	'had_then_lost': 'enforce-read-only',
	'stray_input_on_read_only': 'enforce-read-only',
	'url_changed_on_read_only': 'enforce-read-only',
	'viewport_moved_after_input': 'enforce-read-only',
}


def next_footer(rows: list[dict]) -> str:
	overall = _targets(rows)
	if not overall:
		return 'NEXT: nothing fixable fired. Add a task on a new site with runtime-derived truth before believing this generalises.'
	name, n = overall.most_common(1)[0]
	tasks = Counter(r['task'] for r in rows if r.get('outcome') in GRADED and name in (r.get('patterns') or {}))
	task = tasks.most_common(1)[0][0] if tasks else '<task>'
	arm = ARM_FOR.get(name)
	head = (f"NEXT: most frequent fixable pattern is {name} ({n} runs, mostly {task}). Open its decisive "
	        f"screenshots, write the mechanism as one sentence naming the step, then ")
	if arm:
		return head + f"probe:\n      venv/bin/python -u tools/test.py run --only {task} --reps 4 --arms default,{arm} --label probe-{name}"
	return head + (f"gather more instances (no arm is mapped for this pattern yet — form one as sysmsg:FILE or set:KEY=JSON "
	               f"after reading them):\n      venv/bin/python -u tools/test.py run --only {task} --reps 4 --label look-{name}")


def rate_line(name: str, rows: list[dict]) -> str:
	graded = [r for r in rows if r.get('outcome') in GRADED]
	excluded = [r for r in rows if r.get('outcome') not in GRADED]
	if not graded:
		return f"  {name:<20} no graded runs" + (f"  ({len(excluded)} excluded: {dict(Counter(r['outcome'] for r in excluded))})" if excluded else '')
	k = sum(1 for r in graded if r['correct'])
	n = len(graded)
	lo, hi = wilson(k, n)
	oc = Counter(r['outcome'] for r in graded)
	steps_avg = sum(r['steps'] for r in graded) / n
	waste = sum(r['wasted_actions'] for r in graded) / n
	hl = sum(1 for r in graded if r.get('had_then_lost'))
	parts = [f"{k}/{n} correct (80% CI {lo:.0%}–{hi:.0%})", f"avg {steps_avg:.1f} steps", f"{waste:.1f} wasted"]
	bad = ', '.join(f"{o} {c}" for o, c in oc.items() if o != 'CORRECT')
	if bad:
		parts.append(bad)
	if hl:
		parts.append(f"had-then-lost x{hl}")
	if excluded:
		parts.append(f"{len(excluded)} excluded ({', '.join(f'{o} {c}' for o, c in Counter(r['outcome'] for r in excluded).items())})")
	return f"  {name:<20} " + '   '.join(parts)


def compare(rows_a: list[dict], rows_b: list[dict], label_a: str, label_b: str, min_reps: int = 6) -> str:
	"""Per-task before/after with mechanism counters and a Fisher exact p. Refuses a verdict
	below `min_reps` graded runs per arm — 3 v 3 cannot tell 33% from 67% — and says so when
	the B arm was inert for a task (arm_effective False on every row)."""
	tasks = sorted({r['task'] for r in rows_a} | {r['task'] for r in rows_b})
	lines = [f"COMPARE  A={label_a}  B={label_b}"]
	for t in tasks:
		ga = [r for r in rows_a if r['task'] == t and r.get('outcome') in GRADED]
		gb = [r for r in rows_b if r['task'] == t and r.get('outcome') in GRADED]
		ka, kb = sum(1 for r in ga if r['correct']), sum(1 for r in gb if r['correct'])
		na, nb = len(ga), len(gb)
		lines.append(f"  {t}")
		lines.append(f"    correct      A {ka}/{na}   B {kb}/{nb}")
		ca: Counter = Counter(k for r in ga for k in (r.get('patterns') or {}))
		cb: Counter = Counter(k for r in gb for k in (r.get('patterns') or {}))
		for k in sorted(set(ca) | set(cb)):
			if k == 'scroll_pages_gt1':
				continue
			lines.append(f"    {k:<36} A {ca[k]}/{na}   B {cb[k]}/{nb}")
		b_all = [r for r in rows_b if r['task'] == t]
		if b_all and all(r.get('arm_effective') is False for r in b_all):
			lines.append("    verdict      ARM INERT for this task (arm_effective=False on every B row) — identical configurations")
		elif na < min_reps or nb < min_reps:
			lines.append(f"    verdict      NO VERDICT — {na} v {nb} graded runs; need ≥{min_reps} per arm")
		else:
			p = fisher_two_sided(ka, na - ka, kb, nb - kb)
			lines.append(f"    fisher p     {p:.3f}  (two-sided; <0.1 to claim a rate change on {na} v {nb})")
	return '\n'.join(lines)
