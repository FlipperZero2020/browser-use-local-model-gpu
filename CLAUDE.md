# browsin — notes for whoever edits this repo

A local vision model on the LAN GPU box drives the owner's real Chrome. The card is
rationed; the model is not ours to start. Almost every rule below exists because
something already went wrong.

## Read these first, in this order

1. **`PLAN.md`** — the canonical design doc *and* the status log. §5 is the phases and
   their gates; §7 is "what will bite you"; §10 is what has actually been built and
   measured, with dates. **Do not start a second plan file.** Update this one in place
   as work lands, and put the measured number in, not the expected one.
2. **The `gpu-box` skill** (`~/.claude/skills/gpu-box/SKILL.md`), especially
   *"A lease won't start, or a model load is crawling"*. It will save you an hour.
3. `PLAN.superseded-2026-08-25.md` is kept for provenance only. It was written against
   browser-use 0.9.7 and is wrong about this venv in ways that still look plausible.

## Environment

```bash
cd ~/Documents/claude/browser_use_local_model_GPU
export WARDEN_URL=http://192.168.1.111:8130
export WARDEN_TOKEN_FILE=$HOME/.config/warden/token   # the file, never WARDEN_TOKEN
venv/bin/python ...
```

`WARDEN_TOKEN_FILE` rather than `WARDEN_TOKEN` on purpose: warden's own `auth.py` names
shell history and skill files as the expected leak path for this credential, and an
environment variable is one `ps` away from both.

## Commands

Nothing here is a `pytest` suite. Each phase's gate is a standalone script that measures the
real thing and prints its own verdict; `tools/test_lease_offline.py` is the only test that
fakes anything. **Exit codes are uniform: `0` passed, `1` failed, `2` refused to start** (the
interlock below), so a gate can be scripted — but `cmd | tee log` reports *tee's* status, and
a failing gate then looks like a pass.

```bash
# Offline. No card, no browser, no network — safe to run at any time, in the foreground.
venv/bin/python tools/phase0_gate.py           # pins, zero-cloud env, the retired-kwarg guard
venv/bin/python tools/test_lease_offline.py    # lease cancel/signal paths against a fake warden
venv/bin/python tools/test.py self-check       # every scorer branch + every failure detector (41)
venv/bin/python tools/test.py guide            # THE LOOP: run → diagnose → fix-as-arm → measure → land
venv/bin/python tools/test.py diagnose runs/test-run-<ts>     # re-render diagnoses from saved history
venv/bin/python tools/test.py compare runs/test-run-A runs/test-run-B

# These hold the real card. Background or a long timeout, and ONE AT A TIME.
venv/bin/python tools/phase2_gate.py                       # ~20 min, six lease checks
venv/bin/python tools/phase2_gate.py --only lost,sigint    # or: sigterm,assert,hold,freed
venv/bin/python tools/phase3_gate.py                       # vision workload: resident, right num_ctx
venv/bin/python tools/phase4_gate.py                       # 14 checks, headed Chrome, canvas nonce
venv/bin/python tools/phase4_gate.py --mode no-vision      # a CONTROL run — it is meant to FAIL
venv/bin/python -u tools/test.py run --reps 3             # the six-task table, graded, DIAGNOSED (~17 min)
venv/bin/python -u tools/test.py run --only hn-top-story --reps 4 --arms default,enforce-read-only
venv/bin/python -u tools/test.py one --url URL --task "…" --expect-from hn:1   # one-off, graded
venv/bin/python -u tools/test.py one --url URL --task "…"                      # one-off, UNGRADED

# The demonstration path is now an alias of `test.py one`: same lease, same fresh Chrome,
# same proxy, same printed prompt-size block. It no longer attaches to a running Chrome.
venv/bin/python -u tools/browse.py --url https://en.wikipedia.org/wiki/Main_Page \
    --task "Find today's featured article and report its title."

# Housekeeping. Neither needs the card.
python3 tools/sweep_tmp.py                     # report only
python3 tools/sweep_tmp.py --yes               # actually delete
python3 tools/build_artifact.py                # regenerate PLAN.artifact.html
```

Six things about running them:

- **`--evict` is the only way past the clonin interlock**, and it exists on `test.py run|one`
  (so on `browse.py`) and `phase4_gate.py`. Without it, a card held by the public voice service
  is an exit-2 refusal with a printed explanation, not a failure. Do not add it reflexively:
  clonin's `idle_linger_s` is 120 s, so waiting is usually the right answer. Never in a loop.
- **`foreign_mib` above 2700 with no tenants is a reason to look, not to refuse.** Measured
  2026-09-05: it sat flat at 2805 for 25 minutes while `nvidia-smi` on the box read 2534 MiB,
  no `llama-server` existed, and Task Manager, four Snipping Tools, 17 Chrome processes and
  Plex held GPU contexts — somebody was using the desktop. The leak this rule exists for
  *climbs* (2,230 → 7,336 on 2026-09-01). `card_preflight` now refuses above a 4000 hard
  ceiling or on a rising 15 s trend, and otherwise warns with the numbers and proceeds.
