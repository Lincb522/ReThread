import Testing
import Foundation
import SwiftUI
import AppKit
@testable import CodexConversations
struct ProjectDeletionTests {
    @Test func emptyAndBlockedProjectConfirmationRules() throws {
        let d=JSONDecoder();d.keyDecodingStrategy = .convertFromSnakeCase
        var raw:[String:Any]=["action":"project_delete","id":"project","ids":[],"token":"token","confirmation":"confirm","targets":[],"file_bytes":0,"batch":true,"requires_exit":true,"project":["id":"project","name":"项目名称","roots":["/workspace"],"order":0,"source":"codex_sqlite"],"archived_count":0,"blocked":[]]
        let empty=try d.decode(ActionPlan.self,from:JSONSerialization.data(withJSONObject:raw))
        #expect(empty.title=="删除整个项目");#expect(empty.canExecute);#expect(empty.project?.name=="项目名称");#expect(empty.requiresExit==true)
        raw["blocked"]=[["id":"sid","title":"受限会话","reason":"包含当前任务"]]
        let blocked=try d.decode(ActionPlan.self,from:JSONSerialization.data(withJSONObject:raw));#expect(!blocked.canExecute)
        raw["action"]="delete";raw["blocked"]=[]
        let batch=try d.decode(ActionPlan.self,from:JSONSerialization.data(withJSONObject:raw));#expect(!batch.canExecute)
    }
    @Test @MainActor func realLibraryPreflightAndNativeProjectControls() async throws {
        guard let output=ProcessInfo.processInfo.environment["RETHREAD_PROJECT_DELETE_EVIDENCE"] else { return }
        let folder=URL(fileURLWithPath:output);try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        let suite="rethread.project-delete.visual."+UUID().uuidString
        let preferences=try #require(UserDefaults(suiteName:suite));defer { preferences.removePersistentDomain(forName:suite) }
        let store=ConversationStore(preferences:preferences);store.start();defer { store.stop() }
        for _ in 0..<300 { if store.isConnected && !store.loading { break };try await Task.sleep(for:.milliseconds(100)) }
        #expect(store.isConnected);#expect(store.error==nil)
        let ids=Set(store.sessions.map(\.id));let api:SessionList=try await store.request("/api/sessions?limit=0")
        let projects=try #require(api.projects)
        let project=try #require(projects.filter { p in store.sessions.contains { $0.projectKey==p.id } }.sorted { a,b in store.sessions.filter { $0.projectKey==a.id }.count < store.sessions.filter { $0.projectKey==b.id }.count }.first)
        store.navigate("all",project:project.id)
        try await capture(RootView().frame(width:1012,height:740),store:store,path:folder.appendingPathComponent("project-controls-light.png"),width:1012,height:740,dark:false)
        // Call the same native control's read-only preflight, never its execute action.
        store.query="does-not-match-any-chat"
        await store.prepareProjectDelete(project.id);#expect(store.error==nil)
        let plan=try #require(store.plan);#expect(plan.action=="project_delete");#expect(plan.project?.id==project.id)
        #expect(Set(store.sessions.filter { $0.projectKey==project.id }.map(\.id)).isSubset(of:Set(plan.ids + (plan.blocked ?? []).map(\.id))))
        #expect(plan.requiresExit==true)
        for dark in [false,true] {
            try await capture(ConfirmationView(plan:plan).frame(width:500).background(Palette(dark ? .dark : .light).surface),store:store,path:folder.appendingPathComponent(dark ? "project-confirm-dark.png" : "project-confirm-light.png"),width:500,height:650,dark:dark)
        }
        store.closeModal();store.query="";await store.refresh();#expect(Set(store.sessions.map(\.id))==ids)
        #expect(store.projectCatalog.count==projects.count)
        let report:[String:Any]=["backend_version":store.status?.version ?? "unknown","real_library_connected":true,"read_only_project_preflight":true,"filters_do_not_reduce_scope":true,"project_count_unchanged":true,"conversation_ids_unchanged":true,"native_panels_rendered":3,"destructive_execute_called":false]
        try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:folder.appendingPathComponent("native-project-delete.json"),options:.atomic)
    }
    @MainActor private func capture<V:View>(_ root:V,store:ConversationStore,path:URL,width:CGFloat,height:CGFloat,dark:Bool) async throws {
        let host=NSHostingView(rootView:root.environmentObject(store).environment(\.colorScheme,dark ? .dark : .light))
        host.frame=NSRect(x:0,y:0,width:width,height:height)
        let window=NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false);window.contentView=host;window.orderFront(nil)
        try await Task.sleep(for:.milliseconds(350));host.layoutSubtreeIfNeeded()
        let bitmap=try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds));host.cacheDisplay(in:host.bounds,to:bitmap)
        try #require(bitmap.representation(using:.png,properties:[:])).write(to:path,options:.atomic);window.orderOut(nil)
    }
}
