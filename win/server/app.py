"""magic-redact — Windows/RTX FastAPI service.

Thin HTTP layer over the portable `core/` engine. It owns NO redaction logic:
detection comes from a pluggable `core.detect.base.Detector` (Windows default:
`win.detect_win.WinDetector`), substitution + watermark come from `core.redact`.

Endpoints
  GET  /healthz            -> {ok, detector_available, qwen_available, detector}
  POST /detect   (image)   -> {regions:[RegionSpec.to_dict()], width, height, detector_available}
  POST /redact   (image+json) -> PNG bytes (+ identity in X-Identity header)
  POST /identity           -> a fresh Identity.to_dict()
  GET  /                    -> the single-page web UI (win/web/)

Detector is pluggable via env var MAGIC_REDACT_DETECTOR:
  auto (default) | none | paddle | rapidocr   (paddle/rapidocr both -> WinDetector)
A future macOS build registers a `vision` detector behind the same factory with
NO change to this file.

Graceful degradation is a hard requirement: this module imports and boots with
nothing but FastAPI + Pillow installed. Detector deps (RapidOCR / OpenCV) and the
Qwen Tier-3 server are all optional and probed lazily.
"""
from __future__ import annotations

import base64
import importlib
import io
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from core import RegionSpec, generate_identity, redact
from core.identity import Identity

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_ROOT = Path(__file__).resolve().parents[2]
ASSETS_FACE_DIR = Path(os.environ.get("MAGIC_REDACT_FACE_DIR", _ROOT / "assets" / "faces"))
SAMPLES_DIR = _ROOT / "samples" / "images"
_SAMPLES_MANIFESTS = [_ROOT / "samples" / "manifest.json", SAMPLES_DIR / "manifest.json"]

app = FastAPI(title="magic-redact (win)", version="0.1.0")

# Lazily-built singletons.
_detector = None
_detector_kind = None


# --------------------------------------------------------------------------- #
# Detector factory — dynamic import so a mac `vision` backend can slot in.
# --------------------------------------------------------------------------- #
def _detector_env() -> str:
    return (os.environ.get("MAGIC_REDACT_DETECTOR", "auto") or "auto").strip().lower()


def _build_detector():
    """Return (detector_or_None, kind_str). Never raises."""
    choice = _detector_env()
    if choice == "none":
        return None, "none"

    # Map a backend name -> (module, class). Add mac here later without touching
    # the rest of the file: "vision": ("mac.detect_mac", "VisionDetector").
    registry = {
        "auto": ("win.detect_win", "WinDetector"),
        "paddle": ("win.detect_win", "WinDetector"),
        "rapidocr": ("win.detect_win", "WinDetector"),
        "win": ("win.detect_win", "WinDetector"),
    }
    target = registry.get(choice)
    if target is None:
        print(f"[app] unknown MAGIC_REDACT_DETECTOR={choice!r}; using auto.")
        target = registry["auto"]

    mod_name, cls_name = target
    try:
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        return cls(), choice
    except Exception as exc:
        print(f"[app] detector {mod_name}.{cls_name} unavailable: {exc}")
        return None, choice


def get_detector():
    global _detector, _detector_kind
    if _detector_kind is None:
        _detector, _detector_kind = _build_detector()
    return _detector


def _detector_available() -> bool:
    """True if a detector is configured AND can actually run (deps present)."""
    det = get_detector()
    if det is None:
        return False
    avail = getattr(det, "available", None)
    if isinstance(avail, bool):
        return avail
    # Fall back to the module-level helper if present.
    try:
        from win import detect_win
        return detect_win.availability()["any"]
    except Exception:
        return True  # a detector exists; assume usable.


def _qwen_available() -> bool:
    try:
        from win.diffusion_qwen import qwen_available
        return qwen_available()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_image(raw: bytes) -> Image.Image:
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _regions_from_json(items) -> list[RegionSpec]:
    """Build RegionSpec objects from posted JSON dicts. Tolerant of partial
    input (manual boxes from the UI carry only kind/bbox/field)."""
    out: list[RegionSpec] = []
    for i, it in enumerate(items or []):
        bbox = it.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x, y, w, h = (int(round(float(v))) for v in bbox)
        kind = it.get("kind") or "text"
        if kind not in ("face", "text", "mrz"):
            kind = "text"
        out.append(
            RegionSpec(
                kind=kind,
                bbox=(x, y, w, h),
                id=str(it.get("id") or f"r{i}"),
                field=str(it.get("field") or "unknown"),
                text=it.get("text"),
                confidence=float(it.get("confidence", 1.0)),
                meta=dict(it.get("meta") or {}),
            )
        )
    return out


def _identity_header(identity: Identity) -> str:
    """base64(JSON) of the identity, safe for an HTTP header value."""
    raw = json.dumps(identity.to_dict(), default=str).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "detector": _detector_env(),
        "detector_available": _detector_available(),
        "qwen_available": _qwen_available(),
        "face_library_count": _face_library_count(),
    }


