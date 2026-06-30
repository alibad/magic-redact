"""Font resolution that works on Windows and macOS.

Tries bundled fonts first (drop OCR-B / Arimo into core/fonts_ttf/ to make output
deterministic across machines), then common system fonts, then PIL's default.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_BUNDLED = Path(__file__).parent / "fonts_ttf"

# Ordered preference lists. First hit wins.
_SANS = [
    "Arimo-Regular.ttf", "LiberationSans-Regular.ttf",  # bundled-friendly
    "arial.ttf", "Arial.ttf",                            # Windows / macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "DejaVuSans.ttf",
]
_MONO = [
    "OCRB.ttf", "OCR-B.ttf", "OCRBMatrix.ttf",           # ideal for MRZ
    "Cousine-Regular.ttf", "consola.ttf", "Consolas.ttf",
    "cour.ttf", "Courier New.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "DejaVuSansMono.ttf",
]


def _try(name: str, size: int):
    # Absolute path?
    if os.path.isabs(name) and os.path.exists(name):
        return ImageFont.truetype(name, size)
    # Bundled?
    p = _BUNDLED / name
    if p.exists():
        return ImageFont.truetype(str(p), size)
    # System lookup (PIL searches platform font dirs by bare name).
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return None


@lru_cache(maxsize=256)
def sans(size: int):
    for n in _SANS:
        f = _try(n, size)
        if f:
            return f
    return ImageFont.load_default()


@lru_cache(maxsize=256)
def mono(size: int):
    """Monospace face for the MRZ. OCR-B if available, else a code font."""
    for n in _MONO:
        f = _try(n, size)
        if f:
            return f
    return ImageFont.load_default()
