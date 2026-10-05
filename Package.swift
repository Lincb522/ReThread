// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "CodexConversations",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "CodexConversations", targets: ["CodexConversations"])],
    dependencies: [.package(url: "https://github.com/gonzalezreal/swift-markdown-ui.git", exact: "2.4.1")],
    targets: [.executableTarget(name: "CodexConversations", dependencies: [.product(name: "MarkdownUI", package: "swift-markdown-ui")], path: "Sources", resources: [.copy("Resources")]), .testTarget(name: "NativeTests", dependencies: ["CodexConversations"], path: "NativeTests")],
    swiftLanguageModes: [.v5]
)
