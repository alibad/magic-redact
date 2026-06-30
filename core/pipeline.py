"""The redaction orchestrator both platform targets call.

  redact(image, regions, identity, strategies) -> (image, regions)

For each region, try strategies in priority order; the first that returns a
non-None image wins. A classic fallback at the end guarantees every region is
redacted. Detection is decoupled: pass regions directly (manual UI / one-click)
or run a Detector first via `detect_and_redact`.
"""
from __future__ import annotations

import random
from typing import Iterable, Optional, Sequence

from PIL import Image

from .compose import add_specimen_watermark
from .identity import Identity, generate_identity
from .strategies.base import RedactionStrategy, RegionSpec
from .strategies.blur import ClassicRedactStrategy
from .strategies.library import FaceLibraryStrategy, TextSubstituteStrategy


def default_strategies(face_dir: str = "assets/faces") -> list[RedactionStrategy]:
    """Tier 1 (substitute) first, Tier 2 (classic) as the guaranteed fallback."""
    return [
        TextSubstituteStrategy(),
        FaceLibraryStrategy(face_dir),
        ClassicRedactStrategy(mode="pixelate"),
    ]


def redact(
    image: Image.Image,
    regions: Sequence[RegionSpec],
    *,
    identity: Optional[Identity] = None,
    strategies: Optional[Sequence[RedactionStrategy]] = None,
    only: Optional[Iterable[str]] = None,   # region ids to process; None = all
    watermark: bool = True,
    seed: Optional[int] = None,
) -> tuple[Image.Image, list[RegionSpec]]:
    identity = identity or generate_identity(seed=seed)
    strategies = list(strategies) if strategies is not None else default_strategies()
    rng = random.Random(seed)
    only_set = set(only) if only is not None else None

    out = image.convert("RGB").copy()
    processed: list[RegionSpec] = []
    for region in regions:
        if only_set is not None and region.id not in only_set:
            continue
        for strat in strategies:
            if not strat.can_handle(region):
                continue
            result = strat.apply(out, region, identity, rng)
            if result is not None:
                out = result
                processed.append(region)
                break

    if watermark:
        out = add_specimen_watermark(out)
    return out, processed


def detect_and_redact(image, detector, **kwargs):
    """Convenience: detect regions then redact them in one call."""
    regions = detector.detect(image)
    for i, r in enumerate(regions):
        if not r.id:
            r.id = f"r{i}"
    return redact(image, regions, **kwargs)
