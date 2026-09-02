# Next session — paste this as the opening prompt

Continue the `browsin` project in `~/Documents/claude/browser_use_local_model_GPU`.

**Read first, in this order, before doing anything:**
1. `PLAN.md` in this directory — canonical design doc *and* status log. §5 has the phases
   and their gates, §7 is "what will bite you". Do not start a second plan file; update
   this one in place as work lands.
2. The `gpu-box` skill, especially the section **"A lease won't start, or a model load is
   crawling"** — it will save you an hour.

**Already done. Do not redo:**
- **Phase 1 PASSED** (2026-09-01). browser-use's 21,980-char `AgentOutput` schema compiles
  into a working Ollama grammar in 8.1 s; qwen3 thinking populates `.thinking` *and*
  `.content`, so `ChatOllama` needs no patch. Leave thinking ON — `think=False` is faster
  and measurably worse.
- warden's `start_timeout_s` raised 180 → 600 on both ollama workloads. Backup on the box:
  `D:\warden\policy.json.pre-browsin-timeout-backup`.
- Two stale `diagnose-gpu-box` skills deleted from `clonin/` and `clonin-next/`.
- All design decisions are settled in §1 — `qwen2.5vl:7b`, `num_ctx` 32768, `interactive`
  priority with eviction, in-process leasing, CDP attach to the real Chrome. Do not
  re-litigate them; if evidence contradicts one, say so and ask.

**Your job: Phase 0, then Phase 2.** (§5 of PLAN.md defines both.)

- **Phase 0** — `git init`; `requirements.txt` pinning `browser-use==0.13.8` and
  `warden @ git+https://github.com/FlipperZero2020/warden.git@v0.3.0`; the zero-cloud env
  block from §4.3 set *before* `import browser_use`; the `**kwargs` guard from §4.3;
  `CLAUDE.md` for whoever edits the repo.
- **Phase 2** — `browsin/lease.py`, the asyncio lease holder (§4.2). Five obligations:
  acquire → `wait_active` → heartbeat; wire `held.lost_event` to **cancellation**; assert
  `/api/ps` shows exactly the leased model; assert `num_ctx` matches the policy entry;
  release on every path **including a SIGTERM handler** (Python runs neither `finally` nor
  `atexit` on default SIGTERM — this leaked a lease during Phase 1).

**The verification loop — this is the point, not a formality.**

For each phase: implement → run its gate → if the gate fails, diagnose from **measurement**
(logs, `/v1/events`, `/api/ps`, `foreign_mib`), fix, re-run the gate → only then move on.
Never mark a phase done without having run its gate and pasted the actual output. If a gate
fails twice for the same reason, stop and report rather than trying a third variation.

Gates, verbatim from §5:
- **Phase 0:** `version('browser-use')` → `0.13.8`; `CONFIG.ANONYMIZED_TELEMETRY` False and
  `'posthog' not in sys.modules` after importing `Agent`; the guard raises `TypeError` on a
  0.9.7-era kwarg such as `planner_llm=`.
- **Phase 2:** a lease held **10 continuous minutes** from asyncio, visible in `/v1/status`
  throughout · release frees within `verify_freed_fraction` and books **no ghost** · Ctrl-C
  mid-hold releases cleanly · the `/api/ps` assertion catches a deliberately wrong model
  name · `lost_event` fires and cancels within one heartbeat when the lease is released out
  from under it.

**Live state as of handoff (re-verify, do not trust):**
- warden `:8130` healthy; card idle — free ~13971, **foreign ~2409 (this is the baseline)**,
  ghost 0, no tenants or leases.
- Chrome is running with CDP on `127.0.0.1:9242`, profile
  `~/.config/browseruse/profiles/chrome-default`. It may not still be up.
- **The Defender exclusion has NOT been applied** (`ExclusionPath` is still empty), so cold
  model loads still take ~190 s. That is expected, not a fault. The owner runs this, not
  you — it is a security setting:
  `Add-MpPreference -ExclusionPath "D:\Models\OLAM"` in an elevated shell on the box.

**Will bite you:**
- A lease `start_timeout` **destroys** the load rather than abandoning it, so retries fail
  identically. Symptom looks like flakiness; it is deterministic.
- `foreign_mib` above ~2,600 means a leaked in-flight load that shows in neither `/api/ps`
  nor warden's tenants. Wait for baseline before trusting any VRAM number.
- Never hand-start anything warden owns; never send `keep_alive: 0` (that is warden's own
  eviction verb); never edit `policy.json` with a regex or through PowerShell string
  quoting — `scp` a `.py` and run it with `D:\warden\venv\Scripts\python.exe`.
- Don't `ping` the box (ICMP dropped) and don't reboot it (this VM is a guest on it).
- Run anything that holds a lease with a **long** timeout or in the background; a 2-minute
  tool timeout SIGTERMs the holder and strands the card.

**Environment:**
```bash
cd ~/Documents/claude/browser_use_local_model_GPU
export WARDEN_URL=http://192.168.1.111:8130
export WARDEN_TOKEN_FILE=$HOME/.config/warden/token   # never WARDEN_TOKEN
venv/bin/python ...
```

When Phase 2's gate passes, update `PLAN.md` §5 and §10 with the measured result, run
`python3 tools/build_artifact.py`, and republish the **same file path** to keep the artifact
URL (https://claude.ai/code/artifact/8f877eff-3915-4231-b27d-0a9e4526fefa). Then stop and
report — Phase 3 pulls a 6 GB model and edits the live policy, so it needs the owner.
