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
	'browser_context': 'gone with Playwright; pass browser_session=BrowserSession(cdp_url=...)',
	'page': 'gone with Playwright',
}

#: Names that are still *valid* but are deprecated aliases, so the guard cannot help:
#: passing one is accepted. `browser` and `controller` raise a ValidationError if their
#: modern half is also given (service.py:290), which is the good case; `skills` does not.
ALIASES: dict[str, str] = {
	'browser': 'browser_session',
	'controller': 'tools',
	'skills': 'skills',
}

#: 2026-09-05 measurement: on a read-only task, `qwen2.5vl-32k:7b` correctly identifies the
#: answer in step 1's own `memory` field every time (3/3 clean trials) — the vision read is
#: not the problem. But left to its own defaults it then spends 6-7 more steps retyping that
#: same answer into unrelated `input` fields (once drifting to a different article entirely
#: via a stray `click`) before finally calling `done` — 3/3 correct in the end regardless,
#: but 8 steps and ~70s instead of 1 step and ~10s. This one line fixed it: 3/3 trials dropped
#: to exactly 1 step each, same or better answer. Not a model-capability gap; a missing
#: instruction. See PLAN.md.
#: 2026-09-05, same session — a second, independent bug found while testing a scroll-heavy
#: task: the model's own `memory` field said "scrolled down" on 7 consecutive steps, but 6 of
#: those 7 actually sent `scroll(down=False)` — scroll UP — confirmed by the tool's own printed
#: effect ("Scrolled up 742px"), not a logging artifact. The schema is not ambiguous
#: ("down=True=scroll down, down=False scroll up", default true) — this is the model's own
#: semantic mix-up. One added paragraph fixed the direction: retested on the same task, 10 of
#: 10 scrolls used the correct `down=True`. Merged into the same message as
#: `DONE_PROMPTLY_MESSAGE` below rather than a second `extend_system_message` call, since only
#: one is ever passed to `Agent`. See PLAN.md, 2026-09-05.
DONE_PROMPTLY_MESSAGE = (
	"If the answer to the task is already visible on the screen, call the `done` action "
	"immediately with that answer. Do not call `input`, `click`, or any other action first "
	"to double-check or confirm something you can already read. Only interact with the page "
	"if the task explicitly requires clicking or typing to reach the answer.\n\n"
	"When you need to read further into a page than what is currently visible, use the "
	"`scroll` action with down=true. down=true moves you further into the page toward "
	"content you have not seen yet; down=false moves you backward toward content you "
	"already passed. Before each scroll, check whether the screenshot actually changed "
	"from the previous step — if it looks identical, you scrolled the wrong direction or "
	"hit the end of the page, and should try the opposite direction or increase pages.\n\n"
	"When searching for something that is not yet visible and could be far down a long "
	"page, use a large pages value (3 to 5) per scroll to cover ground quickly, rather "
	"than the default 1.0 — one page at a time is far too slow to reach content that is "
	"many screens away. Only switch to smaller scrolls (0.5 to 1.0) once you can see you "
	"are getting close to the target, so you do not overshoot past it."
)
#: 2026-09-05 caveat on the paragraph above: the one passing run measured so far never
#: actually sent `pages>1.0` — it stuck to the 1.0 default throughout and still reached a
#: target ~12.8k px down a real page (measured via a live DOM query) in 7 scrolls, likely
#: because the real (wide) Chrome viewport reflows to a shorter page than the 1280px-wide
#: measurement used to estimate that figure. The instruction is kept because it is harmless
#: if ignored and plausibly helps on a page too long for even a correct-direction, right-sized
#: scroll to reach in a normal step budget — but it has not yet been the deciding factor in
#: any observed run. Do not cite it as a confirmed fix; re-test if scroll-speed problems
#: persist on a page long enough to force the model to actually raise `pages`.