- **`phase4_gate.py --mode` runs controls** (`blank-canvas`, `no-vision`, `direct-url`,
  `no-proxy`, `signals`, `oversize`). Each one breaks the thing a specific check claims to
  test, so a control that passes everything means the gate cannot fail. They exit `0` when
  their target check fails, which is the point.
- **`test.py` grades against ground truth it fetches itself**, never against the agent's own
  `done`/`success` flag (measured: that flag is noise in both directions). Outcomes are
  `CORRECT / WRONG_ANSWER / NO_ANSWER / HONEST_MISS`; `FIXTURE_STALE`, `TRUTH_UNAVAILABLE`,
  `RACY` (the page moved mid-run) and `SETUP_FAILED` are excluded from the rate and counted —
  none of them is a model failure and none may be written up as one. Read the ROLLUP and the
  DIAGNOSIS blocks before the rate; the rate alone is how an afternoon of wrong conclusions
  happened on 2026-09-05.
- **Every entry point writes `runs/<name>-<timestamp>/`** (gitignored). `test.py` writes
  `results.jsonl` (one line per run, appended as each finishes — a killed batch keeps what it
  measured and is `--resume`-able), per-run `history.json` + `DIAGNOSIS.txt`, `proxy.jsonl`,
  `summary.txt`; the gates write `evidence.json`. `proxy.jsonl` is the *only* place prompt
  sizes exist — see `browsin/proxy.py` for why `history.usage` cannot answer.

## Hard rules

- **Never hand-start anything warden owns**, and never `taskkill` a tenant. A driver
  refuses to manage a process it did not start, so a hand-started backend denies every
  lease for that workload from then on — silently, and outliving your session.
- **Never send `keep_alive: 0` to Ollama.** That is warden's own eviction verb.
- **Never edit `policy.json` with a regex or through PowerShell string quoting.** A regex
  corrupted the live file once (2026-08-30). `scp` a `.py` and run it with
  `D:\warden\venv\Scripts\python.exe`. Policy is re-read on every acquire, so a bad edit
  binds immediately and a `PolicyError` takes out *every* workload, clonin included.
- **Do not `ping` the box** (ICMP is dropped — probe TCP) and **do not reboot it**: this
  Linux VM is a VMware guest running on that machine.
- **Anything that holds a lease runs in the background or with a long timeout.** A
  two-minute tool timeout SIGTERMs the holder and strands the card. See below.
- **`:11434` answers without a lease and always will.** An unleased call does not fail,
  it *succeeds*, loading weights outside warden's book. A successful `curl` is never
  evidence you are doing it right.
- **Never fan `ultracode`/Workflow subagents out across anything that takes a lease or
  drives the real browser.** One GPU slot, one real Chrome window — concurrent agents
  contend for the same lease (most just block or fail) or drive the same window at once,
  and an agent that routes around a busy lease by hand-starting Ollama trips the first
  rule above. Fine for read-only work (code review, research, PLAN.md drafting); never
  for Phase 5 task testing or any live agent run — those stay one at a time.

## The shape of the code

`browsin/` is the library; `tools/` is every entry point. The layering is not cosmetic —
`lease.py`, `browser.py`, `proxy.py`, `fixture.py` and `interlock.py` are **stdlib-only and
do not import `browser_use`**, which is what lets `browsin.lease` hold the card without
paying the seconds, the `/tmp` leak and the telemetry singleton that import costs.

- **`env.py`** — the zero-cloud block, applied on import. Everything else depends on it
  having run first; see the ordering constraint below.
- **`lease.py`** — `async with hold(workload, num_ctx=…)` and nothing else. Wraps
  `warden.client` with the five obligations in its docstring: heartbeat from *acquire*,
  lease-loss cancels the work, assert what is resident, assert the served window, release on
  every path including SIGTERM.
- **`browser.py`** — start or attach the owner's Chrome on CDP port 9242 against the
  browser-use copy profile, and `assert_loopback()` before anything drives it. browser-use
  never launches: passing `cdp_url` gates its whole launch block, which is also why the agent
  structurally cannot close the browser. Process lifetime belongs to this module alone.
- **`proxy.py`** — a logging reverse proxy on `127.0.0.1:11434` forwarding to the leased
  endpoint. It relays the exact bytes that arrived; parsing happens on a copy. Never let it
  normalise `num_ctx`, `model`, `options` or `keep_alive` — that would put warden's book
  wrong rather than merely this run's numbers.
