"""End-to-end core demo — NO detection model, NO image model required.

Synthesizes a fake passport-style document, redacts it with the portable
pipeline (Tier-1 text substitution + face library, Tier-2 fallback), and writes
out/demo_before.png and out/demo_after.png.

    cd magic-redact && python demo.py

If assets/faces is empty the portrait is pixelated (classic fallback); generate
the face pool first to see real substitution:
    python assets/gen/generate_faces.py --count 12
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from core import RegionSpec, generate_identity, redact
from core import fonts

OUT = Path("out")
W, H = 1024, 680


def build_sample():
    """Draw a believable (entirely fictional) passport page and return
    (image, regions). All values here are placeholders to be redacted."""
    img = Image.new("RGB", (W, H), (244, 241, 232))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 64), fill=(40, 58, 90))
    d.text((28, 18), "PASSPORT  /  PASSEPORT", font=fonts.sans(28), fill=(240, 240, 245))

    regions: list[RegionSpec] = []

    # Photo box (face region).
    px, py, pw, ph = 40, 110, 230, 290
    d.rectangle((px, py, px + pw, py + ph), fill=(150, 150, 156))
    d.text((px + 60, py + 135), "PHOTO", font=fonts.sans(26), fill=(90, 90, 96))
    regions.append(RegionSpec(kind="face", bbox=(px, py, pw, ph), id="photo", field="photo"))

    fields = [
        ("Surname", "PLACEHOLDER", "surname"),
        ("Given names", "SAMPLE NAME", "given_names"),
        ("Nationality", "UTOPIA", "nationality"),
        ("Date of birth", "01 JAN 1990", "dob"),
        ("Sex", "X", "sex"),
        ("Place of birth", "SAMPLE CITY", "place_of_birth"),
        ("Passport No.", "X0000000", "doc_number"),
        ("Date of expiry", "01 JAN 2030", "expiry"),
    ]
    col_x = 300
    y = 110
    for i, (label, value, key) in enumerate(fields):
        x = col_x + (i % 2) * 360
        if i % 2 == 0 and i > 0:
            y += 88
        d.text((x, y), label.upper(), font=fonts.sans(15), fill=(120, 120, 128))
        vbox = (x, y + 22, 320, 34)
        d.text((x + 2, y + 24), value, font=fonts.sans(26), fill=(20, 20, 28))
        regions.append(RegionSpec(kind="text", bbox=vbox, id=f"f{i}", field=key, text=value))

    # MRZ — two mono lines across the bottom.
    mrz_y = H - 96
    d.rectangle((0, mrz_y - 12, W, H), fill=(250, 248, 242))
    sample_mrz = ["P<UTOPLACEHOLDER<<SAMPLE<NAME<<<<<<<<<<<<<<<<", "X0000000" + "0UTO9001011X3001011<<<<<<<<<<<<<<00"]
    for li, line in enumerate(sample_mrz):
        ly = mrz_y + li * 40
        d.text((24, ly), line, font=fonts.mono(28), fill=(20, 20, 24))
        regions.append(RegionSpec(kind="mrz", bbox=(24, ly, W - 48, 34), id=f"mrz{li}", field="mrz", text=line))

    return img, regions


def main():
    OUT.mkdir(exist_ok=True)
    img, regions = build_sample()
    img.save(OUT / "demo_before.png")

    identity = generate_identity(seed=42)
    print("Synthetic identity:")
    for k, v in identity.to_dict().items():
        print(f"  {k}: {v}")

    out, processed = redact(img, regions, identity=identity, seed=42)
    out.save(OUT / "demo_after.png")
    print(f"\nRedacted {len(processed)}/{len(regions)} regions "
          f"(strategies: {sorted({r.meta.get('strategy', 'classic') for r in processed})})")
    print(f"Wrote {OUT/'demo_before.png'} and {OUT/'demo_after.png'}")


if __name__ == "__main__":
    main()
