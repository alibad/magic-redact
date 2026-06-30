"""Tier 2 — classic redaction. Always available, never fails.

Used as the fallback when no library asset fits or a region is awkward. Modes:
pixelate (default), blur, block (solid), gradient.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from .base import RedactionStrategy, RegionSpec


class ClassicRedactStrategy(RedactionStrategy):
    name = "classic"

    def __init__(self, mode: str = "pixelate"):
        self.mode = mode

    def can_handle(self, region: RegionSpec) -> bool:
        return True  # universal fallback

    def apply(self, image: Image.Image, region: RegionSpec, identity, rng) -> Image.Image:
        box = region.box
        patch = image.crop(box)
        w, h = patch.size
        if w <= 0 or h <= 0:
            return image

        mode = region.meta.get("redact_mode", self.mode)
        if mode == "blur":
            patch = patch.filter(ImageFilter.GaussianBlur(radius=max(4, min(w, h) // 8)))
        elif mode == "block":
            patch = Image.new("RGB", (w, h), (32, 32, 36))
        elif mode == "gradient":
            patch = _gradient(w, h)
        else:  # pixelate
            factor = max(6, min(w, h) // 8)
            small = patch.resize((max(1, w // factor), max(1, h // factor)), Image.BILINEAR)
            patch = small.resize((w, h), Image.NEAREST)

        image.paste(patch, box)
        return image


def _gradient(w: int, h: int) -> Image.Image:
    base = Image.new("RGB", (w, h), (60, 64, 72))
    top = Image.new("RGB", (w, h), (120, 126, 138))
    mask = Image.new("L", (w, h))
    md = ImageDraw.Draw(mask)
    for y in range(h):
        md.line([(0, y), (w, y)], fill=int(255 * (y / max(1, h - 1))))
    return Image.composite(base, top, mask)
