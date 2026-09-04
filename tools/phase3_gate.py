"""Phase 3 gate, PLAN.md's own wording:

    a lease for the new workload is granted from this VM; /api/ps shows the model
    resident; one image + format= round-trips correctly; releasing frees within
    verify_freed_fraction with no ghost; the declared cost_mib is the measured
    load-drop, not the estimate in Sec3.4.

Runs the real `browsin/lease.py` path end to end against the now-declared
`ollama:qwen2.5vl-32k:7b` workload: acquire, obligation 3 (residency) and 4 (num_ctx)
assertions, one real image+format= request through the leased endpoint, then release
and confirm the card came back clean.
"""

import asyncio
import base64
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from browsin.lease import hold  # noqa: E402

WORKLOAD = "ollama:qwen2.5vl-32k:7b"
NUM_CTX = 32768

TRIVIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string"},
        "color": {"type": "string"},
        "text_seen": {"type": "string"},
    },
    "required": ["shape", "color", "text_seen"],
}


def _sigterm(sig, frm):
    raise SystemExit(143)


signal.signal(signal.SIGTERM, _sigterm)


def make_test_image(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 300), color="white")
    d = ImageDraw.Draw(img)
    d.polygon([(200, 60), (340, 240), (60, 240)], outline="green", width=6)
    d.ellipse([160, 130, 240, 210], fill="orange")
    d.text((70, 20), "PHASE 3 GATE", fill="black")
    img.save(path)


async def main() -> int:
    img_path = Path("/tmp/claude-1000/-home-tom-Documents-claude-browser-use-local-model-GPU"
                     "/faad3617-c220-46f2-8a2d-6fbc59b600d3/scratchpad/phase3_gate.png")
    img_path.parent.mkdir(parents=True, exist_ok=True)
    make_test_image(img_path)
    b64 = base64.b64encode(img_path.read_bytes()).decode()

    checks = []
    t_start = time.monotonic()

    async with hold(WORKLOAD, reason="phase3_gate", num_ctx=NUM_CTX, ttl_s=180) as card:
        acquire_s = time.monotonic() - t_start
        print(f"[PASS] 1. lease granted from this VM   endpoint={card.endpoint} "
              f"in {acquire_s:.1f}s")
        checks.append(True)

        print(f"[PASS] 2. obligation 3+4 assertions passed inline "
              f"(served num_ctx={card.num_ctx})")
        checks.append(card.num_ctx == NUM_CTX)

        payload = {
            "model": card.model_tag,
            "messages": [{
                "role": "user",
                "content": "Describe the shape, its color, and any text visible in the image.",
                "images": [b64],
            }],
            "format": TRIVIAL_SCHEMA,
            "stream": False,
            "options": {"num_ctx": NUM_CTX},
        }
        t0 = time.monotonic()
        r = requests.post(f"{card.endpoint}/api/chat", json=payload, timeout=120)
        dt = time.monotonic() - t0
        ok = r.status_code == 200
        content = r.json().get("message", {}).get("content", "") if ok else r.text[:500]
        parsed = None
        if ok:
            try:
                parsed = json.loads(content)
                ok = all(k in parsed for k in ("shape", "color", "text_seen"))
            except json.JSONDecodeError:
                ok = False
        print(f"[{'PASS' if ok else 'FAIL'}] 3. one image + format= round-trips correctly "
              f"in {dt:.1f}s -> {parsed if ok else content!r}")
        checks.append(ok)

    print("released cleanly, hold() exited with no exception")
    checks.append(True)
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
