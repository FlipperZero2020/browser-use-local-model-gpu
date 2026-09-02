"""The zero-cloud environment, applied on import — and it must be applied *first*.

Import this (or `browsin`, whose `__init__` does it for you) **before**
`import browser_use`. Two of the four settings below are read at browser-use's own
import time, not lazily:

* `browser/profile.py:21` reads `BROWSER_USE_DISABLE_EXTENSIONS` inside the default
  factory for `enable_default_extensions`, and
* `browser/session.py:66` evaluates `DEFAULT_BROWSER_PROFILE = BrowserProfile()` at
  module level, which runs that factory.

So a `load_dotenv()` after the import is too late for at least one of them.

**This is not hygiene.** Unmodified, browser-use's telemetry is ON by default and ships
the literal task string, every URL visited, the full action history, the final answer
text and judge reasoning to `eu.i.posthog.com`, with exception autocapture. The headline
goal of this project is zero cloud API calls; left alone it makes three different kinds.

Applied only where the operator has not spoken: someone who deliberately exports
`ANONYMIZED_TELEMETRY=true` gets what they asked for. But `os.environ.setdefault` is the
wrong tool for that, because **an empty string is not "unset" to browser-use** — its
parser is `os.getenv(name, default).lower()[:1] in 'ty1'`, and `'' in 'ty1'` is True in
Python, so a blank value reads as *enabled*. Worse, `FlatEnvConfig` is a pydantic
`BaseSettings` that cannot parse `''` as a bool, so a blank `ANONYMIZED_TELEMETRY` makes
`import browser_use` raise a `ValidationError` outright. Blank is treated as unset here.

`assert_zero_cloud()` is what the Phase 0 gate calls to prove the result, whichever way
it was reached — and it constructs `ProductTelemetry`, because that is where the posthog
decision is actually made.
"""

from __future__ import annotations

import os

#: name → value, in the order they are applied. The comment on each is the call it stops.
ZERO_CLOUD: dict[str, str] = {
	# PostHog product analytics. `browser_use/telemetry/service.py` reads
	# `CONFIG.ANONYMIZED_TELEMETRY`, which is a lazy property over this var.
	'ANONYMIZED_TELEMETRY': 'false',
	# api.browser-use.com session sync. Its own default is `str(ANONYMIZED_TELEMETRY)`,
	# so this is belt and braces — and it stays correct if the line above is overridden.
	'BROWSER_USE_CLOUD_SYNC': 'false',
	# The pypi.org GET in `agent/service.py:2041`.
	'BROWSER_USE_VERSION_CHECK': 'false',
	# The clients2.google.com CRX fetch for uBlock / ClearURLs / cookie extensions.
	# Read at import time — see the module docstring.
	'BROWSER_USE_DISABLE_EXTENSIONS': '1',
	# The raw.githubusercontent.com model-pricing fetch in `tokens/service.py:57`. The
	# Agent's own `calculate_cost` defaults to False, so this only matters if something
	# constructs a token service directly — but it is one more name that turns into a GET.
	'BROWSER_USE_CALCULATE_COST': 'false',
}


def apply() -> dict[str, str]:
	"""Apply the block. Returns what each name is set to afterwards, applied or not."""
	for name, value in ZERO_CLOUD.items():
		if not os.environ.get(name, '').strip():
			os.environ[name] = value
	return {name: os.environ[name] for name in ZERO_CLOUD}


def assert_zero_cloud() -> dict[str, object]:
	"""Prove it from browser-use's own `CONFIG`, not from `os.environ`.

	Reading the env back would only prove that `apply()` ran. What matters is what
	browser-use *concluded*, so this asks `CONFIG` — which is where every consumer in
	the library reads from — and raises `AssertionError` naming the offender.

	And it goes one step further than reading config, because **`'posthog' not in
	sys.modules` after `import Agent` proves nothing**: measured 2026-09-01, posthog is
	absent from `sys.modules` after the import even with telemetry fully on. The import
	is lazy and lives inside `ProductTelemetry.__init__`, so it happens at *Agent
	construction*. This constructs `ProductTelemetry` — a process-wide singleton whose
	posthog decision is frozen the first time anything builds an Agent, Tools or Registry
	— and checks the client it ended up with.

	Imports `browser_use` as a side effect, so call it after `apply()`.
	"""
	import sys

	from browser_use.config import CONFIG
	from browser_use.telemetry.service import ProductTelemetry

	observed: dict[str, object] = {
		'ANONYMIZED_TELEMETRY': CONFIG.ANONYMIZED_TELEMETRY,
		'BROWSER_USE_CLOUD_SYNC': CONFIG.BROWSER_USE_CLOUD_SYNC,
		'BROWSER_USE_VERSION_CHECK': CONFIG.BROWSER_USE_VERSION_CHECK,
	}
	hot = [name for name, value in observed.items() if value]

	telemetry = ProductTelemetry()
	client = getattr(telemetry, '_posthog_client', 'missing')
	observed['posthog_client'] = type(client).__name__ if client is not None else None
	observed['posthog_imported'] = 'posthog' in sys.modules
	if client is not None or observed['posthog_imported']:
		hot.append('a live PostHog client')

	if hot:
		raise AssertionError(
			f'browser-use still has {", ".join(sorted(hot))} enabled. '
			f'`browsin.env` must be imported before `browser_use`, and before anything '
			f'constructs an Agent — ProductTelemetry is a singleton and decides once. '
			f'Observed: {observed}'
		)
	return observed


apply()
