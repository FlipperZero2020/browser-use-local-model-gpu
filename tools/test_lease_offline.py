#!/usr/bin/env python3
"""Everything about `browsin.lease` that can be proven without warden or the card.

    venv/bin/python tools/test_lease_offline.py

A fake `AsyncWardenClient` stands in for the real one, so the cancellation and signal
paths can be exercised in a second instead of in a 190 s cold load — and exercised in the
cases a real run makes hard to reach on purpose, like a signal arriving *during* the
acquire. The card-level facts stay in `tools/phase2_gate.py`; this is the part that would
otherwise only ever be tested by accident.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from warden.client import Lease, LeaseLost  # noqa: E402

from browsin.lease import (  # noqa: E402
	ContextWindowMismatch,
	ContextWindowUnknown,
	Interrupted,
	NotResident,
	_LostWatcher,
	assert_context_window,
	assert_resident,
	hold,
	is_ollama,
	normalise_tag,
)

FAKE_ENDPOINT = 'http://127.0.0.1:1'
failures: list[str] = []


def check(name: str, ok: bool, detail: str = '') -> None:
	print(f'  [{"PASS" if ok else "FAIL"}] {name}{"  — " + detail if detail else ""}')
	if not ok:
		failures.append(name)


# ── the stand-in ─────────────────────────────────────────────────────────────
class _FakeSync:
	def __init__(self, journal: list) -> None:
		self.journal = journal

	def release(self, lease_id: str) -> None:
		self.journal.append(('sync_release', lease_id))
		# The second-signal path re-raises the signal and dies, so nothing it appends to
		# an in-process list can ever be printed. A file is the only channel that
		# survives, which is the whole reason the marker exists.
		marker = os.environ.get('BROWSIN_TEST_RELEASE_MARKER')
		if marker:
			with open(marker, 'a', encoding='utf-8') as handle:
				handle.write(f'sync_release {lease_id}\n')


class _FakeHeld:
	def __init__(self, lease: Lease, lost: threading.Event) -> None:
		self.lease_id = lease.lease_id
		self.endpoint = lease.endpoint or ''
		self.lease = lease
		self.lost_event = lost

	@property
	def lost(self) -> bool:
		return self.lost_event.is_set()

	def check(self) -> None:
		if self.lost_event.is_set():
			raise LeaseLost('gone', state='gone', lease_id=self.lease_id)


class FakeWarden:
	"""Same shape as `AsyncWardenClient` for the two things `hold()` touches."""

	def __init__(self, *, acquire_delay: float = 0.0) -> None:
		self.journal: list = []
		self.sync = _FakeSync(self.journal)
		self.acquire_delay = acquire_delay
		self.lost = threading.Event()

	@contextlib.asynccontextmanager
	async def lease(self, workload: str, *, on_state=None, **_kw):
		await asyncio.sleep(self.acquire_delay)  # the cold load, in miniature
		view = Lease(lease_id='fake0000', workload_id=workload, state='active',
		             endpoint=FAKE_ENDPOINT, ttl_s=120.0, heartbeat_interval_s=30.0, now=0.0)
		if on_state is not None:
			on_state(view)
		self.journal.append(('acquired', view.lease_id))
		try:
			yield _FakeHeld(view, self.lost)
		finally:
			self.journal.append(('released', view.lease_id))


# ── in-process cases ─────────────────────────────────────────────────────────
async def case_normal() -> None:
	w = FakeWarden()
	async with hold('ollama:qwen3:8b', client=w, verify=False, handle_signals=False) as card:
		assert card.endpoint == FAKE_ENDPOINT
	check('a normal exit releases exactly once',
	      [k for k, _ in w.journal].count('released') == 1, str(w.journal))


async def case_body_raises() -> None:
	w = FakeWarden()
	with contextlib.suppress(ZeroDivisionError):
		async with hold('ollama:qwen3:8b', client=w, verify=False, handle_signals=False):
			raise ZeroDivisionError('the body blew up')
	check('an exception in the body still releases',
	      [k for k, _ in w.journal].count('released') == 1, str(w.journal))


async def case_verify_fails() -> None:
	w = FakeWarden()
	# verify=True against an endpoint nothing answers on: the /api/ps probe raises, and
	# the point is that the lease is given back anyway.
	with contextlib.suppress(Exception):
		async with hold('ollama:qwen3:8b', client=w, verify=True, handle_signals=False):
			check('unreachable', False, 'the body should never have run')
	check('a failed assertion releases before yielding',
	      [k for k, _ in w.journal].count('released') == 1, str(w.journal))


async def case_lost() -> None:
	w = FakeWarden()
	t0 = time.monotonic()
	try:
		async with hold('ollama:qwen3:8b', client=w, verify=False, handle_signals=False):
			threading.Timer(0.3, w.lost.set).start()
			await asyncio.sleep(10)
	except LeaseLost:
		check('a lost lease cancels the body and raises LeaseLost',
		      [k for k, _ in w.journal].count('released') == 1,
		      f'{time.monotonic() - t0:.2f}s, {w.journal}')
		return
	check('a lost lease cancels the body and raises LeaseLost', False, 'no LeaseLost')


async def case_swallows_cancel() -> None:
	"""A body that catches CancelledError broadly must not produce a silent success."""
	w = FakeWarden()
	swallowed = {'n': 0}
	t0 = time.monotonic()
	try:
		async with hold('ollama:qwen3:8b', client=w, verify=False, handle_signals=False):
			threading.Timer(0.2, w.lost.set).start()
			for _ in range(4):
				try:
					await asyncio.sleep(3)
				except asyncio.CancelledError:
					swallowed['n'] += 1
					asyncio.current_task().uncancel()
	except LeaseLost as err:
		check('a body that swallows every cancel still fails loudly',
		      'swallowed the cancellation' in str(err) or swallowed['n'] > 0,
		      f'swallowed {swallowed["n"]} cancel(s) over {time.monotonic() - t0:.1f}s, '
		      f'then LeaseLost: {str(err)[:80]}')
		return
	check('a body that swallows every cancel still fails loudly', False,
	      f'exited cleanly after swallowing {swallowed["n"]} cancel(s)')


async def case_external_cancel() -> None:
	w = FakeWarden()
	task = asyncio.current_task()
	assert task is not None
	asyncio.get_running_loop().call_later(0.3, task.cancel)
	try:
		async with hold('ollama:qwen3:8b', client=w, verify=False, handle_signals=False):
			await asyncio.sleep(10)
	except LeaseLost:
		check('an external cancel is not disguised as a lease loss', False, 'became LeaseLost')
	except Interrupted:
		check('an external cancel is not disguised as a lease loss', False, 'became Interrupted')
	except asyncio.CancelledError:
		task.uncancel()
		check('an external cancel is not disguised as a lease loss',
		      [k for k, _ in w.journal].count('released') == 1, str(w.journal))


# ── the /api/ps assertions, against a stub that answers whatever we like ─────
import http.server  # noqa: E402
import json as _json  # noqa: E402
import threading as _threading  # noqa: E402


class _PsStub:
	"""A one-route HTTP server standing in for Ollama, so the assertions can be shown
	the payloads a real box only produces when something has already gone wrong."""

	def __init__(self, payload: object) -> None:
		self.payload = payload
		outer = self

		class Handler(http.server.BaseHTTPRequestHandler):
			def do_GET(self) -> None:  # noqa: N802
				body = _json.dumps(outer.payload).encode()
				self.send_response(200)
				self.send_header('Content-Type', 'application/json')
				self.send_header('Content-Length', str(len(body)))
				self.end_headers()
				self.wfile.write(body)

			def log_message(self, *_a: object) -> None:
				pass

		self.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
		self.url = f'http://127.0.0.1:{self.server.server_port}'

	def __enter__(self) -> '_PsStub':
		_threading.Thread(target=self.server.serve_forever, daemon=True).start()
		return self

	def __exit__(self, *_exc: object) -> None:
		self.server.shutdown()
		self.server.server_close()


def _entry(**over: object) -> dict:
	entry = {'name': 'qwen3:8b', 'model': 'qwen3:8b', 'size': 5578204118,
	         'size_vram': 5578204118, 'context_length': 4096}
	entry.update(over)
	return entry


def case_assertions() -> None:
	# The one that matters: loaded, right name, and mostly on the CPU.
	with _PsStub({'models': [_entry(size_vram=2600000000)]}) as stub:
		try:
			assert_resident(stub.url, 'ollama:qwen3:8b')
		except NotResident as err:
			check('a model Ollama split onto the CPU is not "resident"',
			      'split it with the CPU' in str(err), str(err)[:110])
		else:
			check('a model Ollama split onto the CPU is not "resident"', False,
			      'passed a model that is 47% on the card')

	with _PsStub({'models': [_entry()]}) as stub:
		try:
			assert_resident(stub.url, 'ollama:qwen3:8b')
			ok = True
		except NotResident as err:
			ok, detail = False, str(err)[:110]
		check('a fully-resident model passes', ok, '' if ok else detail)

	# A 200 of the wrong shape, which a proxy or captive portal will happily return.
	for payload, label in ((None, 'null'), ([], 'a list')):
		with _PsStub(payload) as stub:
			try:
				assert_resident(stub.url, 'ollama:qwen3:8b')
			except NotResident as err:
				check(f'/api/ps answering {label} is a clear error, not an AttributeError',
				      'not a JSON object' in str(err), str(err)[:90])
			except Exception as err:  # noqa: BLE001
				check(f'/api/ps answering {label} is a clear error, not an AttributeError',
				      False, f'{type(err).__name__}: {err}')

	# "I could not check" must stay distinct from "I checked and it was wrong".
	with _PsStub({'models': [_entry(context_length=None,
	                                details={'parameter_size': '8.2B', 'context_length': 40960})]}) as stub:
		try:
			assert_context_window(stub.url, 'ollama:qwen3:8b', 4096)
		except ContextWindowUnknown:
			check('a missing context length is UNKNOWN, not read from details', True,
			      'details.context_length (40960, the architectural max) was not used')
		except ContextWindowMismatch as err:
			check('a missing context length is UNKNOWN, not read from details', False,
			      f'read the wrong field: {str(err)[:80]}')

	with _PsStub({'models': [_entry()]}) as stub:
		try:
			assert_context_window(stub.url, 'ollama:qwen3:8b', 32768)
		except ContextWindowMismatch as err:
			check('a wrong num_ctx is caught', 'served at num_ctx=4096' in str(err), str(err)[:90])
		else:
			check('a wrong num_ctx is caught', False, 'no exception')

	check('normalise_tag handles every declared workload id',
	      [normalise_tag(w) for w in ('ollama:qwen3:8b', 'ollama:qwen2.5-coder:14b')]
	      == ['qwen3:8b', 'qwen2.5-coder:14b'],
	      str([normalise_tag(w) for w in ('ollama:qwen3:8b', 'ollama:qwen2.5-coder:14b')]))
	check('non-ollama workloads are not probed for /api/ps',
	      not any(is_ollama(w) for w in ('clonin', 'acestep', 'exclusive:hashcat'))
	      and is_ollama('ollama:qwen3:8b'), '')


# ── subprocess cases: real signals ───────────────────────────────────────────
CHILD = r'''
import asyncio, os, signal, sys, time, pathlib
sys.path.insert(0, {root!r})
from browsin.lease import Interrupted, hold
sys.path.insert(0, {tools!r})
from test_lease_offline import FakeWarden

async def main():
    w = FakeWarden(acquire_delay={delay})
    try:
        async with hold('ollama:qwen3:8b', client=w, verify=False) as card:
            print('HOLDING', flush=True)
            {body}
    except Interrupted as err:
        print('INTERRUPTED', err.signal, flush=True)
    except asyncio.CancelledError:
        print('CANCELLED_DURING_ACQUIRE', flush=True)
    print('JOURNAL', [k for k, _ in w.journal], flush=True)

asyncio.run(main())
'''


def run_child(name: str, *, delay: float, body: str, signals: int, wait_for_holding: bool,
              expect: str, marker: pathlib.Path | None = None,
              expect_rc: int | None = None) -> None:
	root = str(pathlib.Path(__file__).resolve().parent.parent)
	source = CHILD.format(root=root, tools=str(pathlib.Path(__file__).resolve().parent),
	                      delay=delay, body=body)
	env = dict(os.environ)
	if marker is not None:
		marker.unlink(missing_ok=True)
		env['BROWSIN_TEST_RELEASE_MARKER'] = str(marker)
	proc = subprocess.Popen([sys.executable, '-u', '-c', source], stdout=subprocess.PIPE,
	                        stderr=subprocess.PIPE, text=True, cwd=root, env=env)
	if wait_for_holding:
		while True:
			line = proc.stdout.readline()
			if not line or line.startswith('HOLDING'):
				break
	else:
		time.sleep(0.4)  # mid-acquire, before the fake lease is granted
	for i in range(signals):
		proc.send_signal(signal.SIGTERM)
		if i + 1 < signals:
			time.sleep(0.3)
	out, err = proc.communicate(timeout=60)
	got = (out or '') + (err or '')
	if marker is not None:
		got += marker.read_text(encoding='utf-8') if marker.exists() else ''
	ok = expect in got and (expect_rc is None or proc.returncode == expect_rc)
	check(name, ok,
	      f'rc={proc.returncode} out={out.strip()!r}' + (f' err={err.strip()[-160:]!r}' if err.strip() else ''))


async def main() -> int:
	print('\nin-process (fake warden):')
	for case in (case_normal, case_body_raises, case_verify_fails, case_lost,
	             case_swallows_cancel, case_external_cancel):
		await case()

	print('\nthe /api/ps assertions, against a stub:')
	case_assertions()

	print('\nreal signals, in a child process:')
	run_child('SIGTERM mid-hold releases and reports which signal',
	          delay=0.0, body='await asyncio.sleep(30)', signals=1,
	          wait_for_holding=True, expect='INTERRUPTED SIGTERM')
	run_child('SIGTERM mid-hold still runs the release',
	          delay=0.0, body='await asyncio.sleep(30)', signals=1,
	          wait_for_holding=True, expect="'released'")
	# The one that matters most: before the handlers were installed ahead of the acquire,
	# this was a DEFAULT-disposition SIGTERM — no finally, no atexit, lease stranded for
	# ttl_s + idle_linger_s. That is how Phase 1 lost one.
	run_child('SIGTERM DURING the acquire is caught, not a bare kill',
	          delay=5.0, body='await asyncio.sleep(30)', signals=1,
	          wait_for_holding=False, expect='INTERRUPTED SIGTERM', expect_rc=0)
	# A loop wedged in a blocking call cannot run the graceful callback at all. The second
	# signal must still give the card back and then actually die.
	run_child('a second SIGTERM escapes a loop wedged in a blocking call',
	          delay=0.0, body='time.sleep(30)', signals=2,
	          wait_for_holding=True, expect='sync_release',
	          marker=pathlib.Path('/tmp/browsin-test-release-marker'),
	          expect_rc=-signal.SIGTERM)

	print()
	if failures:
		print(f'OFFLINE LEASE TESTS: FAILED — {len(failures)}: {", ".join(failures)}')
		return 1
	print('OFFLINE LEASE TESTS: all passed')
	return 0


if __name__ == '__main__':
	raise SystemExit(asyncio.run(main()))
