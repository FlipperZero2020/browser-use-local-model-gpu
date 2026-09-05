#!/usr/bin/env python3
"""Phase 4's gate, from PLAN.md §5 — one headed browser step, instrumented.

    export WARDEN_URL=http://192.168.1.111:8130
    export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
    venv/bin/python tools/phase4_gate.py            # run it in the background

**Run it in the background or with a long timeout.** It holds a lease; a two-minute tool
timeout SIGTERMs the holder, and while `browsin.lease` gives the card back on that path,
the gate it was proving is then unproven.

PLAN.md states five conditions. Reading the installed library showed that all five are
satisfiable while the thing they claim to test is broken, so they are rewritten here and
the originals are kept in the docstring of each check. The three worst:

* "*a tab it did not create changes URL*" — browser-use's `directly_open_url` scans the
  task string for a URL and injects a navigate as a **synthetic step 0** written into
  history with `model_output.action = initial_actions`. `history.model_actions()` walks
  every item, so the URL appears there whether the model chose it or the library did. Only
  grading items with `step_number >= 1`, plus `directly_open_url=False` and an assertion
  that the task contains no URL at all, makes this falsifiable.
* "*no `/tmp/browser-use-user-data-dir-*` larger than 4 KB*" — that directory is minted by a
  pydantic field validator and left empty; the 718 MB `copytree` next to it fires on a
  different branch that the CDP-attach path never reaches. The condition is structurally
  incapable of failing. What is worth checking is the whole temp footprint, scoped to this
  run's `TMPDIR`, plus the absence of any `Cookies` / `Login Data` file anywhere beneath it.
* "*measured first-request prompt tokens*" — `history.usage` is zeros by construction on
  the Ollama path. The number can only come from ollama's own `prompt_eval_count`, which
  `ChatOllama` discards, which is why `browsin.proxy` exists. And it must be read from the
  same request that carried the screenshot, or the measurement rewards a run that sent no
  image at all.

Every check names the control run that must make it FAIL. `--mode` runs those:

    --mode blank-canvas   G4 must fail while G3 still passes  (sent vs read)
    --mode no-vision      G3 and G4 must fail
    --mode direct-url     G1 must fail (the navigate came from step 0, not the model)
    --mode no-proxy       G8 must fail (nothing was measured)
    --mode signals        G12 must fail
    --mode oversize       G5 must fail (a padded request through the same proxy)

House discipline, from `tools/phase2_gate.py`: NOTE never gates, "I could not check" is a
FAIL and not a quiet pass, and an unknown `--only` prints NOTHING RAN and exits non-zero.
"""
from __future__ import annotations

# ── TMPDIR first, before anything imports browser_use ─────────────────────────────────
# Four families of temp directory are created by browser-use, one of them at *import* of
# `browser_use.browser.session` (a module-level `BrowserProfile()` whose validator mkdirs
# unconditionally). `tempfile.gettempdir()` caches its answer on first use, so setting the
# environment variable alone is not enough once anything has asked. Both, before the import.
import os  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
RUN_ID = time.strftime('%Y%m%d-%H%M%S')
RUN_DIR = REPO / 'runs' / f'phase4-{RUN_ID}'
SCRATCH = RUN_DIR / 'tmp'
SCRATCH.mkdir(parents=True, exist_ok=True)
(SCRATCH / 'downloads').mkdir(exist_ok=True)   # browser-use will not create this for us
os.environ['TMPDIR'] = str(SCRATCH)
tempfile.tempdir = str(SCRATCH)
os.environ['BROWSER_USE_CONFIG_DIR'] = str(RUN_DIR / 'config')

sys.path.insert(0, str(REPO))

import argparse  # noqa: E402
import asyncio  # noqa: E402
import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import urllib.request  # noqa: E402

import browsin  # noqa: E402,F401  — the zero-cloud env block, before browser_use
from browsin import browser as B  # noqa: E402
from browsin.agent import build_agent, build_llm, build_session, build_tools  # noqa: E402
from browsin.fixture import Fixture, make_nonce  # noqa: E402
from browsin.interlock import Interlock, card_preflight  # noqa: E402
from browsin.lease import (  # noqa: E402
	assert_context_window,
	assert_resident,
	hold,
)
from browsin.proxy import PORT as PROXY_PORT, Proxy  # noqa: E402