#: The overrides PLAN.md §4.3 argues for, with the library default each one replaces.
#: Not applied automatically by `checked_agent` — it only validates — but `build_agent`
#: below applies them, so this is the one home for the reasoning.
#:
#: Six of these were added on 2026-09-04 after reading the installed library rather than
#: the plan. Each defaults ON and each spends the leased card.
PLAN_DEFAULTS: dict[str, Any] = {
	'use_vision': True,  # library default True; the point of leasing a VL model
	'extend_system_message': DONE_PROMPTLY_MESSAGE,  # 2026-09-05 finding, see above
	'use_judge': False,  # library default True = one MORE /api/chat after `done`,
	#                      carrying up to ten screenshots — very likely the largest prompt
	#                      of the whole run, on the leased card, and absent from history.
	'max_history_items': 8,  # library default None = unbounded context growth
	'llm_timeout': 600,  # library default None resolves to 75 s for an ollama name.
	#                      NOTE: this budget covers the empty-action RETRY as well — both
	#                      calls sit inside one `asyncio.wait_for` — so it must fit two
	#                      full prefills, not one. The plan had this backwards.
	'step_timeout': 900,  # library default 180. Must exceed llm_timeout + DOM build +
	#                      action execution; it is the outer guillotine, not a 2x multiple.
	'max_actions_per_step': 1,  # library default 5. One, not two: at two, the gate cannot
	#                      tell an invented element index from a DOM that legitimately
	#                      changed under the second action.
	'max_failures': 5,  # library default 5, restated because it is load-bearing: the run
	#                      only stops at max_failures + final_response_after_failure = 6
	#                      consecutive failures, each allowed a full llm_timeout.
	'calculate_cost': False,  # library default False; usage is zeros on this path anyway
	'enable_signal_handler': False,  # library default True — see the note below
	'message_compaction': False,  # library default True. Its LLM defaults through to the
	#                      leased model, it is untimed, it runs before the step's budgeted
	#                      call, and it prepends up to 6 000 chars to every later prompt.
	'enable_planning': False,  # library default True. Adds two branches to the grammar
	#                      (including an unbounded array of strings) and, from step 5,
	#                      appends a nudge message to every step.
	'loop_detection_enabled': False,  # library default True. Serialises the whole DOM a
	#                      SECOND time each step just to hash it, on this VM's CPU, inside
	#                      the leased window.
	'directly_open_url': False,  # library default True. It scans the task for a URL and
	#                      injects a navigate as a synthetic step 0 — which would make the
	#                      Phase 4 gate's "the model moved the tab" condition unfalsifiable.
}

#: Actions removed from the registry before the model ever sees them.
#:
#: This is not tidiness. Every action's parameter model is inlined into the `format=`
#: JSON schema on *every* request, so the list is also a prompt-size lever — but the
#: reason each of these is here is specific:
#:
#: * `extract`      — two hardcoded 120 s calls to the same leased model that `llm_timeout`
#:                    cannot reach, and a 100 000-char page-markdown cap that is the
#:                    realistic way one request overflows `num_ctx`. `search_page` does the
#:                    same job with no LLM call.
#: * `close`        — Chrome exits with its last tab. The model must not hold that lever
#:                    over the owner's browser.
#: * `evaluate`     — arbitrary JavaScript in a browser carrying the owner's live cookies.
#:                    PLAN.md §7 names prompt injection while holding real logins as the
#:                    top risk in this project; this is its shortest path.
#: * the file actions — `read_file` / `write_file` / `replace_file` / `upload_file` /
#:                    `save_as_pdf` give a model that reads attacker-controlled page text a
#:                    filesystem and an upload button. §7: never give this agent a file tool.
DEFAULT_EXCLUDED_ACTIONS: tuple[str, ...] = (
	'extract', 'close', 'evaluate',
	'read_file', 'write_file', 'replace_file', 'upload_file', 'save_as_pdf',
)

