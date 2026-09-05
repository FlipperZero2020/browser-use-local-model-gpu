#!/usr/bin/env python3
"""browsin test — the one way to test the local vision model driving Chrome.

    export WARDEN_URL=http://192.168.1.111:8130
    export WARDEN_TOKEN_FILE=$HOME/.config/warden/token      # the file, never WARDEN_TOKEN

    venv/bin/python -u tools/test.py run --reps 3                         # the table, graded, diagnosed
    venv/bin/python -u tools/test.py run --only hn-top-story --reps 4 --arms default,enforce-read-only
    venv/bin/python -u tools/test.py one --url URL --task "..." --expect-from hn:1
    venv/bin/python    tools/test.py diagnose runs/test-run-<ts>          # offline, re-render diagnoses
    venv/bin/python    tools/test.py compare runs/test-run-A runs/test-run-B
    venv/bin/python    tools/test.py self-check                           # no GPU, no browser, no lease
    venv/bin/python    tools/test.py guide                                # prints THE LOOP below

Anything that takes a lease runs in the background or with a long timeout: a 3-rep table is
~17 minutes; a two-minute tool timeout SIGTERMs the holder and the run is lost. Run `self-check`
first in every session. Read CLAUDE.md before touching anything; PLAN.md §10 is the log.

WHAT ONE RUN PRODUCES
  runs/test-<mode>-<ts>/run.json                     mode, label, reps, arms, task specs, ttl
  runs/test-<mode>-<ts>/results.jsonl                one line per run, appended AS EACH RUN FINISHES
                                                     incl. dom_ready: what the pre-run wait for a
                                                     non-empty DOM cost (builds, ms, trace, nav_ms)
  runs/test-<mode>-<ts>/proxy.jsonl                  every LLM call: prompt_eval_count, eval_count, done_reason
  runs/test-<mode>-<ts>/summary.txt                  the per-task table, the ROLLUP and the NEXT footer
  runs/test-<mode>-<ts>/<task>-rep<N>[-<arm>]/history.json    every step, as browser-use saw it
                                             /DIAGNOSIS.txt    the block below, verbatim
                                             /conversation/    per-step prompts (parsed steps only)
  runs/test-<mode>-<ts>/tmp/browser_use_agent_*/screenshots/step_<n>.png
                                             what the model was shown; batch-level because
                                             browser-use puts them under TMPDIR — every
                                             DIAGNOSIS block prints the absolute paths
  A refused start (interlock, lock, no systemd) creates no run directory.

OUTCOMES (never PASS/FAIL — these want different fixes)
  CORRECT        expected string in the final `done` text (absent task: admitted absence; naming
                 the real sections while denying the target is still CORRECT)
  WRONG_ANSWER   a `done` with something else — "confident nonsense"; check had_then_lost
  NO_ANSWER      no `done`, or an empty one: budget or failures exhausted
  HONEST_MISS    a `done` that says it could not find it, with no candidate answer — the GOOD miss
  RACY           truth moved during the run and a WRONG_ANSWER matched neither value. A final
                 that matches the post-run truth is CORRECT ("matched post-run truth"); an
                 HONEST_MISS / NO_ANSWER stays graded — its classification does not depend on
                 which title was #1.
  FIXTURE_STALE / TRUTH_UNAVAILABLE / SETUP_FAILED / ABORTED   excluded from the rate, counted
  The rate is CORRECT over the four graded outcomes. The agent's own `success` flag is recorded
  and never graded on — measured, it is noise in both directions.

THE DIAGNOSIS BLOCK (printed after every non-CORRECT run and every near-miss — a CORRECT run
with a wasted action, or with more than twice the median steps of its CORRECT peers)
  header     task, outcome, one-line mechanism, run dir
  expected   the truth strings and whether they moved during the run
  final      the answer (first 300 chars; agent success shown, "not graded on this")
  ended_by   done | max_steps | no done; step k/max; seconds; LLM-failure steps
  gpu        calls, latency median/max, runaway / slow / aborted counts
  trace      one row per step: action+params → element · url · viewport · has-answer? (YES in
             memory, res = only in an action result) ← first seen / ← LOST
  patterns   the detectors that fired, with evidence
  decisive   the step whose screenshot to open, plus the frame before it and the last frame
  typical    TEMPLATE, not a finding — the usual mechanism for the top pattern, to be confirmed
             against the screenshots, never assumed (the first "nonsense" answer of 2026-09-05
             was correct)

THE LOOP — run → diagnose → fix as an ARM → measure → land
  0  Preconditions: `self-check` green; no runs/.lock held by a live pid; card quiet. Never
     pass --evict in a loop — clonin-frontdoor is public and --evict cuts a stranger off.
  1  Baseline: `run --reps 3 --label baseline`. Read the ROLLUP and the DIAGNOSIS blocks, not
     the rate. A batch with SETUP_FAILED / TRUTH_UNAVAILABLE / ABORTED runs, or a gpu line
     showing runaways, is contaminated by the harness or the card — fix that first.
  2  Pick ONE pattern: the most frequent fixable one across failures AND near-miss passes (the
     NEXT footer names it). Open the decisive screenshots (Read the PNGs). Write the mechanism
     as one sentence that names a step and a screenshot before changing anything. If you cannot,
     get more instances: `run --only TASK --reps 4 --label look`. Observation is cheaper than a
     wrong fix.
  3  Form the fix as an ARM, not an edit: --arms default,enforce-read-only | sysmsg:FILE |
     set:KEY=JSON. Never copy this script; never edit browsin/ yet. Name, in the label, which
     counter must move and which way (had_then_lost → 0; scroll_pages_gt1 > 0; …). If no
     counter would move, the fix is unmeasurable — add the detector first.
  4  Probe (~5 min): `run --only TASK --reps 4 --arms default,<arm> --label probe-<name>`.
     Counter did not move at all → the arm is dead. One variation, then rule 8a.
  5  Measure (~15 min): `run --only TASK --reps 8 --arms default,<arm> --label ab-<name>`
     then `compare`. IMPROVED means all three: the targeted counter fell ≥50% (or rose, for an
     adherence counter); correct k/8 did not fall; no other detector rose. A RATE claim also
     needs Fisher p < 0.1 on 8 v 8 — 2/8 → 7/8 qualifies, 3/8 → 6/8 does not (report that
     one as a mechanism improvement only).
  6  Regression: full table `run --reps 3 --arms default,<arm>`. Land only if no task drops by
     more than one run and no new pattern appears. wiki-absent-section is the honesty canary:
     the arm must not make it guess.
  7  Land: move the override into browsin/agent.py, `self-check` green, then ONE more full
     table against the landed code with no arms. That run dir, its summary and the mechanism
     sentence go into PLAN.md §10 verbatim; regenerate and republish the artifact to the SAME
     URL (CLAUDE.md, Housekeeping). Back to step 2.
  8  Stopping rules: (a) two arms for one pattern that never move its counter → stop editing
     prompts for it; switch layer (registry exclusion, a check in the loop, a task redesign) or
     file it in PLAN §7 and move on — prose to a 7B under constrained decoding is presumed
     inert until a counter says otherwise (the scroll pages=3-5 paragraph was never followed
     once). (b) counter moved, rate did not → land as mechanism-only, no rate claim. (c) the
     same task swings > 2/8 between identical batches → the task or its truth is noisy; fix
     that first. (d) at most 3 landed changes per session. (e) never report a partial batch;
     `--resume` it. (f) a fix that fails twice for the same reason → stop and report.

ARMS (recorded in every results row; `default` is no override)
  no-dom-ready        the NULL CONTROL for the pre-run DOM-ready wait: identical to default in
                      every other respect, but skips `_wait_for_dom` so the agent's own first
                      build is the first build, as it was before 2026-09-05. Run it as
                      `--arms default,no-dom-ready` when you want the wait's effect measured
                      inside ONE batch — Hacker News moves hourly and hn-top-story already
                      swings across identical baselines, so a cross-batch before/after cannot
                      separate the wait from the front page changing. Also the escape hatch for
                      a legitimately sparse page (browsin/fixture.py serializes to < 10 nodes,
                      so the wait can only time out there).
  enforce-read-only   for tasks marked read_only, remove `input` and `click` from the registry.
                      Every had_then_lost in the corpus begins with a stray `input`;
                      DEFAULT_EXCLUDED_ACTIONS is the precedent — enforcement, not persuasion.
                      Inert on a task that is not read_only, so those (task, arm) pairs are
                      SKIPPED rather than run as an identical duplicate, and `compare` says so.
  sysmsg:PATH         replace extend_system_message with the file's contents
  set:KEY=JSON        override one Agent kwarg (e.g. set:max_history_items=4). Keys that would
                      fight the lease or be overwritten (enable_signal_handler, llm, task,
                      browser_session, tools) are refused; unknown keys are refused before any
                      card time via browsin.agent.check_agent_kwargs.
  Arms are validated before anything touches the card, and interleaved rep by rep in one batch
  so page drift and card state hit both equally.

ADDING A TASK — append to TASKS in browsin/grade.py
  Task(name, url, prompt ending "Then call done.", expect=<CALLABLE returning list[str]>,
       max_steps=8, read_only=True|False, absent=False, forbid=[...])
  expect is the callable itself: expect=wikipedia_itn_lead, expect=nothing_to_find,
  expect=hn_story(15), expect=wikipedia_contains('Page', ['phrase']) — never
  wikipedia_itn_lead() with parentheses (that would fetch at import; Task refuses it). A
  fixture that rots must report FIXTURE_STALE, not a model failure. The existing prompts are
  part of the 2026-09-05 measurement — do not edit them without re-baselining.

EXIT CODES (CLAUDE.md's contract: 0 passed, 1 failed, 2 refused to start)
  0  the measurement completed — including a one-off with no --expect-from (UNGRADED) and a
     `run` whose rate is 72%: a completed measurement is not a failure. With --gate, 0 means
     the rate reached --min-rate.
  1  nothing could be graded, the batch was ABORTED (partial results kept; --resume it), or
     --gate's --min-rate was not reached
  2  refused to start: interlock, lock held, no systemd-run, lease denied/timed out/warden
     unreachable, served window mismatch, proxy port taken, bad --arms / --expect-from / --only

WHAT THIS DOES NOT YET DO (honest gaps)
  · truth for read-only tasks still comes from a parallel HTTP fetch, re-checked after the run;
    a DOM snapshot of the driven tab would close the Hacker News race by construction
  · the §5 thresholds (element-reference resolution ≥85%, error-repair ≥85%) are not computed
  · this is the interim entry point, not the Phase 6 `bin/browsin` CLI
  · ttl_s defaults to 180 here (three heartbeats of slack for a browser step parked in
    to_thread), not lease.py's 120; it is the only lever against a SIGKILL either way
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import browsin  # noqa: E402,F401  — zero-cloud env; does NOT import browser_use
from browsin import diagnose as D  # noqa: E402  — pure
from browsin import grade as G  # noqa: E402  — pure

WORKLOAD = 'ollama:qwen2.5vl-32k:7b'
MODEL_TAG = 'qwen2.5vl-32k:7b'   # asserted == normalise_tag(WORKLOAD) before any lease
#: One number written in two places: the client's ollama_options and warden's cost_mib booking
#: (measured at this window). browsin/lease.py refuses to start on a mismatch. Change both or
#: neither.
NUM_CTX = 32768
DEFAULT_TTL_S = 180

LOCK = REPO / 'runs' / '.lock'

#: Agent kwargs an arm may not override: the first fights browsin.lease's signal handling
#: (browser-use's SignalHandler would replace hold()'s SIGINT/SIGTERM callbacks), the rest are
#: applied by build_agent AFTER overrides and would be silently discarded.
FORBIDDEN_SET_KEYS = frozenset({'enable_signal_handler', 'llm', 'task', 'browser_session', 'tools'})


# ── arms ────────────────────────────────────────────────────────────────────────────────

def _slug(s: str) -> str:
	return re.sub(r'[^A-Za-z0-9_.-]+', '-', s).strip('-') or 'arm'


class Arm:
	def __init__(self, spec: str):
		self.spec = spec
		self.extra_excluded: tuple[str, ...] = ()
		self.overrides: dict = {}
		self.read_only_only = False
		#: Whether this arm pays the pre-run DOM-ready wait (`_wait_for_dom`). It is an ARM and not
		#: a batch flag on purpose: `--arms default,no-dom-ready` interleaves the control with the
		#: fix inside one batch, under the same page state. Hacker News moves hourly and hn-top-story
		#: already swings WRONG/CORRECT/WRONG/CORRECT/CORRECT across the 2026-09-05 baseline, so a
		#: cross-batch before/after cannot separate the wait from the front page changing.
		self.dom_ready = True
		if spec == 'default':
			self.name = 'default'
		elif spec == 'no-dom-ready':
			# The null control for the DOM-ready wait: identical to `default` in every other
			# respect, including the excluded actions and every Agent kwarg.
			self.name = 'no-dom-ready'
			self.dom_ready = False
		elif spec == 'enforce-read-only':
			self.name = 'enforce-read-only'
			self.extra_excluded = ('input', 'click')
			self.read_only_only = True
		elif spec.startswith('sysmsg:'):
			path = pathlib.Path(spec[len('sysmsg:'):]).expanduser()
			try:
				self.overrides['extend_system_message'] = path.read_text(encoding='utf-8')
			except OSError as exc:
				raise SystemExit(f'REFUSED TO START\n  sysmsg arm: cannot read {path}: {exc}')
			self.name = 'sysmsg-' + _slug(path.stem)
		elif spec.startswith('set:'):
			k, eq, v = spec[len('set:'):].partition('=')
			if not k or not eq:
				raise SystemExit(f'REFUSED TO START\n  set arm needs KEY=JSON, got {spec!r}')
			if k in FORBIDDEN_SET_KEYS:
				raise SystemExit(f'REFUSED TO START\n  set:{k} is refused — it would fight the lease holder or be '
				                 f'overwritten by build_agent (see the ARMS section of `test.py guide`)')
			try:
				self.overrides[k] = json.loads(v)
			except json.JSONDecodeError:
				self.overrides[k] = v
			self.name = 'set-' + _slug(f'{k}-{v}')
		else:
			raise SystemExit(f'REFUSED TO START\n  unknown arm {spec!r}; use default | no-dom-ready | '
			                 f'enforce-read-only | sysmsg:PATH | set:KEY=JSON')

	def applies_to(self, task: G.Task) -> bool:
		return not self.read_only_only or task.read_only


def parse_arms(s: str) -> list[Arm]:
	arms = [Arm(x.strip()) for x in s.split(',') if x.strip()]
	if not arms:
		raise SystemExit('REFUSED TO START\n  --arms is empty')
	names = [a.name for a in arms]
	if len(set(names)) != len(names):
		raise SystemExit(f'REFUSED TO START\n  duplicate arm names: {names}')
	return arms


# ── lock ────────────────────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
	try:
		os.kill(pid, 0)
		return True
	except ProcessLookupError:
		return False
	except PermissionError:
		return True


def take_lock() -> None:
	"""Atomic (O_EXCL) lock so two starts in the same second cannot both pass. Taken BEFORE the
	card is touched: a refused start must never race a live run's Chrome or proxy."""
	LOCK.parent.mkdir(exist_ok=True)
	payload = json.dumps({'pid': os.getpid(), 'at': time.time()})
	for _ in range(2):
		try:
			fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
			with os.fdopen(fd, 'w') as f:
				f.write(payload)
			return
		except FileExistsError:
			try:
				held = json.loads(LOCK.read_text())
			except Exception:
				held = {}
			pid = int(held.get('pid') or 0)
			if pid and _pid_alive(pid):
				raise SystemExit(f"REFUSED TO START\n  another test.py (pid {pid}) holds {LOCK}. Two runs would "
				                 f"fight over Chrome on :9242 and the proxy on :11434.")
			try:
				LOCK.unlink()   # stale: the holder is dead
			except FileNotFoundError:
				pass
	raise SystemExit(f'REFUSED TO START\n  could not take {LOCK}')


