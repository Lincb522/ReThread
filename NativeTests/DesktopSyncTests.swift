import Testing
import Foundation
import AppKit
import SwiftUI
@testable import CodexConversations
struct DesktopSyncTests {
    private func decode(_ json: String) throws -> ActionResult {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(ActionResult.self, from: Data(json.utf8))
    }
    @Test func legacyResultStillDecodes() throws {
        let result = try decode(#"{"ok":true,"action":"delete","affected":1,"backup":"/backup","verification":"已验证"}"#)
        #expect(result.desktopSync == nil); #expect(result.ok)
    }
    @Test func deletionSuccessIndependentOfPendingSync() throws {
        let result = try decode(#"{"ok":true,"action":"delete","affected":2,"backup":"/backup","verification":"本地删除验证通过","desktop_sync":{"status":"pending","affected":2,"message":"对话已删除；Codex 未运行，可稍后仅重试同步","catalog_verified":false},"outcomes":[{"id":"1","title":"测试","status":"verified","detail":"/backup","desktop_sync":{"status":"pending","affected":2,"message":"待同步"}}]}"#)
        #expect(result.ok); #expect(result.desktopSync?.needsRetry == true)
        #expect(result.outcomes?.first?.statusText == "已完成")
        #expect(result.outcomes?.first?.desktopSync?.needsRetry == true)
    }
    @Test func verifiedSyncAndProgress() throws {
        let result = try decode(#"{"ok":true,"action":"desktop_sync","affected":2,"backup":"/backup","verification":"本次仅同步","desktop_sync":{"status":"synced","affected":2,"message":"Codex 列表已同步","catalog_verified":true}}"#)
        #expect(result.desktopSync?.needsRetry == false); #expect(result.desktopSync?.catalogVerified == true)
        let data = Data(#"{"id":"job","mode":"execute","state":"running","phase":"desktop_sync","completed":1,"total":7}"#.utf8)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        #expect(try decoder.decode(BatchJob.self, from:data).title == "正在同步 Codex 列表")
    }
    @Test @MainActor func renderPendingSyncPanel() async throws {
        guard let path = ProcessInfo.processInfo.environment["RETHREAD_SYNC_SNAPSHOT"] else { return }
        let result = try decode(#"{"ok":true,"action":"delete","affected":7,"backup":"~/Library/Application Support/Codex Conversations/backups","verification":"本地删除验证通过。","desktop_sync":{"status":"pending","affected":7,"message":"对话已删除；Codex 未运行或本地同步接口未就绪，可稍后仅重试同步","catalog_verified":false}}"#)
        let store = ConversationStore()
        let root = SuccessPanel(result:result).environmentObject(store).environment(\.colorScheme,.light)
            .frame(width:500).background(Color.white).clipShape(RoundedRectangle(cornerRadius:13))
        let host = NSHostingView(rootView:root);var size=host.fittingSize;host.frame=NSRect(origin:.zero,size:size)
        let window=NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false)
        window.contentView=host;window.orderFront(nil);host.layoutSubtreeIfNeeded()
        try await Task.sleep(for:.milliseconds(250))
        size=host.fittingSize;host.frame=NSRect(origin:.zero,size:size);window.setContentSize(size);host.layoutSubtreeIfNeeded()
        let bitmap=try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds));host.cacheDisplay(in:host.bounds,to:bitmap)
        try #require(bitmap.representation(using:.png,properties:[:])).write(to:URL(fileURLWithPath:path),options:.atomic)
        window.orderOut(nil);#expect(size.height <= 620)
    }
}
