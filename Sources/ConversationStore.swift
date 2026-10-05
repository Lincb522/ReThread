import SwiftUI
import AppKit

@MainActor
final class ConversationStore: ObservableObject {
    @Published var sessions: [Conversation] = [] { didSet { rebuildLibraryIndex() } }
    @Published var selectedID: String?
    @Published var detail: Conversation?
    @Published var diagnosis: Diagnosis?
    @Published var status: BackendStatus?
    @Published var totalCount = 0
    @Published var loading = false
    @Published var loadingDetail = false
    @Published var diagnosing = false
    @Published var busy = false
    @Published var error: String?
    @Published var notice: String?
    @Published var plan: ActionPlan?
    @Published var operations: [OperationRecord] = []
    @Published var query = "" { didSet { filteredCache = nil } }
    @Published var scope = "all" { didSet { filteredCache = nil } }
    @Published var project: String? { didSet { filteredCache = nil } }
    @Published var showOperations = false
    @Published var showSettings = false
    @Published var showAbout = false
    @Published var showRename = false
    @Published var showAgentConsent = false
    @Published var inspectorOpen = false
    @Published var detailTab = "preview"
    @Published var repairMode = "local"
    @Published var appearance = UserDefaults.standard.string(forKey: "rethreadAppearance") ?? "system" {
        didSet { UserDefaults.standard.set(appearance, forKey: "rethreadAppearance") }
    }
    @Published var pins: Set<String> = Set(UserDefaults.standard.stringArray(forKey: "rethreadPins") ?? []) { didSet { filteredCache = nil; rebuildCounts() } }
    @Published var recentOnly = false { didSet { filteredCache = nil } }
    @Published var ascending = false { didSet { filteredCache = nil } }
    @Published var batchJob: BatchJob?
    @Published var batchConnectionNotice: String?
    @Published var batchMode = false
    @Published var batchIDs: Set<String> = []
    @Published var diagnosisCache: [String: Diagnosis] = [:] { didSet { filteredCache = nil; rebuildCounts() } }
    @Published var scanning = false
    @Published var scanCompleted = 0
    @Published var scanTotal = 0
    @Published var agentJob: AgentJob?
    @Published var agentStarting = false
    @Published var agentModel = UserDefaults.standard.string(forKey: "rethreadAgentModel") ?? "" {
        didSet { UserDefaults.standard.set(agentModel, forKey: "rethreadAgentModel") }
    }
    @Published var lastResult: ActionResult?
    @Published var showMerge = false
    @Published var showProjectAssignment = false
    @Published var createProjectFromConversation = false
    @Published var projectAssignmentIDs: [String] = []
    @Published var historyAction: String?
    @Published var historyTarget = ""
    @Published var keepHistorySource = true
    @Published var mergeIDs: [String] = []
    @Published var oldDays = 90 { didSet { filteredCache = nil } }
    @Published var oldKind = "all" { didSet { filteredCache = nil } }
    @Published var focusSearch = false
    private var scanTask: Task<Void, Never>?
    private var agentTask: Task<Void, Never>?

    @Published var startupText = "正在连接本地会话库"
    private var service: Process?
    private var output: Pipe?
    private var errors: Pipe?
    private var stderrText = ""
    private var outputBuffer = ""
    private var baseURL: URL?
    private var booting = false
    private var generation = UUID()
    private let client: URLSession = {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 180
        c.timeoutIntervalForResource = 7200
        c.connectionProxyDictionary = [:]
        return URLSession(configuration: c)
    }()
    private let preferences: UserDefaults
    @Published var collapsedSidebarSections: Set<String> = [] {
        didSet { preferences.set(Array(collapsedSidebarSections).sorted(), forKey: "rethread.sidebar.collapsedSections.v1") }
    }
    private(set) var projectCatalog: [ProjectDescriptor] = []
    private(set) var projectLabels: [String: String] = [:]
    private(set) var projectPaths: [String: [String]] = [:]
    private(set) var independentCount = 0
    let dataDirectory: URL
    var codexHome: String
    var pythonPath = ""
    var codexPath = ""