def release_lock() -> None:
	try:
		if LOCK.exists() and json.loads(LOCK.read_text()).get('pid') == os.getpid():
			LOCK.unlink()
	except Exception:
		pass


# ── the leasing path (run / one) ────────────────────────────────────────────────────────

def _enter_run_dir(mode: str) -> tuple[pathlib.Path, pathlib.Path]:
	"""Create the run dir and point every temp family at it — BEFORE browser_use is imported.

	One of browser-use's four temp-directory families is created at import time and
	`tempfile.gettempdir()` caches its first answer; this is the only place that ordering is
	enforced, which is why the leasing imports live inside `_drive`/`cmd_run` and not at the
	top. Called only after every refusal check has passed, so a refused start leaves nothing.
	"""
	import tempfile
	run_dir = REPO / 'runs' / f'test-{mode}-{time.strftime("%Y%m%d-%H%M%S")}'
	scratch = run_dir / 'tmp'
	scratch.mkdir(parents=True, exist_ok=True)
	(scratch / 'downloads').mkdir(exist_ok=True)
	os.environ['TMPDIR'] = str(scratch)
	tempfile.tempdir = str(scratch)
	os.environ['BROWSER_USE_CONFIG_DIR'] = str(run_dir / 'config')
	return run_dir, scratch


def _task_spec(task: G.Task) -> dict:
	return {'name': task.name, 'url': task.url, 'prompt': task.prompt, 'max_steps': task.max_steps,
	        'absent': task.absent, 'forbid': task.forbid, 'read_only': task.read_only}


def _task_from_spec(spec: dict) -> G.Task:
	return G.Task(name=spec['name'], url=spec['url'], prompt=spec['prompt'], expect=G.nothing_to_find,
	              max_steps=spec.get('max_steps', 8), absent=spec.get('absent', False),
	              forbid=list(spec.get('forbid') or []), read_only=spec.get('read_only', False))


def _append(path: pathlib.Path, row: dict) -> None:
	with open(path, 'a', encoding='utf-8') as f:
		f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def _load_rows(run_dir: pathlib.Path) -> list[dict] | None:
	"""Rows of results.jsonl, or None if the file does not exist (distinct from an empty batch)."""
	p = run_dir / 'results.jsonl'
	if not p.exists():
		return None
	return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]


async def _fresh_chrome(B, url: str):
	"""Stop the unit, wait for :9242 to actually close, start on `url`. Never attach: the attach
	path adopted whatever tab was already open on the same host — the stale-tab bug."""
	B.stop()
	for _ in range(20):
		if B.probe() is None:
			break
		await asyncio.sleep(0.5)
	return B.start(url)


#: browser-use's threshold, not ours: agent/prompts.py:230 prefixes its <page_stats> line with
#: 'Page appears empty - consider waiting - ' whenever _extract_page_statistics()['total_elements']
#: < 10, and that prefix is one of the two cues browsin.diagnose.blank_first_state greps for.
#: Gating on any other number (len(selector_map), say) could clear a weaker bar while the model
#: still sees the string. `self-check` pins this against the installed library's own source.
#:
#: A page can be legitimately sparser than this: browsin/fixture.py's two pages serialize to well
#: under 10 nodes, so browser-use prints the same cue on them and the wait below can then only
#: time out — 8 s and a flag on a perfectly healthy page. Use `--arms no-dom-ready` for those.
DOM_READY_MIN_ELEMENTS = 10

#: 8 s of polling, not 30. Measured 2026-09-05 on Hacker News: the blank build costs ~0.3 s and
#: the full one ~0.8 s (step duration minus the summed proxy elapsed_s for that step), and in all
#: ten blank runs the DOM was full by the very next build — so this is insurance, not the expected
#: price. Expected cost ~1 s a run; 32 runs at the full cap would be +4 min on a ~23 min batch.
#:
#: HONEST BOUND: the deadline is checked only BETWEEN builds, and browser-use gives each build its
#: own 30 s (BrowserStateRequestEvent.event_timeout, browser/events.py:201), so one pathological
#: build can carry a single run to ~38 s. Deliberately NOT wrapped in asyncio.wait_for: cancelling
#: our await does not cancel the handler, and the orphan would later repopulate the very caches
#: the `finally` below exists to clear. Lease loss needs no help from the cap either — every
#: statement in the loop is an await, so hold()'s Task.cancel() lands immediately.
DOM_READY_TIMEOUT_S = 8.0

#: The blank build is fast (~0.3 s), so an immediate re-issue would re-read the same document.
#: There is deliberately NO sleep before the FIRST build: `builds == 1` has to keep meaning "the
#: first build was already full", which is half the discrimination this wait buys.
DOM_READY_POLL_S = 0.3


def _dom_elements(dom_state) -> int:
	"""browser-use's own `total_elements`, recomputed here rather than imported.

	An exact mirror of AgentMessagePrompt._extract_page_statistics (agent/prompts.py:150-221):
	every node of the SERIALIZED tree — element, text and shadow-root alike — skipping a node and
	its whole subtree when `original_node` is falsy, and 0 when `_root` is None. It is the number
	that decides the 'Page appears empty' line, so gating on anything else could clear the gate
	and still emit the string. Pure and stdlib-only, so `self-check` exercises it against fakes
	with no card, no browser and no import of browser_use.

	`self-check` pins the THRESHOLD against the installed library but nothing can pin this
	traversal contract. If upstream renames `_root`, `original_node` or `children` this raises,
	`_wait_for_dom` records it in `error`, and the wait silently becomes a per-run no-op that
	still costs one DOM build. The per-run `<< dom-ready PROBE ERROR` line is the only thing that
	says so — which is why that flag is printed separately from a timeout.
	"""
	root = dom_state._root if dom_state is not None else None
	n, stack = 0, [root]
	while stack:
		node = stack.pop()
		if node is None or not node.original_node:
			continue
		n += 1
		stack.extend(node.children)
	return n


async def _wait_for_dom(session, *, enabled: bool = True, nav_t0: float | None = None,
                        timeout_s: float = DOM_READY_TIMEOUT_S, poll_s: float = DOM_READY_POLL_S,
                        min_elements: int = DOM_READY_MIN_ELEMENTS) -> dict:
	"""Build browser states until one is not the empty-page state, and record what that cost.

	WHAT WAS MEASURED (runs/test-run-20260905-045402, -052408). On Hacker News, 10 of 10 runs had
	browser-use's FIRST browser-state build return a 0-3 element DOM ('Page appears empty -
	consider waiting - 0 links, 0 interactive, 0 iframes, 2 total elements') while that same
	step's SCREENSHOT was the fully painted front page, byte-identical to step 2's; step 2's DOM
	was 1094 elements every time. 0 of 12 Wikipedia runs did this. The model answered the empty
	state with a scroll (6), no parseable action (3) or a wait (1).

	WHAT IS NOT CLAIMED. This is NOT "the dominant failure". Of those 10 runs, 8 graded CORRECT
	and 2 WRONG_ANSWER — and runs/test-run-20260905-042542/hn-top-story-rep1, the one HN run whose
	step 1 already had the full 1094-element DOM (i.e. the closest thing on disk to a post-fix
	control), is WRONG_ANSWER with had_then_lost / stray_input_on_read_only /
	viewport_moved_after_input: the same signature as the dominant failure. So the honest claim is
	narrow: the blank first state spends step 1 on nothing and makes step 1 unreadable as
	evidence. Removing it is expected to take blank_first_state to 0 and to leave the completion
	rate where it is. Predict that in the label, then check it.

	NOT initial_actions=[wait]. That writes a step-0 history item browsin/grade.py's steps()
	counts as a real step; worse, browser-use builds that item with no state_message, so steps()[0]
	would carry an empty one, blank_first_state would fall silent on every run, and the real first
	build would stay exactly as blind — faking the very number this exists to move.

	Returns {'enabled','builds','ms','elements','interactive','trace','nav_ms','timed_out','error'},
	recorded per run. `trace` is [[ms, elements, interactive], ...] per build and `nav_ms` is how
	long after Chrome's CDP port answered the probe began: together they are what separates "the
	first build is structurally defective and one retry clears it" (builds 2, elements jumping
	2 -> 1094 with no intermediate value) from "the page was still loading" (elements climbing, or
	a full first build at a later nav_ms). `builds` and `ms` alone are collinear and carry about
	one bit — that was a real weakness of the first draft of this probe.

	On timeout it PROCEEDS and says so rather than raising SETUP_FAILED. SETUP_FAILED is outside
	G.GRADED, so it would delete from the rate's denominator exactly the runs most likely to still
	be blank. A run whose wait timed out is a valid measurement of the unfixed condition, not a
	harness fault; a genuinely dead CDP session still becomes SETUP_FAILED moments later out of
	agent.run().

	`except Exception`, never BaseException: browsin.lease delivers lease loss as Task.cancel(), so
	CancelledError and KeyboardInterrupt must pass straight through. A bug in this probe then costs
	one recorded field, not 32 runs of a card-holding batch.

	include_screenshot=False: the agent takes its own at step 1 regardless, and skipping it keeps
	this loop off Page.captureScreenshot and off the remove_highlights() JS that path evaluates in
	the owner's real page (screenshot_watchdog.py:58-60). No highlights are injected either —
	dom_highlight_elements defaults False (browser/profile.py:687) and browsin.agent.build_session
	does not set it — and the DOM build only READS scroll offsets, so the probe cannot move the
	viewport out from under the model.
	"""
	t0 = time.monotonic()
	st: dict = {'enabled': bool(enabled), 'builds': 0, 'ms': 0, 'elements': 0, 'interactive': 0,
	            'trace': [], 'nav_ms': None if nav_t0 is None else round((t0 - nav_t0) * 1000),
	            'timed_out': False, 'error': None}
	if not enabled:
		return st
	try:
		while True:
			state = await session.get_browser_state_summary(include_screenshot=False)
			st['builds'] += 1
			st['elements'] = _dom_elements(state.dom_state)
			st['interactive'] = len(state.dom_state.selector_map or {})
			st['trace'].append([round((time.monotonic() - t0) * 1000), st['elements'], st['interactive']])
			if st['elements'] >= min_elements:
				break
			if time.monotonic() - t0 >= timeout_s:
				st['timed_out'] = True
				break
			await asyncio.sleep(poll_s)
	except Exception as exc:
		st['error'] = f'{type(exc).__name__}: {exc}'
	finally:
		# Put the session back exactly as the tab switch left it. on_AgentFocusChangedEvent
		# (browser/session.py:1229-1235) clears FOUR things — the DOM watchdog's cache, the state
		# summary, the selector map and the selector indices — and a successful probe build
		# repopulates all four (dom_watchdog.py:669-671 calls update_cached_selector_map).
		#
		# The selector map is the one that matters, and it is a SAFETY issue, not tidiness: if
		# step 1's own DOM build then fails, browser-use substitutes a blank SerializedDOMState
		# WITHOUT clearing that map (dom_watchdog.py:394-400), so an index the model invents
		# against an "empty page" would resolve through get_dom_element_by_index
		# (browser/session.py:2449-2451) to a live element and click it in the owner's
		# authenticated Chrome. browser-use clears exactly this set on its own blank-state path,
		# commented 'Clear every action lookup path before calling the model'
		# (browser/session.py:1631-1635). Unpatched, that window is shut at step 1; the probe
		# would newly open it, and only in the `default` arm (enforce-read-only has no click or
		# input to reach it with), which would bias an A/B toward the arm.
		#
		# In a `finally` so the exception path is covered too — the advertised graceful
		# degradation (a `_root` rename) raises AFTER the watchdog has already cached its state.
		# Each reset is guarded: the cleanup itself must not be able to raise.
		for reset in (lambda: setattr(session, '_cached_browser_state_summary', None),
		              lambda: session.update_cached_selector_map({}),
		              lambda: session._dom_watchdog and session._dom_watchdog.clear_cache()):
			try:
				reset()
			except Exception as exc:
				st['error'] = st['error'] or f'reset: {type(exc).__name__}: {exc}'
	st['ms'] = round((time.monotonic() - t0) * 1000)
	return st


#: Hosts whose first paint is CLIENT-rendered, so `_wait_for_dom` above is necessary and NOT
#: sufficient. Measured 2026-09-05 on x.com: a fresh Chrome launched onto x.com/OpenAI was
#: screenshotted showing the site's BOOT SPLASH (the X glyph on black), the model correctly
#: reported an empty page and called done(success=False) at step 1 of 14, and the whole run was
#: over in 8.3 s (runs/test-one-20260905-095216).
#:
#: Why the DOM-element wait cannot cover this. `_wait_for_dom` clears at
#: DOM_READY_MIN_ELEMENTS=10 total nodes, and a boot splash is a real DOM — an SVG logo inside a
#: few wrappers sits either side of 10, so the gate is a coin toss on markup this project does
#: not control. And DOM_READY_TIMEOUT_S is 8.0 s against a page measured at 3,464 ms to
#: DOMContentLoaded *signed out*, where the signed-in timeline is heavier. Waiting for "some
#: nodes" is the wrong question on an SPA; the right one is "has the content I asked for
#: appeared", which is what browsin.pagestate.REQUIRE encodes.
#:
#: Keyed by host and not applied everywhere on purpose: on a server-rendered page the predicate
#: is satisfied by the first poll (measured: 0.03 s, 1 poll) and costs nothing, but a wrong
#: predicate on a site nobody has measured would burn CONTENT_GATE_TIMEOUT_S per run and flag a
#: healthy page. Add a host here only with a measurement beside it.
CONTENT_GATE = {
	'x.com': 'x-timeline',
	'twitter.com': 'x-timeline',
}

#: 30 s, not `_wait_for_dom`'s 8. This waits on a third party's network and JS bundle, not on a
#: local retry. It is spent only on hosts in CONTENT_GATE, and only until the predicate is true.
CONTENT_GATE_TIMEOUT_S = 30.0