WORKLOAD = 'ollama:qwen2.5vl-32k:7b'
MODEL_TAG = 'qwen2.5vl-32k:7b'
#: One number, two places: the client's `ollama_options` and the window warden booked
#: `cost_mib` (8375 MiB) against. `hold(num_ctx=…)` reads `/api/ps` and refuses to start on
#: a mismatch, which is the only thing that keeps warden's book honest.
NUM_CTX = 32768
#: PLAN.md's bar. Read from what `/api/ps` actually serves, never from this constant.
PROMPT_BUDGET_FRACTION = 0.60
#: Eight, not six. Measured 2026-09-04: this model intermittently emits `"action": [{}]`
#: — an EMPTY action object, which the grammar permits because every action field is
#: optional, and which then matches no member of the union, so pydantic reports
#: `PydanticUndefined` against all fifteen at once. browser-use counts that as one of six
#: consecutive failures and carries on, so the run needs slack to absorb one or two.
MAX_STEPS = 8
#: `foreign_mib` idles at ~2200-2600. Above that means a load leaked in flight and shows in
#: neither `/api/ps` nor warden's tenants — no VRAM number is trustworthy until it settles.
FOREIGN_BASELINE_MAX = 2700
BOX_OLLAMA_LOG = r'D:\warden\logs\ollama-server.log'
#: PLAN.md §3.2 attributes this string to the ollama build on the box. If the wording ever
#: differs, G6 greps for something that never appears and looks like a pass forever — so
#: G6 also proves it can read the log at all, and fails when it cannot.
TRUNCATION_MARKER = 'truncating input prompt'

results: list[tuple[str, str, str]] = []


def record(gate: str, ok: bool, detail: str) -> None:
	results.append(('PASS' if ok else 'FAIL', gate, detail))
	print(f'\n  [{"PASS" if ok else "FAIL"}] {gate}\n         {detail}\n', flush=True)


def note(gate: str, detail: str) -> None:
	results.append(('NOTE', gate, detail))
	print(f'\n  [NOTE] {gate}\n         {detail}\n', flush=True)


def _ssh(command: str, timeout_s: int = 45) -> tuple[int, str]:
	"""One PowerShell command on the box, base64-encoded so bash and PowerShell agree.

	`ssh gpubox "…"` is parsed by bash locally *and* by PowerShell remotely; `-EncodedCommand`
	is the only form that survives both without quoting games.
	"""
	encoded = base64.b64encode(command.encode('utf-16-le')).decode()
	try:
		r = subprocess.run(
			['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'gpubox',
			 'powershell', '-NoProfile', '-EncodedCommand', encoded],
			capture_output=True, text=True, timeout=timeout_s)
		return r.returncode, (r.stdout or '') + (r.stderr or '')
	except (subprocess.TimeoutExpired, OSError) as exc:
		return 255, f'{type(exc).__name__}: {exc}'


def tree_report(root: pathlib.Path) -> list[dict]:
	out = []
	if not root.exists():
		return out
	for child in sorted(root.iterdir()):
		size = 0
		files = 0
		sensitive = []
		if child.is_dir():
			for p in child.rglob('*'):
				try:
					if p.is_file():
						size += p.stat().st_size
						files += 1
						if p.name in ('Cookies', 'Login Data', 'Web Data', 'Local State'):
							sensitive.append(str(p.relative_to(root)))
				except OSError:
					pass
		else:
			try:
				size = child.stat().st_size
				files = 1
			except OSError:
				pass
		out.append({'name': child.name, 'bytes': size, 'files': files,
		            'sensitive': sensitive})
	return out


# ── the run ────────────────────────────────────────────────────────────────────────────

