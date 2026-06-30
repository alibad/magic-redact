"""magic-redact — shared FastAPI service (Windows + macOS).

ONE server both platforms run. Thin HTTP layer over the portable `core/` engine;
it owns NO redaction logic. The only platform-specific piece — the detector — is
chosen at startup by the MAGIC_REDACT_DETECTOR env var; substitution + watermark
come from `core.redact`, and the web UI in `web/` is the cross-platform front-end.

Endpoints
  GET  /healthz | /health  -> {ok, detector_available, qwen_available, detector}
  POST /detect   (image)   -> {regions:[RegionSpec.to_dict()], width, height, detector_available}
  POST /redact   (image+json) -> PNG bytes (+ identity in X-Identity header)
  POST /detect_redact (image) -> one-shot detect+redact-all -> PNG bytes
  POST /identity           -> a fresh Identity.to_dict()
  GET  /samples            -> specimen test-document gallery list
  GET  /                    -> the single-page web UI (web/)

Detector is pluggable via env var MAGIC_REDACT_DETECTOR:
  auto/win/rapidocr/paddle -> win.detect_win (RapidOCR + OpenCV YuNet, Windows)
  vision                   -> mac.detect_vision (Apple Vision, macOS)
  demo                     -> built-in no-op (boots/HTTP-testable anywhere)
  none                     -> no detector (manual boxes only)
Backends are imported lazily, so the server boots on either OS without the
other's deps. Graceful degradation is a hard requirement: this module imports and
boots with nothing but FastAPI + Pillow installed.
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

_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = _ROOT / "web"
ASSETS_FACE_DIR = Path(os.environ.get("MAGIC_REDACT_FACE_DIR", _ROOT / "assets" / "faces"))
SAMPLES_DIR = _ROOT / "samples" / "images"
_SAMPLES_MANIFESTS = [_ROOT / "samples" / "manifest.json", SAMPLES_DIR / "manifest.json"]

app = FastAPI(title="magic-redact", version="0.2.0")

# Lazily-built singletons.
_detector = None
_detector_kind = None


# --------------------------------------------------------------------------- #
# Detector factory — dynamic import so a mac `vision` backend can slot in.
# --------------------------------------------------------------------------- #
def _detector_env() -> str:
    return (os.environ.get("MAGIC_REDACT_DETECTOR", "auto") or "auto").strip().lower()


def _demo_detector():
    """Zero-dependency no-op detector so the server boots and is HTTP-testable on
    any machine. Returns no regions (use manual boxes / 'draw' mode with it)."""
    from core.detect.base import Detector

    class _DemoDetector(Detector):
        name = "demo"
        available = True

        def detect(self, image):
            return []

    return _DemoDetector()


def _build_detector():
    """Return (detector_or_None, kind_str). Never raises. Backends are imported
    lazily so the server boots on either OS without the other's deps."""
    choice = _detector_env()
    if choice == "none":
        return None, "none"
    if choice == "demo":
        return _demo_detector(), "demo"

    # name -> (module, callable). The callable is a class or a build_*() factory;
    # either way calling it with no args yields a core.detect.base.Detector.
    registry = {
        "auto": ("win.detect_win", "WinDetector"),
        "win": ("win.detect_win", "WinDetector"),
        "paddle": ("win.detect_win", "WinDetector"),
        "rapidocr": ("win.detect_win", "WinDetector"),
        "vision": ("mac.detect_vision", "build_detector"),
    }
    target = registry.get(choice)
    if target is None:
        print(f"[app] unknown MAGIC_REDACT_DETECTOR={choice!r}; using auto.")
        target = registry["auto"]

    mod_name, attr = target
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)(), choice
    except Exception as exc:
        print(f"[app] detector {mod_name}.{attr} unavailable: {exc}")
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
    return avail if isinstance(avail, bool) else True  # detector exists; assume usable


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


@app.get("/health")
def health():
    """Alias of /healthz (some clients / the SwiftUI shell expect /health)."""
    return healthz()


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


@app.post("/detect_redact")
async def detect_redact_endpoint(
    image: UploadFile = File(...),
    seed: Optional[int] = Form(None),
    watermark: bool = Form(True),
    face_source: str = Form("library"),
):
    """One-shot: detect every region then redact them all with one coherent
    identity. Returns the watermarked PNG (+ identity in X-Identity header)."""
    raw = await image.read()
    try:
        img = _load_image(raw)
    except Exception as exc:
        return JSONResponse({"error": f"bad image: {exc}"}, status_code=400)

    det = get_detector()
    if det is None:
        return JSONResponse({"error": "no detector configured"}, status_code=422)
    try:
        specs = det.detect(img)
    except Exception as exc:
        return JSONResponse({"error": f"detection failed: {exc}"}, status_code=500)
    for i, r in enumerate(specs):
        if not r.id:
            r.id = f"r{i}"
    if not specs:
        return JSONResponse({"error": "nothing detected"}, status_code=422)

    identity = generate_identity(seed=seed)
    strategies = _qwen_strategies() if face_source == "qwen" else None
    out_img, processed = redact(
        img, specs, identity=identity, strategies=strategies,
        watermark=bool(watermark), seed=seed,
    )
    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    buf.seek(0)
    headers = {
        "X-Identity": _identity_header(identity),
        "X-Processed-Count": str(len(processed)),
    }
    return Response(content=buf.getvalue(), media_type="image/png", headers=headers)


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


# Serve static assets (app.js, style.css, ...) from web/ at /static.
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# Serve the specimen test-document images for the gallery.
if SAMPLES_DIR.exists():
    app.mount("/samples-img", StaticFiles(directory=str(SAMPLES_DIR)), name="samples-img")
