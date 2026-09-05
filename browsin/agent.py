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
	"Do not go looking for things by scrolling. If you need content that is not on screen, "
	"first call `search_page` with a distinctive word from what you want: it searches the "
	"whole page text instantly and for free, including the parts you cannot see. If it finds "
	"the text, call `find_text` with that text to jump straight there. If it does not find "
	"the text, the content is not on this page at all and scrolling will never reveal it — "
	"go back or try a different link.\n\n"
	"Use `scroll` only to read through a page in order. `pages=1` moves one screen, "
	"`pages=0.5` half a screen, and `pages=10` goes all the way to the end. If a scroll "
	"tells you the page did not move, you are at the end of the page: do not scroll that "
	"way again."
)
#: 2026-09-05, third revision of the scroll paragraph — the previous one was WRONG about the
#: library and inert in practice, and both halves are worth recording so it is not reinvented:
#:
#: * It told the model to "use a large pages value (3 to 5)". That is not browser-use's
#:   vocabulary. `ScrollAction.pages` is documented as '0.5=half page, 1=full page, 10=to
#:   bottom/top', and the scroll action's own description says "High pages (10) reaches
#:   bottom" — verified identical in the installed 0.13.8 and in upstream main on GitHub.
#:   3-5 was an invented middle ground; 10 is the documented "go to the end" value.
#: * It was inert anyway: across three separate runs the model never once sent `pages>1`
#:   (`scroll_pages_gt1` k=0 over n=5, n=23 and n=20 scrolls).
#: * Worse, it pushed *toward* scrolling, against browser-use's own system prompt, which
#:   already says "Prefer search_page over scrolling when looking for specific text content
#:   not visible in browser_state" (system_prompts/system_prompt.md:83).
#:
#: The replacement points at `search_page`/`find_text` first and states the real `pages`
#: semantics. Note what it does NOT try to do: it does not tell the model to stop repeating
#: itself. Three separate attempts to fix the repeat loop with words all failed (see
#: REPEAT_FEEDBACK_ENABLED below for the measurements), so that job belongs to the tools
#: layer, not to this string.

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
		# `num_predict` caps generation. §4.3 prescribed 1024 and the implementation dropped
		# it, so generation was unbounded — measured 2026-09-05 over 309 logged generations:
		# median 162 tokens, p90 206, p99 250, and a **max of 17,328**. Exactly 2 of the 309
		# exceeded 1024, and both were runaways: the model emitting a multi-thousand-line
		# malformed JSON object that never terminates. Uncapped, one of those occupies the
		# full `llm_timeout` (600 s) before anything reclaims the step, and §3.3's warning
		# applies — the request is abandoned but the GPU keeps generating. Capped, the same
		# event truncates into a parse failure that the existing retry handles in seconds.
		# 1024 is 4x the p99, so it cannot clip a legitimate step.
		ollama_options={'num_ctx': num_ctx, 'num_predict': 1024},
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


# ── Making a no-op action say so, in the result the model reads ───────────────────

#: 2026-09-05. Four runs on one task (ncbi myncbi, scroll-heavy) established that this model
#: repeats any action whose result reads as success, and that no amount of *advice* stops it:
#:
#: * browser-use's own system prompt already says it in five places — "NEVER repeat the same
#:   failing action more than 2-3 times" (system_prompt.md:250), "Prefer search_page over
#:   scrolling when looking for specific text content" (:83), "Detect and break out of
#:   unproductive loops" (:99), "If stuck in a loop ... change strategy" (:268). Ignored.
#: * `loop_detection_enabled=True` injects an escalating nudge as a context message. Measured:
#:   19 nudges in one run, escalating to repetition=20 / stagnation=22, and the model issued
#:   the identical `scroll` after every single one. Ignored 19/19.
#: * An `extend_system_message` stating the rule as a hard prohibition, plus pointing at
#:   `search_page`/`find_text`, moved the loop from `scroll` to `search_page` (n=4) and then
#:   back to `scroll` at pages=0.5, twenty times. Ignored.
#:
#: The common cause is not disobedience. Every one of those rules is conditioned on the model
#: recognising that an action *failed* — and `scroll` reports success unconditionally. It
#: returns "Scrolled down 742px" whether the page moved 742px or not one pixel, because it
#: never compares position before and after: it dispatches a ScrollEvent and then formats the
#: message from the *requested* amount (browser_use/tools/service.py, the scroll action). The
#: model is correctly obeying "do not repeat a failing action". It has never been told the
#: action failed.
#:
#: So the feedback belongs on the `ActionResult` the model reads, not in advice alongside it.
#: This wrapper adds two things and changes nothing else:
#:
#:   1. `scroll` that did not move the page says so, with the measured position.
#:   2. Any action repeated with identical arguments says so, with the count.
#:
#: It is deliberately at the tools layer rather than in the prompt, because the prompt layer
#: is exactly what has already been measured not to work three separate ways.
REPEAT_FEEDBACK_ENABLED = True

#: `done` is terminal and `screenshot` is legitimately repeatable; neither is wrapped.
_NO_FEEDBACK_ACTIONS = frozenset({'done', 'screenshot'})