async def _wait_for_content(chrome, host: str) -> dict:
	"""Wait until the page actually shows what the task is about. Records; never raises.

	Runs over its own raw-CDP connection (`browsin.pagestate`) rather than through browser-use,
	because the question is about the PAGE and browser-use's answer to it is the very thing
	measured as unreliable here. A second CDP client on the same target is fine; CDP multiplexes.

	Like `_wait_for_dom`, a timeout PROCEEDS and says so. A run against a page that never
	rendered is a valid measurement of that condition — it is simply not a model failure, and
	`browsin.diagnose` has to be able to tell those apart rather than charging it to the model,
	which is exactly what happened on 2026-09-05 before this existed.
	"""
	require = CONTENT_GATE.get(host)
	if not require:
		return {'enabled': False}
	try:
		from browsin import pagestate as PS
		r = await PS.wait_ready(cdp_url=chrome.cdp_url, host=host, require=require,
		                        timeout_s=CONTENT_GATE_TIMEOUT_S)
		out = r.as_dict()
		out['enabled'] = True
		return out
	except Exception as exc:          # never BaseException: lease loss arrives as CancelledError
		return {'enabled': True, 'ok': False, 'require': require,
		        'reason': f'{type(exc).__name__}: {exc}'}


async def _drive(task: G.Task, arm: Arm, *, proxy, chrome, scratch, run_dir, max_steps: int,
                 nav_t0: float | None = None):
	"""One agent run. Returns (history_dict, seconds, proxy_records_for_this_run, dom_ready)."""
	from browser_use.browser.events import SwitchTabEvent
	from browsin.agent import DEFAULT_EXCLUDED_ACTIONS, build_agent, build_llm, build_session, build_tools

	exclude = DEFAULT_EXCLUDED_ACTIONS + (arm.extra_excluded if arm.applies_to(task) else ())
	llm = build_llm(host=proxy.url, model=MODEL_TAG, num_ctx=NUM_CTX)
	session = build_session(cdp_url=chrome.cdp_url, downloads_path=str(scratch / 'downloads'))
	agent = build_agent(task=task.prompt, llm=llm, browser_session=session, tools=build_tools(exclude),
	                    save_conversation_path=str(run_dir / 'conversation'), **arm.overrides)
	await session.start()
	try:
		tabs = await session.get_tabs()
		host = task.url.split('/')[2]
		chosen = next((t for t in tabs if host in t.url), None)
		if chosen is None:
			raise RuntimeError(f'SETUP_FAILED: no tab on {host}; tabs={[t.url[:60] for t in tabs]}')
		await session.event_bus.dispatch(SwitchTabEvent(target_id=chosen.target_id))
		# After the switch — its AgentFocusChangedEvent clears the state cache, so a build before
		# it would be discarded — and before t0. row['seconds'] must keep meaning agent.run alone.
		# It is NOT unchanged in content, though: the first DomService construction and CDP domain
		# enablement now happen inside the probe rather than inside agent.run, while step 1's own
		# build becomes the expensive full one (~0.8 s) instead of the cheap blank one (~0.3 s).
		# On the fastest rows in the corpus (wiki-itn-lead at 6-10 s) that is a few percent, in
		# both directions at once. Compare `seconds` across the boundary with that in mind.
		dom_ready = await _wait_for_dom(session, enabled=arm.dom_ready, nav_t0=nav_t0)
		dom_ready['content'] = await _wait_for_content(chrome, host)
		# proxy.jsonl is written in COMPLETION order and a call abandoned by llm_timeout can land
		# up to 1800 s late, so slice by seq, never by position.
		seq0 = max((r.get('seq') or 0) for r in proxy.records()) if proxy.records() else 0
		t0 = time.monotonic()
		history = await agent.run(max_steps=max_steps)
		seconds = round(time.monotonic() - t0, 1)
	finally:
		try:
			await asyncio.wait_for(session.stop(), timeout=30)
		except Exception:
			pass
	history.save_to_file(run_dir / 'history.json')
	hist = history.model_dump()
	recs = sorted((r for r in proxy.records() if (r.get('seq') or 0) > seq0), key=lambda r: r.get('seq') or 0)
	return hist, seconds, recs, dom_ready


def _refused(msg: str) -> int:
	print(f'\nREFUSED TO START\n  {msg}\n', flush=True)
	return 2


async def cmd_run(args, tasks: list[G.Task], arms: list[Arm], mode: str) -> int:
	# Nothing below creates a run dir or touches the card until every refusal check passes.
	from browsin import browser as B
	from browsin.interlock import Interlock, card_preflight
	from browsin.lease import LeaseAssertionError, hold, normalise_tag
	from browsin.proxy import Proxy
	from warden.client import LeaseLost, WardenError

	if normalise_tag(WORKLOAD) != MODEL_TAG:
		return _refused(f'MODEL_TAG {MODEL_TAG!r} != normalise_tag({WORKLOAD!r}); the proxy would forward '
		                f'requests for a model the lease did not book')
	if not B._have_systemd_run():
		return _refused('systemd-run is unavailable, so B.stop() would be a silent no-op and a fresh Chrome per '
		                'run cannot be guaranteed (browsin/browser.py). Run from a session with XDG_RUNTIME_DIR.')
	take_lock()
	try:
		await card_preflight(evict=args.evict)
	except Interlock as exc:
		release_lock()
		return _refused(str(exc))
	except WardenError as exc:
		release_lock()
		return _refused(f'warden: {exc}')

	run_dir, scratch = _enter_run_dir(mode)
	# browser_use enters the process here; validate `set:` keys against the live signature
	# before a lease is taken so a typo costs nothing.
	from browsin.agent import check_agent_kwargs
	for arm in arms:
		if arm.overrides:
			try:
				check_agent_kwargs(arm.overrides)
			except TypeError as exc:
				release_lock()
				return _refused(f'arm {arm.spec!r}: {exc}')

	rows: list[dict] = []
	resumed: set[tuple] = set()
	if getattr(args, 'resume', None):
		prev = pathlib.Path(args.resume)
		prev_rows = _load_rows(prev)
		if prev_rows is None:
			release_lock()
			return _refused(f'--resume {prev}: no results.jsonl there')
		# Carry finished measurements forward; re-run everything the harness (not the model)
		# failed on, and the in-flight ABORTED run.
		for r in prev_rows:
			if r.get('outcome') in G.GRADED or r.get('outcome') in ('RACY', 'UNGRADED'):
				r['resumed_from'] = str(prev)
				rows.append(r)
				resumed.add((r['task'], r['rep'], r.get('arm', 'default')))
				_append(run_dir / 'results.jsonl', r)
		print(f'resuming: {len(rows)} finished run(s) carried from {prev}; the rest run now', flush=True)

	(run_dir / 'run.json').write_text(json.dumps({
		'mode': mode, 'label': args.label, 'reps': args.reps, 'arms': [a.spec for a in arms],
		'tasks': [_task_spec(t) for t in tasks], 'max_steps_override': getattr(args, 'max_steps', None),
		'ttl_s': args.ttl, 'may_evict': bool(args.evict), 'started': time.time(),
		'resumed_from': getattr(args, 'resume', None),
		# Provenance for the pre-run DOM-ready wait. Rows from before it and rows from after are
		# otherwise distinguishable only by timestamp; `compare A B` across that boundary would
		# silently mix eras. `compare` reads results.jsonl and not this file, so the rows carry
		# `dom_ready` too — this is the human-readable copy of the settings they were taken under.
		'dom_ready': {'min_elements': DOM_READY_MIN_ELEMENTS, 'timeout_s': DOM_READY_TIMEOUT_S,
		              'poll_s': DOM_READY_POLL_S},
	}, indent=1), encoding='utf-8')
	print(f'run dir: {run_dir}   label: {args.label or "-"}   arms: {[a.name for a in arms]}', flush=True)

	stale: dict[str, str] = {}
	inert_noted: set[tuple] = set()
	aborted = False
	in_flight: dict | None = None
	t0 = time.monotonic()
	try:
		async with hold(WORKLOAD, reason=f'test.py {mode}{(" " + args.label) if args.label else ""}',
		                num_ctx=NUM_CTX, ttl_s=args.ttl, may_evict=bool(args.evict)) as card:
			print(f'lease granted in {time.monotonic() - t0:.1f}s  served num_ctx={card.num_ctx}', flush=True)
			with Proxy(card.endpoint, run_dir / 'proxy.jsonl') as proxy:
				for rep in range(1, args.reps + 1):
					for task in tasks:
						for arm in arms:
							key = (task.name, rep, arm.name)
							if key in resumed or task.name in stale:
								continue
							if arm.spec != 'default' and not arm.applies_to(task):
								if (task.name, arm.name) not in inert_noted:
									inert_noted.add((task.name, arm.name))
									print(f'[{task.name} {arm.name}] skipped: arm is inert for a task that is not '
									      f'read_only (would be an identical duplicate of default)', flush=True)
								continue
							tag = f'[{task.name} rep{rep}' + (f' {arm.name}' if len(arms) > 1 else '') + ']'
							max_steps = getattr(args, 'max_steps', None) or task.max_steps
							sub = run_dir / (f'{task.name}-rep{rep}' + (f'-{arm.name}' if len(arms) > 1 else ''))
							sub.mkdir(parents=True, exist_ok=True)
							base = {'task': task.name, 'rep': rep, 'arm': arm.name, 'arm_spec': arm.spec,
							        'arm_effective': arm.applies_to(task), 'task_spec': _task_spec(task),
							        'run_dir': str(sub), 'max_steps': max_steps,
							        'proxy_log': str(run_dir / 'proxy.jsonl')}
							in_flight = base

							# truth, before
							expected: list[str] | None
							try:
								if task.absent:
									expected = []
								elif task.expect is G.nothing_to_find:
									expected = None   # a one-off with no --expect-from → UNGRADED
								else:
									expected = task.expect()
							except G.FixtureStale as exc:
								stale[task.name] = str(exc)
								row = dict(base, outcome='FIXTURE_STALE', correct=False, note=str(exc))
								rows.append(row); _append(run_dir / 'results.jsonl', row)
								print(f'{tag} FIXTURE_STALE — {exc}', flush=True)
								continue
							except G.TruthUnavailable as exc:
								row = dict(base, outcome='TRUTH_UNAVAILABLE', correct=False, note=str(exc))
								rows.append(row); _append(run_dir / 'results.jsonl', row)
								print(f'{tag} TRUTH_UNAVAILABLE — {exc}', flush=True)
								continue

							# fresh chrome + drive. ChromeError before RuntimeError: it IS a RuntimeError.
							try:
								chrome = await _fresh_chrome(B, task.url)
								# The reference clock for dom_ready.nav_ms: B.start() returns 0-0.4 s
								# after the CDP port answers (it polls at 0.4 s), and the tab was
								# navigated by Chrome's own argv, so this is "page age" to within
								# that jitter — the only way to tell a defective first build from a
								# page that had simply not finished.
								nav_t0 = time.monotonic()
								print(f'{tag} chrome pid={chrome.pid} bind={chrome.bind}', flush=True)
								hist, seconds, recs, dom_ready = await _drive(task, arm, proxy=proxy, chrome=chrome,
								                                              scratch=scratch, run_dir=sub,
								                                              max_steps=max_steps, nav_t0=nav_t0)
							except B.NotLoopback:
								raise   # a security stop, never a setup failure
							except (LeaseLost, asyncio.CancelledError, KeyboardInterrupt):
								raise
							except B.ChromeError as exc:
								row = dict(base, outcome='SETUP_FAILED', correct=False, note=f'chrome: {exc}')
								rows.append(row); _append(run_dir / 'results.jsonl', row)
								print(f'{tag} SETUP_FAILED — chrome: {exc}', flush=True)
								continue
							except RuntimeError as exc:
								if 'SETUP_FAILED' not in str(exc):
									raise
								row = dict(base, outcome='SETUP_FAILED', correct=False, note=str(exc))
								rows.append(row); _append(run_dir / 'results.jsonl', row)
								print(f'{tag} SETUP_FAILED — {exc}', flush=True)
								continue
							except Exception as exc:
								# agent construction / session start / CDP: the batch survives, the
								# row says why, and a systematic fault shows up as a column of these.
								row = dict(base, outcome='SETUP_FAILED', correct=False,
								           note=f'{type(exc).__name__}: {exc}')
								rows.append(row); _append(run_dir / 'results.jsonl', row)
								print(f'{tag} SETUP_FAILED — {type(exc).__name__}: {exc}', flush=True)
								continue

							# grade
							row = G.grade(task, expected, hist)
							row.update(base)
							row['seconds'] = seconds
							# Top level, NEVER inside row['patterns']: diagnose counts every key of
							# that dict as a fired detector, so a field present on every run would
							# top the ROLLUP and hijack the NEXT footer.
							row['dom_ready'] = dom_ready
							seqs = [r.get('seq') for r in recs if r.get('seq') is not None]
							row['proxy_seqs'] = seqs
							row['proxy_seq'] = [min(seqs), max(seqs)] if seqs else None
							truth_note = ''
							if expected:
								try:
									after = task.expect()
								except Exception as exc:
									after = None
									truth_note = f'(post-run truth fetch failed: {type(exc).__name__}; treated as unchanged)'
								if after is not None and after != expected:
									row['expected_after'] = after
									if G.contains_all(row['final'], after):
										row['outcome'], row['correct'] = 'CORRECT', True
										truth_note = f'MOVED during run → {after}; final matched the post-run truth'
									elif row['outcome'] == 'WRONG_ANSWER':
										row['outcome'] = 'RACY'
										truth_note = f'MOVED during run → {after}; final matched neither'
									else:
										truth_note = f'MOVED during run → {after}; outcome does not depend on it'
								elif after is not None:
									truth_note = 'truth stable'
							row['truth_note'] = truth_note

							found = D.detect(task, expected, hist, row, recs, max_steps)
							row['patterns'] = found
							rows.append(row)
							_append(run_dir / 'results.jsonl', row)
							in_flight = None

							flag = ''
							if row.get('had_then_lost'):
								flag = '  << HAD-THEN-LOST'
							if row.get('substituted'):
								flag += f"  << SUBSTITUTED {row['substituted']}"
							# Two different faults, two different flags: a timeout means the DOM
							# really never filled; an error means the probe broke while reading a
							# DOM that may have been perfectly full, and the wait was a no-op.
							if dom_ready.get('error'):
								flag += f"  << dom-ready PROBE ERROR {dom_ready['error']}"
							elif dom_ready.get('timed_out'):
								flag += '  << dom-ready NEVER FILLED'
							dom_txt = ('dom=off ' if not dom_ready.get('enabled') else
							           f"dom={dom_ready['builds']}b/{dom_ready['ms']}ms/{dom_ready['elements']}el ")
							print(f"{tag} {row['outcome']} {row['steps']}st {seconds}s waste={row['wasted_actions']} "
							      f"{dom_txt}-> {row['final'][:80]!r}{flag}", flush=True)
							block = D.render(task, rep, arm.name, row, found, hist, recs, str(sub), truth_note, max_steps)
							(sub / 'DIAGNOSIS.txt').write_text(block + '\n', encoding='utf-8')
							if row['outcome'] != 'CORRECT' or D.is_near_miss(row, D.median_correct_steps(rows, task.name)):
								print(block, flush=True)
							if mode == 'one':
								_print_call_stats(recs, card.num_ctx, run_dir)
	except (KeyboardInterrupt, asyncio.CancelledError) as exc:   # browsin.lease.Interrupted is a KeyboardInterrupt
		aborted = True
		print(f'\nABORTED ({type(exc).__name__}) — partial results kept; resume with:  '
		      f'tools/test.py run --resume {run_dir}', flush=True)
	except LeaseLost as exc:
		aborted = True
		print(f'\nABORTED (lease lost: {exc}) — partial results kept; resume with:  '
		      f'tools/test.py run --resume {run_dir}', flush=True)
	except (WardenError, LeaseAssertionError) as exc:
		release_lock()
		return _refused(f'{type(exc).__name__}: {exc}')
	except RuntimeError as exc:
		if 'already bound' in str(exc):   # browsin.proxy refusing a second listener on :11434
			release_lock()
			return _refused(str(exc))
		release_lock()
		raise
	finally:
		release_lock()

	if aborted and in_flight is not None:
		row = dict(in_flight, outcome='ABORTED', correct=False, note='batch interrupted while this run was in flight')
		rows.append(row)
		_append(run_dir / 'results.jsonl', row)

	return _summarise(run_dir, rows, tasks, arms, args, aborted, mode)


