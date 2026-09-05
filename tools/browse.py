#!/usr/bin/env python3
"""Give the local vision model a browsing task and watch it work.

    export WARDEN_URL=http://192.168.1.111:8130
    export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
    venv/bin/python tools/browse.py --url https://en.wikipedia.org/wiki/Main_Page \\
        --task "Find today's featured article and report its title."

This is the demonstration path, not the gate. `tools/phase4_gate.py` is what proves the
plumbing is real; this is what it looks like when you just want to use it. It shares every
component with the gate — the same lease, the same Chrome control, the same logging proxy —
so a number seen here means the same thing it means there.

Everything load-bearing is documented in the modules it calls. The two things worth
repeating at the entry point:

* **`TMPDIR` is set before `browser_use` is imported**, because one of the four temp-
  directory families is created at *import* time and `tempfile.gettempdir()` caches its
  answer the first time anything asks.
* **The card is released on every path out**, including SIGTERM, because `browsin.lease`
  installs signal handling. Run this in the background or with a long timeout anyway: a
  two-minute tool timeout will SIGTERM it, and while the lease comes back, the run does not.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
RUN_ID = time.strftime('%Y%m%d-%H%M%S')
RUN_DIR = REPO / 'runs' / f'browse-{RUN_ID}'
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

import browsin  # noqa: E402,F401  — zero-cloud env, before browser_use
from browsin import browser as B  # noqa: E402
from browsin.agent import build_agent, build_llm, build_session, build_tools  # noqa: E402
from browsin.interlock import Interlock, card_preflight  # noqa: E402
from browsin.lease import hold  # noqa: E402
from browsin.proxy import Proxy  # noqa: E402

WORKLOAD = 'ollama:qwen2.5vl-32k:7b'
MODEL_TAG = 'qwen2.5vl-32k:7b'
NUM_CTX = 32768


async def main() -> int:
	ap = argparse.ArgumentParser()
	ap.add_argument('--url', required=True, help='the page to open before the model starts')
	ap.add_argument('--task', required=True, help='what to ask it to do')
	ap.add_argument('--max-steps', type=int, default=8)
	ap.add_argument('--evict', action='store_true',
	                help='take the card even if the public voice service is using it')
	ap.add_argument('--keep-chrome', action='store_true', default=True)
	args = ap.parse_args()

	# The clonin interlock, per the owner's 2026-09-04 decision: warn and ask, never evict
	# a public service silently. Phase 3's measurement removed the margin these could have
	# shared, so an `interactive` acquire displaces it rather than sitting beside it.
	try:
		await card_preflight(evict=args.evict)
	except Interlock as exc:
		print(f'\nREFUSED TO START\n  {exc}\n')
		return 2

	if B.probe() is None:
		chrome = B.start(args.url)
		print(f'started chrome pid={chrome.pid} bind={chrome.bind}', flush=True)
	else:
		chrome = B.attach()
		print(f'attached to chrome pid={chrome.pid} bind={chrome.bind}', flush=True)

	t0 = time.monotonic()
	async with hold(WORKLOAD, reason='browse', num_ctx=NUM_CTX, ttl_s=180) as card:
		print(f'lease granted in {time.monotonic() - t0:.1f}s  served num_ctx={card.num_ctx}',
		      flush=True)
		with Proxy(card.endpoint, RUN_DIR / 'proxy.jsonl') as proxy:
			llm = build_llm(host=proxy.url, model=MODEL_TAG, num_ctx=NUM_CTX)
			session = build_session(cdp_url=chrome.cdp_url,
			                        downloads_path=str(SCRATCH / 'downloads'))
			agent = build_agent(task=args.task, llm=llm, browser_session=session,
			                    tools=build_tools(),
			                    save_conversation_path=str(RUN_DIR / 'conversation'))
			await session.start()

			# Adopt the tab we opened rather than whatever browser-use focused: it picks
			# `page_targets[0]`, which is insertion order of concurrently-attached targets
			# and not the foreground tab. On this profile an extension's "what's new" tab
			# has already come first once.
			tabs = await session.get_tabs()
			host = args.url.split('/')[2]
			chosen = next((t for t in tabs if host in t.url), None)
			if chosen is not None:
				from browser_use.browser.events import SwitchTabEvent
				await session.event_bus.dispatch(SwitchTabEvent(target_id=chosen.target_id))
				print(f'driving tab {chosen.target_id[:8]} — {chosen.url[:70]}', flush=True)

			t_run = time.monotonic()
			history = await agent.run(max_steps=args.max_steps)
			run_s = time.monotonic() - t_run

			print('\n' + '=' * 72)
			print(f'ANSWER: {history.final_result()}')
			print('=' * 72)
			print(f'{len(history.history)} steps in {run_s:.0f}s, done={history.is_done()}')
			for h in history.history:
				step = getattr(h.metadata, 'step_number', '?') if h.metadata else '?'
				acts = [list(json.loads(a.model_dump_json(exclude_none=True)))[0]
				        for a in (h.model_output.action if h.model_output else [])]
				print(f'  step {step}: {acts}  url={getattr(h.state, "url", None)}')

			chats = proxy.chat_records()
			counts = [(r.get('response') or {}).get('prompt_eval_count') for r in chats]
			imgs = [(r.get('request') or {}).get('image_count') for r in chats]
			print(f'\nprompt tokens per call: {counts}')
			print(f'images per call:        {imgs}')
			print(f'served window:          {card.num_ctx}')
			print(f'proxy log:              {RUN_DIR / "proxy.jsonl"}')

			try:
				await asyncio.wait_for(session.stop(), timeout=30)
			except Exception as exc:
				print(f'(session stop: {type(exc).__name__}: {exc})')
	return 0


if __name__ == '__main__':
	code = asyncio.run(main())
	sys.stdout.flush()
	# browser-use can leave a CDP reconnect loop running; everything is already released.
	os._exit(code)