async def _scroll_metrics(browser_session) -> tuple[int, int, int] | None:
	"""(scroll_y, content_height, viewport_height) in CSS px, or None if unavailable.

	Read from CDP `Page.getLayoutMetrics` rather than by evaluating JavaScript: the `evaluate`
	action is excluded from this agent on purpose (DEFAULT_EXCLUDED_ACTIONS) and reaching for
	Runtime.evaluate here would quietly reintroduce the same capability on the owner's live
	browser. getLayoutMetrics is also what the library's own scroll uses for viewport height.
	"""
	try:
		cdp = await browser_session.get_or_create_cdp_session()
		m = await cdp.cdp_client.send.Page.getLayoutMetrics(session_id=cdp.session_id)
	except Exception:
		return None
	vis = m.get('cssVisualViewport') or {}
	lay = m.get('cssLayoutViewport') or {}
	content = m.get('cssContentSize') or {}
	y = vis.get('pageY')
	if y is None:
		y = lay.get('pageY')
	height = vis.get('clientHeight') or lay.get('clientHeight')
	try:
		return (round(float(y or 0)), round(float(content.get('height') or 0)), round(float(height or 0)))
	except (TypeError, ValueError):
		return None


def _action_key(name: str, params) -> str:
	"""A stable identity for "the same action with the same arguments"."""
	body = ''
	try:
		if hasattr(params, 'model_dump'):
			body = repr(sorted(params.model_dump(exclude_none=True).items()))
		elif params is not None:
			body = repr(params)
	except Exception:
		body = repr(params)
	return f'{name}|{body}'


def _annotate(result, note: str):
	"""Prepend `note` to the fields the model actually reads back on the next step.

	Both fields, because which one survives into the next prompt depends on whether the action
	set `long_term_memory`: `extracted_content` is only promoted when `long_term_memory` is
	absent (see ActionResult's own comments). Prepended, not appended — the tail of a long
	action result is the easiest part for a small model to skim past.
	"""
	if result is None or getattr(result, 'error', None):
		return result  # a real error already tells the model something went wrong
	update = {}
	old_ltm = getattr(result, 'long_term_memory', None)
	if old_ltm:
		update['long_term_memory'] = f'{note} (What the tool reported: {old_ltm})'
	else:
		update['long_term_memory'] = note
	old_ec = getattr(result, 'extracted_content', None)
	if old_ec:
		update['extracted_content'] = f'{note}\n\n{old_ec}'
	try:
		return result.model_copy(update=update)
	except Exception:
		return result


def wrap_repeat_feedback(tools):
	"""Make every action report a no-op or a repeat *in its own result*. Returns `tools`.

	The registry calls actions as `action.function(params=validated, **special_context)` and
	normalises every function to keyword-only (`registry/service.py`), so a wrapper taking
	`(params=None, **kw)` is safe without preserving any signature — nothing introspects the
	function at call time.
	"""
	state: dict[str, Any] = {'key': None, 'streak': 0}
	actions = tools.registry.registry.actions

	for name, action in actions.items():
		if name in _NO_FEEDBACK_ACTIONS:
			continue
		action.function = _make_feedback_wrapper(name, action.function, state)
	return tools


def _make_feedback_wrapper(name: str, fn, state: dict):
	async def wrapped(params=None, **kw):
		session = kw.get('browser_session')
		before = await _scroll_metrics(session) if (name == 'scroll' and session is not None) else None

		result = await fn(params=params, **kw)

		notes: list[str] = []

		if before is not None:
			after = await _scroll_metrics(session)
			if after is not None and after[0] == before[0]:
				y, content_h, view_h = after
				at_bottom = content_h and (y + view_h) >= (content_h - 2)
				where = 'the BOTTOM' if at_bottom else ('the TOP' if y <= 0 else 'a scroll limit')
				notes.append(
					f'THE SCROLL DID NOTHING. The page did not move: it is still at {y}px of '
					f'{content_h}px total content. You are already at {where} of this page. '
					f'Scrolling in this direction again is guaranteed to do nothing at all. '
					f'Whatever you are looking for is NOT further in this direction, so stop '
					f'scrolling and do something else: use search_page to check whether the text '
					f'is on this page at all, click a different element, or call go_back.'
				)

		key = _action_key(name, params)
		if key == state['key']:
			state['streak'] += 1
			n = state['streak'] + 1
			if n >= 2:
				# Escalates at 3. Measured 2026-09-05 on the myncbi repro with click/input stripped:
				# the n>=2 wording alone moved the loop off `scroll` (10.5 -> 2.0 scrolls per run)
				# but the model then repeated `search_page` 9 times and only called `done` at the
				# budget ceiling, *having already established the correct answer*. Telling it to
				# stop is not enough; it has to be told that what it already knows is sufficient.
				tail = (
					'You MUST choose a different action now — a different tool, or different '
					'arguments.' if n < 3 else
					'STOP. You already have everything this page can tell you. Call `done` NOW '
					'and report what you found — including, if that is the answer, that the thing '
					'you were looking for is not on this page. Reporting an honest negative is a '
					'correct and complete answer; repeating this action is not.'
				)
				notes.append(
					f'REPEATED ACTION: you have now called `{name}` with exactly the same '
					f'arguments {n} times in a row and received the same result every time. '
					f'It is not working and it will not start working. {tail}'
				)
		else:
			state['key'] = key
			state['streak'] = 0

		if notes:
			result = _annotate(result, ' '.join(notes))
		return result

	return wrapped


def build_tools(exclude: tuple[str, ...] = DEFAULT_EXCLUDED_ACTIONS, repeat_feedback: bool | None = None):
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
	# `None` means "use the project default"; the `no-repeat-feedback` arm passes False so the
	# control and the fix can be interleaved inside one batch, under the same page state.
	if REPEAT_FEEDBACK_ENABLED if repeat_feedback is None else repeat_feedback:
		wrap_repeat_feedback(tools)
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
