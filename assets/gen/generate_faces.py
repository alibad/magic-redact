"""Generate a pool of synthetic passport-style portraits with the local
Qwen-Image server (http://localhost:8021) — free, private, on-device.

These faces are the Tier-1 substitution library: at redaction time we just pick
and fit one, no model needed. Generate once, reuse forever.

    python assets/gen/generate_faces.py --count 12
    python assets/gen/generate_faces.py --count 200 --start-seed 1000   # big pool

Stdlib only (urllib) so it runs with zero pip installs. Long timeout so cold
model loads don't abort.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACES_DIR = HERE.parent / "faces"
MANIFEST = HERE.parent / "faces" / "manifest.json"

AGE = ["in their early 20s", "in their late 20s", "in their 30s", "in their 40s",
       "in their 50s", "in their 60s", "elderly"]
PRESENT = ["a man", "a woman", "a person", "a man", "a woman"]
ETHNIC = ["East Asian", "South Asian", "Black", "White", "Hispanic", "Middle Eastern",
          "Southeast Asian", "Mediterranean", "Nordic", "mixed-race"]
HAIR = ["short hair", "medium-length hair", "long hair", "curly hair", "straight hair",
        "a shaved head", "tied-back hair", "wavy hair"]
EXTRA = ["", "", "wearing glasses", "with a light beard", "with freckles",
         "wearing a plain collared shirt", "with a neutral expression"]

NEG = ("multiple people, two faces, hands, text, watermark, logo, caption, "
       "sunglasses, hat, profile view, side view, tilted head, low quality, "
       "blurry, deformed, extra limbs, cartoon, illustration, oversaturated")


def make_prompt(rng: random.Random) -> tuple[str, dict]:
    age = rng.choice(AGE)
    who = rng.choice(PRESENT)
    eth = rng.choice(ETHNIC)
    hair = rng.choice(HAIR)
    extra = rng.choice(EXTRA)
    tags = {"age": age, "present": who, "ethnicity": eth, "hair": hair, "extra": extra}
    prompt = (
        f"passport identification photo of {who}, {eth}, {age}, {hair}"
        f"{', ' + extra if extra else ''}. "
        "Front-facing headshot, head and shoulders, looking directly at the camera, "
        "neutral expression, even soft studio lighting, plain light gray background, "
        "sharp focus, realistic skin texture, biometric ID photo, color photograph"
    )
    return prompt, tags


def png_ok(buf: bytes) -> bool:
    return len(buf) > 24 and buf[:8] == b"\x89PNG\r\n\x1a\n"


def generate_one(endpoint: str, prompt: str, w: int, h: int, steps: int, seed: int) -> bytes:
    body = json.dumps({
        "prompt": prompt, "negative_prompt": NEG,
        "width": w, "height": h, "steps": steps, "cfg": 3.5, "seed": seed,
    }).encode()
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--start-seed", type=int, default=1000)
    ap.add_argument("--endpoint", default="http://localhost:8021")
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=640)
    ap.add_argument("--steps", type=int, default=20)
    args = ap.parse_args()

    FACES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    existing = {m["file"] for m in manifest}

    made = 0
    for i in range(args.count):
        seed = args.start_seed + i
        fname = f"face_{seed:05d}.png"
        if fname in existing:
            continue
        rng = random.Random(seed)
        prompt, tags = make_prompt(rng)
        try:
            buf = generate_one(args.endpoint, prompt, args.width, args.height, args.steps, seed)
        except Exception as e:
            print(f"  [x] {fname}: {e}", flush=True)
            continue
        if not png_ok(buf):
            print(f"  [x] {fname}: not a PNG ({buf[:32]!r})", flush=True)
            continue
        (FACES_DIR / fname).write_bytes(buf)
        manifest.append({"file": fname, "seed": seed, "w": args.width, "h": args.height,
                         "tags": tags, "prompt": prompt})
        MANIFEST.write_text(json.dumps(manifest, indent=2))
        made += 1
        print(f"  [ok] [{made}/{args.count}] {fname}  ({tags['present']}, {tags['ethnicity']}, {tags['age']})", flush=True)

    print(f"Done. {made} new face(s). Pool now {len(manifest)} total in {FACES_DIR}")


if __name__ == "__main__":
    sys.exit(main())
