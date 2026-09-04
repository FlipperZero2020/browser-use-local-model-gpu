"""Phase 3 step 1 — the architecture smoke test, PLAN.md's own wording:

    does this Ollama build run a vision model, load its projector, and honour
    `format=` with an image in the same request?

Talks to Ollama directly, unleased — the model under test is not yet a declared
warden workload, so there is nothing to lease. Never send `keep_alive: 0`; let it
idle out on Ollama's own default rather than issuing warden's own eviction verb.

    python tools/phase3_smoketest.py gemma3:4b
    python tools/phase3_smoketest.py qwen2.5vl:7b
"""

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

TRIVIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string"},
        "color": {"type": "string"},
        "text_seen": {"type": "string"},
    },
    "required": ["shape", "color", "text_seen"],
}


def make_test_image(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 300), color="white")
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 340, 240], outline="red", width=6)
    d.ellipse([140, 100, 260, 200], fill="blue")
    d.text((90, 20), "BROWSIN PHASE 3", fill="black")
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--host", default="http://192.168.1.111:11434")
    args = ap.parse_args()

    img_path = Path("/tmp/claude-1000/-home-tom-Documents-claude-browser-use-local-model-GPU/faad3617-c220-46f2-8a2d-6fbc59b600d3/scratchpad/phase3_test.png")
    img_path.parent.mkdir(parents=True, exist_ok=True)
    make_test_image(img_path)
    b64 = base64.b64encode(img_path.read_bytes()).decode()

    payload = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": "Describe the shape, its color, and any text visible in the image.",
                "images": [b64],
            }
        ],
        "format": TRIVIAL_SCHEMA,
        "stream": False,
    }

    print(f"── smoke test: {args.model} @ {args.host} ──")
    t0 = time.monotonic()
    r = requests.post(f"{args.host}/api/chat", json=payload, timeout=300)
    dt = time.monotonic() - t0
    print(f"HTTP {r.status_code} in {dt:.1f}s")
    if r.status_code != 200:
        print(r.text[:2000])
        return 1

    body = r.json()
    content = body.get("message", {}).get("content", "")
    print(f"content: {content!r}")

    try:
        parsed = json.loads(content)
        json.dumps(parsed)
        for k in ("shape", "color", "text_seen"):
            if k not in parsed:
                print(f"FAIL: schema-required key {k!r} missing from parsed content")
                return 1
        print(f"PASS: parses as the trivial schema -> {parsed}")
        return 0
    except json.JSONDecodeError as e:
        print(f"FAIL: content is not valid JSON: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
