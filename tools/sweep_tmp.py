#!/usr/bin/env python3
"""Delete the `/tmp` directories browser-use leaves behind. There are four families.

    python3 tools/sweep_tmp.py            # report only
    python3 tools/sweep_tmp.py --yes      # delete them

Four families, and only the third is the one PLAN.md originally named:

* `profile.py:553` — the `user_data_dir` validator `mkdtemp()`s when the value is None.
  MEASURED 2026-09-01: this does **not** fire on `import browser_use`, correcting
  PLAN.md §7. `DEFAULT_BROWSER_PROFILE = BrowserProfile()` leaves `user_data_dir` as
  None because pydantic does not validate an unset default; it takes an *explicit*
  `BrowserProfile(user_data_dir=None)`, which is what a `BrowserSession` built from
  kwargs does. Those dirs stay ~4 KB stubs.
* `profile.py:858` — `_copy_profile()` copytrees a real Chrome profile into a fresh temp
  dir whenever `user_data_dir` looks like Chrome's. Those are the 712 MB ones; there
  were three of them, in 2.1 GB across 32 dirs, when this VM was first swept. The match
  is the naive substring `'chrome' in str(user_data_dir).lower()`, and
  `~/.config/browseruse/profiles/chrome-default` contains it.
* `profile.py:471` — `browser-use-downloads-<8hex>`, from the `set_default_downloads_path`
  validator, which mkdirs unconditionally. This one *does* fire on the module-level
  `DEFAULT_BROWSER_PROFILE`, so it is one 4 KB dir per process that imports
  `browser_use.browser.session` — i.e. anything touching `Agent` or `BrowserSession`.

* `profile.py:471` — `browser-use-downloads-<8hex>`, from the `set_default_downloads_path`
  validator, which `mkdir`s unconditionally on **every** `BrowserProfile` construction —
  including the module-level `DEFAULT_BROWSER_PROFILE`, so one lands the moment anything
  imports `browser_use.browser.session`. This is the family that actually accumulates:
  142 of them on this VM against 10 user-data-dirs.
* `agent/service.py:448` — `browser_use_agent_<uuid>_<epoch>`, one per `Agent()`
  construction, holding that run's file system.

Attaching over CDP (PLAN.md §4.4) does **not** avoid these: `browser_profile` is ignored
when `browser_session` is passed, but the profile objects are still constructed. The only
real mitigation is `os.environ['TMPDIR'] = <somewhere sweepable>` as the first statement of
the entry point, before any `browser_use` import — measured to relocate all four families.

Safe while no agent is running. A live agent's profile lives in one of these, so this
refuses to touch a directory modified in the last `--min-age-s` seconds (default 900).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import time

PATTERNS = (
    "browser-use-user-data-dir-*",
    "browser-use-downloads-*",
    "browser_use_agent_*",
    "browseruse-tmp-*",
)
TMP = pathlib.Path("/tmp")


def _size(path: pathlib.Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually delete; without it this only reports")
    ap.add_argument("--min-age-s", type=float, default=900.0,
                    help="skip anything modified more recently than this (default 900). A "
                         "courtesy, not a guarantee: a directory's mtime does not change "
                         "when files inside it are written, so a long-running agent's dir "
                         "can look stale. Sweep when no agent is running.")
    args = ap.parse_args(argv)

    now = time.time()
    victims: list[tuple[pathlib.Path, int]] = []
    skipped_young: list[pathlib.Path] = []
    skipped = 0
    candidates = sorted({p for pattern in PATTERNS for p in TMP.glob(pattern)})
    for path in candidates:
        # Belt and braces: resolve() and re-check the parent, so a symlink called
        # /tmp/browser-use-user-data-dir-x pointing at $HOME cannot be followed.
        if not path.is_dir() or path.is_symlink() or path.resolve().parent != TMP:
            skipped += 1
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            skipped += 1
            continue
        if age < args.min_age_s:
            skipped_young.append(path)
            skipped += 1
            continue
        victims.append((path, _size(path)))

    if skipped_young:
        print(f"{len(skipped_young)} dir(s) younger than {args.min_age_s:.0f}s left alone "
              f"in case an agent is live")
    total = sum(size for _p, size in victims)
    print(f"{len(victims)} leaked dir(s), {_human(total)} total"
          + (f"; {skipped} skipped" if skipped else ""))
    if not victims:
        return 0
    for path, size in sorted(victims, key=lambda pair: -pair[1])[:5]:
        print(f"  {_human(size):>10}  {path}")
    if len(victims) > 5:
        print(f"  … and {len(victims) - 5} more, all smaller")

    if not args.yes:
        print("\nnothing deleted — re-run with --yes")
        return 0

    freed = 0
    for path, size in victims:
        try:
            shutil.rmtree(path)
            freed += size
        except OSError as err:
            print(f"  could not remove {path}: {err}", file=sys.stderr)
    print(f"removed {len(victims)} dir(s), freed {_human(freed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
