#!/usr/bin/env python3
"""Phase 2's gate, from PLAN.md §5. Holds a real lease on the real card.

    export WARDEN_URL=http://192.168.1.111:8130
    export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
    venv/bin/python tools/phase2_gate.py            # ~20 minutes; run it in the background

Five checks, verbatim from the plan:

  1. a lease held 10 continuous minutes from asyncio, visible in /v1/status throughout
  2. release frees within verify_freed_fraction and books no ghost
  3. Ctrl-C mid-hold releases cleanly
  4. the /api/ps assertion catches a deliberately wrong model name
  5. lost_event fires and cancels within one heartbeat when the lease is released out
     from under it

**Run it in the background or with a long timeout.** A two-minute tool timeout SIGTERMs
this process, and while `browsin.lease` handles that and gives the card back, the gate it
was in the middle of proving is then unproven.

The order below is not the order the plan lists, and that is deliberate: a *cold* load of
`qwen3:8b` takes ~190 s, and `idle_linger_s` is 180 s, so every check that starts within
three minutes of the last release reuses a warm tenant. The 10-minute hold goes last
because it is the one whose release we then want to watch being verified.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from warden.client import AsyncWardenClient, LeaseLost, WardenClient  # noqa: E402

from browsin.lease import (  # noqa: E402
	ContextWindowMismatch,
	ContextWindowUnknown,
	Interrupted,
	NotResident,
	assert_context_window,
	assert_resident,
	hold,
)

WORKLOAD = 'ollama:qwen3:8b'
#: A tag that is declared on this box but is NOT what we lease, so a false pass is not
#: available: `/api/ps` could only show it if something really did load the wrong model.
WRONG_TAG = 'ollama:qwen2.5-coder:14b'
#: Phase 1 measured `/api/ps` reporting ctx 4096 for this workload — Ollama's default
#: window, which is what warden's 5462 MiB `cost_mib` was booked at.
EXPECTED_NUM_CTX = 4096
#: PLAN.md §7: the idle baseline for foreign VRAM. Above this means a leaked in-flight
#: load that shows in neither /api/ps nor warden's tenants.
FOREIGN_BASELINE_MAX = 2600

OLLAMA_ENDPOINT = 'http://192.168.1.111:11434'
HOLD_S = 600.0
SAMPLE_S = 15.0
#: idle_linger_s (180) + evict_verify_timeout_s (30) + slack for the 5 s engine tick.
FREED_WAIT_S = 330.0
#: policy.json's global. A teardown counts as verified when at least this fraction of the
#: expected MiB observably comes back; below it, warden books the shortfall as a ghost.
VERIFY_FREED_FRACTION = 0.8

results: list[tuple[str, str, str]] = []
log = logging.getLogger('phase2')


def record(gate: str, ok: bool, detail: str) -> None:
	results.append(('PASS' if ok else 'FAIL', gate, detail))
	print(f'\n  [{"PASS" if ok else "FAIL"}] {gate}\n         {detail}\n', flush=True)


def note(gate: str, detail: str) -> None:
	results.append(('NOTE', gate, detail))
	print(f'\n  [NOTE] {gate}\n         {detail}\n', flush=True)


def vram(status: dict) -> dict:
	return status.get('vram') or {}


# ── preflight ────────────────────────────────────────────────────────────────
async def preflight(warden: AsyncWardenClient) -> dict:
	status = await warden.status()
	v = vram(status)
	print(f'preflight: free={v.get("free_mib")} foreign={v.get("foreign_mib")} '
	      f'ghost={v.get("ghost_mib")} committed={v.get("committed_mib")} '
	      f'tenants={[t.get("workload_id") for t in status.get("tenants") or []]} '
	      f'leases={len(status.get("leases") or [])}', flush=True)
	problems = []
	if (v.get('foreign_mib') or 0) > FOREIGN_BASELINE_MAX:
		problems.append(f'foreign_mib {v.get("foreign_mib")} > {FOREIGN_BASELINE_MAX} — a load '
		                f'leaked in flight; wait for baseline before trusting any VRAM number')
	if v.get('ghost_mib'):
		problems.append(f'ghost_mib {v.get("ghost_mib")} — the book is already under-admitting')
	if status.get('leases'):
		problems.append(f'{len(status["leases"])} lease(s) already open')
	if v.get('committed_mib'):
		note('preflight: the card was not idle',
		     f'committed {v.get("committed_mib")} MiB, tenants '
		     f'{[t.get("workload_id") for t in status.get("tenants") or []]} — a warm start, so '
		     f'the cold-acquire path is not exercised by this run')
	if problems:
		raise SystemExit('preflight refused to start:\n  ' + '\n  '.join(problems))
	return status


# ── gate 5: lost_event cancels the work ──────────────────────────────────────
async def gate_lost(warden: AsyncWardenClient, sync: WardenClient) -> None:
	killed_at: dict[str, float] = {}

	async def kill_it(lease_id: str) -> None:
		await asyncio.sleep(5.0)
		print(f'  releasing {lease_id[:8]} out from under the holder…', flush=True)
		killed_at['t'] = time.monotonic()
		await asyncio.to_thread(sync.release, lease_id)

	t0 = time.monotonic()
	interval = 30.0  # replaced from the lease view below; needed if acquire itself fails
	try:
		async with hold(WORKLOAD, reason='browsin phase 2 gate 5 (lost_event)',
		                handle_signals=False) as card:
			interval = card.heartbeat_interval_s or 30.0
			print(f'  held after {time.monotonic() - t0:.1f}s; heartbeat every {interval:.0f}s',
			      flush=True)
			asyncio.create_task(kill_it(card.held.lease_id))
			# Long enough for several heartbeats. If the bridge does not work we sit here,
			# and the gate fails on the timeout rather than on a wrong answer.
			await asyncio.sleep(4 * interval + 30)
	except LeaseLost as err:
		elapsed = time.monotonic() - killed_at.get('t', t0)
		ok = elapsed <= interval + 10.0
		record('5. lost_event fires and cancels within one heartbeat',
		       ok, f'cancelled {elapsed:.1f}s after the lease was released out from under it '
		           f'(heartbeat interval {interval:.0f}s); LeaseLost: {str(err)[:110]}')
		return
	except asyncio.CancelledError:
		record('5. lost_event fires and cancels within one heartbeat', False,
		       'the task was cancelled but hold() did not convert it into LeaseLost')
		return
	record('5. lost_event fires and cancels within one heartbeat', False,
	       'the hold ran to completion — lost_event never reached the loop')


# ── gate 3: signals release cleanly ──────────────────────────────────────────
def gate_signal(sync: WardenClient, signum: int) -> None:
	name = signal.Signals(signum).name
	root = pathlib.Path(__file__).resolve().parent.parent
	proc = subprocess.Popen(
		[sys.executable, str(root / 'tools' / '_hold_forever.py'), WORKLOAD, '--seconds', '400'],
		stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=root,
	)
	lease_id = ''
	deadline = time.monotonic() + 700
	try:
		while time.monotonic() < deadline:
			line = proc.stdout.readline()
			if not line:
				break
			print(f'    child: {line.rstrip()}', flush=True)
			if line.startswith('HOLDING '):
				lease_id = line.split()[1]
				break
		if not lease_id:
			record(f'3. {name} mid-hold releases cleanly', False,
			       f'child never reported HOLDING; stderr tail: {proc.stderr.read()[-300:]}')
			return

		time.sleep(5.0)  # be unambiguously mid-hold, not mid-handshake
		print(f'    sending {name} to pid {proc.pid}', flush=True)
		proc.send_signal(signum)
		out, err = proc.communicate(timeout=120)
		print(f'    child exited {proc.returncode}: {out.strip()!r}', flush=True)

		released = sync.get(lease_id)
		gone = released is None or released.terminal
		said_so = f'RELEASED_ON_SIGNAL {name}' in out
		record(f'3. {name} mid-hold releases cleanly', gone and said_so and proc.returncode == 0,
		       f'child exited {proc.returncode} saying {out.strip()!r}; warden now reports the '
		       f'lease as {"gone" if released is None else released.state}')
	finally:
		if proc.poll() is None:
			proc.kill()
			proc.wait(timeout=30)
		# Whatever happened, do not leave the card held by a child of this gate.
		if lease_id:
			try:
				sync.release(lease_id)
			except Exception as err:  # noqa: BLE001 - best effort cleanup
				print(f'    cleanup release failed: {err}', flush=True)


# ── gate 4 (and obligation 4): the assertions catch a wrong answer ───────────
async def gate_assertions(warden: AsyncWardenClient) -> None:
	async with hold(WORKLOAD, reason='browsin phase 2 gate 4 (assertions)',
	                num_ctx=EXPECTED_NUM_CTX, handle_signals=False) as card:
		print(f'  resident: {json.dumps(await asyncio.to_thread(_ps_summary, card.endpoint))}',
		      flush=True)

		try:
			await asyncio.to_thread(assert_resident, card.endpoint, WRONG_TAG)
		except NotResident as err:
			record('4. the /api/ps assertion catches a deliberately wrong model name', True,
			       f'assert_resident({WRONG_TAG!r}) raised NotResident: {str(err)[:150]}')
		else:
			record('4. the /api/ps assertion catches a deliberately wrong model name', False,
			       f'assert_resident({WRONG_TAG!r}) passed while {WORKLOAD} was leased')

		# Obligation 4 from §4.2. Not in the gate's five, but it is the other assertion
		# lease.py makes and a wrong answer here oversubscribes the card silently.
		wrong_ctx = EXPECTED_NUM_CTX * 8
		try:
			served = await asyncio.to_thread(assert_context_window, card.endpoint, WORKLOAD,
			                                 wrong_ctx)
		except ContextWindowMismatch as err:
			record('4b. the num_ctx assertion catches a wrong window', True,
			       f'assert_context_window(..., {wrong_ctx}) raised: {str(err)[:150]}')
		except ContextWindowUnknown as err:
			note('4b. the num_ctx assertion catches a wrong window',
			     f'UNPROVEN — this Ollama does not report a context length on /api/ps, so '
			     f'obligation 4 cannot be checked from the client: {str(err)[:200]}')
		else:
			record('4b. the num_ctx assertion catches a wrong window', False,
			       f'it returned {served} instead of rejecting {wrong_ctx}')

		if card.num_ctx is not None:
			note('num_ctx served', f'/api/ps reports context_length={card.num_ctx} for '
			                       f'{card.model_tag}, matching the configured {EXPECTED_NUM_CTX}')


def _ps_summary(endpoint: str) -> list[dict]:
	from browsin.lease import resident_models
	return [{'model': m.get('model') or m.get('name'),
	         'size_vram_mib': round((m.get('size_vram') or 0) / 1048576),
	         'context_length': m.get('context_length')}
	        for m in resident_models(endpoint)]


# ── gates 1 and 2: the ten-minute hold, then the release ─────────────────────
async def gate_hold_and_free(warden: AsyncWardenClient, baseline: dict, hold_s: float) -> None:
	samples: list[dict] = []
	lease_id = ''
	t0 = time.monotonic()
	max_event_id = await max_event(warden)

	async with hold(WORKLOAD, reason='browsin phase 2 gates 1-2 (ten-minute hold)',
	                handle_signals=False) as card:
		lease_id = card.held.lease_id
		granted = time.monotonic()
		print(f'  granted after {granted - t0:.1f}s; holding {hold_s:.0f}s', flush=True)
		while time.monotonic() - granted < hold_s:
			status = await warden.status()
			leases = {l.get('lease_id'): l for l in status.get('leases') or []}
			tenants = [t.get('workload_id') for t in status.get('tenants') or []]
			v = vram(status)
			samples.append({
				't': round(time.monotonic() - granted, 1),
				'mine': lease_id in leases,
				'state': (leases.get(lease_id) or {}).get('state'),
				'tenant': WORKLOAD in tenants,
				'free': v.get('free_mib'), 'committed': v.get('committed_mib'),
				'ghost': v.get('ghost_mib'),
			})
			card.check()
			print(f'    +{samples[-1]["t"]:>5.0f}s  lease={samples[-1]["state"]}  '
			      f'tenant={samples[-1]["tenant"]}  free={samples[-1]["free"]}  '
			      f'committed={samples[-1]["committed"]}  ghost={samples[-1]["ghost"]}',
			      flush=True)
			await asyncio.sleep(SAMPLE_S)

	held_s = time.monotonic() - granted
	if not samples:
		record('1. a lease held 10 continuous minutes, visible in /v1/status throughout',
		       False, f'held {held_s:.0f}s but took no samples — --hold-s below one interval?')
		samples = [{'mine': False, 'state': None, 'tenant': False, 'free': None,
		            'committed': None, 'ghost': 0}]
	bad = [s for s in samples if not (s['mine'] and s['state'] == 'active' and s['tenant'])]
	record('1. a lease held 10 continuous minutes, visible in /v1/status throughout',
	       not bad and held_s >= hold_s and len(samples) >= hold_s / SAMPLE_S - 1,
	       f'{held_s:.0f}s held, {len(samples)} samples, {len(bad)} of them not showing an '
	       f'active lease + tenant; committed {samples[0]["committed"]}→'
	       f'{samples[-1]["committed"]} MiB, ghost stayed '
	       f'{max(s["ghost"] or 0 for s in samples)}')

	await gate_freed(warden, since_id=max_event_id, baseline=baseline)


async def max_event(warden: AsyncWardenClient) -> int:
	"""The newest event id, so a later scan can ignore everything that came before.

	Without this the gate matches an `evict_verified` from a *previous* run and reports a
	pass in about a second — which is exactly what the first version of it did.
	"""
	events = await warden.events(limit=5)
	return max((int(e.get('id') or 0) for e in events), default=0)


async def gate_freed(warden: AsyncWardenClient, *, since_id: int, baseline: dict) -> None:
	"""Gate 2. Watches the teardown that follows a release — it does not cause one.

	Release does **not** free VRAM. `_close_lease` marks the tenant `lingering` and leaves
	it READY; `_begin_stop` only runs `idle_linger_s` (180 s for both ollama workloads)
	later, and `_poll_stopping` then has up to `evict_verify_timeout_s` (30 s) to see the
	memory come back. So the whole observation is ~210 s wide and there is nothing to do
	but wait for it.
	"""
	print(f'  watching for the teardown (events after id {since_id}); '
	      f'idle_linger 180s + evict verify 30s…', flush=True)
	deadline = time.monotonic() + FREED_WAIT_S
	seen: dict[str, dict] = {}
	while time.monotonic() < deadline:
		for ev in await warden.events(limit=300):
			if int(ev.get('id') or 0) <= since_id or ev.get('workload_id') != WORKLOAD:
				continue
			kind = str(ev.get('kind'))
			if kind in ('lingering', 'stopping', 'evict_verified', 'evict_unverified',
			            'stopped', 'lost_abandoned') and kind not in seen:
				seen[kind] = ev
				print(f'    event {ev["id"]} {kind}: {ev.get("detail") or ev.get("fields")}',
				      flush=True)
		if 'stopped' in seen or 'evict_unverified' in seen or 'lost_abandoned' in seen:
			break
		await asyncio.sleep(10.0)

	status = await warden.status()
	v = vram(status)
	tenants = [t.get('workload_id') for t in status.get('tenants') or []]
	resident = await asyncio.to_thread(_ps_summary, baseline['endpoint'])

	verified = seen.get('evict_verified')
	fields = (verified or {}).get('fields') or {}
	freed, expected = fields.get('freed_mib'), fields.get('expected_mib')
	fraction = (freed / expected) if (freed and expected) else None

	ok = (
		verified is not None
		and 'evict_unverified' not in seen
		and 'lost_abandoned' not in seen
		and not v.get('ghost_mib')
		and not v.get('committed_mib')
		and WORKLOAD not in tenants
		and not resident
		and fraction is not None and fraction >= VERIFY_FREED_FRACTION
	)
	detail = (f'saw {sorted(seen)}; '
	          + (f'evict_verified freed {freed} of an expected {expected} MiB '
	             f'({fraction:.2f} >= verify_freed_fraction {VERIFY_FREED_FRACTION})'
	             if fraction is not None else 'no evict_verified with a freed_mib field')
	          + f'; after teardown: committed={v.get("committed_mib")} ghost={v.get("ghost_mib")} '
	            f'free={v.get("free_mib")} tenants={tenants} /api/ps={resident}')
	record('2. release frees within verify_freed_fraction and books no ghost', ok, detail)


# ── main ─────────────────────────────────────────────────────────────────────
async def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument('--hold-s', type=float, default=HOLD_S)
	ap.add_argument('--only', default='',
	                help='comma-separated: lost,sigint,sigterm,assert,hold,freed')
	ap.add_argument('--since-event-id', type=int, default=0,
	                help='for --only freed: ignore events at or below this id')
	args = ap.parse_args()
	only = {s.strip() for s in args.only.split(',') if s.strip()}

	logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
	if not os.environ.get('WARDEN_URL') and not os.environ.get('WARDEN_HOST'):
		raise SystemExit('set WARDEN_URL (and WARDEN_TOKEN_FILE) first')

	# Every hold below passes handle_signals=False, deliberately: the gate must measure
	# lease.py's signal behaviour in a CHILD process, not have it fire in the harness
	# half-way through a measurement. The cost is that this process would then take a
	# default-disposition SIGTERM — no finally, no atexit — and strand the card, which is
	# exactly what happened once while stopping a contaminated run. Turning the signal into
	# a SystemExit is enough: warden's lease() registers an atexit _last_resort at acquire,
	# and SystemExit runs it.
	def _exit_on_signal(signum: int, _frame: object) -> None:
		raise SystemExit(128 + signum)

	for _sig in (signal.SIGTERM, signal.SIGHUP):
		signal.signal(_sig, _exit_on_signal)

	warden = AsyncWardenClient.from_env()
	sync = WardenClient.from_env()
	baseline = await preflight(warden)
	baseline['endpoint'] = OLLAMA_ENDPOINT

	steps = [
		('lost', lambda: gate_lost(warden, sync)),
		('sigint', lambda: asyncio.to_thread(gate_signal, sync, signal.SIGINT)),
		('sigterm', lambda: asyncio.to_thread(gate_signal, sync, signal.SIGTERM)),
		('assert', lambda: gate_assertions(warden)),
		('hold', lambda: gate_hold_and_free(warden, baseline, args.hold_s)),
		# NOT in the default sequence: `hold` already runs gate_freed with the right
		# since_id. This entry exists only for `--only freed`, to watch a teardown that is
		# already in flight after a lease this gate did not take. Run by default it scans
		# from id 0 and re-matches an evict_verified from an older run — which it did,
		# picking up a degenerate `0 MiB of an expected 0 MiB` and failing on it.
		('freed', lambda: gate_freed(warden, since_id=args.since_event_id, baseline=baseline)),
	]
	default_steps = {'lost', 'sigint', 'sigterm', 'assert', 'hold'}
	for name, step in steps:
		if name not in (only or default_steps):
			continue
		print(f'\n{"=" * 78}\n== {name}\n{"=" * 78}', flush=True)
		try:
			await step()
		except Exception as err:  # noqa: BLE001 - one gate must not abort the rest
			logging.exception('gate %s blew up', name)
			record(f'({name}) raised', False, f'{type(err).__name__}: {str(err)[:220]}')

	print('\n' + '=' * 78)
	width = max(len(g) for _s, g, _d in results)
	for status, gate, detail in results:
		print(f'  [{status}] {gate.ljust(width)}  {detail}')
	checks = [r for r in results if r[0] != 'NOTE']
	failed = [g for s, g, _d in checks if s == 'FAIL']
	print()
	if failed:
		print(f'PHASE 2 GATE: FAILED — {len(failed)} of {len(checks)}: {", ".join(failed)}')
		return 1
	print(f'PHASE 2 GATE: PASSED — {len(checks)} of {len(checks)} checks')
	return 0


if __name__ == '__main__':
	raise SystemExit(asyncio.run(main()))
