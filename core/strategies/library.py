"""Tier 1 — substitution from a curated library. The default, model-free path.

  FaceLibraryStrategy   : drop a synthetic portrait into a photo region, fit by
                          aspect (cover-crop), tone-matched and feathered.
  TextSubstituteStrategy: clear the field background and render the identity's
                          value in a size/color matched to the original.

Either strategy may return None to *decline* a region (e.g. empty face pool, or
an unknown text field) so the pipeline falls through to the classic fallback.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from .. import fonts
from ..detect.base import is_mrz_line
from .base import RedactionStrategy, RegionSpec

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


class FaceLibraryStrategy(RedactionStrategy):
    name = "face_library"

    def __init__(self, face_dir: str | Path, tone_match: bool = True, feather: int = 6):
        self.face_dir = Path(face_dir)
        self.tone_match = tone_match
        self.feather = feather
        self._cache: Optional[list[Path]] = None

    def faces(self) -> list[Path]:
        if self._cache is None:
            self._cache = sorted(
                p for p in self.face_dir.glob("**/*") if p.suffix.lower() in _IMG_EXT
            ) if self.face_dir.exists() else []
        return self._cache

    def can_handle(self, region: RegionSpec) -> bool:
        return region.kind == "face"

    def apply(self, image, region, identity, rng) -> Optional[Image.Image]:
        pool = self.faces()
        if not pool:
            return None  # decline -> classic fallback pixelates the face

        x0, y0, x1, y1 = region.box
        bw, bh = x1 - x0, y1 - y0
        if bw <= 0 or bh <= 0:
            return None

        face = Image.open(rng.choice(pool)).convert("RGB")
        face = _cover(face, bw, bh)
        if self.tone_match:
            face = _match_tone(face, image.crop(region.box))

        mask = _feather_mask(bw, bh, self.feather)
        image.paste(face, (x0, y0), mask)
        region.meta["strategy"] = self.name
        return image


class TextSubstituteStrategy(RedactionStrategy):
    name = "text_substitute"

    def can_handle(self, region: RegionSpec) -> bool:
        return region.kind in ("text", "mrz")

    def apply(self, image, region, identity, rng) -> Optional[Image.Image]:
        value = identity.value_for(region.field)
        if value is None:
            # MRZ regions sometimes arrive as kind="text"; rescue them.
            if region.kind == "mrz" or (region.text and is_mrz_line(region.text)):
                value = "\n".join(identity.mrz_lines)
            else:
                return None  # unknown field -> classic fallback

        x0, y0, x1, y1 = region.box
        bw, bh = x1 - x0, y1 - y0
        if bw <= 2 or bh <= 2:
            return None

        bg = region.meta.get("bg_color") or _estimate_bg(image, region.box)
        fg = region.meta.get("fg_color") or _estimate_fg(image, region.box, bg)

        draw = ImageDraw.Draw(image)
        draw.rectangle((x0, y0, x1, y1), fill=bg)

        lines = value.split("\n")
        is_mono = region.kind == "mrz" or region.field == "mrz" or len(lines) > 1
        _draw_fitted(draw, (x0, y0, bw, bh), lines, fg, mono=is_mono)
        region.meta["strategy"] = self.name
        return image


# --- image helpers ----------------------------------------------------------

def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    img = img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), Image.LANCZOS)
    iw, ih = img.size
    left, top = (iw - w) // 2, (ih - h) // 2
    return img.crop((left, top, left + w, top + h))


def _match_tone(src: Image.Image, ref: Image.Image) -> Image.Image:
    """Nudge src channel means toward ref so the face sits in the document's light."""
    try:
        s = ImageStat.Stat(src).mean
        r = ImageStat.Stat(ref.convert("RGB")).mean
    except Exception:
        return src
    lut = []
    for sc, rc in zip(s, r):
        shift = (rc - sc) * 0.5
        lut.extend(min(255, max(0, int(i + shift))) for i in range(256))
    return src.point(lut)


def _feather_mask(w: int, h: int, feather: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    inset = max(1, feather)
    d.rectangle((inset, inset, w - inset, h - inset), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=feather)) if feather else mask


def _estimate_bg(image: Image.Image, box) -> tuple[int, int, int]:
    """Median-ish background from the field's border ring."""
    x0, y0, x1, y1 = box
    ring = []
    img = image.convert("RGB")
    for x in range(x0, x1, max(1, (x1 - x0) // 12)):
        ring.append(img.getpixel((x, max(0, y0 + 1))))
        ring.append(img.getpixel((x, min(img.height - 1, y1 - 1))))
    if not ring:
        return (245, 245, 240)
    n = len(ring)
    return tuple(sorted(c[i] for c in ring)[n // 2] for i in range(3))


def _estimate_fg(image: Image.Image, box, bg) -> tuple[int, int, int]:
    """Dark text on light bg, or light text on dark bg."""
    return (20, 20, 24) if sum(bg) > 384 else (235, 235, 235)


def _draw_fitted(draw, rect, lines, fg, mono=False):
    x, y, w, h = rect
    n = max(1, len(lines))
    size = max(8, int(h / n * 0.82))
    get_font = fonts.mono if mono else fonts.sans
    # Shrink to fit the widest line.
    for _ in range(24):
        font = get_font(size)
        widest = max(draw.textlength(ln, font=font) for ln in lines)
        if widest <= w * 0.98 or size <= 8:
            break
        size -= 1
    font = get_font(size)
    line_h = h / n
    for i, ln in enumerate(lines):
        ty = y + i * line_h + (line_h - size) / 2
        draw.text((x + 1, ty), ln, font=font, fill=fg)
