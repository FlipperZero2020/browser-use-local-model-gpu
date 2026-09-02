#!/usr/bin/env python3
"""Phase 0's gate, from PLAN.md §5. No GPU, no browser, no network.

    venv/bin/python tools/phase0_gate.py

Three checks, verbatim from the plan:

  * `version('browser-use')` -> `0.13.8`
  * `CONFIG.ANONYMIZED_TELEMETRY` is False and `'posthog' not in sys.modules` after
    importing `Agent` — checked as written, and then checked properly, because **as
    written it is a false pass**: measured 2026-09-01, posthog is absent from
    `sys.modules` after that import even with telemetry fully ON. The import is lazy and
    lives inside `ProductTelemetry.__init__`, so it happens at Agent *construction*.
  * the guard raises `TypeError` on a 0.9.7-era kwarg

Plus two the plan asks for elsewhere: the warden pin (§2) and the `/tmp` leak (§7).

The telemetry check runs in a **fresh subprocess**. Run in-process it would prove much
less: `posthog` could already be in `sys.modules` from anything, and the env block's
whole claim is about what happens on a cold `import browser_use`.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXPECTED_BROWSER_USE = '0.13.8'
EXPECTED_WARDEN_COMMIT = 'a252644aa5bdb17b611d9448e413ababe6fbaec7'  # == v0.3.0^{}

PASS, FAIL, NOTE = 'PASS', 'FAIL', 'NOTE'
results: list[tuple[str, str, str]] = []

TMP_GLOB = 'browser-use-user-data-dir-*'


def tmp_profiles() -> set[pathlib.Path]:
	return set(pathlib.Path('/tmp').glob(TMP_GLOB))


#: Taken before anything imports browser_use, so the check below can tell the dirs this
#: gate leaks itself (one per probe, by design — §7) apart from ones already piling up.
before_probe = tmp_profiles()


def record(name: str, ok: bool, detail: str) -> None:
	results.append((PASS if ok else FAIL, name, detail))


def note(name: str, detail: str) -> None:
	"""Reported, never gating. For things another process on this VM can dirty."""
	results.append((NOTE, name, detail))


# ── 1. the pin ───────────────────────────────────────────────────────────────
try:
	version = md.version('browser-use')
	record('browser-use is pinned at ' + EXPECTED_BROWSER_USE, version == EXPECTED_BROWSER_USE,
	       f'version(\'browser-use\') == {version!r}')
except md.PackageNotFoundError:
	record('browser-use is pinned at ' + EXPECTED_BROWSER_USE, False, 'not installed')

try:
	warden_v = md.version('warden')
	direct = md.distribution('warden').read_text('direct_url.json') or ''
	pinned = EXPECTED_WARDEN_COMMIT in direct
	record('warden is the v0.3.0 commit', pinned and warden_v == '0.3.0',
	       f'{warden_v} @ {EXPECTED_WARDEN_COMMIT[:12] if pinned else direct[:80]}')
except md.PackageNotFoundError:
	record('warden is the v0.3.0 commit', False, 'not installed')

# ── 2. zero cloud, in a cold process ─────────────────────────────────────────
PROBE = r'''
import json, sys
IMPORT_BROWSIN
from browser_use import Agent       # the import under test
from browser_use.config import CONFIG
from browser_use.telemetry.service import ProductTelemetry
after_import = 'posthog' in sys.modules          # the gate's literal wording
client = getattr(ProductTelemetry(), '_posthog_client', 'missing')   # where it is decided
json.dump({
    'ANONYMIZED_TELEMETRY': bool(CONFIG.ANONYMIZED_TELEMETRY),
    'BROWSER_USE_CLOUD_SYNC': bool(CONFIG.BROWSER_USE_CLOUD_SYNC),
    'BROWSER_USE_VERSION_CHECK': bool(CONFIG.BROWSER_USE_VERSION_CHECK),
    'posthog_after_import': after_import,
    'posthog_after_construction': 'posthog' in sys.modules,
    'posthog_client': None if client is None else type(client).__name__,
    'agent': Agent.__module__ + '.' + Agent.__name__,
}, sys.stdout)
'''


def run_probe(env: dict[str, str] | None, *, with_browsin: bool) -> dict:
	source = PROBE.replace(
		'IMPORT_BROWSIN',
		'import browsin  # applies the env block, imports no browser_use'
		if with_browsin else 'pass  # deliberately NOT applying the env block',
	)
	child = dict(os.environ)
	# The probe must not inherit this process's own zero-cloud state.
	for name in ('ANONYMIZED_TELEMETRY', 'BROWSER_USE_CLOUD_SYNC', 'BROWSER_USE_VERSION_CHECK',
	             'BROWSER_USE_DISABLE_EXTENSIONS', 'BROWSER_USE_CALCULATE_COST'):
		child.pop(name, None)
	child.update(env or {})
	proc = subprocess.run([sys.executable, '-c', source], capture_output=True, text=True,
	                      cwd=ROOT, env=child)
	if proc.returncode != 0:
		raise RuntimeError(f'probe exited {proc.returncode}: {proc.stderr.strip()[-400:]}')
	return json.loads(proc.stdout)


try:
	probe = run_probe(None, with_browsin=True)
except RuntimeError as err:
	record('zero-cloud after importing Agent', False, str(err))
	probe = {}
else:
	ok = (not probe['ANONYMIZED_TELEMETRY']
	      and not probe['BROWSER_USE_CLOUD_SYNC']
	      and not probe['BROWSER_USE_VERSION_CHECK']
	      and not probe['posthog_after_import'])
	record('zero-cloud after importing Agent', ok,
	       f'ANONYMIZED_TELEMETRY={probe["ANONYMIZED_TELEMETRY"]} '
	       f'CLOUD_SYNC={probe["BROWSER_USE_CLOUD_SYNC"]} '
	       f'VERSION_CHECK={probe["BROWSER_USE_VERSION_CHECK"]} '
	       f'posthog in sys.modules={probe["posthog_after_import"]} '
	       f'({probe["agent"]})')

	# The check the plan's wording was reaching for. posthog is imported inside
	# ProductTelemetry.__init__, so construction is where the decision lands.
	record('no PostHog client after ProductTelemetry() is constructed',
	       probe['posthog_client'] is None and not probe['posthog_after_construction'],
	       f'client={probe["posthog_client"]}, '
	       f'posthog in sys.modules={probe["posthog_after_construction"]}')

# A negative control, so the check above is known to have teeth: run the same probe with
# telemetry ON and confirm the plan's literal wording still passes while the real check
# fails. Without this the two checks are indistinguishable from a pair that always pass.
try:
	hot = run_probe({'ANONYMIZED_TELEMETRY': 'true'}, with_browsin=False)
except RuntimeError as err:
	record('the telemetry check discriminates', False, str(err))
else:
	record('the telemetry check discriminates',
	       not hot['posthog_after_import'] and hot['posthog_client'] == 'Posthog',
	       f'with telemetry ON: the plan\'s literal check still says '
	       f'posthog-in-sys.modules={hot["posthog_after_import"]} (a false pass), while '
	       f'construction yields a live {hot["posthog_client"]} client')

# ── 3. the **kwargs guard ────────────────────────────────────────────────────
from browsin.agent import check_agent_kwargs, valid_agent_kwargs  # noqa: E402

stale = {'planner_llm': None, 'validate_output': True, 'max_steps': 25, 'tool_calling_method': 'auto'}
try:
	check_agent_kwargs({'task': 'x', 'llm': None, **stale})
except TypeError as err:
	named = [name for name in stale if name in str(err)]
	record('the guard rejects 0.9.7-era kwargs', sorted(named) == sorted(stale),
	       'TypeError names ' + ', '.join(sorted(named)))
else:
	record('the guard rejects 0.9.7-era kwargs', False, 'no TypeError raised')

live = {'task': 'x', 'use_vision': True, 'use_judge': False, 'max_history_items': 8,
        'llm_timeout': 600, 'step_timeout': 900, 'max_actions_per_step': 2,
        'max_failures': 5, 'calculate_cost': False, 'enable_signal_handler': False}
try:
	check_agent_kwargs(live)
except TypeError as err:
	record('the guard passes every kwarg PLAN.md §4.3 uses', False, str(err).splitlines()[0])
else:
	record('the guard passes every kwarg PLAN.md §4.3 uses', True,
	       f'{len(live)} accepted, out of {len(valid_agent_kwargs())} real parameters')

# ── 4. the /tmp leak ─────────────────────────────────────────────────────────
# MEASURED 2026-09-01, correcting PLAN.md §7: importing browser_use does NOT leak one.
# `BrowserProfile()` leaves user_data_dir None, because pydantic does not run an `after`
# field validator on an *unset default* — the mkdtemp at profile.py:553 fires only when
# user_data_dir is explicitly passed as None. So this normally reports 0, and a nonzero
# number here means something in the gate started constructing profiles.
import shutil  # noqa: E402

mine = tmp_profiles() - before_probe
for path in mine:
	shutil.rmtree(path, ignore_errors=True)
stale = sorted(tmp_profiles())
# Reported, not gated. Any other process on this VM that imports browser_use adds one
# while this runs, so a hard check here would fail for reasons that are nothing to do
# with Phase 0. The sweep is a chore (§7), not an invariant.
note('/tmp profile leak (informational)',
     f'this run created {len(mine)} (expected 0 — import alone does not leak); '
     f'{len(stale)} pre-existing'
     + (' — tools/sweep_tmp.py --yes' if stale else ''))

# ── report ───────────────────────────────────────────────────────────────────
width = max(len(name) for _s, name, _d in results)
print()
for status, name, detail in results:
	print(f'  [{status}] {name.ljust(width)}   {detail}')
checks = [r for r in results if r[0] != NOTE]
failed = [name for status, name, _d in checks if status == FAIL]
print()
if failed:
	print(f'PHASE 0 GATE: FAILED — {len(failed)} of {len(checks)}: {", ".join(failed)}')
	raise SystemExit(1)
print(f'PHASE 0 GATE: PASSED — {len(checks)} of {len(checks)} checks')
