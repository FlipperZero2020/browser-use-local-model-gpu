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
#:
#: Nothing can go below that heartbeat. `_Heartbeat._run` is `while not
#: self._stop.wait(self.interval)` — it sleeps the whole interval *before* beating, and
#: only a beat's 404 sets `lost_event`. Warden has no push channel, so `ttl_s` is the only
#: lever on detection latency: the cadence is `min(policy interval, ttl_s/3)`, and warden
#: always sends 30, so `WardenClient(heartbeat_interval_s=…)` is never consulted.
LOST_POLL_S = 0.25

#: `Task.cancel()` is cooperative. A body parked in `asyncio.to_thread()` around a
#: blocking HTTP POST does not see the cancellation until that POST returns, and a step
#: that catches `CancelledError` broadly would swallow it outright. So the watcher keeps
#: asking rather than asking once.
#:
#: The cadence is a real trade-off and the first version got it wrong. Re-firing every
#: second cuts up the body's *own* async cleanup — a `finally` that awaits anything gets
#: cancelled again part-way through closing the browser. Five seconds, three times, gives
#: an unwinding body room while still defeating a swallow, and the whole window is 15 s
#: against a 30 s heartbeat. A body that needs longer than that after a revocation is
#: being cut short deliberately: the lease is already gone, the model is already unloaded,
#: and warden may be loading somebody else's weights into that memory.
REPEAT_CANCEL_S = 5.0
REPEAT_CANCEL_MAX = 3

#: Set by `warden hold` (PLAN.md §7, Phase 7). If it is already in the environment,
#: somebody upstream is holding the card and acquiring a second lease would double-book it.
ENDPOINT_ENV = 'WARDEN_ENDPOINT'

#: Total wall-clock budget for both `/api/ps` assertions together. Two local GETs against a
#: LAN box; if they have not finished in this long, something is wrong with the endpoint and
#: the lease should fail rather than hang before the caller ever sees the card.
ASSERTION_BUDGET_S = 45.0


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


def is_ollama(workload: str) -> bool:
	"""Only `ollama:*` workloads have an `/api/ps` to assert against.

	`hold()` is generic — it leases `clonin` and `acestep` just as happily — and probing
	those endpoints for `/api/ps` would fail for reasons that have nothing to do with the
	lease. Obligations 3 and 4 are Ollama-shaped, so they are skipped, out loud, elsewhere.
	"""
	return workload.startswith('ollama:')


def normalise_tag(tag: str) -> str:
	"""`qwen3` and `qwen3:latest` are the same model; `ollama:qwen3:8b` names a workload.

	Warden's workload ids are `<driver>:<tag>`, and the tag itself contains a colon, so
	splitting on the *first* colon is the only split that is right for both.
	"""
	tag = tag.strip()
	if tag.startswith('ollama:'):
		tag = tag.split(':', 1)[1]
	return tag if ':' in tag else tag + ':latest'


#: warden's ollama driver resolves the model as `spec.config.get('model')` first and only
#: falls back to stripping the `ollama:` prefix off the workload id — its own docstring says
#: `config.model` exists "for a workload whose id should not be its model name". Neither
#: declared ollama workload sets it today, so deriving the tag from the id is correct on
#: this box; `hold(model_tag=...)` is the escape hatch for when that stops being true, and
#: policy is not readable from the client to find out.
MODEL_TAG_NOTE = 'derived from the workload id; pass model_tag= if policy sets config.model'


def resident_models(endpoint: str, *, timeout_s: float = 15.0) -> list[dict]:
	"""`GET /api/ps` — what Ollama has loaded *right now*, as raw dicts.

	Raw rather than `ollama.ProcessResponse` because a typed model can only give back
	fields its own version knows about, and the field this module most wants — the served
	context length — is the one most likely to be newer than the client.
	"""
	body = _get_json(endpoint.rstrip('/') + '/api/ps', timeout_s)
	if not isinstance(body, dict):
		raise NotResident(
			f'{endpoint}/api/ps answered with {type(body).__name__}, not a JSON object. '
			f'Is that endpoint really Ollama? A reverse proxy or a captive portal will '
			f'happily return 200 and valid JSON of the wrong shape.'
		)
	return [m for m in (body.get('models') or []) if isinstance(m, dict)]


