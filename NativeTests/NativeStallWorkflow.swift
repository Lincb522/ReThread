import Testing
import Foundation
@testable import CodexConversations
struct NativeStallWorkflow {
    @Test @MainActor func sevenWithQueueAndLargeBackup() async throws {
        guard let home = ProcessInfo.processInfo.environment["RETHREAD_STALL_ACCEPTANCE_HOME"] else { return }
        #expect(FileManager.default.fileExists(atPath: home + "/.rethread-acceptance"))
        guard FileManager.default.fileExists(atPath: home + "/.rethread-acceptance") else { return }
        let store = ConversationStore(); store.codexHome = home; store.start()
        defer { store.stop() }
        for _ in 0..<300 {
            if store.isConnected && !store.loading { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        #expect(store.error == nil); #expect(store.sessions.count == 12)
        let selected = store.sessions.filter { !$0.isArchived && $0.localPathExists == true }
        #expect(selected.count == 7)
        var originals: [String: Data] = [:]
        for item in store.sessions where item.localPathExists == true && !selected.contains(where: { $0.id == item.id }) {
            originals[item.localPath] = try Data(contentsOf: URL(fileURLWithPath: item.localPath))
        }
        store.batchMode = true; store.batchIDs = Set(selected.map(\.id)); await store.prepareBatch("delete")
        let plan = try #require(store.plan); #expect(plan.ids.count == 7)
        var phases = Set<String>(); var sawCurrent = false
        let observer = Task { @MainActor in
            while !Task.isCancelled {
                if let job = store.batchJob {
                    phases.insert(job.phase); sawCurrent = sawCurrent || job.currentIndex != nil
                }
                do { try await Task.sleep(for: .milliseconds(30)) } catch { break }
            }
        }
        await store.execute(plan, confirmation: plan.confirmation); observer.cancel()
        #expect(store.error == nil); let result = try #require(store.lastResult)
        #expect(result.ok); #expect(result.affected == 7)
        #expect(result.outcomes?.allSatisfy { $0.status == "verified" } == true)
        #expect(store.sessions.count == 5); #expect(!store.busy); #expect(store.batchJob == nil)
        #expect(sawCurrent)
        for (path,bytes) in originals { #expect(try Data(contentsOf: URL(fileURLWithPath: path)) == bytes) }
        var retained = 0
        for outcome in result.outcomes ?? [] {
            let data = try Data(contentsOf: URL(fileURLWithPath: outcome.detail + "/manifest.json"))
            let json = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            #expect(json["status"] as? String == "verified")
            retained += (json["retained_bookkeeping"] as? [Any])?.count ?? 0
        }
        #expect(retained == 7)
        if let path = ProcessInfo.processInfo.environment["RETHREAD_STALL_ACCEPTANCE_RESULT"] {
            let report: [String: Any] = ["official_deleted":7,"remaining":5,"retained_queue_revisions":retained,"current_item_visible":sawCurrent,"observed_phases":Array(phases).sorted(),"unselected_histories_unchanged":true,"engine":store.status?.codexVersion ?? "unknown"]
            try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:path),options:.atomic)
        }
    }
}