async def run_gate(mode: str, keep_chrome: bool, evict: bool = False) -> dict:
	"""Do the whole thing once and return everything the checks need to grade it."""
	ev: dict = {'mode': mode, 'run_dir': str(RUN_DIR)}
	ev['preflight'] = await card_preflight(evict=evict)

	seed = int(RUN_ID.replace('-', '')) % 100000
	nonce = make_nonce(seed)
	ev['nonce'] = nonce
	ev['seed'] = seed

	real_tmp_before = set(pathlib.Path('/tmp').glob('browser*use*')) | \
	                  set(pathlib.Path('/tmp').glob('browser_use*'))

	with Fixture(nonce, blank_canvas=(mode == 'blank-canvas')) as fx:
		ev['fixture'] = {'start': fx.start_url, 'second': fx.second_url,
		                 'blank_canvas': fx.blank_canvas}
		print(f'fixture on {fx.origin}  nonce={nonce!r} '
		      f'{"(BLANK CANVAS CONTROL)" if fx.blank_canvas else ""}', flush=True)

		# ── Chrome: a controlled tab set, launched onto a real http page ──────────────
		B.stop()
		for _ in range(20):
			if B.probe() is None:
				break
			time.sleep(0.5)
		chrome = B.start(fx.start_url)
		ev['chrome'] = {'pid': chrome.pid, 'bind': chrome.bind,
		                'browser': chrome.version.get('Browser')}
		print(f'chrome pid={chrome.pid} bind={chrome.bind} '
		      f'{chrome.version.get("Browser")}', flush=True)

		before = B.targets()
		ev['targets_before'] = [{'id': t['id'], 'url': t.get('url'), 'type': t.get('type')}
		                        for t in before]
		B.preflight([t for t in before if t.get('type') == 'page'])

		# ── the box's log offset, so G6 scans only this run ───────────────────────────
		rc, out = _ssh(f'(Get-Item "{BOX_OLLAMA_LOG}").Length')
		ev['box_log'] = {'offset_rc': rc, 'offset_raw': out.strip()}
		m = re.search(r'(\d+)', out)
		ev['box_log']['offset'] = int(m.group(1)) if (rc == 0 and m) else None

		# ── the lease ─────────────────────────────────────────────────────────────────
		t_lease = time.monotonic()
		async with hold(WORKLOAD, reason=f'phase4_gate {mode}', num_ctx=NUM_CTX,
		                ttl_s=180) as card:
			ev['lease'] = {
				'endpoint': card.endpoint,
				'num_ctx_served': card.num_ctx,
				'acquire_s': round(time.monotonic() - t_lease, 1),
				'lease_id': getattr(card.lease, 'lease_id', None),
			}
			print(f'lease granted in {ev["lease"]["acquire_s"]}s  endpoint={card.endpoint} '
			      f'served num_ctx={card.num_ctx}', flush=True)

			proxy_ctx = Proxy(card.endpoint, RUN_DIR / 'proxy.jsonl')
			# The no-proxy control points ChatOllama straight at the box: the run still
			# works, and G8 must fail because nothing was measured.
			use_proxy = mode != 'no-proxy'
			llm_host = card.endpoint

			with (proxy_ctx if use_proxy else _NullCtx()) as proxy:
				if use_proxy:
					llm_host = proxy.url
				ev['llm_host'] = llm_host
				ev['proxy_upstream'] = getattr(proxy, 'upstream', None)

				if mode == 'oversize':
					# A scripted request through the same proxy, with a prompt far past the
					# served window. G5 must fail on it.
					payload = json.dumps({
						'model': MODEL_TAG,
						'messages': [{'role': 'user', 'content': 'x ' * 200000}],
						'stream': False,
						'options': {'num_ctx': NUM_CTX},
					}).encode()
					req = urllib.request.Request(
						f'{llm_host}/api/chat', data=payload,
						headers={'Content-Type': 'application/json'})
					try:
						with urllib.request.urlopen(req, timeout=600) as r:
							r.read()
					except Exception as exc:
						ev['oversize_error'] = f'{type(exc).__name__}: {exc}'
					ev['proxy_records'] = proxy.records() if use_proxy else []
					return ev

				llm = build_llm(host=llm_host, model=MODEL_TAG, num_ctx=NUM_CTX)
				session = build_session(cdp_url=chrome.cdp_url,
				                        downloads_path=str(SCRATCH / 'downloads'))

				overrides: dict = {'save_conversation_path': str(RUN_DIR / 'conversation')}
				# No URL anywhere in this string: `Agent._extract_start_url` would find one
				# and inject a synthetic step 0, which is precisely what G1 must be unable
				# to be satisfied by. `directly_open_url=False` is the belt to that brace.
				task = (
					'Press the green Continue button. '
					'Then look at the picture on the page that appears and read the access '
					'code shown in it. '
					'Then finish and report that access code.'
				)
				if mode == 'no-vision':
					overrides['use_vision'] = False
				if mode == 'signals':
					overrides['enable_signal_handler'] = True
				if mode == 'direct-url':
					# The control: put a URL in the task and let the library inject it.
					overrides['directly_open_url'] = True
					task = f'Go to {fx.second_url} and report what the page says.'
				ev['task'] = task

				agent = build_agent(task=task, llm=llm, browser_session=session,
				                    tools=build_tools(), **overrides)
				ev['settings'] = {
					'use_vision': agent.settings.use_vision,
					'use_judge': agent.settings.use_judge,
					'enable_signal_handler': overrides.get('enable_signal_handler', False),
					'max_history_items': agent.settings.max_history_items,
					'llm_timeout': agent.settings.llm_timeout,
					'step_timeout': agent.settings.step_timeout,
					'max_actions_per_step': agent.settings.max_actions_per_step,
					'directly_open_url': agent.directly_open_url,
					'message_compaction': agent.settings.message_compaction,
					'enable_planning': agent.settings.enable_planning,
				}
				ev['initial_actions'] = repr(getattr(agent, 'initial_actions', None))
				ev['initial_url'] = repr(getattr(agent, 'initial_url', None))
				ev['task_contains_url'] = bool(re.search(r'https?://', task))

				# Attach, then adopt the tab we mean. browser-use focuses
				# `page_targets[0]`, which is dict-insertion order of concurrently-attached
				# targets — not the foreground tab, and on this profile an extension's
				# "what's new" tab has already come first once.
				await session.start()
				tabs = await session.get_tabs()
				ev['tabs_after_start'] = [{'target_id': t.target_id, 'url': t.url}
				                          for t in tabs]
				chosen = next((t for t in tabs if t.url.startswith(fx.origin)), None)
				if chosen is not None:
					from browser_use.browser.events import SwitchTabEvent
					await session.event_bus.dispatch(SwitchTabEvent(target_id=chosen.target_id))
					ev['adopted_target'] = chosen.target_id
				else:
					ev['adopted_target'] = None

				# ── the concurrent sampler: warden's own view, not browsin's ──────────
				samples: list[dict] = []
				stop_sampling = asyncio.Event()

				async def sampler() -> None:
					while not stop_sampling.is_set():
						s: dict = {'t': round(time.monotonic() - t_lease, 1)}
						try:
							card.check()
							s['lease'] = 'held'
						except Exception as exc:
							s['lease'] = f'LOST: {type(exc).__name__}'
						try:
							s['listening'] = B.listening()
						except Exception as exc:
							s['listening'] = f'error: {exc}'
						samples.append(s)
						try:
							await asyncio.wait_for(stop_sampling.wait(), timeout=5.0)
						except asyncio.TimeoutError:
							pass

				sampler_task = asyncio.create_task(sampler())
				t_run = time.monotonic()
				try:
					history = await agent.run(max_steps=MAX_STEPS)
					ev['run_error'] = None
				except Exception as exc:
					history = getattr(agent, 'history', None)
					ev['run_error'] = f'{type(exc).__name__}: {exc}'
				finally:
					stop_sampling.set()
					await asyncio.gather(sampler_task, return_exceptions=True)
				ev['run_s'] = round(time.monotonic() - t_run, 1)
				ev['samples'] = samples
				ev['lan_refuses'] = B.lan_refuses()

				# ── what the run produced ─────────────────────────────────────────────
				if history is not None:
					ev['final_result'] = history.final_result()
					ev['is_done'] = history.is_done()
					ev['n_history'] = len(history.history)
					steps = []
					for h in history.history:
						steps.append({
							'step': getattr(h.metadata, 'step_number', None) if h.metadata else None,
							'url': getattr(h.state, 'url', None),
							'actions': [json.loads(a.model_dump_json(exclude_none=True))
							            for a in (h.model_output.action if h.model_output else [])],
							'results': [{'error': r.error,
							             'extracted': (r.extracted_content or '')[:400],
							             'success': r.success}
							            for r in (h.result or [])],
						})
					ev['steps'] = steps
					last = history.history[-1] if history.history else None
					ev['last_step_number'] = (
						getattr(last.metadata, 'step_number', None)
						if last is not None and last.metadata else None)
					ev['last_success'] = (
						history.history[-1].result[-1].success
						if history.history and history.history[-1].result else None)
				else:
					ev['final_result'] = None

				# Residency and window at BOTH ends, before release.
				try:
					assert_resident(card.endpoint, MODEL_TAG, exact=True)
					served = assert_context_window(card.endpoint, MODEL_TAG, NUM_CTX)
					ev['post_run_residency'] = {'ok': True, 'served_num_ctx': served}
				except Exception as exc:
					ev['post_run_residency'] = {'ok': False,
					                            'error': f'{type(exc).__name__}: {exc}'}

				ev['proxy_records'] = proxy.records() if use_proxy else []

				# Stop the session before leaving the lease. Measured 2026-09-04: without
				# this the process never exits — browser-use's CDP auto-reconnect keeps the
				# loop alive after the work is done — and a gate that hangs inside `hold()`
				# strands the card. Safe under CDP attach: `stop()`'s browser-kill branch is
				# guarded by `is_local and _subprocess`, and both are false here, so the
				# owner's Chrome is untouched. It also ends the StorageStateWatchdog's
				# 60-second poll of the owner's cookie jar.
				try:
					await asyncio.wait_for(session.stop(), timeout=30)
					ev['session_stopped'] = True
				except Exception as exc:
					ev['session_stopped'] = f'{type(exc).__name__}: {exc}'

		# ── after the lease is released ───────────────────────────────────────────────
		ev['targets_after'] = [{'id': t['id'], 'url': t.get('url'), 'type': t.get('type')}
		                       for t in B.targets()]
		ev['chrome_alive'] = B.probe() is not None
		ev['chrome_pid_after'] = B.listening()

		if ev['box_log'].get('offset') is not None:
			rc, out = _ssh(
				f'$fs=[IO.File]::Open("{BOX_OLLAMA_LOG}",\'Open\',\'Read\',\'ReadWrite\');'
				f'$fs.Seek({ev["box_log"]["offset"]},\'Begin\')|Out-Null;'
				f'$sr=New-Object IO.StreamReader($fs);$t=$sr.ReadToEnd();$sr.Close();'
				f'Write-Output ("BYTES=" + $t.Length);'
				f'$t -split "`n" | Select-String -SimpleMatch "{TRUNCATION_MARKER}" '
				f'| ForEach-Object {{ "HIT: " + $_.Line }}')
			ev['box_log']['scan_rc'] = rc
			ev['box_log']['scan_out'] = out.strip()[:4000]

	ev['tmp_scratch'] = tree_report(SCRATCH)
	real_tmp_after = set(pathlib.Path('/tmp').glob('browser*use*')) | \
	                 set(pathlib.Path('/tmp').glob('browser_use*'))
	ev['real_tmp_new'] = sorted(str(p) for p in (real_tmp_after - real_tmp_before))
	ev['gettempdir'] = tempfile.gettempdir()
	ev['cwd_artifacts'] = sorted(
		p.name for p in REPO.iterdir()
		if p.name in ('agent_history.gif', 'AgentHistory.json', 'agent_history.json'))

	if not keep_chrome:
		B.stop()
	return ev