def _face_library_count() -> int:
    try:
        exts = (".png", ".jpg", ".jpeg", ".webp")
        return sum(
            1 for p in ASSETS_FACE_DIR.glob("**/*") if p.suffix.lower() in exts
        ) if ASSETS_FACE_DIR.exists() else 0
    except Exception:
        return 0


@app.post("/detect")
async def detect(image: UploadFile = File(...)):
    raw = await image.read()
    try:
        img = _load_image(raw)
    except Exception as exc:
        return JSONResponse({"error": f"bad image: {exc}"}, status_code=400)

    det = get_detector()
    available = _detector_available()
    regions: list = []
    if det is not None and available:
        try:
            specs = det.detect(img)
            regions = [r.to_dict() for r in specs]
        except Exception as exc:
            # Never crash: degrade to manual mode.
            print(f"[app] detect failed, falling back to manual: {exc}")
            regions = []
            available = False

    return {
        "regions": regions,
        "width": img.width,
        "height": img.height,
        "detector_available": available,
        "detector": _detector_env(),
    }


@app.post("/redact")
async def redact_endpoint(
    image: UploadFile = File(...),
    regions: str = Form("[]"),
    seed: Optional[int] = Form(None),
    identity_seed: Optional[int] = Form(None),
    only: Optional[str] = Form(None),
    watermark: bool = Form(True),
    face_source: str = Form("library"),  # "library" | "qwen"
):
    raw = await image.read()
    try:
        img = _load_image(raw)
    except Exception as exc:
        return JSONResponse({"error": f"bad image: {exc}"}, status_code=400)

    try:
        region_items = json.loads(regions) if isinstance(regions, str) else regions
    except Exception as exc:
        return JSONResponse({"error": f"bad regions JSON: {exc}"}, status_code=400)

    specs = _regions_from_json(region_items)
    if not specs:
        return JSONResponse({"error": "no regions provided"}, status_code=400)

    only_ids = None
    if only:
        try:
            only_ids = json.loads(only) if only.strip().startswith("[") else [only]
        except Exception:
            only_ids = [only]

    # identity_seed makes re-rolls reproducible; seed feeds strategy RNG too.
    eff_seed = identity_seed if identity_seed is not None else seed
    identity = generate_identity(seed=eff_seed)

    # Optional Tier-3 Qwen face source, opt-in and fully degrade-safe.
    strategies = None
    if face_source == "qwen":
        strategies = _qwen_strategies()

    try:
        out_img, processed = redact(
            img,
            specs,
            identity=identity,
            strategies=strategies,
            only=only_ids,
            watermark=bool(watermark),
            seed=eff_seed,
        )
    except Exception as exc:
        return JSONResponse({"error": f"redact failed: {exc}"}, status_code=500)

    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    buf.seek(0)
    headers = {
        "X-Identity": _identity_header(identity),
        "X-Processed-Count": str(len(processed)),
    }
    return Response(content=buf.getvalue(), media_type="image/png", headers=headers)


def _qwen_strategies():
    """Strategy list with Qwen face generation in front of the defaults.
    Returns the portable defaults if the Qwen adapter can't load."""
    from core.pipeline import default_strategies
    base = default_strategies(str(ASSETS_FACE_DIR))
    try:
        from win.diffusion_qwen import QwenFaceStrategy
        return [QwenFaceStrategy()] + base
    except Exception as exc:
        print(f"[app] Qwen strategy unavailable, using library: {exc}")
        return base


@app.post("/identity")
def fresh_identity(seed: Optional[int] = Form(None)):
    return generate_identity(seed=seed).to_dict()


@app.get("/samples")
def samples():
    """List specimen test documents in samples/images/ for the UI gallery."""
    by_file = {}
    man = next((p for p in _SAMPLES_MANIFESTS if p.exists()), None)
    if man:
        try:
            for m in json.loads(man.read_text(encoding="utf-8")):
                if m.get("file"):
                    by_file[m["file"]] = m
        except Exception:
            by_file = {}
    items = []
    if SAMPLES_DIR.exists():
        exts = (".png", ".jpg", ".jpeg", ".webp")
        for p in sorted(SAMPLES_DIR.glob("*")):
            if p.suffix.lower() not in exts:
                continue
            m = by_file.get(p.name, {})
            items.append({
                "file": p.name,
                "url": f"/samples-img/{p.name}",
                "title": m.get("title") or p.name,
                "license": m.get("license", ""),
                "source_url": m.get("source_url", ""),
            })
    return {"items": items, "count": len(items)}


@app.get("/")
def index():
    idx = WEB_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse({"error": "web UI not found", "expected": str(idx)}, status_code=404)


# Serve static assets (app.js, style.css, ...) from win/web/ at /static.
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# Serve the specimen test-document images for the gallery.
if SAMPLES_DIR.exists():
    app.mount("/samples-img", StaticFiles(directory=str(SAMPLES_DIR)), name="samples-img")