def _context_length(entry: dict) -> int | None:
	"""The window this runner was *loaded* at. Measured on 0.32.15: top-level
	`context_length`, 4096 for `qwen3:8b` — whose architectural maximum is 40960.

	Only these two keys, deliberately. `details` carries the model's own metadata
	(`parameter_size`, `quantization_level`, and on some builds the architecture's maximum
	context), which is a different number from the one the runner is serving. Reading it
	as a fallback would turn "I could not check" into a confident wrong answer, which is
	the exact failure `ContextWindowUnknown` exists to keep separate.
	"""
	for key in ('context_length', 'num_ctx'):
		value = entry.get(key)
		if isinstance(value, int) and value > 0:
			return value
	return None


def _on_card(entry: dict) -> tuple[int, int]:
	"""`(vram_bytes, total_bytes)` for a resident model. This is how `ollama ps` computes
	its 100% GPU / 47%/53% CPU/GPU column."""
	vram = entry.get('size_vram')
	total = entry.get('size')
	return (int(vram) if isinstance(vram, int) else 0,
	        int(total) if isinstance(total, int) else 0)


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
			f'Either another tenant holds VRAM this run did not budget for, or something '
			f'called :11434 without a lease and loaded weights outside warden\'s book — '
			f'an unleased call succeeds, it does not fail. Check /v1/status, and pass '
			f'exact_residency=False only once you know which.'
		)

	entry = seen[want]
	vram, total = _on_card(entry)
	# "Resident" is not the same as "on the GPU". Ollama silently splits a model across
	# CPU and GPU when it does not fit, and reports both numbers; `ollama ps` divides them
	# for its GPU% column. warden booked cost_mib of VRAM for this lease, so a model that
	# landed on the CPU means the book is wrong AND inference is an order of magnitude
	# slower — with nothing in the response saying so.
	if total > 0 and vram < total * 0.99:
		raise NotResident(
			f'{want} is loaded but only {vram / 1048576:.0f} MiB of {total / 1048576:.0f} '
			f'MiB is on the card ({100 * vram / total:.0f}%) — Ollama split it with the CPU. '
			f'warden booked this lease as VRAM, so its book is now wrong, and generation '
			f'will be far slower than the measurement this workload was declared from.'
		)
	return entry


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

	What it cannot see: `lost_event` fires only on a genuine 404. A warden that is merely
	*unreachable* is transient by construction (`_Heartbeat._run` swallows
	`WardenUnreachable` and keeps beating), so during a warden outage there is no signal
	at all — and one such beat can occupy the heartbeat thread for ~240 s, four attempts
	at a 60 s timeout plus backoff. Losing warden and losing the lease are different
	events and only the second one reaches here.
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
			# Returns True immediately if it is already set — which it can be, because
			# the heartbeat starts at acquire and `Held` only reaches us after
			# `wait_active`, so a lease can be lost before we ever see the handle.
			if self._lost.wait(LOST_POLL_S):
				break
		if self._stop.is_set():
			return
		for attempt in range(REPEAT_CANCEL_MAX):
			if attempt and self._stop.wait(REPEAT_CANCEL_S):
				return  # the holder acknowledged it and is unwinding; stop asking
			try:
				self._loop.call_soon_threadsafe(self._on_lost)
			except RuntimeError:
				# The loop closed while we were waiting. Nothing left to cancel.
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
                         seen: dict[str, Any], release_sync: Callable[[], None]):
	"""SIGINT and SIGTERM cancel `task` instead of killing the process where it stands.

	Two layers, because one is not enough.

	**In the loop.** `loop.add_signal_handler` rather than `signal.signal`, because it runs
	the callback *inside* the loop: the `async with` unwinds, warden's release runs, and
	the card comes back. A `signal.signal` handler raising `SystemExit` also works, but it
	does the cleanup during `Runner.close()` teardown and gives no control over the exit
	code.

	**On the main thread.** A loop callback cannot run while the loop is wedged — parked in
	`asyncio.to_thread()` around a blocking POST, which is the normal state of a browser
	agent mid-step. So a plain handler goes on top of it (both fire: overwriting asyncio's
	`_sighandler_noop` leaves the wakeup-fd write intact). It does nothing on the first
	signal, leaving the graceful path to work. On the **second** it restores the default
	disposition, releases the lease synchronously, and re-raises — so an impatient operator
	is never trapped into a `SIGKILL`, which is the one exit nothing can cover.

	Installed *before* the acquire, not after. A SIGTERM during a ~190 s cold load would
	otherwise hit the default disposition, which runs neither `finally` nor `atexit` — the
	precise way Phase 1 stranded a lease. Handlers are removed on the way out, so a
	REPL keeps its ordinary Ctrl-C.
	"""
	def graceful(signum: int) -> None:
		if seen.get('signal') is not None:
			return  # the plain handler below owns escalation
		seen['signal'] = signal.Signals(signum).name
		log.warning('%s — cancelling the run and giving the card back', seen['signal'])
		task.cancel()

	def plain(signum: int, _frame: Any) -> None:
		seen['signals_seen'] = seen.get('signals_seen', 0) + 1
		if seen['signals_seen'] < 2:
			return  # first one: let the loop callback do it properly
		name = signal.Signals(signum).name
		log.warning('%s again — releasing synchronously and stopping now', name)
		signal.signal(signum, signal.SIG_DFL)
		try:
			release_sync()
		finally:
			signal.raise_signal(signum)

	installed: list[tuple[int, Any]] = []
	for signum in (signal.SIGINT, signal.SIGTERM):
		try:
			loop.add_signal_handler(signum, graceful, signum)
		except (NotImplementedError, RuntimeError, ValueError):
			# Not the main thread, or a platform without it. warden's atexit still covers
			# the ordinary exits; only the bare-SIGTERM case is lost.
			log.debug('could not install a loop handler for %s', signal.Signals(signum).name)
			continue
		previous = signal.signal(signum, plain)
		installed.append((signum, previous))
	try:
		yield
	finally:
		for signum, previous in installed:
			with contextlib.suppress(RuntimeError, ValueError, TypeError):
				signal.signal(signum, previous)
			with contextlib.suppress(RuntimeError, ValueError):
				loop.remove_signal_handler(signum)


