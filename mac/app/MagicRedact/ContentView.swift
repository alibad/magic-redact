// ContentView.swift — the main screen.
//
// Left: the document with a tappable overlay of detected region boxes. Tap a box
// to redact JUST that region (server /redact with `only:[id]`). Toolbar:
//   * Open…           load an image
//   * Detect          POST /detect -> draw region boxes + identity
//   * Redact all      POST /detect_redact (or /redact with all ids)
//   * Re-roll         POST /identity -> new coherent person (re-redact uses it)
//   * Download        save the current (redacted) image
// Right: a side panel showing the current synthetic identity.
//
// This is a skeleton: it compiles conceptually against the models/client above
// and is meant to be opened in Xcode and finished on-device (see README).

import SwiftUI
#if canImport(AppKit)
import AppKit
import UniformTypeIdentifiers
#endif

@MainActor
final class RedactViewModel: ObservableObject {
    @Published var serverURL: String = "http://127.0.0.1:8000"
    @Published var originalImage: NSImage?      // what we uploaded
    @Published var displayImage: NSImage?       // original or redacted preview
    @Published var imagePixelSize: CGSize = .zero
    @Published var regions: [Region] = []
    @Published var identity: SyntheticIdentity?
    @Published var status: String = "Open a document to begin."
    @Published var busy = false
    @Published var hoveredRegionID: String?

    private var client: RedactClient { RedactClient(baseURL: URL(string: serverURL) ?? URL(string: "http://127.0.0.1:8000")!) }
    private var currentBase64: String?          // base64 of displayImage for /redact

    func loadImage(_ image: NSImage, data: Data) {
        originalImage = image
        displayImage = image
        imagePixelSize = pixelSize(of: image)
        currentBase64 = data.base64EncodedString()
        regions = []
        identity = nil
        status = "Loaded. Tap Detect to find regions."
    }

    func detect() async {
        guard let data = originalImage?.pngData else { return }
        await run("Detecting…") {
            let resp = try await self.client.detect(imageData: data)
            self.regions = resp.regions
            self.identity = resp.identity
            self.currentBase64 = resp.image_b64
            if let img = imageFromBase64(resp.image_b64) { self.displayImage = img }
            self.imagePixelSize = CGSize(width: resp.width, height: resp.height)
            self.status = "Found \(resp.regions.count) region(s)."
        }
    }

    func redactAll() async {
        // Use the already-detected regions + current identity if we have them,
        // else do a one-shot detect+redact.
        if !regions.isEmpty, let b64 = currentBase64 {
            await run("Redacting all…") {
                let resp = try await self.client.redact(
                    imageBase64: b64, regions: self.regions, identity: self.identity)
                self.applyRedaction(resp.image_b64)
                self.status = "Redacted \(resp.processed_ids.count) region(s)."
            }
        } else if let data = originalImage?.pngData {
            await run("Redacting all…") {
                let resp = try await self.client.detectAndRedactAll(imageData: data)
                self.regions = resp.regions
                self.identity = resp.identity
                self.applyRedaction(resp.image_b64)
                self.status = "Redacted \(resp.processed_ids.count) region(s)."
            }
        }
    }

    /// Redact a single region (the per-region hover/tap edit).
    func redactOne(_ region: Region) async {
        guard let b64 = currentBase64 else { return }
        await run("Redacting \(region.field)…") {
            let resp = try await self.client.redact(
                imageBase64: b64, regions: self.regions,
                identity: self.identity, only: [region.id])
            self.applyRedaction(resp.image_b64)
            self.status = "Redacted \(region.field)."
        }
    }

    func rerollIdentity() async {
        await run("Re-rolling identity…") {
            let idn = try await self.client.newIdentity()
            self.identity = idn
            self.status = "New identity: \(idn.fullName). Tap Redact all to apply."
        }
    }

    func download() {
        #if canImport(AppKit)
        guard let img = displayImage, let png = img.pngData else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = "redacted.png"
        if panel.runModal() == .OK, let url = panel.url {
            try? png.write(to: url)
            status = "Saved to \(url.lastPathComponent)."
        }
        #endif
    }

    // MARK: - helpers

    private func applyRedaction(_ b64: String) {
        currentBase64 = b64
        if let img = imageFromBase64(b64) { displayImage = img }
    }

    private func run(_ label: String, _ work: @escaping () async throws -> Void) async {
        busy = true; status = label
        defer { busy = false }
        do { try await work() }
        catch { status = "Error: \(error.localizedDescription)" }
    }

    private func pixelSize(of image: NSImage) -> CGSize {
        if let rep = image.representations.first {
            return CGSize(width: rep.pixelsWide, height: rep.pixelsHigh)
        }
        return image.size
    }
}

struct ContentView: View {
    @StateObject private var vm = RedactViewModel()

    var body: some View {
        HSplitView {
            documentPane
                .frame(minWidth: 560)
            identityPanel
                .frame(width: 300)
        }
        .toolbar { toolbarContent }
        .overlay(alignment: .bottom) {
            Text(vm.status)
                .font(.caption)
                .padding(6)
                .background(.thinMaterial, in: Capsule())
                .padding(.bottom, 8)
        }
    }

    // MARK: - Document pane with tappable region overlay