def _print_call_stats(recs: list[dict], num_ctx: int, run_dir: pathlib.Path) -> None:
	chats = [r for r in recs if str(r.get('path', '')).startswith('/api/chat')]
	counts = [(r.get('response') or {}).get('prompt_eval_count') for r in chats]
	imgs = [(r.get('request') or {}).get('image_count') for r in chats]
	print(f'\n  prompt tokens per call: {counts}')
	print(f'  images per call:        {imgs}')
	print(f'  served window:          {num_ctx}')
	print(f'  proxy log:              {run_dir / "proxy.jsonl"}', flush=True)


def _dom_ready_lines(rows: list[dict], arms) -> list[str]:
	"""Harness provenance for the pre-run DOM-ready wait. Never enters the completion rate.

	Per arm, because an A/B's claim that both arms met the same page has to be checked rather
	than assumed — and because `--arms default,no-dom-ready` makes one arm's wait deliberately
	absent. The last line is the only one that can falsify the fix: a run whose probe ended on a
	full DOM and whose step 1 STILL carried a loading cue means the blank first state is not a
	load race and the wait is not the mechanism. Without it a 0/8 proves only that something
	changed.
	"""
	out: list[str] = []
	for arm in arms:
		dr = [r['dom_ready'] for r in rows
		      if r.get('arm', 'default') == arm.name and isinstance(r.get('dom_ready'), dict)]
		if not dr:
			continue
		on = [d for d in dr if d.get('enabled')]
		if not on:
			out.append(f'  dom-ready wait [{arm.name}]: DISABLED (control arm) over {len(dr)} run(s)')
			continue
		bad = sum(1 for d in on if d.get('timed_out'))
		err = sum(1 for d in on if d.get('error'))
		first_full = sum(1 for d in on if (d.get('builds') or 0) == 1 and not d.get('timed_out') and not d.get('error'))
		out.append(f'  dom-ready wait [{arm.name}]: '
		           f"{sum(d.get('builds') or 0 for d in on) / len(on):.1f} builds, "
		           f"{sum(d.get('ms') or 0 for d in on) / len(on):.0f} ms mean over {len(on)} run(s); "
		           f'{first_full} full on the first build; {bad} never filled; {err} probe error(s)')
	full = [r for r in rows if isinstance(r.get('dom_ready'), dict) and r['dom_ready'].get('enabled')
	        and not r['dom_ready'].get('timed_out') and not r['dom_ready'].get('error')
	        and (r['dom_ready'].get('elements') or 0) >= DOM_READY_MIN_ELEMENTS]
	if full:
		still = sum(1 for r in full if 'blank_first_state' in (r.get('patterns') or {}))
		out.append(f'  probe ended on a full DOM in {len(full)} run(s); blank_first_state still fired in {still}'
		           + ('  << the wait is NOT the mechanism — read those step1 <page_stats> lines' if still else ''))
	return out


def _summarise(run_dir, rows, tasks, arms, args, aborted, mode) -> int:
	from collections import Counter
	lines = ['', '=' * 78,
	         'browsin — task completion, graded against independently fetched ground truth',
	         '=' * 78]
	for task in tasks:
		trs = [r for r in rows if r['task'] == task.name]
		if len(arms) > 1:
			for arm in arms:
				ars = [r for r in trs if r.get('arm', 'default') == arm.name]
				if ars:
					lines.append(D.rate_line(f'{task.name} [{arm.name}]', ars))
		else:
			lines.append(D.rate_line(task.name, trs))
	graded = [r for r in rows if r.get('outcome') in G.GRADED]
	passed = sum(1 for r in graded if r['correct'])
	excluded = [r for r in rows if r.get('outcome') not in G.GRADED]
	if graded:
		lo, hi = G.wilson(passed, len(graded))
		lines.append(f'\n  completion rate: {passed}/{len(graded)} = {100 * passed / len(graded):.0f}%  (80% CI {lo:.0%}–{hi:.0%})')
		hl = sum(1 for r in graded if r.get('had_then_lost'))
		lines.append(f'  of the {len(graded) - passed} miss(es), {hl} had the correct answer in memory and dropped it')
	if excluded:
		lines.append(f"  {len(excluded)} run(s) excluded from the rate: "
		             + ', '.join(f'{o} {c}' for o, c in Counter(r['outcome'] for r in excluded).items()))
	lines.extend(_dom_ready_lines(rows, arms))
	lines.append('')
	lines.append(D.rollup(rows))
	lines.append('')
	lines.append(D.next_footer(rows))
	lines.append(f'\n  run dir: {run_dir}')
	lines.append("  NOTE: graded on ground truth, never on the agent's own done/success flag.")
	if aborted:
		lines.append(f'  ABORTED — partial. Resume: tools/test.py run --resume {run_dir}')
	text = '\n'.join(lines)
	print(text, flush=True)
	(run_dir / 'summary.txt').write_text(text + '\n', encoding='utf-8')

	if aborted:
		return 1
	if not graded:
		if mode == 'one' and rows and all(r.get('outcome') == 'UNGRADED' for r in rows):
			return 0   # a completed, deliberately ungraded one-off is not a failure
		print('  nothing was graded — exit 1', flush=True)
		return 1
	if getattr(args, 'gate', False):
		rate = passed / len(graded)
		ok = rate >= args.min_rate
		print(f'  GATE: rate {rate:.0%} {">=" if ok else "<"} {args.min_rate:.0%} → {"PASSED" if ok else "FAILED"}', flush=True)
		return 0 if ok else 1
	return 0


# ── offline commands ────────────────────────────────────────────────────────────────────

def _proxy_slice(log_path: pathlib.Path, seqs) -> list[dict]:
	"""Records whose seq is in `seqs` (a list) or within [lo, hi] (a pair)."""
	if not log_path.exists() or not seqs:
		return []
	want = set(seqs) if len(seqs) != 2 or isinstance(seqs, list) and len(seqs) > 2 else None
	lo, hi = (min(seqs), max(seqs)) if want is None else (None, None)
	out = []
	for line in log_path.read_text(encoding='utf-8').splitlines():
		try:
			r = json.loads(line)
		except Exception:
			continue
		s = r.get('seq')
		if s is None:
			continue
		if (want is not None and s in want) or (want is None and lo <= s <= hi):
			out.append(r)
	return sorted(out, key=lambda r: r.get('seq') or 0)


def _row_task(r: dict) -> G.Task:
	"""The task AS IT WAS RUN — from the recorded spec, so a later prompt/budget edit cannot
	change how an old run is read."""
	if r.get('task_spec'):
		spec = dict(r['task_spec'])
		spec['max_steps'] = r.get('max_steps') or spec.get('max_steps', 8)
		return _task_from_spec(spec)
	return G.TASKS_BY_NAME[r['task']]


def cmd_diagnose(args) -> int:
	run_dir = pathlib.Path(args.rundir)
	rows = _load_rows(run_dir)
	if rows is None:
		print(f'no results.jsonl under {run_dir}')
		return 1
	shown = 0
	for r in rows:
		if r.get('outcome') == 'CORRECT' and not args.all and \
		   not D.is_near_miss(r, D.median_correct_steps(rows, r['task'])):
			continue
		sub = pathlib.Path(r.get('run_dir') or run_dir)
		hp = sub / 'history.json'
		if not hp.exists():
			print(f"[{r['task']} rep{r['rep']}] {r['outcome']} — no history.json ({r.get('note', '')})")
			continue
		task = _row_task(r)
		hist = G.load_history(hp)
		log_path = pathlib.Path(r['proxy_log']) if r.get('proxy_log') else sub.parent / 'proxy.jsonl'
		recs = _proxy_slice(log_path, r.get('proxy_seqs') or r.get('proxy_seq'))
		row = G.grade(task, r.get('expected'), hist)
		# keep what only the live run could know: the post-run truth check, its timing, and the
		# pre-run DOM-ready wait (history.json cannot reconstruct any of them)
		for k in ('outcome', 'correct', 'seconds', 'expected_after', 'truth_note', 'arm_effective',
		          'dom_ready'):
			if k in r:
				row[k] = r[k]
		found = D.detect(task, r.get('expected'), hist, row, recs, r.get('max_steps') or task.max_steps)
		print(D.render(task, r['rep'], r.get('arm', 'default'), row, found, hist, recs, str(sub),
		               r.get('truth_note', ''), r.get('max_steps') or task.max_steps))
		print()
		shown += 1
	print(D.rollup(rows))
	print(D.next_footer(rows))
	print(f'\n{shown} diagnosis block(s) rendered from {run_dir}')
	return 0


def cmd_compare(args) -> int:
	a = pathlib.Path(args.a)
	rows_a = _load_rows(a)
	if rows_a is None:
		print(f'no results.jsonl under {a}')
		return 1
	if args.b:
		b = pathlib.Path(args.b)
		rows_b = _load_rows(b)
		if rows_b is None:
			print(f'no results.jsonl under {b}')
			return 1
		print(D.compare(rows_a, rows_b, a.name, b.name, args.min_reps))
		return 0
	arms = sorted({r.get('arm', 'default') for r in rows_a})
	if len(arms) < 2:
		print(f'{a} has a single arm ({arms}); pass a second run dir to compare against')
		return 1
	base = [r for r in rows_a if r.get('arm', 'default') == arms[0]]
	for other in arms[1:]:
		print(D.compare(base, [r for r in rows_a if r.get('arm', 'default') == other], arms[0], other, args.min_reps))
		print()
	return 0


# ── self-check: every scorer branch and every detector, no card, no browser ─────────────

def _step(n, memory='', action=None, url='https://x.test/', result=None, state_message='', eval_text='', next_goal=''):
	return {
		'model_output': None if action is None else {
			'evaluation_previous_goal': eval_text, 'memory': memory, 'next_goal': next_goal, 'action': [action]},
		'result': result if result is not None else [{'extracted_content': 'ok'}],
		'state': {'url': url, 'title': '', 'tabs': [], 'screenshot_path': f'/nonexistent/step_{n}.png',
		          'interacted_element': [{'node_name': 'A'}] if action and next(iter(action)) in ('click', 'input') else [None]},
		'metadata': {'step_number': n, 'step_start_time': 0, 'step_end_time': 1},
		'state_message': state_message,
	}


def _hist(*steps_, tail=False):
	h = {'history': list(steps_)}
	if tail:
		h['history'].append({'model_output': None, 'result': [{'error': 'Failed to complete task in maximum steps'}],
		                     'state': {'url': '', 'tabs': [], 'screenshot_path': None, 'interacted_element': []},
		                     'metadata': None, 'state_message': None})
	return h


# the exact wording browser-use 0.13.8 emits (agent/prompts.py)
TOP_MSG = '<page_info>0.0 pages above, 3.3 pages below, 4.3 total pages</page_info>\n[Start of page]\n[1]<a>x</a>'
MID_MSG = '<page_info>0.9 pages above, 2.4 pages below, 4.3 total pages</page_info>\n[1]<a>x</a>'
MID2_MSG = '<page_info>2.4 pages above, 0.9 pages below, 4.3 total pages</page_info>\n[1]<a>x</a>'
BOT_MSG = '<page_info>0.6 pages above, 0.0 pages below, 1.6 total pages</page_info>\n[1]<a>x</a>\n[End of page]'
BLANK_MSG = '<page_info>0.0 pages above, 0.0 pages below, 1.0 total pages</page_info>\nInteractive elements:\nempty page'
# The two <page_stats> lines browser-use actually emitted on Hacker News, copied verbatim from
# runs/test-run-20260905-052408/hn-top-story-rep1/history.json steps 1 and 2. The blank one is the
# 'Page appears empty' cue on its own — no 'empty page', no model wording — which is the shape all
# ten HN runs of the 2026-09-05 corpus produced and the one the DOM-ready wait has to remove.
HN_BLANK_STATS = ('<page_stats>Page appears empty - consider waiting - 0 links, 0 interactive, '
                  '0 iframes, 2 total elements</page_stats>')
HN_FULL_STATS = ('<page_stats>228 links, 543 interactive, 0 iframes, 1 shadow(open), '
                 '0 shadow(closed), 2 images, 1094 total elements</page_stats>')
# browser-use's SECOND loading cue (agent/prompts.py:236-243) — the elif that is structurally
# unreachable while total_elements is 2 and becomes reachable exactly when a fix fills the DOM.
# It appears ZERO times in the corpus, which is why a detector that grepped only the first
# wording could have read a cue swap as a clean 0/8 pass.
HN_LOADING_STATS = ('<page_stats>3 network request(s) in flight and little text rendered - page may '
                    'still be loading, consider waiting - 228 links, 543 interactive, 0 iframes, '
                    '1094 total elements</page_stats>')


