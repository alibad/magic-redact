# samples/ — test documents for redaction

A corpus of **specimen / sample identity documents** (passport data pages,
national ID cards, driving licences, residence permits, visas) pulled from
Wikimedia Commons, to test detection + redaction across real-world layouts that
the synthetic `demo.py` document can't represent.

## Ethics & licensing

These are government **SPECIMEN** pages and **sample** documents containing dummy
data and artificially generated faces/text — **not real people's identity
documents**. Harvesting real IDs is exactly what magic-redact exists to prevent.

`manifest.json` records, per file: the Commons `title`, `source_url`,
`image_url`, `license`, and `artist/credit`. Respect each file's license if you
redistribute; this corpus is intended for **local testing only**.

## Layout

```
samples/
  fetch_specimens.py   # the downloader (committed)
  manifest.json        # provenance + license for every file (committed)
  images/              # the document images themselves (GITIGNORED — re-pull anytime)
```

The images are gitignored to keep the repo light and avoid redistributing
third-party files; re-fetch them on any machine with:

```bash
python samples/fetch_specimens.py --limit 100        # paced; resumes if interrupted
python samples/fetch_specimens.py --limit 100 --delay 1.5   # gentler if rate-limited
```

The fetcher paces requests (1s default) and backs off on HTTP 429, and writes
the manifest incrementally so a rate-limit/crash never loses progress.

## Using them to test the app

- **Web UI:** start the win server (`python -m uvicorn server.app:app --port 8100`),
  open it, and drag any file from `samples/images/` in → Detect → Redact all.
- **Batch:** point a detector run at the folder to see how auto-detection holds
  up across passports vs. ID cards vs. licences (good for tuning the field
  heuristics in `win/detect_win.py`).

These real layouts are deliberately harder than the synthetic demo — expect the
detector to miss or mislabel some fields. That's the signal for what to tune,
and the per-region / manual-box UI is the safety net for anything missed.
