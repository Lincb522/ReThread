import Testing
import Foundation
@testable import CodexConversations
struct NativeDependencyWorkflow {
    @Test @MainActor func selectedForkThenSource() async throws {
        guard let home=ProcessInfo.processInfo.environment["RETHREAD_FORK_HOME"] else {return}
        try await run(home:home, external:false)
    }
    @Test @MainActor func actualExternalStorageScan() async throws {
        guard let home=ProcessInfo.processInfo.environment["RETHREAD_EXTERNAL_HOME"] else {return}
        try await run(home:home, external:true)
    }
    @MainActor private func run(home:String, external:Bool) async throws {
        #expect(FileManager.default.fileExists(atPath:home+"/.rethread-acceptance"))
        let scope=try #require(JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:home+"/acceptance-scope.json"))) as? [String:Any])
        let parent=try #require(scope["parent"] as? String)
        let ids=Set(try #require(scope["ids"] as? [String]))
        let store=ConversationStore();store.codexHome=home;store.start();defer{store.stop()}
        for _ in 0..<300 {if store.isConnected && !store.loading {break};try await Task.sleep(for:.milliseconds(100))}
        #expect(store.error == nil);#expect(store.sessions.count==12)
        var untouched:[String:Data]=[:]
        for item in store.sessions where !ids.contains(item.id) {untouched[item.localPath]=try Data(contentsOf:URL(fileURLWithPath:item.localPath))}
        store.batchIDs=external ? [parent] : ids;store.batchMode=true
        let started=Date();await store.prepareBatch("delete")
        let plan=try #require(store.plan);#expect(Set(plan.ids)==ids);#expect(plan.blocked?.isEmpty == true)
        if external {#expect(plan.externalDelete == true);#expect(plan.missingHistories?.count==4)}
        else {#expect(plan.dependencyOrdered == true);#expect(plan.targets.first?.id==scope["child"] as? String)}
        let preflight=Date().timeIntervalSince(started);await store.execute(plan,confirmation:plan.confirmation)
        #expect(store.error == nil);let result=try #require(store.lastResult);#expect(result.ok);#expect(result.affected==ids.count)
        #expect(store.sessions.count==12-ids.count)
        for (path,data) in untouched {#expect(try Data(contentsOf:URL(fileURLWithPath:path))==data)}
        var backups:[String]=[]
        for outcome in result.outcomes ?? [] {
            #expect(outcome.status=="verified");backups.append(outcome.detail)
            #expect(FileManager.default.fileExists(atPath:outcome.detail+"/official-events.json"))
        }
        if external,let aliases=scope["aliases"] as? [String:String] {
            for (alias,destination) in aliases {#expect(try FileManager.default.destinationOfSymbolicLink(atPath:alias)==destination)}
        }
        if let path=ProcessInfo.processInfo.environment["RETHREAD_DEPENDENCY_RESULT"] {
            let report:[String:Any]=["case":external ? "external" : "fork","official_deleted":result.affected,"unselected_unchanged":true,"remaining":store.sessions.count,"preflight_seconds":preflight,"total_seconds":Date().timeIntervalSince(started),"backups":backups,"engine":store.status?.codexVersion ?? "unknown"]
            try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:path),options:.atomic)
        }
    }
}