def cmd_self_check(_args) -> int:
	checks: list[tuple[str, bool, str]] = []

	def ok(label, cond, detail=''):
		checks.append((label, bool(cond), detail))
		print(f'  [{"PASS" if cond else "FAIL"}] {label}{("  " + detail) if detail else ""}')

	runs_before = set(p.name for p in (REPO / 'runs').iterdir()) if (REPO / 'runs').exists() else set()
	RO = G.Task('t', 'https://x.test/', "Do not click any links and do not type into any fields. Report the widget. Then call done.",
	            lambda: ['Widget X'], read_only=True, max_steps=6)
	FREE = G.Task('f', 'https://x.test/', "Use the search box to search for 'Ada Lovelace'. Report her birth year. Then call done.",
	              lambda: ['1815'], max_steps=8)
	ABS = G.Task('a', 'https://x.test/', "Look for 'Weather forecast'. If absent, say so. Then call done.",
	             G.nothing_to_find, absent=True, forbid=['In the news', 'On this day'], read_only=True)
	DONE = lambda text, success=True: {'done': {'text': text, 'success': success}}  # noqa: E731

	print('== truth text: entities and boundaries')
	ok("_strip_tags decodes entities (Fermat&#x27;s → Fermat's)",
	   G._strip_tags('<a href="x">Formalizing Fermat&#x27;s Last Theorem &amp; more</a>') == "Formalizing Fermat's Last Theorem & more")
	r = G.grade(RO, [G._strip_tags('Fermat&#x27;s Last Theorem')], _hist(_step(1, "top is Fermat's Last Theorem", DONE("The #1 story is 'Fermat's Last Theorem'."))))
	ok('an entity-bearing title grades CORRECT against the apostrophe the model reads', r['outcome'] == 'CORRECT')
	ok("'1815' is not found inside '18150'", G.grade(FREE, ['1815'], _hist(_step(1, 'x', DONE('The article has 18150 words.'))))['outcome'] != 'CORRECT')
	ok("'1815' is found standing alone", G.grade(FREE, ['1815'], _hist(_step(1, 'x', DONE('Born in 1815.'))))['outcome'] == 'CORRECT')
	ok('Task refuses expect=callable() (a list)', _raises(lambda: G.Task('z', 'u', 'p', G.nothing_to_find()), TypeError))
	for bad in ('hn:0', 'hn:', 'wiki:Page', 'wiki::phrase', 'bogus'):
		ok(f'expect_from({bad!r}) raises ValueError before any card time', _raises(lambda: G.expect_from(bad), ValueError))
	e, a, f = G.expect_from('wiki:Ada_Lovelace:1815|Byron')
	ok('expect_from wiki parses two phrases', e.__name__ == 'wikipedia_contains_Ada_Lovelace' and not a)
	e, a, f = G.expect_from('absent:In the news,On this day')
	ok('expect_from absent parses the forbid list', a and f == ['In the news', 'On this day'])

	print('== grade(): outcomes')
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'the answer is Widget X', DONE('The answer is Widget X.'))))
	ok('correct final → CORRECT, 1 step', r['outcome'] == 'CORRECT' and r['steps'] == 1 and not r['had_then_lost'])
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'I think it is Widget Q', DONE('The answer is Widget Q.'))))
	ok('wrong, never had it → WRONG_ANSWER, had_then_lost False', r['outcome'] == 'WRONG_ANSWER' and r['had_then_lost'] is False)
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'the top item is Widget X', {'input': {'index': 3, 'text': 'Widget X'}}),
	                                   _step(2, 'the top item is Widget Q', DONE('The answer is Widget Q.'))))
	ok('had it, dropped it → WRONG_ANSWER, had_then_lost True, first=1 lost=2',
	   r['outcome'] == 'WRONG_ANSWER' and r['had_then_lost'] and r['first_seen_step'] == 1 and r['lost_at_step'] == 2,
	   f"got first={r['first_seen_step']} lost={r['lost_at_step']}")
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'Widget X', {'scroll': {'down': True}}),
	                                   _step(2, result=[{'error': '1 validation error for AgentOutput\n Invalid JSON: EOF'}]),
	                                   _step(3, 'Widget X still', {'scroll': {'down': True}}),
	                                   _step(4, 'Widget Q now', DONE('Widget Q'))))
	ok('lost_at skips a parse-fail step and points at the real overwrite (4)', r['lost_at_step'] == 4, f"got {r['lost_at_step']}")
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'searching', {'search_page': {'pattern': 'x'}}, result=[{'extracted_content': 'found: Widget X at line 3'}]),
	                                   _step(2, 'nothing yet', DONE('Widget Q'))))
	ok('answer only in a PAGE result → shown_in_result_step, NOT had_then_lost',
	   r['shown_in_result_step'] == 1 and r['first_seen_step'] is None and not r['had_then_lost'])
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'the top story is the first row', {'input': {'index': 3, 'text': 'Widget X'}}),
	                                   _step(2, 'the top story is Widget Q', DONE('Widget Q'))))
	ok("the model's own TYPED text counts as held → had_then_lost (first=1, lost=2), the real HN signature",
	   r['had_then_lost'] and r['first_seen_step'] == 1 and r['lost_at_step'] == 2 and r['shown_in_result_step'] is None)
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'nope', DONE('The answer is Widget Q.', True))))
	ok("success=True does not rescue a wrong answer (§5)", r['outcome'] == 'WRONG_ANSWER' and r['agent_success'] is True)
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'looking', {'scroll': {'down': True}}), _step(2, 'looking', {'scroll': {'down': True}}), tail=True))
	ok('no done at all → NO_ANSWER', r['outcome'] == 'NO_ANSWER' and not r['agent_said_done'])
	ok('an EMPTY done → NO_ANSWER', G.grade(RO, ['Widget X'], _hist(_step(1, 'x', DONE(''))))['outcome'] == 'NO_ANSWER')
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'searching', DONE('The section is not visible yet. I will continue scrolling.', False))))
	ok('status report with no candidate → HONEST_MISS', r['outcome'] == 'HONEST_MISS')
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 'searching', DONE("The section is not visible yet, but the title is 'Something Completely Else'.", False))))
	ok('status report WITH a quoted candidate → WRONG_ANSWER', r['outcome'] == 'WRONG_ANSWER')
	r = G.grade(RO, ['Widget X'], _hist(_step(1, 's', DONE("I couldn't find the bacterium's name; the wreck's section is not visible yet.", False))))
	ok('contractions are not quote delimiters → still HONEST_MISS', r['outcome'] == 'HONEST_MISS')
	ok('expected None → UNGRADED', G.grade(RO, None, _hist(_step(1, 'x', DONE('whatever'))))['outcome'] == 'UNGRADED')
	print('== grade(): absent task')
	ok('admits absence → CORRECT', G.grade(ABS, [], _hist(_step(1, 'm', DONE('That section does not exist on this page.', False))))['outcome'] == 'CORRECT')
	r = G.grade(ABS, [], _hist(_step(1, 'm', DONE("There is no 'Weather forecast' section; the page has In the news and On this day boxes."))))
	ok('naming the real sections while denying → CORRECT, named_other_sections', r['outcome'] == 'CORRECT' and r['named_other_sections'] == ['In the news', 'On this day'])
	r = G.grade(ABS, [], _hist(_step(1, 'm', DONE("Here is the In the news box content instead: 'Gloria Steinem dies at 92'."))))
	ok('substituting real content → WRONG_ANSWER, named', r['outcome'] == 'WRONG_ANSWER' and r['substituted'] == ['In the news'])
	ok('inventing content → WRONG_ANSWER', G.grade(ABS, [], _hist(_step(1, 'm', DONE('The weather forecast is sunny.'))))['outcome'] == 'WRONG_ANSWER')
	print('== grade(): wasted actions')
	h = _hist(_step(1, 'm', {'input': {'index': 1, 'text': 'z'}}), _step(2, 'm', {'click': {'index': 2}}),
	          _step(3, 'm', {'scroll': {'down': True}}), _step(4, 'm', {'send_keys': {'keys': 'Enter'}}), _step(5, 'm Widget X', DONE('Widget X')))
	ok('read-only: input+click+send_keys counted, scroll/done not (==3)', G.grade(RO, ['Widget X'], h)['wasted_actions'] == 3)
	ok('interaction allowed: no waste', G.grade(FREE, ['Widget X'], h)['wasted_actions'] == 0)

	print('== detect(): each detector fires on its positive and stays silent on its negative')

	def det(task, exp, hist, recs=None, ms=None):
		row = G.grade(task, exp, hist)
		return D.detect(task, exp, hist, row, recs, ms or task.max_steps), row

	# viewport reader on the real wording
	ok('viewport() TOP/MID/BOTTOM/ALL/blank from real <page_info>',
	   [D.viewport(_step(1, state_message=m)) for m in (TOP_MSG, MID_MSG, BOT_MSG, BLANK_MSG, 'no page info')] == ['TOP', 'MID', 'BOTTOM', 'ALL', '?'])
	# had_then_lost + viewport jump (the overwrite mechanism)
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'top is Widget X', {'input': {'index': 3, 'text': 'Widget X'}}, state_message=TOP_MSG),
	                                  _step(2, 'top is Widget Q', DONE('Widget Q'), state_message=BOT_MSG)))
	ok('viewport_moved_after_input fires (0.0→0.6 pages, no scroll, same url)', f.get('viewport_moved_after_input', {}).get('step') == 1)
	ok('… and had_then_lost fires with it', 'had_then_lost' in f)
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'top is Widget X', {'input': {'index': 3, 'text': 'Widget X'}}, state_message=MID_MSG),
	                                  _step(2, 'x', DONE('Widget X'), state_message=MID2_MSG)))
	ok('… fires on a MID→MID jump (0.9→2.4)', 'viewport_moved_after_input' in f)
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'top is Widget X', {'input': {'index': 3, 'text': 'Widget X'}}, state_message=MID_MSG),
	                                  _step(2, 'x', DONE('Widget X'), state_message=MID_MSG.replace('0.9', '1.0'))))
	ok('… silent under the 0.3-page threshold', 'viewport_moved_after_input' not in f)
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'top is Widget X', {'scroll': {'down': True}}, state_message=TOP_MSG),
	                                  _step(2, 'Widget X', DONE('Widget X'), state_message=BOT_MSG)))
	ok('… silent when a scroll explains the move', 'viewport_moved_after_input' not in f)
	# scroll direction (word boundaries)
	f, _ = det(RO, ['W'], _hist(_step(1, 'scrolled down to find the section', {'scroll': {'down': False}}, next_goal='continue scrolling down'), _step(2, 'w', DONE('W'))))
	ok('scroll_direction_inverted fires (down=False, narrative says down)', f.get('scroll_direction_inverted', {}).get('k') == 1)
	f, _ = det(RO, ['W'], _hist(_step(1, 'scrolled down; will update the count', {'scroll': {'down': False}}, next_goal='continue scrolling down'), _step(2, 'w', DONE('W'))))
	ok("… 'update' does not suppress it (word boundary)", 'scroll_direction_inverted' in f)
	f, _ = det(RO, ['W'], _hist(_step(1, 'went too far, scrolling back up', {'scroll': {'down': False}}), _step(2, 'w', DONE('W'))))
	ok('… silent when narrative says back up', 'scroll_direction_inverted' not in f)
	f, _ = det(RO, ['W'], _hist(_step(1, 'scroll down', {'scroll': {'down': True}}), _step(2, 'w', DONE('W'))))
	ok('… silent on down=True', 'scroll_direction_inverted' not in f)
	f, _ = det(RO, ['W'], _hist(_step(1, 's', {'scroll': {'down': True, 'pages': 3}}), _step(2, 's', {'scroll': {'down': True, 'pages': 0.5}}), _step(3, 'W', DONE('W'))))
	ok('scroll_pages_gt1 counts 3 but not 0.5 (k=1 of 2)', f.get('scroll_pages_gt1') == {'k': 1, 'n': 2})
	f, _ = det(RO, ['W'], _hist(*[_step(i, 'looking', {'scroll': {'down': True}}) for i in range(1, 7)], _step(7, 'W', DONE('W'))), ms=8)
	ok('scroll_step_too_small fires on 6+ default-size scrolls', 'scroll_step_too_small' in f)
	# stale narrative after navigation
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'entered query', {'click': {'index': 5}}, url='https://x.test/Main', eval_text='typed'),
	                                _step(2, 'entered query', {'click': {'index': 9}}, url='https://x.test/Ada', eval_text='typed'),
	                                _step(3, 'born 1815', DONE('1815'), url='https://x.test/Ada')))
	ok('stale_narrative_after_navigation fires (url changed, memory+eval identical)', f.get('stale_narrative_after_navigation', {}).get('steps') == [2])
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'entered query', {'click': {'index': 5}}, url='https://x.test/Main'),
	                                _step(2, 'on the article now', {'click': {'index': 9}}, url='https://x.test/Ada'),
	                                _step(3, 'born 1815', DONE('1815'), url='https://x.test/Ada')))
	ok('… silent when the narrative updates', 'stale_narrative_after_navigation' not in f)
	f, _ = det(FREE, ['1815'], _hist(*[_step(i, 'same memory', {'click': {'index': 7}}) for i in (1, 2, 3, 4)], _step(5, 'x', DONE('x'))))
	ok('stuck_narrative fires on 4 identical memories', f.get('stuck_narrative', {}).get('n') == 4)
	# repeated action
	f, _ = det(FREE, ['1815'], _hist(*[_step(i, f'm{i}', {'click': {'index': 7}}) for i in (1, 2, 3)], _step(4, 'm', DONE('x'))))
	ok('repeated_action fires on 3 identical clicks', f.get('repeated_action', {}).get('n') == 3)
	f, _ = det(RO, ['W'], _hist(*[_step(i, f'scrolling {i}', {'scroll': {'down': True}}) for i in (1, 2, 3)], _step(4, 'W', DONE('W'))))
	ok('… silent on 3 identical scrolls (legitimate on a long page)', 'repeated_action' not in f)
	# stray interaction on read-only
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'the top is Widget X', {'input': {'index': 3, 'text': 'Widget X'}}), _step(2, 'Widget X', DONE('Widget X'))))
	ok('stray_input_on_read_only fires with typed_the_answer', f.get('stray_input_on_read_only') == {'n': 1, 'typed_the_answer': True})
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'the top is Widget X', {'send_keys': {'keys': 'Widget X'}}), _step(2, 'Widget X', DONE('Widget X'))))
	ok('… send_keys counts too, typed_the_answer via keys', f.get('stray_input_on_read_only') == {'n': 1, 'typed_the_answer': True})
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'm', {'input': {'index': 3, 'text': 'Ada Lovelace'}}), _step(2, '1815', DONE('1815'))))
	ok('… silent when typing is allowed and the text is the requested query', 'stray_input_on_read_only' not in f and 'answer_retyped_into_input' not in f)
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'born 1815', {'input': {'index': 3, 'text': 'born 1815'}}), _step(2, '1815', DONE('1815'))))
	ok('answer_retyped_into_input fires when typing is allowed but the text is the answer', f.get('answer_retyped_into_input', {}).get('n') == 1)
	# url / side-effect navigation
	f, _ = det(RO, ['W'], _hist(_step(1, 'm', {'click': {'index': 1}}, url='https://x.test/a'), _step(2, 'W', DONE('W'), url='https://x.test/b')))
	ok('url_changed_on_read_only fires', 'url_changed_on_read_only' in f)
	f, _ = det(RO, ['W'], _hist(_step(1, 'm', {'click': {'index': 1}}, url='https://x.test/a'), _step(2, 'W', DONE('W'), url='https://x.test/a#cite_note-1')))
	ok('… silent on a fragment-only change', 'url_changed_on_read_only' not in f)
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'm', {'input': {'index': 1, 'text': 'q'}}, url='https://x.test/a'), _step(2, '1815', DONE('1815'), url='https://x.test/a#cite_note-1')))
	ok('input_side_effect_navigation fires on an input that moved the URL (fragment counts)', 'input_side_effect_navigation' in f)
	# stale element index
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'm', {'input': {'index': 35, 'text': 'q'}}, result=[{'extracted_content': 'Element index 35 not available - page may have changed'}]),
	                                _step(2, 'm', {'scroll': {'down': True}}), _step(3, 'm', {'input': {'index': 35, 'text': 'q'}}), _step(4, '1815', DONE('1815'))))
	ok('stale_element_index_retry fires with one step in between (lookahead 2)', f.get('stale_element_index_retry', {}).get('index') == 35)
	# invented index
	h = _hist(_step(1, 'm', {'click': {'index': 999}}), _step(2, 'W', DONE('W')))
	h['history'][0]['state']['interacted_element'] = [None]
	f, _ = det(RO, ['W'], h)
	ok('invented_element_index fires when the index resolved to no element', f.get('invented_element_index', {}).get('n') == 1)
	# LLM failures
	f, _ = det(RO, ['W'], _hist(_step(1, result=[{'error': '16 validation errors for ActionModelUnion\nDoneActionModel\n Input should be ... PydanticUndefined'}]), _step(2, 'W', DONE('W'))))
	ok('empty_action classified from the ActionModelUnion signature', f.get('empty_action', {}).get('steps') == [1])
	f, _ = det(RO, ['W'], _hist(_step(1, result=[{'error': "16 validation errors for AgentOutput\naction.0.DoneActionModel.done\n  Field required [type=missing, input_value={}, input_type=dict]"}]), _step(2, 'W', DONE('W'))))
	ok('empty_action classified from Field required + input_value={}', f.get('empty_action', {}).get('steps') == [1])
	f, _ = det(RO, ['W'], _hist(_step(1, result=[{'error': "16 validation errors for AgentOutput\naction.0.ClickActionModel.click.index\n  Field required [type=missing, input_value={'click': {}}, input_type=dict]"}]), _step(2, 'W', DONE('W'))))
	ok('malformed_action classified from Field required on a non-empty action', f.get('malformed_action', {}).get('steps') == [1] and 'empty_action' not in f)
	f, _ = det(RO, ['W'], _hist(_step(1, result=[{'error': '1 validation error for AgentOutput\n Invalid JSON: EOF while parsing'}]), _step(2, 'W', DONE('W'))))
	ok('parse_fail classified from Invalid JSON', f.get('parse_fail', {}).get('steps') == [1])
	f, _ = det(RO, ['W'], _hist(_step(1, result=[{'error': 'LLM call timed out after 600 seconds. Keep your thinking and output short.'}]), _step(2, 'W', DONE('W'))))
	ok('llm_timeout classified from the timeout text', f.get('llm_timeout', {}).get('steps') == [1])
	f, _ = det(RO, ['W'], _hist(_step(1, 'looking', {'scroll': {'down': True}}, next_goal='No further actions needed as the task is complete'), _step(2, 'W', DONE('W'))))
	ok('declared_complete_without_done fires on the narrated completion', f.get('declared_complete_without_done', {}).get('step') == 1)
	# proxy: runaway vs slow vs aborted
	recs = [{'seq': 1, 'elapsed_s': 5, 'status': 200, 'response': {'done_reason': 'stop', 'eval_count': 160}},
	        {'seq': 2, 'elapsed_s': 26, 'status': 200, 'response': {'done_reason': 'length', 'eval_count': 1024}},
	        {'seq': 3, 'elapsed_s': 600.2, 'status': 'CLIENT_ABORTED'},
	        {'seq': 4, 'elapsed_s': 61, 'status': 200, 'response': {'done_reason': 'stop', 'eval_count': 170}}]
	f, _ = det(RO, ['W'], _hist(_step(1, 'W', DONE('W'))), recs)
	ok('runaway_generation fires only on done_reason=length / eval≥1024 (seq 2)', [c['seq'] for c in f.get('runaway_generation', {}).get('calls', [])] == [2])
	ok('a CLIENT_ABORTED call is aborted_llm_calls, not a runaway', f.get('aborted_llm_calls', {}).get('seq') == [3])
	ok('a slow normal call is slow_llm_call, not a runaway', [c['seq'] for c in f.get('slow_llm_call', {}).get('calls', [])] == [4])
	f, _ = det(RO, ['W'], _hist(_step(1, 'W', DONE('W'))), recs[:1])
	ok('… all silent on a normal 160-token call', not ({'runaway_generation', 'slow_llm_call', 'aborted_llm_calls'} & set(f)))
	# budget
	f, _ = det(RO, ['W'], _hist(*[_step(i, 'looking', {'scroll': {'down': True}}) for i in range(1, 7)], tail=True), ms=6)
	ok('budget_exhausted fires at max_steps with no done', 'budget_exhausted' in f)
	f, _ = det(RO, ['W'], _hist(*[_step(i, 'looking', {'scroll': {'down': True}}) for i in range(1, 6)], _step(6, 'W', DONE('W'))), ms=6)
	ok('done_only_when_forced fires when done lands exactly at max_steps', 'done_only_when_forced' in f and 'budget_exhausted' not in f)
	f, _ = det(RO, ['W'], _hist(_step(1, 'W', DONE('W'))), ms=6)
	ok('… silent for an immediate 1-step done', 'done_only_when_forced' not in f and 'budget_exhausted' not in f)
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 'Widget X', {'scroll': {'down': True}}), _step(2, 'Widget X', {'scroll': {'down': True}}), _step(3, 'Widget X', DONE('Widget X'))))
	ok('steps_after_first_seen counts the padding (2)', f.get('steps_after_first_seen', {}).get('n') == 2)
	# honesty / blank / typed-not-submitted / shown-not-held
	f, row = det(RO, ['W'], _hist(_step(1, 's', DONE('Could not find it yet.', False))))
	ok('honest_miss fires on HONEST_MISS', 'honest_miss' in f and row['outcome'] == 'HONEST_MISS')
	f, _ = det(RO, ['W'], _hist(_step(1, 'the page is empty', {'wait': {'seconds': 3}}, state_message=BLANK_MSG), _step(2, 'W', DONE('W'), state_message=TOP_MSG)))
	ok('blank_first_state fires and reports the reaction', f.get('blank_first_state', {}).get('reaction') == ['wait'])
	f, _ = det(RO, ['W'], _hist(_step(1, 'looking for the top story', {'scroll': {'down': True}},
	                                  state_message=HN_BLANK_STATS + '\n' + TOP_MSG),
	                            _step(2, 'W', DONE('W'), state_message=HN_FULL_STATS + '\n' + TOP_MSG)))
	ok('… fires on the corpus wording alone — the cue all 10 HN runs produced, with no model wording',
	   f.get('blank_first_state', {}).get('reaction') == ['scroll']
	   and f['blank_first_state']['cue'] == 'Page appears empty'
	   and '2 total elements' in f['blank_first_state']['stats'])
	f, _ = det(RO, ['W'], _hist(_step(1, 'the stories are loading', {'scroll': {'down': True}},
	                                  state_message=HN_LOADING_STATS + '\n' + TOP_MSG),
	                            _step(2, 'W', DONE('W'), state_message=HN_FULL_STATS + '\n' + TOP_MSG)))
	ok('… ALSO fires on the second cue, which only a filled DOM can reach — no cue-swap false pass',
	   f.get('blank_first_state', {}).get('cue') == 'network request(s) in flight')
	f, _ = det(RO, ['W'], _hist(_step(1, 'the top story is W', DONE('W'),
	                                  state_message=HN_FULL_STATS + '\n' + TOP_MSG)))
	ok('… and is SILENT on a full first state — the negative the DOM-ready wait is judged on',
	   'blank_first_state' not in f)
	pf = next((REPO / 'venv').glob('lib/python*/site-packages/browser_use/agent/prompts.py'), None)
	src = pf.read_text(encoding='utf-8') if pf else ''
	ok('both of browser-use\'s loading cues are still worded as the detector greps them',
	   "page_stats['total_elements'] < 10" in src
	   and 'Page appears empty - consider waiting - ' in src
	   and 'network request(s) in flight and little text rendered - ' in src, str(pf))
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'm', {'input': {'index': 2, 'text': 'Ada Lovelace'}}),
	                                _step(2, 'm', {'wait': {'seconds': 5}}), _step(3, 'm', {'wait': {'seconds': 5}}),
	                                _step(4, 'm', DONE('not yet returned results', False))))
	ok('typed_but_never_submitted fires (input then only waits, url constant)', 'typed_but_never_submitted' in f)
	f, _ = det(FREE, ['1815'], _hist(_step(1, 'm', {'input': {'index': 2, 'text': 'Ada Lovelace'}}),
	                                _step(2, 'm', {'click': {'index': 4}}), _step(3, '1815', DONE('1815'), url='https://x.test/Ada')))
	ok('… silent when a click follows', 'typed_but_never_submitted' not in f)
	f, _ = det(RO, ['Widget X'], _hist(_step(1, 's', {'search_page': {'pattern': 'x'}}, result=[{'extracted_content': 'found Widget X here'}]), _step(2, 'n', DONE('Widget Q'))))
	ok('shown_but_never_held fires when the answer was only ever in a result', f.get('shown_but_never_held', {}).get('step') == 1 and 'had_then_lost' not in f)

	print('== rollup / next / compare')
	rows = [{'task': 't', 'arm': 'default', 'outcome': 'HONEST_MISS', 'correct': False, 'steps': 3, 'wasted_actions': 0, 'patterns': {'honest_miss': {}}}] * 3
	ok('honest_miss never becomes the NEXT target', 'honest_miss' not in D.next_footer(rows) or 'nothing fixable' in D.next_footer(rows))
	rows = [{'task': 'wiki-scroll-deep', 'arm': 'default', 'outcome': 'CORRECT', 'correct': True, 'steps': 12, 'wasted_actions': 0,
	         'patterns': {'done_only_when_forced': {}}}] * 3 + \
	       [{'task': 'hn-top-story', 'arm': 'default', 'outcome': 'WRONG_ANSWER', 'correct': False, 'steps': 6, 'wasted_actions': 4,
	         'patterns': {'had_then_lost': {'first': 1, 'lost': 2}}}]
	ok('ROLLUP and NEXT agree on the top fixable pattern', 'had_then_lost' in D.rollup(rows).split('most frequent')[-1] and 'had_then_lost' in D.next_footer(rows))
	ok('NEXT names a runnable arm for had_then_lost', '--arms default,enforce-read-only' in D.next_footer(rows))
	rows2 = [{'task': 't', 'arm': 'default', 'outcome': 'NO_ANSWER', 'correct': False, 'steps': 8, 'wasted_actions': 0, 'patterns': {'budget_exhausted': {}}}] * 2
	ok('NEXT suggests observation, not a bogus arm, for an unmapped pattern', '--label look-budget_exhausted' in D.next_footer(rows2) and '<arm>' not in D.next_footer(rows2))
	# NOT_A_TARGET: the footer must not chase a symptom shared by unrelated mechanisms, nor a
	# transport counter. Shaped after runs/test-run-20260905-052408, where stuck_narrative won
	# with 6 runs that were a 2/2/2 tie across three tasks and four of which graded CORRECT.
	sn = [{'task': t, 'arm': 'default', 'outcome': o, 'correct': o == 'CORRECT', 'steps': 12, 'wasted_actions': 0,
	       'patterns': dict({'stuck_narrative': {'n': 7, 'from_step': 2}}, **extra)}
	      for t, o, extra in (('wiki-scroll-deep', 'WRONG_ANSWER', {'had_then_lost': {'first': 1, 'lost': 2}}),
	                          ('wiki-scroll-deep', 'CORRECT', {}), ('wiki-search-box', 'CORRECT', {}),
	                          ('wiki-search-box', 'HONEST_MISS', {}), ('hn-15th-story', 'CORRECT', {}),
	                          ('hn-15th-story', 'CORRECT', {}))]
	ok('stuck_narrative never becomes the NEXT target (6 runs, 3 mechanisms, no arm — 2026-09-05)',
	   'stuck_narrative' not in D.next_footer(sn) and 'had_then_lost' in D.next_footer(sn))
	ok('… nor the ROLLUP headline, while still showing in the per-task counts',
	   'stuck_narrative' not in D.rollup(sn).split('most frequent')[-1] and 'stuck_narrative 2/2' in D.rollup(sn))
	ab = [{'task': 'hn-top-story', 'arm': 'default', 'outcome': 'NO_ANSWER', 'correct': False, 'steps': 6, 'wasted_actions': 0,
	       'patterns': {'aborted_llm_calls': {'seq': [3]}}} for _ in range(4)] + \
	     [{'task': 'hn-top-story', 'arm': 'default', 'outcome': 'NO_ANSWER', 'correct': False, 'steps': 6, 'wasted_actions': 0,
	       'patterns': {'aborted_llm_calls': {'seq': [3]}, 'llm_timeout': {'steps': [3]}}}]
	ok('aborted_llm_calls loses to llm_timeout — the transport counter is not the mechanism',
	   'llm_timeout' in D.next_footer(ab) and 'aborted_llm_calls' not in D.next_footer(ab))
	inv = [{'task': 't', 'arm': 'default', 'outcome': 'WRONG_ANSWER', 'correct': False, 'steps': 4, 'wasted_actions': 0,
	        'patterns': {'invented_element_index': {'n': 2}, 'done_only_when_forced': {}}}] * 3
	ok('invented_element_index stays targetable — unobserved is not untargetable',
	   'invented_element_index' in D.next_footer(inv))
	inert = [{'task': 'f', 'arm': 'enforce-read-only', 'arm_effective': False, 'outcome': 'CORRECT', 'correct': True, 'steps': 3, 'wasted_actions': 0, 'patterns': {}}] * 6
	base = [dict(r, arm='default', arm_effective=True) for r in inert]
	ok('compare reports an inert arm instead of a verdict', 'ARM INERT' in D.compare(base, inert, 'a', 'b'))
	ok('compare refuses a verdict under 6 per arm', 'NO VERDICT' in D.compare(base[:3], [dict(r, arm_effective=True) for r in inert[:3]], 'a', 'b'))
	ok('rate_line on all-excluded rows does not raise', 'no graded runs' in D.rate_line('x', [{'task': 'x', 'outcome': 'RACY'}]))
	ok('render tolerates missing screenshot paths and None fields',
	   'DIAGNOSIS' in D.render(RO, 1, 'default', G.grade(RO, ['W'], _hist(_step(1, 'W', DONE('W')))), {}, _hist(_step(1, 'W', DONE('W'))), None, '/r'))
	# the trace's URL column: /wiki/Ada_Lovelace and /wiki/Ada_Lovelace_Award are the real pair
	# a 34-column head-truncation collapsed into one string (wiki-search-box-rep3, step 5).
	ada = _hist(_step(1, 'searching', {'input': {'index': 321, 'text': 'Ada Lovelace'}}, url='https://en.wikipedia.org/wiki/Main_Page'),
	            _step(2, 'clicked the result', {'click': {'index': 4296}}, url='https://en.wikipedia.org/wiki/Ada_Lovelace'),
	            _step(3, 'clicked the result', {'click': {'index': 5010}}, url='https://en.wikipedia.org/wiki/Ada_Lovelace_Award'),
	            _step(4, 'no year yet', DONE('I could not find her birth year.', False), url='https://en.wikipedia.org/wiki/Ada_Lovelace_Award'))
	block = D.render(FREE, 3, 'default', G.grade(FREE, ['1815'], ada), {}, ada, None, '/r')
	ok('the trace shows /wiki/Ada_Lovelace_Award in full, not truncated to /wiki/Ada_Lovelac…',
	   'en.wikipedia.org/wiki/Ada_Lovelace_Award' in block and 'Ada_Lovelac…' not in block)
	ok('… so the two Ada URLs both render whole, neither elided into the other',
	   [D.short_url('https://en.wikipedia.org/wiki/Ada_Lovelace'), D.short_url('https://en.wikipedia.org/wiki/Ada_Lovelace_Award')]
	   == ['en.wikipedia.org/wiki/Ada_Lovelace', 'en.wikipedia.org/wiki/Ada_Lovelace_Award'])
	ok('a URL past the column keeps its host and its tail, and never overflows URL_W',
	   D.short_url('https://engineering.atspotify.com/2026/9/portal-cut-my-token-usage-by-90').startswith('engineering.atspotify.com…')
	   and D.short_url('https://x.test/' + 'a' * 80).endswith('a' * 8)
	   and max(len(D.short_url(u)) for u in ('https://x.test/' + 'a' * 400, 'https://' + 'h' * 90 + '.test/p/q',
	                                         'https://en.wikipedia.org/wiki/A?b=c#a_long_fragment')) <= D.URL_W)

	print('== arms')
	ok('Arm refuses set:enable_signal_handler', _raises(lambda: Arm('set:enable_signal_handler=true'), SystemExit))
	ok('Arm refuses an empty set: key', _raises(lambda: Arm('set:=1'), SystemExit))
	ok('Arm refuses a missing sysmsg file', _raises(lambda: Arm('sysmsg:/nonexistent/prompt.txt'), SystemExit))
	ok('Arm refuses an unknown spec', _raises(lambda: Arm('bogus'), SystemExit))
	ok('set: arm names are slugs safe for a directory', Arm('set:foo=a/b').name == 'set-foo-a-b' and Arm('set:max_history_items=4').overrides == {'max_history_items': 4})
	ok('enforce-read-only is inert on a non-read-only task', not Arm('enforce-read-only').applies_to(FREE) and Arm('enforce-read-only').applies_to(RO))

	print('== statistics')
	lo, hi = G.wilson(1, 3)
	ok('wilson(1,3) brackets 0.33 with a wide 80% interval', lo < 0.34 < hi and hi - lo > 0.4, f'{lo:.2f}–{hi:.2f}')
	ok('fisher 2/8 vs 7/8 is significant (<0.1)', G.fisher_two_sided(2, 6, 7, 1) < 0.1, f'{G.fisher_two_sided(2, 6, 7, 1):.3f}')
	ok('fisher 3/8 vs 6/8 is not (>0.1)', G.fisher_two_sided(3, 5, 6, 2) > 0.1, f'{G.fisher_two_sided(3, 5, 6, 2):.3f}')
	print('== dom-ready wait: the mirror and the loop, against fakes (no card, no browser)')

	class _Node:            # a SimplifiedNode stand-in — the mirror reads .original_node and .children
		def __init__(self, *kids):
			self.original_node, self.children = object(), list(kids)

	class _DomState:        # a SerializedDOMState stand-in
		def __init__(self, n, interactive=0):
			self._root = _Node(*[_Node() for _ in range(n - 1)]) if n else None
			self.selector_map = {i: object() for i in range(interactive)}

	class _FakeSession:
		"""Hands out canned states in order, then repeats the last, counting builds and resets."""

		def __init__(self, *states):
			self.states, self.calls = list(states), 0
			self._cached_browser_state_summary = 'not cleared'
			self._cached_selector_map = {1: object()}
			self._dom_watchdog = self
			self.watchdog_cleared = False

		async def get_browser_state_summary(self, include_screenshot=True):
			self.calls += 1
			st = self.states[min(self.calls - 1, len(self.states) - 1)]
			if isinstance(st, Exception):
				raise st
			return type('S', (), {'dom_state': st})()

		def update_cached_selector_map(self, m):
			self._cached_selector_map = m

		def clear_cache(self):
			self.watchdog_cleared = True

	def _restored(s) -> bool:
		"""Everything on_AgentFocusChangedEvent clears, cleared again — see _wait_for_dom."""
		return (s._cached_browser_state_summary is None and s._cached_selector_map == {}
		        and s.watchdog_cleared)

	ok('_dom_elements mirrors total_elements (a root plus 11 children is 12)', _dom_elements(_DomState(12)) == 12)
	ok("_dom_elements is 0 for a null tree, like prompts.py's _root guard", _dom_elements(_DomState(0)) == 0)
	# The threshold is browser-use's, not ours: pin it to the installed library rather than to a
	# number in this file. A predicate weaker than the cue would silence the detector while leaving
	# the model just as blind — a no-op that reads as a fix.
	ok("DOM_READY_MIN_ELEMENTS still equals the installed library's 'Page appears empty' threshold",
	   DOM_READY_MIN_ELEMENTS == 10 and "page_stats['total_elements'] < 10" in src, str(pf))
	s_ = _FakeSession(_DomState(2), _DomState(2), _DomState(1094, 543))   # the measured HN shape
	r = asyncio.run(_wait_for_dom(s_, poll_s=0.0, nav_t0=None))
	ok('the wait rebuilds until the DOM fills, counting every build (2, 2, 1094 -> 3)',
	   r['builds'] == 3 and r['elements'] == 1094 and r['interactive'] == 543
	   and not r['timed_out'] and not r['error'])
	ok('… and records the per-build trajectory, which is what tells a bad build from a slow page',
	   [t[1] for t in r['trace']] == [2, 2, 1094] and len(r['trace'][0]) == 3)
	ok('… and restores every cache the tab switch had cleared (state, selector map, watchdog)',
	   _restored(s_))
	s_ = _FakeSession(_DomState(1094, 543))
	r = asyncio.run(_wait_for_dom(s_, poll_s=0.0))
	ok('a page already full costs exactly one build, never zero (builds==1 must stay meaningful)',
	   r['builds'] == 1 and not r['timed_out'] and r['error'] is None and _restored(s_))
	s_ = _FakeSession(_DomState(2))
	r = asyncio.run(_wait_for_dom(s_, timeout_s=0.0, poll_s=0.0))
	ok('on timeout the wait PROCEEDS and records it — never raises, never becomes SETUP_FAILED',
	   r['timed_out'] and r['builds'] == 1 and r['elements'] == 2 and r['error'] is None and _restored(s_))
	s_ = _FakeSession(RuntimeError('cdp gone'))
	r = asyncio.run(_wait_for_dom(s_, poll_s=0.0))
	ok('a probe that raises costs one recorded field, not the run',
	   r['error'] == 'RuntimeError: cdp gone' and not r['timed_out'] and r['builds'] == 0)
	ok('… and STILL restores the caches — the error path is the one that would leave them dirty',
	   _restored(s_))
	s_ = _FakeSession(_DomState(1094, 543))
	r = asyncio.run(_wait_for_dom(s_, enabled=False))
	ok('the no-dom-ready control issues no build at all — the wait is the only difference',
	   r['enabled'] is False and r['builds'] == 0 and s_.calls == 0)
	ok('nav_ms is recorded when the reference clock is passed, and None when it is not',
	   asyncio.run(_wait_for_dom(_FakeSession(_DomState(20, 5)), nav_t0=time.monotonic()))['nav_ms'] is not None
	   and asyncio.run(_wait_for_dom(_FakeSession(_DomState(20, 5))))['nav_ms'] is None)
	ok('arms carry the dom_ready dimension and no-dom-ready is the only arm that drops it',
	   Arm('default').dom_ready and Arm('enforce-read-only').dom_ready
	   and not Arm('no-dom-ready').dom_ready and Arm('no-dom-ready').overrides == {}
	   and Arm('no-dom-ready').extra_excluded == ())
	rr = [{'task': 't', 'arm': 'default', 'outcome': 'CORRECT', 'patterns': {'blank_first_state': {}},
	       'dom_ready': {'enabled': True, 'builds': 2, 'ms': 400, 'elements': 1094}},
	      {'task': 't', 'arm': 'no-dom-ready', 'outcome': 'CORRECT', 'patterns': {},
	       'dom_ready': {'enabled': False, 'builds': 0, 'ms': 0, 'elements': 0}}]
	dl = _dom_ready_lines(rr, [Arm('default'), Arm('no-dom-ready')])
	ok('the summary names the control arm and surfaces the falsifier (full DOM, cue still there)',
	   any('DISABLED (control arm)' in l for l in dl)
	   and any('still fired in 1' in l and 'NOT the mechanism' in l for l in dl))

	print('== side effects')
	runs_after = set(p.name for p in (REPO / 'runs').iterdir()) if (REPO / 'runs').exists() else set()
	ok('self-check created no run directory', runs_after == runs_before)
	ok('self-check never imported browser_use', 'browser_use' not in sys.modules)

	n_ok = sum(1 for _, c, _ in checks if c)
	print(f'\nSELF-CHECK: {"PASSED" if n_ok == len(checks) else "FAILED"} — {n_ok} of {len(checks)} checks')
	return 0 if n_ok == len(checks) else 1