# ── the handshake ────────────────────────────────────────────────────────────
async def _run_assertions(card: Card, tag: str, num_ctx: int | None, exact: bool) -> None:
	"""Obligations 3 and 4, off the loop and on a total deadline.

	`urlopen(timeout=…)` bounds each socket operation, not the call: a server that trickles
	bytes forever never trips it. `wait_for` bounds the whole thing, which matters because
	this runs *before* the caller is handed the card and therefore before anything they
	wrote could cancel it.
	"""
	async def probe() -> None:
		await asyncio.to_thread(assert_resident, card.endpoint, tag, exact=exact)
		if num_ctx is not None:
			card.num_ctx = await asyncio.to_thread(
				assert_context_window, card.endpoint, tag, num_ctx)

	await asyncio.wait_for(probe(), timeout=ASSERTION_BUDGET_S)


@contextlib.asynccontextmanager
async def hold(
	workload: str,
	*,
	reason: str | None = None,
	num_ctx: int | None = None,
	model_tag: str | None = None,
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
	configure and it becomes an assertion. Obligations 3 and 4 are Ollama-shaped and are
	skipped, with a log line, for any other driver.
	"""
	tag = model_tag or normalise_tag(workload)
	assertable = verify and is_ollama(workload)
	if verify and not assertable:
		log.info('%s is not an ollama workload; skipping the /api/ps assertions', workload)
	inherited = os.environ.get(ENDPOINT_ENV)
	if inherited:
		# Somebody upstream — a future `warden hold` — already has the card. Taking a
		# second lease would double-book it against ourselves.
		log.info('%s is set; using the inherited endpoint and not acquiring a lease', ENDPOINT_ENV)
		card = Card(workload=workload, model_tag=tag, endpoint=inherited, leased=False)
		if assertable:
			await _run_assertions(card, tag, num_ctx, exact_residency)
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

	seen: dict[str, Any] = {'signal': None, 'lost': False, 'lease_id': None}
	watcher: _LostWatcher | None = None

	def release_sync() -> None:
		"""Blocking DELETE, safe from a signal handler. Idempotent; a 404 is not an error."""
		lease_id = seen.get('lease_id')
		if not lease_id:
			return
		with contextlib.suppress(Exception):
			warden.sync.release(lease_id)

	def remember(view: Lease) -> None:
		# The acquire snapshot is the earliest moment a lease id exists, and the last
		# resort above needs it before anything can go wrong with the wait.
		if view.lease_id:
			seen['lease_id'] = view.lease_id
		narrate(view)

	try:
		with contextlib.ExitStack() as stack:
			if handle_signals:
				stack.enter_context(_signal_cancellation(loop, task, seen, release_sync))

			async with warden.lease(
				workload, reason=reason, ttl_s=ttl_s, priority=priority,
				timeout_s=timeout_s, may_evict=may_evict, on_state=remember,
				# We convert loss into cancellation ourselves and re-raise LeaseLost from
				# there, so the library's own exit-time raise would only be a duplicate.
				raise_if_lost=False,
			) as held:
				seen['lease_id'] = held.lease_id
				card = Card(workload=workload, model_tag=tag, endpoint=held.endpoint,
				            leased=True, held=held, lease=held.lease)

				def on_lost() -> None:
					if not seen['lost']:
						seen['lost'] = True
						card.lost.set()
						log.error('lease %s lost — cancelling the run', held.lease_id[:8])
					# Not guarded: the watcher re-fires because one cancel can be
					# swallowed, and cancelling a finished task is a harmless no-op.
					task.cancel()

				try:
					if assertable:
						await _run_assertions(card, tag, num_ctx, exact_residency)

					watcher = _LostWatcher(held.lost_event, loop, on_lost)
					watcher.start()
					yield card

					# The body returned normally — but it may have done so by swallowing
					# every cancellation the watcher sent, in which case everything after
					# the revocation ran against a model warden had already unloaded. A
					# silent success is the worst available outcome, so say it out loud.
					# (warden's own `raise_if_lost` is off because we raise from the
					# cancellation path instead; this is the other half of it.)
					if held.lost or seen['lost']:
						raise LeaseLost(
							f'lease {held.lease_id} for {workload} was lost while it was '
							f'held, and the run finished anyway — it swallowed the '
							f'cancellation, so its last steps ran against an unloaded model',
							state='revoked', lease_id=held.lease_id,
						)
				finally:
					if watcher is not None:
						watcher.stop()

	except asyncio.CancelledError:
		# Order matters. Stop the watcher first, or its repeat-cancel re-marks this task
		# as cancelling and interrupts the release that is still unwinding.
		if watcher is not None:
			watcher.stop()
		if not (seen['lost'] or seen['signal']):
			# Somebody else cancelled us. Leave the cancellation intact and let it
			# propagate: rewriting it would destroy their signal, and `asyncio.run` turns
			# an untouched CancelledError back into the KeyboardInterrupt it came from.
			raise
		# Ours. Clear it so anything still unwinding can await, then say what happened.
		task.uncancel()
		if seen['lost']:
			raise LeaseLost(
				f'lease {seen["lease_id"]} for {workload} was revoked while it was held; '
				f'the run was cancelled rather than left talking to an unloaded model',
				state='revoked', lease_id=seen['lease_id'],
			) from None
		raise Interrupted(
			f'{seen["signal"]} during {workload}; the lease was released',
			signal=seen['signal'],
		) from None


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