    private var documentPane: some View {
        GeometryReader { geo in
            ZStack {
                if let img = vm.displayImage {
                    Image(nsImage: img)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                    overlay(in: geo.size)
                } else {
                    ContentUnavailableViewCompat(
                        "No document",
                        systemImage: "doc.viewfinder",
                        description: "Open an ID-style image to begin.")
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .windowBackgroundColor))
        }
    }

    /// Draw a tappable rectangle per region, scaled from image-pixel space to the
    /// on-screen fitted-image rect.
    private func overlay(in viewSize: CGSize) -> some View {
        let fit = fittedImageRect(imagePixel: vm.imagePixelSize, in: viewSize)
        return ZStack(alignment: .topLeading) {
            ForEach(vm.regions) { region in
                let r = scaledRect(region.rect, fit: fit, pixel: vm.imagePixelSize)
                Rectangle()
                    .stroke(color(for: region), lineWidth: vm.hoveredRegionID == region.id ? 3 : 1.5)
                    .background(Rectangle().fill(color(for: region).opacity(0.08)))
                    .frame(width: r.width, height: r.height)
                    .position(x: r.midX, y: r.midY)
                    .onHover { vm.hoveredRegionID = $0 ? region.id : nil }
                    .onTapGesture { Task { await vm.redactOne(region) } }
                    .help("\(region.field)\(region.text.map { ": \($0)" } ?? "")")
            }
        }
        .frame(width: viewSize.width, height: viewSize.height)
    }

    private func color(for region: Region) -> Color {
        switch region.kind {
        case "face": return .blue
        case "mrz":  return .purple
        default:     return .orange
        }
    }

    // MARK: - Identity side panel

    private var identityPanel: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Synthetic identity").font(.headline)
                if let id = vm.identity {
                    row("Name", id.fullName)
                    row("Sex", id.sex)
                    row("Nationality", "\(id.nationality.name) (\(id.nationality.iso3))")
                    row("Date of birth", id.dob)
                    row("Place of birth", id.place_of_birth)
                    row("Doc number", id.doc_number)
                    row("Issue", id.issue)
                    row("Expiry", id.expiry)
                    Divider()
                    Text("MRZ").font(.subheadline).bold()
                    ForEach(id.mrz, id: \.self) { line in
                        Text(line).font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                    }
                } else {
                    Text("Detect a document to generate a coherent fake person.")
                        .foregroundStyle(.secondary).font(.callout)
                }
                Spacer()
            }
            .padding()
        }
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private func row(_ k: String, _ v: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(k.uppercased()).font(.caption2).foregroundStyle(.secondary)
            Text(v).font(.callout)
        }
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup {
            Button { openImage() } label: { Label("Open", systemImage: "folder") }
            Button { Task { await vm.detect() } } label: { Label("Detect", systemImage: "viewfinder") }
                .disabled(vm.originalImage == nil || vm.busy)
            Button { Task { await vm.redactAll() } } label: { Label("Redact all", systemImage: "wand.and.stars") }
                .disabled(vm.originalImage == nil || vm.busy)
            Button { Task { await vm.rerollIdentity() } } label: { Label("Re-roll", systemImage: "dice") }
                .disabled(vm.busy)
            Button { vm.download() } label: { Label("Download", systemImage: "square.and.arrow.down") }
                .disabled(vm.displayImage == nil)
            if vm.busy { ProgressView().controlSize(.small) }
        }
    }

    // MARK: - File open

    private func openImage() {
        #if canImport(AppKit)
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .image]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url,
           let img = NSImage(contentsOf: url),
           let data = try? Data(contentsOf: url) {
            vm.loadImage(img, data: data)
        }
        #endif
    }

    // MARK: - Geometry: map image-pixel boxes into the fitted on-screen image

    private func fittedImageRect(imagePixel: CGSize, in view: CGSize) -> CGRect {
        guard imagePixel.width > 0, imagePixel.height > 0 else { return .zero }
        let scale = min(view.width / imagePixel.width, view.height / imagePixel.height)
        let w = imagePixel.width * scale
        let h = imagePixel.height * scale
        return CGRect(x: (view.width - w) / 2, y: (view.height - h) / 2, width: w, height: h)
    }

    private func scaledRect(_ pixelRect: CGRect, fit: CGRect, pixel: CGSize) -> CGRect {
        guard pixel.width > 0, pixel.height > 0 else { return .zero }
        let sx = fit.width / pixel.width
        let sy = fit.height / pixel.height
        return CGRect(
            x: fit.minX + pixelRect.minX * sx,
            y: fit.minY + pixelRect.minY * sy,
            width: pixelRect.width * sx,
            height: pixelRect.height * sy)
    }
}

/// Back-compat shim: ContentUnavailableView is macOS 14+. Falls back to a label.
struct ContentUnavailableViewCompat: View {
    let title: String
    let systemImage: String
    let description: String
    init(_ title: String, systemImage: String, description: String) {
        self.title = title; self.systemImage = systemImage; self.description = description
    }
    var body: some View {
        if #available(macOS 14.0, *) {
            ContentUnavailableView(title, systemImage: systemImage, description: Text(description))
        } else {
            VStack(spacing: 8) {
                Image(systemName: systemImage).font(.largeTitle).foregroundStyle(.secondary)
                Text(title).font(.headline)
                Text(description).font(.callout).foregroundStyle(.secondary)
            }
        }
    }
}
