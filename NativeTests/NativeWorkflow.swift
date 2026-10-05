import Testing
import Foundation
@testable import CodexConversations

struct NativeWorkflow {
    @Test @MainActor func realStoreLifecycle() async throws {
        let env = ProcessInfo.processInfo.environment
        let home = try #require(env["RETHREAD_ACCEPTANCE_HOME"])
        #expect(FileManager.default.fileExists(atPath: home + "/.rethread-acceptance"))
        guard FileManager.default.fileExists(atPath: home + "/.rethread-acceptance") else { return }
        let store = ConversationStore(); store.codexHome = home; store.start()
        defer { store.stop() }
        for _ in 0..<200 {
            if store.isConnected && !store.loading { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        #expect(store.error == nil); #expect(store.isConnected); #expect(store.sessions.count == 12)
        store.query = "桌面端交互"; #expect(store.filtered.count == 1)
        let item = try #require(store.filtered.first)
        await store.select(item.id)
        #expect(store.detail?.messages?.count == 2)
        let pinned = store.pins.contains(item.id)
        store.togglePin(item.id); #expect(store.pins.contains(item.id) != pinned)
        store.togglePin(item.id); #expect(store.pins.contains(item.id) == pinned)
        await store.diagnose(); #expect(store.diagnosis?.scan.complete == true)
        #expect(store.diagnosis?.changes.isEmpty == true)
        store.query = ""; store.navigate("archived"); #expect(store.filtered.count == 3)
        store.navigate("all"); await store.select(item.id)
        for action in ["archive", "unarchive"] {
            await store.prepare(action)
            let p = try #require(store.plan)
            await store.execute(p, confirmation: p.confirmation)
            #expect(store.error == nil)
            #expect(store.lastResult?.ok == true)
            #expect(store.detail?.isArchived == (action == "archive"))
            store.closeModal()
        }
        await store.prepare("rename", title: "Swift 原生完整流程验收")
        let rename = try #require(store.plan); await store.execute(rename, confirmation: rename.confirmation)
        #expect(store.error == nil); #expect(store.detail?.title == "Swift 原生完整流程验收"); store.closeModal()
        store.batchIDs = Set(store.sessions.filter { !$0.isArchived && $0.id != item.id && $0.localPathExists == true }.prefix(2).map(\.id))
        await store.prepareBatch("archive"); #expect(store.plan != nil); #expect(store.plan?.batch == true); #expect(store.plan?.ids.count == 2); store.closeModal()
        store.navigate("old"); store.oldDays = 0; #expect(store.filtered.count == 12)
        store.oldDays = 365; #expect(store.filtered.isEmpty); store.navigate("all")
        store.batchIDs = Set(store.sessions.filter { !$0.isArchived && $0.id != item.id && $0.localPathExists == true }.prefix(2).map(\.id))
        let sources = store.batchIDs
        store.openMerge(); #expect(store.showMerge); #expect(store.mergeIDs.count == 2)
        await store.prepareMerge(title: "原生合并验收")
        let merge = try #require(store.plan); #expect(merge.messageCount == 4)
        await store.execute(merge, confirmation: merge.confirmation)
        #expect(store.error == nil); #expect(store.lastResult?.newId != nil)
        #expect(store.sessions.count == 13); #expect(store.detail?.messages?.count == 4)
        store.closeModal(); store.batchIDs = sources
        await store.prepareBatch("delete")
        let batch = try #require(store.plan); #expect(batch.batch == true)
        await store.execute(batch, confirmation: batch.confirmation)
        #expect(store.error == nil); #expect(store.lastResult?.outcomes?.count == 2)
        #expect(store.lastResult?.affected == 2); #expect(store.sessions.count == 11)
        store.closeModal()
        await store.select(item.id); await store.prepare("delete")
        let delete = try #require(store.plan); await store.execute(delete, confirmation: delete.confirmation)
        #expect(store.error == nil); #expect(store.sessions.count == 10); #expect(store.selectedID == nil)
        store.closeModal(); await store.loadOperations(); #expect(store.operations.count == 7)
        let broken = try #require(store.sessions.first { $0.localPathExists == false })
        await store.select(broken.id); await store.diagnose()
        #expect(store.diagnosis?.changes["rollout_path"] != nil)
    }
}
