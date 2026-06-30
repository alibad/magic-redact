// RedactClient.swift — networking client for the shared local FastAPI server.
//
// NOTE (post-consolidation): the canonical server is `server/app.py`, and its
// /detect & /redact use multipart upload returning PNG bytes (+ X-Identity
// header) — see web/app.js for the exact contract. This client still uses the
// earlier JSON/base64 shapes. The simplest Mac front-end TODAY is the SERVED WEB
// UI (run the server with MAGIC_REDACT_DETECTOR=vision and open its URL in
// Safari). To finish this native shell, align the calls below with web/app.js.
//
// Base URL defaults to the local server; change it in one place to point at a
// server running elsewhere on the LAN.

import Foundation
#if canImport(AppKit)
import AppKit
#endif

enum RedactError: Error, LocalizedError {
    case badResponse(String)
    case http(Int, String)
    case encoding

    var errorDescription: String? {
        switch self {
        case .badResponse(let s): return "Bad response: \(s)"
        case .http(let code, let body): return "HTTP \(code): \(body)"
        case .encoding: return "Could not encode/decode image"
        }
    }
}

/// Thin async client. One instance per app; baseURL is configurable.
final class RedactClient {
    let baseURL: URL
    private let session: URLSession

    init(baseURL: URL = URL(string: "http://127.0.0.1:8000")!) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 120   // detection/diffusion can be slow
        self.session = URLSession(configuration: cfg)
    }

    // MARK: - Health

    func health() async throws -> Bool {
        let (data, resp) = try await session.data(from: baseURL.appendingPathComponent("health"))
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return false }
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (obj?["ok"] as? Bool) ?? false
    }

    // MARK: - Detect (multipart image upload)

    func detect(imageData: Data, filename: String = "upload.png") async throws -> DetectResponse {
        let req = multipartRequest(path: "detect", imageData: imageData, filename: filename)
        return try await send(req)
    }

    func detectAndRedactAll(imageData: Data, filename: String = "upload.png") async throws -> DetectRedactResponse {
        let req = multipartRequest(path: "detect_redact", imageData: imageData, filename: filename)
        return try await send(req)
    }

    // MARK: - Redact (JSON)

    /// Redact the given regions of an image. Pass `only` to redact a subset (a
    /// single region for per-region edit); pass the existing `identity` so every
    /// edit stays consistent with the same synthetic person.
    func redact(imageBase64: String,
                regions: [Region],
                identity: SyntheticIdentity?,
                only: [String]? = nil,
                seed: Int? = nil) async throws -> RedactResponse {
        var body: [String: Any] = [
            "image_b64": imageBase64,
            "regions": regions.map(regionDict),
        ]
        if let identity { body["identity"] = try identityDict(identity) }
        if let only { body["only"] = only }
        if let seed { body["seed"] = seed }

        var req = URLRequest(url: baseURL.appendingPathComponent("redact"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await send(req)
    }

    // MARK: - Identity

    func newIdentity(seed: Int? = nil) async throws -> SyntheticIdentity {
        var req = URLRequest(url: baseURL.appendingPathComponent("identity"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = [:]
        if let seed { body["seed"] = seed }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await send(req)
    }

    // MARK: - Plumbing

    private func send<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw RedactError.badResponse("no HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw RedactError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func multipartRequest(path: String, imageData: Data, filename: String) -> URLRequest {
        let boundary = "magicredact-\(UUID().uuidString)"
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"image\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: image/png\r\n\r\n")
        body.append(imageData)
        append("\r\n--\(boundary)--\r\n")
        req.httpBody = body
        return req
    }

    private func regionDict(_ r: Region) -> [String: Any] {
        [
            "id": r.id,
            "kind": r.kind,
            "bbox": r.bbox,
            "field": r.field,
            "text": r.text as Any,
            "confidence": r.confidence,
            "meta": [:],
        ]
    }

    private func identityDict(_ i: SyntheticIdentity) throws -> [String: Any] {
        let data = try JSONEncoder().encode(i)
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw RedactError.encoding
        }
        return obj
    }
}

// MARK: - Image helpers (AppKit)

#if canImport(AppKit)
extension NSImage {
    /// PNG data for upload.
    var pngData: Data? {
        guard let tiff = tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .png, properties: [:])
    }
}

extension Data {
    var base64Image: String { base64EncodedString() }
}

/// Decode a base64 PNG (as the server returns) into an NSImage.
func imageFromBase64(_ b64: String) -> NSImage? {
    var s = b64
    if let comma = s.firstIndex(of: ","), s.hasPrefix("data:") {
        s = String(s[s.index(after: comma)...])
    }
    guard let data = Data(base64Encoded: s) else { return nil }
    return NSImage(data: data)
}
#endif
