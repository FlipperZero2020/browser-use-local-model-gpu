"""Hold the card from asyncio, and give it back on every path out.

    async with hold("ollama:qwen3:8b", reason="check a web page") as card:
        ...                                   # card.endpoint is live and staying that way

`warden.client` already does the acquire / wait / heartbeat / release handshake, and this
module does not reimplement it. What it adds is the five obligations PLAN.md §4.2 lists,
each of which exists because the obvious version is wrong:

1. **Acquire, wait, heartbeat** — delegated to `AsyncWardenClient.lease()`. The heartbeat
   starts at *acquire*, not at grant, because a queued lease runs down the same TTL clock.
2. **Loss cancels the work.** `Held.lost_event` is a `threading.Event` set by warden's
   heartbeat thread; this bridges it to `Task.cancel()`. Polling `:11434` instead tells
   you nothing: warden is a control plane, not a proxy, so when it revokes your lease it
   unloads the model and **the endpoint keeps answering** — the next request silently
   reloads several GB outside warden's book while the session looks healthy. The
   heartbeat's 404 is the only revocation channel that exists.
3. **Assert what is resident.** Nothing enforces that your request names the model you
   leased. Two seconds of `/api/ps` turns an OOM at step 12 into an error at step 0.
4. **Assert the context window.** warden booked `cost_mib` at some `num_ctx`; the client
   sets `num_ctx` from Python. They are one number written in two places, and if they
   drift the card is oversubscribed silently.
5. **Release on every path, including SIGTERM.** Python runs neither `finally` nor
   `atexit` for a default SIGTERM, which stranded a lease during Phase 1. A `SIGKILL` is
   uncoverable and `ttl_s` is the only lever against it — hence a short default.

Stdlib only, on purpose: `warden.client` keeps a hard dependency floor because its
consumers run on other machines, and the `/api/ps` probe here honours the same one rather
than dragging in `httpx` for two GETs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from warden.client import (
	AsyncWardenClient,
	Held,
	Lease,
	LeaseLost,
	WardenClient,
	heartbeat_interval_for,
)

log = logging.getLogger('browsin.lease')

#: Deliberately shorter than policy's 300 s default. TTL is the only thing that collects a
#: lease after a SIGKILL, and `ollama:qwen3:8b` lingers a further `idle_linger_s = 180`
#: after the lease closes — so 300 + 180 is eight minutes of a stranded card, and 120 + 180
#: is five. Anything shorter starts racing a slow warden: the heartbeat cadence is
#: `min(policy interval, ttl/3)`, so at 90 s the client beats every 30 s with one beat of
#: slack, and below that it beats faster for no benefit.
DEFAULT_TTL_S = 120.0

#: Longer than the 600 s `start_timeout_s` both ollama workloads now carry, on purpose. If
#: the client gives up first, warden is still loading and the next acquire collides with a
#: load in flight; letting warden's own timeout fire first turns the failure into a
#: `start_failed` event and a terminal lease state that says what happened.
DEFAULT_ACQUIRE_TIMEOUT_S = 660.0

#: The plan's choice (§1). `interactive` outranks `batch`, so a lease may evict clonin or
#: ACE-Step but never another interactive tenant.
DEFAULT_PRIORITY = 'interactive'

#: How often the watcher thread re-checks `lost_event`. Two orders of magnitude below the
#: 30 s heartbeat it is watching, so it contributes nothing measurable to the gate's
#: "cancels within one heartbeat", and it costs one wakeup every quarter second.
LOST_POLL_S = 0.25

#: Set by `warden hold` (PLAN.md §7, Phase 7). If it is already in the environment,
#: somebody upstream is holding the card and acquiring a second lease would double-book it.
ENDPOINT_ENV = 'WARDEN_ENDPOINT'


class Interrupted(KeyboardInterrupt):
	"""SIGINT or SIGTERM arrived while the card was held, and the lease was released.

	A `KeyboardInterrupt` subclass so ordinary `except KeyboardInterrupt` still works and
	`except Exception` still does not swallow it, with `.signal` naming which one it was —
	because "the operator stopped it" and "SIGTERM from a tool timeout" call for different
	messages, and by the time it reaches a caller they are otherwise indistinguishable.
	"""

	def __init__(self, message: str, *, signal: str) -> None:
		super().__init__(message)
		self.signal = signal


class LeaseAssertionError(RuntimeError):
	"""The lease was granted but the card is not in the state it implies."""


class NotResident(LeaseAssertionError):
	"""`/api/ps` does not show exactly the model that was leased."""


class ContextWindowMismatch(LeaseAssertionError):
	"""The served context window is not the one the policy entry was measured at."""


class ContextWindowUnknown(LeaseAssertionError):
	"""Ollama did not report a context length, so obligation 4 cannot be checked.

	Distinct from a mismatch on purpose: "I looked and it was wrong" and "I could not
	look" call for different responses, and collapsing them is how a missing field
	becomes a false alarm — or worse, a silent pass.
	"""


# ── Ollama's /api/ps, over stdlib ────────────────────────────────────────────
def _get_json(url: str, timeout_s: float) -> Any:
	req = urllib.request.Request(url, headers={'Accept': 'application/json'})
	with urllib.request.urlopen(req, timeout=timeout_s) as resp:
		raw = resp.read()
	return json.loads(raw) if raw else {}


def normalise_tag(tag: str) -> str:
	"""`qwen3` and `qwen3:latest` are the same model; `ollama:qwen3:8b` names a workload.

	Warden's workload ids are `<driver>:<tag>`, and the tag itself contains a colon, so
	splitting on the *first* colon is the only split that is right for both.
	"""
	tag = tag.strip()
	if tag.startswith('ollama:'):
		tag = tag.split(':', 1)[1]
	return tag if ':' in tag else tag + ':latest'


def resident_models(endpoint: str, *, timeout_s: float = 15.0) -> list[dict]:
	"""`GET /api/ps` — what Ollama has loaded *right now*, as raw dicts.

	Raw rather than `ollama.ProcessResponse` because a typed model can only give back
	fields its own version knows about, and the field this module most wants — the served
	context length — is the one most likely to be newer than the client.
	"""
	body = _get_json(endpoint.rstrip('/') + '/api/ps', timeout_s)
	return list(body.get('models') or [])


def _context_length(entry: dict) -> int | None:
	"""The served window, from wherever this Ollama version puts it."""
	for key in ('context_length', 'num_ctx', 'context'):
		value = entry.get(key)
		if isinstance(value, int) and value > 0:
			return value
	details = entry.get('details')
	if isinstance(details, dict):
		for key in ('context_length', 'num_ctx'):
			value = details.get(key)
			if isinstance(value, int) and value > 0:
				return value
	return None


def assert_resident(endpoint: str, workload_or_tag: str, *, exact: bool = True,
                    timeout_s: float = 15.0) -> dict:
	"""Raise unless `/api/ps` shows the leased model. Returns its entry.

	`exact=True` — the plan's wording, and the default — also rejects the case where
	something *else* is loaded alongside it, because on this card that means either a
	second tenant is holding VRAM you did not budget for or a load leaked outside
	warden's book. Relax it only with a reason.
	"""
	want = normalise_tag(workload_or_tag)
	models = resident_models(endpoint, timeout_s=timeout_s)
	seen = {normalise_tag(str(m.get('model') or m.get('name') or '')): m for m in models}

	if want not in seen:
		if not models:
			raise NotResident(
				f'{endpoint}/api/ps shows nothing resident, but warden granted a lease for '
				f'{want}. The lease is active and the model is not loaded — that is warden '
				f'and Ollama disagreeing, not a slow load: wait_active already returned.'
			)
		raise NotResident(
			f'{endpoint}/api/ps shows {sorted(seen)} resident, not {want}. '
			f'Nothing makes a request name the model you leased, so this is the check that '
			f'turns an OOM at step 12 into an error at step 0.'
		)
	if exact and len(seen) > 1:
		other = sorted(set(seen) - {want})
		raise NotResident(
			f'{endpoint}/api/ps shows {other} resident alongside the leased {want}. '
			f'Either another tenant holds VRAM this run did not budget for, or a load '
			f'leaked outside warden\'s book. Check /v1/status before continuing.'
		)
	return seen[want]


def assert_context_window(endpoint: str, workload_or_tag: str, expected_num_ctx: int, *,
                          timeout_s: float = 15.0) -> int:
	"""Raise unless the served window is `expected_num_ctx`. Returns the served value.

	`expected_num_ctx` is the client's half of a number that also lives in warden's
	`policy.json`, where it decided `cost_mib`. If they drift, warden's book is wrong.
	"""
	entry = assert_resident(endpoint, workload_or_tag, exact=False, timeout_s=timeout_s)
	served = _context_length(entry)
	if served is None:
		raise ContextWindowUnknown(
			f'{endpoint}/api/ps reported no context length for '
			f'{normalise_tag(workload_or_tag)} (keys: {sorted(entry)}). Obligation 4 cannot '
			f'be checked from here on this Ollama version — read the served n_ctx from '
			f'D:\\warden\\logs\\ollama-server.log instead, and say so rather than assuming.'
		)
	if served != expected_num_ctx:
		raise ContextWindowMismatch(
			f'{normalise_tag(workload_or_tag)} is served at num_ctx={served}, but this run '
			f'is configured for {expected_num_ctx}. Sending the configured value would make '
			f'Ollama reload the model at a different size — outside warden\'s book, which '
			f'booked cost_mib at the measured window. Change both numbers or neither.'
		)
	return served


# ── threading.Event → asyncio cancellation ───────────────────────────────────
class _LostWatcher:
	"""Cancel a task when warden's heartbeat thread reports the lease gone.

	A thread rather than `await asyncio.to_thread(lost_event.wait)`, because cancelling
	that coroutine does not stop the thread underneath it: the clean-exit path would leave
	a threadpool worker blocked on an Event that is never set, for the life of the process.
	A short-timeout poll loop is stoppable, and 0.25 s of latency against a 30 s heartbeat
	is not a number anyone will ever measure.

	Idempotent: `stop()` twice is fine, and firing after the loop has closed is a no-op
	rather than a `RuntimeError` on a dead loop.
	"""

	def __init__(self, lost_event: threading.Event, loop: asyncio.AbstractEventLoop,
	             on_lost: Callable[[], None]) -> None:
		self._lost = lost_event
		self._loop = loop
		self._on_lost = on_lost
		self._stop = threading.Event()
		self._thread: threading.Thread | None = None

	def start(self) -> None:
		if self._thread is not None:
			return
		self._thread = threading.Thread(target=self._run, name='browsin-lost-watch', daemon=True)
		self._thread.start()

	def stop(self, *, join_s: float = 2.0) -> None:
		self._stop.set()
		thread, self._thread = self._thread, None
		if thread is not None and thread is not threading.current_thread():
			thread.join(timeout=join_s)

	def _run(self) -> None:
		while not self._stop.is_set():
			if not self._lost.wait(LOST_POLL_S):
				continue
			if self._stop.is_set():
				return
			try:
				self._loop.call_soon_threadsafe(self._on_lost)
			except RuntimeError:
				# The loop closed while we were waiting. There is nothing left to cancel.
				pass
			return


# ── what `hold()` yields ─────────────────────────────────────────────────────
@dataclass
class Card:
	"""A live endpoint, and the facts that were checked before you were handed it."""

	workload: str
	model_tag: str
	endpoint: str
	#: False when `$WARDEN_ENDPOINT` was already set — somebody upstream holds the lease
	#: and this process deliberately did not take a second one.
	leased: bool
	#: The served context window, when it could be read. None when Ollama did not report
	#: one and `num_ctx` was not asserted.
	num_ctx: int | None = None
	held: Held | None = None
	lease: Lease | None = None
	#: Set on the loop when the lease is lost. Awaitable, unlike `Held.lost_event`.
	lost: asyncio.Event = field(default_factory=asyncio.Event)

	@property
	def heartbeat_interval_s(self) -> float | None:
		return heartbeat_interval_for(self.lease) if self.lease is not None else None

	def check(self) -> None:
		"""Raise `LeaseLost` if the lease went away. Cheap; call it between steps."""
		if self.held is not None:
			self.held.check()
		elif self.lost.is_set():
			raise LeaseLost(f'lease for {self.workload} is gone', state='gone')


# ── signals ──────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _signal_cancellation(loop: asyncio.AbstractEventLoop, task: asyncio.Task,
                         seen: dict[str, Any]):
	"""SIGINT and SIGTERM cancel `task` instead of killing the process where it stands.

	`loop.add_signal_handler` rather than `signal.signal`, because it runs the callback
	*inside* the loop: the `async with` unwinds, warden's release runs, and the card comes
	back. A `signal.signal` handler raising `SystemExit` escapes `run_until_complete` and
	leaves the release to `atexit`, which is a strictly worse place for it to happen.

	A second signal restores the default disposition and re-raises, so an impatient
	operator is never trapped — the lease then falls to `atexit`, and failing that to the
	TTL. Handlers are removed on the way out, so a REPL keeps its ordinary Ctrl-C.
	"""
	def handle(signum: int) -> None:
		name = signal.Signals(signum).name
		if seen.get('signal') is not None:
			log.warning('%s again — giving up on a clean release; the TTL will collect it', name)
			with contextlib.suppress(RuntimeError, ValueError):
				loop.remove_signal_handler(signum)
			signal.signal(signum, signal.SIG_DFL)
			signal.raise_signal(signum)
			return
		seen['signal'] = name
		log.warning('%s — cancelling the run and giving the card back', name)
		task.cancel()

	installed: list[int] = []
	for signum in (signal.SIGINT, signal.SIGTERM):
		try:
			loop.add_signal_handler(signum, handle, signum)
		except (NotImplementedError, RuntimeError, ValueError):
			# Not the main thread, or a platform without it. warden's atexit still covers
			# the ordinary exits; only the SIGTERM case is lost, so say so.
			log.debug('could not install a handler for %s', signal.Signals(signum).name)
			continue
		installed.append(signum)
	try:
		yield
	finally:
		for signum in installed:
			with contextlib.suppress(RuntimeError, ValueError):
				loop.remove_signal_handler(signum)


# ── the handshake ────────────────────────────────────────────────────────────
@contextlib.asynccontextmanager
async def hold(
	workload: str,
	*,
	reason: str | None = None,
	num_ctx: int | None = None,
	ttl_s: float = DEFAULT_TTL_S,
	priority: str | None = DEFAULT_PRIORITY,
	timeout_s: float = DEFAULT_ACQUIRE_TIMEOUT_S,
	may_evict: bool = True,
	verify: bool = True,
	exact_residency: bool = True,
	handle_signals: bool = True,
	client: AsyncWardenClient | None = None,
	on_state: Callable[[Lease], None] | None = None,
) -> AsyncIterator[Card]:
	"""Lease `workload`, prove the card is in the state the lease implies, and yield it.

	The body is cancelled if the lease is lost, and a `LeaseLost` is raised in its place so
	the cancellation cannot be mistaken for an ordinary one. Everything is released on the
	way out: normally, on an exception, on Ctrl-C, and on SIGTERM.

	`num_ctx=None` skips obligation 4 rather than guessing — pass the window this run will
	configure and it becomes an assertion.
	"""
	inherited = os.environ.get(ENDPOINT_ENV)
	if inherited:
		# Somebody upstream — a future `warden hold` — already has the card. Taking a
		# second lease would double-book it against ourselves.
		log.info('%s is set; using the inherited endpoint and not acquiring a lease', ENDPOINT_ENV)
		card = Card(workload=workload, model_tag=normalise_tag(workload),
		            endpoint=inherited, leased=False)
		if verify:
			await asyncio.to_thread(assert_resident, card.endpoint, workload,
			                        exact=exact_residency)
			if num_ctx is not None:
				card.num_ctx = await asyncio.to_thread(
					assert_context_window, card.endpoint, workload, num_ctx)
		yield card
		return

	warden = client or AsyncWardenClient.from_env()
	loop = asyncio.get_running_loop()
	task = asyncio.current_task()
	assert task is not None, 'hold() must be awaited from inside a task'

	def narrate(view: Lease) -> None:
		log.info('lease %s: %s%s', view.lease_id[:8] or '?', view.state,
		         f' ({view.pending_reason})' if view.pending_reason else '')
		if on_state is not None:
			on_state(view)

	seen: dict[str, Any] = {'signal': None, 'lost': False}
	watcher: _LostWatcher | None = None

	async with warden.lease(
		workload, reason=reason, ttl_s=ttl_s, priority=priority,
		timeout_s=timeout_s, may_evict=may_evict, on_state=narrate,
		# We convert loss into cancellation ourselves and re-raise LeaseLost from there,
		# so the library's own exit-time raise would only ever be a duplicate.
		raise_if_lost=False,
	) as held:
		card = Card(workload=workload, model_tag=normalise_tag(workload),
		            endpoint=held.endpoint, leased=True, held=held, lease=held.lease)

		def on_lost() -> None:
			if seen['lost']:
				return
			seen['lost'] = True
			card.lost.set()
			log.error('lease %s lost — cancelling the run', held.lease_id[:8])
			task.cancel()

		try:
			if verify:
				# Off the loop: two blocking GETs, and the heartbeat thread keeps beating
				# through them either way.
				await asyncio.to_thread(assert_resident, card.endpoint, workload,
				                        exact=exact_residency)
				if num_ctx is not None:
					card.num_ctx = await asyncio.to_thread(
						assert_context_window, card.endpoint, workload, num_ctx)

			watcher = _LostWatcher(held.lost_event, loop, on_lost)
			watcher.start()

			with contextlib.ExitStack() as stack:
				if handle_signals:
					stack.enter_context(_signal_cancellation(loop, task, seen))
				yield card

		except asyncio.CancelledError:
			# Clear the pending cancellation before unwinding: the release below is an
			# `await`, and on a task still marked cancelling it would be interrupted
			# before it could give the card back.
			task.uncancel()
			if seen['lost']:
				raise LeaseLost(
					f'lease {held.lease_id} for {workload} was revoked while it was held; '
					f'the run was cancelled rather than left talking to an unloaded model',
					state='revoked', lease_id=held.lease_id,
				) from None
			if seen['signal']:
				raise Interrupted(
					f'{seen["signal"]} during {workload}; the lease was released',
					signal=seen['signal'],
				) from None
			raise
		finally:
			if watcher is not None:
				watcher.stop()


async def probe(client: AsyncWardenClient | None = None) -> dict:
	"""`GET /v1/status`, with `vram.available_mib` filled in. Takes nothing."""
	warden = client or AsyncWardenClient.from_env()
	return await warden.status()


__all__ = [
	'Card',
	'Interrupted',
	'ContextWindowMismatch',
	'ContextWindowUnknown',
	'LeaseAssertionError',
	'NotResident',
	'assert_context_window',
	'assert_resident',
	'hold',
	'normalise_tag',
	'probe',
	'resident_models',
]
