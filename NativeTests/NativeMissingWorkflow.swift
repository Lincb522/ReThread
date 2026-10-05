import Testing
import Foundation
@testable import CodexConversations
struct NativeMissingWorkflow {
    @Test @MainActor func externalParentWithMissingChildren() async throws {
        guard let home = ProcessInfo.processInfo.environment["RETHREAD_MISSING_ACCEPTANCE_HOME"] else { return }
        guard FileManager.default.fileExists(atPath: home + "/.rethread-acceptance") else { Issue.record("Acceptance marker missing"); return }
        let scope = try #require(JSONSerialization.jsonObject(with: Data(contentsOf:URL(fileURLWithPath:home+"/acceptance-scope.json"))) as? [String:Any])
        let parent = try #require(scope["parent"] as? String)
        let ids = Set(try #require(scope["ids"] as? [String]))
        let store = ConversationStore();store.codexHome=home;store.start();defer {store.stop()}
        for _ in 0..<300 { if store.isConnected && !store.loading {break};try await Task.sleep(for:.milliseconds(100)) }
        #expect(store.error == nil);#expect(store.sessions.count == 12)
        var untouched:[String:Data]=[:]
        for item in store.sessions where !ids.contains(item.id) {untouched[item.localPath]=try Data(contentsOf:URL(fileURLWithPath:item.localPath))}
        store.batchIDs=[parent];store.batchMode=true;await store.prepareBatch("delete")
        let plan=try #require(store.plan);#expect(Set(plan.ids)==ids);#expect(plan.missingHistories?.count==4)
        #expect(plan.blocked?.isEmpty == true);#expect(plan.externalDelete == true)
        await store.execute(plan,confirmation:plan.confirmation)
        #expect(store.error == nil);let result=try #require(store.lastResult)
        #expect(result.ok);#expect(result.affected==6);#expect(store.sessions.count==6)
        let outcome=try #require(result.outcomes?.first)
        let manifest=try #require(JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:outcome.detail+"/manifest.json"))) as? [String:Any])
        #expect((manifest["missing_histories"] as? [String])?.count==4)
        #expect((manifest["files"] as? [Any])?.count==2);#expect((manifest["rows"] as? [Any])?.count==6)
        #expect(manifest["status"] as? String == "verified")
        for (path,data) in untouched {#expect(try Data(contentsOf:URL(fileURLWithPath:path))==data)}
        for n in 0..<2 {#expect(try FileManager.default.destinationOfSymbolicLink(atPath:home+"/sessions/offloaded-day\(n)") == (scope["external"] as! String)+"/day\(n)")}
        if let path=ProcessInfo.processInfo.environment["RETHREAD_MISSING_ACCEPTANCE_RESULT"] {
            let report:[String:Any] = ["official_deleted":result.affected,"backup_files":2,"backup_index_rows":6,"missing_histories_recorded":4,"remaining_unselected":store.sessions.count,"other_files_unchanged":true,"symlinks_preserved":true,"engine":store.status?.codexVersion ?? "unknown"]
            try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:path),options:.atomic)
        }
    }
}
