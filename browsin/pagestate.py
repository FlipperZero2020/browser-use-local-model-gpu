"""Read — and, for the cleanup path only, click — the live driven tab over raw CDP.

This module answers questions that no HTTP fetch can: *what does the page look like right
now, in the browser that is signed in*. `browsin.grade`'s three truth sources
(`wikipedia_itn_lead`, `hn_story`, `wikipedia_contains`) all fetch the page again over
plain HTTP, which is correct for anonymous read-only tasks and structurally incapable of
answering "did the Like land" — the like state exists only inside a session-authenticated
render.

**It deliberately does not import `browser_use`.** Same reasoning as `lease.py`,
`browser.py` and `proxy.py`: that import costs seconds, mints a `/tmp` directory family and
decides the telemetry singleton, and two of this module's callers must run *before* the
lease is taken (the login precheck) or with no agent in the picture at all (the cleanup
safety net). It uses `websockets` — already in the venv as a browser-use transitive
dependency — rather than the stdlib, because CDP has no non-websocket transport. That is a
weaker guarantee than `browser.py`'s stdlib-only floor and a deliberate one; the rule that
matters is the `browser_use` exclusion, not asceticism.

Connecting a second CDP client to a target browser-use is already driving is fine: CDP
multiplexes clients per target, and everything here is `Runtime.evaluate` against a target
we located by URL rather than adopted.

## The measurements this module is built on (2026-09-05, this machine, not assumed)

Probed the owner's signed-in `chrome-default` profile on `x.com/OpenAI` over CDP :9242,
and the same URL signed out:

| | signed in | signed out |
|---|---|---|
| `[data-testid]` total | **186** | **0** |
| `article[data-testid="tweet"]` | 7 | 0 (5 bare `<article>` preview cards) |
| `[data-testid="like"]` | 7 | 0 |
| `[data-testid="unlike"]` | **0** | 0 |
| `[data-testid="SideNav_AccountSwitcher_Button"]` | 1 | 0 |
| `body.innerText` length | 2353 | 618 |
| "Log in"/"Sign up"/"Continue to X" present | no | yes |

Four consequences, each load-bearing somewhere below:

1. **Like and unlike are one button with a flipping `data-testid`.** A post that is *not*
   liked carries `like`; a post that *is* liked carries `unlike`. So the count of
   `[data-testid="unlike"]` is the number of liked posts currently rendered — a directly
   observable integer, not an inference from the agent's narration.
2. **The baseline on this account is zero.** Nothing on that page was liked, so an `unlike`
   present after a like phase was created by us. That is what makes the verdict unambiguous
   and the cleanup safe: we are never guessing whether a like was the owner's.
3. **Signed out, the page carries no `data-testid` at all.** Not a different value — none.
   So `totalTestIds == 0` on an `x.com` URL *is* the login wall, and it is checkable in
   ~200 ms before anything expensive happens.
4. **The signed-out page reaches `DOMContentLoaded` at 3,464 ms**, and that is the *light*
   preview; the signed-in timeline is heavier. The run that provoked this module died 8.3 s
   after start having screenshotted the boot splash. A fixed short sleep would not have
   covered it and a longer one would waste the lease on every fast page — hence `wait_ready`
   polls a content predicate instead of sleeping.

`X_*` constants below are therefore *measured selectors*, and `probe_testids()` exists so a
run reports what it actually found rather than grading against a selector X may have
changed. If `article[data-testid="tweet"]` ever reads 0 on a page that visibly has posts,
that is the first thing to look at.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_CDP = 'http://127.0.0.1:9242'

#: How long a single `Runtime.evaluate` may take. Generous: the page may be mid-layout.
EVAL_TIMEOUT_S = 20.0

# ── the JS, kept as named constants so a selector change is a one-line diff ─────────────

#: Everything `wait_ready` and the verdict need, in one round trip. Returns a JSON *string*
#: (not an object) because `returnByValue` on a deep object is a serialisation minefield and
#: a string always survives it intact.
_SNAPSHOT_JS = r"""
(() => {
  const q = s => document.querySelectorAll(s).length;
  const text = (document.body && document.body.innerText) || '';
  return JSON.stringify({
    url: location.href,
    ready_state: document.readyState,
    text_len: text.length,
    total_testids: q('[data-testid]'),
    // generic readiness: something a person could actually act on
    interactive: q('a[href], button, input, textarea, select, [role="button"], [role="link"]'),
    // x.com specifics (measured 2026-09-05)
    tweets: q('article[data-testid="tweet"]'),
    like: q('[data-testid="like"]'),
    unlike: q('[data-testid="unlike"]'),
    account_switcher: q('[data-testid="SideNav_AccountSwitcher_Button"]'),
    primary_column: q('[data-testid="primaryColumn"]'),
    login_words: /Log in|Sign up|Continue to X|Sign in/.test(text.slice(0, 4000))
  });
})()
"""

#: The census `probe_testids` reports. Not used for grading — used so a wrong selector
#: announces itself instead of silently reading zero.
_TESTID_CENSUS_JS = r"""
(() => {
  const ids = {};
  for (const e of document.querySelectorAll('[data-testid]')) {
    const k = e.getAttribute('data-testid');
    ids[k] = (ids[k] || 0) + 1;
  }
  return JSON.stringify(Object.entries(ids).sort((a, b) => b[1] - a[1]).slice(0, 40));
})()
"""

#: The cleanup. Clicks at most `limit` currently-liked posts back to unliked.
#:
#: This is the ONLY place in this project where the harness itself clicks something, and it
#: is deliberately not available to the model: `evaluate` is in
#: `agent.DEFAULT_EXCLUDED_ACTIONS` precisely so page text can never reach a JS execution
#: path. The harness running a fixed, non-parameterised expression it authored is a
#: different thing from handing the model an arbitrary-JS tool, and this expression
#: interpolates nothing from the page or the task.
#:
#: It targets `[data-testid="unlike"]` and nothing else, so the worst case of a stale
#: selector is that it clicks nothing — never that it likes something. That asymmetry is
#: intentional: the failure mode of the safety net must not be "created more likes".
_UNLIKE_JS = r"""
(() => {
  const btns = Array.from(document.querySelectorAll('[data-testid="unlike"]'));
  const n = Math.min(btns.length, %d);
  let clicked = 0;
  for (let i = 0; i < n; i++) {
    try { btns[i].click(); clicked++; } catch (e) { /* keep going */ }
  }
  return JSON.stringify({attempted: n, clicked: clicked, found: btns.length});
})()
"""


#: The aria-label is what browser-use actually shows the model — its serialised element line is
#: `[1060]<button aria-label=2845 Likes. Like />`. The data-testid is invisible to the model. So
#: when a task has to NAME its target for a 7B that measurably obeys concrete strings better than
#: behavioural prose, this is the string that matters, and guessing it is how a run gets graded
#: against a selector nobody checked. Reported every run for that reason.
_ARIA_JS = r"""
(() => {
  const pick = sel => Array.from(document.querySelectorAll(sel))
    .slice(0, 4)
    .map(e => (e.getAttribute('aria-label') || '(none)'));
  return JSON.stringify({like: pick('[data-testid="like"]'), unlike: pick('[data-testid="unlike"]')});
})()
"""


class PageStateError(RuntimeError):
	"""The tab could not be read. Never silently a zero count."""


@dataclass
class Snapshot:
	"""One read of the live tab. Every field measured, none inferred."""

	url: str = ''
	ready_state: str = ''
	text_len: int = 0
	total_testids: int = 0
	interactive: int = 0
	tweets: int = 0
	like: int = 0
	unlike: int = 0
	account_switcher: int = 0
	primary_column: int = 0
	login_words: bool = False
	at: float = 0.0

	@property
	def liked(self) -> int:
		"""Posts currently liked and rendered. The number the verdict is built on."""
		return self.unlike

	@property
	def looks_logged_in(self) -> bool:
		"""True only on positive evidence. `total_testids == 0` is the signed-out shape."""
		return self.account_switcher > 0 or (self.total_testids > 0 and not self.login_words)

	def as_dict(self) -> dict:
		return {k: getattr(self, k) for k in (
			'url', 'ready_state', 'text_len', 'total_testids', 'interactive', 'tweets',
			'like', 'unlike', 'account_switcher', 'primary_column', 'login_words', 'at')}


@dataclass
class Readiness:
	"""What `wait_ready` waited for, how long, and whether it got it.

	`ok=False` is not an exception on purpose. A run that starts against a page that never
	rendered is still worth measuring — it is just not a *model* failure, and the evidence
	has to say so or the next reader will charge it to the model exactly as happened on
	2026-09-05. `tools/test.py` records this dict and `browsin.diagnose` reads it.
	"""

	ok: bool = False
	waited_s: float = 0.0
	polls: int = 0
	reason: str = ''
	require: str = ''
	first: dict = field(default_factory=dict)
	last: dict = field(default_factory=dict)

	def as_dict(self) -> dict:
		return {'ok': self.ok, 'waited_s': round(self.waited_s, 2), 'polls': self.polls,
		        'reason': self.reason, 'require': self.require,
		        'first': self.first, 'last': self.last}


# ── CDP plumbing ───────────────────────────────────────────────────────────────────────

def _targets(cdp_url: str, timeout_s: float = 5.0) -> list[dict]:
	# ProxyHandler({}) explicitly: this VM has proxy variables in some shells and a
	# proxied request to 127.0.0.1 fails in a way that reads as "Chrome is down".
	opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
	try:
		with opener.open(f'{cdp_url.rstrip("/")}/json/list', timeout=timeout_s) as r:
			return json.loads(r.read().decode())
	except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
		raise PageStateError(f'cannot list CDP targets at {cdp_url}: {exc!r}') from exc


def pick_target(cdp_url: str = DEFAULT_CDP, *, host: str = '') -> dict:
	"""The page target to read, chosen by URL host — never by index.

	browser-use's own `page_targets[0]` is dict-insertion order of concurrently-attached
	targets, which has already been an extension tab once on this profile (§7). Selecting
	by host is the same discipline `tools/test.py` applies when it dispatches `SwitchTabEvent`.
	"""
	pages = [t for t in _targets(cdp_url) if t.get('type') == 'page']
	if not pages:
		raise PageStateError(f'no page targets at {cdp_url}')
	if host:
		match = [t for t in pages if host in (t.get('url') or '')]
		if match:
			return match[0]
		raise PageStateError(
			f'no tab on {host!r}; tabs={[(t.get("url") or "")[:60] for t in pages]}')
	return pages[0]


async def evaluate(expression: str, *, cdp_url: str = DEFAULT_CDP, host: str = '',
                   timeout_s: float = EVAL_TIMEOUT_S) -> str:
	"""`Runtime.evaluate` on the chosen tab, returning the expression's string value.

	Every expression here returns `JSON.stringify(...)`, so the transported value is always
	a string. `returnByValue` on a deep object goes through CDP's own serialiser and loses
	shape in ways that are tedious to debug; a string does not.
	"""
	import websockets  # local: keeps `import browsin.pagestate` cheap for callers that only

	#                    want the dataclasses (e.g. `test.py self-check`).

	target = pick_target(cdp_url, host=host)
	ws_url = target.get('webSocketDebuggerUrl')
	if not ws_url:
		raise PageStateError(f'target {target.get("id")} exposes no webSocketDebuggerUrl')

	async def _run() -> str:
		async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
			await ws.send(json.dumps({'id': 1, 'method': 'Runtime.enable'}))
			await ws.recv()
			await ws.send(json.dumps({
				'id': 2, 'method': 'Runtime.evaluate',
				'params': {'expression': expression, 'returnByValue': True,
				           'awaitPromise': True},
			}))
			while True:
				msg = json.loads(await ws.recv())
				if msg.get('id') != 2:
					continue          # an unsolicited Runtime event; not our reply
				if 'error' in msg:
					raise PageStateError(f'CDP error: {msg["error"]}')
				res = msg.get('result', {})
				if res.get('exceptionDetails'):
					raise PageStateError(f'JS threw: {res["exceptionDetails"]}')
				val = res.get('result', {})
				if 'value' not in val:
					raise PageStateError(f'no value in CDP reply: {json.dumps(msg)[:400]}')
				return val['value']

	try:
		return await asyncio.wait_for(_run(), timeout=timeout_s)
	except asyncio.TimeoutError as exc:
		raise PageStateError(f'Runtime.evaluate timed out after {timeout_s}s') from exc
	except PageStateError:
		raise
	except Exception as exc:                       # websocket/transport failures
		raise PageStateError(f'CDP evaluate failed: {type(exc).__name__}: {exc}') from exc


# ── the three things callers actually want ─────────────────────────────────────────────

async def snapshot(*, cdp_url: str = DEFAULT_CDP, host: str = '') -> Snapshot:
	"""One measured read of the tab."""
	raw = await evaluate(_SNAPSHOT_JS, cdp_url=cdp_url, host=host)
	try:
		d = json.loads(raw)
	except json.JSONDecodeError as exc:
		raise PageStateError(f'snapshot did not return JSON: {raw[:200]!r}') from exc
	d['at'] = time.time()
	known = {f for f in Snapshot.__dataclass_fields__}
	return Snapshot(**{k: v for k, v in d.items() if k in known})


async def probe_testids(*, cdp_url: str = DEFAULT_CDP, host: str = '') -> list:
	"""The top data-testid census, so a stale selector announces itself."""
	raw = await evaluate(_TESTID_CENSUS_JS, cdp_url=cdp_url, host=host)
	try:
		return json.loads(raw)
	except json.JSONDecodeError:
		return []


#: Named readiness predicates. Each takes a `Snapshot` and returns True when the page is
#: worth showing a model. Keep them *positive* — "content exists" — never "not blank",
#: because the boot splash is a perfectly valid non-blank DOM and would satisfy a negative.
REQUIRE = {
	# x.com: at least one real post container. The splash has zero, the login wall has zero.
	'x-timeline': lambda s: s.tweets >= 1,
	# generic: a page a person could act on. `interactive` floors out the HN case, where the
	# screenshot was fully painted but the DOM build returned two elements.
	'content': lambda s: s.interactive >= 5 and s.text_len >= 200,
	# a very weak floor, for pages that are legitimately sparse
	'any-text': lambda s: s.text_len >= 50,
}


async def wait_ready(*, cdp_url: str = DEFAULT_CDP, host: str = '', require: str = 'content',
                     timeout_s: float = 30.0, poll_s: float = 0.5) -> Readiness:
	"""Poll until `require` is satisfied, or the timeout elapses. Never raises on timeout.

	The failure this exists for, measured 2026-09-05: a fresh Chrome launched onto
	`x.com/OpenAI`, browser-use captured its first state ~1 s later, and the model was shown
	the X boot splash. It correctly reported an empty page and called `done(success=False)`
	at step 1 of 14, in 8.3 s. Nothing about that was a model failure.

	Two things this is deliberately NOT:

    * **Not `browser_profile.minimum_wait_page_load_time`.** That field's own description
      reads "Minimum time to wait before capturing page state" and it is **never read** in
      browser-use 0.13.8 — five occurrences in the installed package, all declarations
      (`profile.py:680`, `session.py:185/219/325`, `beta/service.py:953`), zero read sites.
      Same for `wait_for_network_idle_page_load_time`. Setting either is a silent no-op.
    * **Not `initial_actions=[wait]`.** That writes a step-0 history item that
      `grade.steps()` counts as a real step, which would corrupt every step count in the
      table for a harness concern.

	On timeout it returns `ok=False` with the samples rather than raising, because a run
	against a page that never rendered is still worth measuring — it is just not the model's
	fault, and the evidence must be able to say which.
	"""
	pred = REQUIRE.get(require)
	if pred is None:
		raise PageStateError(f'unknown readiness predicate {require!r}; have {sorted(REQUIRE)}')

	t0 = time.monotonic()
	out = Readiness(require=require)
	last_exc = ''
	while time.monotonic() - t0 < timeout_s:
		out.polls += 1
		try:
			snap = await snapshot(cdp_url=cdp_url, host=host)
		except PageStateError as exc:
			# The tab may not exist yet, or be mid-navigation. Keep polling; only the
			# timeout is fatal, and even then only to `ok`.
			last_exc = f'{type(exc).__name__}: {exc}'
			await asyncio.sleep(poll_s)
			continue
		if not out.first:
			out.first = snap.as_dict()
		out.last = snap.as_dict()
		if pred(snap):
			out.ok = True
			out.waited_s = time.monotonic() - t0
			out.reason = f'{require} satisfied after {out.polls} poll(s)'
			return out
		await asyncio.sleep(poll_s)

	out.waited_s = time.monotonic() - t0
	out.reason = (f'{require} NOT satisfied within {timeout_s:.0f}s'
	              + (f'; last error {last_exc}' if last_exc else '')
	              + (f'; last={out.last}' if out.last else '; the tab was never readable'))
	return out


async def aria_labels(*, cdp_url: str = DEFAULT_CDP, host: str = '') -> dict:
	"""What the like/unlike buttons are actually called, as the model sees them."""
	raw = await evaluate(_ARIA_JS, cdp_url=cdp_url, host=host)
	try:
		return json.loads(raw)
	except json.JSONDecodeError:
		return {'like': [], 'unlike': []}


async def unlike_up_to(limit: int, *, cdp_url: str = DEFAULT_CDP, host: str = '') -> dict:
	"""The safety net: click at most `limit` liked posts back to unliked. Returns evidence.

	Targets `[data-testid="unlike"]` only, so a stale selector clicks nothing rather than
	liking something. The caller is responsible for re-snapshotting to confirm — this
	returns what it *attempted*, and an attempt is not a measurement.
	"""
	if limit <= 0:
		return {'attempted': 0, 'clicked': 0, 'found': 0}
	raw = await evaluate(_UNLIKE_JS % int(limit), cdp_url=cdp_url, host=host)
	try:
		return json.loads(raw)
	except json.JSONDecodeError as exc:
		raise PageStateError(f'unlike did not return JSON: {raw[:200]!r}') from exc


#: Who is signed in, read from the avatar container's own data-testid
#: (`UserAvatar-Container-<handle>`). Needed because the authoritative like list lives at a
#: per-account URL and hardcoding a handle would silently verify the wrong account.
_HANDLE_JS = r"""
(() => {
  const e = document.querySelector('[data-testid^="UserAvatar-Container-"]');
  return e ? e.getAttribute('data-testid').replace('UserAvatar-Container-', '') : '';
})()
"""


async def account_handle(*, cdp_url: str = DEFAULT_CDP, host: str = '') -> str:
	"""The signed-in account's handle, or '' if it cannot be read."""
	try:
		return (await evaluate(_HANDLE_JS, cdp_url=cdp_url, host=host)) or ''
	except PageStateError:
		return ''


