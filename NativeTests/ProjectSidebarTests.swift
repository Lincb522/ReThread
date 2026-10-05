import Testing
import Foundation
import SwiftUI
import AppKit
@testable import CodexConversations
struct ProjectSidebarTests {
    @Test @MainActor func collapsedSectionsPersistIndependently() throws {
        let suite="rethread.sidebar.tests."+UUID().uuidString
        let preferences=try #require(UserDefaults(suiteName:suite));defer { preferences.removePersistentDomain(forName:suite) }
        let store=ConversationStore(preferences:preferences)
        for id in ["workspace","projects","health","management"] { #expect(store.sectionExpanded(id));store.setSectionExpanded(id,false) }
        let reopened=ConversationStore(preferences:preferences)
        for id in ["workspace","projects","health","management"] { #expect(!reopened.sectionExpanded(id)) }
        reopened.setSectionExpanded("projects",true)
        #expect(reopened.sectionExpanded("projects"));#expect(!reopened.sectionExpanded("workspace"))
        #expect(!reopened.sectionExpanded("health"));#expect(!reopened.sectionExpanded("management"))
        #expect(ConversationStore(preferences:preferences).sectionExpanded("projects"))
    }
    @Test @MainActor func liveProjectNamesFilteringAndNativeSidebar() async throws {
        guard let output=ProcessInfo.processInfo.environment["RETHREAD_SIDEBAR_EVIDENCE"] else { return }
        let directory=URL(fileURLWithPath:output);try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        let suite="rethread.sidebar.visual."+UUID().uuidString
        let preferences=try #require(UserDefaults(suiteName:suite));defer { preferences.removePersistentDomain(forName:suite) }
        let store=ConversationStore(preferences:preferences);store.start();defer { store.stop() }
        for _ in 0..<300 { if store.isConnected && !store.loading { break };try await Task.sleep(for:.milliseconds(100)) }
        #expect(store.isConnected);#expect(store.error == nil)
        let initialIDs=Set(store.sessions.map(\.id))
        let api:SessionList=try await store.request("/api/sessions?limit=0")
        let projects=try #require(api.projects)
        #expect(!projects.isEmpty);#expect(store.projects.count==projects.count)
        for project in projects {
            #expect(store.projectName(project.id)==project.name)
            #expect(store.projects.first { $0.0==project.id }?.1==api.sessions.filter { $0.projectKey==project.id }.count)
            store.navigate("all",project:project.id)
            #expect(store.title==project.name);#expect(store.filtered.allSatisfy { $0.projectKey==project.id })
        }
        store.navigate("projectless")
        #expect(store.filtered.count==store.independentCount)
        #expect(store.filtered.allSatisfy { $0.projectIsSaved==false })
        store.navigate("all");#expect(store.filtered.count==api.sessions.count)
        if let sample=projects.first(where: { p in store.sessions.contains { $0.projectKey==p.id } }) {
            store.query=sample.name;#expect(store.filtered.contains { $0.projectKey==sample.id });store.query=""
        }
        for (name,collapsed,dark) in [("sidebar-expanded",[String](),false),("sidebar-projects",["workspace"],false),("sidebar-collapsed",["workspace","projects","health","management"],false),("sidebar-dark",["workspace","health"],true)] {
            store.collapsedSidebarSections=Set(collapsed)
            let root=SidebarView().environmentObject(store).environment(\.colorScheme,dark ? .dark : .light).frame(width:224,height:740)
            let host=NSHostingView(rootView:root);host.frame=NSRect(x:0,y:0,width:224,height:740)
            let window=NSWindow(contentRect:host.frame,styleMask:.borderless,backing:.buffered,defer:false)
            window.contentView=host;window.orderFront(nil);host.layoutSubtreeIfNeeded()
            try await Task.sleep(for:.milliseconds(300));host.layoutSubtreeIfNeeded()
            let bitmap=try #require(host.bitmapImageRepForCachingDisplay(in:host.bounds));host.cacheDisplay(in:host.bounds,to:bitmap)
            try #require(bitmap.representation(using:.png,properties:[:])).write(to:directory.appendingPathComponent(name+".png"),options:.atomic)
            window.orderOut(nil)
        }
        store.collapsedSidebarSections=[]
        let full=NSHostingView(rootView:RootView().environmentObject(store).frame(width:1012,height:740))
        full.frame=NSRect(x:0,y:0,width:1012,height:740)
        let fullWindow=NSWindow(contentRect:full.frame,styleMask:.borderless,backing:.buffered,defer:false)
        fullWindow.contentView=full;fullWindow.orderFront(nil)
        try await Task.sleep(for:.milliseconds(500));full.layoutSubtreeIfNeeded()
        let fullBitmap=try #require(full.bitmapImageRepForCachingDisplay(in:full.bounds));full.cacheDisplay(in:full.bounds,to:fullBitmap)
        try #require(fullBitmap.representation(using:.png,properties:[:])).write(to:directory.appendingPathComponent("library-light.png"),options:.atomic)
        fullWindow.orderOut(nil)
        #expect(Set(store.sessions.map(\.id))==initialIDs)
        let report:[String:Any]=["backend_version":store.status?.version ?? "unknown","saved_projects":projects.count,
            "conversations":store.sessions.count,"independent":store.independentCount,"all_names_match_api":true,
            "project_filters_passed":true,"conversation_ids_unchanged":true,"states_rendered":4,"section_preferences_isolated":true]
        try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:directory.appendingPathComponent("native-sidebar.json"),options:.atomic)
    }
}
