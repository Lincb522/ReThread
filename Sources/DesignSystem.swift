import SwiftUI
import AppKit

struct Palette {
    let dark: Bool
    init(_ scheme: ColorScheme) { dark = scheme == .dark }
    var surface: Color { Color(hex: dark ? 0x1b1c20 : 0xfcfcfd) }
    var sidebar: Color { Color(hex: dark ? 0x18191d : 0xf2f2f4) }
    var soft: Color { Color(hex: dark ? 0x222328 : 0xf4f4f6) }
    var hover: Color { Color(hex: dark ? 0x292a31 : 0xededf0) }
    var line: Color { Color(hex: dark ? 0x2b2c32 : 0xe8e8ec) }
    var border: Color { Color(hex: dark ? 0x393b44 : 0xd9dade) }
    var text: Color { Color(hex: dark ? 0xe7e8ed : 0x232429) }
    var secondary: Color { Color(hex: dark ? 0xaeb1bc : 0x686b75) }
    var muted: Color { Color(hex: dark ? 0x90939f : 0x858894) }
    var selected: Color { Color(hex: dark ? 0x282a32 : 0xeeeef3) }
    var accent: Color { Color(hex: dark ? 0xb1bdd7 : 0x737f99) }
    var amber: Color { Color(hex: dark ? 0xc6a66e : 0xa47b43) }
    var amberSoft: Color { Color(hex: dark ? 0x302b23 : 0xf8f2e9) }
    var red: Color { Color(hex: dark ? 0xd18b8b : 0xb75557) }
    var redSoft: Color { Color(hex: dark ? 0x382a2d : 0xf7eced) }
    var button: Color { Color(hex: dark ? 0xe4e6ee : 0x282a30) }
    var buttonText: Color { Color(hex: dark ? 0x292b34 : 0xffffff) }
}
extension Color {
    init(hex: UInt32) { self.init(.sRGB, red: Double((hex >> 16) & 255)/255, green: Double((hex >> 8) & 255)/255, blue: Double(hex & 255)/255, opacity: 1) }
}
struct Icon: View {
    let name: String
    var size: CGFloat = 16
    private static let images: [String: NSImage] = {
        let bundle: Bundle
        if Bundle.main.bundleURL.pathExtension == "app" {
            let url = Bundle.main.resourceURL!.appendingPathComponent("CodexConversations_CodexConversations.bundle")
            guard let packaged = Bundle(url: url) else { preconditionFailure("Missing packaged icon bundle: \(url.path)") }
            bundle = packaged
        } else { bundle = .module }
        let root = bundle.resourceURL!.appendingPathComponent("Icons")
        guard let urls = try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil), !urls.isEmpty else { preconditionFailure("Missing Lucide assets: \(root.path)") }
        return Dictionary(uniqueKeysWithValues: urls.compactMap { url in
            guard let image = NSImage(contentsOf: url) else { return nil }
            image.isTemplate = true
            return (url.deletingPathExtension().lastPathComponent, image)
        })
    }()
    var body: some View {
        if let image = Self.images[name] { Image(nsImage: image).resizable().aspectRatio(contentMode: .fit).frame(width: size, height: size).accessibilityHidden(true) }
    }
}
struct Line: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View { Rectangle().fill(Palette(scheme).line).frame(height: 1) }
}
struct AppLogo: View {
    var size: CGFloat = 35
    var body: some View {
        Icon(name: "messages-square", size: size * 0.57).foregroundStyle(Color(hex: 0xe2e7f1))
            .frame(width: size, height: size)
            .background(LinearGradient(colors: [Color(hex: 0x424752), Color(hex: 0x292c34)], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: size * 0.27))
            .overlay(RoundedRectangle(cornerRadius: size * 0.27).strokeBorder(Color(hex: 0x575e6e), lineWidth: 0.8))
            .shadow(color: .black.opacity(0.14), radius: 4, y: 2)
    }
}
struct ToolButton: View {
    @Environment(\.colorScheme) private var scheme
    let icon: String
    let title: String
    let action: () -> Void
    var body: some View {
        Button(action: action) { Icon(name: icon, size: 15).frame(width: 28, height: 28).contentShape(Rectangle()) }
            .buttonStyle(HoverButton()).foregroundStyle(Palette(scheme).secondary).help(title).accessibilityLabel(title)
    }
}
struct HoverButton: ButtonStyle {
    @Environment(\.colorScheme) private var scheme
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.55 : 1)
            .background(configuration.isPressed ? Palette(scheme).hover : .clear, in: RoundedRectangle(cornerRadius: 5))
    }
}
struct ActionButton: View {
    @Environment(\.colorScheme) private var scheme
    let title: String
    var icon: String? = nil
    var primary = false
    var destructive = false
    var height: CGFloat = 33
    let action: () -> Void
    var body: some View {
        let p = Palette(scheme)
        Button(action: action) {
            HStack(spacing: 7) { if let icon { Icon(name: icon, size: 13) }; Text(title).font(.system(size: 11, weight: .medium)) }
                .padding(.horizontal, 12).frame(height: height)
                .foregroundStyle(primary ? (destructive ? .white : p.buttonText) : p.secondary)
                .background(primary ? (destructive ? p.red : p.button) : p.surface, in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(primary ? .clear : p.border, lineWidth: 1))
        }.buttonStyle(HoverButton()).disabledAppearance()
    }
}
private struct DisabledAppearance: ViewModifier {
    @Environment(\.isEnabled) private var enabled
    func body(content: Content) -> some View { content.opacity(enabled ? 1 : 0.4) }
}
extension View {
    func disabledAppearance() -> some View { modifier(DisabledAppearance()) }
}
struct CapsuleLabel: View {
    @Environment(\.colorScheme) private var scheme
    let text: String
    var body: some View { Text(text).font(.system(size: 9)).foregroundStyle(Palette(scheme).muted).padding(.horizontal, 7).padding(.vertical, 3).background(Palette(scheme).soft, in: RoundedRectangle(cornerRadius: 4)).overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Palette(scheme).line)) }
}
struct EmptyPanel: View {
    @Environment(\.colorScheme) private var scheme
    let icon: String
    let title: String
    let description: String
    var body: some View {
        VStack(spacing: 13) {
            Icon(name: icon, size: 30).foregroundStyle(Palette(scheme).muted)
            Text(title).font(.system(size: 14, weight: .medium)).foregroundStyle(Palette(scheme).text)
            Text(description).font(.system(size: 11)).lineSpacing(5).foregroundStyle(Palette(scheme).muted).multilineTextAlignment(.center)
        }.frame(maxWidth: .infinity, maxHeight: .infinity).padding(30)
    }
}
struct WindowDragRegion: NSViewRepresentable {
    final class DragView: NSView {
        override var mouseDownCanMoveWindow: Bool { true }
        override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
    }
    func makeNSView(context: Context) -> NSView { DragView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
@discardableResult func openCodex(_ id: String) -> Bool { guard let url = URL(string: "codex://threads/\(id)") else { return false }; return NSWorkspace.shared.open(url) }
func copyText(_ text: String) { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) }
