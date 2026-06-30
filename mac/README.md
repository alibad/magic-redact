# magic-redact — macOS (Apple Silicon) target  ·  THE RUNBOOK

This is the Mac target of **magic-redact**, a 100%-local document-anonymization
tool. The portable engine in `../core/` is shared, unchanged, with the Windows
target. Only **two** things differ on the Mac, both behind interfaces:

| Piece | Windows | macOS (this target) |
|-------|---------|---------------------|
| **Detector** | RapidOCR / OpenCV | **Apple Vision** (`VNRecognizeTextRequest` + `VNDetectFaceRectangles`) |
| **Tier-3 diffusion** (optional) | local Qwen server | **Draw Things** (Qwen-Image-Edit on Apple Silicon; optional Server Offload to the RTX 5090) |

The shared FastAPI server is platform-neutral Python; on the Mac you run it
**as-is** and select the detector with one env var:

```bash
export MAGIC_REDACT_DETECTOR=vision
```

> Safety property (never change): every redacted image gets a tiled, visible
> **SPECIMEN** watermark from `core`. The server applies it unconditionally and
> exposes no way to disable it.

---

## What's already done vs. what you finish on the Mac

**Done (authored on Windows, import-tested there):**
- `detect_vision.py` — the `vision` `Detector`: shells out to the Swift helper,
  parses its JSON, builds `RegionSpec`s, assigns each text region a `field`
  (label-left/value-right pairing + MRZ detection + date refinement). Field-
  assignment logic is unit-exercised.
- `detect_vision/VisionRegions.swift` (+ `Package.swift`) — the Apple Vision
  bridge. **Authored, not compiled** (no Swift toolchain on Windows).
- `diffusion_drawthings.py` — Draw Things Tier-3 adapter (health + txt2img +
  portrait implemented; instruction-edit stubbed pending on-device API check).
- `server/app.py` — the shared FastAPI service with the detector factory keyed on
  `MAGIC_REDACT_DETECTOR`. Verified end-to-end on Windows with the `demo` detector.
- `app/MagicRedact/*.swift` — SwiftUI shell (entry, models, HTTP client, UI).
  **Authored, not compiled.**

**You finish on the Mac (needs the device):**
1. `swift build` the Vision helper and confirm it prints JSON for a real image.
2. Run the server with `MAGIC_REDACT_DETECTOR=vision`, confirm `/detect` returns
   Vision regions on a real ID image.
3. (Optional) Install Draw Things, enable its API, verify the Tier-3 adapter;
   finish the stubbed instruction-edit path.
4. Create the Xcode project from the Swift sources, point it at the server, run.

---

## Prerequisites

- **Xcode** + Command Line Tools: `xcode-select --install` (provides `swift`).
- **Python 3.10+** (3.12 recommended).
- A virtualenv is recommended:
  ```bash
  cd magic-redact
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r core/requirements.txt          # just Pillow
  pip install -r mac/server/requirements.txt     # fastapi, uvicorn, multipart
  ```

---

## Step 1 — Prove the engine is portable (no ML, no Vision)

Run the core demo on the Mac. This exercises identity + MRZ + tier-1/2 strategies
+ the watermark + pipeline with **zero** platform code:

```bash
cd magic-redact
python demo.py
open out/demo_after.png      # SPECIMEN-stamped, fields swapped to one fake person
```

If that renders, `core/` runs unchanged on your Mac — the whole hard part is done
and shared.

---

## Step 2 — Build & test the Apple Vision helper

```bash
cd magic-redact/mac/detect_vision
swift build -c release
# Binary lands at: .build/release/VisionRegions

# Smoke-test it on any image with text / a face:
./.build/release/VisionRegions /path/to/an-id-photo.png | python3 -m json.tool
```

You should see JSON like:

```json
{
  "width": 1024,
  "height": 680,
  "regions": [
    {"kind": "face", "bbox": [40, 110, 230, 290], "text": null, "confidence": 0.97},
    {"kind": "text", "bbox": [300, 134, 198, 30], "text": "GARCIA", "confidence": 0.93}
  ]
}
```

