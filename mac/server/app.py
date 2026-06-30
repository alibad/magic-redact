"""Shared FastAPI server for magic-redact (platform-neutral).

This is the same HTTP service both targets run; it lives here in ``mac/server``
as the reference implementation the Mac uses, but it contains NO Mac-only code.
The only platform-specific piece — the detector — is chosen at startup by the
``MAGIC_REDACT_DETECTOR`` env var:

    MAGIC_REDACT_DETECTOR=vision   -> mac/detect_vision.py   (Apple Vision)
    MAGIC_REDACT_DETECTOR=win      -> win/detect_*.py         (RapidOCR/OpenCV)
    MAGIC_REDACT_DETECTOR=demo     -> built-in zero-dependency synthetic detector

Endpoints (the contract the SwiftUI / web front-ends call):

    GET  /health                       -> {"ok": true, "detector": "..."}
    POST /identity                     -> generate a fresh coherent identity
    POST /detect      (multipart img)  -> regions + identity + the image echoed
    POST /redact      (JSON)           -> redacted PNG (base64) for given regions
    POST /detect_redact (multipart)    -> one-shot: detect then redact all

Every redacted image carries the mandatory SPECIMEN watermark from core; this
server never exposes a way to disable it.

Run:
    cd magic-redact
    MAGIC_REDACT_DETECTOR=vision python -m uvicorn mac.server.app:app --port 8000

Dependencies beyond core (Pillow): fastapi, uvicorn, python-multipart.
See mac/server/requirements.txt.
"""
from __future__ import annotations

import base64
import importlib
import io
import os
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PIL import Image

from core import RegionSpec, generate_identity, redact
from core.identity import Identity

app = FastAPI(title="magic-redact", version="0.1.0")

# The SwiftUI app and any local web UI talk to this from the same machine;
# permissive CORS is fine because the server only ever binds locally.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Detector factory — the one platform swap, behind an env var.
# ---------------------------------------------------------------------------

_DETECTOR = None


def _build_demo_detector():
    """Zero-dependency fallback so the server boots and is testable anywhere
    (including Windows dev) without OCR/Vision installed. It returns no regions
    for a real upload; use it only to smoke-test the HTTP plumbing."""
    from core.detect.base import Detector

    class _DemoDetector(Detector):
        name = "demo"

        def detect(self, image):
            return []

    return _DemoDetector()


def get_detector():
    """Lazily construct the detector named by MAGIC_REDACT_DETECTOR."""
    global _DETECTOR
    if _DETECTOR is not None:
        return _DETECTOR

    name = os.environ.get("MAGIC_REDACT_DETECTOR", "demo").strip().lower()
    if name == "vision":
        # Imported lazily so the server starts on Windows even though the Vision
        # backend needs the Mac at run time.
        mod = importlib.import_module("mac.detect_vision")
        _DETECTOR = mod.build_detector()
    elif name == "win":
        mod = importlib.import_module("win.detect")  # defined by the win target
        _DETECTOR = mod.build_detector()
    elif name == "demo":
        _DETECTOR = _build_demo_detector()
    else:
        raise RuntimeError(f"Unknown MAGIC_REDACT_DETECTOR={name!r}")
    return _DETECTOR


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------

class RegionModel(BaseModel):
    id: str = ""
    kind: str
    bbox: List[int]
    field: str = "unknown"
    text: Optional[str] = None
    confidence: float = 1.0
    meta: dict = {}

    @classmethod
    def from_spec(cls, r: RegionSpec) -> "RegionModel":
        d = r.to_dict()
        return cls(**d)

    def to_spec(self) -> RegionSpec:
        x, y, w, h = self.bbox
        return RegionSpec(
            kind=self.kind, bbox=(x, y, w, h), id=self.id, field=self.field,
            text=self.text, confidence=self.confidence, meta=dict(self.meta or {}),
        )


class IdentityRequest(BaseModel):
    seed: Optional[int] = None
    sex: Optional[str] = None
    nationality_iso3: Optional[str] = None


