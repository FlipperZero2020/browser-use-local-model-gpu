# Plan: Browser-Use with Local Ollama Model on GPU Box

## Goal

A single Python project where you type a prompt in the terminal and a real
browser window opens and executes the task — powered entirely by a local model
running on the LAN GPU box (192.168.1.111) via Ollama.  Zero cloud API calls.

```
$ python run.py "go to hacker news and find the top post about AI"
  → Chromium window opens
  → Agent navigates, clicks, reads
  → Result printed to terminal
```

---

## What Already Exists (context for implementers)

| Project | Path | Relevant pieces |
|---------|------|-----------------|
| `browser_use_agent` | `/home/tom/Documents/browser_use_agent/` | Mature browser-use 0.9.7 wrapper. Uses `ChatGoogle` (Gemini). Has persistent Chrome profiles, task system, retry/verification logic. The `Agent` + `Browser` + `ChatOllama` integration pattern is what we need — but stripped of all the UPC/form-filling machinery. |
| `browser-use-2026-08` | `/home/tom/Documents/claude/browser-use-2026-08/` | Google's `computer-use-preview` (Gemini emitting click/type actions against screenshots). Completely different approach — NOT browser-use library. Ignore for this project. |
| `2026-08-09_cline` | `/home/tom/Documents/claude/2026-08-09_cline/` | `claude-local` wrapper that points Claude Code CLI at Ollama. Has the LAN subnet auto-discovery logic at `bin/claude-local` — reuse the GPU-box discovery pattern. |
| `gpu_box_audit` | `/home/tom/Documents/claude/gpu_box_audit/` | Audit of GPU box from 2026-08-23. Key facts below. |
| `local_Model` | `/home/tom/Documents/claude/local_Model/` | Research on local LLM viability. Verdict: 8B models score 24.6% vs frontier 84.7% on coding — browser-use tasks have a similar gap. Expect degraded quality. |

### GPU Box Facts (from audit)

- **GPU**: NVIDIA RTX 4060 Ti, **16 GB VRAM**
- **Free VRAM**: ~6.8 GB idle (other services consume ~9.3 GB)
- **Ollama version**: 0.32.14, reachable at `http://192.168.1.111:11434`
- **Best model available today**: `qwen3-32k:8b` (5.23 GB, Q4_K_M, 32768 ctx, has tool-calling)
- **14B models cannot load** — weights (8.37 GB) exceed free VRAM (6.82 GB)
- **Vision**: qwen3-32k:8b is **text-only**. No vision model currently pulled.
- **Ollama config**: `OLLAMA_ORIGINS=*`, `OLLAMA_HOST=0.0.0.0` (open to LAN)

### Browser-Use Library (v0.9.7) — Key Integration Points

The library has **native Ollama support**:

```python
from browser_use.llm.ollama.chat import ChatOllama
from browser_use import Agent, Browser, BrowserProfile

llm = ChatOllama(model="qwen3-32k:8b", host="http://192.168.1.111:11434")
agent = Agent(task="find the top HN post", llm=llm, use_vision=False)
result = await agent.run()
```

- `ChatOllama` lives at `browser_use.llm.ollama.chat` — takes `model`, `host`, `timeout`, `ollama_options`
- `Agent.__init__` takes `task: str`, `llm: BaseChatModel`, plus ~30 optional params
- `use_vision=False` is required for text-only models (falls back to DOM/accessibility-tree extraction)
- `browser_profile` can set a persistent Chrome user data dir, headless=False, etc.
- The library needs `playwright` browsers installed (`playwright install chromium`)

---

## Critical Risk: Model Quality

**This will work but will be unreliable.**  An 8B text-only model driving browser
automation is at the edge of what's viable.  Expect:

- Simple navigation tasks (go to URL, click a link, read text) → likely works
- Multi-step form filling, search refinement, complex workflows → likely fails
- No screenshot understanding (text-only) → relies entirely on DOM tree parsing

This is the "typical" local-model browser-use experience.  The community
consensus is that models under ~30B struggle significantly, and vision is very
helpful.  The plan includes a phase to pull a vision-capable model to improve
results.

---

## Implementation Phases

### Phase 1: Environment Setup
**Executor**: Sonnet 5, single chat

1. Create Python venv in this project directory
2. Install dependencies:
   ```
   pip install browser-use ollama python-dotenv
   ```
   (browser-use 0.9.7 pulls in playwright, pydantic, httpx, etc.)
3. Install Playwright Chromium:
   ```
   playwright install chromium
   ```
   On Linux Mint, may also need:
   ```
   playwright install-deps chromium
   ```
