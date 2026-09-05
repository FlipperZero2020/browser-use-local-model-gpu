# Prompt: keep stress-testing browsin

Paste this into a fresh Claude Code session opened at
`~/Documents/claude/browser_use_local_model_GPU`.

---

Read `CLAUDE.md`, then run and read, in full:

```bash
venv/bin/python tools/test.py guide
```

That guide is the single source for how this project is tested — the one entry point, what a
run produces, the outcome taxonomy, the diagnosis block, the run → diagnose → fix-as-arm →
measure → land loop with its stopping rules, how to add a task, and what the tool does not yet
do. Nothing in this file duplicates it on purpose: two copies of a procedure is how the
previous plan went stale.

Then:

```bash
export WARDEN_URL=http://192.168.1.111:8130
export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
venv/bin/python tools/test.py self-check          # must be green before anything touches the card
venv/bin/python -u tools/test.py run --reps 3 --label baseline-$(date +%F)   # background / long timeout
```

Read the ROLLUP and the DIAGNOSIS blocks before the rate. The baseline to beat, and every
measured number since, is in `PLAN.md` §10 — never carry numbers here.

Report by pasting the run's `summary.txt` and the mechanism sentence into `PLAN.md` §10, then
regenerate and republish the artifact to the same URL (CLAUDE.md, Housekeeping).
