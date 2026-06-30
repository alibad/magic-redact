// MagicRedactApp.swift — SwiftUI entry point for the macOS native shell.
//
// Architecture (see mac/app/README.md): this app is a THIN native shell. All the
// heavy logic — detection (Apple Vision via the Swift helper), the coherent
// synthetic identity, the redaction strategies, and the mandatory SPECIMEN
// watermark — lives in the shared local FastAPI server (server/app.py) run
// with MAGIC_REDACT_DETECTOR=vision. The app just loads an image, calls the
// server's /detect, /redact, /identity endpoints, and draws the result.
//
// Why a shell instead of reimplementing in Swift: the engine is already written,
// tested, and shared with the Windows target. Re-deriving identity/MRZ/strategy
// logic in Swift would fork the safety-critical bits (e.g. the watermark). The
// shell keeps one source of truth.

import SwiftUI

@main
struct MagicRedactApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 900, minHeight: 600)
        }
        .windowStyle(.titleBar)
    }
}
