"""Detector interface + a field classifier shared by all platform backends.

Platform detectors (PaddleOCR on Windows, Apple Vision on macOS) subclass
Detector and return RegionSpecs. The heuristic `classify_field` maps OCR text +
position to a semantic field so the identity generator knows what to substitute.
"""
from __future__ import annotations

import re
from typing import List

from PIL import Image

from ..strategies.base import RegionSpec

# Label keywords seen on/next to a value, normalized to a field key.
_LABELS = {
    "surname": "surname", "last name": "surname",
    "given name": "given_names", "given names": "given_names", "first name": "given_names",
    "name": "full_name",
    "date of birth": "dob", "birth": "dob", "dob": "dob",
    "nationality": "nationality", "citizenship": "nationality",
    "sex": "sex",
    "passport no": "doc_number", "passport number": "doc_number",
    "document no": "doc_number", "no.": "doc_number",
    "date of expiry": "expiry", "expiry": "expiry", "expiration": "expiry",
    "date of issue": "issue", "issue": "issue",
    "place of birth": "place_of_birth",
    "authority": "authority",
}

_MRZ_RE = re.compile(r"^[A-Z0-9<]{30,44}$")


def is_mrz_line(text: str) -> bool:
    t = (text or "").strip().upper().replace(" ", "")
    return bool(_MRZ_RE.match(t)) and t.count("<") >= 2


def classify_field(text: str, *, label_hint: str | None = None) -> str:
    """Best-effort field key from a value's text and/or a nearby label."""
    if label_hint:
        for k, v in _LABELS.items():
            if k in label_hint.lower():
                return v
    t = (text or "").strip()
    if is_mrz_line(t):
        return "mrz"
    if re.fullmatch(r"[MFXmfx]", t):
        return "sex"
    if re.search(r"\d{1,2}[ /.-][A-Za-z0-9]{2,3}[ /.-]\d{2,4}", t):
        return "dob"  # a date — caller may refine to expiry/issue by position
    if re.fullmatch(r"[A-Z]{3}", t):
        return "nationality"
    if re.fullmatch(r"[A-Z][0-9]{6,9}", t.replace(" ", "")):
        return "doc_number"
    return "unknown"


class Detector:
    """Subclass per platform. Returns regions for an opened image."""

    name = "base"

    def detect(self, image: Image.Image) -> List[RegionSpec]:
        raise NotImplementedError
