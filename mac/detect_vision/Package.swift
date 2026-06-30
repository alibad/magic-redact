// swift-tools-version:5.7
// SwiftPM manifest for the VisionRegions command-line helper.
//
//   cd mac/detect_vision
//   swift build -c release
//   ./.build/release/VisionRegions /path/to/document.png
//
// The product name is "VisionRegions"; the compiled binary lands at
//   .build/release/VisionRegions
// which is the path mac/detect_vision.py looks for by default
// (override with the MAGIC_REDACT_VISION_BIN env var).
import PackageDescription

let package = Package(
    name: "VisionRegions",
    platforms: [
        .macOS(.v11)   // VNRecognizeTextRequest .accurate needs 10.15; .v11 for language tuning
    ],
    targets: [
        .executableTarget(
            name: "VisionRegions",
            path: "Sources/VisionRegions"
        )
    ]
)