class RedactRequest(BaseModel):
    image_b64: str                       # PNG/JPEG base64 (data URI ok)
    regions: List[RegionModel]
    identity: Optional[dict] = None      # an Identity.to_dict() to reuse; else seeded
    only: Optional[List[str]] = None     # region ids to redact; None = all
    seed: Optional[int] = None


# ---------------------------------------------------------------------------
# Image (de)serialization
# ---------------------------------------------------------------------------

def _decode_image(b64: str) -> Image.Image:
    if "," in b64[:32]:
        b64 = b64.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _encode_image(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _identity_from_dict(d: dict) -> Identity:
    """Rebuild an Identity from a to_dict() payload so the client can keep ONE
    coherent person across per-region edits and the 'redact all' click."""
    from datetime import date

    def _d(s: str) -> date:
        return date.fromisoformat(s)

    nat = d["nationality"]
    return Identity(
        sex=d["sex"],
        given_names=d["given_names"],
        surname=d["surname"],
        nationality_iso3=nat["iso3"],
        nationality_name=nat["name"],
        issuing_iso3=d["issuing_iso3"],
        dob=_d(d["dob"]),
        expiry=_d(d["expiry"]),
        issue=_d(d["issue"]),
        doc_number=d["doc_number"],
        place_of_birth=d["place_of_birth"],
        personal_number=d.get("personal_number", ""),
        seed=d.get("seed"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "detector": os.environ.get("MAGIC_REDACT_DETECTOR", "demo")}


@app.post("/identity")
def make_identity(req: IdentityRequest):
    idn = generate_identity(seed=req.seed, sex=req.sex,
                            nationality_iso3=req.nationality_iso3)
    return idn.to_dict()


@app.post("/detect")
async def detect_endpoint(image: UploadFile = File(...)):
    """Run the platform detector on an uploaded image. Returns the detected
    regions plus a fresh coherent identity and the echoed image, so the client
    can render the overlay and the side panel together."""
    data = await image.read()
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read image: {e}")

    detector = get_detector()
    try:
        regions = detector.detect(img)
    except Exception as e:  # noqa: BLE001 - surface backend errors clearly
        raise HTTPException(500, f"Detection failed: {e}")
    for i, r in enumerate(regions):
        if not r.id:
            r.id = f"r{i}"

    idn = generate_identity()
    return {
        "width": img.width,
        "height": img.height,
        "image_b64": _encode_image(img),
        "regions": [RegionModel.from_spec(r).model_dump() for r in regions],
        "identity": idn.to_dict(),
    }


@app.post("/redact")
def redact_endpoint(req: RedactRequest):
    """Redact specified regions of an image with a (possibly supplied) identity.
    The SPECIMEN watermark is always applied (mandatory safety property)."""
    img = _decode_image(req.image_b64)
    regions = [m.to_spec() for m in req.regions]
    identity = _identity_from_dict(req.identity) if req.identity else None

    out, processed = redact(
        img, regions,
        identity=identity,
        only=req.only,
        seed=req.seed,
        watermark=True,           # never disabled
    )
    return {
        "image_b64": _encode_image(out),
        "processed_ids": [r.id for r in processed],
    }


@app.post("/detect_redact")
async def detect_redact_endpoint(image: UploadFile = File(...)):
    """One-shot 'Redact all': detect then redact every region with a fresh
    coherent identity. Returns the redacted PNG, the identity, and the regions."""
    data = await image.read()
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read image: {e}")

    detector = get_detector()
    try:
        regions = detector.detect(img)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Detection failed: {e}")
    for i, r in enumerate(regions):
        if not r.id:
            r.id = f"r{i}"

    idn = generate_identity()
    out, processed = redact(img, regions, identity=idn, watermark=True)
    return {
        "image_b64": _encode_image(out),
        "identity": idn.to_dict(),
        "regions": [RegionModel.from_spec(r).model_dump() for r in regions],
        "processed_ids": [r.id for r in processed],
    }