    init(preferences: UserDefaults = .standard) {
        self.preferences = preferences
        collapsedSidebarSections = Set(preferences.stringArray(forKey: "rethread.sidebar.collapsedSections.v1") ?? [])
        let env = ProcessInfo.processInfo.environment
        codexHome = env["CODEX_MANAGER_HOME"] ?? UserDefaults.standard.string(forKey: "codexManagerHome") ?? NSHomeDirectory() + "/.codex"
        dataDirectory = URL(fileURLWithPath: env["CODEX_MANAGER_DATA"] ?? NSHomeDirectory() + "/Library/Application Support/Codex Conversations")
    }
    // Read models are invalidated by their inputs, not by hover, modal, or Agent updates.
    private var sortedSessions: [Conversation] = []
    private var filteredCache: [Conversation]?
    private var filteredCacheExpiry = Date.distantFuture
    private(set) var projects: [(String, Int)] = []
    private(set) var projectIndexes: [String: Int] = [:]
    private var attentionIDs: Set<String> = []
    private(set) var archivedCount = 0
    private(set) var attentionCount = 0
    private(set) var pinnedCount = 0
    private(set) var projectionBuildCount = 0

    private func rebuildLibraryIndex() {
        sortedSessions = sessions.sorted { $0.updated == $1.updated ? $0.id < $1.id : $0.updated > $1.updated }
        let groups = Dictionary(grouping: sessions.filter { $0.projectIsSaved != false }, by: \.projectKey)
        projectLabels = Dictionary(uniqueKeysWithValues: projectCatalog.map { ($0.id, $0.name) })
        projectPaths = Dictionary(uniqueKeysWithValues: projectCatalog.map { ($0.id, $0.roots) })
        for (id, rows) in groups where projectLabels[id] == nil {
            projectLabels[id] = rows.first?.project; projectPaths[id] = rows.first?.projectRoots ?? []
        }
        let order = Dictionary(uniqueKeysWithValues: projectCatalog.map { ($0.id, $0.order) })
        projects = projectLabels.keys.map { ($0, groups[$0]?.count ?? 0) }
            .sorted { (order[$0.0] ?? Int.max, $0.0) < (order[$1.0] ?? Int.max, $1.0) }
        independentCount = sessions.filter { $0.projectIsSaved == false }.count
        projectIndexes = Dictionary(uniqueKeysWithValues: projects.enumerated().map { ($0.element.0, $0.offset) })
        filteredCache = nil
        rebuildCounts()
    }
    private func rebuildCounts() {
        archivedCount = sessions.reduce(0) { $0 + ($1.isArchived ? 1 : 0) }
        pinnedCount = sessions.reduce(0) { $0 + (pins.contains($1.id) ? 1 : 0) }
        attentionIDs = Set(sessions.compactMap { item in
            let attention = diagnosisCache[item.id].map { !$0.changes.isEmpty } ?? false
            return attention ? item.id : nil
        })
        attentionCount = attentionIDs.count
    }
    var filtered: [Conversation] {
        if let filteredCache, Date() < filteredCacheExpiry { return filteredCache }
        let cutoff = Date().addingTimeInterval(-7 * 86400)
        let normalizedQuery = query.folding(options: .caseInsensitive, locale: .current)
        let result = sortedSessions.filter { item in
            let scopeOK: Bool
            switch scope {
            case "archived": scopeOK = item.isArchived
            case "active": scopeOK = !item.isArchived
            case "pinned": scopeOK = pins.contains(item.id)
            case "projectless": scopeOK = item.projectIsSaved == false
            case "repair": scopeOK = attentionIDs.contains(item.id)
            case "old": scopeOK = (oldDays == 0 || item.updated < Date().addingTimeInterval(-Double(oldDays) * 86400))
                && (oldKind == "all" || (oldKind == "external" && item.storageRestricted == true) || (oldKind == "legacy" && item.historyMode == "legacy"))
            default: scopeOK = true
            }
            return scopeOK && (project == nil || item.projectKey == project)
                && (!recentOnly || item.updated > cutoff)
                && (query.isEmpty || item.searchIndex.contains(normalizedQuery))
        }
        let ordered = ascending ? Array(result.reversed()) : result
        filteredCache = ordered
        filteredCacheExpiry = Date().addingTimeInterval(60)
        projectionBuildCount += 1
        return ordered
    }
    var title: String {
        if let project { return projectName(project) }
        return ["all":"全部对话", "archived":"已归档", "active":"未归档", "pinned":"置顶对话", "repair":"可修复", "old":"旧会话", "projectless":"独立对话"][scope] ?? "全部对话"
    }
    func projectName(_ id: String) -> String { projectLabels[id] ?? "未关联项目" }
    func projectHelp(_ id: String) -> String { ([projectName(id)] + (projectPaths[id] ?? [])).joined(separator: "\n") }
    func sectionExpanded(_ id: String) -> Bool { !collapsedSidebarSections.contains(id) }
    func setSectionExpanded(_ id: String, _ expanded: Bool) {
        if expanded { collapsedSidebarSections.remove(id) } else { collapsedSidebarSections.insert(id) }
    }
    var isConnected: Bool { status != nil }
    var agentRunning: Bool { agentStarting || agentJob?.isRunning == true }
    var hasModal: Bool { batchJob?.isRunning == true || showMerge || showProjectAssignment || historyAction != nil || plan != nil || showSettings || showAbout || showRename || showAgentConsent || lastResult != nil || error != nil }
    func needsAttention(_ item: Conversation) -> Bool { attentionIDs.contains(item.id) }
    func rowStatus(_ item: Conversation) -> String {
        if needsAttention(item) { return "索引可修复" }
        if let d = diagnosisCache[item.id], ["corrupt", "missing", "incomplete"].contains(d.health ?? "") { return d.statusTitle }
        if item.storageRestricted == true { return "外置存储" }
        if item.localPathExists == false { return "路径待检查" }
        if item.isArchived { return "已归档" }
        return diagnosisCache[item.id] == nil ? "未检查" : "结构正常"
    }
    func togglePin(_ id: String) {
        if pins.contains(id) { pins.remove(id) } else { pins.insert(id) }
        UserDefaults.standard.set(Array(pins).sorted(), forKey: "rethreadPins")
    }
    func navigate(_ target: String, project: String? = nil) {
        self.scope = target; self.project = project; if target == "old" { recentOnly = false }; inspectorOpen = false; showOperations = false; batchIDs = []
    }
    func closeModal() {
        guard !busy else { return }
        plan = nil; historyAction = nil; showMerge = false; showProjectAssignment = false; showSettings = false; showAbout = false
        showRename = false; showAgentConsent = false; lastResult = nil; error = nil
    }
    func closeInspector() { inspectorOpen = false; showOperations = false }

