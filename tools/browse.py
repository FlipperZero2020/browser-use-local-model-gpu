#!/usr/bin/env python3
"""Give the local vision model a browsing task and watch it work.

    export WARDEN_URL=http://192.168.1.111:8130
    export WARDEN_TOKEN_FILE=$HOME/.config/warden/token
    venv/bin/python -u tools/browse.py --url https://en.wikipedia.org/wiki/Main_Page \\
        --task "Find today's featured article and report its title."

This is a thin alias for `tools/test.py one`, kept so the command documented in CLAUDE.md,
NEXT_SESSION.md and PLAN.md keeps working. Everything it used to do itself — the lease, the
proxy, Chrome, the printed prompt-size block — now happens on the same code path as the graded
measurement, so a number seen here means the same thing it means there.

Two things changed on 2026-09-05 and are worth knowing:

* **It no longer attaches to a running Chrome.** The old attach path adopted whichever open tab
  shared the task's host and never navigated to `--url`; a previous run's leftover tab drove two
  runs onto the wrong page with no error (PLAN.md §10). A fresh Chrome is started on `--url`
  every time. The profile is the dedicated browser-use working copy, not the daily driver.
* **The answer is UNGRADED unless you say where the truth comes from.** Pass
  `--expect-from itn | hn:N | wiki:PAGE:PHRASE | absent:F1,F2` and it is scored and diagnosed
  like a table task. There is deliberately no free-text expectation.

`--keep-chrome` is accepted and ignored: it never did anything (it was declared default=True
and never read).
"""
from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
	argv = [a for a in argv if a != '--keep-chrome']
	cmd = [sys.executable, '-u', str(HERE / 'test.py'), 'one', *argv]
	os.execv(cmd[0], cmd)
	return 1  # not reached


if __name__ == '__main__':
	sys.exit(main(sys.argv[1:]))