4. Create `.env` file:
   ```
   OLLAMA_HOST=http://192.168.1.111:11434
   OLLAMA_MODEL=qwen3-32k:8b
   ```
5. Create `.gitignore` (venv/, .env, __pycache__/, etc.)
6. Verify Ollama connectivity:
   ```python
   from ollama import AsyncClient
   client = AsyncClient(host="http://192.168.1.111:11434")
   response = await client.chat(model="qwen3-32k:8b", messages=[{"role":"user","content":"hi"}])
   print(response.message.content)
   ```

**Done when**: venv works, Ollama responds, Playwright Chromium launches.

---

### Phase 2: Minimal Working Script
**Executor**: Sonnet 5, single chat

Create `run.py` — the main entry point.  This is the core deliverable.

**Requirements:**
- Accept a prompt as a CLI argument: `python run.py "your task here"`
- If no argument, enter a simple input() prompt loop
- Connect to Ollama on the GPU box (auto-discover or use .env)
- Create a browser-use `Agent` with `ChatOllama` and run it
- Browser window must be **visible** (not headless)
- Print the agent's result to terminal when done
- Graceful Ctrl+C handling (close browser cleanly)

**Minimal implementation sketch:**

```python
#!/usr/bin/env python3
"""Browser-Use with local Ollama model on LAN GPU box."""

import asyncio
import sys
from dotenv import load_dotenv
import os

load_dotenv()

from browser_use import Agent, Browser, BrowserProfile
from browser_use.llm.ollama.chat import ChatOllama


async def run_task(task: str):
    host = os.getenv("OLLAMA_HOST", "http://192.168.1.111:11434")
    model = os.getenv("OLLAMA_MODEL", "qwen3-32k:8b")

    llm = ChatOllama(model=model, host=host)

    browser_profile = BrowserProfile(
        headless=False,
        # Shared with browser_use_agent so logins carry over
        user_data_dir="~/.config/browseruse/profiles/chrome-default",
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser_profile=browser_profile,
        use_vision=False,  # qwen3-32k:8b is text-only
        max_failures=5,    # local models fail more, be lenient
        max_actions_per_step=2,  # smaller model = fewer actions per step
    )

    result = await agent.run()
    print(f"\n--- Result ---\n{result}")


def main():
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = input("Enter task: ")

    if not task.strip():
        print("No task provided.")
        sys.exit(1)

    asyncio.run(run_task(task))


if __name__ == "__main__":
    main()
```

**Key decisions for the implementer:**
- `use_vision=False` because qwen3-32k:8b cannot process images
- `max_actions_per_step=2` — 8B models get confused with too many parallel actions
- `max_failures=5` — local models produce more malformed outputs
- Shares the browser profile with `browser_use_agent` (`~/.config/browseruse/profiles/chrome-default`) so existing logins carry over
- No retry/verification/scoring machinery — keep it simple

**Done when**: `python run.py "go to example.com and tell me the heading"` opens
Chromium, navigates, and prints the heading text.

---

### Phase 2b: VRAM Pre-Flight Check
**Executor**: Sonnet 5, can be done in the same chat as Phase 2

Before launching the agent, query the GPU box for available VRAM and warn if
it's too low to load the selected model.  Ollama doesn't expose VRAM directly,
but we can check whether the model is loadable:

1. **Approach A (simple)**: Call `POST /api/show` with the model name to get its
   size, then call `POST /api/ps` to see what's already loaded and how much
   memory is in use.  If the model is already loaded, skip the check.
2. **Approach B (SSH, optional)**: SSH to the GPU box and run
   `nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits` to get
   actual free VRAM in MiB.  Compare against model size.

Wire this into `run.py` as a function called before `Agent()` is created.
If VRAM looks tight, print a warning like:

```
⚠ GPU box reports ~4.2 GB free VRAM. Model qwen3-32k:8b needs ~5.2 GB.
  Other Ollama models may need to be unloaded first.
  Try: curl -X POST http://192.168.1.111:11434/api/generate -d '{"model":"<loaded_model>","keep_alive":0}'
  Continue anyway? [y/N]
```

Use Approach A (Ollama API only, no SSH dependency).  The `/api/ps` endpoint
returns `size_vram` for each loaded model — subtract from total to estimate
headroom.

**Done when**: Starting `run.py` with insufficient VRAM prints a clear warning
instead of hanging on a silent OOM.

---

### Phase 3: GPU Box Auto-Discovery (optional but recommended)
**Executor**: Sonnet 5, single chat

Borrow the subnet-scanning pattern from `claude-local` at
`/home/tom/Documents/claude/2026-08-09_cline/bin/claude-local`.

