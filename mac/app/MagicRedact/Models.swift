// Models.swift — Codable mirrors of the server's JSON contract.
//
// These match server/app.py exactly:
//   * RegionModel / RegionSpec.to_dict()  -> Region
//   * Identity.to_dict()                  -> SyntheticIdentity
//   * /detect and /detect_redact responses

import Foundation
import CoreGraphics

/// One editable area found in the document. bbox is [x, y, w, h] in TOP-LEFT
/// pixel coordinates of the ORIGINAL image (same space the server uses).
struct Region: Codable, Identifiable, Equatable {
    let id: String
    let kind: String          // "face" | "text" | "mrz"
    let bbox: [Int]           // [x, y, w, h]
    var field: String
    var text: String?
    var confidence: Double

    var rect: CGRect {
        guard bbox.count == 4 else { return .zero }
        return CGRect(x: bbox[0], y: bbox[1], width: bbox[2], height: bbox[3])
    }

    static func == (a: Region, b: Region) -> Bool { a.id == b.id }
}

/// The coherent synthetic person (Identity.to_dict()).
struct SyntheticIdentity: Codable, Equatable {
    struct Nationality: Codable, Equatable {
        let iso3: String
        let name: String
    }
    let sex: String
    let given_names: String
    let surname: String
    let nationality: Nationality
    let issuing_iso3: String
    let dob: String
    let expiry: String
    let issue: String
    let doc_number: String
    let place_of_birth: String
    let personal_number: String
    let mrz: [String]
    let seed: Int?

    var fullName: String { "\(given_names) \(surname)" }
}

/// Response of POST /detect.
struct DetectResponse: Codable {
    let width: Int
    let height: Int
    let image_b64: String
    let regions: [Region]
    let identity: SyntheticIdentity
}

/// Response of POST /redact.
struct RedactResponse: Codable {
    let image_b64: String
    let processed_ids: [String]
}

/// Response of POST /detect_redact.
struct DetectRedactResponse: Codable {
    let image_b64: String
    let identity: SyntheticIdentity
    let regions: [Region]
    let processed_ids: [String]
}
