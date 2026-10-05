import Testing
import Foundation
import SwiftUI
import AppKit
@testable import CodexConversations
struct HistoryMaintenanceTests {
    @Test func pathPlanDecodesAndLabels() throws {
        let d=JSONDecoder();d.keyDecodingStrategy = .convertFromSnakeCase
        for action in ["migrate","link","repair"] {
            let raw:[String:Any]=["action":action,"id":"selected","ids":["selected"],"token":"token","confirmation":"confirm","targets":[["id":"selected","title":"Selected"]],"file_bytes":42,"source":"/old/history.jsonl","destination":"/new/history.jsonl","keep_source":true,"external_destination":true]
            let plan=try d.decode(ActionPlan.self,from:JSONSerialization.data(withJSONObject:raw))
            #expect(plan.source=="/old/history.jsonl");#expect(plan.keepSource==true)
            #expect(plan.externalDestination==true);#expect(plan.title==["migrate":"会话迁移","link":"索引链接","repair":"索引修复"][action])
        }
    }
    @Test @MainActor func nativeReadOnlyHistoryPreflightAndPanels() async throws {
        guard let output=ProcessInfo.processInfo.environment["RETHREAD_HISTORY_EVIDENCE"] else { return }
        let folder=URL(fileURLWithPath:output);try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        let store=ConversationStore();store.start();defer { store.stop() }
        for _ in 0..<300 { if store.isConnected && !store.loading { break };try await Task.sleep(for:.milliseconds(100)) }
        #expect(store.error==nil);#expect(store.isConnected)
        // Read and preflight only against the user's real library; never execute a plan here.
        let candidate=try #require(store.sessions.first { $0.localPathExists==true && $0.historyMode=="legacy" && $0.id != ProcessInfo.processInfo.environment["CODEX_THREAD_ID"] })
        let ids=Set(store.sessions.map(\.id));await store.select(candidate.id)
        await store.repairIndex();#expect(store.error==nil);#expect(store.plan==nil)
        #expect(store.notice=="索引引用正常，无需修复")
        let target=folder.appendingPathComponent("chosen-destination");try FileManager.default.createDirectory(at:target,withIntermediateDirectories:true)
        store.openHistoryTool("migrate");#expect(store.hasModal);store.historyTarget=target.path
        try await capture(HistoryPathPanel(),store:store,path:folder.appendingPathComponent("migration-panel.png"))
        await store.prepareHistory();#expect(store.error==nil)
        let plan=try #require(store.plan);#expect(plan.action=="migrate");#expect(plan.source==candidate.localPath)
        #expect(plan.keepSource==true);#expect(plan.ids==[candidate.id])
        try await capture(ConfirmationView(plan:plan),store:store,path:folder.appendingPathComponent("migration-confirmation.png"))
        store.closeModal();#expect(!store.hasModal)
        store.openHistoryTool("link");store.historyTarget=candidate.localPath
        try await capture(HistoryPathPanel(),store:store,path:folder.appendingPathComponent("link-panel.png"))
        store.closeModal();await store.refresh();#expect(Set(store.sessions.map(\.id))==ids)
        await store.loadOperations();#expect(store.error==nil)
        let report:[String:Any]=["backend_version":store.status?.version ?? "unknown","real_library_connected":true,"read_only_preflight":true,"healthy_index_no_repair":true,"single_target_plan":true,"source_destination_displayed":true,"native_panels_rendered":3,"conversations_unchanged":true]
        try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:folder.appendingPathComponent("native-history.json"),options:.atomic)
    }
    @MainActor private func capture<V:View>(_ root:V,store:ConversationStore,path:URL) async throws {
        let host=NSHostingView(rootView:root.environmentObject(store).environment(\.colorScheme,.light).frame(width:500).background(Palette(.light).surface))
        host.frame=NSRect(x:0,y:0,width:500,height:720)
        let window=NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false);window.contentView=host;window.orderFront(nil)
        try await Task.sleep(for:.milliseconds(350));host.layoutSubtreeIfNeeded()
        let bitmap=try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds));host.cacheDisplay(in:host.bounds,to:bitmap)
        try #require(bitmap.representation(using:.png,properties:[:])).write(to:path,options:.atomic);window.orderOut(nil)
    }
}
