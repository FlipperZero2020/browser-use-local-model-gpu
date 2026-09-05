# Next session — paste this as the opening prompt

Continue the `browsin` project in `~/Documents/claude/browser_use_local_model_GPU`.

**Read first, in this order, before doing anything:**
1. `PLAN.md` — canonical design doc *and* status log. Start with **"What this is, in plain
   language"** at the top if you are cold. §5 has the phases and their gates, §7 is "what
   will bite you" (it has grown a lot, and the Phase 4 group at its end is all measured),
   §10 is what has actually been measured. Do not start a second plan file.
2. `CLAUDE.md` in this directory — the rules for editing this repo.
3. The `gpu-box` skill, especially **"A lease won't start, or a model load is crawling"**.

**Already done. Do not redo.** `git log` is the honest record.

- **Phases 0, 1, 2, 3 PASSED** — pins and the zero-cloud env block; the 21,980-char
  `AgentOutput` schema compiling into a working Ollama grammar; `browsin/lease.py` holding
  the card from asyncio and giving it back on every path; and the vision model
  `ollama:qwen2.5vl-32k:7b` measured at **8375 MiB** and declared in `policy.json`.
- **Phase 4 PASSED 2026-09-04** — the local vision model drives the owner's real Chrome
  over CDP. `tools/phase4_gate.py` is the gate: **14 checks, and six control runs prove it
  can fail.** New modules: `browsin/browser.py` (start/attach Chrome, prove the debug port
  is loopback-only), `browsin/proxy.py` (the logging reverse proxy that is the *only* way to
  see prompt size, since `history.usage` is zeros on this path), `browsin/fixture.py` (the
  canvas-nonce page that separates "a screenshot was sent" from "the model read it").
- `tools/browse.py` is the demonstration path — same lease, same Chrome, same proxy:

      venv/bin/python -u tools/browse.py --url https://en.wikipedia.org/wiki/Main_Page \
          --task "Find today's featured article and report its title."

**Your job: Phase 5 — real tasks, externally verified.** (§5 defines it.) Then Phase 6 puts
`browsin` on PATH with a config and a skill.

**Phase 5's gate needs one thing Phase 4 turned up.** Element-reference resolution rate
cannot be read from `history.errors()`: for `click` and `input`, a missing index returns
`ActionResult(extracted_content=…)`, not `.error`, so `errors()` reports zero on exactly the
failure being measured. Scan `extracted_content` for
`not available - page may have changed`. Run at `max_actions_per_step=1` or the gate cannot
tell an invented index from a DOM that legitimately changed under a second action.

**The thing most likely to shape Phase 5's numbers.** Roughly one step in three, the model
emits `"action": [{}]` — structurally empty, allowed by the grammar because every action
field is optional, matching no union member. browser-use retries and runs still complete,
but they cost extra steps. §7 has the full signature. If Phase 5's completion rate
disappoints, shrinking the action registry or testing `flash_mode` is the first lever, not
a different model.

**Live state as of handoff (re-verify, do not trust):**
- warden `:8130` healthy. `foreign_mib` idles ~2200–2600 on a **quiet** card and sits at
  ~2740 with a tenant resident — that is a CUDA context, not a leak. Only gate on it when
  `tenants` is empty.
- The owner's decisions, 2026-09-04: drive the **browser-use copy** profile
  (`~/.config/browseruse/profiles/chrome-default`, holds real cookies), **stop and ask**
  before displacing the clonin voice service, keep everything in `PLAN.md` rather than a
  second doc.
- `.env` still says `OLLAMA_MODEL=qwen3-32k:8b`, which is not what this project leases.
  Nothing browsin wrote reads it, but browser-use calls `load_dotenv()`.

**Will bite you** (the full list is §7):
- Anything holding a lease runs in the **background** or with a long timeout.
- Never hand-start anything warden owns; never send `keep_alive: 0`; never edit
  `policy.json` with a regex.
- Don't `ping` the box and don't reboot it — this VM is a guest on it.
- `pkill -f` matches the killing shell's own argv. Kill by pid.
- `cmd | tee log` reports **tee's** exit status, so a failing gate looks like a pass.

**Environment:**
```bash
cd ~/Documents/claude/browser_use_local_model_GPU
export WARDEN_URL=http://192.168.1.111:8130
export WARDEN_TOKEN_FILE=$HOME/.config/warden/token   # the file, never WARDEN_TOKEN
venv/bin/python ...
```

When a phase's gate passes, update `PLAN.md` §5 and §10 with the **pasted output**, run
`python3 tools/build_artifact.py`, and republish the **same file path** to keep the artifact
URL (https://claude.ai/code/artifact/8f877eff-3915-4231-b27d-0a9e4526fefa).
