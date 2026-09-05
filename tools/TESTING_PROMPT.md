# Prompt: keep stress-testing browsin

Paste this into a fresh Claude Code session opened at
`~/Documents/claude/browser_use_local_model_GPU`. Works with any model; Opus will get
further per session than Sonnet on the open-ended parts.

---

Read `CLAUDE.md` and `PLAN.md` first — house convention here, not optional background.
Then read `tools/phase5_gate.py`, which is the test harness described below.

## What this is

`browsin` lets a local 7B vision model (`qwen2.5vl-32k:7b`) on a LAN GPU box drive a real
Chrome. GPU time is rationed by a broker (`warden`) and you take a **lease** — you never
start the model by hand.

## The measurement you are extending

`tools/phase5_gate.py` runs a table of browsing tasks N times each and scores every run
against ground truth **it fetches itself**, never against the agent's own `done` flag.

```bash
export WARDEN_URL=http://192.168.1.111:8130
export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
venv/bin/python -u tools/phase5_gate.py --reps 3                  # whole table
venv/bin/python -u tools/phase5_gate.py --reps 4 --only hn-top-story   # one task
venv/bin/python tools/test_phase5_grade_offline.py                # scorer control, no GPU
```

**Baseline measured 2026-09-05 — beat this or explain why it moved:**

| task | rate | note |
|---|---|---|
| `wiki-itn-lead` | 3/3 | static read, 1 step |
| `wiki-scroll-deep` | 3/3 | ~12 steps, slow but reliable |
| `wiki-absent-section` | 3/3 | correctly admits absence, no fabrication |
| `hn-15th-story` | 2/3 | |
| `wiki-search-box` | 1/3 | both failures ran out of budget mid-task |
| `hn-top-story` | 1/3 | 1 confirmed *had-then-lost* |
| **overall** | **13/18 = 72%** | |

## Rules that make a result mean something

- **Never grade on the agent's own `done`/`success` flag.** Constrained decoding guarantees
  well-formed output whether or not the model understood the page. The documented failure
  mode is *confident nonsense* — fluent, schema-valid, wrong.
- **Never hand-type an expected answer.** Derive it at run time (`Task.expect`). A fixture
  that has rotted must report `FIXTURE-STALE`, never be scored as a model failure. This has
  already burned one round of testing: a task was graded against a fact that lived on a
  different Wikipedia article than the one being browsed.
- **A gate that has never failed has not passed.** After touching the scorer, run
  `tools/test_phase5_grade_offline.py` (13 checks, no GPU) and make sure it still fails on
  the cases it is supposed to fail on.
- **A single run is not a distribution.** Anything below ~3 reps tells you nothing about a
  7B model. Hand-running six tasks once suggested 83%; the batch said 72%.
- **Restart Chrome between runs** (the harness does this via `B.stop()` / `B.start(url)`).
  `tools/browse.py`'s attach path adopts whatever tab is already open on the same host, so a
  previous run's leftover tab silently contaminates the next one. Still unfixed.
- **Anything holding a lease runs in background or with a long timeout.** A 2-minute tool
  timeout SIGTERMs the holder and strands the card.

## Adding a task

Append to `TASKS` in `tools/phase5_gate.py`:

```python
Task(
    name='some-name',
    url='https://...',
    prompt='What the model is told. End with "Then call done."',
    expect=...,          # callable -> list[str] that must appear in the final answer
    max_steps=8,
    read_only=True,      # prompt forbids clicking/typing, so count those as wasted
    absent=False,        # True = correct answer is admitting the thing is not there
    forbid=[],           # for absent tasks: real things it must not substitute instead
)
```

Ground-truth helpers already there: `wikipedia_itn_lead()`, `hn_story(n)`,
`wikipedia_contains(page, [...])` (stable fact, still verified live), `nothing_to_find()`.

## Worth attacking, roughly in order of value

1. **`had_then_lost`** — the model puts the correct answer in `model_output.memory`, takes
   an unnecessary action, then replaces it with something real-but-wrong and calls done. The
   scorer detects it. Nothing prevents it. The strongest fix is probably *enforcement rather
   than persuasion*: once a step's `memory` states an answer, reject any subsequent
   non-`done` action in the agent loop. Note the repo already enforces at this level —
   `DEFAULT_EXCLUDED_ACTIONS` removes dangerous actions from the registry outright — so
   there is precedent for doing it in code instead of in the prompt.
2. **`wiki-search-box` at 1/3** — it types the query fine, then burns its whole budget
   re-clicking a search control it has already used, never noticing the results page loaded.
   Determine whether it is purely a step-budget problem or a "doesn't notice the page
   changed" problem; they need different fixes.
3. **Prompt instructions get ignored.** The scroll `pages=3-5` guidance in
   `browsin/agent.py` has never once been followed — the model used the 1.0 default in every
   observed run. Do not add more prompt text without measuring whether it changes behaviour;
   assume it does nothing until a before/after says otherwise.
4. **The stale-tab bug in `tools/browse.py`** (attach path ignores `--url`). Real, reproduced
   twice, still open. The harness works around it; `browse.py` itself does not.
5. **More sites.** Everything reliable so far is Wikipedia. Both weak scores are the two
   non-Wikipedia tasks, which is either a real generalisation gap or too small a sample —
   currently indistinguishable. Add 2-3 more sites with runtime-derived truth and find out.

## How to report

Update `PLAN.md`'s status log with **measured** numbers and paste the gate's real output —
never "expected" numbers, never a summary in place of the output. Then regenerate and
republish the artifact:

```bash
python3 tools/build_artifact.py
# republish to the SAME url so the owner's link keeps working:
# https://claude.ai/code/artifact/8f877eff-3915-4231-b27d-0a9e4526fefa
```