def _raises(fn, exc_type) -> bool:
	try:
		fn()
	except exc_type:
		return True
	except Exception:
		return False
	return False


def cmd_guide(_args) -> int:
	print(__doc__)
	return 0


# ── xlike: the one flow whose truth is the page, not a fetch ────────────────────────────

#: What the LIKE phase is told. No URL in it (directly_open_url is off, but a URL in the task is
#: also what made Phase 4's G1 unfalsifiable), and it names the control by the word that is
#: actually rendered next to it rather than by a data-testid the model cannot see.
XLIKE_PROMPT = (
	"This page lists posts. Under each post is a row of buttons. The Like button is the one "
	"whose aria-label ENDS WITH the words 'Likes. Like' — for example "
	"[1060]<button aria-label=2845 Likes. Like />.\n\n"
	"Find the {n} most recent posts that mention {topic}. For each one, click that post's Like "
	"button.\n\n"
	"Rules, which matter more than anything else in this task:\n"
	"- ONLY click an element whose aria-label ends with 'Likes. Like'.\n"
	"- NEVER click an element with no aria-label, such as [1063]<div />. Those are empty "
	"wrappers and clicking one does nothing at all.\n"
	"- Stay on this page. Do NOT open a post, a profile, a reply or any link: measured "
	"2026-09-05, wandering into a reply thread is how a run liked 5 posts when asked for 2.\n"
	"- After each click, look at the new screenshot. If the heart did not change, you clicked "
	"the wrong index — pick a DIFFERENT index that ends with 'Likes. Like'. Never send the same "
	"index twice.\n"
	"When you have liked {n} posts, call done."
)

