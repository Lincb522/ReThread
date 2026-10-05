import Testing
import Foundation
import SwiftUI
import AppKit
@testable import CodexConversations

struct MergeProjectTests {
    @Test func projectAndMergeConfirmationNames() throws {
        let decoder=JSONDecoder();decoder.keyDecodingStrategy = .convertFromSnakeCase
        for action in ["merge", "project_assign", "project_create"] {
            let raw:[String:Any] = ["action":action,"id":"id","ids":["id"],"token":"token","confirmation":"confirm","targets":[["id":"id","title":"对话"]],"file_bytes":0,"batch":true,"requires_exit":action != "merge"]
            let plan=try decoder.decode(ActionPlan.self,from:JSONSerialization.data(withJSONObject:raw))
            #expect(plan.title == Action.title(action));#expect(plan.canExecute)
        }
    }
    @Test @MainActor func realLibraryProjectPanelsAndUnlimitedSelection() async throws {
        guard let output=ProcessInfo.processInfo.environment["RETHREAD_BUILD16_EVIDENCE"] else { return }
        let folder=URL(fileURLWithPath:output);try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
        let suite="rethread.build16.visual."+UUID().uuidString
        let preferences=try #require(UserDefaults(suiteName:suite));defer { preferences.removePersistentDomain(forName:suite) }
        let store=ConversationStore(preferences:preferences);store.start();defer { store.stop() }
        for _ in 0..<300 { if store.isConnected && !store.loading { break };try await Task.sleep(for:.milliseconds(100)) }
        #expect(store.isConnected);#expect(store.error==nil)
        let initial=Set(store.sessions.map(\.id))
        let item=try #require(store.sessions.first { $0.id != ProcessInfo.processInfo.environment["CODEX_THREAD_ID"] && !$0.isArchived })
        store.openProjectAssignment([item.id]);#expect(store.hasModal)
        try await capture(ProjectAssignmentPanel(),store:store,path:folder.appendingPathComponent("move-project-light.png"),dark:false,height:680)
        store.createProjectFromConversation=true
        try await capture(ProjectAssignmentPanel(),store:store,path:folder.appendingPathComponent("create-project-dark.png"),dark:true,height:560)
        let project=try #require(store.projectCatalog.first { $0.id != item.projectKey })
        await store.prepareProjectAssignment(projectID:project.id,name:"",root:"")
        #expect(store.error==nil)
        let plan=try #require(store.plan);#expect(plan.action=="project_assign");#expect(plan.requiresExit==true)
        try await capture(ConfirmationView(plan:plan),store:store,path:folder.appendingPathComponent("move-confirm-light.png"),dark:false,height:650)
        store.closeModal();store.batchIDs=Set(store.sessions.prefix(12).map(\.id));store.openMerge()
        #expect(store.showMerge);#expect(store.mergeIDs.count==12);#expect(store.error==nil)
        try await capture(MergePanel(),store:store,path:folder.appendingPathComponent("merge-unlimited-light.png"),dark:false,height:625)
        store.closeModal();#expect(!store.hasModal);await store.refresh();#expect(Set(store.sessions.map(\.id))==initial)
        let report:[String:Any] = ["native_panels_rendered":4,"merge_12_selection_accepted":true,"existing_project_preflight":true,"project_requires_exit_explicit":true,"user_conversations_unchanged":true,"destructive_execute_called":false,"backend_version":store.status?.version ?? ""]
        try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:folder.appendingPathComponent("native-build16.json"),options:.atomic)
    }
    @MainActor private func capture<V:View>(_ view:V,store:ConversationStore,path:URL,dark:Bool,height:CGFloat) async throws {
        let host=NSHostingView(rootView:view.frame(width:500).environmentObject(store).environment(\.colorScheme,dark ? .dark : .light).background(Palette(dark ? .dark : .light).surface))
        host.frame=NSRect(x:0,y:0,width:500,height:height)
        let window=NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false);window.contentView=host;window.orderFront(nil)
        try await Task.sleep(for:.milliseconds(350));host.layoutSubtreeIfNeeded()
        let bitmap=try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds));host.cacheDisplay(in:host.bounds,to:bitmap)
        try #require(bitmap.representation(using:.png,properties:[:])).write(to:path,options:.atomic);window.orderOut(nil)
    }
}
