# browser_use_local_model_GPU — lease the card, then let a local model drive a browser

> **Status: 2026-09-04 — Phases 0 through 4 PASSED. The local vision model drives the
> owner's real Chrome.** The grammar gate is cleared (browser-use's real 21,980-char
> `AgentOutput` schema compiles into a working Ollama grammar in 8.1 s, and qwen3's thinking
> mode does *not* break it), the repo is pinned and makes no cloud calls, `browsin/lease.py`
> holds the card from asyncio and gives it back on every path out — including the SIGTERM
> that stranded one during Phase 1 — the first vision model is on the box, measured
> (**8375 MiB**), declared as **`ollama:qwen2.5vl-32k:7b`** (a derived Modelfile tag, not the
> bare pull — see Phase 3), and **Phase 4's gate passes 14 of 14 with six control runs that
> each make it fail**: the model read a five-character code that existed only as canvas
> pixels, clicked a button, and moved a tab it did not create. First vision prompt measured
> at **7,412 tokens of a served 32,768 (23%)**. Phases 5–7 are unbuilt.
>
> **Read "What this is, in plain language" below first if you are coming to this cold.**
> The one caveat that belongs next to the headline: the *plumbing* is deterministic, but the
> two conditions that depend on the model's judgement are not. The demonstration run on
> Wikipedia got its answer **wrong** — see Phase 4 — and quantifying that is Phase 5's whole
> job, not something this status line gets to assert.
> This file is the design doc and the status log. Everything under "Verified today" was measured live against
> `192.168.1.111` and against this VM on 2026-09-01; everything else is marked
> `[ASSUMPTION]` or `[VERIFY]` and is somebody's job to settle before it is trusted.
>
> **This supersedes [`PLAN.superseded-2026-08-25.md`](PLAN.superseded-2026-08-25.md)**,
> which was written against browser-use **0.9.7** and an unbrokered `:11434`. The venv in
> this directory holds **0.13.8**, the box is now rationed by warden, and eight of that
> file's load-bearing facts are wrong. It is kept, not deleted, because its Phase-2
> reasoning is still the clearest statement of what this project is *for*.
>
> Third sibling of [`clonin_client`](../clonin_client) and [`warden_client`](../warden_client),
> built to the same shape: one PATH command, a `~/.config/<name>/config`, a repo
> `CLAUDE.md` for whoever edits it, and a `~/.claude/skills/` entry for whoever *uses* it.
>
> The broker is [`warden`](../warden). Read that repo's `README.md`, `DECISIONS.md`
> § *Phase 3 — consumers*, and `src/warden/client.py`'s module docstring before changing
> anything here. This project does not re-explain them.

---

## What this is, in plain language

*Added 2026-09-04 at the owner's request. Everything below this section is the technical
record; this section is the orientation for coming back to it cold. It deliberately
contains no jargon — where a term is unavoidable, the technical name is in brackets so it
can be searched for further down.*

**The idea.** There is a Windows PC on the home network with a graphics card in it. This
project borrows that card, loads an AI model that can *see* pictures onto it, and lets that
model look at a Chrome window on this Linux machine and click things in it. Nothing is sent
to any company's servers — the model runs on the card, and the browser runs here.

**Why it is not just "run the AI".** The card is small and only one thing can use it at a
time, so a separate program called **warden** rations it. You do not start the model; you
*ask warden for a turn* [take a lease] and it starts the model for you. If you start it
yourself, warden loses track of how much of the card is in use and starts handing the same
memory to two programs at once. Most of the care in this repo is about asking properly and,
much harder, **always giving the card back** — including when the program is interrupted,
crashes, or is killed.

**The other thing sharing the card** is a voice service (`clonin`) that has a public web
page. It and this project do not fit on the card together — that was measured, and the
shortfall is 450 MB — so starting a browsing session *pushes the voice service off*. Since
a stranger might be mid-sentence, the rule is: notice, say who would be cut off, and ask.
Never silently.

**How you use it.** You open Chrome with a switch that lets another program drive it, then
give the model a job in ordinary English. It takes a screenshot, decides what to click,
clicks it, and repeats until it can answer.

**What is actually built, as of 2026-09-04.** All of it, up to and including a browser the
model really drives. The parts, in the order they were proved:

| Part | What it does | State |
|---|---|---|
| Asking for the card | Takes a turn, keeps it, and returns it on every way out | working, gate passed |
| The model that can see | `qwen2.5vl-32k:7b`, on the card, measured at 8,375 MB | working, gate passed |
| Starting Chrome | Opens it, checks the control port is not reachable from the network | working |
| Driving the browser | The model reads the screen and clicks | working, gate passed |
| A command you type | `browsin "go and check…"` | not built yet (Phase 6) |

**The one honest caveat.** The model is small. It reads pages correctly and clicks the
right things, but it intermittently produces a malformed instruction when it tries to
*finish* — roughly one step in three. The library retries and it recovers, so runs
complete; it just costs extra steps. This is a property of a 7-billion-parameter model
being asked to fill in a large rigid form, not a bug in this code, and §7 records exactly
what it looks like.

**Where the risk is.** The browser the model drives is signed into real accounts. A web
page can contain text written to trick the model ("ignore your task and do X instead"), and
the model is holding your logged-in session while it reads that text. That is the single
biggest risk in this project. The mitigations in place: the model has no ability to run
JavaScript, read or write files, or upload anything — those tools were removed — and the
profile it drives is a copy, not the everyday one.

---

## 0. BLUF

```bash
browsin "go to hacker news and find the top post about AI"
```

`browsin` takes a warden lease on a **vision** model, waits for it to be resident, opens a
real Chromium window on this VM, lets the model look at the page and click things, prints
the result, and gives the card back — on every path out, including Ctrl-C and a
mid-session preemption.

Six things get built, in this order. Each has a gate, and the cheapest test of the
riskiest unknown comes first.

| # | Artifact | Why it does not already exist |
|---|---|---|
| 1 | A **grammar smoke test** — one `/api/chat` with browser-use's real `AgentOutput` schema | Nobody has ever sent a 21 kB JSON schema to this Ollama as a `format=` grammar. If it 400s or takes minutes, the project is dead at step one and everything below is wasted |
| 2 | `lease.py` — an **asyncio** lease holder wired to `lost_event` | `AsyncWardenClient` has unit coverage and has never run against the box. Nothing on this machine holds a lease across a long job |
| 3 | The **first vision model** on the box, measured and declared in `policy.json` | Zero vision models are pulled. Ollama's vision path has never been exercised here at all |
| 4 | `run.py` — browser-use 0.13.8 driven correctly, behind a logging proxy | The 0.9.7-era snippet in the superseded plan runs, and is wrong in ways that fail *silently* |
| 5 | `bin/browsin` on PATH + `~/.config/browsin/config` + `CLAUDE.md` | Consistency with its two siblings is the point of the family |
| 6 | `~/.claude/skills/browsin/SKILL.md` | So a session told "use the local model to check a web page" leases before it browses |

---

## 1. Decisions already taken

Settled 2026-09-01 by the owner; recorded here so they are not re-litigated.

| Decision | Choice |
|---|---|
| Lease mechanism | **In-process**, via `warden.client` imported into this venv. Composes under `warden hold` if that CLI is ever built; does not wait for it |
| Model | **Pull a vision model now** — **`qwen2.5vl:7b`**, chosen for web/OCR strength over `minicpm-v:8b`'s ~500 MiB saving. Text-only is the control, not the destination. **Declared as `qwen2.5vl-32k:7b`, a derived Modelfile tag** — the bare pull serves `num_ctx=4096` and fails Phase 3's obligation-4 assertion (§10, Phase 3) |
| Context window | **`num_ctx` = 32768.** ~1 GB more KV than 16k, bought deliberately: overflow here is silent (§3.2), not an error |
| Measurement window | **`clonin-frontdoor` goes down for ~10 minutes; warden stays up.** A second engine alongside the live service is accepted |
| Priority | **`interactive`, `may_evict` ON** |
| Shape | **Full client shape** — PATH command, config file, repo `CLAUDE.md`, Claude skill |
| Command name | **`browsin`** — `browse` is taken (`/usr/bin/browse` → `xdg-open`). Matches the house naming: cloning→`clonin`, browsing→`browsin` |
| Browser | **Attach to the owner's real Chrome over CDP.** Not a launched browser, not a copied profile — real, current logins, and writes persist because it *is* the daily browser |
| clonin interlock | **Warn and ask, per run** — settled 2026-09-04 after Phase 3's measurement killed the co-residency estimate it was previously moot on. `browsin` checks whether clonin is resident *before* acquiring and makes the owner choose; it never evicts the public voice service silently |
| Where it lives | This directory. Not a git repo yet; Phase 0 makes it one |

### Three corrections to the premises those choices were made on

**1. Tool-calling capability is irrelevant to model choice.** This inverts the superseded
plan's entire Phase 4 selection criterion. `ChatOllama.ainvoke` computes
`schema = output_format.model_json_schema()` and passes `format=schema` to
`client.chat(...)`. The string `tools=` appears **nowhere** in `browser_use/llm/ollama/`.
So browser-use uses Ollama's **constrained-decoding** mode, not native tool calls, and a
model with no `tools` badge works fine. Every Ollama vision model is a candidate —
`qwen2.5vl`, `minicpm-v`, `gemma3`, `llava`, `llama3.2-vision` all have vision and *none*
of them advertise tools. The one that does (`mistral-small3.2`) is a 15 GB download that
cannot fit the card. The badge you were about to select on is the wrong badge.

**2. `interactive` + `may_evict` is sharper than it sounds, because clonin is public.**
`clonin-frontdoor.service` is **active** and internet-facing behind `clonin.flatmix.uk`,
taking a lease per request. And the **live** box policy has clonin at
`batch`/`max_priority: batch` — the repo copy still says `interactive`. So an
`interactive` browsing session **outranks the public front door and can stop a stranger's
sentence mid-word**, and nothing can evict it back (`exclusive:hashcat` has no driver).
ACE-Step is `batch` and evictable too. Both consequences are handled in §4, but they are a
choice, not an accident. Say it out loud once and move on.

**3. `browse` was never available.** The superseded plan's Phase 5 proposes
`alias browse=...`. `/usr/bin/browse` is a symlink to `xdg-open` from `xdg-utils`. And an
alias is invisible to `cron`, `systemd`, and every non-interactive shell — ship a
`~/.local/bin` symlink like `clonin` does, not a `.bashrc` alias like `claude-local` does.

---

## 2. Verified today — 2026-09-01, measured, not remembered

```
warden       http://192.168.1.111:8130  {"status":"ok","service":"warden"}
             tenants: []  leases: []  queue: []  exclusive_holder: null
card         16380 total · ~14150 free · ~2230 foreign · ~12820 live-available · 12489 book
             max admissible peak = floor(12489 / 1.10) = 11353 MiB
ollama       http://192.168.1.111:11434  version 0.32.15   /api/ps → {"models":[]}
box disks    C: 76.4 GB free · D: 62.6 GB free · B: 14.7 TB free  (OLLAMA_MODELS=D:\Models\OLAM)
registry     reachable from the box (404 on bare /v2/ is the live-registry response)
warden pkg   git+https://github.com/FlipperZero2020/warden.git@v0.3.0 resolves;
             stdlib-only, `dependencies = []`, requires-python >=3.11
this venv    Python 3.12.3 · browser-use 0.13.8 · ollama-python 0.6.1 · no playwright pkg
this VM      X11/XFCE, DISPLAY=:0.0, 1914x916 · no GPU passthrough (VMware SVGA II, llvmpipe)
browser      /home/tom/.cache/ms-playwright/chromium-1194/chrome-linux/chrome  ← what
             browser-use actually picks, NOT the system Chrome 152
`browse`     TAKEN → xdg-open.  `browsin` free.  ~/.local/bin has clonin, no warden
```

Models pulled on the box — **all five are text-only**:

| tag | bytes | capabilities | note |
|---|---|---|---|
| `qwen3-32k:8b` | 5,225,387,844 | completion, tools, **thinking** | `num_ctx 32768` in the Modelfile. **Not declared in `policy.json`** |
| `qwen3:8b` | 5,225,388,164 | completion, tools, thinking | declared, booked **5462 MiB** at Ollama's default window |
| `qwen2.5-coder:14b` | 8,988,124,298 | completion, tools, insert | declared, 9239 MiB. cline measured it emitting tool calls as **plain text** |
| `huihui_ai/…-abliterate:14b` | 8,988,124,423 | completion, tools, insert | same, wrapped in `<tools>` tags |
| `nomic-embed-text` | 274,302,450 | embedding | — |

**No model reports a `vision` capability.** Nothing on this box has ever exercised
Ollama's multimodal path — not the projector loading, not the image wire format, not
`format=` combined with an image. That is an unknown of a different kind from the ones
warden_client faced, and Phase 3 exists to retire it cheaply.

### The declared workloads, live on the box

```
clonin                    clonin   batch        max_prio=batch   3726          :8123
ollama:qwen3:8b           ollama   interactive  —                5462          :11434
ollama:qwen2.5-coder:14b  ollama   interactive  —                9239          :11434
acestep                   acestep  batch        —                3257 peak 6853 :7860
exclusive:hashcat         hashcat  exclusive    —               10752          (no driver)
```

There is **no `/v1/workloads` endpoint and no `/openapi.json`** — `DECISIONS.md` records
that as deliberate. The only discovery channel is the `detail` string on a failed
acquire, so any table like the one above must say where it came from and be corrected
when policy moves.

---

## 3. What is actually in the way

### 3.1 The context arithmetic, and why `num_ctx` is now a client-side knob

Measured from the installed package:

| term | measured | tokens @ 3.4–3.6 c/t |
|---|---|---|
| `system_prompt.md` (the default for a non-browser-use, non-Anthropic model) | 24,145 chars | **~6,700–7,100** |
| `system_prompt_flash.md` (`flash_mode=True`) | 2,397 chars | ~700 |
| DOM clickable-elements block, `max_clickable_elements_length` | **capped at 40,000 chars** | **~11,100–11,800** |
| agent history | `max_history_items` defaults to **`None` — unbounded** | grows every step |
| screenshot, vision on | 256 (gemma3) → ~1,280 (qwen2.5vl) → ~2,880 (llava) | per step, one only |

So a default-configured step is **~18,000–19,000 tokens before history or screenshot**.
`chars/4` was the first estimate and it is wrong in the optimistic direction — dense
markdown with JSON fragments and HTML attribute soup runs 3.4–3.6 chars/token. Treat
every token figure above as an **upper bound derived offline**: the DOM numbers came from
a harness that could not run `DomService.viewport_threshold=1000`, the viewport-visibility
filter, so the real serialized page is smaller by an unmeasured amount. **That is what
Phase 4's logging proxy is a gate for.**

The genuinely good news, and it removes the whole Modelfile problem the superseded plan
implied: **`ChatOllama` passes `ollama_options` straight through as `options=`, and the
ollama client's `Options` type has `num_ctx`.** So the context window is set from Python:

```python
ChatOllama(model=..., host=..., ollama_options={"num_ctx": 16384})
```

No derived Modelfile, no `/api/create`, no second tag. **But this couples two numbers
that live in different places**: warden books a fixed `cost_mib` measured at *some*
`num_ctx`, and nothing stops the client asking for a bigger one. Say it in the policy
`$comment`, assert it in `lease.py`, and treat a mismatch as a bug:

> **The `num_ctx` in `~/.config/browsin/config` and the `num_ctx` the policy entry was
> measured at are one number written in two places. If they drift, warden's book is
> wrong and the card is oversubscribed silently.**

### 3.2 Silent truncation — and the one way vision makes it *better*

Read from ollama 0.32.15's source by two independent agents: past `NumCtx-1`, context
shift cuts the prompt to `numCtx - max((numCtx-nKeep)/2, 1)` — roughly **half the window
discarded, head first, keeping only `nKeep=4` tokens**. The system prompt is destroyed
while the grammar keeps emitting perfectly valid JSON. The only trace anywhere is
`slog.Warn("truncating input prompt")` in the *server's* log, on Windows.

