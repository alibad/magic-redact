"""Apple Vision detector for magic-redact (macOS / Apple Silicon target).

This is the module the shared FastAPI server loads when
``MAGIC_REDACT_DETECTOR=vision``. It is the Mac counterpart of the Windows
RapidOCR/OpenCV detector: same ``Detector`` interface, same ``RegionSpec``
output, so ``core.pipeline`` and the rest of the engine run unchanged.

How it works
------------
Apple's Vision framework is not callable from Python, so detection is delegated
to a tiny native Swift helper (``mac/detect_vision/VisionRegions.swift``) that we
compile once with SwiftPM. This module shells out to that binary, which prints a
single JSON object::

    {"width": W, "height": H, "regions": [
        {"kind": "text", "bbox": [x, y, w, h], "text": "...", "confidence": 0.93},
        {"kind": "face", "bbox": [x, y, w, h], "text": null, "confidence": 1.0}
    ]}

bboxes are already in TOP-LEFT pixel coordinates (the Swift side converts from
Vision's bottom-left normalized space), so they drop straight into ``RegionSpec``.

We then assign each text region a semantic ``field`` using
``core.detect.base.classify_field`` plus positional heuristics:

* **MRZ**: the bottom long ``[A-Z0-9<]`` lines, detected with ``is_mrz_line``.
* **label-left / value-right**: passport layouts print a small grey label
  ("Surname", "Date of birth") to the left of or above its value. We pair each
  value with the nearest preceding label on the same row (or directly above) and
  feed the label text to ``classify_field`` as a ``label_hint``.
* date fields are refined (dob vs expiry vs issue) by vertical position and the
  label hint, since ``classify_field`` returns a generic ``dob`` for any date.

Portability
-----------
This module MUST import cleanly on Windows (it is authored and import-tested on
the Windows dev box). Therefore it has **no top-level macOS-only imports** — it
only uses the stdlib plus ``core``. The Mac is required at *run time* (to execute
the compiled Swift binary); a clear, actionable error is raised if the binary is
missing or this is run on a non-macOS host.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from PIL import Image

# `core` is portable and safe to import everywhere.
from core.detect.base import Detector, classify_field, is_mrz_line
from core.strategies.base import RegionSpec

# ---------------------------------------------------------------------------
# Locating the compiled Swift helper.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_DEFAULT_BIN_CANDIDATES = [
    _HERE / "detect_vision" / ".build" / "release" / "VisionRegions",
    _HERE / "detect_vision" / ".build" / "debug" / "VisionRegions",
]


def _resolve_binary(explicit: Optional[str] = None) -> Optional[Path]:
    """Find the VisionRegions binary. Order: explicit arg -> env var -> PATH ->
    default SwiftPM build locations. Returns None if nothing is found."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("MAGIC_REDACT_VISION_BIN")
    if env:
        candidates.append(Path(env))
    on_path = shutil.which("VisionRegions")
    if on_path:
        candidates.append(Path(on_path))
    candidates.extend(_DEFAULT_BIN_CANDIDATES)

    for c in candidates:
        if c.is_file():
            return c
    return None


_BUILD_HINT = (
    "Build it first on the Mac:\n"
    "    cd mac/detect_vision && swift build -c release\n"
    "or point MAGIC_REDACT_VISION_BIN at the compiled VisionRegions binary."
)


# ---------------------------------------------------------------------------
# Field assignment heuristics (label-left/value-right + MRZ + date refinement).
# ---------------------------------------------------------------------------

# Canonical label phrases (the keys core's classify_field already understands)
# plus a few common bilingual/extra prints. We deliberately reuse core's source
# of truth so the label vocabulary stays in sync with the classifier.
try:
    from core.detect.base import _LABELS as _CORE_LABELS  # type: ignore
    _LABEL_PHRASES = tuple(_CORE_LABELS.keys())
except Exception:  # noqa: BLE001 - never let a core refactor break import
    _LABEL_PHRASES = (
        "surname", "given names", "name", "date of birth", "nationality",
        "sex", "passport no", "date of expiry", "date of issue",
        "place of birth", "authority",
    )
_LABEL_EXTRA = ("type", "code", "country code", "passeport", "passport")

_DATE_RE = re.compile(r"\d{1,2}[ /.\-][A-Za-z0-9]{2,4}[ /.\-]\d{2,4}")