#: Substrings that change the Agent's behaviour purely from the model's *name*, silently.
#: `'deepseek' in model.lower()` sets `use_vision = False` at construction with only a
#: warning — so a vision model whose tag contained it would be leased, measured, and never
#: sent a single image. `grok-3` / `grok-code` do the same.
VISION_KILLING_SUBSTRINGS = ('deepseek', 'grok-3', 'grok-code')


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

	Two caveats this guard cannot cover, both about names that are *valid* but whose
	meaning moved:

	* `enable_signal_handler` defaults to True and installs browser-use's own SIGINT
	  handler, which would fight `browsin.lease`'s. Pass it False from anything that
	  holds a lease — `PLAN_DEFAULTS` does.
	* The deprecated aliases in `ALIASES` are in the signature, so passing `browser=` or
	  `controller=` is accepted here and only browser-use's own validator objects, and
	  only when both halves are given.
	"""
	check_agent_kwargs(kwargs)
	return Agent(**kwargs)


# ── Phase 4: the three constructions, each with its own trap ───────────────────────────

def build_llm(*, host: str, model: str, num_ctx: int, connect_timeout_s: float = 10.0):
	"""`ChatOllama` pointed at `host`, with `num_ctx` on the wire and the traps asserted.

	Three of these asserts exist because the failure they catch is *silent*:

	* **The port is not optional.** ollama's client treats a bare `http://host` as port 80,
	  so a missing `:11434` does not error — it connects somewhere else, or nowhere.
	* **The model name can turn vision off by itself** (`VISION_KILLING_SUBSTRINGS`).
	* **`num_ctx` is one number in two places.** The same constant must reach
	  `browsin.lease.hold(num_ctx=…)`, which reads `/api/ps` and refuses to start on a
	  mismatch. Nothing in browser-use validates it: `Agent._verify_and_setup_llm` has an
	  empty body and contacts nothing, so a wrong host or tag first shows up as a
	  fabricated `502` on step 1 — after the lease is taken and Chrome is up.

	A plain dict rather than `ollama.Options`: a typo in the `Options` form is dropped by
	`model_dump(exclude_none=True)` and sends *no options at all*, which would leave the
	card serving its default window while warden's book says otherwise. A dict typo at
	least reaches the wire, where `/api/ps` can catch it.
	"""
	import httpx

	from browser_use import ChatOllama

	if ':11434' not in host:
		raise ValueError(
			f'host={host!r} carries no port; ollama silently reads that as port 80 '
			f'(_client.py:1374). Pass an explicit :11434.'
		)
	low = model.lower()
	bad = [s for s in VISION_KILLING_SUBSTRINGS if s in low]
	if bad:
		raise ValueError(
			f'model tag {model!r} contains {bad}, which makes browser-use set '
			f'use_vision=False at construction with only a warning. The lease would pay '
			f'for a vision model that is never sent an image.'
		)
	return ChatOllama(
		model=model,
		host=host,
		ollama_options={'num_ctx': num_ctx},
		# httpx-level only, and NOT the same thing as `llm_timeout`. Left unbounded for
		# read/write so the proxy and `llm_timeout` are the only clocks that matter.
		timeout=httpx.Timeout(None, connect=connect_timeout_s),
	)


def build_session(*, cdp_url: str, downloads_path: str):
	"""`BrowserSession` attached over CDP, passing nothing that would arm a launch path.

	Deliberately absent, each for a measured reason:

	* `executable_path` / `channel` — either one flips `is_local=True`, which is the single
	  flag that arms every browser-killing path in `LocalBrowserWatchdog`.
	* `user_data_dir` — a path containing the substring `chrome` triggers a 718 MB
	  one-way `copytree` of the real profile, and the profile this project attaches to is
	  literally named `chrome-default`.
	* `allowed_domains` / `prohibited_domains` — with either set, `SecurityWatchdog` closes
	  the owner's pre-existing tabs at connect, and Chrome exits with its last tab.

	`downloads_path` IS passed: left unset, browser-use points Chrome's download directory
	at a temp dir via CDP `Browser.setDownloadBehavior`, and whether Chrome puts it back
	when the client disconnects is unverified.
	"""
	from browser_use import BrowserSession

	return BrowserSession(
		cdp_url=cdp_url,
		downloads_path=downloads_path,
		# Do not grant clipboard-read and friends on the owner's real browser.
		permissions=[],
	)


def build_tools(exclude: tuple[str, ...] = DEFAULT_EXCLUDED_ACTIONS):
	"""`Tools` with `exclude` removed — and an error if any name was not actually there.

	`Registry.exclude_action` appends an unknown name and only logs, at DEBUG, when it
	really deletes something. So a typo (or a name from a different browser-use version —
	`extract_structured_data` is the one that nearly landed here) is a **silent no-op**,
	and the caller believes it removed a hazard it never touched. Verify against the live
	registry instead of trusting the call.
	"""
	from browser_use import Tools

	available = set(Tools().registry.registry.actions)
	missing = sorted(set(exclude) - available)
	if missing:
		raise ValueError(
			f'these actions do not exist in this browser-use, so excluding them would be a '
			f'silent no-op: {missing}. Registered: {sorted(available)}'
		)
	tools = Tools(exclude_actions=list(exclude))
	left = set(tools.registry.registry.actions)
	still = sorted(set(exclude) & left)
	if still:
		raise ValueError(f'exclusion did not take for {still}')
	return tools


def build_agent(*, task: str, llm, browser_session, tools=None, **overrides: Any):
	"""`PLAN_DEFAULTS`, then the caller's overrides, through `checked_agent`.

	`checked_agent` catches a name `Agent.__init__` would swallow. It cannot catch a name
	that is still *valid* but whose meaning moved, which is why `PLAN_DEFAULTS` is applied
	here rather than left to each caller to remember.
	"""
	kwargs: dict[str, Any] = dict(PLAN_DEFAULTS)
	kwargs.update(overrides)
	kwargs.update(task=task, llm=llm, browser_session=browser_session,
	              tools=tools if tools is not None else build_tools())
	return checked_agent(**kwargs)
