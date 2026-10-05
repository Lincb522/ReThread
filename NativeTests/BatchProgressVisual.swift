import Testing
import AppKit
import SwiftUI
@testable import CodexConversations
struct BatchProgressVisual {
    @Test @MainActor func renderProgressPanel() throws {
        guard let destination = ProcessInfo.processInfo.environment["RETHREAD_PROGRESS_SNAPSHOT"] else { return }
        // Visual-state coverage only; official mutation acceptance is NativeStallWorkflow.
        let now = Date().timeIntervalSince1970
        let values: [String: Any] = ["id":"visual-progress","mode":"execute","state":"running","phase":"official_rpc","completed":0,"total":7,
            "current_title":"重构 SwiftUI 导航与状态管理","current_index":1,"scope_count":4,"message":"等待 Codex 核对历史引用并执行删除",
            "started_at":now-78,"phase_started_at":now-42,"stop_requested":false]
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let job = try decoder.decode(BatchJob.self, from: JSONSerialization.data(withJSONObject: values))
        let store = ConversationStore(); store.batchJob = job; store.busy = true
        let root = BatchProgressPanel(job:job).environmentObject(store).environment(\.colorScheme,.light)
            .frame(width:500).background(Color.white).clipShape(RoundedRectangle(cornerRadius:13))
        let host = NSHostingView(rootView: root)
        let size = host.fittingSize; host.frame = NSRect(origin:.zero,size:size)
        let window = NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false)
        window.contentView=host;window.orderFront(nil);host.layoutSubtreeIfNeeded()
        let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds))
        host.cacheDisplay(in:host.bounds,to:bitmap)
        let data = try #require(bitmap.representation(using:.png,properties:[:]))
        try data.write(to:URL(fileURLWithPath:destination),options:.atomic)
        window.orderOut(nil)
        #expect(size.height <= 550)
    }
}