class _NullCtx:
	upstream = None

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def records(self):
		return []


# ── the checks ─────────────────────────────────────────────────────────────────────────

def grade(ev: dict) -> None:
	mode = ev['mode']
	chats = [r for r in ev.get('proxy_records', [])
	         if str(r.get('path', '')).startswith('/api/chat')]
	with_img = [r for r in chats if (r.get('request', {}).get('image_count') or 0) > 0]
	first_img = min(with_img, key=lambda r: r['seq']) if with_img else None
	served = ev.get('lease', {}).get('num_ctx_served') or NUM_CTX

	# G0 — did the run happen at all? Without this, a crash before the agent was built
	# shows up as thirteen unrelated failures and the reader has to reconstruct which one
	# was the cause. It is also the check that catches a swallowed exception: everything
	# downstream fails identically whether the model was wrong or never ran.
	if mode != 'oversize':
		record('G0  the agent was constructed and the run was attempted',
		       ev.get('settings') is not None and ev.get('n_history') is not None,
		       f'settings={"set" if ev.get("settings") else "MISSING"} '
		       f'n_history={ev.get("n_history")} run_error={ev.get("run_error")} '
		       f'session_stopped={ev.get("session_stopped")} '
		       f'— if this fails, every check below fails as a consequence, not on its own')

	# G1 — the agent drove a tab that already existed, by its own action.
	before = {t['id']: t['url'] for t in ev.get('targets_before', []) if t['type'] == 'page'}
	after = {t['id']: t['url'] for t in ev.get('targets_after', []) if t['type'] == 'page'}
	second = ev['fixture']['second']
	moved = [tid for tid, url in after.items()
	         if tid in before and url != before[tid] and url.startswith(second)]
	model_steps = [s for s in ev.get('steps', []) if (s.get('step') or 0) >= 1]
	clicked = any(a for s in model_steps for a in s['actions'] if 'click' in a)
	g1_ok = (bool(moved) and clicked
	         and ev.get('initial_actions') in ('None', None)
	         and not ev.get('task_contains_url'))
	record('G1  a pre-existing tab changed URL, and a model action did it',
	       g1_ok,
	       f'moved={moved} clicked_at_step>=1={clicked} '
	       f'initial_actions={ev.get("initial_actions")} '
	       f'task_contains_url={ev.get("task_contains_url")}  '
	       f'(control: --mode direct-url)')

	# G2 — the prompt actually fitted, measured from the request that carried the image.
	if first_img is None:
		record('G2  first vision prompt under 60% of the served window', False,
		       'no /api/chat carried an image, so there is no prompt size to measure — '
		       'UNPROVEN, which is a failure, not a pass')
	else:
		pec = (first_img.get('response') or {}).get('prompt_eval_count')
		chars = first_img['request'].get('total_text_chars') or 0
		budget = int(PROMPT_BUDGET_FRACTION * served)
		floor_ok = pec is not None and pec >= chars / 6
		g2_ok = bool(pec) and pec < budget and floor_ok
		record('G2  first vision prompt under 60% of the served window', g2_ok,
		       f'prompt_eval_count={pec} budget={budget} (0.60 x served {served}) '
		       f'request text chars={chars} chars/6={chars // 6} '
		       f'{"" if floor_ok else "-- BELOW the chars/6 floor: cache hit or truncation, "}'
		       f'seq={first_img["seq"]}')

	# G3 — an image was actually on the wire, and it was a real screenshot.
	if first_img is None:
		record('G3  a real screenshot reached the model', False,
		       f'zero /api/chat requests carried an image (of {len(chats)} chat requests)')
	else:
		imgs = first_img['request'].get('images') or []
		big = [i for i in imgs if i.get('png') and i['png'][0] > 64 and i['png'][1] > 64]
		record('G3  a real screenshot reached the model', bool(big),
		       f'{len(imgs)} image(s), dimensions={[i.get("png") for i in imgs]} '
		       f'sha256[0]={(imgs[0].get("sha256") or "")[:16] if imgs else "-"} '
		       f'(controls: --mode no-vision, --mode blank-canvas)')

	# G4 — the model read the PIXELS, not the DOM.
	nonce = ev['nonce']
	final = ev.get('final_result') or ''
	all_text = json.dumps(ev.get('steps', []))
	g4_ok = (nonce.lower() in final.lower()) and bool(ev.get('is_done')) \
	        and (ev.get('last_step_number') or 0) >= 1
	record('G4  the model read the canvas nonce (pixels only, not in the DOM)', g4_ok,
	       f'nonce={nonce!r} in final_result={nonce.lower() in final.lower()} '
	       f'is_done={ev.get("is_done")} last_step={ev.get("last_step_number")} '
	       f'final_result={final[:200]!r} '
	       f'(also seen anywhere in history: {nonce.lower() in all_text.lower()})')

	# G5 — nothing truncated, measured locally on every response.
	# A response with NO `prompt_eval_count` is an offender, not a skip.
	#
	# The first version of this check read `if pec is not None and pec >= served`, which
	# quietly passed any exchange whose counts were missing — and the `--mode oversize`
	# control then PASSED G5, which is exactly the "a gate that cannot fail has not passed"
	# failure this repo keeps hitting. Measured 2026-09-04: a 400 000-char prompt comes back
	# 200 OK carrying only `{"model": …, "done": …}` — no `prompt_eval_count`, no
	# `done_reason`, no `message`. That is what an over-window request looks like from here,
	# so a missing count is the *signal*, not an absence of one.
	bad = []
	for r in chats:
		resp = r.get('response') or {}
		if r.get('status') == 'CLIENT_ABORTED':
			bad.append(f'seq{r["seq"]}:aborted-mid-flight')
			continue
		if resp.get('done_reason') != 'stop':
			bad.append(f'seq{r["seq"]}:done_reason={resp.get("done_reason")!r}')
		pec = resp.get('prompt_eval_count')
		if pec is None:
			bad.append(f'seq{r["seq"]}:no prompt_eval_count in response (keys={sorted(resp)})')
		elif pec >= served:
			bad.append(f'seq{r["seq"]}:prompt_eval_count={pec}>=served{served}')
	if not chats:
		record('G5  no response shows truncation or a non-stop finish', False,
		       'no /api/chat exchanges were logged at all — nothing to grade')
	else:
		record('G5  no response shows truncation or a non-stop finish', not bad,
		       f'{len(chats)} chat exchange(s); offenders={bad or "none"} '
		       f'prompt_eval_counts='
		       f'{[(r.get("response") or {}).get("prompt_eval_count") for r in chats]} '
		       f'(control: --mode oversize)')

	# G6 — and on the box, where the truncation would actually be logged.
	bl = ev.get('box_log', {})
	if bl.get('offset') is None or bl.get('scan_rc') != 0:
		record('G6  the box ollama log records no truncation for this run', False,
		       f'could not read {BOX_OLLAMA_LOG}: offset={bl.get("offset")} '
		       f'scan_rc={bl.get("scan_rc")} out={str(bl.get("scan_out"))[:200]!r} — '
		       f'UNPROVEN is a FAIL, never a quiet pass')
	else:
		hits = [ln for ln in (bl.get('scan_out') or '').splitlines()
		        if ln.startswith('HIT: ')]
		read_bytes = re.search(r'BYTES=(\d+)', bl.get('scan_out') or '')
		record('G6  the box ollama log records no truncation for this run', not hits,
		       f'scanned {read_bytes.group(1) if read_bytes else "?"} new bytes from offset '
		       f'{bl["offset"]}; hits={hits or "none"}')

	# G7 — the debug port stayed on loopback for the whole run, proven two ways.
	binds = {tuple(s) for smp in ev.get('samples', [])
	         if isinstance(smp.get('listening'), list) for s in smp['listening']}
	addrs = {b[0] for b in binds}
	g7_ok = bool(binds) and addrs <= {'127.0.0.1', '[::1]'} and ev.get('lan_refuses') is True
	record('G7  the CDP port stayed bound to loopback, and the LAN refused', g7_ok,
	       f'sampled binds={sorted(addrs) or "NONE SAMPLED"} across '
	       f'{len(ev.get("samples", []))} samples; LAN connect refused='
	       f'{ev.get("lan_refuses")}')

	# G8 — the proxy saw every LLM call, so G2/G5 are measuring the real traffic.
	expect_proxy = mode != 'no-proxy'
	g8_ok = (bool(chats)
	         and ev.get('llm_host') == f'http://127.0.0.1:{PROXY_PORT}'
	         and ev.get('proxy_upstream') == ev.get('lease', {}).get('endpoint'))
	record('G8  every LLM call went through the proxy', g8_ok,
	       f'llm_host={ev.get("llm_host")} proxy_upstream={ev.get("proxy_upstream")} '
	       f'lease_endpoint={ev.get("lease", {}).get("endpoint")} '
	       f'chat_requests={len(chats)} history_steps={ev.get("n_history")} '
	       f'{"" if expect_proxy else "(this IS the no-proxy control; failing is correct)"}')

	# G9 — the lease was held for the whole run, on warden's evidence.
	samples = ev.get('samples', [])
	held = [s for s in samples if s.get('lease') == 'held']
	expected = max(1, int((ev.get('run_s') or 0) / 5) - 1)
	g9_ok = len(samples) >= 2 and len(held) == len(samples) and len(samples) >= expected
	record('G9  the lease was held continuously across the run', g9_ok,
	       f'{len(held)}/{len(samples)} samples held, expected >= {expected} for a '
	       f'{ev.get("run_s")}s run; lease_id={ev.get("lease", {}).get("lease_id")}')

	# G10 — residency and the served window still correct at the far end.
	pr = ev.get('post_run_residency', {})
	record('G10 model still resident at the booked window after the run', bool(pr.get('ok')),
	       f'{pr}')

	# G11 — the temp footprint, scoped to this run and to the real /tmp.
	scoped = [t for t in ev.get('tmp_scratch', [])
	          if not t['name'].startswith('browser_use_agent_')]
	fat = [t for t in scoped if t['bytes'] > 1_000_000]
	leaked = [t for t in ev.get('tmp_scratch', []) if t['sensitive']]
	g11_ok = (not fat and not leaked and not ev.get('real_tmp_new')
	          and ev.get('gettempdir') == str(SCRATCH) and not ev.get('cwd_artifacts'))
	record('G11 temp footprint contained, and no profile data copied', g11_ok,
	       f'gettempdir={ev.get("gettempdir")} '
	       f'entries={[(t["name"], t["bytes"]) for t in ev.get("tmp_scratch", [])]} '
	       f'oversized={[t["name"] for t in fat]} sensitive={leaked} '
	       f'new in real /tmp={ev.get("real_tmp_new")} cwd={ev.get("cwd_artifacts")}')

	# G12 — browsin owns the signal handlers, and the judge call is off.
	s = ev.get('settings', {})
	g12_ok = (s.get('enable_signal_handler') is False and s.get('use_judge') is False
	          and s.get('directly_open_url') is False)
	record('G12 browsin owns SIGINT/SIGTERM, and no judge call was made', g12_ok,
	       f'enable_signal_handler={s.get("enable_signal_handler")} '
	       f'use_judge={s.get("use_judge")} directly_open_url={s.get("directly_open_url")} '
	       f'(browser-use REPLACES the loop signal handlers rather than adding to them, '
	       f'and its second Ctrl-C is os._exit(0), which runs no atexit at all) '
	       f'(control: --mode signals)')

	# The owner's browser, altered or not.
	record('G13 the owner\'s Chrome survived and lost no tabs', ev.get('chrome_alive') is True
	       and len(after) >= len(before),
	       f'chrome alive={ev.get("chrome_alive")} tabs before={len(before)} '
	       f'after={len(after)} pid_after={ev.get("chrome_pid_after")}')