Create a small `discover.py` utility:
- Scan the LAN /24 for a host with Ollama on port 11434
- Cache the result in `.ollama_host` so subsequent runs skip the scan
- Fall back to `OLLAMA_HOST` env var or the hardcoded 192.168.1.111

Wire it into `run.py` so the script "just works" even if the GPU box's
DHCP lease changes IP.

**Done when**: `run.py` finds Ollama without a hardcoded IP.

---

### Phase 4: Pull a Vision Model
**Executor**: Sonnet 5, single chat (will need to SSH or curl the GPU box)

The biggest quality improvement is switching from text-only to a vision model
so the agent can actually see the page.  Candidates that fit in ~6-7 GB VRAM:

| Model | Size | VRAM est. | Vision | Tool calls | Notes |
|-------|------|-----------|--------|------------|-------|
| `qwen2.5-vl:7b` | ~4.7 GB | ~5.5 GB | Yes | Yes | Best candidate — Qwen VL has strong web understanding |
| `llava:7b` | ~4.1 GB | ~5.0 GB | Yes | Limited | Good vision but weaker tool calling |
| `minicpm-v:8b` | ~4.9 GB | ~5.8 GB | Yes | Limited | Decent vision, may struggle with tool format |
| `gemma3:12b` | ~8.1 GB | ~9 GB | Yes | Yes | Too large for current VRAM headroom |

**Recommended**: Try `qwen2.5-vl:7b` first.

Steps:
1. Pull the model on the GPU box:
   ```
   curl -X POST http://192.168.1.111:11434/api/pull -d '{"name":"qwen2.5-vl:7b"}'
   ```
   (or SSH in and run `ollama pull qwen2.5-vl:7b`)
2. Verify it loads without OOM — watch VRAM usage
3. Test vision capability:
   ```python
   # Send a base64 image and ask what it shows
   ```
4. Update `.env` to `OLLAMA_MODEL=qwen2.5-vl:7b`
5. Remove `use_vision=False` in `run.py` (or set to `True` / `"auto"`)
6. Re-test the example.com task — agent should now see the page

**VRAM concern**: If other services (ACE-Step, etc.) are running, the vision
model may not fit.  May need to stop other Ollama-loaded models first:
```
curl -X POST http://192.168.1.111:11434/api/generate -d '{"model":"qwen3-32k:8b","keep_alive":0}'
```

**Done when**: Agent uses screenshots to navigate and quality noticeably improves.

---

### Phase 5: Polish and Usability
**Executor**: Sonnet 5, single chat

1. **Prompt loop mode**: After a task completes, ask "Another task? (or q to quit)"
   so you don't restart the browser every time
2. **`--headless` flag**: For tasks where you don't need to watch
3. **`--model` flag**: Override model from CLI (`python run.py --model qwen2.5-vl:7b "task"`)
4. **Logging**: Save conversation history to `runs/<timestamp>/` for debugging
   failed tasks (browser-use has `save_conversation_path` built in)
5. **Status output**: Print each agent step to terminal as it happens
   (use `register_new_step_callback`)
6. **Shell alias**: Add to `~/.bashrc`:
   ```bash
   alias browse='cd /home/tom/Documents/claude/browser_use_local_model_GPU && source venv/bin/activate && python run.py'
   ```

**Done when**: The tool feels usable for quick one-off browser tasks from the
terminal.

---

## File Structure (target)

```
browser_use_local_model_GPU/
├── .env                    # OLLAMA_HOST, OLLAMA_MODEL
├── .gitignore
├── PLAN.md                 # This file
├── run.py                  # Main entry point
├── discover.py             # GPU box auto-discovery (Phase 3)
├── requirements.txt        # browser-use, ollama, python-dotenv
├── runs/                   # Saved conversation logs (Phase 5)
│   └── <timestamp>/
└── venv/                   # Python virtual environment
```

---

## Resolved Decisions

1. **VRAM pre-flight check**: YES — added as Phase 2b.  Query `/api/ps` before
   launching the agent and warn if VRAM looks insufficient.
2. **Browser profile**: SHARE with `browser_use_agent` — use the existing
   `~/.config/browseruse/profiles/chrome-default` so logins carry over.
3. **Model strategy**: Start with `qwen3-32k:8b` (already pulled, text-only,
   `use_vision=False`).  Phase 4 (vision model) is a future improvement, not a
   blocker for getting the tool working.

---

## What This Is NOT

- Not a task/form-filling system (that's `browser_use_agent`)
- Not a Gemini computer-use project (that's `browser-use-2026-08`)
- Not a Claude Code replacement (that's `2026-08-09_cline`)
- Just: prompt → local model → browser does the thing → result
