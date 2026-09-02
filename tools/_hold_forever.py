#!/usr/bin/env python3
"""Take a lease, announce it, and wait. The victim for Phase 2's signal gates.

    venv/bin/python tools/_hold_forever.py ollama:qwen3:8b --seconds 300

Prints one line — `HOLDING <lease_id> <endpoint>` — as soon as the card is live, so a
parent can wait for that rather than guessing when to signal. Then it prints exactly one
outcome line and exits:

    RELEASED_ON_SIGNAL <SIGINT|SIGTERM>     the interesting case
    RELEASED_ON_LOST                        the lease was revoked underneath it
    RELEASED_ON_TIMEOUT                     nobody signalled; the hold ran out

Separate from `phase2_gate.py` because a gate cannot send itself a SIGTERM and still be
around to report on what happened.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from warden.client import LeaseLost  # noqa: E402

from browsin.lease import DEFAULT_TTL_S, Interrupted, hold  # noqa: E402


async def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument('workload')
	ap.add_argument('--seconds', type=float, default=300.0)
	ap.add_argument('--ttl-s', type=float, default=DEFAULT_TTL_S)
	ap.add_argument('--num-ctx', type=int, default=None)
	ap.add_argument('--no-verify', action='store_true')
	args = ap.parse_args()

	logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
	                    stream=sys.stderr)
	try:
		async with hold(args.workload, reason='browsin phase 2 signal gate',
		                ttl_s=args.ttl_s, num_ctx=args.num_ctx,
		                verify=not args.no_verify) as card:
			print(f'HOLDING {card.held.lease_id} {card.endpoint}', flush=True)
			await asyncio.sleep(args.seconds)
	except Interrupted as err:
		print(f'RELEASED_ON_SIGNAL {err.signal}', flush=True)
		return 0
	except LeaseLost as err:
		print(f'RELEASED_ON_LOST {err}', flush=True)
		return 0
	print('RELEASED_ON_TIMEOUT', flush=True)
	return 0


if __name__ == '__main__':
	raise SystemExit(asyncio.run(main()))
