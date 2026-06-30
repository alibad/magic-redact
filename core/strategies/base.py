"""Region model + the strategy interface.

A RegionSpec is one editable area found in a document. A RedactionStrategy
knows how to replace some kinds of region with synthetic content. The pipeline
tries strategies in priority order and uses the first that `can_handle` a region.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Literal, Optional

from PIL import Image

Kind = Literal["face", "text", "mrz"]


@dataclass
class RegionSpec:
    kind: Kind
    bbox: tuple[int, int, int, int]          # (x, y, w, h) in pixels
    id: str = ""
    field: str = "unknown"                    # semantic field (see identity.FIELD_ALIASES)
    text: Optional[str] = None                # OCR'd original text, if any
    confidence: float = 1.0
    meta: dict = dc_field(default_factory=dict)  # font size hint, est. colors, angle, ...

    @property
    def box(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) for PIL crop/paste."""
        x, y, w, h = self.bbox
        return (x, y, x + w, y + h)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "bbox": list(self.bbox),
            "field": self.field, "text": self.text,
            "confidence": round(self.confidence, 3), "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RegionSpec":
        """Rebuild from a to_dict() payload (e.g. posted by the web UI)."""
        return cls(
            kind=d["kind"],
            bbox=tuple(d["bbox"]),
            id=d.get("id", ""),
            field=d.get("field", "unknown"),
            text=d.get("text"),
            confidence=d.get("confidence", 1.0),
            meta=dict(d.get("meta") or {}),
        )


class RedactionStrategy:
    """Base class. Subclasses implement can_handle + apply."""

    name: str = "base"

    def can_handle(self, region: RegionSpec) -> bool:
        raise NotImplementedError

    def apply(self, image: Image.Image, region: RegionSpec, identity, rng) -> Image.Image:
        """Mutate `image` in place (and/or return it) to redact `region`."""
        raise NotImplementedError
