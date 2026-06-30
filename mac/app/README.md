# magic-redact — macOS SwiftUI app (native shell)

These are **authored Swift sources**, not a buildable Xcode project. They are a
thin native shell over the shared local FastAPI server. You create the Xcode
project (or a SwiftPM executable) on the Mac and drop these files in.

## What the app is

A thin client. All heavy logic stays in the shared server (`server/app.py`,
run with `MAGIC_REDACT_DETECTOR=vision`):

- detection → Apple Vision (via the `VisionRegions` Swift helper the server calls
  through `mac/detect_vision.py`);
- the coherent synthetic identity, the redaction strategies, and the mandatory
  **SPECIMEN** watermark → `core/`.

The app loads an image, calls `/detect`, draws tappable region boxes, and calls
`/redact` (whole image or a single region) and `/identity`. It never reimplements
the engine — one source of truth, shared with the Windows target.

## Files

| File | Role |
|------|------|
| `MagicRedact/MagicRedactApp.swift` | `@main` SwiftUI `App` entry. |
| `MagicRedact/Models.swift` | Codable mirrors of the server JSON (`Region`, `SyntheticIdentity`, responses). |
| `MagicRedact/RedactClient.swift` | Async HTTP client for `/health`, `/detect`, `/redact`, `/identity`, `/detect_redact`. |
| `MagicRedact/ContentView.swift` | UI: image load, tappable region overlay, per-region edit, **Redact all**, **Re-roll identity**, **Download**, identity side panel. |

## Create the Xcode project (recommended path)

1. Xcode → **File ▸ New ▸ Project… ▸ macOS ▸ App**.
   - Product Name: `MagicRedact`
   - Interface: **SwiftUI**, Language: **Swift**, no Core Data/Tests needed.
2. Delete the auto-generated `ContentView.swift` and `*App.swift`.
3. **Add Files to "MagicRedact"…** → add the four files from `MagicRedact/` here.
   (Or drag the `MagicRedact/` folder in; uncheck "Copy items" if you want them to
   stay in the repo.)
4. **App Sandbox / networking**: in *Signing & Capabilities*, if the App Sandbox
   is on, enable **Outgoing Connections (Client)** so the app can reach the local
   server. Localhost HTTP is allowed by ATS for `127.0.0.1`; if you point the app
   at a LAN IP over plain HTTP, add an ATS exception for that host in `Info.plist`.
5. Build & Run (⌘R). The app opens; point it at the server (default
   `http://127.0.0.1:8000`) — see the field in the view model.

## Or: SwiftPM executable (no Xcode UI)

You can wrap these in a `Package.swift` with an `.executableTarget`, but a SwiftUI
`@main` app is far easier to run as a `.app` via Xcode. The Vision **helper**
(`mac/detect_vision/`) is already SwiftPM and is separate from this app — the app
does not call Vision directly; the server does.

## Notes / what to finish on-device

- The geometry mapping (image-pixel boxes → fitted on-screen rect) is implemented
  in `ContentView.swift`; verify it against a real document once Vision returns
  boxes (it assumes the image is shown `aspectRatio(.fit)`).
- `ContentUnavailableView` is gated for macOS 14+ via a small shim.
- AppKit image helpers (`pngData`, `imageFromBase64`) are under `#if canImport(AppKit)`.
- Drag-and-drop and an in-app server-URL field are easy adds; the view model
  already exposes `serverURL`.