Notes:
- `bbox` is `[x, y, w, h]` in **top-left pixel** coordinates (the Swift side
  converts from Vision's bottom-left normalized space). This is exactly what
  `detect_vision.py` expects — the contract matches by construction.
- Languages: `./VisionRegions --langs en-US,fr-FR <image>` (defaults to `en-US`).
- First run may prompt for permissions / be slightly slow as Vision warms up.

If `swift build` can't find Vision, confirm `xcode-select -p` points at a full
Xcode (not just CLT) — `VNRecognizeTextRequest` needs the macOS SDK.

---

## Step 3 — Run the shared server with the Vision detector

```bash
cd magic-redact
source .venv/bin/activate
export MAGIC_REDACT_DETECTOR=vision
# Optional overrides:
#   export MAGIC_REDACT_VISION_BIN=/abs/path/to/VisionRegions   # if not in default build dir
#   export MAGIC_REDACT_VISION_LANGS=en-US,fr-FR

python -m uvicorn mac.server.app:app --port 8000
```

Confirm it works:

```bash
curl -s http://127.0.0.1:8000/health           # {"ok":true,"detector":"vision"}

# Detect on a real ID image:
curl -s -F "image=@/path/to/an-id-photo.png" http://127.0.0.1:8000/detect \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['regions']),'regions'); print(d['identity']['given_names'], d['identity']['surname'])"
```

`/detect` returns the regions, a fresh coherent identity, and the echoed image.
The server resolves the `VisionRegions` binary automatically from the default
build dir; if you built elsewhere, set `MAGIC_REDACT_VISION_BIN`.

Endpoints the front-end uses:
- `GET  /health`
- `POST /identity`          → new coherent person
- `POST /detect`            (multipart `image`) → regions + identity + image
- `POST /redact`            (JSON) → redacted PNG (base64); `only:[ids]` for one
- `POST /detect_redact`     (multipart `image`) → one-shot "Redact all"

---

## Step 4 — (Optional) Draw Things Tier-3 diffusion

Tier-3 is an *optional* per-region realism upgrade. The default tier-1/2 path
needs no model and already produces a coherent, watermarked result.

1. **Install Draw Things** (Mac App Store) and open it.
2. **Download a Qwen-Image / Qwen-Image-Edit model** in-app. For instruction
   edits you want a **Qwen-Image-Edit** checkpoint. Add a **Lightning LoRA** for
   fast sampling (few steps) on Apple Silicon.
3. **Enable the API server**: Draw Things → settings → enable the HTTP API
   (default `http://127.0.0.1:7860`, A1111-compatible).
   - **Server Offload / Bridge** (optional): to run the model on the **RTX 5090**
     box instead of the Mac, enable Draw Things' Server Offload / gRPCServerCLI
     bridge and point it at the RTX machine's LAN IP. From this code's view it's
     still just an HTTP endpoint — only the URL changes:
     ```bash
     export MAGIC_REDACT_DRAWTHINGS_URL=http://<rtx-box-lan-ip>:7860
     ```
4. **Wire the adapter** (`mac/diffusion_drawthings.py`):
   ```bash
   export MAGIC_REDACT_DRAWTHINGS_URL=http://127.0.0.1:7860   # or the RTX IP
   export MAGIC_REDACT_DRAWTHINGS_MODEL=<your qwen-image-edit model name>
   export MAGIC_REDACT_DRAWTHINGS_LORA=<lightning-lora-name>  # optional, for speed
   python mac/diffusion_drawthings.py      # prints URL + reachable: true/false
   ```
   - `generate_portrait()` and `txt2img()` are **implemented** but unverified
     on-device — field names may need a tweak for your Draw Things build.
   - `instruction_edit_region()` is **stubbed**: the request body is assembled,
     but the exact Qwen-Image-Edit API surface must be confirmed on the device
     (img2img + an edit/instruction field). Follow the inline TODO, then remove
     the `raise NotImplementedError` and test against a region crop.

   **Model size / RAM tradeoff:** Qwen-Image-Edit is large. Aim for **≥24–36 GB
   unified memory** for comfortable full-precision runs; with less, use a more
   quantized model and/or a **Lightning LoRA** (fewer steps) to keep it fast.
   Tier-3 is optional — skip it if RAM is tight; tier-1/2 still produce great
   results.

---

## Step 5 — Build & run the SwiftUI app

The Swift sources are in `mac/app/MagicRedact/`. See `mac/app/README.md` for the
exact steps to create the Xcode project and add the files. In short:

1. Xcode → New ▸ Project ▸ macOS ▸ App (SwiftUI), name `MagicRedact`.
2. Replace the generated `*App.swift`/`ContentView.swift` by **adding** the four
   files from `mac/app/MagicRedact/`.
3. If App Sandbox is on, enable **Outgoing Connections (Client)** so the app can
   reach the local server.
4. Make sure the server from Step 3 is running, then ⌘R. The app defaults to
   `http://127.0.0.1:8000`.
5. **Open** an ID image → **Detect** (boxes appear) → tap a box to redact just it,
   or **Redact all**, **Re-roll** the identity, **Download** the result.

---

## Troubleshooting

- **`VisionRegions helper binary not found`** (from Python): build Step 2, or set
  `MAGIC_REDACT_VISION_BIN` to the compiled binary.
- **`The Apple Vision detector only runs on macOS`**: you ran the `vision`
  detector on a non-Mac host. That's expected; use the Windows backend there.
- **`swift build` can't find `Vision`**: `sudo xcode-select -s /Applications/Xcode.app`
  to point at full Xcode, not just Command Line Tools.
- **Empty/odd regions**: tune `--langs`, and check the image isn't rotated; the
  field heuristics assume a roughly upright ID layout.
- **Draw Things `reachable: false`**: confirm its API server is enabled and the
  URL/port match `MAGIC_REDACT_DRAWTHINGS_URL`.

---

## Layout (this target)

```
mac/
  detect_vision/
    Package.swift                       SwiftPM manifest for the helper
    Sources/VisionRegions/VisionRegions.swift   Apple Vision -> JSON bridge
  detect_vision.py                      `vision` Detector (shells out to the helper)
  diffusion_drawthings.py               optional Tier-3 Draw Things adapter
  server/
    app.py                              shared, platform-neutral FastAPI service
    requirements.txt                    fastapi / uvicorn / multipart
  app/
    README.md                           how to create the Xcode project
    MagicRedact/*.swift                 SwiftUI shell (entry, models, client, UI)
  README.md                             this runbook
```
