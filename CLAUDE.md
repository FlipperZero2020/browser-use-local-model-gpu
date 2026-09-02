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