- **`agent.py`** — the only module that imports `browser_use`. **Nothing calls `Agent(...)`
  directly; call `checked_agent(...)`.** `Agent.__init__` ends in a `**kwargs` it never
  reads, so every retired 0.9.7-era parameter constructs cleanly and does nothing at all —
  no `TypeError`, no warning, no log line. That is what let a plan written for 0.9.7 appear
  to work against this venv for weeks. The guard turns it back into an exception.
- **`fixture.py`** — the two-page local site whose nonce is painted into a `<canvas>` and
  exists nowhere in the DOM. It is what separates "a screenshot was sent" from "the model
  read it"; `assert_nonce_not_in_dom()` proves that property rather than assuming it.
- **`interlock.py`** — `card_preflight()`, in its own module because `test.py` and the gates
  need it and importing `phase4_gate` for it would silently redirect the caller's temp files
  into a stray run directory.
- **`grade.py`** — ground truth fetched at run time (`wikipedia_itn_lead`, `hn_story`,
  `wikipedia_contains`), the `Task` table, and `grade()` over a plain history dict. **Pure,
  stdlib-only, no `browser_use`**, so `test.py self-check` and `diagnose` can import it
  without creating a run directory — four stray `runs/phase5-*` dirs on disk are why.
- **`diagnose.py`** — the failure detectors (each corresponds to a pattern in the 64-run census
  of 2026-09-05, `docs/failure-census-2026-09-05.txt`, except three labelled instrumentation),
  the DIAGNOSIS renderer, the ROLLUP/NEXT footer, Wilson intervals and Fisher exact for
  `compare`. Pure as well. The history shape it reads is documented in
  `docs/browser-use-0.13.8-history-api.txt` — read that before touching a detector.

**A new leasing entry point must do what `tools/test.py`'s `_enter_run_dir()` does before it
imports anything from `browser_use`** — and only on the path that leases. `TMPDIR`,
`tempfile.tempdir` and `BROWSER_USE_CONFIG_DIR` are set to the run directory, because one of
the four temp-directory families is created at browser-use's *import* time and
`tempfile.gettempdir()` caches its answer the first time anything asks. Offline paths must
never pay that: `test.py self-check` asserts no run dir was created and `browser_use` was
never imported.

**`requirements.txt` pins with `==` on purpose. Never relax one to `>=`.** An unpinned
`pip install browser-use` is the root cause of the divergence PLAN.md §10 exists to record;
the file's own comments carry the reasoning and the verified warden commit.

## Two ordering constraints in the code

- **The zero-cloud env block must run before `import browser_use`.** `browser_use`
  evaluates `DEFAULT_BROWSER_PROFILE = BrowserProfile()` at import time, so a
  `load_dotenv()` after the import is too late. `browsin/env.py` owns this; import it
  first, or import `browsin` (its `__init__` does it for you). Unmodified, browser-use
  ships the literal task string, every URL visited, the full action history and the
  final answer to PostHog — which is the exact opposite of this project's headline goal.
- **`num_ctx` is one number written in two places.** The client sets it through
  `ollama_options`; warden booked `cost_mib` at whatever window the model was *measured*
  at. If they drift, warden's book is wrong and the card is oversubscribed silently, so
  `browsin/lease.py` asserts the live served window against the configured one and
  refuses to start on a mismatch. When you change one, change the other.

## Releasing the card

`warden.client`'s `lease()` covers `finally`, exceptions and Ctrl-C, and registers an
`atexit` for the way out. It does **not** cover a default `SIGTERM` — Python runs neither
`finally` nor `atexit` for one, and that leaked a lease during Phase 1. Every entry point
that holds a lease installs signal handling; `browsin.lease.hold()` does it for you.

`ttl_s` is the only lever against a `SIGKILL`. Default to 120 s, not policy's 300 —
`ollama:qwen3:8b` lingers a further `idle_linger_s = 180` after the lease closes.

## Phase discipline

Implement → run the phase's gate → if it fails, diagnose from **measurement** (warden's
`/v1/events`, `/api/ps`, `foreign_mib`, `D:\warden\logs\ollama-server.log`), fix, re-run.
Never mark a phase done in §10 without pasting the gate's actual output. If a gate fails
twice for the same reason, stop and report rather than trying a third variation.

`foreign_mib` idles at **~2,200–2,600 MiB**. Above that means a leaked in-flight load that
appears in neither `/api/ps` nor warden's tenants; wait for baseline before trusting any
VRAM number.

## Housekeeping

`import browser_use` leaks a `/tmp/browser-use-user-data-dir-*` per process, permanently.
`tools/sweep_tmp.py` clears them; run it when no agent is running.

`PLAN.artifact.html` is generated — `python3 tools/build_artifact.py` — and gitignored.
Republish it to the **same** artifact URL so the link the owner has keeps working:
<https://claude.ai/code/artifact/8f877eff-3915-4231-b27d-0a9e4526fefa>
