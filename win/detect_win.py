"""Windows detection backend for magic-redact.

A single `WinDetector(Detector)` that finds the identity-bearing regions of an
ID-style document and returns `core.detect.base.RegionSpec`s. It is built from
two independent, all-pip-installable, PyTorch-free capabilities:

  * Text + boxes  -> RapidOCR (`rapidocr-onnxruntime`). Each OCR line becomes a
    `text` (or `mrz`) region. We classify the line into a semantic `field`
    using `core.detect.base.classify_field` plus light positional heuristics
    (labels sit above/left of their value; the long [A-Z0-9<] lines at the
    bottom are the MRZ).
  * Face box      -> OpenCV YuNet (`cv2.FaceDetectorYN`, ONNX model auto
    downloaded on first use). Emits one `face` region, padded out to a portrait
    crop around the detected face.

EVERY capability degrades gracefully. The heavy imports happen lazily inside
try/except *at call time*, never at module import. If a library is missing we
simply skip that capability and still return whatever else we found (possibly an
empty list) — importing or constructing this module must never raise because a
dependency is absent. That is what lets `server.app` import and boot with
nothing but FastAPI + Pillow installed.

The macOS target ships its own `Detector` (Apple Vision); both subclass the same
`core.detect.base.Detector`, so the server's detector factory can swap them.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import List, Optional

from PIL import Image

from core.detect.base import Detector, classify_field, is_mrz_line
from core.strategies.base import RegionSpec

# Where we cache the auto-downloaded YuNet ONNX model.
_MODEL_DIR = Path(os.environ.get("MAGIC_REDACT_MODEL_DIR", Path(__file__).parent / "models"))
_YUNET_FILE = "face_detection_yunet_2023mar.onnx"
# OpenCV Zoo mirror (stable raw GitHub URL).
_YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


def _availability() -> dict:
    """Report which optional capabilities can actually run, without importing
    them into the caller. Cheap, used by /healthz."""
    have_ocr = False
    have_face = False
    try:
        import rapidocr_onnxruntime  # noqa: F401
        have_ocr = True
    except Exception:
        pass
    try:
        import cv2  # noqa: F401
        have_face = hasattr(__import__("cv2"), "FaceDetectorYN")
    except Exception:
        pass
    return {"ocr": have_ocr, "face": have_face, "any": have_ocr or have_face}


class WinDetector(Detector):
    name = "win"

    def __init__(self, *, enable_ocr: bool = True, enable_face: bool = True):
        self.enable_ocr = enable_ocr
        self.enable_face = enable_face
        self._ocr = None          # lazily-built RapidOCR engine
        self._ocr_failed = False
        self._yunet_ready = None  # tri-state: None=untried, False=unavailable

    # -- public API ----------------------------------------------------------
    def detect(self, image: Image.Image) -> List[RegionSpec]:
        """Run every available capability and merge the regions. Never raises
        because a dependency is missing — worst case returns []."""
        img = image.convert("RGB")
        regions: List[RegionSpec] = []

        if self.enable_ocr:
            try:
                regions.extend(self._detect_text(img))
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[WinDetector] OCR pass skipped: {exc}")

        if self.enable_face:
            try:
                face = self._detect_face(img)
                if face is not None:
                    regions.insert(0, face)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[WinDetector] face pass skipped: {exc}")

        for i, r in enumerate(regions):
            if not r.id:
                r.id = f"r{i}"
        return regions

    @property
    def available(self) -> bool:
        return _availability()["any"]

    # -- text / OCR ----------------------------------------------------------
    def _get_ocr(self):
        if self._ocr is not None or self._ocr_failed:
            return self._ocr
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:
            self._ocr_failed = True
            print(f"[WinDetector] RapidOCR unavailable: {exc}")
            return None
        try:
            self._ocr = RapidOCR()
        except Exception as exc:  # pragma: no cover - defensive
            self._ocr_failed = True
            print(f"[WinDetector] RapidOCR init failed: {exc}")
            return None
        return self._ocr

    def _detect_text(self, img: Image.Image) -> List[RegionSpec]:
        engine = self._get_ocr()
        if engine is None:
            return []

        import numpy as np  # numpy ships with rapidocr/onnxruntime

        # RapidOCR wants BGR ndarray; PIL is RGB.
        arr = np.asarray(img)[:, :, ::-1].copy()
        result, _ = engine(arr)
        if not result:
            return []

        # Normalize each detection to (bbox=(x,y,w,h), text, score, center).
        lines = []
        for box, text, score in result:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, y0 = int(min(xs)), int(min(ys))
            x1, y1 = int(max(xs)), int(max(ys))
            lines.append(
                {
                    "bbox": (x0, y0, x1 - x0, y1 - y0),
                    "text": (text or "").strip(),
                    "score": float(score) if score is not None else 1.0,
                    "cx": (x0 + x1) / 2.0,
                    "cy": (y0 + y1) / 2.0,
                }
            )

        return self._lines_to_regions(lines, img.height)

    def _lines_to_regions(self, lines: list, page_h: int) -> List[RegionSpec]:
        """Turn raw OCR lines into field-tagged value regions.

        Heuristics (all best-effort, never fatal):
          * MRZ: any line matching the [A-Z0-9<] machine-readable pattern, and
            anything in the bottom ~22% of the page that looks MRZ-ish.
          * Labels (Surname, Date of birth, ...) are detected via classify_field
            with a label_hint; we DON'T emit the label itself as a region — we
            attach its field to the nearest value to the right/below.
          * Remaining value lines are classified on their own text.
        """
        regions: List[RegionSpec] = []
        labels = []   # (field, line) for recognized label words
        values = []   # candidate value lines

        bottom_band = page_h * 0.78

        idx = 0
        for ln in lines:
            text = ln["text"]
            if not text:
                continue

            # 1. MRZ lines.
            if is_mrz_line(text) or (ln["cy"] >= bottom_band and _looks_mrzish(text)):
                regions.append(
                    RegionSpec(
                        kind="mrz",
                        bbox=ln["bbox"],
                        id=f"mrz{idx}",
                        field="mrz",
                        text=text,
                        confidence=ln["score"],
                        meta={"source": "rapidocr"},
                    )
                )
                idx += 1
                continue

            # 2. Is this a label (keyword)? classify_field via label_hint.
            label_field = classify_field("", label_hint=text)
            looks_like_label = label_field != "unknown" and _is_short_label(text)
            if looks_like_label:
                labels.append({"field": label_field, **ln})
                continue

            values.append(ln)

        # 3. Attach each value to the closest label that sits above or to its
        #    left; otherwise classify the value's own text.
        for v in values:
            field = _classify_value(v, labels)
            kind = "mrz" if field == "mrz" else "text"
            regions.append(
                RegionSpec(
                    kind=kind,
                    bbox=v["bbox"],
                    id=f"t{idx}",
                    field=field,
                    text=v["text"],
                    confidence=v["score"],
                    meta={"source": "rapidocr"},
                )
            )
            idx += 1

        # 4. Disambiguate multiple date fields by vertical order:
        #    earliest=dob, then issue, then expiry (only when ambiguous).
        _refine_dates(regions)
        return regions

    # -- face / YuNet --------------------------------------------------------
    def _ensure_yunet_model(self) -> Optional[str]:
        path = _MODEL_DIR / _YUNET_FILE
        if path.exists() and path.stat().st_size > 0:
            return str(path)
        try:
            _MODEL_DIR.mkdir(parents=True, exist_ok=True)
            print(f"[WinDetector] downloading YuNet model -> {path}")
            with urllib.request.urlopen(_YUNET_URL, timeout=60) as resp:
                data = resp.read()
            if not data:
                return None
            path.write_bytes(data)
            return str(path)
        except Exception as exc:
            print(f"[WinDetector] YuNet model download failed: {exc}")
            return None

    def _detect_face(self, img: Image.Image) -> Optional[RegionSpec]:
        if self._yunet_ready is False:
            return None
        try:
            import cv2
            import numpy as np
        except Exception as exc:
            self._yunet_ready = False
            print(f"[WinDetector] OpenCV unavailable: {exc}")
            return None
        if not hasattr(cv2, "FaceDetectorYN"):
            self._yunet_ready = False
            print("[WinDetector] cv2.FaceDetectorYN not present (update opencv).")
            return None

        model_path = self._ensure_yunet_model()
        if not model_path:
            self._yunet_ready = False
            return None

        w, h = img.size
        try:
            detector = cv2.FaceDetectorYN.create(model_path, "", (w, h), 0.7, 0.3, 5000)
            arr = np.asarray(img)[:, :, ::-1].copy()  # RGB -> BGR
            detector.setInputSize((w, h))
            _, faces = detector.detect(arr)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[WinDetector] YuNet detect failed: {exc}")
            return None

        self._yunet_ready = True
        if faces is None or len(faces) == 0:
            return None

        # Pick the largest face.
        best = max(faces, key=lambda f: f[2] * f[3])
        fx, fy, fw, fh = (float(best[0]), float(best[1]), float(best[2]), float(best[3]))

        # Pad the tight face box out to a portrait crop (ID photos frame head +
        # shoulders). Expand more downward for shoulders, less upward.
        px = fw * 0.55
        crop_x = max(0, int(fx - px))
        crop_y = max(0, int(fy - fh * 0.6))
        crop_x1 = min(w, int(fx + fw + px))
        crop_y1 = min(h, int(fy + fh * 1.5))
        bbox = (crop_x, crop_y, crop_x1 - crop_x, crop_y1 - crop_y)

        return RegionSpec(
            kind="face",
            bbox=bbox,
            id="face",
            field="photo",
            confidence=float(best[-1]) if best[-1] is not None else 0.9,
            meta={"source": "yunet", "tight_face": [int(fx), int(fy), int(fw), int(fh)]},
        )


# --- module-level heuristics ------------------------------------------------

def _looks_mrzish(text: str) -> bool:
    """Loose MRZ test for bottom-band lines OCR may mangle (e.g. spaces)."""
    t = (text or "").upper().replace(" ", "")
    if len(t) < 20:
        return False
    allowed = sum(c.isalnum() or c == "<" for c in t)
    return allowed / max(1, len(t)) > 0.85 and t.count("<") >= 1


def _is_short_label(text: str) -> bool:
    """A label is typically a few words, no digits-as-value."""
    t = text.strip()
    return len(t) <= 24 and not any(ch.isdigit() for ch in t)


def _classify_value(value: dict, labels: list) -> str:
    """Best field for a value line: nearest label above/left, else self-classify."""
    vx, vy = value["cx"], value["cy"]
    best_label = None
    best_d = 1e9
    for lab in labels:
        lx, ly = lab["cx"], lab["cy"]
        # Value should be below or to the right of its label.
        below = ly <= vy + 8 and abs(lx - vx) < 260 and (vy - ly) < 120
        right = abs(ly - vy) < 28 and lx <= vx and (vx - lx) < 360
        if not (below or right):
            continue
        d = (lx - vx) ** 2 + (ly - vy) ** 2
        if d < best_d:
            best_d, best_label = d, lab
    if best_label is not None:
        return best_label["field"]

    f = classify_field(value["text"])
    return f if f != "unknown" else "unknown"


def _refine_dates(regions: List[RegionSpec]) -> None:
    """If several regions are generically tagged 'dob' (a date pattern), assign
    them top->bottom as dob / issue / expiry. Only touches ambiguous ones."""
    dates = [r for r in regions if r.field == "dob"]
    if len(dates) <= 1:
        return
    dates.sort(key=lambda r: r.bbox[1])
    order = ["dob", "expiry", "issue"]
    for i, r in enumerate(dates):
        r.field = order[i] if i < len(order) else "dob"


def availability() -> dict:
    """Public helper for the server's /healthz."""
    return _availability()