def _looks_like_label(text: str) -> bool:
    """Conservative: a region is a *label* (not a value) only when its text is
    essentially a known label phrase — short, and the matched phrase covers most
    of the characters. This avoids eating values like "SAMPLE NAME" (which merely
    *contains* "name") or "MARIA HOLDER" as labels.

    Two extra signals push toward label:
      * ALL-CAPS text is almost always a value on an ID, so we require the label
        phrase to dominate before calling all-caps text a label;
      * a trailing colon is a strong label tell.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    had_colon = raw.endswith(":")
    t = raw.lower().rstrip(":").strip()
    if not t or len(t) > 24:
        return False

    phrases = _LABEL_PHRASES + _LABEL_EXTRA
    best = 0
    for p in phrases:
        p = p.strip()
        if not p:
            continue
        if t == p:
            return True
        if p in t:
            best = max(best, len(p))
    if best == 0:
        return False
    # The label phrase must cover most of the text (so "date of birth" -> label,
    # but "SAMPLE NAME" where only "name" matches -> value).
    coverage = best / len(t)
    if had_colon:
        return coverage >= 0.5
    return coverage >= 0.7


def _center(bbox) -> tuple[float, float]:
    x, y, w, h = bbox
    return (x + w / 2.0, y + h / 2.0)


def _same_row(a, b, tol_frac: float = 0.6) -> bool:
    """True if two boxes sit on roughly the same text row."""
    _, ay, _, ah = a
    _, by, _, bh = b
    acy = ay + ah / 2.0
    bcy = by + bh / 2.0
    tol = max(ah, bh) * tol_frac
    return abs(acy - bcy) <= tol


def _nearest_label(value_bbox, labels) -> Optional[str]:
    """Find the best label for a value box: a label to its LEFT on the same row,
    else the closest label directly ABOVE it. Returns the label's text or None."""
    vx, vy, vw, vh = value_bbox

    # 1) same-row, to the left.
    same_row = [
        lt for (lb, lt) in labels
        if _same_row(lb, value_bbox) and (lb[0] + lb[2]) <= vx + vw * 0.5
    ]
    if same_row:
        # closest by horizontal gap.
        same_row_boxes = [
            (lb, lt) for (lb, lt) in labels
            if _same_row(lb, value_bbox) and (lb[0] + lb[2]) <= vx + vw * 0.5
        ]
        lb, lt = min(same_row_boxes, key=lambda p: vx - (p[0][0] + p[0][2]))
        return lt

    # 2) directly above (label box overlaps horizontally and sits just above).
    above = []
    for (lb, lt) in labels:
        lx, ly, lw, lh = lb
        horiz_overlap = min(lx + lw, vx + vw) - max(lx, vx)
        if horiz_overlap > 0 and (ly + lh) <= vy + vh * 0.3:
            above.append((vy - (ly + lh), lt))
    if above:
        above.sort(key=lambda p: p[0])
        return above[0][1]
    return None


def _refine_date_field(field: str, label_hint: Optional[str], value_bbox, img_h: int) -> str:
    """classify_field returns a generic 'dob' for any date. Refine to dob/expiry/
    issue using the label hint first, then vertical position as a tie-breaker."""
    if field != "dob":
        return field
    hint = (label_hint or "").lower()
    if "expiry" in hint or "expir" in hint:
        return "expiry"
    if "issue" in hint:
        return "issue"
    if "birth" in hint or "dob" in hint:
        return "dob"
    # No useful label: leave as dob (the most common standalone date on an ID).
    return "dob"


