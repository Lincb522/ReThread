import Testing
import Foundation
@testable import CodexConversations
struct NativeDesktopSyncWorkflow {
    @Test @MainActor func syncExistingVerifiedDeletesWithoutRepeatingDeletion() async throws {
        guard let reportPath=ProcessInfo.processInfo.environment["RETHREAD_LIVE_DESKTOP_SYNC_REPORT"] else { return }
        // Explicit opt-in uses the real library's verified deletion journals only.
        // The endpoint cannot invoke an archive or delete RPC.
        let store=ConversationStore();store.start();defer { store.stop() }
        for _ in 0..<300 { if store.isConnected && !store.loading { break };try await Task.sleep(for:.milliseconds(100)) }
        #expect(store.isConnected);#expect(store.error == nil)
        let beforeIDs=Set(store.sessions.map(\.id))
        await store.syncDesktop()
        let result=try #require(store.lastResult)
        #expect(store.error == nil);#expect(!store.busy);#expect(result.action=="desktop_sync")
        #expect(result.desktopSync?.status=="synced");#expect(result.desktopSync?.catalogVerified==true)
        #expect(result.affected>0)
        await store.refresh()
        #expect(Set(store.sessions.map(\.id))==beforeIDs)
        await store.loadOperations()
        #expect(store.operations.contains { $0.action=="delete" && $0.desktopSync?.status=="synced" })
        let report:[String:Any]=["action":result.action,"catalog_verified":result.desktopSync?.catalogVerified == true,
            "affected":result.affected,"library_ids_unchanged":Set(store.sessions.map(\.id))==beforeIDs,
            "operations_show_sync":true,"backend_version":store.status?.version ?? "unknown"]
        try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:reportPath),options:.atomic)
    }
}
