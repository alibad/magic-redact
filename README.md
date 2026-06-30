# magic-redact

**One-click document anonymization that runs entirely on your own hardware.**

Upload an ID-style document (passport, license, badge). The app finds the
identity-bearing regions — the portrait and each text field — and replaces them
with a *single coherent synthetic person*. Hover a region to change just that
one; click **Redact all** to swap everything at once. No data ever leaves the
machine.

This is a **privacy / anonymization** tool. Every output is stamped with a
visible, tiled **SPECIMEN** watermark so results are unmistakably synthetic and
cannot be passed off as genuine documents. That stamp is a deliberate safety
property and is on by default.

---

## How it works — the 3-tier strategy

Redaction is *substitution*, not generation. You rarely need a heavy model:

| Tier | What | Needs a model? | Where |
|------|------|----------------|-------|
| **1 — Library** | Drop a curated synthetic **face** (aspect-fit, tone-matched, feathered) and render **text** from name/place pools, with a coherent identity (name ↔ MRZ ↔ dates all agree) | No | default |
| **2 — Classic** | Pixelate / blur / block / gradient — the guaranteed fallback when no asset fits | No | always available |
| **3 — Diffusion** | Qwen-Image-Edit (font-preserving text edit, pose-matched face swap) for max realism | Yes (optional) | per-region upgrade |

The "magic" is Tier 1 + **one coherent fake identity** applied across every field
in a single click — fast, deterministic, fully offline.

## Layout

```
magic-redact/
  core/      PORTABLE engine — pure Python, Pillow-only. Runs on Windows AND macOS.
             identity (coherent fake person + MRZ), strategies (tier 1/2),
             detect interface + field classifier, compose (watermark), pipeline.
  win/       THIS DEVICE target (Windows + RTX 5090): FastAPI service, PaddleOCR
             detection, optional Qwen Tier-3, web UI.            [see win/README.md]
  mac/       MAC target (Apple Silicon): Apple Vision detection, Draw Things
             Tier-3, SwiftUI/web front-end.                      [see mac/README.md]
  assets/    Synthetic face library (generated once by the local Qwen server) +
             name/place pools live in core/pools.
```

**Both targets share `core/` unchanged.** Only two things differ per platform and
both sit behind interfaces: the **detector** (PaddleOCR ↔ Apple Vision) and the
optional **diffusion backend** (Qwen ↔ Draw Things). Build/test on Windows, then
continue the Mac front-end on the Mac — the hard logic is already done and shared.

## Quickstart (works right now, no ML)

```bash
cd magic-redact
python -m pip install -r core/requirements.txt    # just Pillow

# 1. See the whole engine work on a synthesized sample document:
python demo.py                                     # -> out/demo_before.png, out/demo_after.png

# 2. Build the face library with the local Qwen server (http://localhost:8021):
python assets/gen/generate_faces.py --count 12     # small seed batch
python assets/gen/generate_faces.py --count 200 --start-seed 2000   # full pool

# 3. Run a platform target — see win/README.md or mac/README.md
```

## Status

- [x] Portable core: identity + MRZ (ICAO 9303 check digits verified), tier 1/2
      strategies, watermark, pipeline, runnable demo.
- [x] Qwen face-pool generator.
- [ ] `win/` — FastAPI + detection + web UI  *(in progress)*
- [ ] `mac/` — Apple Vision + Draw Things + front-end  *(scaffold for on-device continuation)*
- [ ] Tier-3 diffusion adapters (Qwen / Draw Things).

## Core interfaces (for the platform targets)

```python
from core import generate_identity, redact, detect_and_redact, RegionSpec
from core.detect.base import Detector          # subclass per platform -> List[RegionSpec]

regions = [RegionSpec(kind="face"|"text"|"mrz", bbox=(x,y,w,h), field="surname", text="...")]
identity = generate_identity(seed=42)           # coherent fake person, .to_dict(), .value_for(field)
out_img, processed = redact(image, regions, identity=identity)   # PIL.Image in, PIL.Image out
```
