# Next session — paste this as the opening prompt

Continue the `browsin` project in `~/Documents/claude/browser_use_local_model_GPU`.

**Read first, in this order, before doing anything:**
1. `PLAN.md` — canonical design doc *and* status log. §5 has the phases and their gates,
   §7 is "what will bite you" (it has grown a lot), §10 is what has actually been measured.
   Do not start a second plan file; update this one in place as work lands.
2. `CLAUDE.md` in this directory — the rules for editing this repo.
3. The `gpu-box` skill, especially **"A lease won't start, or a model load is crawling"**.

**Already done. Do not redo.** The repo is git now; `git log` is the honest record.

- **Phase 0 PASSED** — pins, the zero-cloud env block, the `**kwargs` guard, `CLAUDE.md`.
  `venv/bin/python tools/phase0_gate.py` re-runs it in about ten seconds.
- **Phase 1 PASSED** (before this session) — the 21,980-char `AgentOutput` schema compiles
  into a working Ollama grammar in 8.1 s. Leave thinking ON.
- **Phase 2 PASSED** — `browsin/lease.py` holds the card from asyncio and gives it back on
  every path. `tools/phase2_gate.py` is the gate (~22 minutes, run it in the background);
  `tools/test_lease_offline.py` is 18 checks that need neither warden nor the card.
- All design decisions are settled in §1. Do not re-litigate them; if evidence contradicts
  one, say so and ask.

**Your job: Phase 3 — but it needs the owner before it can start.** (§5 defines it.)

Phase 3 pulls a 6 GB model and edits the live `policy.json`, so two things are the owner's
call, not yours:

- **The Defender exclusion is still unapplied** (`ExclusionPath` is empty), so a genuinely
  cold load takes ~190 s. It is a security setting and the owner runs it, in an elevated
  shell on the box: `Add-MpPreference -ExclusionPath "D:\Models\OLAM"`. Phase 3 works
  without it — `start_timeout_s` is 600 — it is just slow.
- **The measurement window.** §5 Phase 3 step 3 runs `measure_footprints.py` on the box,
  which stands up a second engine beside the live service and makes it deny admissions for
  the duration, and it wants `clonin-frontdoor` gated so a stranger's page load cannot take
  a lease mid-measurement. §8 records the owner already agreed to ~10 minutes of that;
  confirm it is still fine before you start.

Then, in order: `gemma3:4b` as the cheap architecture smoke test (does this Ollama build
run *any* vision model?), then `qwen2.5vl:7b`, then measure, then declare it in policy.

**The verification loop is the point, not a formality.** Implement → run the gate →
diagnose failures from **measurement** (`/v1/events`, `/api/ps`, `foreign_mib`,
`D:\warden\logs\ollama-server.log`) → re-run. Never mark a phase done in §10 without
pasting the gate's actual output. If a gate fails twice for the same reason, stop and
report rather than trying a third variation.

**And check the gate itself.** Phases 0 and 2 produced **four false passes** between them,
including one written into the plan. A gate that cannot fail has not passed. Before
trusting a new one, make it grade a run you deliberately broke.

**Live state as of handoff (re-verify, do not trust):**
- warden `:8130` healthy; card idle — free ~13910, **foreign ~2450 (this is the baseline)**,
  committed 0, ghost 0, no tenants or leases.
- `ollama:qwen3:8b` is the only model this project has leased. Still no vision model pulled.
- Chrome is **not** running with CDP on `127.0.0.1:9242`. Phase 4 needs it; Phase 3 does not.
- `.env` in this directory still says `OLLAMA_MODEL=qwen3-32k:8b`, which **policy.json does
  not declare**. Nothing browsin wrote reads it, but browser-use calls `load_dotenv()`.

**Will bite you** (the full list is §7; these are the ones that cost time this session):
- A lease `start_timeout` **destroys** the load rather than abandoning it, so retries fail
  identically. Deterministic, not flaky.
- **A warm Windows file cache hides the cold-load problem.** A second lease for a model
  loaded minutes ago goes ACTIVE in ~15 s, not ~190 s. Do not read that as a fix.
- `foreign_mib` above ~2,600 means a leaked in-flight load that shows in neither `/api/ps`
  nor warden's tenants. Wait for baseline before trusting any VRAM number.
- **Release does not free VRAM.** `idle_linger_s` is 180 s for both ollama workloads, then
  the stop, then up to 30 s of verification. Anything asserting on free VRAM straight after
  a DELETE will fail for the wrong reason.
- Never hand-start anything warden owns; never send `keep_alive: 0`; never edit
  `policy.json` with a regex or through PowerShell quoting — `scp` a `.py` and run it with
  `D:\warden\venv\Scripts\python.exe`.
- Don't `ping` the box (ICMP dropped) and don't reboot it (this VM is a guest on it).
- Anything holding a lease runs in the **background** or with a long timeout. And if you
  kill such a thing, check `/v1/status` afterwards — `handle_signals=False` callers take a
  bare SIGTERM, which runs neither `finally` nor `atexit`.

**Environment:**
```bash
cd ~/Documents/claude/browser_use_local_model_GPU
export WARDEN_URL=http://192.168.1.111:8130
export WARDEN_TOKEN_FILE=$HOME/.config/warden/token   # never WARDEN_TOKEN
venv/bin/python ...
```

When Phase 3's gate passes, update `PLAN.md` §5 and §10 with the measured result, run
`python3 tools/build_artifact.py`, and republish the **same file path** to keep the artifact
URL (https://claude.ai/code/artifact/8f877eff-3915-4231-b27d-0a9e4526fefa).
