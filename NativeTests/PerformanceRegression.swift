import Foundation
import Testing
@testable import CodexConversations

struct PerformanceRegression {
    @Test func isoTimestampVariants() throws {
        let z = try #require(Conversation.parseDate("2026-10-05T05:00:00Z"))
        #expect(Conversation.parseDate("2026-10-05T05:00:00+00:00") == z)
        #expect(Conversation.parseDate("2026-10-05T13:00:00+08:00") == z)
        let fractional = try #require(Conversation.parseDate("2026-10-05T05:00:00.123456+00:00"))
        #expect(abs(fractional.timeIntervalSince(z) - 0.123456) < 0.00001)
        #expect(Conversation.parseDate("") == nil)
        #expect(Conversation.parseDate("not-a-date") == nil)
    }

    @Test @MainActor func realLibraryProjectionAndInvalidation() throws {
        let env = ProcessInfo.processInfo.environment
        let path = try #require(env["RETHREAD_PERFORMANCE_LIBRARY"])
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        let list = try d.decode(SessionList.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        #expect(list.sessions.count > 100)
        let store = ConversationStore()
        let start = Date(); store.applyLibrarySnapshot(list)
        let indexMS = Date().timeIntervalSince(start) * 1000
        let original = store.filtered.map(\.id); #expect(original.count == list.sessions.count)
        let builds = store.projectionBuildCount
        let cachedStart = Date(); var total = 0
        for _ in 0..<1000 { total += store.filtered.count; _ = store.projects.count; _ = store.attentionCount }
        let cachedMS = Date().timeIntervalSince(cachedStart) * 1000
        #expect(total == original.count * 1000); #expect(store.projectionBuildCount == builds)
        store.showSettings = true; _ = store.filtered; store.showSettings = false
        #expect(store.projectionBuildCount == builds)
        store.ascending = true; #expect(store.filtered.map(\.id) == Array(original.reversed()))
        store.ascending = false
        let sample = try #require(list.sessions.first)
        store.query = sample.id; #expect(store.filtered.map(\.id) == [sample.id])
        store.query = ""; store.scope = "archived"; #expect(store.filtered.count == list.sessions.filter(\.isArchived).count)
        store.scope = "all"; store.project = sample.projectKey
        #expect(store.filtered.allSatisfy { $0.projectKey == sample.projectKey })
        store.project = nil; store.scope = "pinned"; store.pins = [sample.id]
        #expect(store.filtered.map(\.id) == [sample.id]); #expect(store.pinnedCount == 1)
        store.scope = "all"; store.recentOnly = true
        #expect(store.filtered.allSatisfy { $0.updated > Date().addingTimeInterval(-7 * 86400) })
        store.recentOnly = false
        let diagnosticData = try JSONSerialization.data(withJSONObject:["id":sample.id,"title":"diagnostic","history_mode":"legacy","issues":[["code":"performance_check","level":"warning","text":"check"]],"scan":["lines":0,"invalid_lines":[],"complete":true,"user_event":true],"changes":[:],"boundaries":[]])
        store.diagnosisCache[sample.id] = try d.decode(Diagnosis.self, from: diagnosticData)
        store.scope = "repair"; #expect(!store.filtered.contains { $0.id == sample.id })
        #expect(store.attentionCount == 0)
        store.scope = "all"
        var searchMS: [Double] = []
        for term in ["S","Sw","Swi","Swif","Swift","修","修复","",sample.id,"", "Noctra", ""] {
            let t = Date(); store.query = term; _ = store.filtered
            searchMS.append(Date().timeIntervalSince(t) * 1000)
            let expected = Set(list.sessions.filter { term.isEmpty || "\($0.title) \($0.projectName ?? "") \($0.cwd) \($0.id) \($0.previewText ?? "")".localizedCaseInsensitiveContains(term) }.map(\.id))
            #expect(Set(store.filtered.map(\.id)) == expected)
        }
        store.sessions = Array(list.sessions.dropFirst())
        #expect(store.filtered.count == list.sessions.count - 1)
        #expect(store.projects.reduce(0) { $0 + $1.1 } + store.independentCount == store.sessions.count)
        let result: [String:Any] = ["conversations":list.sessions.count,"index_ms":indexMS,"cached_1000_reads_ms":cachedMS,"search_edits_ms":searchMS,"cache_rebuilt_for_unrelated_ui":false,"date_reparsed_during_sort":false]
        if let output = env["RETHREAD_PERFORMANCE_RESULT"] {
            try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:output),options:.atomic)
        }
        // Generous real-machine budgets catch a seconds-long UI freeze, not sub-ms scheduler noise.
        #expect(cachedMS < 100)
        #expect(searchMS.max()! < 200)
    }
}