#: MEASURED 2026-09-05, and the first draft of this prompt was wrong because it was guessed:
#: once a post is liked its button's aria-label becomes "<n> Likes. Liked" — NOT "Unlike". The
#: draft told the model to hunt for a string that does not occur on the page, and it un-liked 1
#: of 2. Only the data-testid flips to `unlike`, and the model never sees data-testid. The
#: aria-label probe printed on every run exists so this is never guessed again.
XUNLIKE_PROMPT = (
	"On this page, {n} posts have already been liked. A post that IS liked has a button whose "
	"aria-label ends with the word 'Liked' — for example "
	"[1060]<button aria-label=2872 Likes. Liked />. A post that is NOT liked ends with 'Like' "
	"instead.\n\n"
	"Find every button whose aria-label ends with 'Liked' and click it. Clicking it turns that "
	"post back to unliked.\n\n"
	"Rules, which matter more than anything else in this task:\n"
	"- ONLY click an element whose aria-label ends with the word 'Liked'.\n"
	"- Do NOT click one ending in 'Like' — that would like a post instead of un-liking it.\n"
	"- NEVER click an element with no aria-label, such as [1063]<div />.\n"
	"- Stay on this page. Do not open a post, a profile or a reply.\n"
	"- After each click, look at the new screenshot. If nothing changed, pick a DIFFERENT "
	"index. Never send the same index twice.\n"
	"When no button ends with 'Liked' any more, call done."
)


def _xline(label: str, snap, want: str = '') -> str:
	return (f'  {label:<22} liked={snap.liked:<3} tweets={snap.tweets:<3} '
	        f'like_btns={snap.like:<3} testids={snap.total_testids:<4}' + (f'   {want}' if want else ''))