async def goto(url: str, *, cdp_url: str = DEFAULT_CDP, host: str = '',
               require: str = 'content', timeout_s: float = 30.0) -> Readiness:
	"""Navigate the driven tab and wait for it to be worth reading. Harness-only."""
	await evaluate(f'(()=>{{location.href={url!r}; return "nav"}})()',
	               cdp_url=cdp_url, host=host)
	await asyncio.sleep(2.0)          # let the SPA begin its route change before polling
	return await wait_ready(cdp_url=cdp_url, host=host, require=require, timeout_s=timeout_s)


async def liked_count(*, cdp_url: str = DEFAULT_CDP, host: str = '', handle: str = '') -> tuple:
	"""The AUTHORITATIVE number of posts this account has liked, from its own Likes page.

	Why this is not `snapshot().liked`. That counts `[data-testid="unlike"]` among the posts
	*currently rendered in the tab*, which is only ever a viewport of one timeline. Measured
	2026-09-05, and it is the reason this function exists: a run liked 2 posts on x.com/OpenAI,
	the model then navigated into the reply thread and liked more, and the profile-page count
	afterwards read 1 — while the account's Likes page held **5**. The harness reported
	"liked is now 0 (baseline 0)" and the account was not clean. A count that can silently miss
	what it is supposed to guard is worse than no count, because it is trusted.

	Returns (count, url_checked). Raises rather than returning 0 on failure: a zero that means
	"could not look" is exactly the failure this replaced.
	"""
	handle = handle or await account_handle(cdp_url=cdp_url, host=host)
	if not handle:
		raise PageStateError('cannot determine the signed-in handle, so the authoritative '
		                     'Likes page cannot be located; refusing to report a like count')
	url = f'https://x.com/{handle}/likes'
	r = await goto(url, cdp_url=cdp_url, host=host)
	snap = await snapshot(cdp_url=cdp_url, host=host)
	if not r.ok and snap.tweets == 0 and snap.total_testids == 0:
		raise PageStateError(f'{url} never rendered; {r.reason}')
	return snap.tweets, snap.url


