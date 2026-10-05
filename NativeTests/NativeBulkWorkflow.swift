import Testing
import Foundation
@testable import CodexConversations

struct NativeBulkWorkflow {
    @Test @MainActor func wholeLibraryAndOfficialDeletion() async throws {
        // Opt-in destructive acceptance uses only a newly seeded, explicitly marked library.
        guard let home = ProcessInfo.processInfo.environment["RETHREAD_BULK_ACCEPTANCE_HOME"] else { return }
        #expect(FileManager.default.fileExists(atPath: home + "/.rethread-acceptance"))
        guard FileManager.default.fileExists(atPath: home + "/.rethread-acceptance") else { return }
        let store = ConversationStore(); store.codexHome = home; store.start()
        defer { store.stop() }
        for _ in 0..<300 {
            if store.isConnected && !store.loading { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        #expect(store.error == nil); #expect(store.sessions.count == 440)
        var originalFiles: [String: Data] = [:]
        for item in store.sessions {
            originalFiles[item.id] = try Data(contentsOf: URL(fileURLWithPath: item.localPath))
        }
        store.batchMode = true; store.toggleSelectAll()
        #expect(store.allFilteredSelected); #expect(store.batchIDs.count == 440)
        await store.prepareBatch("delete")
        let whole = try #require(store.plan)
        #expect(whole.ids.count == 440); #expect(whole.targets.count == 440)
        #expect(whole.blocked?.isEmpty == true); #expect(store.batchJob == nil)
        store.closeModal(); store.toggleSelectAll(); #expect(store.batchIDs.isEmpty)
        store.batchIDs = Set(store.sessions.filter(\.isArchived).map(\.id) + store.sessions.filter { !$0.isArchived }.prefix(72).map(\.id))
        await store.prepareBatch("delete")
        let plan = try #require(store.plan); #expect(plan.ids.count == 75)
        await store.execute(plan, confirmation: plan.confirmation)
        #expect(store.error == nil)
        let result = try #require(store.lastResult)
        #expect(result.ok); #expect(result.affected == 75); #expect(result.outcomes?.count == 75)
        #expect(result.outcomes?.allSatisfy { $0.status == "verified" } == true)
        #expect(store.sessions.count == 365); #expect(store.batchIDs.isEmpty); #expect(!store.busy)
        #expect(store.sessions.allSatisfy { $0.localPathExists == true })
        for item in store.sessions {
            let bytes = try Data(contentsOf: URL(fileURLWithPath: item.localPath))
            #expect(bytes == originalFiles[item.id])
        }
        let manifests = try FileManager.default.contentsOfDirectory(atPath: result.backup)
        #expect(manifests.count == 75)
        if let path = ProcessInfo.processInfo.environment["RETHREAD_BULK_ACCEPTANCE_RESULT"] {
            let report: [String: Any] = ["unselected_histories_unchanged":true,"whole_library_preflight":whole.ids.count,"official_deleted":result.affected,
                "verified_outcomes":result.outcomes?.count ?? 0,"remaining":store.sessions.count,"backups":manifests.count,
                "error_present":store.error != nil,"engine":store.status?.codexVersion ?? "unknown"]
            try JSONSerialization.data(withJSONObject: report,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:path),options:.atomic)
        }
    }
}
