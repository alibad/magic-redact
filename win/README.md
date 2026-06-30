# magic-redact — Windows / RTX target

A FastAPI service + a no-build vanilla web UI that drive the portable `core/`
engine on Windows. Upload an ID-style document, auto-detect the identity-bearing
regions (portrait + text fields + MRZ), and replace them with **one coherent
synthetic person**. Every output carries the mandatory tiled **SPECIMEN**
watermark applied by `core` — that is a safety property and is never disabled by
this target.

Everything here lives under `win/` and reuses `core/` unchanged. The macOS target
(`mac/`) runs the *same server*; only the detector differs (Apple Vision there,
RapidOCR/YuNet here), both behind the same `core.detect.base.Detector` interface.

---

## Run

```bash
cd magic-redact

# Required deps only (server + portable engine — no ML):
python -m pip install -r win/requirements.txt

# Boot the server (default port 8099):
python -m uvicorn server.app:app --port 8099
# then open http://localhost:8099
```

The server imports and boots cleanly with **no optional deps installed** — you'll
get manual-box mode instead of auto-detection (see below). To enable
auto-detection:

```bash
pip install rapidocr-onnxruntime opencv-python
```

To enable the optional Tier-3 Qwen face source, start the local Qwen-Image server
(separate process) so it answers at `http://localhost:8021/health` — no extra
Python deps required.

---

## Architecture

```
win/
  server/app.py      FastAPI app. Thin HTTP layer; owns NO redaction logic.
  detect_win.py      WinDetector(Detector): RapidOCR text/boxes + YuNet face.
  diffusion_qwen.py  OPTIONAL Tier-3: fresh Qwen portrait inpainted into a face.
  web/               Single-page UI (index.html + style.css + app.js, no build).
  models/            Auto-downloaded YuNet ONNX cache (created on first face detect).
  requirements.txt   Required vs optional deps, clearly separated.
```

Request flow:

1. **`POST /detect`** runs `WinDetector.detect(image)`:
   - **RapidOCR** (`rapidocr-onnxruntime`) reads every text line + its box. Each
     line is mapped to a semantic `field` via `core.detect.base.classify_field`
     plus positional heuristics — labels sit above/left of their value; the long
     `[A-Z0-9<]` lines at the bottom are MRZ via `is_mrz_line`. Multiple date
     fields are ordered top→bottom into dob / expiry / issue.
   - **YuNet** (`cv2.FaceDetectorYN`) finds the portrait and emits a `face`
     region padded out to a head-and-shoulders crop.
2. The UI overlays the returned regions as color-coded, clickable boxes.
3. **`POST /redact`** builds `RegionSpec`s from the posted JSON and calls
   `core.redact(...)` with one coherent `Identity`. Returns a watermarked PNG.

### Endpoints

| Method | Path        | Purpose |
|--------|-------------|---------|
| GET    | `/healthz`  | `{ok, detector, detector_available, qwen_available, face_library_count}` |
| POST   | `/detect`   | multipart `image` → `{regions:[…], width, height, detector_available}` |
| POST   | `/redact`   | `image` + form (`regions` JSON, `seed?`, `identity_seed?`, `only?` ids, `watermark?`, `face_source=library\|qwen`) → **PNG bytes**. The identity used is returned base64-JSON in the `X-Identity` response header (and processed count in `X-Processed-Count`). |
| POST   | `/identity` | `seed?` → a fresh `Identity.to_dict()` (UI "Re-roll identity"). |
| GET    | `/`         | the web UI; static files served from `web/` at `/static`. |

`identity_seed` makes re-rolls reproducible: the UI sends the same seed it shows
in the side panel so a per-region redact and a "Redact all" use the *same* person.

---

## Graceful degradation (a hard requirement)

Optional libraries are imported **lazily, inside try/except, at call time** — never
at module import. Consequences:

- **No RapidOCR** → text/MRZ detection is skipped.
- **No OpenCV / `FaceDetectorYN`** → face detection is skipped (or the YuNet model
  can't download) → no face region.
- **Neither installed** → `/detect` returns `regions: []` with
  `detector_available: false`. The UI switches to **manual mode**: draw boxes on
  the canvas and tag each (face / a field name / MRZ), then redact. Nothing
  crashes; `python -c "import server.app"` succeeds with only FastAPI + Pillow.
- Even with deps installed, a per-call detector failure is caught and degrades to
  manual mode rather than erroring the request.

The substitution + **SPECIMEN watermark** always come from `core`, so the privacy
guarantee holds on every path (library face, pixelate fallback, or Qwen).

---

## Detector env var (pluggable backend)

`MAGIC_REDACT_DETECTOR` selects the backend via a small dynamic-import factory in
`server/app.py` (no code edits to swap):

| Value | Effect |
|-------|--------|
| `auto` (default) | `win.detect_win.WinDetector` (RapidOCR + YuNet). |
| `none` | No detector — UI is always manual mode. |
| `paddle` / `rapidocr` / `win` | Also `WinDetector` (alias names). |

A future macOS build registers `"vision": ("mac.detect_mac", "VisionDetector")`
in the same factory table; the server file needs no other change.

Other env vars: `MAGIC_REDACT_FACE_DIR` (face library, default `assets/faces`),
`MAGIC_REDACT_MODEL_DIR` (YuNet cache, default `win/models`),
`MAGIC_REDACT_QWEN_URL` (default `http://localhost:8021`).

---

## Tier-3 Qwen (optional) and its limitation

`win/diffusion_qwen.py` adds an opt-in face source. When the UI's **"Qwen face"**
checkbox is on (only shown if `/healthz` reports `qwen_available`), `/redact`
inserts a `QwenFaceStrategy` ahead of the library strategy: it asks the local
Qwen server (`POST /generate`, 600 s timeout, stdlib urllib) for a fresh
photorealistic synthetic portrait, seeded off the identity for reproducibility,
and feathers it into the face region.

**Important limitation.** The model running at `:8021` is **Qwen-Image
(text-to-image)**, *not* Qwen-Image-Edit. So Tier-3 here means "generate a new
portrait and inpaint it" — a higher-quality alternative to the static face
library — **not** instruction-based in-image editing. True **font-preserving text
edits** and **pose-matched face swaps** (keeping the original head pose/lighting)
require **Qwen-Image-Edit-2511** to be added later. Until then, all text fields
are handled by the portable Tier-1 `TextSubstituteStrategy`; Qwen only ever
supplies a face. If the Qwen server is down, the strategy declines and the
pipeline falls back to the library/classic face path — the app works fully
without it.

---

## Verify

```bash
# Imports cleanly with NO optional deps:
python -c "import server.app; print('import OK')"

# Boot:
python -m uvicorn server.app:app --port 8099

# In another shell:
curl http://localhost:8099/healthz
curl -X POST http://localhost:8099/identity -F seed=42
# Round-trip a redaction on the demo sample (writes a watermarked PNG):
python demo.py                                   # creates out/demo_before.png
curl -X POST http://localhost:8099/redact \
  -F image=@out/demo_before.png \
  -F 'regions=[{"id":"photo","kind":"face","bbox":[40,110,230,290],"field":"photo"},{"id":"f0","kind":"text","bbox":[300,132,320,34],"field":"surname"}]' \
  -F identity_seed=42 -o out/redacted.png
```
