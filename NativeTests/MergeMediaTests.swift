import Testing
import Foundation
import SwiftUI
import AppKit
@testable import CodexConversations

struct MergeMediaTests {
    @Test @MainActor func mediaCountsAndConfirmation() async throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let raw: [String: Any] = ["action":"merge", "id":"one", "ids":["one","two"], "token":"test", "confirmation":"确认", "targets":[["id":"one","title":"界面设计讨论"],["id":"two","title":"图片与语音记录"]], "file_bytes":74989010, "batch":true, "message_count":10035, "image_count":3, "audio_count":2, "excluded_count":8, "new_title":"项目完整记录"]
        let plan = try decoder.decode(ActionPlan.self, from:JSONSerialization.data(withJSONObject:raw))
        #expect(plan.imageCount == 3); #expect(plan.audioCount == 2); #expect(plan.messageCount == 10035)
        let old = raw.filter { !["image_count","audio_count"].contains($0.key) }
        let legacy = try decoder.decode(ActionPlan.self, from:JSONSerialization.data(withJSONObject:old))
        #expect(legacy.imageCount == nil); #expect(legacy.audioCount == nil)
        guard let path = ProcessInfo.processInfo.environment["RETHREAD_BUILD17_EVIDENCE"] else { return }
        let folder = URL(fileURLWithPath:path); try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        let suite = "rethread.build17.visual."+UUID().uuidString
        let preferences = try #require(UserDefaults(suiteName:suite)); defer { preferences.removePersistentDomain(forName:suite) }
        let store = ConversationStore(preferences:preferences)
        for dark in [false,true] {
            let host = NSHostingView(rootView:ConfirmationView(plan:plan).frame(width:500).environmentObject(store).environment(\.colorScheme,dark ? .dark : .light).background(Palette(dark ? .dark : .light).surface))
            host.frame = NSRect(x:0,y:0,width:500,height:630)
            let window = NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false); window.contentView=host; window.orderFront(nil)
            try await Task.sleep(for:.milliseconds(350)); host.layoutSubtreeIfNeeded()
            let bitmap = try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds));host.cacheDisplay(in:host.bounds,to:bitmap)
            try #require(bitmap.representation(using:.png,properties:[:])).write(to:folder.appendingPathComponent(dark ? "merge-media-dark.png" : "merge-media-light.png"),options:.atomic);window.orderOut(nil)
        }
    }
}
