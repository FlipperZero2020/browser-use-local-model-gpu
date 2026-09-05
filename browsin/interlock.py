"""Refuse to take the card when taking it would cut somebody else off, or when the
numbers describing it cannot be trusted.

Lives in its own module because `tools/test.py`, `tools/phase4_gate.py` and (through
`test.py`) `tools/browse.py` all need it, and `tools/phase4_gate.py` cannot be imported for
it: that module sets `TMPDIR`,
`tempfile.tempdir` and `BROWSER_USE_CONFIG_DIR` and creates a run directory *at import*,
because those have to happen before `browser_use` is imported. Importing it for one
function would silently redirect the caller's temp files into a stray run directory.
"""

from __future__ import annotations


class Interlock(RuntimeError):
	"""The card is not free, and taking it would cut somebody else off."""


#: `foreign_mib` idles here on a QUIET card with a QUIET desktop (PLAN.md §7, measured
#: 2026-09-01). Above it with no tenants is a reason to look closer, not — by itself — to refuse.
FOREIGN_BASELINE_MAX = 2700
#: Nothing the desktop does reaches this; a leaked in-flight model load (5-8 GB) does.
FOREIGN_HARD_MAX = 4000
#: The mechanism this rule exists to catch is a load *climbing* into VRAM while appearing in
#: neither `/api/ps` nor warden's tenants (measured 2026-09-01: 2,230 → 7,336). It climbs by
#: hundreds of MiB per sample. A busy desktop is flat.
FOREIGN_RISE_MIB = 150
FOREIGN_TREND_WAIT_S = 15


async def card_preflight(*, evict: bool, verbose: bool = True) -> dict:
	"""Check the card is in a state worth starting on. Returns warden's `/v1/status`.

	**The clonin interlock, settled by the owner 2026-09-04: warn and ask, per run.**
	Phase 3 measured this project's vision workload at 8,375 MiB, which needs 9,213 after the
	×1.10 admission factor — against the 8,763 of book left when clonin holds its 3,726. That
	is a 450 MiB deficit, so an `interactive` acquire does not co-reside with the voice
	service, it *evicts* it. `clonin-frontdoor` is public, so the sentence being cut off may
	belong to a stranger. Never silently.
	"""
	from browsin.lease import probe

	status = await probe()
	v = status.get('vram') or {}
	tenants = status.get('tenants') or []
	if verbose:
		print(f'preflight: free={v.get("free_mib")} foreign={v.get("foreign_mib")} '
		      f'ghost={v.get("ghost_mib")} committed={v.get("committed_mib")} '
		      f'tenants={[t.get("workload_id") for t in tenants]} '
		      f'leases={len(status.get("leases") or [])}', flush=True)

	problems = []
	# The foreign-memory baseline only means anything on a QUIET card.
	#
	# Measured 2026-09-04, and it cost a false refusal before it was understood: with
	# `ollama:qwen2.5vl-32k:7b` resident from a previous run's idle linger, `foreign_mib`
	# sits at a stable 2740 — above the 2200-2600 idle range in PLAN.md §7 — and stays
	# there. That is not the leaked in-flight load this rule is written to catch; it is the
	# CUDA context of a legitimately resident tenant, which warden books under `cost_mib`
	# and not under `foreign`. Comparing a loaded card against an idle card's baseline is
	# comparing unlike states, so the check is scoped to the state it was measured in.
	if tenants:
		if verbose:
			print(f'   (foreign_mib {v.get("foreign_mib")} not gated: {len(tenants)} '
			      f'tenant(s) resident, which inflates it above the idle baseline)',
			      flush=True)
	elif (v.get('foreign_mib') or 0) > FOREIGN_BASELINE_MAX:
		# Above the idle baseline with no tenants. Measured 2026-09-05, and it cost every run
		# of an evening before it was understood: `foreign_mib` sat FLAT at 2805 for 25 minutes
		# while nvidia-smi on the box read 2534 MiB total — inside the baseline — with no
		# `llama-server` process anywhere, and the GPU contexts belonging to Task Manager,
		# four Snipping Tools, 17 Chrome processes, Plex and Settings: somebody was using the
		# desktop. That memory is exactly what warden calls "foreign" and cannot reclaim, and
		# it is not the leaked load this rule is written for. A leak CLIMBS. So: refuse on a
		# hard ceiling the desktop cannot reach, refuse on a rising trend, and otherwise warn
		# with the numbers and proceed — the number is recorded in the preflight line either
		# way, so a reader of the log can judge it.
		import asyncio
		first = v.get('foreign_mib') or 0
		if first > FOREIGN_HARD_MAX:
			problems.append(
				f'foreign_mib {first} > {FOREIGN_HARD_MAX} on a card with NO tenants: that is a '
				f'model-sized amount of VRAM nobody is accounting for — a load leaked in flight, '
				f'visible in neither /api/ps nor warden\'s tenants. Wait for it to self-reap.')
		else:
			await asyncio.sleep(FOREIGN_TREND_WAIT_S)
			second = ((await probe()).get('vram') or {}).get('foreign_mib') or first
			if second - first > FOREIGN_RISE_MIB:
				problems.append(
					f'foreign_mib is CLIMBING: {first} → {second} in {FOREIGN_TREND_WAIT_S} s with '
					f'NO tenants — a load leaking in flight. Wait for baseline; no VRAM number is '
					f'trustworthy until it settles.')
			elif verbose:
				print(f'   WARNING foreign_mib {first} is above the {FOREIGN_BASELINE_MAX} idle baseline '
				      f'but flat ({first} → {second} over {FOREIGN_TREND_WAIT_S} s) and under the '
				      f'{FOREIGN_HARD_MAX} hard ceiling: consistent with a busy desktop on the box, '
				      f'not a leaked load. Proceeding; the excess is memory warden cannot reclaim.',
				      flush=True)
	if v.get('ghost_mib'):
		problems.append(f'ghost_mib {v.get("ghost_mib")}: the book is already under-admitting')
	if problems:
		raise Interlock('; '.join(problems))

	displaced = [t for t in tenants if 'clonin' in str(t.get('workload_id', '')).lower()]
	if displaced and not evict:
		raise Interlock(
			'the voice service (clonin) is holding the card, and this workload does not fit '
			'beside it — it would be EVICTED, mid-sentence, and clonin-frontdoor is public.\n'
			f'  resident: {[(t.get("workload_id"), t.get("cost_mib")) for t in displaced]}\n'
			'  Re-run with --evict to displace it deliberately, or wait: clonin\'s '
			'idle_linger_s is 120 s, so the normal wait is short.')
	if displaced and verbose:
		print(f'!! --evict given: displacing {[t.get("workload_id") for t in displaced]}',
		      flush=True)
	return status
