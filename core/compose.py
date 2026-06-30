"""Output finishing — the SPECIMEN watermark.

Every redacted document gets a visible, hard-to-remove SPECIMEN overlay so the
result is unmistakably synthetic and can't be passed off as a genuine document.
This is a deliberate safety property of the tool, on by default.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from . import fonts


def add_specimen_watermark(image: Image.Image, text: str = "SPECIMEN", opacity: int = 38) -> Image.Image:
    base = image.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    size = max(18, w // 14)
    font = fonts.sans(size)
    step_x = int(draw.textlength(text + "   ", font=font)) or size * 5
    step_y = size * 4

    for yy in range(-h, h * 2, step_y):
        for xx in range(-w, w * 2, step_x):
            draw.text((xx, yy), text, font=font, fill=(200, 30, 30, opacity))

    overlay = overlay.rotate(30, expand=False, resample=Image.BICUBIC)
    return Image.alpha_composite(base, overlay).convert("RGB")