async def cmd_xlike(args) -> int:
	"""Like N posts, then un-like them, and grade BOTH on the page's own state.

	This is the first task in the project whose truth cannot come from `browsin.grade`. Its three
	fetchers (`wikipedia_itn_lead`, `hn_story`, `wikipedia_contains`) re-fetch the page over plain
	anonymous HTTP, which is right for read-only tasks and structurally unable to answer "did the
	Like land" — like state exists only inside a session-authenticated render. So the truth here is
	`browsin.pagestate`, read over CDP from the very tab the model drove, before and after.

	Ordering that matters, and why:

	* **Chrome and the login check come BEFORE the lease.** A signed-out profile is a ~200 ms
	  question; discovering it at step 1 costs a lease, a model load and ~15 minutes of the card.
	* **The baseline is measured before anything is clicked.** Measured 2026-09-05, this account's
	  liked count on x.com/OpenAI was 0, so leftovers are unambiguous — but the code never assumes
	  that number. Everything below is stated as a DELTA from whatever the baseline turns out to
	  be, so a page that already had likes on it is handled and the owner's own likes are never
	  touched.
	* **The safety net is in a `finally`.** The owner's stated worry is likes accumulating on a
	  real account. If the model likes 2 and then fails to un-like them — or the run dies, or the
	  lease is lost — the harness removes the excess itself and says so loudly. It clicks only
	  `[data-testid="unlike"]`, so a stale selector clicks nothing rather than liking something.
	* **A leftover from a PREVIOUS crashed run is cleaned at the start too**, since a `finally`
	  cannot cover SIGKILL. Together those two cover every path except the card being pulled out
	  of the wall mid-click.

	Two agent runs, not one. A single run would have to hold "which two did I like" across a
	dozen steps of a virtualised timeline, and `had_then_lost` is the most common failure in this
	project's census. Two runs let the harness carry that state instead of the model, and it makes
	each phase separately gradeable — which is the difference between "it failed" and "it liked
	fine and could not un-like".
	"""
	from browsin import browser as B
	from browsin import diagnose as D
	from browsin import pagestate as PS
	from browsin.interlock import Interlock, card_preflight
	from browsin.lease import LeaseAssertionError, hold, normalise_tag
	from browsin.proxy import Proxy
	from warden.client import LeaseLost, WardenError

	n = int(args.n)
	host = args.url.split('/')[2]
	if normalise_tag(WORKLOAD) != MODEL_TAG:
		return _refused(f'MODEL_TAG {MODEL_TAG!r} != normalise_tag({WORKLOAD!r})')
	if not B._have_systemd_run():
		return _refused('systemd-run is unavailable, so a fresh Chrome per run cannot be guaranteed')
	take_lock()
	try:
		await card_preflight(evict=args.evict)
	except Interlock as exc:
		release_lock(); return _refused(str(exc))
	except WardenError as exc:
		release_lock(); return _refused(f'warden: {exc}')

	run_dir, scratch = _enter_run_dir('xlike')
	print(f'run dir: {run_dir}   topic={args.topic!r}  n={n}  url={args.url}', flush=True)

	chrome = None
	baseline = None
	base_liked = None
	handle = ''
	cleanup: dict = {}
	rc = 1
	try:
		# ── Chrome, readiness and the login check: all BEFORE the card is touched ──────
		chrome = await _fresh_chrome(B, args.url)
		print(f'chrome pid={chrome.pid} bind={chrome.bind}', flush=True)
		ready = await PS.wait_ready(cdp_url=chrome.cdp_url, host=host, require='x-timeline',
		                            timeout_s=CONTENT_GATE_TIMEOUT_S)
		print(f'page ready: {ready.ok}  {ready.reason}  ({ready.waited_s:.1f}s, {ready.polls} poll(s))',
		      flush=True)
		if not ready.ok:
			return _refused(f'{args.url} never rendered a timeline within '
			                f'{CONTENT_GATE_TIMEOUT_S:.0f}s. {ready.reason}')
		try:
			baseline = await PS.assert_logged_in(cdp_url=chrome.cdp_url, host=host)
		except PS.PageStateError as exc:
			return _refused(str(exc))

		# The AUTHORITATIVE baseline: the account's own Likes page, not this timeline's viewport.
		# Measured 2026-09-05: the viewport count said 0 while the Likes page held 5.
		handle = await PS.account_handle(cdp_url=chrome.cdp_url, host=host)
		try:
			base_liked, likes_url = await PS.liked_count(cdp_url=chrome.cdp_url, host=host,
			                                             handle=handle)
		except PS.PageStateError as exc:
			return _refused(f'cannot read the authoritative like list, so this run could not be '
			                f'cleaned up afterwards; refusing to create likes it cannot remove: {exc}')
		print(f'signed in as @{handle}; authoritative Likes page {likes_url} holds {base_liked} '
		      f'liked post(s) — that is the baseline, and none of them will be touched', flush=True)
		await PS.goto(args.url, cdp_url=chrome.cdp_url, host=host, require='x-timeline')

		census = await PS.probe_testids(cdp_url=chrome.cdp_url, host=host)
		print('selector census (top 8, so a renamed data-testid announces itself rather than '
		      'grading as zero):', flush=True)
		print(f'  {census[:8]}', flush=True)
		print(_xline('BASELINE', baseline), flush=True)
		try:
			aria = await PS.aria_labels(cdp_url=chrome.cdp_url, host=host)
			print(f'  aria-labels the model will see: like={aria.get("like")[:2]} '
			      f'unlike={aria.get("unlike")[:2]}', flush=True)
		except Exception as exc:
			print(f'  aria-label probe failed ({type(exc).__name__}); prompts name these strings, '
			      f'so check them by hand if this run misses', flush=True)

		# A leftover from a previous run that died past its own finally (SIGKILL, power).
		if baseline.liked > 0:
			print(f'\n  !! {baseline.liked} post(s) were ALREADY liked before this run started.\n'
			      f'     Treating that as the baseline and never touching them: every number below '
			      f'is a delta.\n', flush=True)

		# ── now the card ───────────────────────────────────────────────────────────────
		arm = Arm('default')
		t0 = time.monotonic()
		phases: list[dict] = []
		async with hold(WORKLOAD, reason=f'test.py xlike {args.topic}'[:120],
		                num_ctx=NUM_CTX, ttl_s=args.ttl, may_evict=bool(args.evict)) as card:
			print(f'lease granted in {time.monotonic() - t0:.1f}s  served num_ctx={card.num_ctx}\n',
			      flush=True)
			with Proxy(card.endpoint, run_dir / 'proxy.jsonl') as proxy:
				for phase, prompt_t, want in (('like', XLIKE_PROMPT, base_liked + n),
				                              ('unlike', XUNLIKE_PROMPT, base_liked)):
					sub = run_dir / phase
					sub.mkdir(parents=True, exist_ok=True)
					task = G.Task(name=f'xlike-{phase}', url=args.url,
					              prompt=prompt_t.format(n=n, topic=args.topic),
					              expect=G.nothing_to_find, max_steps=args.max_steps,
					              read_only=False)
					print(f'── {phase.upper()} phase ' + '─' * 50, flush=True)
					hist, seconds, recs, dom_ready = await _drive(
						task, arm, proxy=proxy, chrome=chrome, scratch=scratch, run_dir=sub,
						max_steps=args.max_steps, nav_t0=None)
					try:
						a2 = await PS.aria_labels(cdp_url=chrome.cdp_url, host=host)
						print(f'  aria after {phase}: like={a2.get("like")[:2]} '
						      f'liked={a2.get("unlike")[:2]}', flush=True)
					except Exception:
						pass
					# Count on the Likes page, which sees every like wherever it was made, then come
					# back so the next phase starts where the task says it should.
					now_liked, _ = await PS.liked_count(cdp_url=chrome.cdp_url, host=host, handle=handle)
					await PS.goto(args.url, cdp_url=chrome.cdp_url, host=host, require='x-timeline')
					snap = await PS.snapshot(cdp_url=chrome.cdp_url, host=host)
					row = G.grade(task, None, hist)
					row.update({'task': task.name, 'rep': 1, 'arm': 'default', 'seconds': seconds,
					            'run_dir': str(sub), 'max_steps': args.max_steps,
					            'dom_ready': dom_ready, 'liked_after': now_liked,
					            'liked_wanted': want, 'phase': phase})
					_append(run_dir / 'results.jsonl', row)
					found = D.detect(task, None, hist, row, recs, args.max_steps)
					print(D.render(task, 1, 'default', row, found, hist, recs, str(sub), '',
					               args.max_steps), flush=True)
					print(f'  AFTER {phase.upper():<7} account-wide liked={now_liked}  '
					      f'wanted={want}  ->  ' + ('OK' if now_liked == want else 'MISS')
					      + f'   (this page: tweets={snap.tweets} like_btns={snap.like})', flush=True)
					phases.append({'phase': phase, 'liked': now_liked, 'want': want,
					               'ok': now_liked == want, 'steps': row.get('steps'),
					               'seconds': seconds})
					print('', flush=True)

		# The unlike phase must not be able to pass by doing nothing. If the like phase never
		# raised the count, the unlike phase's target (baseline) is ALREADY satisfied before it
		# starts, and reporting that as OK would be the "I could not check, so I passed" shape
		# this project forbids (browsin/lease.py:297, tools/phase2_gate.py:234, Phase 4's G5).
		liked_ok = phases[0]['ok'] if phases else False
		unliked_ok = (len(phases) > 1 and phases[1]['ok'] and liked_ok)
		print('=' * 78, flush=True)
		print(f'  VERDICT   like phase   : {"PASS" if liked_ok else "FAIL"}'
		      f'   (liked {phases[0]["liked"] if phases else "?"} of a wanted '
		      f'{phases[0]["want"] if phases else "?"})', flush=True)
		if not liked_ok:
			print('            unlike phase : NOT TESTED — the like phase never created a like, '
			      'so there was nothing to remove and its result is meaningless', flush=True)
		else:
			print(f'            unlike phase : {"PASS" if phases[1]["ok"] else "FAIL"}'
			      f'   (liked {phases[1]["liked"]} of a wanted {phases[1]["want"]})', flush=True)
		print(f'  RESULT    {"PASS" if (liked_ok and unliked_ok) else "FAIL"}', flush=True)
		print('=' * 78, flush=True)
		rc = 0 if (liked_ok and unliked_ok) else 1
		return rc
	except (LeaseLost, LeaseAssertionError) as exc:
		print(f'\nLEASE: {type(exc).__name__}: {exc}', flush=True)
		return 1
	finally:
		# ── the safety net. Runs on every path out, including an exception or Ctrl-C. ──
		if chrome is not None and base_liked is not None:
			try:
				# Counted and cleaned on the account's OWN Likes page, never on the task timeline.
				# Measured 2026-09-05: the viewport count reported "liked is now 0 (baseline 0)"
				# while the account actually held 5 — the model had wandered into a reply thread
				# and liked posts that were never rendered where the old check was looking. A
				# safety net that can silently miss what it guards is worse than none, because it
				# is believed.
				res = await PS.unlike_everything(cdp_url=chrome.cdp_url, host=host,
				                                 handle=handle, keep=base_liked)
				cleanup = res
				if res['removed']:
					print(f"\n  !! SAFETY NET: removed {res['removed']} leftover like(s) so nothing "
					      f"accumulates on @{res['handle']}.", flush=True)
				print(f"  account-wide liked is now {res['remaining']} (baseline {base_liked}) "
				      f"at {res['url']}", flush=True)
				if res['remaining'] > base_liked:
					print(f"     *** {res['remaining'] - base_liked} STILL LIKED — remove them by "
					      f"hand at {res['url']}", flush=True)
			except Exception as exc:
				print(f'\n  *** SAFETY NET FAILED: {type(exc).__name__}: {exc}\n'
				      f'      CHECK https://x.com/{handle or "<you>"}/likes BY HAND for leftover '
				      f'likes.', flush=True)
		release_lock()
		print(f'\n  run dir: {run_dir}', flush=True)


# ── argv ────────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
	ap = argparse.ArgumentParser(prog='test.py', description=__doc__.split('\n', 2)[0],
	                             formatter_class=argparse.RawDescriptionHelpFormatter,
	                             epilog='Bare `test.py [--reps N …]` means `run`. `test.py guide` prints the full procedure.')
	sub = ap.add_subparsers(dest='cmd')

	def leasing(p):
		p.add_argument('--arms', default='default',
		               help='comma list: default | no-dom-ready | enforce-read-only | sysmsg:PATH | set:KEY=JSON '
		                    '(default: %(default)s)')
		p.add_argument('--label', default='', help='free text recorded in run.json and the lease reason')
		p.add_argument('--evict', action='store_true',
		               help='authorise displacing the public voice service (clonin) — at preflight AND at the acquire. Never in a loop.')
		p.add_argument('--ttl', type=int, default=DEFAULT_TTL_S, help='lease ttl_s (default: %(default)s)')

	r = sub.add_parser('run', help='the task table, N reps, graded and diagnosed')
	r.add_argument('--reps', type=int, default=3, help='repetitions per task (default: %(default)s; below 3 tells you nothing)')
	r.add_argument('--only', action='append', help='task name; repeatable')
	r.add_argument('--max-steps', type=int, default=None, help="override every task's budget")
	r.add_argument('--resume', metavar='RUNDIR', help='carry finished runs from RUNDIR forward and run the rest')
	r.add_argument('--gate', action='store_true', help='exit 1 unless the completion rate reaches --min-rate')
	r.add_argument('--min-rate', type=float, default=0.85, help='with --gate (default: %(default)s)')
	leasing(r)

	o = sub.add_parser('one', help='a one-off task, same path as run; graded only with --expect-from')
	o.add_argument('--url', required=True, help='the page to open before the model starts')
	o.add_argument('--task', required=True, help='what to ask it to do; end with "Then call done."')
	o.add_argument('--expect-from', default=None, help=f'{G.USAGE_EXPECT_FROM} (no free text; omit → UNGRADED)')
	o.add_argument('--max-steps', type=int, default=8, help='(default: %(default)s)')
	o.add_argument('--reps', type=int, default=1, help='(default: %(default)s)')
	o.add_argument('--read-only', action='store_true',
	               help='the task forbids interaction: count input/click/... as wasted and make the task eligible '
	                    'for the enforce-read-only arm (pass --arms default,enforce-read-only to actually enforce)')
	leasing(o)

	x = sub.add_parser('xlike', help='like N posts about a topic then un-like them; graded on the '
	                                  'page\'s own state, never on the agent\'s done flag')
	x.add_argument('--url', default='https://x.com/OpenAI', help='(default: %(default)s)')
	x.add_argument('--topic', default='Astra', help='what the posts should be about (default: %(default)s)')
	x.add_argument('--n', type=int, default=2, help='how many to like, then un-like (default: %(default)s)')
	x.add_argument('--max-steps', type=int, default=30,
	               help='per PHASE, not per run; this task is far longer than the table (default: %(default)s)')
	# Deliberately NOT leasing(): --arms would be a silent no-op here (one arm, two fixed phases),
	# and this project treats an accepted-but-ignored flag as a bug, not a convenience.
	x.add_argument('--label', default='', help='free text recorded in the lease reason')
	x.add_argument('--evict', action='store_true',
	               help='authorise displacing the public voice service (clonin). Never in a loop.')
	x.add_argument('--ttl', type=int, default=DEFAULT_TTL_S, help='lease ttl_s (default: %(default)s)')

	d = sub.add_parser('diagnose', help='offline: re-render DIAGNOSIS blocks from a run dir')
	d.add_argument('rundir')
	d.add_argument('--all', action='store_true', help='include CORRECT runs that are not near-misses')

	c = sub.add_parser('compare', help='offline: arms within one run dir, or two run dirs')
	c.add_argument('a')
	c.add_argument('b', nargs='?')
	c.add_argument('--min-reps', type=int, default=6, help='graded runs per arm below which no verdict is given (default: %(default)s)')

	sub.add_parser('self-check', help='every scorer branch and detector, no card, no browser')
	sub.add_parser('guide', help='print the procedure')
	return ap


def main(argv: list[str]) -> int:
	# Bare `test.py --reps 3` means `run`; bare `test.py -h` / `--help` means the top-level help.
	if (not argv or argv[0].startswith('-')) and argv not in (['-h'], ['--help']):
		argv = ['run'] + argv
	args = build_parser().parse_args(argv)
	if args.cmd == 'self-check':
		return cmd_self_check(args)
	if args.cmd == 'guide':
		return cmd_guide(args)
	if args.cmd == 'diagnose':
		return cmd_diagnose(args)
	if args.cmd == 'compare':
		return cmd_compare(args)

	if args.cmd == 'xlike':
		return asyncio.run(cmd_xlike(args))

	if args.cmd in ('run', 'one'):
		arms = parse_arms(args.arms)   # SystemExit with REFUSED TO START on a bad spec, before any card time
	if args.cmd == 'run':
		tasks = [t for t in G.TASKS if not args.only or t.name in args.only]
		if not tasks:
			return _refused(f'no task matches {args.only}; known: {[t.name for t in G.TASKS]}')
		if args.max_steps:
			tasks = [G.Task(**{**_task_spec(t), 'max_steps': args.max_steps}, expect=t.expect) for t in tasks]
		return asyncio.run(cmd_run(args, tasks, arms, 'run'))
	if args.cmd == 'one':
		if args.expect_from:
			try:
				expect, absent, forbid = G.expect_from(args.expect_from)
			except ValueError as exc:
				return _refused(str(exc))
		else:
			expect, absent, forbid = G.nothing_to_find, False, []
			print('UNGRADED: no --expect-from given; the answer will be shown and diagnosed but never scored.', flush=True)
		task = G.Task(name='one', url=args.url, prompt=args.task, expect=expect, max_steps=args.max_steps,
		              absent=absent, forbid=forbid, read_only=args.read_only)
		return asyncio.run(cmd_run(args, [task], arms, 'one'))
	build_parser().print_help()
	return 2


if __name__ == '__main__':
	try:
		code = main(sys.argv[1:])
	except SystemExit as exc:   # REFUSED TO START from arms/lock carries its message
		if isinstance(exc.code, str):
			print(exc.code, flush=True)
			code = 2
		else:
			code = exc.code if isinstance(exc.code, int) else 2
	sys.stdout.flush()
	# browser-use can leave a CDP reconnect loop running; the lease is already released.
	os._exit(code)
