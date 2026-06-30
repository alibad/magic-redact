"""OPTIONAL Tier-3 face source: a fresh synthetic portrait from the local
Qwen-Image server, inpainted into a document's face region.

The model currently running at http://localhost:8021 is **Qwen-Image
(text-to-image)**, NOT Qwen-Image-Edit. That distinction matters:

  * What Tier-3 IS here: on demand, generate a brand-new photorealistic synthetic
    portrait from a text prompt and composite it into the `face` region — a
    higher-quality alternative to picking a face from the static `assets/faces`
    library. The result is still watermarked SPECIMEN by core like everything
    else.
  * What Tier-3 is NOT (yet): true instruction-based, in-image editing — i.e.
    font-preserving text edits or pose-matched face swaps that keep the original
    head pose/lighting. That needs **Qwen-Image-Edit-2511** (an image-to-image
    edit model) wired in later. Until then, text fields are always handled by the
    portable Tier-1 `TextSubstituteStrategy`; Qwen only ever supplies a face.

Everything here is fully optional. If the server is down or unreachable the
adapter reports unavailable and the app keeps working on Tier 1/2. Only stdlib
`urllib` is used (no extra dependency), with a long timeout because diffusion is
slow.

Server contract (provided):
    GET  /health   -> {ok: bool, ...}
    POST /generate {prompt, negative_prompt, width, height, steps, cfg, seed}
                   -> PNG bytes
"""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

from core.strategies.base import RedactionStrategy, RegionSpec

QWEN_URL = os.environ.get("MAGIC_REDACT_QWEN_URL", "http://localhost:8021")
_TIMEOUT = 600  # diffusion is slow; per spec.

_PORTRAIT_PROMPT = (
    "a neutral passport-style head-and-shoulders portrait photo of a fictional "
    "person, plain light studio background, soft even lighting, looking straight "
    "at camera, photorealistic, ID document photo, sharp focus"
)
_NEG_PROMPT = (
    "text, watermark, logo, multiple people, hands, cartoon, illustration, "
    "blurry, deformed, extra limbs, nsfw"
)


def qwen_available(timeout: float = 4.0) -> bool:
    """Quick health probe. Never raises."""
    try:
        req = urllib.request.Request(QWEN_URL.rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok", data.get("loaded", True)))
    except Exception:
        return False


def generate_portrait(
    width: int = 512,
    height: int = 640,
    *,
    seed: Optional[int] = None,
    steps: int = 28,
    cfg: float = 4.0,
    prompt: Optional[str] = None,
    negative_prompt: Optional[str] = None,
) -> Optional[Image.Image]:
    """Ask the Qwen server for a fresh synthetic portrait. Returns a PIL image,
    or None if the server is unavailable/failed. Never raises."""
    payload = {
        "prompt": prompt or _PORTRAIT_PROMPT,
        "negative_prompt": negative_prompt if negative_prompt is not None else _NEG_PROMPT,
        "width": int(width),
        "height": int(height),
        "steps": int(steps),
        "cfg": float(cfg),
    }
    if seed is not None:
        payload["seed"] = int(seed)

    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            QWEN_URL.rstrip("/") + "/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                print(f"[diffusion_qwen] /generate HTTP {resp.status}")
                return None
            raw = resp.read()
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except urllib.error.URLError as exc:
        print(f"[diffusion_qwen] generate failed (server unreachable?): {exc}")
        return None
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[diffusion_qwen] generate failed: {exc}")
        return None


class QwenFaceStrategy(RedactionStrategy):
    """A Tier-3, opt-in face strategy. Drop into the strategy list AHEAD of the
    library strategy when the user opts into Qwen faces. Generates a fresh
    portrait per face region and feathers it into place. Declines (returns None)
    on any failure so the pipeline falls back to library/classic.
    """

    name = "qwen_face"

    def __init__(self, feather: int = 6, steps: int = 28, cfg: float = 4.0):
        self.feather = feather
        self.steps = steps
        self.cfg = cfg

    def can_handle(self, region: RegionSpec) -> bool:
        return region.kind == "face"

    def apply(self, image, region, identity, rng) -> Optional[Image.Image]:
        x0, y0, x1, y1 = region.box
        bw, bh = x1 - x0, y1 - y0
        if bw <= 8 or bh <= 8:
            return None

        # Seed the portrait off the identity so re-rolls are reproducible.
        seed = getattr(identity, "seed", None)
        if seed is None:
            seed = rng.randint(0, 2**31 - 1)

        # Generate a slightly larger-than-region portrait, then cover-fit.
        portrait = generate_portrait(
            width=_round8(max(384, bw)),
            height=_round8(max(480, bh)),
            seed=int(seed),
            steps=self.steps,
            cfg=self.cfg,
        )
        if portrait is None:
            return None  # decline -> library/classic fallback handles it.

        face = _cover(portrait, bw, bh)
        mask = _feather_mask(bw, bh, self.feather)
        image.paste(face, (x0, y0), mask)
        region.meta["strategy"] = self.name
        region.meta["face_source"] = "qwen"
        return image


# --- helpers (kept local; do not touch core) --------------------------------

def _round8(n: int) -> int:
    return max(8, (int(n) // 8) * 8)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    img = img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), Image.LANCZOS)
    iw, ih = img.size
    left, top = (iw - w) // 2, (ih - h) // 2
    return img.crop((left, top, left + w, top + h))


def _feather_mask(w: int, h: int, feather: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    inset = max(1, feather)
    d.rectangle((inset, inset, w - inset, h - inset), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=feather)) if feather else mask