This is the exact bug that hid for weeks in `2026-08-09_cline` — "Ollama's default window
is 4096, Claude Code sends ~30,000, so ~86% of the prompt was silently discarded; the
model was answering a mangled fragment."

Two aggravators specific to this project:

- **`ChatOllama` returns `usage=None` on both branches.** browser-use has *zero* token
  telemetry for Ollama. There is no `prompt_tokens` to compare against `num_ctx`, no cost
  line, nothing. Detection must come from **outside** the agent.
- **`max_history_items=None`** means history is unbounded and is the fastest-growing term.
  It, not the screenshot, is what pushes a run past `num_ctx` mid-task.

And one genuine mercy: **with an image in the request Ollama refuses to context-shift and
errors instead.** So a vision run fails *loudly* where a text run fails silently.
`[VERIFY]` on 0.32.15 specifically — but if it holds, it is an argument for vision that
has nothing to do with model quality.

### 3.3 The 75-second wall that `ChatOllama(timeout=…)` does not lift

`Agent._get_model_timeout` resolves the model name against a table — `gemini` 75/90,
`groq` 30, o3/claude/sonnet/deepseek 90, **else 75**. An Ollama model name hits the else.
`service.py:1178` then wraps the call in `asyncio.wait_for(..., timeout=self.settings.llm_timeout)`.

`ChatOllama(timeout=300)` is the **httpx** timeout and does not touch this. Worse: on
timeout the coroutine is cancelled but **the GPU keeps generating to completion**, holding
VRAM and delaying the next request — cascading timeouts are the expected failure mode.

**Measured 2026-09-01:** a *small* constrained request (200 prompt tokens) round-trips in
**8.1 s** — prefill 0.1 s, generation 4.1 s, plus 658 chars of thinking. Comfortably inside
the wall. But that is 200 tokens, not the 12–19k a real browser step sends, and prefill
scales with prompt size while this measurement does not constrain it. A 7B VL model
prefilling 12–19k tokens *including* a ~1,280-token image may still approach 75 s;
Phase 4's proxy measurement is what settles it. Pass `llm_timeout`
explicitly on the `Agent`, and budget `step_timeout × max_steps` as a real wall-clock
ceiling — `run(max_steps=25)` with the default `step_timeout=180` is 75 minutes; at
`step_timeout=900` it is 6.25 hours.

### 3.4 The vision shortlist, with corrected arithmetic

