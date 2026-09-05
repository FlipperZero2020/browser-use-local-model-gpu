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
		if spec == 'default':
			self.name = 'default'
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
			raise SystemExit(f'REFUSED TO START\n  unknown arm {spec!r}; use default | enforce-read-only | '
			                 f'sysmsg:PATH | set:KEY=JSON')

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


async def _drive(task: G.Task, arm: Arm, *, proxy, chrome, scratch, run_dir, max_steps: int):
	"""One agent run. Returns (history_dict, seconds, proxy_records_for_this_run)."""
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
	return hist, seconds, recs


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
								print(f'{tag} chrome pid={chrome.pid} bind={chrome.bind}', flush=True)
								hist, seconds, recs = await _drive(task, arm, proxy=proxy, chrome=chrome,
								                                   scratch=scratch, run_dir=sub, max_steps=max_steps)
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
							print(f"{tag} {row['outcome']} {row['steps']}st {seconds}s waste={row['wasted_actions']} "
							      f"-> {row['final'][:80]!r}{flag}", flush=True)
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
		# keep what only the live run could know: the post-run truth check and its timing
		for k in ('outcome', 'correct', 'seconds', 'expected_after', 'truth_note', 'arm_effective'):
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
	inert = [{'task': 'f', 'arm': 'enforce-read-only', 'arm_effective': False, 'outcome': 'CORRECT', 'correct': True, 'steps': 3, 'wasted_actions': 0, 'patterns': {}}] * 6
	base = [dict(r, arm='default', arm_effective=True) for r in inert]
	ok('compare reports an inert arm instead of a verdict', 'ARM INERT' in D.compare(base, inert, 'a', 'b'))
	ok('compare refuses a verdict under 6 per arm', 'NO VERDICT' in D.compare(base[:3], [dict(r, arm_effective=True) for r in inert[:3]], 'a', 'b'))
	ok('rate_line on all-excluded rows does not raise', 'no graded runs' in D.rate_line('x', [{'task': 'x', 'outcome': 'RACY'}]))
	ok('render tolerates missing screenshot paths and None fields',
	   'DIAGNOSIS' in D.render(RO, 1, 'default', G.grade(RO, ['W'], _hist(_step(1, 'W', DONE('W')))), {}, _hist(_step(1, 'W', DONE('W'))), None, '/r'))

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


# ── argv ────────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
	ap = argparse.ArgumentParser(prog='test.py', description=__doc__.split('\n', 2)[0],
	                             formatter_class=argparse.RawDescriptionHelpFormatter,
	                             epilog='Bare `test.py [--reps N …]` means `run`. `test.py guide` prints the full procedure.')
	sub = ap.add_subparsers(dest='cmd')

	def leasing(p):
		p.add_argument('--arms', default='default',
		               help='comma list: default | enforce-read-only | sysmsg:PATH | set:KEY=JSON (default: %(default)s)')
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
