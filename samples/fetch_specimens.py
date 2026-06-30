"""Fetch ~100 SPECIMEN / sample identity documents from Wikimedia Commons to
test redaction on real-world layouts. Stdlib only (urllib).

ETHICS: these are government *specimen* pages and *sample* documents with dummy
data and artificially generated faces/text — NOT real people's IDs. Harvesting
real identity documents is exactly what magic-redact exists to prevent. The
manifest records each file's source URL + license.

    python samples/fetch_specimens.py --limit 100

Writes images to samples/images/ and samples/images/manifest.json (provenance).
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "images"
API = "https://commons.wikimedia.org/w/api.php"
UA = "magic-redact-test-fetcher/0.1 (https://github.com/alibad/magic-redact)"

# Search terms chosen to surface specimen ID-style documents across many
# countries and document types (passport, national ID, licence, permit, visa).
QUERIES = [
    "specimen passport",
    "passport specimen data page",
    "specimen identity card",
    "specimen national identity card",
    "specimen driving licence",
    "specimen driver license",
    "sample passport",
    "specimen residence permit",
    "specimen visa",
    "specimen identity document",
    "passport data page specimen",
    "identity card specimen",
]


def api(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch(url: str, tries: int = 6) -> bytes:
    """GET with exponential backoff on 429/503 (Wikimedia rate-limits bots)."""
    delay = 3.0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < tries - 1:
                time.sleep(delay)
                delay *= 1.8
                continue
            raise
    return b""


def search_titles(query: str, limit: int = 40) -> list[str]:
    out, offset = [], 0
    while len(out) < limit:
        d = api({"action": "query", "list": "search", "srsearch": query,
                 "srnamespace": 6, "srlimit": 50, "sroffset": offset})
        hits = d.get("query", {}).get("search", [])
        if not hits:
            break
        out += [h["title"] for h in hits]
        cont = d.get("continue", {}).get("sroffset")
        if cont is None:
            break
        offset = cont
    return out[:limit]


def imageinfo(titles: list[str]) -> dict:
    info = {}
    for i in range(0, len(titles), 40):
        batch = titles[i:i + 40]
        d = api({"action": "query", "prop": "imageinfo", "titles": "|".join(batch),
                 "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 1400})
        for page in d.get("query", {}).get("pages", {}).values():
            ii = (page.get("imageinfo") or [None])[0]
            if ii:
                info[page["title"]] = ii
    return info


def slug(title: str) -> str:
    s = re.sub(r"^File:", "", title)
    s = re.sub(r"\.[A-Za-z0-9]+$", "", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return s[:56] or "doc"


def ext_from(url: str) -> str:
    m = re.search(r"\.(png|jpg|jpeg)(?:$|\?)", url.lower())
    return (".jpg" if not m else "." + m.group(1)).replace(".jpeg", ".jpg")


def clean(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between downloads")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    seen, titles = set(), []
    for q in QUERIES:
        for t in search_titles(q, 40):
            if t not in seen:
                seen.add(t)
                titles.append(t)
    print(f"candidate files: {len(titles)}")

    info = imageinfo(titles)
    # Resume from any prior run; write the manifest incrementally so progress
    # survives a rate-limit/crash.
    mpath = HERE / "manifest.json"   # committable provenance; images/ is gitignored
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else []
    done = {m["title"] for m in manifest}
    n = len(manifest)

    for t in titles:
        if n >= args.limit:
            break
        if t in done:
            continue
        ii = info.get(t)
        if not ii:
            continue
        # Prefer the rasterized thumbnail (works for PDF/large originals too).
        url = ii.get("thumburl") or ii.get("url")
        if not url:
            continue
        try:
            data = fetch(url)
        except Exception as e:
            print(f"  [x] {t}: {e}")
            continue
        if len(data) < 2000:  # error page / placeholder
            continue
        fname = f"{n:03d}_{slug(t)}{ext_from(url)}"
        (OUT / fname).write_bytes(data)
        em = ii.get("extmetadata", {}) or {}
        manifest.append({
            "file": fname,
            "title": t,
            "source_url": ii.get("descriptionurl") or ii.get("url"),
            "image_url": url,
            "mime": ii.get("mime", ""),
            "license": clean((em.get("LicenseShortName", {}) or {}).get("value", "")),
            "artist": clean((em.get("Artist", {}) or {}).get("value", ""))[:140],
            "width": ii.get("thumbwidth") or ii.get("width"),
            "height": ii.get("thumbheight") or ii.get("height"),
        })
        done.add(t)
        n += 1
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if n % 10 == 0:
            print(f"  [{n}/{args.limit}] downloaded")
        time.sleep(args.delay)
    print(f"Done. {n} specimen documents in {OUT}")


if __name__ == "__main__":
    main()