async def unlike_everything(*, cdp_url: str = DEFAULT_CDP, host: str = '', handle: str = '',
                            keep: int = 0, max_rounds: int = 25) -> dict:
	"""Un-like from the account's Likes page until `keep` remain. One at a time, verified.

	One per round rather than a batch: un-liking removes the post from this list, so the page
	re-renders under the click and a batch would be clicking stale nodes. Slower and correct.
	"""
	handle = handle or await account_handle(cdp_url=cdp_url, host=host)
	if not handle:
		raise PageStateError('cannot determine the signed-in handle; refusing to click blind')
	await goto(f'https://x.com/{handle}/likes', cdp_url=cdp_url, host=host)
	removed = 0
	for _ in range(max_rounds):
		snap = await snapshot(cdp_url=cdp_url, host=host)
		if snap.unlike <= keep:
			break
		r = await unlike_up_to(1, cdp_url=cdp_url, host=host)
		removed += int(r.get('clicked') or 0)
		await asyncio.sleep(2.5)
	await goto(f'https://x.com/{handle}/likes', cdp_url=cdp_url, host=host)
	final = await snapshot(cdp_url=cdp_url, host=host)
	return {'removed': removed, 'remaining': final.tweets, 'handle': handle,
	        'url': f'https://x.com/{handle}/likes'}


async def assert_logged_in(*, cdp_url: str = DEFAULT_CDP, host: str = '',
                           settle_s: float = 12.0) -> Snapshot:
	"""Refuse early if the profile is signed out. Cheap, and runs BEFORE the lease.

	Worth its own function because the alternative — discovering it at step 1 — costs a
	full lease, a model load and ~15 minutes of the card. `settle_s` exists because the
	signed-out shape (`total_testids == 0`) is indistinguishable from *not loaded yet*: both
	are zero. So this waits for the app to boot before it is willing to call it a login wall.
	"""
	ready = await wait_ready(cdp_url=cdp_url, host=host, require='content',
	                         timeout_s=settle_s)
	snap = await snapshot(cdp_url=cdp_url, host=host)
	if not snap.looks_logged_in:
		raise PageStateError(
			f'that profile is signed out of {snap.url or host!r}: '
			f'data-testid count={snap.total_testids}, account_switcher={snap.account_switcher}, '
			f'login words on page={snap.login_words} (readiness: {ready.reason}). '
			f'Sign in by hand in this profile, quit Chrome fully, and re-run:\n'
			f'  google-chrome --user-data-dir=$HOME/.config/browseruse/profiles/chrome-default '
			f'https://x.com')
	return snap
