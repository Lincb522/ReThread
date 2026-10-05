import Foundation

struct Conversation: Codable, Identifiable, Hashable, Sendable {
    let id: String
    let title: String
    let cwd: String
    let createdAt: String
    let updatedAt: String
    let isArchived: Bool
    let isTemporary: Bool
    let messageCount: Int?
    let localPath: String
    let localPathExists: Bool?
    let historyMode: String
    let model: String?
    let modelProvider: String?
    let storageRestricted: Bool?
    let visibilityFlag: Int
    let previewText: String?
    var fileBytes: Int64?
    var messages: [ChatMessage]?
    var previewLimited: Bool?
    var previewErrors: Int?
    // Derived once at the wire boundary, never inside a SwiftUI sort comparator.
    let project: String
    let projectKey: String
    let projectName: String?
    let projectRoots: [String]?
    let projectSource: String?
    let projectOrder: Int?
    let projectIsSaved: Bool?
    let updated: Date
    let searchIndex: String

    enum CodingKeys: String, CodingKey {
        case id, title, cwd, createdAt, updatedAt, isArchived, isTemporary, messageCount
        case localPath, localPathExists, historyMode, model, modelProvider, visibilityFlag
        case previewText, fileBytes, messages, previewLimited, previewErrors, storageRestricted
        case projectKey, projectName, projectRoots, projectSource, projectOrder, projectIsSaved
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        cwd = try c.decode(String.self, forKey: .cwd)
        createdAt = try c.decode(String.self, forKey: .createdAt)
        updatedAt = try c.decode(String.self, forKey: .updatedAt)
        isArchived = try c.decode(Bool.self, forKey: .isArchived)
        isTemporary = try c.decode(Bool.self, forKey: .isTemporary)
        messageCount = try c.decodeIfPresent(Int.self, forKey: .messageCount)
        localPath = try c.decode(String.self, forKey: .localPath)
        localPathExists = try c.decodeIfPresent(Bool.self, forKey: .localPathExists)
        historyMode = try c.decode(String.self, forKey: .historyMode)
        model = try c.decodeIfPresent(String.self, forKey: .model)
        modelProvider = try c.decodeIfPresent(String.self, forKey: .modelProvider)
        storageRestricted = try c.decodeIfPresent(Bool.self, forKey: .storageRestricted)
        visibilityFlag = try c.decode(Int.self, forKey: .visibilityFlag)
        previewText = try c.decodeIfPresent(String.self, forKey: .previewText)
        fileBytes = try c.decodeIfPresent(Int64.self, forKey: .fileBytes)
        messages = try c.decodeIfPresent([ChatMessage].self, forKey: .messages)
        previewLimited = try c.decodeIfPresent(Bool.self, forKey: .previewLimited)
        previewErrors = try c.decodeIfPresent(Int.self, forKey: .previewErrors)
        // Older exported responses remain decodable; live API 13 supplies all identity fields.
        projectKey = try c.decodeIfPresent(String.self, forKey: .projectKey) ?? cwd
        projectName = try c.decodeIfPresent(String.self, forKey: .projectName)
        projectRoots = try c.decodeIfPresent([String].self, forKey: .projectRoots)
        projectSource = try c.decodeIfPresent(String.self, forKey: .projectSource)
        projectOrder = try c.decodeIfPresent(Int.self, forKey: .projectOrder)
        projectIsSaved = try c.decodeIfPresent(Bool.self, forKey: .projectIsSaved)
        project = projectName ?? "未关联项目"
        updated = Self.parseDate(updatedAt) ?? .distantPast
        searchIndex = "\(title) \(projectName ?? "") \(cwd) \(id) \(previewText ?? "")".folding(options: .caseInsensitive, locale: .current)
    }
    static func parseDate(_ string: String) -> Date? {
        guard !string.isEmpty else { return nil }
        // Foundation's value-type ISO strategy shares parsing machinery and is thread-safe.
        return try? Date.ISO8601FormatStyle(includingFractionalSeconds: string.contains(".")).parse(string)
    }
}
struct ChatMessage: Codable, Hashable, Sendable {
    let role: String
    let text: String
    let timestamp: String
    let kind: String
    let phase: String
}
struct ProjectDescriptor: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let roots: [String]
    let order: Int
    let source: String
}
struct SessionList: Decodable, Sendable { let sessions: [Conversation]; let total: Int; let projects: [ProjectDescriptor]? }
struct BackendStatus: Decodable, Sendable {
    let version: String
    let codexHome: String
    let codexBinary: String?
    let codexVersion: String?
    let database: String
    let codexCliAvailable: Bool
    let csrfToken: String
    let agentAvailable: Bool?
}
struct APIError: Decodable, Sendable { let error: String }
struct Issue: Codable, Identifiable, Sendable {
    let level: String
    let code: String
    let text: String
    var id: String { code }
}
enum JSONValue: Codable, Hashable, Sendable {
    case string(String), number(Int), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode(Int.self) { self = .number(v) }
        else { self = .bool(try c.decode(Bool.self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        }
    }
    var text: String {
        switch self {
        case .string(let s): return s
        case .number(let n): return String(n)
        case .bool(let b): return String(b)
        case .null: return "null"
        }
    }
}
struct Diagnosis: Decodable, Sendable {
    let health: String?
    let storageRestricted: Bool?
    var statusTitle: String {
        if !changes.isEmpty { return "索引可修复" }
        switch health {
        case "corrupt": return "内容异常"
        case "missing": return "历史未找到"
        case "incomplete": return "检查未完成"
        case "healthy": return "内容结构正常"
        default: return scan.complete ? "检查已完成" : "检查未完成"
        }
    }
    let id: String
    let title: String
    let historyMode: String
    let issues: [Issue]
    let scan: Scan
    let changes: [String: JSONValue]
    let boundaries: [String]
    struct Scan: Decodable, Sendable {
        let lines: Int
        let invalidLines: [Int]
        let complete: Bool
        let sha256: String?
        let userEvent: Bool
    }
}
struct ActionPlan: Decodable, Identifiable, Sendable {
    let project: ProjectDescriptor?
    let archivedCount: Int?
    let hiddenCount: Int?
    let requiresExit: Bool?
    let externalDelete: Bool?
    let missingHistories: [String]?
    let dependencyOrdered: Bool?
    let token: String
    let action: String
    let id: String
    let ids: [String]
    let confirmation: String
    let targets: [Target]
    let fileBytes: Int64
    let diagnostic: Diagnosis?
    let batch: Bool?
    let blocked: [Blocked]?
    let destination: String?
    let source: String?
    let keepSource: Bool?
    let externalDestination: Bool?
    let messageCount: Int?
    let imageCount: Int?
    let audioCount: Int?
    let excludedCount: Int?
    let newTitle: String?
    struct Blocked: Decodable, Identifiable, Sendable { let id: String; let title: String; let reason: String }
    struct Target: Decodable, Identifiable, Sendable { let id: String; let title: String }
    var title: String { (batch == true && !["project_delete", "project_assign", "project_create", "merge"].contains(action) ? "批量" : "") + Action.title(action) }
    var canExecute: Bool { action == "project_delete" ? (project != nil && (blocked ?? []).isEmpty) : !ids.isEmpty }
}
struct DesktopSyncResult: Decodable, Sendable {
    let status: String
    let message: String
    let affected: Int
    let catalogVerified: Bool?
    var needsRetry: Bool { status == "pending" || status == "sent" }
}
struct ActionResult: Decodable, Sendable {
    let ok: Bool
    let action: String
    let affected: Int
    let backup: String
    let verification: String
    let newId: String?
    let outcomes: [Outcome]?
    let desktopSync: DesktopSyncResult?
    func withDesktopSync(_ sync: DesktopSyncResult) -> ActionResult {
        ActionResult(ok: ok, action: action, affected: affected, backup: backup, verification: verification, newId: newId, outcomes: outcomes, desktopSync: sync)
    }
    struct Outcome: Decodable, Identifiable, Sendable {
        let id: String; let title: String; let status: String; let detail: String
        let desktopSync: DesktopSyncResult?
        var statusText: String { ["verified":"已完成", "failed":"失败", "blocked":"预检受限", "not_executed":"未执行", "pending":"待同步", "sent":"已通知", "not_applicable":"未连接桌面端"][status] ?? status }
    }
}
struct OperationRecord: Decodable, Identifiable, Sendable {
    let action: String
    let ids: [String]
    let createdAt: String
    let status: String
    let backup: String
    let error: String?
    let desktopSync: DesktopSyncResult?
    var statusText: String {
        switch status {
        case "verified": return "本地状态验证通过"
        case "failed_unchanged": return "操作失败 · 已核对原记录未变"
        case "failed_or_partial": return "操作未完成 · 请检查备份记录"
        default: return "操作尚未验证 · 请检查备份记录"
        }
    }
    var id: String { backup }
}
struct OperationList: Decodable, Sendable { let operations: [OperationRecord] }
enum Action {
    static func title(_ value: String) -> String {
        switch value {
        case "archive": return "归档对话"
        case "unarchive": return "取消归档"
        case "delete": return "删除对话"
        case "project_delete": return "删除整个项目"
        case "project_assign": return "移动到项目"
        case "project_create": return "基于对话新建项目"
        case "desktop_sync": return "同步 Codex 列表"
        case "repair": return "索引修复"
        case "migrate": return "会话迁移"
        case "link": return "索引链接"
        case "rename": return "重命名对话"
        case "relocate": return "归位历史"
        case "merge": return "合并会话"
        default: return value
        }
    }
}
enum AppFailure: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

struct AgentJob: Decodable, Identifiable, Sendable {
    let id: String
    let threadId: String
    let state: String
    let model: String
    let provider: String
    let report: AgentReport?
    let error: String?
    let canApply: Bool
    var isRunning: Bool { ["queued", "running", "cancelling"].contains(state) }
}
struct AgentReport: Decodable, Sendable {
    let summary: String
    let reason: String
    let actions: [String]
    let risks: [String]
    let needsManualReview: Bool
}

struct BatchJob: Decodable, Sendable {
    let id: String
    let mode: String
    let state: String
    let phase: String
    let completed: Int
    let total: Int
    let error: String?
    let plan: ActionPlan?
    let result: ActionResult?
    var isRunning: Bool { state == "queued" || state == "running" }
    let currentTitle: String?
    let currentIndex: Int?
    let scopeCount: Int?
    let message: String?
    let bytesDone: Int64?
    let bytesTotal: Int64?
    let startedAt: Double?
    let updatedAt: Double?
    let phaseStartedAt: Double?
    let waitRemainingSeconds: Int?
    let rpcElapsedSeconds: Int?
    let diagnosticsAvailable: Bool?
    let stopRequested: Bool?
    var title: String {
        ["merge_write":"正在流式合并", "verify":"正在流式验证", "project_delete":"正在移除项目记录", "preflight":"正在预检", "validating":"正在核对会话状态", "executing":"正在处理",
         "backup":"正在备份历史", "backup_verify":"正在校验备份", "metadata_backup":"正在备份关联记录",
         "lookup":"正在核对删除范围", "engine_start":"正在启动 Codex", "official_rpc":"Codex 正在处理",
         "engine_cleanup":"正在结束本次调用", "verification":"正在验证结果", "desktop_sync":"正在同步 Codex 列表"][phase] ?? "正在处理"
    }
    static func elapsedLabel(since start: Double?, now: Date) -> String {
        guard let start else { return "计时中" }
        let seconds = max(0, Int(now.timeIntervalSince1970 - start))
        return seconds < 60 ? "\(seconds) 秒" : "\(seconds / 60) 分 \(seconds % 60) 秒"
    }
}
