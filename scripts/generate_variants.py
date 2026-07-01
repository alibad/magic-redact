"""Generate N synthetic VARIANTS of a document — same layout, different content.

Detects the regions once, then re-runs the redaction pipeline in *replace* mode
(every text field swapped for new, format-preserving content; the face swapped
from the library) with a different coherent identity each time. Because we
generate the content, we know the ground truth: each variant gets a labels JSON
with the exact value written into every field box.

    python scripts/generate_variants.py --input samples/images/xxx.jpg --count 8
    python scripts/generate_variants.py --input doc.png --count 20 --augment

Outputs to out/variants/: <stem>_vNNN.png + <stem>_vNNN.json

SAFETY: outputs keep the mandatory SPECIMEN watermark by default and are a
labelled *synthetic dataset*, not passable documents. --no-watermark exists for
research pipelines but is a deliberate, logged choice.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import generate_identity, redact, replace_strategies  # noqa: E402

FACES_DIR = ROOT / "assets" / "faces"


def get_regions(img, want_detector: bool):
    if not want_detector:
        return []
    try:
        from win.detect_win import WinDetector
    except Exception as e:
        print(f"[warn] detector unavailable ({e}); no regions.")
        return []
    regions = WinDetector().detect(img)
    for i, r in enumerate(regions):
        if not r.id:
            r.id = f"r{i}"
    return regions


def augment(img, rng):
    """Label-preserving photometric jitter (no geometry, so bboxes stay valid):
    simulate different capture conditions for training robustness."""
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.85, 1.15))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.9, 1.15))
    img = ImageEnhance.Color(img).enhance(rng.uniform(0.9, 1.1))
    if rng.random() < 0.4:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="source document image")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--seed-start", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "out" / "variants"))
    ap.add_argument("--augment", action="store_true", help="photometric jitter per variant")
    ap.add_argument("--no-watermark", dest="watermark", action="store_false")
    ap.add_argument("--no-detect", dest="detect", action="store_false",
                    help="skip detection (nothing to replace unless you add regions)")
    args = ap.parse_args()

    src = Path(args.input)
    img = Image.open(src).convert("RGB")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    regions = get_regions(img, args.detect)
    print(f"source {src.name}  {img.width}x{img.height}  regions={len(regions)}")
    if not regions:
        print("[warn] no regions detected -> variants would be identical. Aborting.")
        return 1

    strategies = replace_strategies(str(FACES_DIR))
    import random
    stem = src.stem

    for i in range(args.count):
        seed = args.seed_start + i
        identity = generate_identity(seed=seed)
        out_img, processed = redact(
            img, regions,
            identity=identity, strategies=strategies,
            watermark=args.watermark, seed=seed,
        )
        if args.augment:
            out_img = augment(out_img, random.Random(seed))

        png = out_dir / f"{stem}_v{seed:03d}.png"
        out_img.save(png)
        labels = {
            "source": src.name,
            "seed": seed,
            "width": img.width,
            "height": img.height,
            "watermarked": bool(args.watermark),
            "augmented": bool(args.augment),
            "identity": identity.to_dict(),
            "fields": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "field": r.field,
                    "bbox": list(r.bbox),
                    "value": r.meta.get("value"),          # exact text written (None for face)
                    "strategy": r.meta.get("strategy"),
                }
                for r in processed
            ],
        }
        (out_dir / f"{stem}_v{seed:03d}.json").write_text(json.dumps(labels, indent=2), encoding="utf-8")
        print(f"  [{i + 1}/{args.count}] {png.name}  ({identity.full_name}, {identity.nationality_iso3}, "
              f"{len(processed)} fields)")

    wm = "watermarked" if args.watermark else "NO WATERMARK"
    print(f"Done. {args.count} variants ({wm}) + labels in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