    func start() {
        guard service == nil else { return }
        let tag = UUID(); generation = tag
        error = nil; booting = false; status = nil; startupText = "正在连接本地会话库"
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("Backend/manager.py")
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("manager.py")
        let script = Bundle.main.bundleURL.pathExtension == "app" ? bundled : source
        guard let script, FileManager.default.fileExists(atPath: script.path) else { fail("没有找到随应用交付的维护引擎 manager.py。"); return }
        let paths = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let python = paths.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else { fail("请安装 Python 3.11+ 后重开应用。"); return }
        pythonPath = python
        codexPath = "正在识别 Codex 引擎"
        let process = Process(), pipe = Pipe(), err = Pipe()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script.path, "--port", "0", "--codex-home", codexHome, "--sqlite-home", codexHome, "--data-dir", dataDirectory.path]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        process.standardOutput = pipe; process.standardError = err
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            guard !bytes.isEmpty else { return }
            let chunk = String(decoding: bytes, as: UTF8.self)
            Task { @MainActor [weak self] in self?.consume(chunk, tag: tag) }
        }
        err.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            guard !bytes.isEmpty else { return }
            let chunk = String(decoding: bytes, as: UTF8.self)
            Task { @MainActor [weak self] in
                guard let self, self.generation == tag else { return }
                self.stderrText = String((self.stderrText + chunk).suffix(4000))
            }
        }
        process.terminationHandler = { [weak self] process in
            let code = process.terminationStatus
            Task { @MainActor [weak self] in
                guard let self, self.generation == tag else { return }
                self.status = nil; self.loading = false; self.busy = false
                self.fail("本地维护引擎已退出（\(code)）。\n\(self.stderrText)")
            }
        }
        do { try process.run(); service = process; output = pipe; errors = err }
        catch { fail(error.localizedDescription) }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(20))
            guard let self, self.generation == tag, self.status == nil else { return }
            self.fail("本地引擎尚未就绪。\n\(self.stderrText)")
        }
    }
    private func consume(_ chunk: String, tag: UUID) {
        guard generation == tag else { return }
        outputBuffer += chunk
        while let end = outputBuffer.firstIndex(of: "\n") {
            let line = String(outputBuffer[..<end]); outputBuffer.removeSubrange(...end)
            if line.hasPrefix("http://127.0.0.1:"), let url = URL(string: line), !booting {
                baseURL = url; booting = true
                Task { [weak self] in await self?.connect() }
            }
        }
    }
    func stop() {
        generation = UUID()
        scanTask?.cancel(); scanning = false
        agentJob = nil; diagnosisCache = [:]; diagnosis = nil
        agentTask?.cancel()
        output?.fileHandleForReading.readabilityHandler = nil
        errors?.fileHandleForReading.readabilityHandler = nil
        service?.terminationHandler = nil
        if let service, service.isRunning { service.terminate() }
        service = nil; output = nil; errors = nil; status = nil; baseURL = nil
        outputBuffer = ""; stderrText = ""
    }
    func reconnect() { stop(); start() }
    private func connect() async {
        do {
            status = try await request("/api/status")
            codexPath = status?.codexBinary ?? "引擎未返回路径"
            await refresh()
        }
        catch { fail(error.localizedDescription) }
    }
    func request<T: Decodable & Sendable>(_ path: String, body: [String: Any]? = nil) async throws -> T {
        guard let baseURL, let url = URL(string: path, relativeTo: baseURL) else { throw AppFailure.message("本地引擎尚未连接。") }
        var req = URLRequest(url: url)
        if path == "/api/execute" { req.timeoutInterval = 7200 }
        if body == nil && path.hasPrefix("/api/batch/jobs/") { req.timeoutInterval = 5 }
        if let body {
            guard let status else { throw AppFailure.message("缺少当前连接令牌，请重新连接。") }
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.setValue(status.csrfToken, forHTTPHeaderField: "X-Codex-CSRF")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await client.data(for: req)
        guard let response = response as? HTTPURLResponse else { throw AppFailure.message("本地引擎返回无效响应。") }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard (200..<300).contains(response.statusCode) else {
            let message = (try? decoder.decode(APIError.self, from: data))?.error ?? "本地引擎返回 HTTP \(response.statusCode)"
            throw AppFailure.message(message)
        }
        return try await Task.detached(priority: .userInitiated) {
            let backgroundDecoder = JSONDecoder()
            backgroundDecoder.keyDecodingStrategy = .convertFromSnakeCase
            return try backgroundDecoder.decode(T.self, from: data)
        }.value
    }
    func applyLibrarySnapshot(_ response: SessionList) {
        let old = Dictionary(uniqueKeysWithValues: sessions.map { ($0.id, $0) })
        projectCatalog = response.projects ?? []
        sessions = response.sessions; totalCount = response.total
        if let project, projectLabels[project] == nil { self.project = nil }
        let current = Dictionary(uniqueKeysWithValues: sessions.map { ($0.id, $0) })
        diagnosisCache = diagnosisCache.filter { id, _ in current[id] != nil && old[id] == current[id] }

        batchIDs.formIntersection(Set(sessions.map(\.id)))
        if let selectedID, !sessions.contains(where: { $0.id == selectedID }) { self.selectedID = nil; detail = nil; inspectorOpen = false }
    }
    func refresh() async {
        guard !loading else { return }; loading = true
        defer { loading = false }
        do {
            let response: SessionList = try await request("/api/sessions?limit=0")
            applyLibrarySnapshot(response)
        } catch { fail(error.localizedDescription) }
    }
    func select(_ id: String?) async {
        selectedID = id; diagnosis = id.flatMap { diagnosisCache[$0] }; detail = nil
        if id != nil { inspectorOpen = true; showOperations = false }
        guard let id else { loadingDetail = false; return }
        loadingDetail = true
        defer { if selectedID == id { loadingDetail = false } }
        do {
            let result: Conversation = try await request("/api/sessions/\(id)")
            guard selectedID == id else { return }; detail = result
        } catch { if selectedID == id { fail(error.localizedDescription) } }
    }
    func diagnose() async {
        guard let id = selectedID, !diagnosing else { return }; diagnosing = true
        defer { diagnosing = false }
        do {
            let result: Diagnosis = try await request("/api/sessions/\(id)/diagnosis")
            diagnosisCache[id] = result
            guard selectedID == id else { return }; diagnosis = result
        } catch { fail(error.localizedDescription) }
    }
    func openHistoryTool(_ action: String) {
        guard selectedID != nil, !busy, ["migrate", "link"].contains(action) else { return }
        historyTarget = ""; keepHistorySource = true; historyAction = action
    }
    func chooseHistoryTarget() {
        guard let action = historyAction, !busy else { return }
        let panel = NSOpenPanel()
        panel.title = action == "migrate" ? "选择历史迁移目录" : "选择此会话的原始历史文件"
        panel.canChooseDirectories = action == "migrate"; panel.canChooseFiles = action == "link"
        panel.allowsMultipleSelection = false; panel.canCreateDirectories = action == "migrate"
        panel.prompt = "选择"
        if panel.runModal() == .OK, let url = panel.url { historyTarget = url.path }
    }
    func prepareHistory() async {
        guard let action = historyAction, let id = selectedID, !busy else { return }
        busy = true; defer { busy = false }
        do {
            let result: ActionPlan = try await request("/api/plans", body: ["action":action,"id":id,"target_path":historyTarget,"keep_source":keepHistorySource])
            historyAction = nil; plan = result
        } catch { fail(error.localizedDescription) }
    }
    func repairIndex() async {
        guard let id = selectedID, !busy, !diagnosing else { return }
        detailTab = "diagnosis"; repairMode = "local"
        await diagnose()
        guard selectedID == id, error == nil, let result = diagnosis else { return }
        if result.changes.isEmpty {
            notice = result.health == "healthy" ? "索引引用正常，无需修复" : "未生成自动修复方案，可使用索引链接选择原始历史文件"
        } else { await prepare("repair") }
    }
    func prepare(_ action: String, title: String? = nil) async {
        guard let id = selectedID, !busy else { return }; busy = true
        defer { busy = false }
        do {
            var body: [String: Any] = ["action": action, "id": id]
            if let title { body["title"] = title }
            plan = try await request("/api/plans", body: body)
        } catch { fail(error.localizedDescription) }
    }
    func execute(_ plan: ActionPlan, confirmation: String) async {
        guard !busy else { return }; busy = true
        defer { busy = false }
        do {
            let result: ActionResult
            if plan.batch == true {
                let job = try await runBatch("execute", body: ["token": plan.token, "confirmation": confirmation])
                guard let value = job.result else { throw AppFailure.message("批量任务未返回执行结果，请检查操作记录。") }
                result = value
            } else {
                result = try await request("/api/execute", body: ["token": plan.token, "confirmation": confirmation])
            }
            self.plan = nil
            let affected = Set(plan.ids)
            diagnosisCache = diagnosisCache.filter { !affected.contains($0.key) }
            lastResult = result
            notice = result.ok ? (result.desktopSync?.message ?? "操作完成 · 本地状态验证通过") : "部分操作未完成，请查看逐项结果"
            batchIDs = []
            if result.action == "project_delete", result.ok { project = nil; scope = "all" }
            if ["delete", "project_delete"].contains(result.action) { selectedID = nil; detail = nil; inspectorOpen = false }
            await refresh()
            if let newID = result.newId {
                await select(newID)
                if result.ok && result.action == "merge" {
                    await openAndVerifyMerge(result)
                }
            }
            else if let selectedID { await select(selectedID) }
        } catch { fail(error.localizedDescription) }
    }
    func openAndVerifyMerge(_ result: ActionResult) async {
        guard let id = result.newId else { return }
        guard URL(fileURLWithPath: codexHome).standardizedFileURL == URL(fileURLWithPath: NSHomeDirectory() + "/.codex").standardizedFileURL,
              let database = status?.database, URL(fileURLWithPath: database).deletingLastPathComponent().standardizedFileURL == URL(fileURLWithPath: codexHome).standardizedFileURL else {
            notice = "合并已保存到所选会话库；独立会话库未向默认 Codex 窗口发送打开请求。"; return
        }
        guard openCodex(id) else { notice = "合并已保存；系统未接受 Codex 打开请求，请手动打开后核验。"; return }
        do {
            for attempt in 0..<10 {
                if attempt > 0 { try await Task.sleep(for: .milliseconds(500)) }
                let sync: DesktopSyncResult = try await request("/api/merge/sync", body: ["id":id])
                lastResult = result.withDesktopSync(sync)
                if sync.catalogVerified == true { notice = sync.message; return }
            }
        } catch { notice = "合并已保存；桌面同步核验失败：" + error.localizedDescription }
    }
    func syncDesktop() async {
        guard !busy, isConnected else { return }; busy = true
        defer { busy = false }
        do {
            let result: ActionResult = try await request("/api/desktop-sync", body: [:])
            lastResult = result
            notice = result.desktopSync?.message ?? result.verification
            if showOperations { await loadOperations() }
        } catch { fail(error.localizedDescription) }
    }
    func loadOperations() async {
        showOperations = true; inspectorOpen = true
        do { let result: OperationList = try await request("/api/operations"); operations = result.operations }
        catch { fail(error.localizedDescription) }
    }
    func scanAll() {
        if scanning { scanTask?.cancel(); return }
        guard isConnected else { return }
        scanning = true; scanCompleted = 0
        let ids = filtered.map(\.id); scanTotal = ids.count
        scanTask = Task { [weak self] in
            guard let self else { return }
            defer { self.scanning = false }
            for id in ids {
                if Task.isCancelled { return }
                do {
                    let d: Diagnosis = try await self.request("/api/sessions/\(id)/diagnosis")
                    guard !Task.isCancelled else { return }
                    self.diagnosisCache[id] = d; self.scanCompleted += 1
                    if self.selectedID == id { self.diagnosis = d }
                } catch {
                    if !Task.isCancelled { self.fail("会话检查中断：\(error.localizedDescription)") }
                    return
                }
            }
            self.notice = "检查完成 · \(self.scanCompleted) 段对话"
        }
    }
    func prepareProjectDelete(_ id: String) async {
        guard !busy, isConnected else { return }
        busy = true; defer { busy = false }
        do {
            let job = try await runBatch("plan", body: ["action":"project_delete", "project_id":id])
            guard let value = job.plan else { throw AppFailure.message("项目预检未返回确认计划。") }
            plan = value
        } catch { fail(error.localizedDescription) }
    }
    func prepareBatch(_ action: String) async {
        guard !busy, !batchIDs.isEmpty else { return }
        busy = true; defer { busy = false }
        do {
            let job = try await runBatch("plan", body: ["action":action, "ids":batchIDs.sorted()])
            guard let value = job.plan else { throw AppFailure.message("批量预检未返回确认计划。") }
            plan = value
        } catch { fail(error.localizedDescription) }
    }
    private func runBatch(_ mode: String, body: [String: Any]) async throws -> BatchJob {
        var job: BatchJob = try await request("/api/batch/jobs/\(mode)", body: body)
        batchJob = job; batchConnectionNotice = nil
        defer { batchJob = nil; batchConnectionNotice = nil }
        var statusFailures = 0
        while job.isRunning {
            try await Task.sleep(for: .milliseconds(500))
            do {
                job = try await request("/api/batch/jobs/\(job.id)")
                batchJob = job; batchConnectionNotice = nil; statusFailures = 0
            } catch {
                guard service?.isRunning == true else {
                    throw AppFailure.message("本地引擎已停止，请检查操作记录与备份；未自动重试批量操作。任务：\(job.id)")
                }
                // Only transient network failures may retry this GET; HTTP/schema errors are real failures.
                statusFailures += 1
                guard Self.canRetryBatchStatus(error, failures: statusFailures) else {
                    throw AppFailure.message("进度读取失败：\(error.localizedDescription)\n任务：\(job.id)\n后台操作结果尚未确认，请查看操作记录；没有重复提交删除。")
                }
                batchConnectionNotice = "状态连接中断，正在重新读取（\(statusFailures)/5）；不会重复提交操作。"
                try await Task.sleep(for: .seconds(2))
            }
        }
        guard job.state == "completed" else { throw AppFailure.message(job.error ?? "批量任务未完成，请检查操作记录。") }
        return job
    }
    nonisolated static func canRetryBatchStatus(_ error: Error, failures: Int) -> Bool {
        guard failures <= 5, let network = error as? URLError else { return false }
        return [.timedOut, .networkConnectionLost, .cannotConnectToHost, .notConnectedToInternet].contains(network.code)
    }
    func stopBatchAfterCurrent() async {
        guard let job = batchJob, job.isRunning, job.mode == "execute", job.stopRequested != true else { return }
        do {
            let _: BatchJob = try await request("/api/batch/jobs/stop", body: ["job_id":job.id])
            batchConnectionNotice = "已请求停止，当前项完成并验证后，不再处理后续会话。"
        } catch { batchConnectionNotice = "停止请求未确认：\(error.localizedDescription)" }
    }
    var allFilteredSelected: Bool { !filtered.isEmpty && batchIDs == Set(filtered.map(\.id)) }
    func toggleSelectAll() { batchIDs = allFilteredSelected ? [] : Set(filtered.map(\.id)) }

    func openProjectAssignment(_ ids: [String], create: Bool = false) {
        guard !busy, !ids.isEmpty else { return }
        projectAssignmentIDs = ids; createProjectFromConversation = create; showProjectAssignment = true
    }
    func prepareProjectAssignment(projectID: String?, name: String, root: String) async {
        guard !busy else { return }; busy = true; defer { busy = false }
        var body: [String: Any] = ["action":projectID == nil ? "project_create" : "project_assign", "ids":projectAssignmentIDs]
        if let projectID { body["project_id"] = projectID }
        else { body["name"] = name; body["root"] = root }
        do {
            let job = try await runBatch("plan", body:body)
            guard let result = job.plan else { throw AppFailure.message("项目预检未返回计划") }
            showProjectAssignment = false; plan = result
        } catch { fail(error.localizedDescription) }
    }

    func openMerge() {
        guard batchIDs.count >= 2 else { fail("合并至少选择 2 条会话，数量不设上限。"); return }
        mergeIDs = sessions.filter { batchIDs.contains($0.id) }.sorted { $0.updated < $1.updated }.map(\.id)
        showMerge = true
    }
    func prepareMerge(title: String) async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do {
            let job = try await runBatch("plan", body:["action":"merge", "ids":mergeIDs,"title":title])
            guard let result = job.plan else { throw AppFailure.message("合并预检未返回计划") }
            showMerge = false; plan = result
        } catch { fail(error.localizedDescription) }
    }
    func startAgent() async {
        guard let id = selectedID, !agentRunning else { return }
        agentStarting = true; showAgentConsent = false; agentJob = nil
        defer { agentStarting = false }
        do {
            let job: AgentJob = try await request("/api/agent/jobs", body: ["id":id,"consent":true,"model":agentModel])
            agentJob = job; pollAgent(job.id)
        } catch { fail(error.localizedDescription) }
    }
    private func pollAgent(_ id: String) {
        agentTask?.cancel()
        agentTask = Task { [weak self] in
            guard let self else { return }
            do {
                while !Task.isCancelled {
                    let job: AgentJob = try await self.request("/api/agent/jobs/\(id)")
                    guard !Task.isCancelled else { return }
                    self.agentJob = job
                    if !job.isRunning { return }
                    try await Task.sleep(for: .milliseconds(750))
                }
            } catch {
                if !Task.isCancelled { self.fail("Agent 状态读取失败：\(error.localizedDescription)"); self.agentJob = nil }
            }
        }
    }
    func cancelAgent() async {
        guard let job = agentJob, job.isRunning else { return }
        do { agentJob = try await request("/api/agent/cancel", body: ["job_id":job.id]) }
        catch { fail(error.localizedDescription) }
    }
    func prepareAgentPlan() async {
        guard let job = agentJob, job.canApply, !busy else { return }
        busy = true; defer { busy = false }
        do { plan = try await request("/api/agent/plan", body: ["job_id":job.id]) }
        catch { fail(error.localizedDescription) }
    }
    func openAgent() async {
        guard let id = selectedID ?? filtered.first?.id else { notice = "请先选择一段对话"; return }
        detailTab = "diagnosis"; repairMode = "agent"
        await select(id)
    }
    func chooseHome() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.showsHiddenFiles = true; panel.message = "选择含 state_*.sqlite 的 Codex 本地数据目录"; panel.prompt = "连接此目录"
        if panel.runModal() == .OK, let url = panel.url {
            codexHome = url.path; UserDefaults.standard.set(url.path, forKey: "codexManagerHome")
            sessions = []; selectedID = nil; detail = nil; reconnect()
        }
    }
    func revealBackup(_ path: String) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
    private func fail(_ message: String) { error = message; startupText = "连接需要处理" }
}