def main() -> int:
	ap = argparse.ArgumentParser()
	ap.add_argument('--mode', default='normal',
	                choices=['normal', 'blank-canvas', 'no-vision', 'direct-url',
	                         'no-proxy', 'signals', 'oversize'])
	ap.add_argument('--evict', action='store_true',
	                help='proceed even if the public voice service holds the card, '
	                     'displacing it mid-sentence')
	ap.add_argument('--keep-chrome', action='store_true',
	                help='leave the browser up after the run so it can be looked at')
	args = ap.parse_args()

	def _sigterm(sig, frm):
		raise SystemExit(143)

	signal.signal(signal.SIGTERM, _sigterm)

	print(f'PHASE 4 GATE — mode={args.mode}  run_dir={RUN_DIR}', flush=True)
	print(f'TMPDIR={tempfile.gettempdir()}', flush=True)

	try:
		ev = asyncio.run(run_gate(args.mode, args.keep_chrome, args.evict))
	except Interlock as exc:
		print(f'\nPHASE 4 GATE: REFUSED TO START\n  {exc}\n')
		return 2
	(RUN_DIR / 'evidence.json').write_text(json.dumps(ev, indent=2, default=str))
	print(f'\nevidence written to {RUN_DIR / "evidence.json"}\n', flush=True)

	grade(ev)

	graded = [r for r in results if r[0] != 'NOTE']
	if not graded:
		print('PHASE 4 GATE: NOTHING RAN — no check produced a result')
		return 1
	failed = [r for r in graded if r[0] == 'FAIL']
	print('\n' + '=' * 78)
	for status, gate, _ in results:
		print(f'  {status:4}  {gate}')
	print('=' * 78)
	print(f'PHASE 4 GATE ({args.mode}): {len(graded) - len(failed)} of {len(graded)} passed')
	if args.mode != 'normal':
		print('  This is a CONTROL run. The check(s) it targets are SUPPOSED to fail;\n'
		      '  a control that passes everything means the gate cannot fail.')
	return 1 if (failed and args.mode == 'normal') else 0


if __name__ == '__main__':
	code = main()
	sys.stdout.flush()
	sys.stderr.flush()
	# The lease is released, the evidence is on disk and the summary is printed. browser-use
	# can still be holding a reconnect loop open, so do not wait on it.
	os._exit(code)
