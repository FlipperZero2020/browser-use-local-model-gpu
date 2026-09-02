"""Constructing a browser-use `Agent` without silently doing half of nothing.

`Agent.__init__` in browser-use 0.13.8 ends in a `**kwargs` it never reads. Every
0.9.7-era parameter therefore constructs cleanly and has no effect at all: no
`TypeError`, no warning, no log line. That is what let a plan written for 0.9.7 keep
"working" against a 0.13.8 venv for weeks — PLAN.md §10, correction 1.

So nothing in this project calls `Agent(...)` directly. It calls `checked_agent(...)`,
which turns that class of mistake back into the exception it should always have been.

Importing this module imports `browser_use`, which is slow and leaks a
`/tmp/browser-use-user-data-dir-*`. `browsin.env` is imported first, as it must be.
"""

from __future__ import annotations

import inspect
from typing import Any

import browsin.env as _env  # noqa: F401  — the zero-cloud block, before browser_use

from browser_use import Agent

#: Parameters that existed in the 0.9.7 era and are gone in 0.13.8, mapped to what to do
#: instead. Only used to make the error message actionable; the guard itself is generic
#: and rejects any unknown name, not merely these.
RETIRED: dict[str, str] = {
	'planner_llm': 'planning is built in — see enable_planning / planning_replan_on_stall',
	'planner_interval': 'gone with planner_llm',
	'use_planner': 'gone with planner_llm',
	'validate_output': 'gone; use_judge is the nearest surviving idea',
	'max_steps': 'moved to Agent.run(max_steps=...), whose default is 500',
	'tool_calling_method': 'gone — the Ollama path uses format=<json schema>, never tools=',
	'message_context': 'renamed; see extend_system_message / override_system_message',
	'browser': 'gone with Playwright; pass browser_session=BrowserSession(cdp_url=...)',
	'browser_context': 'gone with Playwright; pass browser_session=...',
	'page': 'gone with Playwright',
}

#: The overrides PLAN.md §4.3 argues for, with the library default each one replaces.
#: Not applied automatically — a caller passes what it wants and this module only
#: validates — but kept here so the reasoning has one home. Phase 4 consumes it.
PLAN_DEFAULTS: dict[str, Any] = {
	'use_vision': True,  # library default True; the point of leasing a VL model
	'use_judge': False,  # library default True = one extra full LLM call per run
	'max_history_items': 8,  # library default None = unbounded context growth
	'llm_timeout': 600,  # library default None resolves to 75 s for an ollama name
	'step_timeout': 900,  # library default 180
	'max_actions_per_step': 2,  # library default 5
	'max_failures': 5,  # library default 5, restated because it is load-bearing
	'calculate_cost': False,  # library default False; usage is None on this path anyway
	'enable_signal_handler': False,  # library default True — see the note below
}


def valid_agent_kwargs() -> frozenset[str]:
	"""Every name `Agent.__init__` actually reads, from the live signature.

	Found by *kind*, not by name: `self` is dropped, and `**kwargs` / `*args` are dropped
	because they are the hole this guard exists to close, not parameters. Deriving it
	from the installed class rather than a hand-kept list is the whole point — a list
	would go stale in exactly the way that caused the problem.
	"""
	params = inspect.signature(Agent.__init__).parameters
	return frozenset(
		name
		for name, p in params.items()
		if name != 'self'
		and p.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
	)


def check_agent_kwargs(kwargs: dict[str, Any]) -> None:
	"""Raise `TypeError` if `Agent` would swallow any of these. Cheap; call it always."""
	bad = sorted(set(kwargs) - valid_agent_kwargs())
	if not bad:
		return
	lines = [
		f'Agent() does not accept {bad} in browser-use 0.13.8. '
		f'It would not raise — __init__ ends in a **kwargs it never reads, so these '
		f'would be accepted and silently ignored.'
	]
	lines += [f'  {name}: {RETIRED[name]}' for name in bad if name in RETIRED]
	raise TypeError('\n'.join(lines))


def checked_agent(**kwargs: Any) -> Agent:
	"""`Agent(**kwargs)`, but a typo or a stale parameter is an error, not a no-op.

	One caveat this guard cannot cover: a name that is *valid* but whose meaning moved.
	`enable_signal_handler` defaults to True and installs browser-use's own SIGINT
	handler, which would fight `browsin.lease`'s. Pass it False from anything that holds
	a lease — `PLAN_DEFAULTS` does.
	"""
	check_agent_kwargs(kwargs)
	return Agent(**kwargs)
