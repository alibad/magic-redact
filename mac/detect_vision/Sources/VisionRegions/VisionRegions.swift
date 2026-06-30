// VisionRegions.swift — the Apple Vision bridge for magic-redact (macOS / Apple Silicon).
//
// A tiny command-line helper that the Python `mac/detect_vision.py` adapter shells
// out to. It runs two native, fully-local Vision requests on an image:
//
//   * VNRecognizeTextRequest      (recognitionLevel .accurate)  -> text + boxes
//   * VNDetectFaceRectanglesRequest                             -> face boxes
//
// and prints a single JSON object on stdout matching the contract the Python side
// parses (see detect_vision.py):
//
//   {
//     "width":  Int,
//     "height": Int,
//     "regions": [
//       {"kind":"text","bbox":[x,y,w,h],"text":"...","confidence":0.93},
//       {"kind":"face","bbox":[x,y,w,h],"text":null,"confidence":1.0}
//     ]
//   }
//
// IMPORTANT coordinate note: Vision returns normalized boxes in a BOTTOM-LEFT
// origin (0,0 = bottom-left, 1,1 = top-right). magic-redact / Pillow use a
// TOP-LEFT origin in pixels. We convert here so Python receives ready-to-use
// (x, y, w, h) pixel boxes with y measured from the top.
//
// Build:   swift build -c release   (see ../Package.swift)
// Run:     ./VisionRegions /path/to/document.png
//          ./VisionRegions --langs en,fr /path/to/document.png
//
// This file is authored on Windows and compiled on the Mac. It requires macOS
// 10.15+ for VNRecognizeTextRequest .accurate; recognitionLanguages tuning and
// automatic language detection require macOS 11+.

import Foundation
import Vision
import CoreGraphics
import ImageIO

#if canImport(AppKit)
import AppKit
#endif

// MARK: - Output model

struct OutRegion: Codable {
    let kind: String          // "text" | "face"
    let bbox: [Int]           // [x, y, w, h] top-left pixel coords
    let text: String?         // recognized text for "text", nil for "face"
    let confidence: Double
}

struct OutPayload: Codable {
    let width: Int
    let height: Int
    let regions: [OutRegion]
}

// MARK: - Helpers

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    // Emit a valid-but-empty JSON payload on stdout too, so a caller that only
    // reads stdout still gets parseable JSON and a clear error on stderr.
    let empty = OutPayload(width: 0, height: 0, regions: [])
    if let data = try? JSONEncoder().encode(empty), let s = String(data: data, encoding: .utf8) {
        print(s)
    }
    exit(2)
}

/// Load a CGImage from a file path (PNG/JPEG/HEIC/etc. via ImageIO).
func loadCGImage(_ path: String) -> CGImage {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else {
        fail("Could not open image source: \(path)")
    }
    guard let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        fail("Could not decode image: \(path)")
    }
    return cg
}

/// Vision returns a normalized rect with a BOTTOM-LEFT origin. Convert to a
/// TOP-LEFT pixel rect (x, y, w, h) clamped to the image bounds.
func toTopLeftPixels(_ normalized: CGRect, width: Int, height: Int) -> [Int] {
    let W = Double(width)
    let H = Double(height)
    let x = normalized.origin.x * W
    // Flip Y: Vision y is from the bottom; we want distance from the top.
    let yTop = (1.0 - normalized.origin.y - normalized.size.height) * H
    let w = normalized.size.width * W
    let h = normalized.size.height * H

    let xi = max(0, min(Int(x.rounded()), width))
    let yi = max(0, min(Int(yTop.rounded()), height))
    var wi = max(0, Int(w.rounded()))
    var hi = max(0, Int(h.rounded()))
    if xi + wi > width { wi = width - xi }
    if yi + hi > height { hi = height - yi }
    return [xi, yi, wi, hi]
}

// MARK: - Argument parsing

var args = Array(CommandLine.arguments.dropFirst())
var languages: [String] = ["en-US"]
var imagePath: String? = nil

var i = 0
while i < args.count {
    let a = args[i]
    if a == "--langs", i + 1 < args.count {
        languages = args[i + 1]
            .split(separator: ",")
            .map { String($0).trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        i += 2
    } else if a == "-h" || a == "--help" {
        print("Usage: VisionRegions [--langs en-US,fr-FR] <image-path>")
        exit(0)
    } else {
        imagePath = a
        i += 1
    }
}

guard let path = imagePath else {
    fail("Usage: VisionRegions [--langs en-US,fr-FR] <image-path>")
}

let cgImage = loadCGImage(path)
let width = cgImage.width
let height = cgImage.height

// MARK: - Vision requests

var regions: [OutRegion] = []

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

// 1) Text recognition (accurate). Each observation's boundingBox is the line box;
//    topCandidates(1) gives the recognized string + a confidence.
let textRequest = VNRecognizeTextRequest()
textRequest.recognitionLevel = .accurate
textRequest.usesLanguageCorrection = true
if #available(macOS 11.0, *) {
    textRequest.recognitionLanguages = languages
    // Leave automaticallyDetectsLanguage off so results are deterministic for
    // the language list we pass; flip on if you want broader coverage.
}

// 2) Face rectangles (portrait detection for the photo region).
let faceRequest = VNDetectFaceRectanglesRequest()

do {
    try handler.perform([textRequest, faceRequest])
} catch {
    fail("Vision perform() failed: \(error.localizedDescription)")
}

// Collect text observations.
if let results = textRequest.results {
    for obs in results {
        guard let top = obs.topCandidates(1).first else { continue }
        let bbox = toTopLeftPixels(obs.boundingBox, width: width, height: height)
        if bbox[2] <= 0 || bbox[3] <= 0 { continue }
        regions.append(OutRegion(
            kind: "text",
            bbox: bbox,
            text: top.string,
            confidence: Double(top.confidence)
        ))
    }
}

// Collect face observations.
if let results = faceRequest.results {
    for obs in results {
        let bbox = toTopLeftPixels(obs.boundingBox, width: width, height: height)
        if bbox[2] <= 0 || bbox[3] <= 0 { continue }
        regions.append(OutRegion(
            kind: "face",
            bbox: bbox,
            text: nil,
            confidence: Double(obs.confidence)
        ))
    }
}

// MARK: - Emit JSON

let payload = OutPayload(width: width, height: height, regions: regions)
let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
do {
    let data = try encoder.encode(payload)
    if let s = String(data: data, encoding: .utf8) {
        print(s)
    } else {
        fail("Could not stringify JSON output")
    }
} catch {
    fail("JSON encoding failed: \(error.localizedDescription)")
}
