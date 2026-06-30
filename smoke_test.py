"""Smoke test for the shared HTTP server — stdlib only (urllib).

Synthesizes an ID (the demo passport + a real library face pasted into the photo
box), then exercises the LIVE server end to end: /healthz, /detect, /redact.
Writes out/smoke_redacted.png. Falls back to the demo's known regions if the
detector finds nothing, so the redact path is always exercised.

    .venv/Scripts/python.exe -m uvicorn server.app:app --port 8100        # terminal 1
    MR_BASE=http://127.0.0.1:8100 .venv/Scripts/python.exe smoke_test.py  # terminal 2
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))
BASE = os.environ.get("MR_BASE", "http://127.0.0.1:8100")


def build_test_image():
    """Demo passport with a real generated face in the photo box -> (path, known_regions)."""
    from demo import build_sample

    img, regions = build_sample()
    faces = sorted((ROOT / "assets" / "faces").glob("*.png"))
    if faces:
        face = Image.open(faces[0]).convert("RGB").resize((230, 290))
        img.paste(face, (40, 110))
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    p = out_dir / "test_id.png"
    img.save(p)
    return p, [r.to_dict() for r in regions]


def _encode_multipart(fields, files):
    boundary = b"----magicredactsmoke"
    body = b""
    for k, v in fields.items():
        body += b"--" + boundary + b"\r\n"
        body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
        body += str(v).encode() + b"\r\n"
    for k, (fn, data, ct) in files.items():
        body += b"--" + boundary + b"\r\n"
        body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (k, fn)).encode()
        body += ("Content-Type: %s\r\n\r\n" % ct).encode()
        body += data + b"\r\n"
    body += b"--" + boundary + b"--\r\n"
    return body, "multipart/form-data; boundary=" + boundary.decode()


def post(path, fields, files):
    body, ct = _encode_multipart(fields, files)
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": ct}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def wait_up(tries=60):
    for _ in range(tries):
        try:
            return get_json("/healthz")
        except Exception:
            time.sleep(0.5)
    raise SystemExit(f"server never came up at {BASE}")


def main():
    print("[1/3] waiting for server ...")
    h = wait_up()
    print("      healthz:", json.dumps(h))

    img_path, known = build_test_image()
    data = img_path.read_bytes()

    print("[2/3] POST /detect ...")
    _, _, body = post("/detect", {}, {"image": ("test_id.png", data, "image/png")})
    det = json.loads(body)
    regions = det.get("regions") or []
    print(f"      detector_available={det.get('detector_available')}  regions={len(regions)}")
    for r in regions:
        txt = (r.get("text") or "")[:26]
        print(f"        {r['kind']:5} {r['field']:14} bbox={r['bbox']} text={txt!r}")

    use = regions if regions else known
    src = "detected" if regions else "known-demo-fallback"
    print(f"[3/3] POST /redact  ({len(use)} regions, {src}) ...")
    st, hdrs, body = post(
        "/redact",
        {"regions": json.dumps(use), "watermark": "true", "identity_seed": "7"},
        {"image": ("test_id.png", data, "image/png")},
    )
    out = ROOT / "out" / "smoke_redacted.png"
    out.write_bytes(body)
    Image.open(io.BytesIO(body)).verify()
    print(f"      redact status={st}  processed={hdrs.get('x-processed-count')}")
    print(f"      wrote {out} ({len(body)} bytes) - valid PNG OK")
    print("SMOKE OK")


if __name__ == "__main__":
    main()