KV cost per token is `2 × n_kv_heads × head_dim × 2 B × n_layers`. **Weights below are
derived from ollama.com's rounded download size**, which is a proxy, and the graph/CUDA
term is deliberately omitted — the one calibration point available (qwen3:8b, predicted
5,559 vs warden's measured 5,462) has a **negative** residual, so a positive constant
cannot be justified. Every figure is therefore an estimate that `measure_footprints.py`
must replace before it enters `policy.json`.

| model | pull | KV/token | @16k need ×1.10 | @32k need ×1.10 | verdict |
|---|---|---|---|---|---|
| **`minicpm-v:8b`** | 5.5 GB | 56 KiB | ~6,755 (54%) | ~7,741 (62%) | lightest serious candidate |
| **`qwen2.5vl:7b`** | 6.0 GB | 56 KiB | ~7,280 (58%) | ~8,265 (66%) | strongest web/OCR understanding |
| `llava:7b` | 4.7 GB | 128 KiB | ~7,838 (63%) | ~10,090 (81%) | fits — but ~2,880 tok/image |
| `gemma3:4b` | 3.3 GB | 136 KiB* | ~5,855 (47%) | — | *iSWA makes this much lower `[VERIFY]`. 256 tok/image, flat |
| `llama3.2-vision:11b` | 7.8 GB | 128 KiB | ~10,436 (84%) | **~12,689 — over 12489** | 16k only, empty card only |
| `gemma3:12b` | 8.1 GB | 384 KiB* | SWA-dependent | — | `[VERIFY]`; without iSWA it blows the ceiling outright |
| `mistral-small3.2:24b` | 15 GB | — | — | — | 14,305 MiB of weights alone. Does not fit |

Percentages are of the **12,489 MiB book ceiling on an empty card**. With clonin resident
(3,726) the remaining book is 8,763 — but that is a *priority* question, not a memory one:
`_plan_eviction` blocks a victim when `tenant_priority >= lease.priority_value`, and
clonin is live at `batch`, so an `interactive` browsing lease **evicts it**. ACE-Step at
`batch` likewise. And when nothing fits, warden **queues with a `retry_after_s`** rather
than hard-refusing.

**Decided 2026-09-01: `qwen2.5vl:7b`**, with `minicpm-v:8b` as the fallback if it disappoints
and `gemma3:4b` as the *smoke test* — 3.3 GB is the cheapest way to learn whether this
Ollama build runs any vision architecture at all. **Measured 2026-09-04 at 8375 MiB
(§10, Phase 3), declared as the derived tag `qwen2.5vl-32k:7b`** — **11.5% above** the
estimate, which is what removed the co-residency margin (§10, 2026-09-04).

At the decided 32k window `qwen2.5vl:7b` needs ~8,265 MiB after the safety factor, against
8,763 MiB of book remaining with clonin resident — so on these **estimates** it co-resides
with the voice service rather than evicting it, and the `interactive`/`may_evict` decision
is a fallback for the contended case rather than the normal path. A 53 MiB margin on a
figure derived from a rounded download size is not a guarantee; Phase 3's measurement is
what settles it, and it may well take the margin away.

Two naming traps: the registry name is **`qwen2.5vl`, not `qwen2.5-vl`** —
`ollama.com/library/qwen2.5-vl` returns 404, and the superseded plan's pull command
therefore cannot succeed as written. And `use_vision='auto'` is the worst of both: it adds
a 24th action, making the constrained-decode grammar *larger* than either `True` or `False`.

---

## 4. Design

### 4.1 Shape

```
browser_use_local_model_GPU/
├── PLAN.md                     this file — design doc and status log
├── PLAN.superseded-2026-08-25.md
├── CLAUDE.md                   for whoever EDITS this repo
├── requirements.txt            browser-use==0.13.8 pinned, warden @ git+…@v0.3.0
├── bin/browsin                 bash shim → venv python → cli.py   (symlinked to PATH)
├── browsin/
│   ├── cli.py                  arg parsing, config precedence, the REPL loop
│   ├── lease.py                the asyncio lease holder — the load-bearing module
│   ├── agent.py                browser-use construction, correct for 0.13.8
│   └── proxy.py                the logging reverse proxy (Phase 4 gate, then kept)
├── runs/<timestamp>/           conversation logs + the proxy's prompt-size record
└── venv/
```

Config precedence copies `clonin` exactly — **env var > `~/.config/browsin/config` >
built-in default** — so if the box moves, one file changes and the script, the skill and
every caller follow. Do not hardcode the host anywhere.

### 4.2 `lease.py` — why in-process, and what it must do

A `warden hold -- <cmd>` wrapper has exactly one lever when the lease is revoked:
`--on-lost kill|warn`. That is structurally wrong here. A browser agent that loses its
model mid-task needs to **stop the loop, close the window cleanly, and say what happened**
— not be SIGTERMed at an arbitrary step with a half-filled form on screen. So:

```python
async with hold("ollama:qwen2.5vl:7b", reason=task[:120]) as held:
    ...
```

and inside, five obligations:

1. **Acquire → `wait_active` → heartbeat.** The cadence is `min(heartbeat_interval_s or 30,
   ttl_s/3)`, computed once at acquire, and the first beat lands at `t=interval`, not `t=0`.
2. **Wire `held.lost_event` to cancellation.** It is a `threading.Event`. ~~Bridge it with
   `loop.add_reader`/`run_in_executor`.~~ **Corrected by measurement:** `to_thread(evt.wait)`
   cannot be stopped — cancelling the await leaves the worker blocked forever, pinning
   `asyncio.run`'s shutdown for up to `THREAD_JOIN_TIMEOUT` (300 s) and burning an executor
   slot the warden client itself needs; and a `socketpair` + `add_reader` still needs a
   thread to notice the Event, plus two file descriptors `loop.close()` does not close. What
   ships is a daemon watcher thread polling `lost_event.wait(0.25)` and routing
   `task.cancel()` through `loop.call_soon_threadsafe` — the only call that is safe from
   that thread, and one that raises `RuntimeError` on a closed loop. **Polling
   `:11434` tells you nothing** — warden is a control plane, not a proxy, so when it
   revokes your lease it unloads the model and the endpoint *keeps answering*. The next
   request silently reloads several GB outside warden's book and the session looks healthy.
   The heartbeat's 404 is the only revocation channel that exists.
3. **Assert what is resident.** Nothing enforces that your request names the model you
   leased. After ACTIVE, `GET /api/ps` and assert the resident set is exactly the leased
   tag. This is two seconds of work that turns an OOM at step 12 into an error at step 0.
4. **Assert `num_ctx`.** Compare the configured window against the policy entry's measured
   window (§3.1) and refuse to start on a mismatch.
5. **Release on every path.** `finally` + `atexit` cover exceptions and Ctrl-C. A **default
   `SIGTERM` does not** — Python runs neither for one, which is how Phase 1 stranded a
   lease — so the handlers go on *before* the acquire, not after, or a signal during a
   ~190 s cold load is still a bare kill. `SIGKILL` is uncoverable and the lease then sits
   for `ttl_s + idle_linger_s`; a shorter `--ttl` is the only lever, default 120 s rather
   than policy's 300. And a cancel during the acquire needs an explicit DELETE: warden's
   async facade drives a *sync* context manager through `to_thread(cm.__enter__)`, so
   cancelling that await leaves a live, heartbeating lease whose `__exit__` never runs.

If `$WARDEN_ENDPOINT` is already set — i.e. somebody ran us under a future `warden hold`
— **do not double-lease**. Use the endpoint given, skip acquire, and say so.

### 4.3 `agent.py` — correct for 0.13.8

The superseded plan's snippet **runs**. Every symbol still exists. That is the danger:
`Agent.__init__` ends in `**kwargs` it never reads, so `planner_llm=`, `validate_output=`,
`max_steps=`, `tool_calling_method=` and every other 0.9.7-era parameter construct cleanly
and do **nothing**. Guard it:

```python
_VALID = set(inspect.signature(Agent.__init__).parameters) - {"self", "kwargs"}
bad = set(kw) - _VALID
if bad:
    raise TypeError(f"Agent() does not accept {sorted(bad)} in browser-use 0.13.8")
```

Zero-cloud env, set **before `import browser_use`** — `browser/session.py` evaluates
`DEFAULT_BROWSER_PROFILE = BrowserProfile()` at import time, so a `load_dotenv()` after the
import is too late for at least one of these:

```python
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")       # PostHog
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
os.environ.setdefault("BROWSER_USE_VERSION_CHECK", "false")  # the pypi.org GET
os.environ.setdefault("BROWSER_USE_DISABLE_EXTENSIONS", "1") # the clients2.google.com CRX fetch
```

This is not hygiene. **PostHog telemetry is ON by default and ships the literal task
string, every URL visited, the full action history, the final answer text and judge
reasoning to `eu.i.posthog.com`, with exception autocapture.** The headline goal of this
project is "zero cloud API calls"; unmodified, it makes three different kinds.

The construction, with every default that matters overridden and why:

```python
llm = ChatOllama(
    model=MODEL, host=held.endpoint,
    timeout=600.0,                        # default None = INFINITE httpx timeout
    ollama_options={"num_ctx": NUM_CTX, "temperature": 0.2, "num_predict": 1024},
)
agent = Agent(
    task=task, llm=llm, browser_session=session,
    use_vision=True,          # default True; the whole point once a VL model is leased
    use_judge=False,          # default True = one extra full LLM call per run
    max_history_items=8,      # default None = UNBOUNDED context growth
    llm_timeout=600,          # default resolves to 75 s for an ollama name (§3.3)
    step_timeout=900,         # default 180
    max_actions_per_step=2,   # see the caveat below
    max_failures=5,
    calculate_cost=False,     # usage is None anyway
)
history = await agent.run(max_steps=25)   # max_steps is a run() arg; DEFAULT IS 500
print(history.final_result())             # run() returns AgentHistoryList, not a string
```

**The thinking question is settled, and the fear was unfounded.** The research's single
largest unreproduced claim was that qwen3's forced reasoning would land in
`message.thinking` leaving `message.content` empty — which, since `ChatOllama` reads only
`.content`, would make `model_validate_json('')` raise and **every step fail identically**.
Measured: with `format=<schema>` and thinking at its default, ollama populates **both**
fields — 658 chars of thinking *and* 386 chars of valid `AgentOutput` JSON in `.content`.
One completion, `prompt_eval=200 / eval=121`; no evidence of the doubled-prefill
two-request dance the same research feared. `ChatOllama` needs no patch and no `think=`
parameter.

**Leave thinking ON.** `think=False` is 2.6x faster (3.1 s vs 8.1 s) and produces *worse*
actions: on an identical prompt the thinking run correctly emitted a single `done`, while
the no-think run hallucinated a `navigate` and a `click` on a page it had already been told
the contents of. Speed is not the binding constraint here; action quality is.

`flash_mode=True` cuts the system prompt from 24,145 to 2,397 chars — but it also forces
`enable_planning=False` and strips the output schema to `['memory','action']`, with no
scratchpad field at all. For a reasoning-tuned model that is **a bet, not a size
optimization**. Decide it from Phase 4's measured prompt size, not in advance.

### 4.4 The browser — attach, never launch

**Decided 2026-09-01: `browsin` drives the owner's real Chrome over CDP.** It does not
launch a browser and it does not touch a profile directory. That single choice deletes most
of this section's original hazard list, so what follows is mostly a record of what is *no
longer* a problem, plus the one new thing that is.

```python
session = BrowserSession(cdp_url="http://127.0.0.1:9242")
agent = Agent(task=task, llm=llm, browser_session=session, ...)
```

When `browser_session` is passed, **`browser_profile` is ignored entirely**
(`agent/service.py:294`). So:

| Hazard the launch path had | Status under CDP attach |
|---|---|
| 718 MB `_copy_profile()` copytree per construction, one-way | **gone** — no profile is constructed |
| Nothing copies back, so logins never persist | **gone** — it is the real profile; writes are real |
| Playwright's Chromium 141 wins over system Chrome 152 | **gone** — you choose the binary by starting it |
| `executable_path` silently rewriting `user_data_dir` | **gone** — neither is set |
| Silent 30 s stall on a browser that starts then dies | **gone** — nothing is launched |
| Stale `SingletonLock` (dead PID 153182) in `chrome-default` | **gone** — Chrome resolves its own lock at startup |
| `/tmp/browser-use-user-data-dir-*` leak per `import browser_use` | **the premise was wrong** — the import leaks nothing, and CDP attach does not avoid the four families that *are* real (§7) |

What `bin/browsin` must therefore do before it leases anything:

1. **Probe `127.0.0.1:9242`.** If CDP answers, attach and go.
2. **If nothing answers, do not launch a second Chrome on a profile that is already open** —
   that fails on the singleton lock in a confusing way. Detect whether Chrome is running:
   - running, no debug port → print the exact restart command and stop. Chrome only opens
     the CDP port at startup; it cannot be enabled on a running instance.
   - not running → offer to start it, with the real profile and the debug port.
3. **Never bind the debug port to anything but loopback** (see §7).
4. **Check whether clonin is resident, and if it is, stop and ask.** Phase 3 measured the
   vision model at **8,375 MiB**, needing **9,213** after the ×1.10 factor against the
   **8,763** of book left when clonin holds its 3,726 — a **450 MiB deficit**. So an
   `interactive` acquire does not co-reside with the voice service, it *evicts* it, and
   `clonin-frontdoor` is public: the sentence being cut off may belong to a stranger.
   `GET /v1/status` and look for a clonin tenant; if there is one, print who would be
   displaced and require an explicit `--evict` (or an interactive yes) rather than
   proceeding. `--wait` queues instead, and clonin's `idle_linger_s` is 120 s, so the
   normal wait is short.

The command it should print, and the one the skill documents:

```bash
google-chrome --remote-debugging-port=9242 \
  --user-data-dir="$HOME/.config/browseruse/profiles/chrome-default"
```

**The cost of this choice, stated plainly.** The agent now operates a browser holding live
cookies and saved passwords — `Cookies` and `Login Data` in that profile are real and
current. A local model reading attacker-controlled page text, while holding the owner's
sessions, is the highest-consequence failure mode in this project. §7 treats it as the top
security item, not a footnote. Two mitigations are cheap and belong in Phase 4: run against
a Chrome profile that is *not* signed into anything you would mind losing, and never point
it at an untrusted site during a session where a sensitive tab is open.

**The browser and the model do not compete for VRAM.** This guest has no GPU passthrough —
VMware SVGA II, `vmwgfx`, llvmpipe, no `/dev/nvidia*`. Chromium rasterises on the CPU and
never touches the 4060 Ti. The contention that *is* real is CPU: this guest holds 6 vCPUs
of the host's 6-core i7-8700K, and the host is the same physical machine that feeds the
GPU. Budget it; nobody has.

---

## 5. Phases

Each phase ends with a gate. Do not start the next until its gate passes. **Ordering
principle: the riskiest unknown is also the cheapest to test, so it goes first.**

### Phase 0 — correct the record. ✅ **PASSED 2026-09-01**

`git init`. Pin `browser-use==0.13.8` and `warden @ git+…@v0.3.0` in `requirements.txt`
(the root cause of this whole divergence was an unpinned `pip install browser-use`). Write
`CLAUDE.md`. Add the zero-cloud env block. Add the `**kwargs` guard.

**Gate:** `version('browser-use')` → `0.13.8`; `CONFIG.ANONYMIZED_TELEMETRY` is False and
`'posthog' not in sys.modules` after importing `Agent`; the guard raises `TypeError` on a
0.9.7-era kwarg.

**The gate's middle clause is a false pass, and had to be replaced rather than merely
run.** Measured: `'posthog' not in sys.modules` after `from browser_use import Agent` is
`True` *even with telemetry fully enabled*, because the import is lazy and lives inside
`ProductTelemetry.__init__` — it happens at Agent **construction**. `tools/phase0_gate.py`
therefore checks the literal wording, then constructs `ProductTelemetry` and asserts the
client is `None`, and then runs the same probe a third time with telemetry ON as a
**negative control**, so the two checks are known to differ rather than assumed to.

```
  [PASS] browser-use is pinned at 0.13.8                    version('browser-use') == '0.13.8'
  [PASS] warden is the v0.3.0 commit                        0.3.0 @ a252644aa5bd
  [PASS] zero-cloud after importing Agent                   ANONYMIZED_TELEMETRY=False CLOUD_SYNC=False
                                                            VERSION_CHECK=False posthog in sys.modules=False
  [PASS] no PostHog client after ProductTelemetry()         client=None, posthog in sys.modules=False
  [PASS] the telemetry check discriminates                  with telemetry ON: the plan's literal check still
                                                            says posthog-in-sys.modules=False (a false pass),
                                                            while construction yields a live Posthog client
  [PASS] the guard rejects 0.9.7-era kwargs                 TypeError names max_steps, planner_llm,
                                                            tool_calling_method, validate_output
  [PASS] the guard passes every kwarg PLAN.md §4.3 uses     10 accepted, out of 62 real parameters

PHASE 0 GATE: PASSED — 7 of 7 checks
```

### Phase 1 — the grammar smoke test. ✅ **PASSED 2026-09-01**

Dump the real `AgentOutput` schema out of the venv (~21 kB, 47 `$defs`, 15 `anyOf`, 23
actions). Lease **`ollama:qwen3:8b`** — already declared, already pulled, at its booked
5462 MiB and its booked default window, because a grammar test needs no context. Send
**one** `/api/chat` with `format=<that schema>` and a 50-token prompt.

Four things come out of one call:

- Does Ollama 0.32.15 **compile** the schema into a working grammar, or 400?
- How long is the first constrained token, and total latency? Against the **75 s** wall.
- Is `message.content` non-empty, or did qwen3's thinking go to `message.thinking`?
  `ChatOllama` reads only `.content` and never passes `think=`. If content is empty,
  `model_validate_json('')` raises → `ModelProviderError` → **every step fails identically**.
- Read `D:\warden\logs\ollama-server.log` for how many completions the request produced and
  the served `n_ctx`. One agent traced ollama's `ChatHandler` *forcing* `Think=true` for
  thinking-capable models and disabling the grammar on a first unconstrained pass. **This
  is the single largest unreproduced claim in the research** and this call settles it.

**Gate — all four met.** Run as three calls in one lease to separate the failure modes:

| | config | latency | content | thinking | result |
|---|---|---|---|---|---|
| A | `format=<schema>`, thinking default | 8.1 s | 386 ch | 658 ch | **parses as `AgentOutput`** |
| B | `format=<schema>`, `think=False` | 3.1 s | 286 ch | 0 | parses, but worse actions |
| C | no `format` (control) | 7.5 s | 56 ch | 1365 ch | own shape — the grammar is what forces it |

Lease ACTIVE after 185.1 s; `/api/ps` showed `qwen3:8b` resident at 5319 MiB, ctx 4096 —
matching llama.cpp's own `5319 MiB` projection and warden's booked 5462. Released with
**no ghost**. The approach is viable; what is *not* yet established is behaviour at a real
browser step's prompt size (§3.3).

### Phase 2 — `lease.py`. ✅ **PASSED 2026-09-01**

Build §4.2. `AsyncWardenClient` has never run against this box.

**Gate, all five:** a lease held **10 continuous minutes** from asyncio, visible in
`/v1/status` throughout · release frees within `verify_freed_fraction` and books **no
ghost** · Ctrl-C mid-hold releases cleanly · the `/api/ps` assertion catches a deliberately
wrong model name · `lost_event` fires and cancels within one heartbeat when the lease is
released out from under it.

`browsin/lease.py` is the module; `tools/phase2_gate.py` is the gate;
`tools/test_lease_offline.py` is the eighteen things that can be proven against a fake
warden client in a second rather than in a 190-second cold load — including the cases a
real run cannot easily reach, like a signal arriving *during* the acquire.

**The gate had to be repaired three times before it was worth trusting**, and every repair
came from running it rather than reading it. §10 records what each one was. The rule this
phase actually established is narrower than "test it": *a gate that cannot fail has not
passed*, and the way to find out which kind you have is to make it grade a run you
deliberately broke.

**Gate output, `tools/phase2_gate.py`, 2026-09-01 22:22–22:37, from an idle card
(`free 13879 · foreign 2501 · ghost 0 · committed 0 · no tenants, no leases`):**

```
== lost
  lease 5ba574cd: pending (starting) -> active after 15.3s; heartbeat every 30s
  releasing 5ba574cd out from under the holder...
  lease 5ba574cd lost - cancelling the run
  [PASS] 5. lost_event fires and cancels within one heartbeat
         cancelled 9.7s after the lease was released out from under it (interval 30s)

== sigint / sigterm                       (a child process, so the gate survives to report)
  [PASS] 3. SIGINT  mid-hold releases cleanly   child exited 0 'RELEASED_ON_SIGNAL SIGINT';
                                                warden reports the lease as released
  [PASS] 3. SIGTERM mid-hold releases cleanly   child exited 0 'RELEASED_ON_SIGNAL SIGTERM';
                                                warden reports the lease as released

== assert
  resident: [{"model": "qwen3:8b", "size_vram_mib": 5320, "context_length": 4096}]
  [PASS] 4.  the /api/ps assertion catches a deliberately wrong model name
         resident=['qwen3:8b']; assert_resident('ollama:qwen2.5-coder:14b') raised NotResident
  [PASS] 4b. the num_ctx assertion catches a wrong window
         served at num_ctx=4096, configured 32768 -> ContextWindowMismatch
  [NOTE] /api/ps reports context_length=4096 for qwen3:8b, matching the configured 4096

== hold                                                        40 samples, one every 15 s
  +   0s  lease=active tenant=True free=8512 committed=5462 ghost=0
  ...
  +585s  lease=active tenant=True free=8514 committed=5462 ghost=0
  [PASS] 1. a lease held 10 continuous minutes, visible in /v1/status throughout
         603s held, 40 samples, 0 of them not showing an active lease + tenant;
         committed 5462->5462 MiB, ghost stayed 0

== the teardown that follows the release          (release does NOT free VRAM; linger does)
  event 2200 lingering       last lease gone; lingering 180s
  event 2202 stopping        idle_linger_elapsed
  event 2203 evict_verified  5467 MiB of an expected 5416 MiB came back (ollama:qwen3:8b)
  event 2204 stopped
  [PASS] 2. release frees within verify_freed_fraction and books no ghost
         freed 5467 of an expected 5416 MiB (1.01, policy floor 0.8); after teardown:
         committed=0 ghost=0 free=13975 tenants=[] /api/ps=[]

PHASE 2 GATE: PASSED - 7 of 7 checks, covering all five
```

Two honest caveats on those numbers. The acquire went ACTIVE in **15.3 s, not ~190 s** —
the model had been loaded and unloaded minutes earlier, so the 5 GB blob and its Defender
scan were both still in the Windows file cache. That is the same trap the `gpu-box` skill
warns about for disk benchmarks, and it applies to a second *lease* too: **this run did not
exercise a genuinely cold load.** And `expected_mib` was 5416 rather than policy's booked
5462, because warden verifies against what it last *measured*, not against `cost_mib`.

Eighteen further checks run with no warden and no card at all
(`venv/bin/python tools/test_lease_offline.py`), covering what a real run cannot easily
reach: a signal arriving *during* the acquire, a body that swallows every cancellation, a
second signal into a wedged loop, an `/api/ps` serving a CPU-split model, and one whose
only context length is the architectural maximum hiding in `details`.

### Phase 3 — vision on the box. ✅ **PASSED 2026-09-04**

1. **Smoke-test the architecture cheaply first.** Pull `gemma3:4b` (3.3 GB). Send one image
   with `format=<a trivial schema>`. This asks the only question that matters at this
   stage: *does this Ollama build run a vision model, load its projector, and honour
   `format=` with an image in the same request?* Nothing on the box has ever done it.
2. **Then pull `qwen2.5vl:7b`** (6.0 GB — note the registry name has no hyphen). Decided;
   `minicpm-v:8b` is the fallback if it disappoints on real pages.
3. **Measure it.** `tools/measure_footprints.py` runs **on the box**, in a scratch dir with
   a scratch `--ledger`, on a **quiet card**, one workload at a time. It builds a *second*
   engine alongside the live service, which will see the measured model as foreign memory
   and deny admissions for the window. `[VERIFY]` with the owner whether that is acceptable
   or whether it needs a maintenance window with warden stopped — and **gate
   `clonin-frontdoor` for the duration**, because a stranger's page load can take a lease
   mid-measurement.
4. **Declare it in `policy.json`.** JSON-aware script only, **never a regex** — a regex
   corrupted the live file once, on 2026-08-30. Back up beside it as
   `policy.json.pre-browsin-backup`, write-temp-and-`os.replace`, read the live file back
   and confirm nothing else moved. No restart: `PolicySource` re-reads on `(mtime, size)`,
   which cuts both ways — **a bad edit binds on the very next acquire, with no window to
   catch it**, and a `PolicyError` takes out the *whole file*, clonin and ACE-Step included.
   Record the measured `num_ctx` — **32768, decided** — in `$comment_cost` (§3.1).

**Gate:** a lease for the new workload is granted from this VM; `/api/ps` shows the model
resident; one image + `format=` round-trips correctly; releasing frees within
`verify_freed_fraction` with no ghost; the declared `cost_mib` is the **measured**
load-drop, not the estimate in §3.4.

**A finding step 4 did not anticipate: the bare pull cannot be the declared workload.**
`ollama:qwen2.5vl:7b` loaded plain (warden's own load call passes no `options`, empty
prompt) served `context_length=4096` — measured directly against `:11434` before touching
warden at all. `browsin/lease.py`'s obligation-4 assertion compares the *served* window
against the client's configured `num_ctx` and refuses to start on a mismatch (that is what
the assertion is *for* — Phase 2's gate proved it by deliberately engineering exactly this
mismatch). So the plan's own num_ctx=32768 decision would have failed obligation 4 on
every single start, immediately, in production — not a hypothetical, since the served
window is fixed at whatever the model's own default is, and nothing in the client's request
options is consulted by warden's plain load. The fix is the same one already sitting unused
on the box: a **derived Modelfile tag**, `qwen2.5vl-32k:7b` (`FROM qwen2.5vl:7b` +
`PARAMETER num_ctx 32768`), exactly the pattern `qwen3-32k:8b` had already established
(§2's model table) — created with `ollama create`, no re-download, four existing layers
reused plus one new ~20 KiB parameter layer. Verified served at `context_length=32768`
before it was ever declared in policy. **This is declared, not `qwen2.5vl:7b` bare** — the
plan's §1/§3.4 references to `qwen2.5vl:7b` mean this derived tag in practice.

**Gate output, 2026-09-04, from a quiet card (free 14127, foreign 2253, no tenants),
`clonin-frontdoor` + its cloudflared tunnel gated for the measurement window:**

```
tools/measure_footprints.py ollama:qwen2.5vl-32k:7b --policy scratch_browsin/policy.json ...
── ollama:qwen2.5vl-32k:7b ── card quiet at 14126 MiB free
   ready in 18.3s, drop 8343 MiB (engine measured 8343)
   warm drop 8369 MiB
   freed 8375 MiB on evict, ghosts 0

 workload                      budget   load    warm   freed  engine
 ollama:qwen2.5vl-32k:7b        8265   8343    8369    8375    8343
```

`cost_mib` declared at **8375** (the evict-recovered figure, matching house convention for
every other MEASURED entry) — **11.5% above** §3.4's pre-measurement estimate. The "within 1.3%" first written here
compared unlike quantities: **8265 is a *need*** (estimated cost × the 1.10 safety factor),
while **8375 is a *cost***. Like for like it is 7514 estimated cost → 8375 measured (+11.5%),
or 8265 estimated need → 9213 measured need (+11.5%). The error mattered: at 1.3% the
estimate looks validated, and at 11.5% it is what cost the co-residency margin. Backed up as
`policy.json.pre-browsin-backup`; re-read after write confirmed every other workload and
every top-level key byte-for-byte unchanged.

Then the real gate, `tools/phase3_gate.py`, run from this VM through `browsin/lease.py`
against the now-declared workload — no scratch engine, the actual production path:

```
[PASS] 1. lease granted from this VM   endpoint=http://192.168.1.111:11434 in 18.5s
[PASS] 2. obligation 3+4 assertions passed inline (served num_ctx=32768)
[PASS] 3. one image + format= round-trips correctly in 1.8s -> {'shape': 'triangle', 'color': 'green', 'text_seen': 'PHASE 3 GATE'}
released cleanly, hold() exited with no exception
```

Release doesn't free VRAM immediately — the idle linger does, same pattern Phase 2 found —
so the fourth condition was confirmed from warden's own event log after the 180s linger
elapsed: `evict_verified — 8439 MiB of an expected 8368 MiB came back
(ollama:qwen2.5vl-32k:7b)`, ratio 1.0085 against the 0.8 floor, `ghost_mib: 0`. Card
returned to `free 14128 · foreign 2252 · ghost 0 · no tenants` — indistinguishable from
its pre-measurement baseline.

**One more data point, incidental to this phase but relevant to §10's 2026-09-03 entries
below — read those first if this looks like it is re-claiming a fixed bug.** `qwen2.5vl:7b`
and the derived `qwen2.5vl-32k:7b` were both **newly pulled/created files, loaded cold for
the first time, with the Defender exclusion already in place** — exactly the case the
corrected understanding says the exclusion actually helps (a first-ever scan is unavoidable
either way, but nothing here needed a second one). Both loaded in the 13-23s normal range
(18.1-18.3s engine-measured), not the 180-190s of 09-01's one bad window. This is a second,
independent confirmation of the *corrected* claim — "add the exclusion before pulling a new
model" — not of the earlier, retracted one that credited it with fixing a persistent slowdown.

### Phase 4 — one headed browser step, instrumented. ✅ **PASSED 2026-09-04**

Start Chrome yourself with the debug port (§4.4), confirm CDP answers on `127.0.0.1:9242`,
and attach. No launching, no profile. Then one Agent step against a fixed trivial page,
**behind `proxy.py`** — a logging reverse proxy on `:11434`.
This is the only way to see the real prompt size, because `usage=None` means the agent has
no idea. `local_Model/PLAN.md` independently asks for exactly this "on day one".

**Gate as originally written:** the agent drives the *already-open* Chrome (a tab it did not create changes URL) ·
measured first-request prompt tokens recorded and **under 60% of the served `num_ctx`** ·
**no** `/tmp/browser-use-user-data-dir-*` larger than 4 KB was created · the box's ollama
log contains no `truncating input prompt` · `ss -ltnp` shows 9242 bound to `127.0.0.1`, not `0.0.0.0`.

**All five of those are satisfiable while the thing they test is broken.** Reading the
installed library before writing the gate — rather than after it passed — is what turned
this up, and the corrected gate (`tools/phase4_gate.py`) is 14 checks with six control runs
that must each make it fail. The three worst:

- **"a tab it did not create changes URL"** — `directly_open_url` (default **True**) scans
  the task string for a URL and injects a navigate as a *synthetic step 0*, written into
  history with `model_output.action = initial_actions`. `history.model_actions()` walks
  every item, so the URL appears there whether the model chose it or the library did. Fixed
  by grading only `step_number >= 1`, passing `directly_open_url=False`, and asserting the
  task string contains no URL at all.
- **"no `/tmp/browser-use-user-data-dir-*` larger than 4 KB"** — that directory is minted
  empty by a pydantic field validator; the 718 MB `copytree` beside it fires on a branch the
  CDP-attach path never reaches. **The condition cannot fail.** Replaced by a scan of the
  run's whole `TMPDIR`, a diff of the real `/tmp`, and a check that no `Cookies` /
  `Login Data` / `Web Data` / `Local State` file exists anywhere beneath either.
- **"measured first-request prompt tokens"** — `history.usage` is **zeros by construction**
  on the Ollama path, so browser-use cannot answer this at all. The number exists only in
  ollama's own `prompt_eval_count`, which `ChatOllama` discards. Hence `browsin/proxy.py`.
  And it must come from the *same request that carried the screenshot*, or the measurement
  rewards a run that sent no image.

**Gate output, 2026-09-04, run 4** (`runs/phase4_gate_run4.log`; card had the model resident
from a previous run's idle linger, so the acquire was warm):

```
preflight: free=5238 foreign=2741 ghost=0 committed=0 tenants=['ollama:qwen2.5vl-32k:7b'] leases=0
   (foreign_mib 2741 not gated: 1 tenant(s) resident, which inflates it above the idle baseline)
fixture on http://127.0.0.1:41603  nonce='VEYV4'
chrome pid=61980 bind=127.0.0.1 Chrome/152.0.7977.82
lease granted in 0.1s  endpoint=http://192.168.1.111:11434 served num_ctx=32768

  [PASS] G0  the agent was constructed and the run was attempted
  [PASS] G1  a pre-existing tab changed URL, and a model action did it
         moved=['4D4582B34A4715F637E44269EC57ABA8'] clicked_at_step>=1=True
         initial_actions=None task_contains_url=False
  [PASS] G2  first vision prompt under 60% of the served window
         prompt_eval_count=7412 budget=19660 (0.60 x served 32768)
         request text chars=24997 chars/6=4166 seq=1
  [PASS] G3  a real screenshot reached the model
         1 image(s), dimensions=[[1906, 742]] sha256[0]=e302922b8ff4e2a7
  [PASS] G4  the model read the canvas nonce (pixels only, not in the DOM)
         nonce='VEYV4' in final_result=True is_done=True last_step=4
         final_result="The access code is 'VEYV4'."
  [PASS] G5  no response shows truncation or a non-stop finish
         7 chat exchange(s); offenders=none
         prompt_eval_counts=[7412, 7473, 7499, 7581, 7607, 7593, 7619]
  [PASS] G6  the box ollama log records no truncation for this run
         scanned 16014 new bytes from offset 101420730; hits=none
  [PASS] G7  the CDP port stayed bound to loopback, and the LAN refused
         sampled binds=['127.0.0.1'] across 6 samples; LAN connect refused=True
  [PASS] G8  every LLM call went through the proxy
         llm_host=http://127.0.0.1:11434 proxy_upstream=http://192.168.1.111:11434
         lease_endpoint=http://192.168.1.111:11434 chat_requests=7 history_steps=4
  [PASS] G9  the lease was held continuously across the run
         6/6 samples held, expected >= 4 for a 28.8s run
  [PASS] G10 model still resident at the booked window after the run
         {'ok': True, 'served_num_ctx': 32768}
  [PASS] G11 temp footprint contained, and no profile data copied
         gettempdir=<run>/tmp  entries=[('browser-use-downloads-28e1dbd9', 0),
         ('browser-use-user-data-dir-gu3odvu3', 0),
         ('browser_use_agent_…', 73469), ('downloads', 0)]
         oversized=[] sensitive=[] new in real /tmp=[] cwd=[]
  [PASS] G12 browsin owns SIGINT/SIGTERM, and no judge call was made
  [PASS] G13 the owner's Chrome survived and lost no tabs

PHASE 4 GATE (normal): 14 of 14 passed
```

**Re-run after the G5 fix, and this is the authoritative pass.** Run 4 above was graded by a
G5 that could not fail (see the controls below), so it does not get to be the record. Run 6,
against the corrected gate, passed **14 of 14** independently — a different nonce, a
different tab id, and a noticeably worse path through the task:

```
  [PASS] G4  nonce='HLK3R' in final_result=True is_done=True last_step=8
  [PASS] G5  9 chat exchange(s); offenders=none
         prompt_eval_counts=[7414, 7475, 7501, 7567, 7703, 7840, 8063, 8209, 8373]
  [PASS] G9  12/12 samples held, expected >= 10 for a 59.5s run
PHASE 4 GATE (normal): 14 of 14 passed
```

Run 6 is the more honest picture of what this model does, and it is worth reading closely.
It took **8 steps to run 4's 4**, spent three of them waiting and three clicking nothing,
and its final answer *describes the page wrongly* — "the page did not change… the page is
empty" — while nonetheless ending "The access code shown is `'HLK3R'`". Both halves are
explicable and neither is a plumbing fault: page two's DOM genuinely has no interactive
elements, so the serialised DOM it reasons over really does look empty, while the canvas it
was *looking at* really did say HLK3R. **The DOM said nothing and the pixels said the code,
and the model reported the code.** That is the vision path working, and it is also a preview
of exactly the "confident nonsense" §5 Phase 5 warns about: a run whose narrative is wrong
and whose answer is right, which no schema check and no `done` flag can tell apart.

**The prompt budget is not tight.** 7,412 tokens of a served 32,768 — **23%** — with the
full-page screenshot, the 12,101-byte `format=` schema and eight history items included, and
it grew only to 7,619 over seven calls. §3.1's arithmetic was pessimistic; the binding
constraint on this workload is not the context window.

**The gate was then made to fail, six ways, before it was trusted.** A gate that has never
failed has not passed — phases 0 and 2 produced four false passes between them, and this
project does not get to skip the step. Each control changes exactly one thing:

| `--mode` | what it breaks | must fail | observed |
|---|---|---|---|
| `blank-canvas` | the canvas is empty; the page is otherwise byte-identical | **G4 only** | **G4 failed, G3 still passed** — 13/14 |
| `no-vision` | `use_vision=False` | G3, G4 | G2, G3, G4 failed — 11/14 |
| `direct-url` | a URL in the task + `directly_open_url=True` | G1 | G1, G4, G12 failed — 11/14 |
| `no-proxy` | `ChatOllama` pointed straight at the box | G8 | G2, G3, G5, G8 failed — 9/14 |
| `signals` | `enable_signal_handler=True` | G12 | G12 failed — 13/14 |
| `oversize` | a 400 000-char prompt through the same proxy | G5 | G5 failed *(only after a fix — see below)* |

`blank-canvas` is the one that matters most: **G3 passed and G4 failed on the same run.** A
screenshot was still on the wire, of the right dimensions, and the model still could not
produce a code that was not in the pixels. That is the difference between "an image was
sent" and "the model read it", and no other check in the gate can tell them apart.

**The `oversize` control found a real hole in the gate itself, on its first run.** G5
*passed* when it had to fail. The rule read `if pec is not None and pec >= served`, so an
exchange whose response carried **no** `prompt_eval_count` was silently skipped — the exact
"I could not check, so I passed" shape `browsin/lease.py` and `tools/phase2_gate.py` already
forbid, reintroduced in a new file. What made it visible is worth recording: an over-window
request comes back **200 OK carrying only `{"model": …, "done": …}`** — no
`prompt_eval_count`, no `done_reason`, no `message`. A missing count *is* the truncation
signal on this build, not the absence of one. Corrected, and re-run:

```
  [FAIL] G5  no response shows truncation or a non-stop finish
         1 chat exchange(s); offenders=['seq1:done_reason=None',
         "seq1:no prompt_eval_count in response (keys=['done', 'model'])"]
```

**Two earlier runs failed, and both failures were mine rather than the system's.** Run 1
returned all-FAIL from a `str.replace` that patched the *first* matching line — inside the
`--mode oversize` branch — so `session.stop()` was spliced in before `session` existed, and
a blanket `except Exception` swallowed the resulting `NameError` and returned early. Run 3
was refused outright by the interlock on `foreign_mib`, correctly per the rule as written and
wrongly in substance (§7). Both are recorded because the gate *did the right thing both
times*: it failed loudly rather than passing, and `G0 the run was attempted` was added so a
crash before the agent exists reads as one cause rather than thirteen symptoms.

**The demonstration run, on a real site, got the answer wrong — and that is the most useful
result of the day.** `tools/browse.py` against `en.wikipedia.org/wiki/Main_Page`, asked for
*"the title of the article featured in the 'From today's featured article' section"*:

```
ANSWER: The title of the featured article on the Wikipedia main page is 'Egyptian goose'.
6 steps in 47s, done=True
  step 1: ['scroll']   step 2: []   step 3: []   step 4: ['input']
  step 5: ['input']    step 6: ['done']
prompt tokens per call: [11572, 11658, 11684, 11670, 11696, 11682, 11708, 10758, 11552]
images per call:        [1, 1, 1, 1, 1, 1, 1, 1, 1]
```

The correct answer was **"the second and final season of *1923*"**, and it was on screen,
above the fold, when the run started. Every part of the machinery did its job — nine calls,
an image on every one, ~11.6k tokens against a 32,768 window (**35%**, on a page far busier
than the fixture), no truncation, lease held and returned.

**What the model did is worth stating exactly, because it is not hallucination.**
"Egyptian goose" *is* on that page — in the **"Recently featured:"** list underneath
*today's featured **picture***, at the very bottom. Step 1 was a `scroll`, away from the
featured article it had already been shown, and it then reported a real string from the
wrong section as the answer. This is §7's "confident nonsense" in its purest form: schema
valid, `done=True`, `is_done()` true, a fluent sentence, and wrong. **No gate that trusts
the agent's own `done` could catch it** — which is precisely why Phase 5's conditions are
machine-checkable equality against a known answer, and why §5 says never the agent's own
`done` action.

It also sharpens what Phase 5 must measure. Two of six steps were empty-action failures and
two more were `input` into the search box, so the *useful* action rate here was 2 of 6. That
is the number Phase 5 exists to establish over repeated runs, and this single run is not it.

**What Phase 4 leaves for Phase 5, stated as a warning rather than a result.** The gate
passes, and every *plumbing* condition in it is deterministic — the lease, the proxy, the
loopback binding, the temp footprint and the residency assertions do not depend on what the
model says. G1 and G4 do. They require the model to click the right thing and to read five
large black characters, and this is a 7B model: run-to-run variance is real and Phase 5's
whole job is to measure it rather than assert it. Do not read "14 of 14" as "reliable"; read
it as "the pipeline is sound and the first end-to-end run succeeded". The completion-rate
number belongs to Phase 5, over repeated runs, and §5's instruction to report it *with no
disqualifying floor on the first pass* is the right posture for it.

### Phase 5 — real tasks, externally verified.

3–5 fixed tasks with **machine-checkable** success conditions — final URL matches, extracted
string equals expected. **Never the agent's own `done` action.** Grammar-constrained
decoding means the model *cannot* produce malformed output, so "it finished and the JSON
parsed" tells you precisely nothing about whether it understood the page. The failure mode
here is not a crash, it is **confident nonsense**, and `max_failures` never trips on it.

**Gate:** element-reference resolution rate (did the index the model chose exist on the
page?) ≥85% · error-repair recovery ≥85% · **task completion measured and reported with no
disqualifying floor on the first pass.** State plainly that the first two thresholds are
adopted from `local_Model/PLAN.md`, not an industry bar.

### Phase 6 — `browsin` on PATH, config, skill.

`ln -s ~/Documents/claude/browser_use_local_model_GPU/bin/browsin ~/.local/bin/browsin`, so
edits are live with no reinstall — the `clonin` precedent. `~/.config/browsin/config`.
Then `~/.claude/skills/browsin/SKILL.md`.

**Gate:** it runs correctly from `bash -lc` with **no interactive shell** — which also
re-exercises the DRM-display trap (§7) — and a *fresh* session given "use the local model
to check this web page" reaches for the lease before it touches `:11434`. Skill
descriptions resolve at session start, so the session that writes the skill cannot observe
its own triggering.

### Phase 7 — later, each independently optional

- **A filtering reverse proxy**, promoted from `proxy.py`: reject any request naming a model
  other than the leased one, and refuse any prompt over `num_ctx`. Converts warden's
  cooperative contract into an enforced one at the exact boundary where this project
  otherwise breaks the card's accounting. ~30 lines on top of what Phase 4 already builds.
- **`warden hold` integration** — if warden_client Phase 2 ever ships, `bin/browsin` should
  detect `$WARDEN_ENDPOINT` and skip its own acquire. The library keeps the in-process
  holder; they are complementary, not duplicative.
- **A second `batch` workload id** for unattended runs, so an overnight eval cannot starve
  the box at `interactive`.
- **A `llama-server` driver** — `local_Model/PLAN.md` prefers llama.cpp for the flags
  (KV-cache quantisation, prefix caching, grammar control) that Ollama hides. That is a new
  `ServiceDriver` plus its own measurement cycle. The escape hatch if Phase 5's gates fail,
  not the starting point.

---

## 6. The disagreement with this machine's own research, stated plainly

`local_Model/PLAN.md` is the canonical record for `cap_local_llm_coding_agent_replacement`
and it is a **negative result**. Two things must be said honestly:

- **It evaluates no 8B at all.** Its smallest agent is `gpt-oss-20b` at 3.1–3.4% on
  Terminal-Bench. So it is not evidence about `qwen2.5vl:7b` driving a browser; it is
  evidence that the *class* has been disappointing, measured carefully, on this hardware.
- **The machine index separately records `cap_scheduled_web_page_check` as a negative
  result** — "35 agents / 1.6M tokens / 71 min / 0 confirmed" — and synergy `syn_035`
  explicitly says to check it *before* another browser-automation attempt. This plan is
  that second attempt. It should read that record in Phase 0 and say what it is doing
  differently.

Where the research genuinely does **not** transfer: its central criticism of Claude Code
against a local endpoint lands on an OpenAI-compatible shim and a 30–52k fixed session
baseline. browser-use is a different harness with a different baseline, which §3.1 measures
rather than inherits. And its ollama-vs-llama.cpp argument is about *flags*, not about
whether the plumbing should exist — `browsin` is agnostic to what is behind the endpoint.

**The honest framing of this project is therefore: the deliverable is a measured number,
possibly a bad one, plus reusable plumbing.** If an 8B VL model cannot drive a browser,
that is a finding and the lease library, the proxy and the skill all survive it.

---

## 7. What will bite you

Ordered by how expensive the surprise is.

- **`:11434` answers without a lease, and always will.** warden leases ollama *models*, not
  the ollama server. An unleased call does not fail, it **succeeds** — loading weights
  outside warden's book. Never read a successful curl as evidence you are doing it right.
- **The card is not quiet by design.** `clonin-frontdoor.service` is active and public. Any
  procedure gated on `tenants: []` is racing an inbound HTTPS request from a stranger.
- **The `done` action is not evidence of anything.** Constrained decoding guarantees
  schema-valid output whether or not the model understood the page.
- **`llm_timeout` is 75 s and `ChatOllama(timeout=…)` does not lift it.** On timeout the
  request is abandoned but the GPU keeps generating, holding VRAM.
- **`max_history_items` defaults to `None`** — unbounded. It is the fastest-growing term in
  the context budget and it overflows you mid-task, silently.
- **browser-use leaks `/tmp` directories — but not on the import, and not the one this
  plan named.** ~~`import browser_use` leaks a `/tmp` directory, permanently.~~ **Corrected
  2026-09-01 by measurement:** a bare `import browser_use` creates **zero** directories,
  because `browser_use/__init__.py` uses a lazy-import table and never reaches
  `browser.session`. And the `mkdtemp` validator does not fire for the module-level
  `DEFAULT_BROWSER_PROFILE = BrowserProfile()` either, because **pydantic does not run an
  `after` field validator on an unset default** — `BrowserProfile().user_data_dir` is
  `None`. What is actually true:
  - `from browser_use import Agent` creates one empty `/tmp/browser-use-downloads-<8hex>`
    per process, from the `set_default_downloads_path` validator, which `mkdir`s
    unconditionally. 4 KB, and unavoidable short of moving `TMPDIR`.
  - Each `BrowserSession(...)` / `Agent(...)` **construction** creates one
    `/tmp/browser-use-user-data-dir-<8>`, because `BrowserProfile.model_config` sets
    `revalidate_instances='always'`, so the profile is re-validated on the way into the
    session and *then* the validator fires. Per construction, not per import.
  - The count that started this bullet — **32 dirs, 2.2 GB** — was 29 empty 4 KB stubs
    plus **three 712 MB copies of the real Chrome profile**, cookies and `Login Data`
    included, sitting world-unreadable but undeleted in `/tmp`. All 32 removed in Phase 0;
    `tools/sweep_tmp.py` is the sweep, and it covers both prefixes.
- **One outbound call has no kill switch at all.**
  `browser/watchdogs/aboutblank_watchdog.py:180` injects
  `img.src = 'https://cf.browser-use.com/logo.svg'` into the overlay it paints on
  `about:blank` tabs. No env var gates it, and it is issued by the **browser**, not by
  Python — so the four-name zero-cloud block does not touch it and neither would a
  filtering proxy on `:11434`. "Zero cloud API calls" is true of the LLM path and of
  telemetry; it is not yet true of the browser. Phase 4 has to decide whether to block the
  host at the browser or accept it and say so.
- **`beta/service.py:4578` calls the version check with no `BROWSER_USE_VERSION_CHECK`
  gate.** The mainline `Agent` honours the switch; the beta service does not. Do not use it.
- **There are four `/tmp` families, not one, and the biggest count is the one nobody
  named.** `browser-use-downloads-*` is created on *every* `BrowserProfile` construction —
  142 of them against 10 user-data-dirs on this VM — plus `browser_use_agent_<uuid>_<epoch>`
  per `Agent()`, and `browseruse-tmp-*`. **CDP attach does not avoid them**: passing
  `browser_session` makes `browser_profile` ignored, but the profile objects are still
  built. The only real fix is `os.environ['TMPDIR'] = …` as the first statement of the
  entry point, before any `browser_use` import — measured to relocate all four.
- **`user_data_dir=None` is not "no profile"** — it is a fresh temp profile, and passing it
  explicitly is not the trigger either way: `session.py` filters `None` kwargs out before
  the profile is built. Any path containing `chrome` triggers the 718 MB copytree, one-way,
  and the match is the naive substring `'chrome' in str(user_data_dir).lower()` — which
  `~/.config/browseruse/profiles/chrome-default`, the profile §4.4 attaches to, contains.
- **Display detection answers from DRM, not X.** With `DISPLAY` unset, `xrandr` fails but
  the DRM enumerator still returns 1914x916 — so browser-use picks `headless=False` and
  then hangs. This is exactly what a cron job, a systemd unit or a non-login ssh gets.
- **A dead browser is a silent 30-second stall** with the real stderr in an undrained pipe.
- **`max_actions_per_step` does not shrink the grammar** — prompt substitution plus post-hoc
  list truncation. The tokens were already generated.
- **`save_conversation_path` is a directory in 0.13.8**, not a file prefix.
- **`directly_open_url=True`** scans the task string for a URL and injects a navigate as an
  initial action. The agent may go somewhere you did not ask for.
- **Never send `keep_alive: 0`.** That is warden's own eviction verb. `gpu-box/SKILL.md`
  still recommends it and the superseded plan hands out the exact curl.
- **Never hand-start anything warden owns, and never `taskkill` a tenant** — it bypasses
  eviction verification, so warden books a ghost and under-admits for 900 s.
- **Lease-loss detection has a 30-second floor and no bridge lowers it.**
  `_Heartbeat._run` is `while not self._stop.wait(self.interval)` — it sleeps the whole
  interval *before* beating, and only a beat's 404 sets `lost_event`. warden has no push
  channel of any kind. `WardenClient(heartbeat_interval_s=5)` looks like the fix and does
  nothing: the client uses `lease.heartbeat_interval_s or default`, and warden always
  sends 30. The **only** lever is a shorter `ttl_s`, through `min(interval, ttl_s/3)`.
- **`lost_event` is not the fastest revocation signal available, only the only *push-ish*
  one.** Polling `GET /v1/leases/{id}` observes a revocation at *your* cadence rather than
  the heartbeat's, because the lease view goes terminal immediately. `lease.py` does not do
  this today — the gate's bar is "within one heartbeat" and it clears that — but Phase 4 is
  where it starts to matter, because a browser step can run for minutes against a model
  that is already gone. Note the cost: every `/v1/status` and `/v1/leases` call pumps
  warden's engine under its lock and can run `nvidia-smi` (cached 0.2 s), so a 5 s cadence
  is fine and a 1 s one from several processes is not.
- **`lost_event` never fires when warden is merely unreachable.** The heartbeat swallows
  `WardenUnreachable` as transient by design, and one such beat can occupy the heartbeat
  thread for ~240 s (four attempts at a 60 s timeout, plus backoff). Losing warden and
  losing the lease are different events, and only the second one is observable.
- **`Task.cancel()` is cooperative and cannot preempt a blocking call.** An agent parked
  inside `asyncio.to_thread()` around a synchronous POST to ollama does not see the cancel
  until that POST returns, and a step that catches `CancelledError` broadly swallows it
  outright. `lease.py` re-fires the cancel once a second, ten times, and stops the instant
  the holder acknowledges — because a repeat-cancel that outlives the acknowledgement
  re-marks the task as cancelling and interrupts the release itself.
- **Do not blanket-convert `CancelledError` into a lease error.** `asyncio.run` turns an
  *untouched* `CancelledError` back into the `KeyboardInterrupt` it came from, so
  rewriting a cancellation you did not cause destroys the caller's signal. Convert only
  the ones you raised, and `uncancel()` only those.
- **`$WARDEN_ENDPOINT` turns obligation 2 off, and nothing sets it.** The short-circuit in
  §4.2 — "use the endpoint given, skip acquire, and say so" — means this process holds no
  lease, runs no heartbeat, and therefore has *no revocation channel at all*, while
  `:11434` keeps answering exactly as it does after any eviction. `Card.check()` now fails
  closed on that path rather than returning quietly, and the "say so" is a WARNING rather
  than an INFO, because under default logging (root at WARNING, no handler) an INFO record
  is dropped and the branch produces no output whatsoever. Nothing in this repo or in
  warden sets the variable; today it can only come from a stale export.
- **`Agent(enable_signal_handler=...)` defaults to `True`, and its second Ctrl-C is
  `os._exit(0)`** (`browser_use/utils.py:290`). `os._exit` runs no `atexit` at all, so
  browser-use's handler does not merely fight the lease holder's — it defeats warden's own
  last-resort release. `enable_signal_handler=False` is not a preference for a lease
  holder, it is required.
- **A cancellation the body swallows is worse than one it never gets.** Agent loops catch
  `CancelledError` broadly. A body that outlasts every cancel and then returns *normally*
  produced a clean exit from `hold()` while everything after the revocation ran against an
  unloaded model. `lease.py` now checks `held.lost` after a normal body exit too. The
  repeat-cancel cadence is the same trade-off from the other side: re-firing every second
  chopped up the body's own async cleanup, so it is 5 s × 3 — 15 s of room against a 30 s
  heartbeat, and a body needing longer is cut short on purpose.
- **An empty-string env var is not "unset" to browser-use, and is not harmless.** The
  parser is `os.getenv(name, default).lower()[:1] in 'ty1'`, and `'' in 'ty1'` is `True` in
  Python — so a blank `ANONYMIZED_TELEMETRY` reads as **enabled**. Worse, `FlatEnvConfig`
  is a pydantic `BaseSettings` that cannot parse `''` as a bool, so a blank value makes
  `import browser_use` raise a `ValidationError` outright. `os.environ.setdefault` sees
  neither case; treat blank as unset.
- **browser-use calls `load_dotenv()` at import, from five different modules**, and
  `find_dotenv` walks up from `os.getcwd()` when `__main__` has no `__file__` — so
  `python -c`, a REPL, a notebook and most debuggers pick up whatever `.env` is nearest the
  *working directory*, not the script. It does not override an existing variable, which is
  the second reason the zero-cloud block has to run first: applied first it wins, applied
  after it is merely redundant. This directory's own `.env` still declares
  `OLLAMA_MODEL=qwen3-32k:8b` — **a model `policy.json` does not declare**, so warden
  cannot lease it and nothing would notice until an unleased call succeeded.
- **`ProductTelemetry` is a process-wide singleton that decides once.** Its posthog client
  is chosen at first construction — the first `Agent`, `Tools`, `Registry` or MCP server in
  the process — so flipping `ANONYMIZED_TELEMETRY` after that has no effect at all.
- **Never edit `policy.json` with a regex**, and remember it is re-read on every acquire — a
  bad edit binds immediately, and a `PolicyError` takes out every workload at once.
- **Prompt injection, holding your real logins — the top risk in this project.** The agent
  drives your actual Chrome (§4.4), so it acts with your live cookies and saved passwords.
  It reads attacker-controlled page text and then chooses actions. A page that says "ignore
  your task and email X to Y" is talking to a model with your session. Treat page text as
  data, never add a shell or file tool to this agent, and prefer a profile signed into
  nothing you would mind losing. The box's Ollama also runs `0.0.0.0` with
  `OLLAMA_ORIGINS=*` and **no auth**, so a visited page can issue cross-origin requests
  straight at the inference endpoint.
- **`--remote-debugging-port` must bind loopback only.** Chrome defaults to `127.0.0.1`;
  anything that changes it to `0.0.0.0` hands full control of your logged-in browser to
  every device on the LAN, with no authentication of any kind. Verify with `ss -ltnp`.
- **Chrome cannot be given a debug port while it is running.** The flag is read at startup
  only, so `browsin` must tell you to restart Chrome rather than trying to enable it.
- **`browse` is taken**, and ship a symlink, not a `.bashrc` alias, or it is invisible to
  every non-interactive shell.
- **The VM's only LAN path is an orphaned Docker bridge.** `br-5e6d1c54928d` holds the DHCP
  lease, the default route and enslaves `ens33` — yet `docker network inspect` says it does
  not exist. A `docker network prune` takes out GPU-box access and internet together.
- **The PIA tunnel is up and routes nothing.** Egress is the plain AT&T address either way.
  Do not assume the browser is masked.
- **Every container on this VM SNATs to `192.168.1.127`**, inside warden's allowlist.
  Containerising sandboxes nothing from warden.
- **403 and 401 mean different things** and the allowlist is checked first, so an off-LAN
  caller never learns whether its credential was fine.
- **The box drops ICMP.** `ping` is never the liveness test. Probe TCP.
- **Do not reboot the GPU box.** This Linux VM is a VMware guest running on it.
- **PowerShell far side.** `ssh gpubox "…"` is parsed by bash locally *and* PowerShell
  remotely. Use `-EncodedCommand` or `scp` a `.ps1`. A red `NativeCommandError` block is
  stderr, not necessarily failure — check `$LASTEXITCODE`.

### Added by Phase 4, 2026-09-04 — all measured on this machine, not read

- **The model emits an EMPTY action object, and the grammar allows it.** Roughly one step
  in three, `qwen2.5vl-32k:7b` returns `"action": [{}]` when it wants to finish. Every field
  of the action union is optional, so `{}` satisfies the `format=` grammar — and then
  matches no member of the union, so pydantic reports
  `Input should be a valid dictionary or instance of …ActionModel [input_value=PydanticUndefined]`
  against **all fifteen members at once**. That signature means a structurally empty action,
  *not* a wrong choice of action and not a broken schema: a one-shot reproduction
  (`tools/diag_done.py`) confirmed the same schema, sent to the same model, round-trips and
  validates perfectly on a short prompt. browser-use has two separate handlers for it —
  "Model returned empty action. Retrying…" inside `_get_model_output_with_retry`, and
  `Invalid model output format` counted against `max_failures` — so runs recover and
  complete. **Budget steps for it.** `MAX_STEPS` of 8 absorbed three occurrences in the run
  that passed. This is a small model filling in a 12 kB rigid form, not a defect in this
  repo, and it is the strongest argument in the file for eventually shrinking the action
  registry or testing `flash_mode`.
- **Chrome's browser process rewrites its own `/proc/self/cmdline` into one NUL-free
  string.** It sets its process title, so the usual `split('\0')` yields a *single* element
  holding the whole command line, while its renderer/GPU/zygote children keep proper NUL
  separation. A token-membership test therefore returns False for the exact process that
  owns the debug port and True for the children — precisely inverted. This silently disarmed
  the "Chrome is already running on this profile" guard until it was measured;
  `browsin/browser.py` now normalises to a string and matches on argument boundaries.
- **`foreign_mib`'s idle baseline only means anything on a card with no tenants.** With
  `ollama:qwen2.5vl-32k:7b` resident from a previous run's idle linger, `foreign_mib` sits
  stable at **2740** — above the 2200–2600 idle range recorded above — and stays there. That
  is the CUDA context of a legitimately resident tenant, which warden books under `cost_mib`,
  not the leaked in-flight load the rule exists to catch. Gating on the absolute number
  regardless of tenancy produced a false refusal; the check is now scoped to a quiet card.
- **`Tools(exclude_actions=[…])` silently no-ops on a name that does not exist.**
  `Registry.exclude_action` appends the name and only logs, at DEBUG, when it actually
  deletes something. `extract_structured_data` — the name an earlier analysis confidently
  recommended excluding — **does not exist in 0.13.8**; the action is `extract`. Excluding
  the wrong name leaves the hazard live while the caller believes it is gone.
  `browsin.agent.build_tools` verifies against the live registry and raises instead.
- **Under CDP attach the process never exits on its own.** browser-use's CDP auto-reconnect
  keeps the loop alive after the work finishes, so a script that just falls off the end of
  `main()` hangs forever. `await session.stop()` is required — and is safe here, because its
  browser-kill branch is guarded by `is_local and _subprocess` and both are false on the
  attach path. It also ends `StorageStateWatchdog`'s 60-second poll of the owner's cookie
  jar, which **is** running: contrary to the natural reading of the source, binding the
  profile to the session re-runs the `user_data_dir` validator, so `user_data_dir` is never
  None and the watchdog is always enabled.
- **browser-use adopts `page_targets[0]`, which is not the foreground tab.** It is
  dict-insertion order of concurrently-attached targets. On the `chrome-default` profile an
  Adblock Plus "what's new" tab has already come first once, on the very first launch.
  Select the tab by URL and dispatch `SwitchTabEvent`; never trust the index.
- **Launch Chrome straight onto the target URL, never onto a new-tab page.** `connect()`
  navigates every new-tab-page target to `about:blank` *and mutates the cached URL*, so the
  `TabCreatedEvent` that follows matches `about:blank` exactly and `AboutBlankWatchdog`
  paints a full-viewport black overlay, retitles the tab, and makes **Chrome** fetch
  `https://cf.browser-use.com/logo.svg`. Starting on a real page means none of it fires.
  `--host-resolver-rules="MAP cf.browser-use.com ~NOTFOUND,…"` is the belt to that brace —
  it is the only lever, since the request comes from the browser and no environment variable
  reaches it. Chrome shows a permanent "unsupported command-line flag" banner as a result;
  cosmetic, but the owner will see it.
- **`use_judge` defaults True and costs one more `/api/chat` after `done`,** carrying up to
  ten screenshots — very likely the largest prompt of the run, on the leased card, and
  invisible in `history`. `llm_timeout` covers the empty-action **retry** as well as the
  first call, so it must fit two full prefills, not one.
- **`pkill -f <pattern>` matches the killing shell's own argv.** It killed this session's
  own tool call, exit 144, mid-diagnosis — the same self-inflicted failure the `gpu-box`
  notes already record. Kill by pid, after matching `/proc/<pid>/cmdline`.
- **`… | tee file` makes the pipeline's exit status `tee`'s**, so a gate that correctly
  returned 1 reported success. Redirect instead, or set `pipefail`.

---

## 8. Open questions for the owner

All eight of the original questions are now settled. Recorded here so the reasoning is not
lost, and so a later reader can see which were decisions rather than findings:

| Question | Settled |
|---|---|
| Measurement window | warden stays up; `clonin-frontdoor` down ~10 min |
| Gate the public front door for it | yes |
| `qwen2.5vl:7b` or `minicpm-v:8b` | `qwen2.5vl:7b`, fallback `minicpm-v:8b` |
| `num_ctx` | 32768 |
| clonin interlock | **reopened and re-settled 2026-09-04**: warn and ask per run. The measurement removed the margin it was moot on — see §10 |
| The 829 MB Chrome profile | **attach to the real Chrome over CDP** (§4.4) |
| The two stale project-scoped skills | **deleted 2026-09-01** — see §10 |
| Does this project get an artifact | yes; published |

The next thing that needs an owner is not a question but a **measurement**: Phase 1's single
`/api/chat` call, which decides whether the whole approach is viable.

---

## 9. Rollback

Every step undoes cleanly, and none of it touches warden's engine.

| Step | Undo |
|---|---|
| Policy entry | Restore `policy.json.pre-browsin-backup` on the box. No restart — next acquire re-reads |
| Pulled vision model | `ollama rm qwen2.5vl:7b` on the box. Frees the disk; nothing else references it |
| `bin/browsin` | `rm ~/.local/bin/browsin`. Nothing else on the machine shells out to it |
| `browsin` skill | `rm -r ~/.claude/skills/browsin` |
| Superseded plan | It is preserved in this directory, not deleted |
| `/tmp` leak | `rm -rf /tmp/browser-use-user-data-dir-*` — safe when no agent is running |
| Deleted `diagnose-gpu-box` skills | `git -C ~/Documents/claude/clonin checkout -- .claude/skills/` (and the same in `clonin-next`) |
| The whole thing | `rm -rf ~/Documents/claude/browser_use_local_model_GPU` |

---

## 10. Status log

**2026-09-01 — planned.** Nothing built. Live state surveyed and recorded in §2 by a
21-agent survey-and-adversarial-verify pass; every load-bearing claim below was re-checked
by a second agent against the filesystem or the live box, and the numbers that survived are
marked by provenance rather than asserted.

Nine corrections to beliefs held at the start, all verified rather than remembered:

1. **This venv is browser-use 0.13.8, not 0.9.7.** Playwright was dropped entirely for
   `cdp_use` + `browser_harness`; `max_steps` moved to `run()` with a default of **500**;
   `run()` returns an `AgentHistoryList`, not a string; `use_vision` and `use_judge` both
   default **True**. And `Agent.__init__` ends in `**kwargs` it never reads, so the old
   snippet constructs cleanly and silently ignores half of what it is told.
2. **browser-use's Ollama path uses `format=<json schema>` constrained decoding, never
   `tools=`.** Tool-calling capability is not a model-selection criterion. This reverses the
   superseded plan's Phase 4 entirely and widens the vision shortlist to every VL model.
3. **`num_ctx` is settable from Python** via `ollama_options`. No derived Modelfile is
   needed — but it now couples the client's config to the policy entry's measured cost.
4. **`llm_timeout` resolves to 75 s for any Ollama model name**, and `ChatOllama(timeout=…)`
   does not lift it. Probably the first thing that would have broken a vision run.
5. **`max_history_items` defaults to `None`** — unbounded. The largest un-costed term in the
   context budget.
6. **PostHog telemetry is on by default** and ships the task string, every URL and the full
   action history. Directly contradicts the project's headline goal.
7. **Zero vision models are pulled** and Ollama's multimodal path has never been exercised
   on this box. `qwen2.5-vl` is not a registry name — `ollama.com/library/qwen2.5-vl` 404s;
   it is `qwen2.5vl`. The superseded plan's pull command could never have worked.
8. **The browser cannot contend with the model for VRAM** — this guest has no GPU
   passthrough. CPU contention with the VMware host is real and unbudgeted.
9. **`warden hold` does not exist.** `warden_client/` is one `PLAN.md` in a directory that
   is not a git repo, and `which warden` returns nothing. In-process leasing is not a
   duplication of a shipped thing; it is the only lease-holding code on this machine.

Two corrections the adversarial pass made to its own survey, kept here because they change
the shortlist: **`llava:7b` is LLaVA-1.6 on Mistral-7B with grouped-query attention**
(128 KiB/token, not 512) and is not disqualified on VRAM; and the **+350–700 MiB graph term**
applied to every candidate estimate is unjustified — the single calibration point available
has a *negative* residual. §3.4's table has the term removed and is still an estimate that
`measure_footprints.py` must replace.

**2026-09-01, later — four decisions taken, plan updated.** `qwen2.5vl:7b` over
`minicpm-v:8b`; `num_ctx` **32768**, buying ~1 GB of KV against the fact that overflow on
this stack is silent rather than an error; the Phase 3 measurement runs with **warden up
and `clonin-frontdoor` down for ~10 minutes**. §1, §3.4 and Phase 3 updated; §8 is down
from eight questions to three. One consequence worth watching: at 32k the chosen model is
estimated to *co-reside* with the voice service rather than evict it, which would make the
`interactive`/`may_evict` choice a fallback rather than the normal path — on a 53 MiB
margin derived from a rounded download size, so Phase 3's measurement may take it back.

**2026-09-01, later still — the browser question answered, and two stale skills removed.**
`browsin` will **attach to the owner's real Chrome over CDP** rather than launch one or copy
a profile. That deletes seven of §4.4's original hazards outright — the 718 MB per-run
copytree, the one-way profile copy, the wrong-Chromium default, the `executable_path`
rewrite, the silent 30 s launch stall and the stale `SingletonLock` — and replaces them with
two smaller obligations: probe the debug port before leasing, and never let that port leave
loopback. It also raises prompt injection from "a risk this family does not otherwise have"
to **the top security item in the plan**, because the agent now acts with live cookies and
saved passwords.

Separately, `clonin/.claude/skills/diagnose-gpu-box` and the identical copy in `clonin-next`
were **deleted**. Both were byte-identical 6,452-byte files, mentioned warden zero times, and
still taught the decommissioned `log_relay` / `:8124` path that
`2026-08-29_clonin` retired on 2026-08-30 — the one procedure that makes warden's
`CloninDriver` refuse to manage clonin at all. The corrected global
`~/.claude/skills/gpu-box/SKILL.md` covers the same ground and applies in every directory;
duplication was what let these two drift in the first place. Both repos are git and the
deletion is staged-clean, so §9 restores them in one command.

The clonin interlock question was left to my judgement and I have called it: **no interlock**,
because on §3.4's estimates the chosen model co-resides with the voice service rather than
evicting it. That rests on a 53 MiB margin computed from a rounded download size, so it is
a decision with an expiry date — Phase 3's measurement either confirms it or brings the
question straight back.

**2026-09-01, Phase 1 — PASSED, after two failures that were both about the box, not the
plan.** The gate cleared on the third attempt: browser-use's real 21,980-char / 49-`$def`
`AgentOutput` schema compiles into a working Ollama grammar and returns parseable JSON in
8.1 s. **The largest unreproduced claim in the research is refuted** — qwen3's thinking does
not empty `message.content`; ollama populates thinking *and* content, one completion, no
doubled prefill, and `ChatOllama` needs no patch. Thinking should stay **on**: disabling it
is 2.6x faster and measurably worse, hallucinating a `navigate` and a `click` where the
thinking run correctly emitted a single `done`.

Getting there produced four findings §7 did not have, three of which are properties of this
machine rather than of this project:

1. **A start timeout destroys the load.** warden closing the connection makes ollama abort
   outright, so every retry restarts from zero — a hard loop, not a slow success.
   `qwen3:8b` needs 187.9–190.0 s against a 180 s default and missed by ~8 s, twice, on a
   verified-clean card. `start_timeout_s` raised to **600** on both ollama workloads
   (backup: `D:\warden\policy.json.pre-browsin-timeout-backup`; 4 fields changed, parser
   re-validated, `allocatable_mib` unchanged at 12489, no restart).
2. **Windows Defender is why loads are slow** — real-time on, zero exclusions, scanning
   every read of a 5 GB blob on a 429 MB/s SSD. Owner to apply the exclusion; it is a
   security setting and not mine to change.
3. **SIGTERM leaks a lease**, which §4.2 had credited only to SIGKILL.
4. **A leaked in-flight load is invisible** in both `/api/ps` and warden's tenants, showing
   only as `foreign_mib` climbing 2,230 → 7,336.

One process note worth keeping: the middle diagnosis was **wrong**. A leaked load from the
first failure was blamed for the second, the clean-card rerun disproved it, and only the
`llama-server started in 187.90 seconds` log line settled it. The measurement decided it,
not the reasoning about it — which is the same rule §5 already applies to the agent's own
`done` action.


**2026-09-01, Phases 0 and 2 — PASSED. The repo exists; the card can be held and given
back.** `git init`, the pins, the zero-cloud block, the `**kwargs` guard, `CLAUDE.md`,
`browsin/lease.py`, and three tools: `phase0_gate.py`, `phase2_gate.py` and
`test_lease_offline.py`. Both gates' actual output is pasted in §5 rather than summarised.

**The headline is a finding about gates, not about leases.** Between them these two phases
produced **four false passes**, every one of which looked like a green tick:

1. **The plan's own Phase 0 gate is a false pass as written.** `'posthog' not in
   sys.modules` after importing `Agent` is `True` *with telemetry fully enabled* — the
   import is lazy inside `ProductTelemetry.__init__`, so it happens at Agent
   **construction**. `phase0_gate.py` now runs the literal wording, then constructs
   `ProductTelemetry` and checks the client, then runs the whole probe a third time with
   telemetry ON as a **negative control**, so the two checks are known to differ rather
   than assumed to.
2. **Gate 2 "passed" in about a second** by reading `freed_mib` at the top level of an
   event, where it is `None` — the numbers live under `fields` — and by scanning the whole
   event log, so it matched an `evict_verified` from an earlier run and never waited out
   the 180 s linger.
3. **The standalone `freed` watcher ran by default** with `since_id=0` after the real check
   had already run, matched a degenerate `evict_verified: 0 MiB of an expected 0 MiB` from
   hours earlier, and made one run report both PASS and FAIL for the same gate.
4. **`--only <typo>` skipped every step and printed `PASSED`, exit 0.** Three more of the
   same family were found by adversarial review and fixed before the final run: gate 5
   graded a `LeaseLost` raised during the *acquire* against a clock started before it;
   gate 3 accepted "warden has no record of this lease" — which means warden *restarted* —
   as proof of a release; gate 4 counted any `NotResident`, including "nothing is resident".

Ten findings the design did not have, all measured:

1. **`/api/ps` reports the served context window**, top-level `context_length`, 4096 for
   `qwen3:8b` against an architectural maximum of 40960. Obligation 4 is therefore
   checkable from the client, which §4.2 could only hope for. It is *not* readable from
   `details`, which carries the model's metadata — reading that would turn "I could not
   check" into a confident wrong answer.
2. **Residency is not the same as being on the card.** `/api/ps` returns `size` and
   `size_vram`; their ratio is exactly how `ollama ps` computes its GPU% column. Ollama
   silently splits a model across CPU and GPU when it does not fit, and warden books the
   lease as VRAM either way — so obligation 3 has to compare the two numbers or it
   certifies a card that is not holding the model.
3. **Lease-loss detection has a 30-second floor** and no bridge lowers it:
   `_Heartbeat._run` sleeps the whole interval *before* beating, warden has no push
   channel, and `WardenClient(heartbeat_interval_s=…)` is never consulted because warden
   always sends 30. `ttl_s` is the only lever. Polling `GET /v1/leases/{id}` *is* faster
   and is left to Phase 4, where a step can run for minutes.
4. **A cancel during the acquire leaves a live, heartbeating lease.**
   `AsyncWardenClient.lease` drives a sync context manager through
   `asyncio.to_thread(cm.__enter__)`; cancelling that await detaches the coroutine while
   the worker thread completes the acquire and parks at the yield, and `cm.__exit__` never
   runs. `hold()` issues the DELETE itself on that path.
5. **`Task.cancel()` is cooperative**, so a body parked in a blocking call — the normal
   state of a browser agent mid-step — does not see it until that call returns, and a body
   that catches `CancelledError` broadly can outlast the cancel and **return normally after
   the lease is gone**. That silent success was the worst bug in the module. Re-firing the
   cancel defeats it, but re-firing *too fast* chops up the body's own async cleanup: 5 s ×
   3 is the settled trade-off, and a body needing longer is cut short on purpose.
6. **browser-use's own signal handler ends in `os._exit(0)`** (`utils.py:290`), which runs
   no `atexit` at all. `enable_signal_handler=False` is not a preference for a lease
   holder, it is required.
7. **An empty-string env var is not "unset" to browser-use.** `'' in 'ty1'` is `True`, so a
   blank `ANONYMIZED_TELEMETRY` reads as *enabled* — and `FlatEnvConfig` cannot parse it as
   a bool, so `import browser_use` raises outright. `os.environ.setdefault` sees neither.
8. **`import browser_use` leaks nothing** — §7's bullet was wrong in both its trigger and
   its mechanism. There are four `/tmp` families, the one that actually accumulates is
   `browser-use-downloads-*` (142 against 10 user-data-dirs), and **CDP attach does not
   avoid any of them** because the profile objects are still constructed. `TMPDIR` is the
   only real mitigation. 2.1 GB swept, three of those dirs being 712 MB copies of the real
   Chrome profile, cookies and `Login Data` included.
9. **One outbound call has no kill switch**: `aboutblank_watchdog.py:180` injects
   `cf.browser-use.com/logo.svg` into the `about:blank` overlay. It is issued by the
   *browser*, so neither the env block nor a proxy on `:11434` touches it. "Zero cloud" is
   true of the LLM path and of telemetry; it is not yet true of the browser, and Phase 4
   has to decide that.
10. **A warm Windows file cache hides the cold-load problem.** After one load-and-unload
    cycle the same acquire goes ACTIVE in ~15 s rather than ~190 s, because the 5 GB blob
    and its Defender scan are both cached. The gpu-box skill already warns not to read a
    second read as a disk measurement; the same applies to a second *lease*. The Defender
    exclusion is still unapplied, so the 190 s figure stands for a genuinely cold load.

One process note, and it is the same one Phase 1 recorded. Stopping a contaminated gate run
stranded a lease, because every `hold()` inside the harness passes `handle_signals=False`
on purpose — the gate must measure `lease.py`'s signal behaviour in a *child*, not have it
fire mid-measurement — which left the harness itself taking a bare SIGTERM. The fix is one
line (`SIGTERM` → `SystemExit`, so warden's `atexit` runs), and the lesson is the one §7
already states: **the thing that holds the lease is not always the thing you remembered to
protect.** Card returned to `committed 0 · ghost 0 · free ~13910` after every run.

**2026-09-03 — the Defender exclusion is applied. Item 10 above is now stale.** Owner ran
`Add-MpPreference -ExclusionPath "D:\Models\OLAM"` on the box, elevated. Verified two ways,
not just read back from config: `Get-MpPreference | Select ExclusionPath` lists
`D:\Models\OLAM` (alongside pre-existing `D:\hf-cache`, `D:\video`), and the path is the real
Ollama store, not a typo'd/dead one — `blobs/` + `manifests/registry.ollama.ai/...` for
`qwen3:8b`, `qwen2.5-coder:14b`, `nomic-embed-text`. Then, with Ollama not resident
(`/v1/status` showed only `clonin`, no ollama tenant), a fresh `ollama:qwen3:8b` lease was
taken from this VM to force a genuine cold load: acquired in 22.0 s, and
`D:\warden\logs\ollama-server.log` shows `llama-server started in 16.31/16.37 seconds` at
the matching timestamp. That is the *cold* number now — no warm-file-cache confound — well
under both the 180 s the model used to miss by ~8 s and the 600 s `start_timeout_s` raised
in Phase 1. The 190 s cold-load figure throughout §2/§3/Phase 1/item 10 no longer describes
this box's current state.

**2026-09-03, correction — the exclusion is worth keeping, but it did not cause the
speed-up, and the entry above overstates its case.** Pulled every `started in` line from
`D:\warden\logs\ollama-server.log`: **42 load events** between 2026-08-29 and 2026-09-03.
The distribution kills the causal claim.

| window | load time |
|---|---|
| 2026-08-29 → 08-30 (13 events) | 1.3 – 22.9 s |
| **2026-09-01 20:48 – 21:00 (4 events)** | **179.9 – 190.0 s** |
| 2026-09-01 21:41 – 22:53 (6 events) | 13.3 – 14.6 s — **no exclusion yet** |
| 2026-09-03 17:00, post-exclusion | 16.3 s |

The 190 s was **one twelve-minute window** that resolved on its own two days before the
exclusion existed, and 16.3 s after the fix is indistinguishable from 13.8 s before it. So
"190 s → 16 s because of the exclusion" is not supported; loads had already returned to ~14 s
by 21:41 on 09-01 with nothing changed that affects load speed. The most economical
explanation is Defender's cached scan verdict being invalidated — a signature update does
this — forcing one full scan of the 4.87 GB blob, after which the verdict is re-cached.

**Keep the exclusion anyway, for the reason that actually applies to Phase 3.** It does not
make steady-state loads faster; it removes the re-scan penalty entirely, and the load most
exposed to that penalty is the **first** load of a **newly pulled** file — which is exactly
`qwen2.5vl:7b`. So the exclusion is worth having *before* the Phase 3 pull rather than as a
fix for slowness already observed.

Two things this corrects elsewhere: §7's Defender bullet and the `gpu-box` skill both said
"every read of a ~5 GB blob gets scanned", which the 42-event distribution refutes — the
skill has been rewritten to say a slow load clears itself and should not be chased. And the
methodological point is the same one this project keeps relearning: **a single measurement
is not a distribution.** The 190 s figure was real, reproducible within its window, and
still not a property of the box.

**2026-09-04 — Phase 3's measurement expired the interlock decision, exactly as flagged.**
The vision workload is declared live as `ollama:qwen2.5vl-32k:7b`, `cost_mib` **8375**,
measured with `measure_footprints.py` on a quiet card with `clonin-frontdoor` gated — the
procedure §5 asked for, followed. The number is the problem:

| | MiB |
|---|---|
| measured `cost_mib` | 8,375 |
| admission need, ×1.10 | 9,213 |
| book on an empty card | 12,489 — **fits** |
| book with clonin resident (3,726) | 8,763 — **does not fit, by 450** |

§3.4 estimated ~7,514 raw / ~8,265 after the factor, so the estimate was **861 MiB low —
11%** — and the 53 MiB of headroom it claimed never existed. That estimate came from
dividing ollama.com's rounded download string by 1048576 with the graph term deliberately
omitted; the omission was defensible on one calibration point and is now falsified on a
second. **Treat every remaining row of §3.4's table as similarly optimistic** — in
particular `minicpm-v:8b`, the declared fallback, whose ~6,755/~7,741 figures were built
the same way.

The consequence is behavioural, not arithmetic: an `interactive` browsing lease **evicts
the public voice service**, every time it is loaded, rather than sitting beside it. The
"no interlock" call in the entry above was explicitly made with an expiry date attached to
this measurement, and the measurement has collected it.

**Settled by the owner 2026-09-04: warn and ask, per run.** `browsin` checks for a clonin
tenant before it acquires, names who would be displaced, and requires an explicit choice —
`--evict` to proceed, `--wait` to queue behind the 120 s idle linger. It never evicts a
public service silently. §1, §4.4 and §8 updated; this is now an implementation obligation
for Phase 6's CLI, not a documentation note.

**2026-09-04 — Phase 4 PASSED. The model drives the browser.** A local vision model on the
LAN GPU box read a page it had never seen, clicked a button, moved a tab it did not create,
and reported a five-character code that existed *only* as canvas pixels. Full gate output,
the six control runs that prove the gate can fail, and the corrected conditions are in §5
Phase 4. Four new modules: `browsin/browser.py`, `browsin/proxy.py`, `browsin/fixture.py`,
and `build_llm` / `build_session` / `build_tools` / `build_agent` in `browsin/agent.py`.

The measured numbers that change how the rest of this file should be read:

| | measured | what it displaces |
|---|---|---|
| first vision prompt | **7,412 tokens** | §3.1's arithmetic was pessimistic — this is **23%** of the served 32,768, with the screenshot, the 12,101-byte `format=` schema and 8 history items all included |
| prompt growth over 7 calls | 7,412 → 7,619 | `max_history_items=8` holds it flat; unbounded history was the right thing to fix |
| screenshot on the wire | 1906×742 PNG, sha256 verified | proves the vision path end to end, independently of what the model then said |
| warm lease acquire | **0.1 s** | vs 24.4 s from cold-in-linger, vs the 190 s of one bad window on 09-01 |
| whole run | 28.8 s, 4 steps, 7 LLM calls | on a warm card |
| real `/tmp` litter | **zero**, across nine runs | `TMPDIR` + `tempfile.tempdir` before the import relocates all four families |

**A methodological note this session earned the hard way, three times.** Every failure in
Phase 4 was a *checking* failure rather than a system failure, and each was found by the
gate rather than by inspection:

1. A `str.replace` patched the first matching line instead of the intended one, and a
   blanket `except Exception` swallowed the resulting `NameError` — 13 checks failed at once
   with no traceback. `G0 the run was attempted` now exists so this reads as one cause.
2. The `foreign_mib` interlock refused a legitimate run, because an idle-card baseline was
   being applied to a card with a tenant resident. Right rule, wrong scope.
3. **G5 could not fail.** It skipped any response with no `prompt_eval_count`, so the
   oversize control passed. This is the same "I could not check, so I passed" shape that
   `browsin/lease.py:297` and `tools/phase2_gate.py:234` were written to forbid — and it was
   reintroduced in a brand-new file by the same author on the same day. **The rule is not
   "remember not to do this"; it is "run the control".** Nothing else would have found it.

Two smaller corrections of the same kind, both from measurement rather than reading:
`pkill -f` killed its own shell (exit 144), and `cmd | tee log` reported *tee's* exit status,
so a gate that correctly returned 1 looked like a pass. Both are in §7.

**2026-09-05 — the owner ran `tools/browse.py` by hand against Wikipedia and hit "confident
nonsense" that turned out to be two separate things, only one of which was the model.**

First, a real tooling bug in `tools/browse.py`'s attach path: when Chrome is already running,
`B.attach()` never navigates to `--url` — it just adopts whichever open tab's URL contains the
same host (browse.py:79-109). A prior run had left the dedicated Chrome profile sitting on an
unrelated article (`en.wikipedia.org/wiki/In_the_News`, a real but wrong page — a 1970s CBS
kids' news show, not the Main Page's "In the news" box), and every subsequent run silently
drove that stale tab instead of the Main Page, with no error. The workaround used below was
`systemctl --user stop browsin-chrome` before each run, forcing `B.start(args.url)` to launch
fresh. **Closed later the same day:** `tools/browse.py` is now a thin alias of
`tools/test.py one`, which starts a fresh Chrome on `--url` for every run and never attaches;
the attach path is gone from every entry point (see the consolidation entry below).

Second, once that contamination was controlled for (fresh Chrome, verified on the real Main
Page each time), the model's *reading* was measured correct **3 for 3**: every trial's
step-1 `memory` field named the right answer from the screenshot alone. The actual defect was
step discipline — two of the three trials then spent 6-7 more steps retyping that same
already-correct answer into unrelated `input` elements (one drifting via a stray `click` to
`en.wikipedia.org/wiki/Freddie_Mercury` and back) before finally calling `done`. Right answer
either way, 8 steps and ~70s instead of 1 step and ~10s. This matches `qwen2.5vl-32k:7b`'s
known trouble formatting a clean `done` (§10, Phase 4/1236) — it isn't new, but this is the
first time it was isolated from a comprehension failure and fixed rather than filed.

The fix, tested as a one-line `extend_system_message` before landing it: *"If the answer to
the task is already visible on the screen, call the `done` action immediately... Do not call
`input`, `click`, or any other action first to double-check."* Three more clean trials, same
task, same page:

| | steps (3 trials) | correct |
|---|---|---|
| before | 1, 8, 8 | 3/3, but 2 of 3 padded with dead steps |
| after | 1, 1, 1 | 3/3, immediately every time |

Confirmed a fourth time through the real `tools/browse.py` entry point (not just the throwaway
test copy): 1 step, 6s, correct. **Landed as `DONE_PROMPTLY_MESSAGE` in
`browsin/agent.py`'s `PLAN_DEFAULTS`**, so every caller of `build_agent` gets it without
having to know to ask.

The methodological point this session re-earned: a couple of ad-hoc runs is anecdote, not
measurement (§5's own warning, ignored for about an hour before ground-truth-checking the
actual Wikipedia page caught it). The first "nonsense" answer in this session
("Gloria Steinem in 1977") was in fact *correct* — "Gloria Steinem" was the real headline,
"in 1977" was the caption on her accompanying photo — and calling it a model failure without
checking was as much a checking failure as anything in Phase 4's list above.

Separately raised and **rejected** this session: replacing `qwen2.5vl-32k:7b` with a bigger
vision model (the owner asked about something described as "Kimi K3 VL"). `Kimi K3` itself is
a ~2.8T-parameter model, nowhere close to fitting this card. The small member of that family,
`Kimi-VL-A3B` (MoE, 2.8B active params), is a real Ollama/GGUF-compatible model, but at
~9.8 GB of weights alone it would likely land at or past this card's ceiling once the same
~10-15% measured overhead this project keeps finding is added (§10, Phase 3) — and the
measured problem turned out not to be a comprehension gap a bigger model would fix anyway.
Not pursued further; `minicpm-v:8b` remains the named fallback if `DONE_PROMPTLY_MESSAGE`
ever stops being enough.

**2026-09-05, later — starting Phase 5 for real: a 6-task checklist stressing scrolling,
multi-hop clicks, legitimate typing, a non-Wikipedia site, precise scroll targeting, and the
"confident nonsense" honesty failure specifically. First task in found a second real bug and
one test-design mistake of my own — recorded separately so they are not confused for each
other.**

**A second real bug, independent of `DONE_PROMPTLY_MESSAGE`: the model inverts scroll
direction while narrating the opposite.** Task 1 (scroll a long article to reach a section
below the fold) drove `qwen2.5vl-32k:7b` to send `scroll(down=False)` — scroll **up** — on 6
of its first 7 steps, every one captioned "scrolled down to find the section" in its own
`memory` field. The schema is not ambiguous (`"down=True=scroll down, down=False scroll up"`,
default `true`), so this is the model's own semantic mix-up, not an unclear tool. Confirmed
by the printed effect (`Scrolled up 742px`, six times) matching the wrong parameter, not a
logging artifact. Tested a fix the same way as `DONE_PROMPTLY_MESSAGE`: one added paragraph —
scroll `down=true` moves toward unseen content, `down=false` moves backward, and check
whether the screenshot actually changed before repeating a scroll. Retest, same task: **10 of
10** scrolls correctly used `down=True`. **Not yet landed in `browsin/agent.py`** — tested
only in a scratch copy — because the one run available to confirm it also *works* (reaches
the target, not just points the right way) was invalidated by the next finding.

**My own mistake, caught by checking rather than assumed: Task 1's target does not exist on
the page I pointed it at.** I asked for the "Wreck" section of `Sinking_of_the_Titanic` and
graded the run against 1985/Robert Ballard, sourced from a *different* article,
`Wreck_of_the_Titanic`. Fetching `Sinking_of_the_Titanic`'s actual table of contents after
the fact: **Background, Ice warnings, 14 April 1912, 15 April 1912, Aftermath, Casualties and
survivors, Notes, References, Bibliography, External links — no "Wreck" section at all.** The
model never found it because it is not there, and it did not fabricate an answer either time
— both runs ended `success: False` with an honest "not found yet" rather than a confident
wrong guess. That is the correct behavior for content that genuinely is not on the page, and
it means Task 1 as designed cannot tell the scroll-direction fix's real effectiveness from a
false negative. **Same rule this file keeps re-learning, this time caught before it was
written up as a model failure: verify the target before grading the run against it.** Task 1
needs a real target — a long page with a fact at a known, checkable depth — before it can be
re-run as a measurement rather than retired as a lesson.

**All 6 tasks completed, 2026-09-05, after landing the scroll-direction fix and re-deriving
Task 1's target. 5 of 6 passed against independently-verified ground truth; 1 genuine
"confident nonsense" failure, caught live rather than inferred.**

| # | Task | Result | Detail |
|---|---|---|---|
| 1 | Scroll ~12.8k px into `Wreck_of_the_Titanic`, name the rust bacterium | **PASS** | "Halomonas titanicae", correct, 8 steps. Target depth confirmed by a live DOM query (`getBoundingClientRect`) before grading — see above for why that mattered |
| 2 | Click the bolded name in "In the news", report their birth year | **PASS** | Gloria Steinem → 1934, correct. Clicked the right link first try, but then spent 1 `input` + 4 `scroll` steps after already landing on the answer before calling `done` — `DONE_PROMPTLY_MESSAGE` is weaker right after a navigation than when nothing has changed |
| 3 | Use the search box for "Ada Lovelace", report her birth year | **PASS** | 1815, correct. Typing still works when the task genuinely calls for it — the fix does not over-suppress real form use. But clicked an inert `<span>` 5 times in a row believing search had not happened yet, even after landing on the right article — a milder case of not re-reading the current screenshot |
| 4 | Hacker News, report the #1 story | **FAIL** | Step 1 correctly read "Actively exploited sandbox RCE in all Chromium versions" (verified top story) into its own memory, then typed it into an unrelated input instead of finishing (the old habit, on an unfamiliar site). Step 2 **replaced the correct answer with a different, real story** — "Portal by Spotify cut my Claude Code token usage by 90%", actually position #13 — and called `done` with `success: True`. Textbook confident nonsense: fluent, schema-valid, and wrong, with no signal distinguishing it from the correct answer next to it |
| 5 | Hacker News, report the 15th story by position | **PASS** | "Show HN: Open-Source eInk Bike Computer", exact match. Same stray `input` habit as task 4, but this time the second step did not overwrite the correct answer — confirms task 4's failure is intermittent, not a guaranteed break |
| 6 | Report on a Main Page section that does not exist ("Weather forecast") | **PASS** | Correctly said the section does not exist, `success: False`, 1 step, no fabrication. This is the regression check that mattered most given today's other change — `DONE_PROMPTLY_MESSAGE` did not make the model more willing to guess when it has nothing to report |

**What this adds up to.** Reading/comprehension held up in every case that stayed on one
page — 6 of 6 correct when graded fairly. The one failure was not a reading error, it was an
own-answer overwrite triggered by an unnecessary interaction (typing into something the task
never asked it to touch), and it only happened once in six chances to reproduce the same
pattern (tasks 4 and 5 shared the same stray-input habit; only 4 corrupted the answer). Two
things worth doing before trusting this pattern further: (1) tighten
`DONE_PROMPTLY_MESSAGE` so that once a `memory` field already states the answer, no further
action is generated in that same or a later step before `done` — right now the instruction is
descriptive ("call done immediately") rather than something the grammar or a post-hoc check
enforces; (2) run tasks 4-style single-page reads several more times each on an unfamiliar
site specifically, since Wikipedia-only testing cannot surface this — it never happened once
across ten-plus Wikipedia runs this session, only on the first two Hacker News attempts.
`minicpm-v:8b` remains untried; nothing in today's results points at the model choice as the
active ingredient in the one failure — it holds an answer correctly in `memory` and then
overwrites it, which looks like an agent-loop/instruction-following gap rather than a
comprehension one.

### Phase 5 — the gate exists, and the first real number is 72%

**2026-09-05 — `tools/phase5_gate.py` and `tools/test_phase5_grade_offline.py` — both folded
into `tools/test.py` the same evening; see the consolidation entry below.** Hand-running
tasks and reading logs is what produced the mistakes above; this replaces it. The gate runs a
task table N times under **one lease**, restarts Chrome per run (the attach path was removed
from every entry point later that day), and grades every run against ground truth **the harness fetches itself** — the
ITN lead and the Hacker News front page are scraped immediately before each run, and the
"stable" fixtures are re-verified live so a rotted one reports `FIXTURE-STALE` instead of
being charged to the model. §5's rule is enforced structurally: `grade()` never reads
`is_done()` for correctness.

**First measured completion rate, 6 tasks x 3 reps, graded on ground truth:**

```
  wiki-itn-lead        3/3 correct   avg 1.0 steps, 0.0 wasted actions
  wiki-scroll-deep     3/3 correct   avg 11.7 steps, 0.0 wasted actions
  wiki-search-box      1/3 correct   avg 8.0 steps, 0.0 wasted actions
  hn-top-story         1/3 correct   avg 6.0 steps, 4.0 wasted actions  had-then-lost x1
  hn-15th-story        2/3 correct   avg 8.0 steps, 5.3 wasted actions
  wiki-absent-section  3/3 correct   avg 1.0 steps, 0.0 wasted actions

  completion rate: 13/18 = 72%
```

**Hand-testing had said 83% (5 of 6) the same afternoon. It was luck**, and the gap between
those two numbers is the entire argument for the harness. §10 keeps recording this lesson;
this is the first time the project has had the instrument to avoid it.

**`had_then_lost` is a real, recurring failure mode, not an anecdote.** The detector fires
when the expected string appears in some step's `model_output.memory` and is absent from the
final answer — "understood the page, then threw the answer away", which wants a completely
different fix from "never understood it". Three confirmed occurrences so far, all on
`hn-top-story`, and twice it produced *the same* wrong answer ("The Highest Point in the
Netherlands"), so whatever it is attending to is systematic rather than random.

**Two hypotheses tested against the follow-up probe (4 reps, the two weak tasks):**

1. **`wiki-search-box` is not step-starved.** Raising `max_steps` 8 -> 14 did not convert
   failures into slow successes; the distribution is bimodal — 3, 3 steps when it works,
   **14, 14 when it fails**. A stuck run stays stuck, so budget is the wrong lever. It types
   the query correctly and then re-clicks a search control it has already used, never
   registering that the results page loaded.
2. **Wasted actions correlate with wrong answers, but the evidence is confounded and is
   recorded as a hypothesis, not a result.** Across 31 graded runs, `wasted_actions == 0`
   scores 17/21 (81%) and `>= 1` scores 5/10 (50%) — but essentially every high-waste run is
   Hacker News and every zero-waste run is Wikipedia, so site and waste cannot be separated
   in this sample. The only clean within-task split is `hn-top-story` itself: 2/2 correct at
   0-1 wasted actions, 1/5 at 3-5. Directionally consistent, far too small to assert.

**The race guard, added after the first batch and immediately earned.** The Hacker News front
page reorders continuously, and a run graded against an expectation fetched 60 s earlier can
fail for reasons that have nothing to do with the model. The gate now re-derives ground truth
*after* each run and marks a disagreeing run `RACY` — excluded from the rate, not counted
against it. It fired on its first outing: `Actively exploited sandbox RCE in all Chromium
versions` -> `AI handles incidents, engineers lose touch with their systems` mid-run. An hour
earlier that would have been written up as a model failure.

**A real bug the gate surfaced that no task was looking for: generation was uncapped.** A
probe run stalled for ~7 minutes with the lease healthy and the proxy silent — one request in
flight, the model emitting a multi-thousand-line malformed JSON object. §4.3 specifies
`ollama_options={... "num_predict": 1024}`; `build_llm` shipped only `num_ctx`, so nothing
bounded generation. Measured across **309 logged generations**: median **162** tokens, p90
206, p99 250 — and a max of **17,328**. Exactly **2 of 309** exceeded 1024, and both were
runaways. Uncapped, one of those occupies the full 600 s `llm_timeout` while §3.3's warning
applies (the request is abandoned, the GPU keeps generating); capped, it truncates into a
parse failure the existing retry clears in seconds. `num_predict: 1024` is now set, at 4x the
measured p99, so it cannot clip a legitimate step. **The plan had this right and the
implementation dropped it** — the same class of silent divergence as §10's `max_steps` and
`tool_calling_method` findings, and the argument for gates that measure rather than read.

**Not yet re-measured with the cap in place.** The 72% and the probe both predate it, so the
next batch is the before/after. Do not cite the cap as an improvement until that exists.

### Consolidation — `tools/test.py` is the one way to test

**2026-09-05, evening — four files became one entry point, and the diagnosis moved out of the
chat and into the tool.** The owner's ask, verbatim in spirit: *one simple way of testing; keep
what I was doing (read the log, find the mechanism, fix, measure); let it loop.* What was
built, from a 64-run failure census and an independent design critique rather than from
memory:

- **`browsin/grade.py`** — runtime-derived truth (ITN lead, HN position, a phrase verified live
  in an article), the six-task table, and `grade()` over a plain history dict with an
  **outcome taxonomy** (`CORRECT / WRONG_ANSWER / NO_ANSWER / HONEST_MISS`, plus `RACY`,
  `FIXTURE_STALE`, `TRUTH_UNAVAILABLE`, `SETUP_FAILED`, `ABORTED` excluded and counted). Pure,
  stdlib-only; imports nothing from `browser_use`.
- **`browsin/diagnose.py`** — the detectors, one per census pattern
  (`docs/failure-census-2026-09-05.txt`; three labelled instrumentation), the per-run
  DIAGNOSIS block (trace with viewport and has-answer columns, patterns with evidence, the
  decisive screenshot and the frame before it, a TEMPLATE mechanism sentence), the
  batch ROLLUP and a NEXT footer that names the top fixable pattern and the exact probe
  command, Wilson intervals on every rate, Fisher exact for `compare`. Pure as well.
- **`tools/test.py`** — `run` (table × reps, one lease, fresh Chrome per run, graded,
  diagnosed, `results.jsonl` appended per run, `--resume`), `one` (same path, graded only with
  `--expect-from`), `diagnose` and `compare` offline, `self-check` (95 controls, no card),
  `guide`. Fixes are recorded **arms** (`--arms default,enforce-read-only | sysmsg:FILE |
  set:KEY=JSON`), interleaved rep by rep; `compare` refuses a verdict under 6 per arm.
  `tools/browse.py` is an alias of `test.py one` — it never attaches, so the morning's
  stale-tab bug is closed by removing the path. `phase5_gate.py` and its offline test are
  gone; `TESTING_PROMPT.md` points at `test.py guide` so the procedure exists once.

**Reviewed adversarially before it was trusted.** Four read-only lenses (detector
correctness, CLAUDE.md hard rules, docs-vs-code, edge cases) produced 57 findings; a skeptic
per lens tried to refute each; 56 stood, all fixed. The two that mattered: **scraped truth
kept HTML entities** — `Fermat&#x27;s` vs the `Fermat's` the model reads — so any Hacker News
title with an apostrophe (2 of 30 that afternoon) would have graded a correct answer as
confident nonsense, and the post-run refetch could not rescue it; and **`ChromeError` is a
`RuntimeError`**, so a Chrome start failure escaped the per-run handler and aborted the whole
batch instead of recording `SETUP_FAILED`. Also from the review: `--evict` now reaches the
acquire (`may_evict`) and not only the preflight; the viewport reader parses browser-use's real
`<page_info>N.N pages above` text (a draft matched "pixels above", which the library never
emits); RACY is assigned only when a WRONG_ANSWER matched neither truth, a final matching the
post-run truth is CORRECT, and an HONEST_MISS stays graded; arms are validated before any card
time; exit codes follow CLAUDE.md's 0/1/2. Self-check grew from 41 to 95 so every detector has
a positive that must fire and a negative that must stay silent.

**The interlock refused every run for an evening, and it was wrong — the same class as
2026-09-04's false refusal.** `foreign_mib` sat **flat at 2805** for 25 minutes on an empty
card. `nvidia-smi` on the box read **2534 MiB**, inside the idle baseline; no `llama-server`
process existed; the GPU contexts belonged to Task Manager, four Snipping Tools, 17 Chrome
processes, Plex and Settings — somebody was using the Windows desktop, which is exactly the
memory warden calls foreign and cannot reclaim. The leak the rule exists for *climbs*
(2,230 → 7,336 on 09-01). `card_preflight` now refuses above a 4000 hard ceiling or on a
rising 15 s trend, and otherwise warns with the numbers and proceeds. Right rule, wrong scope,
again — and the fix is a trend, not a bigger constant.

**First run through the finished tool** — `runs/test-run-20260905-045402`, the whole table
once, arms `default` and `enforce-read-only` (inert on the two non-read-only tasks, skipped):

```
  wiki-itn-lead [default] 1/1 correct (80% CI 38%–100%)   avg 1.0 steps   0.0 wasted
  wiki-itn-lead [enforce-read-only] 1/1 correct (80% CI 38%–100%)   avg 1.0 steps   0.0 wasted
  wiki-scroll-deep [default] 1/1 correct (80% CI 38%–100%)   avg 10.0 steps   0.0 wasted
  wiki-search-box [default] 0/1 correct (80% CI 0%–62%)   avg 14.0 steps   0.0 wasted   HONEST_MISS 1
  hn-top-story [default] 0/1 correct (80% CI 0%–62%)   avg 6.0 steps   4.0 wasted   WRONG_ANSWER 1
  hn-top-story [enforce-read-only] 1/1 correct (80% CI 38%–100%)   avg 2.0 steps   0.0 wasted
  hn-15th-story [default] 1/1 correct (80% CI 38%–100%)   avg 8.0 steps   6.0 wasted
  hn-15th-story [enforce-read-only] 1/1 correct (80% CI 38%–100%)   avg 2.0 steps   0.0 wasted
  wiki-absent-section [default] 1/1 correct (80% CI 38%–100%)   avg 1.0 steps   0.0 wasted
  wiki-absent-section [enforce-read-only] 1/1 correct (80% CI 38%–100%)   avg 1.0 steps   0.0 wasted
  completion rate: 8/10 = 80%  (80% CI 60%–91%)
  of the 2 miss(es), 0 had the correct answer in memory and dropped it
ROLLUP — patterns per task (count of runs the pattern fired in / graded runs)
  wiki-scroll-deep     stuck_narrative 1/1, steps_after_first_seen 1/1, scroll_step_too_small 1/1
  wiki-search-box      repeated_action 1/1, stuck_narrative 1/1, done_only_when_forced 1/1, honest_miss 1/1
  hn-top-story         blank_first_state 2/2, stray_input_on_read_only 1/2, url_changed_on_read_only 1/2, input_side_effect_navigation 1/2, repeated_action 1/2, done_only_when_forced 1/2
  hn-15th-story        blank_first_state 2/2, stray_input_on_read_only 1/2, repeated_action 1/2, stuck_narrative 1/2, done_only_when_forced 1/2, steps_after_first_seen 1/2
  adherence: scroll pages>1 used in 0/12 scrolls; scroll direction inverted in 0 runs
  most frequent fixable pattern across the batch: blank_first_state (4 runs)
```

**What it says, and what it does not yet say.**

1. **`enforce-read-only` — 4/4, 1–2 steps, zero wasted actions — against `default`'s 3/4 with
   10 wasted actions and one WRONG_ANSWER on the same four tasks.** The honesty canary stayed
   CORRECT under the arm. This is the hypothesis the census pointed at (every had-then-lost
   begins with a stray `input`; remove the verb rather than ask nicely) with its first measured
   data point. **One rep is a probe, not a claim** — the tool's own rule 5 wants 8 v 8 and a
   Fisher p < 0.1 before a rate is asserted. That measurement is the next command:
   `tools/test.py run --only hn-top-story --only hn-15th-story --reps 8 --arms default,enforce-read-only --label ab-enforce`.
2. **`blank_first_state` on 4 of 4 Hacker News runs.** With a fresh Chrome, browser-use takes
   its first snapshot before HN has rendered ("Page appears empty"); the model reacts with a
   `scroll` every time, and that scroll is what moved row 1 off-screen before the first real
   read in the default `hn-top-story` failure (trace: step 1 `scroll` from a blank TOP, step 2
   already at BOTTOM). Never seen on Wikipedia. **This is a harness timing problem with a
   harness-side fix** — wait for a non-empty DOM before handing the tab to the agent — and a
   better next target than any prompt edit. Filed here as the next SETUP-side item.
3. **`wiki-search-box` → HONEST_MISS**, the good miss: 11 repeated clicks on the same `<span>`
   *after* the search had already navigated to the article, then an honest "the search term
   has been entered" at 14/14. `stuck_narrative(n=13)` caught it; the stricter
   `stale_narrative_after_navigation` did not, because the eval text differed at the navigation
   step while the memory did not — loosen that detector to memory-only if this recurs.
4. **Adherence counters now surface in the rollup:** `scroll pages>1 used in 0/12 scrolls`.
   The scroll-pages instruction in `DONE_PROMPTLY_MESSAGE` has still never been followed once.
5. **The num_predict cap is still not re-measured at 3 reps.** This run's 45 LLM calls showed 0
   runaway, 0 slow, 0 aborted — consistent with the cap, not proof of it.

Open, in order of value: the 8 v 8 for `enforce-read-only`; a DOM-ready wait before the agent
starts (kills `blank_first_state` by construction); DOM-snapshot truth from the driven tab
(closes the HN race instead of excluding it); the §5 thresholds (element-reference resolution,
error-repair) are still not computed; Phase 6 (`bin/browsin`, config, skill) has not started.