class VisionDetector(Detector):
    """Detector backed by Apple Vision via the VisionRegions Swift helper.

    Parameters
    ----------
    binary:
        Path to the compiled ``VisionRegions`` executable. If omitted, it is
        resolved from ``MAGIC_REDACT_VISION_BIN``, then ``PATH``, then the
        default SwiftPM build locations under ``mac/detect_vision/.build``.
    languages:
        Recognition languages passed to Vision (e.g. ``["en-US", "fr-FR"]``).
    timeout:
        Seconds to wait for the helper before giving up.
    """

    name = "vision"

    def __init__(
        self,
        binary: Optional[str] = None,
        languages: Optional[List[str]] = None,
        timeout: float = 60.0,
    ):
        self._explicit_binary = binary
        self.languages = languages or ["en-US"]
        self.timeout = timeout
        # Resolve lazily so importing/constructing on Windows never throws; the
        # error only surfaces when you actually call detect() without a binary.
        self._binary = _resolve_binary(binary)

    # -- public API ---------------------------------------------------------

    def detect(self, image: Image.Image) -> List[RegionSpec]:
        payload = self._run_helper(image)
        return self._build_regions(payload)

    # -- internals ----------------------------------------------------------

    def _run_helper(self, image: Image.Image) -> dict:
        binary = self._binary or _resolve_binary(self._explicit_binary)
        if binary is None:
            raise FileNotFoundError(
                "VisionRegions helper binary not found. "
                + _BUILD_HINT
            )
        if platform.system() != "Darwin":
            raise RuntimeError(
                f"The Apple Vision detector only runs on macOS; this host is "
                f"{platform.system()!r}. Set MAGIC_REDACT_DETECTOR to the "
                f"Windows backend on this machine, or run on the Mac."
            )

        # Write the image to a temp PNG the Swift helper can read.
        tmp = Path(tempfile.mkdtemp(prefix="magic_redact_vision_"))
        img_path = tmp / "input.png"
        try:
            image.convert("RGB").save(img_path, format="PNG")
            cmd = [str(binary), "--langs", ",".join(self.languages), str(img_path)]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(
                    f"VisionRegions timed out after {self.timeout}s"
                ) from e

            if proc.returncode != 0:
                raise RuntimeError(
                    f"VisionRegions exited with code {proc.returncode}. "
                    f"stderr:\n{proc.stderr.strip()}"
                )
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"VisionRegions did not return valid JSON.\n"
                    f"stdout:\n{proc.stdout[:500]}\nstderr:\n{proc.stderr[:500]}"
                ) from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _build_regions(self, payload: dict) -> List[RegionSpec]:
        img_h = int(payload.get("height", 0)) or 1
        raw = payload.get("regions", [])

        # Split into faces, label candidates, and value candidates.
        faces: List[dict] = []
        labels: List[tuple] = []   # (bbox, text)
        values: List[dict] = []

        for r in raw:
            kind = r.get("kind")
            bbox = tuple(int(v) for v in r.get("bbox", [0, 0, 0, 0]))
            if kind == "face":
                faces.append({"bbox": bbox, "confidence": float(r.get("confidence", 1.0))})
                continue
            text = r.get("text") or ""
            if _looks_like_label(text):
                labels.append((bbox, text))
            else:
                values.append({"bbox": bbox, "text": text,
                               "confidence": float(r.get("confidence", 0.0))})

        regions: List[RegionSpec] = []
        idx = 0

        # Faces -> photo regions.
        for f in faces:
            regions.append(RegionSpec(
                kind="face", bbox=f["bbox"], id=f"face{idx}", field="photo",
                text=None, confidence=f["confidence"],
                meta={"detector": "vision"},
            ))
            idx += 1

        for v in values:
            bbox = v["bbox"]
            text = v["text"]

            if is_mrz_line(text):
                regions.append(RegionSpec(
                    kind="mrz", bbox=bbox, id=f"mrz{idx}", field="mrz",
                    text=text, confidence=v["confidence"],
                    meta={"detector": "vision"},
                ))
                idx += 1
                continue

            label_hint = _nearest_label(bbox, labels)
            field = classify_field(text, label_hint=label_hint)
            field = _refine_date_field(field, label_hint, bbox, img_h)

            regions.append(RegionSpec(
                kind="text", bbox=bbox, id=f"t{idx}", field=field,
                text=text, confidence=v["confidence"],
                meta={"detector": "vision",
                      "label_hint": label_hint} if label_hint else {"detector": "vision"},
            ))
            idx += 1

        return regions


# Factory hook the shared server uses when MAGIC_REDACT_DETECTOR=vision.
def build_detector(**kwargs) -> VisionDetector:
    """Construct the Vision detector. Reads optional env config so the server can
    stay platform-agnostic:

    * ``MAGIC_REDACT_VISION_BIN``  — path to the compiled helper.
    * ``MAGIC_REDACT_VISION_LANGS`` — comma-separated recognition languages.
    """
    langs_env = os.environ.get("MAGIC_REDACT_VISION_LANGS")
    if langs_env and "languages" not in kwargs:
        kwargs["languages"] = [s.strip() for s in langs_env.split(",") if s.strip()]
    return VisionDetector(**kwargs)


if __name__ == "__main__":  # pragma: no cover - manual smoke test on the Mac
    if len(sys.argv) < 2:
        print("Usage: python detect_vision.py <image-path>", file=sys.stderr)
        raise SystemExit(1)
    det = build_detector()
    im = Image.open(sys.argv[1])
    for region in det.detect(im):
        print(region.to_dict())
