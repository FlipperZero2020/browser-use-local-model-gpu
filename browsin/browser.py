"""Start the owner's Chrome with a debug port, or attach to one that is already up.

`browsin` never lets browser-use launch a browser. Passing `cdp_url` to `BrowserSession`
gates the whole launch block behind `if not self.cdp_url:` (session.py:794), which is also
why the agent structurally cannot kill the browser: `LocalBrowserWatchdog._subprocess` is
only ever set on the launch path, and every termination site requires it
(local_browser_watchdog.py:65-71, 85-87). Nothing in browser-use sends CDP `Browser.close`
at all. So the process lifetime belongs entirely to this module.

Stdlib only, matching `browsin.lease`'s dependency floor.

Five things here exist because reading the library turned up something the plan assumed
away. Each is cheap; none is optional.

1. **Chrome refuses to open the debug port on the default user-data-dir.** The binary
   carries the string `DevTools remote debugging requires a non-default data directory.`,
   and browser-use's own launcher asserts the same thing. So `~/.config/google-chrome` is
   out — it opens a window with no port, quietly. The exact predicate is *not* verified;
   the first launch is the measurement. Hence `verify()` reads the journal.

2. **The port probe must come before the process scan.** Chromium forwards
   `--user-data-dir` to its renderer/GPU/zygote children, which share both the exe and the
   comm name with the browser process — so a naive scan matches N processes of which only
   one carries `--remote-debugging-port`, and the caller lands in the "running but no debug
   port, restart it" dead end on a Chrome whose port is live. Children are excluded by
   `--type=`. And `pgrep -f` is never used: it matches the detector's own argv.

3. **The initial tab must be a real http(s) page.** On connect, browser-use navigates every
   new-tab-page target to `about:blank` and mutates its cached URL (session.py:1926-1943);
   the `TabCreatedEvent` that follows then matches `about:blank` exactly, so
   `AboutBlankWatchdog` paints a full-viewport black overlay over the owner's tab, rewrites
   `document.title`, and — from the *browser*, not from Python — fetches
   `https://cf.browser-use.com/logo.svg` (aboutblank_watchdog.py:180). Launching straight
   onto the target URL means no new-tab page exists, so none of that fires. The
   `--host-resolver-rules` flag below is the belt to that pair of braces.

4. **A hostname-less tab gets closed.** `SecurityWatchdog` closes tabs whose URL has no
   hostname at connect (`file://`, `view-source:`, `about:<non-blank>`), and
   `_close_extension_options_pages()` closes extension option pages after *every*
   navigation. Chrome exits with its last tab. `preflight()` refuses rather than discover
   this on the owner's browser.

5. **The debug port's bind address cannot be set, only measured.** There is no
   `--remote-debugging-address` in this build. `assert_loopback()` is the first real
   measurement of it on this machine; a `0.0.0.0` bind would expose the owner's logged-in
   browser to the LAN, to every docker bridge on this VM, and to the PIA tunnel interface.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: browser-use's own vestigial constant (`profile.py:28`), kept because PLAN.md §4.4
#: already documents it and because 9222 collides with every other CDP tool on a machine.
CDP_PORT = 9242

CHROME = '/usr/bin/google-chrome'
CHROME_REAL_EXE = '/opt/google/chrome/chrome'

#: The owner's choice, 2026-09-04: the browser-use working copy, not the daily driver.
#: Non-default, so the debug port is allowed to open at all (see the module docstring).
DEFAULT_PROFILE = Path.home() / '.config/browseruse/profiles/chrome-default'

#: The systemd --user unit name. A named unit gives the process its own cgroup, a journal,
#: and a handle to stop it by — none of which `setsid nohup` provides.
UNIT = 'browsin-chrome'

#: The one outbound call browser-use makes that no environment variable can reach, because
#: the browser issues it rather than Python. Blocked before DNS. `*.browser-use.com` covers
#: api./cloud./llm.api. for free if a version bump starts using them.
#:
#: This is browser-wide for this Chrome instance's lifetime: browser-use.com is unreachable
#: in this profile while browsin's Chrome is up. For an automation profile that is not a
#: cost, but it is not scoped and pretending otherwise would be dishonest.
BLOCKED_HOSTS = ('cf.browser-use.com', '*.browser-use.com', 'browser-use.com')
HOST_RESOLVER_RULES = ','.join(f'MAP {h} ~NOTFOUND' for h in BLOCKED_HOSTS)

FLAGS = (
	'--no-first-run',
	'--no-default-browser-check',
	# Any hard stop leaves exit_type != Normal, and the next launch then covers the page
	# the agent is trying to read with a restore bubble.
	'--hide-crash-restore-bubble',
)

#: A tab whose URL matches any of these is one browser-use will navigate away from or close
#: outright. Refusing is better than finding out on the owner's browser.
_NEW_TAB = ('about:blank', 'chrome://new-tab-page/', 'chrome://new-tab-page',
            'chrome://newtab/', 'chrome://newtab')
_EXTENSION_PAGES = ('options.html', 'welcome.html', 'onboarding.html')


class ChromeError(RuntimeError):
	"""Chrome is not in a state browsin is willing to drive."""


class NotLoopback(ChromeError):
	"""The debug port is reachable from somewhere other than this machine."""


@dataclass
class Chrome:
	"""A live Chrome with a CDP port, and the evidence that it is safe to attach to."""

	port: int
	pid: int
	version: dict
	targets: list[dict]
	launched_by_us: bool
	bind: str = ''
	journal: list[str] = field(default_factory=list)

	@property
	def cdp_url(self) -> str:
		return f'http://127.0.0.1:{self.port}'

	def page_targets(self) -> list[dict]:
		return [t for t in self.targets if t.get('type') == 'page']


# ── probing ────────────────────────────────────────────────────────────────────────────

def _get(url: str, timeout_s: float = 5.0):
	# `no_proxy` explicitly: browser-use's own /json/version fetch uses
	# `trust_env=not is_localhost`, and this VM has proxy variables in some shells.
	opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
	with opener.open(url, timeout=timeout_s) as r:
		return json.loads(r.read().decode())


def probe(port: int = CDP_PORT, *, timeout_s: float = 5.0) -> dict | None:
	"""`/json/version`, or None if nothing answers. The authoritative liveness test."""
	try:
		return _get(f'http://127.0.0.1:{port}/json/version', timeout_s)
	except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
		return None


def targets(port: int = CDP_PORT, *, timeout_s: float = 5.0) -> list[dict]:
	return _get(f'http://127.0.0.1:{port}/json/list', timeout_s)


def listening(port: int = CDP_PORT) -> list[tuple[str, int]]:
	"""Every listening socket on `port`, as (bind address, pid). Parsed, not grepped."""
	out = subprocess.run(['ss', '-ltnpH', f'sport = :{port}'],
	                     capture_output=True, text=True).stdout
	found = []
	for line in out.splitlines():
		if not line.strip():
			continue
		# Local Address:Port is the 4th whitespace field of `ss -H` output.
		fields = line.split()
		if len(fields) < 4:
			continue
		addr = fields[3].rsplit(':', 1)[0]
		m = re.search(r'pid=(\d+)', line)
		found.append((addr, int(m.group(1)) if m else 0))
	return found


def assert_loopback(port: int = CDP_PORT) -> tuple[str, int]:
	"""Exactly one listener, on 127.0.0.1, owned by a chrome carrying our port flag.

	There is no flag that sets the bind address in this Chrome build, so this cannot be
	configured — only measured. A `0.0.0.0` bind hands full control of a logged-in browser
	to anything that can route here, with no authentication of any kind.
	"""
	socks = listening(port)
	if len(socks) != 1:
		raise NotLoopback(f'expected exactly one listener on {port}, found {socks!r}')
	addr, pid = socks[0]
	if addr not in ('127.0.0.1', '[::1]'):
		raise NotLoopback(
			f'the debug port is bound to {addr}, not loopback. Stop Chrome now: this '
			f'exposes a logged-in browser to the LAN (192.168.1.0/24), to every docker '
			f'bridge on this VM, and to the PIA tunnel interface. No flag fixes it.'
		)
	if pid:
		cmdline = _cmdline(pid)
		if not _has_flag(cmdline, f'--remote-debugging-port={port}'):
			raise NotLoopback(
				f'pid {pid} holds port {port} but its command line does not carry '
				f'--remote-debugging-port={port}; that is not our Chrome'
			)
	return addr, pid


def _cmdline(pid: int) -> str:
	"""`/proc/<pid>/cmdline` as one whitespace-joined string.

	Measured 2026-09-04, and it is not what the obvious code assumes: Chrome's **browser**
	process rewrites its own `/proc/self/cmdline` into a single NUL-free string when it sets
	its process title, so the usual `split('\\0')` yields *one* element holding the entire
	command line. Its children keep proper NUL separation. A token-membership test
	(`'--remote-debugging-port=9242' in args`) therefore returns False for the exact process
	that owns the port, and True for the renderers — precisely inverted.

	Both callers here want a containment test, so this normalises to a string and
	`_has_flag` does the boundary work.
	"""
	try:
		raw = Path(f'/proc/{pid}/cmdline').read_bytes()
	except OSError:
		return ''
	return ' '.join(a for a in raw.decode(errors='replace').split('\0') if a)


def _has_flag(cmdline: str, flag: str) -> bool:
	"""True if `flag` appears as a whole argument — start of string or after a space.

	Not a bare `in`: `--user-data-dir=/x/chrome` must not match a profile at
	`/x/chrome-default`, and `--type=` must not match `--utility-sub-type=`.
	"""
	return re.search(rf'(?:^|\s){re.escape(flag)}(?:\s|$|=)', cmdline) is not None


def running_on_profile(profile: Path, port: int = CDP_PORT) -> list[tuple[int, str | None]]:
	"""Browser processes (not renderers) already using `profile`, with their debug port.

	Only consulted when the port probe has already come back empty — a live port is proof
	enough on its own, and scanning first is how you end up telling the owner to restart a
	Chrome that was working.
	"""
	found = []
	out = subprocess.run(['pgrep', '-x', 'chrome'], capture_output=True, text=True).stdout
	for pid_s in out.split():
		pid = int(pid_s)
		try:
			exe = os.path.realpath(f'/proc/{pid}/exe')
		except OSError:
			continue
		if exe != CHROME_REAL_EXE:
			continue
		args = _cmdline(pid)
		# Renderer/GPU/zygote children inherit --user-data-dir from the browser process.
		if _has_flag(args, '--type'):
			continue
		if not _has_flag(args, f'--user-data-dir={profile}'):
			continue
		m = re.search(r'(?:^|\s)--remote-debugging-port=(\d+)', args)
		found.append((pid, m.group(1) if m else None))
	return found


# ── the tab safety check ───────────────────────────────────────────────────────────────

def preflight(page_targets: list[dict]) -> None:
	"""Refuse to attach if any open tab is one browser-use would navigate away or close.

	Chrome exits with its last tab, so a closure is not merely rude — it can end the
	session mid-run and take the lease's whole reason for existing with it.
	"""
	problems = []
	for t in page_targets:
		url = (t.get('url') or '').strip()
		low = url.lower()
		if url in _NEW_TAB or low.startswith('about:blank'):
			problems.append(
				f'{url!r} is a new-tab/about:blank page — browser-use paints a black '
				f'overlay over it, retitles it, and makes Chrome fetch cf.browser-use.com'
			)
		elif low.startswith(('file://', 'view-source:', 'about:', 'data:')):
			problems.append(f'{url!r} has no hostname — SecurityWatchdog closes it at connect')
		elif low.startswith('chrome-extension://') and any(p in low for p in _EXTENSION_PAGES):
			problems.append(f'{url!r} is an extension page — closed after every navigation')
	if problems:
		raise ChromeError(
			'refusing to attach; these open tabs would be altered or closed:\n  '
			+ '\n  '.join(problems)
			+ '\nClose them, or start a Chrome of our own with browsin.browser.start().'
		)


# ── launching ──────────────────────────────────────────────────────────────────────────

def _have_systemd_run() -> bool:
	return bool(shutil.which('systemd-run')) and bool(os.environ.get('XDG_RUNTIME_DIR'))


def journal(unit: str = UNIT, lines: int = 40) -> list[str]:
	"""Chrome's own answer about the port, which is the only place it is ever stated.

	`DevTools listening on ws://…` is success; `Cannot start http server for devtools.`
	means the port is taken; the non-default-data-dir refusal appears here or nowhere.
	"""
	out = subprocess.run(
		['journalctl', '--user', '-u', unit, '-b', '--no-pager', '-n', str(lines)],
		capture_output=True, text=True).stdout
	return [ln for ln in out.splitlines() if ln.strip()]


def start(
	url: str,
	*,
	profile: Path = DEFAULT_PROFILE,
	port: int = CDP_PORT,
	unit: str = UNIT,
	block_browser_use_cdn: bool = True,
	wait_s: float = 30.0,
) -> Chrome:
	"""Launch a headed Chrome on `profile`, showing `url`, with the debug port open.

	`url` is required and must be a real http(s) page: launching onto a new-tab page is
	what arms the overlay-and-CDN-fetch chain described in the module docstring.

	The process is put in its own systemd --user unit so it outlives the shell that started
	it — a browser the owner is meant to watch must not die when a tool call returns.
	"""
	if not url.lower().startswith(('http://', 'https://')):
		raise ChromeError(f'initial url must be http(s), got {url!r} — see the module docstring')
	if probe(port) is not None:
		raise ChromeError(f'something is already answering CDP on {port}; attach instead')
	if profile == Path.home() / '.config/google-chrome':
		raise ChromeError(
			'that is the default user-data-dir; Chrome refuses to open the debug port on it '
			'("DevTools remote debugging requires a non-default data directory") and will '
			'open a window with no port, quietly. Use the browseruse profile or a scratch dir.'
		)
	already = running_on_profile(profile, port)
	if already:
		raise ChromeError(
			f'Chrome is already running on {profile} (pid(s) '
			f'{", ".join(str(p) for p, _ in already)}) with no usable debug port. The flag is '
			f'read at startup only, so it cannot be enabled on a running instance — quit that '
			f'Chrome and try again. Chrome on other profiles can stay open; the singleton lock '
			f'lives inside the profile.'
		)
	profile.mkdir(parents=True, exist_ok=True)

	argv = [CHROME, f'--user-data-dir={profile}', f'--remote-debugging-port={port}', *FLAGS]
	if block_browser_use_cdn:
		argv.append(f'--host-resolver-rules={HOST_RESOLVER_RULES}')
	argv.append(url)

	if _have_systemd_run():
		subprocess.run(['systemctl', '--user', 'reset-failed', unit],
		               capture_output=True, text=True)
		launch = ['systemd-run', '--user', f'--unit={unit}',
		          f'--setenv=DISPLAY={os.environ.get("DISPLAY", ":0.0")}', *argv]
		r = subprocess.run(launch, capture_output=True, text=True)
		if r.returncode != 0:
			raise ChromeError(f'systemd-run failed: {r.stderr.strip()}')
	else:
		# Fallback: escape the tool shell's process group so a returning tool call does not
		# take the browser with it. Never a bare `nohup ... &`.
		subprocess.Popen(['setsid', 'nohup', *argv],
		                 stdin=subprocess.DEVNULL,
		                 stdout=open('/tmp/browsin-chrome.log', 'ab'),
		                 stderr=subprocess.STDOUT,
		                 start_new_session=True)

	deadline = time.monotonic() + wait_s
	version = None
	while time.monotonic() < deadline:
		version = probe(port, timeout_s=2.0)
		if version is not None:
			break
		time.sleep(0.4)
	log = journal(unit) if _have_systemd_run() else []
	if version is None:
		raise ChromeError(
			f'Chrome did not open a CDP port on {port} within {wait_s:.0f}s.\n'
			+ ('journal:\n  ' + '\n  '.join(log[-15:]) if log else 'no journal available')
		)

	bind, pid = assert_loopback(port)
	tabs = targets(port)
	return Chrome(port=port, pid=pid, version=version, targets=tabs,
	              launched_by_us=True, bind=bind, journal=log)


def attach(port: int = CDP_PORT) -> Chrome:
	"""Adopt a Chrome that is already listening, after proving it is safe to drive."""
	version = probe(port)
	if version is None:
		raise ChromeError(f'nothing is answering CDP on 127.0.0.1:{port}')
	bind, pid = assert_loopback(port)
	tabs = targets(port)
	chrome = Chrome(port=port, pid=pid, version=version, targets=tabs,
	                launched_by_us=False, bind=bind)
	preflight(chrome.page_targets())
	return chrome


def stop(unit: str = UNIT) -> None:
	"""Stop the Chrome we started. Never `pkill -f` — that pattern matches its own shell."""
	if _have_systemd_run():
		subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, text=True)


def lan_refuses(port: int = CDP_PORT, host: str = '192.168.1.127', timeout_s: float = 3.0) -> bool:
	"""True if the debug port is NOT reachable at this VM's LAN address.

	The negative half of the loopback proof. `assert_loopback` reads what the kernel says;
	this tries it. Neither is sufficient alone — `ss` could be misparsed, and a refused
	connect could be a firewall rather than a bind.
	"""
	import socket
	try:
		with socket.create_connection((host, port), timeout=timeout_s):
			return False
	except OSError:
		return True
