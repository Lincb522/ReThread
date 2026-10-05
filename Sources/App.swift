import SwiftUI
import AppKit

@main
struct CodexConversationsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var store = ConversationStore()
    var body: some Scene {
        Window("续言 ReThread", id: "main") {
            RootView().environmentObject(store).ignoresSafeArea().frame(minWidth: 1000, minHeight: 650)
                .background(SnapshotHook(store: store))
        }
        .defaultSize(width: 1200, height: 810)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(replacing: .appInfo) { Button("关于续言 ReThread") { store.showAbout = true } }
            CommandGroup(after: .textEditing) {
                Button("搜索对话") { store.closeInspector(); store.focusSearch.toggle() }.keyboardShortcut("k")
                Button("刷新会话") { Task { await store.refresh() } }.keyboardShortcut("r")
                Button("同步 Codex 删除记录") { Task { await store.syncDesktop() } }.disabled(store.busy || !store.isConnected)
            }
            CommandGroup(replacing: .appSettings) { Button("偏好设置…") { store.showSettings = true }.keyboardShortcut(",") }
        }
    }
}
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static weak var store: ConversationStore?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Self.store?.busy == true else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "操作正在进行"
        alert.informativeText = "请等待预检或备份、执行与验证完成，再退出应用。"
        alert.addButton(withTitle: "继续等待"); alert.runModal()
        sender.windows.first?.makeKeyAndOrderFront(nil)
        return .terminateCancel
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

/// In-process renderer for native visual acceptance. Never captures other apps.
struct SnapshotHook: NSViewRepresentable {
    let store: ConversationStore
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        let mountedAt = Date()
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(100))
            view.window?.titlebarAppearsTransparent = true
            view.window?.titleVisibility = .hidden
            view.window?.isMovableByWindowBackground = false
        }
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--snapshot"), args.indices.contains(i+1) else { return view }
        let path = args[i+1]
        let env = ProcessInfo.processInfo.environment
        let state = env["RETHREAD_SNAPSHOT_STATE"] ?? "library"
        Task { @MainActor in
            for _ in 0..<100 {
                try? await Task.sleep(for: .milliseconds(200))
                if store.isConnected && !store.loading { break }
            }
            let loadedMS = Date().timeIntervalSince(mountedAt) * 1000
            if let query = env["RETHREAD_SNAPSHOT_SEARCH"] { store.query = query }
            if args.contains("--select-first") || ["detail", "diagnosis", "agent", "archive", "delete", "rename", "agent-consent", "external-delete"].contains(state), let item = store.filtered.first { await store.select(item.id) }
            if args.contains("--diagnose") || state == "diagnosis" { store.detailTab = "diagnosis"; await store.diagnose() }
            if state == "agent" || state == "agent-consent" { store.detailTab = "diagnosis"; store.repairMode = "agent" }
            if state == "agent-consent" { store.showAgentConsent = true }
            if state == "old" { store.navigate("old"); store.oldDays = 0 }
            if state == "selection" { store.batchMode = true }
            if state == "batch-all" {
                store.batchMode = true; store.batchIDs = Set(store.sessions.map(\.id))
                await store.prepareBatch("delete")
            }
            if ["batch", "merge"].contains(state) {
                store.batchMode = true
                store.batchIDs = Set(store.sessions.filter { $0.localPathExists == true && !$0.isArchived }.prefix(3).map(\.id))
                if state == "batch" { await store.prepareBatch("delete") } else { store.openMerge() }
            }
            if state == "relocate", let item = store.sessions.first(where: { $0.storageRestricted == true }) { await store.select(item.id); await store.prepare("relocate") }
            if state == "about" { store.showAbout = true }
            if state == "settings" { store.showSettings = true }
            if state == "operations" { await store.loadOperations() }
            if state == "rename" { store.showRename = true }
            if state == "external-delete" { await store.prepare("delete") }
            if ["archive", "delete"].contains(state) { await store.prepare(state) }
            if let window = view.window { window.setContentSize(NSSize(width: 1012, height: 740)) }
            store.appearance = args.contains("--dark") ? "dark" : "light"
            try? await Task.sleep(for: .seconds(2))
            guard let window = view.window, let content = window.contentView,
                  let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds) else {
                print("SNAPSHOT_FAILED: native window unavailable"); return
            }
            let renderStart = Date()
            content.cacheDisplay(in: content.bounds, to: bitmap)
            let renderMS = Date().timeIntervalSince(renderStart) * 1000
            if let data = bitmap.representation(using: .png, properties: [:]) {
                do {
                    try data.write(to: URL(fileURLWithPath: path), options: .atomic)
                    let metrics: [String: Any] = ["loaded_ms": loadedMS, "render_ms": renderMS, "connected": store.isConnected, "loaded_count": store.sessions.count, "projection_builds": store.projectionBuildCount, "error_present": store.error != nil]
                    try JSONSerialization.data(withJSONObject: metrics, options: [.prettyPrinted, .sortedKeys]).write(to: URL(fileURLWithPath: path + ".metrics.json"), options: .atomic)
                    print("SNAPSHOT_OK \(path)")
                }
                catch { print("SNAPSHOT_FAILED \(error)") }
                if args.contains("--snapshot-quit") { store.stop(); NSApplication.shared.terminate(nil) }
            }
        }
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
