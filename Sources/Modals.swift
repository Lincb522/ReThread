import SwiftUI

struct ModalHost: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        Group {
            if let error = store.error { ErrorPanel(message: error) }
            else if let job = store.batchJob, job.isRunning { BatchProgressPanel(job: job) }
            else if let plan = store.plan { ConfirmationView(plan: plan).id(plan.token) }
            else if let result = store.lastResult { SuccessPanel(result: result) }
            else if store.historyAction != nil { HistoryPathPanel() }
            else if store.showMerge { MergePanel() }
            else if store.showProjectAssignment { ProjectAssignmentPanel() }
            else if store.showAbout { AboutPanel() }
            else if store.showAgentConsent { AgentConsentPanel() }
            else if store.showRename { RenamePanel() }
            else if store.showSettings { SettingsPanel() }
        }.frame(width: store.showAbout ? 440 : 500)
            .background(p.surface, in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(p.border))
            .compositingGroup().shadow(color: .black.opacity(0.22), radius: 35, y: 15)
            .foregroundStyle(p.text).accessibilityAddTraits(.isModal)
    }
}
struct ModalHeading: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let title: String
    let icon: String
    var destructive = false
    var body: some View {
        let p = Palette(scheme)
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Icon(name: icon, size: 21).foregroundStyle(destructive ? p.red : p.secondary).frame(width: 43, height: 43).background(destructive ? p.redSoft : p.soft, in: RoundedRectangle(cornerRadius: 11)).overlay(RoundedRectangle(cornerRadius: 11).strokeBorder(p.border.opacity(0.6)))
                Spacer(); ToolButton(icon: "x", title: "关闭弹窗") { store.closeModal() }.disabled(store.busy)
            }
            Text(title).font(.system(size: 23, weight: .semibold)).tracking(-0.6).fixedSize(horizontal: false, vertical: true)
        }
    }
}
struct ModalFooter<Content: View>: View {
    @Environment(\.colorScheme) private var scheme
    @ViewBuilder var content: () -> Content
    var body: some View { HStack(spacing: 10, content: content).padding(.horizontal, 29).padding(.vertical, 19).background(Palette(scheme).soft.opacity(0.65)).overlay(alignment: .top) { Line() } }
}
struct ConfirmationView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let plan: ActionPlan
    @State private var confirmation = ""
    @FocusState private var focused: Bool
    private var p: Palette { Palette(scheme) }
    private var isDelete: Bool { ["delete", "project_delete"].contains(plan.action) }
    private var requiredConfirmation: String { plan.action == "project_delete" ? (plan.project?.name ?? "") : "删除" }
    private var buttonTitle: String { ["project_delete":"备份并删除项目", "delete":"备份并删除", "repair":"备份并修复", "migrate":"备份并迁移", "link":"备份并链接"][plan.action] ?? plan.title }
    private var actionIcon: String { ["project_delete":"trash-2", "delete":"trash-2", "repair":"wrench", "migrate":"hard-drive", "link":"link"][plan.action] ?? "archive" }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            AdaptiveModalScroll(maxHeight: isDelete ? 430 : 560) {
                VStack(alignment: .leading, spacing: 20) {
                    ModalHeading(title: plan.title, icon: actionIcon, destructive: isDelete)
                    Text(explanation).font(.system(size: 12)).foregroundStyle(p.secondary).lineSpacing(6)
                    if plan.requiresExit == true { Text(plan.action == "project_delete" ? "执行前请结束任务并完全退出 Codex / ChatGPT 与 CLI。会话全部验证删除后才移除项目登记；任一项受限或失败都会保留项目记录。" : "项目归属同步前，请完全退出 Codex / ChatGPT 与 CLI，避免旧缓存覆盖。完成后重新打开 Codex，即可查看新项目归属。").font(.system(size: 11)).foregroundStyle(p.amber).lineSpacing(5) }
                    if let project = plan.project {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(project.name).font(.system(size: 15, weight: .semibold))
                            Text(plan.action == "project_delete" ? "包含已归档 \(plan.archivedCount ?? 0) 段 · 不受搜索与列表筛选影响" : "目标项目 · 保持会话工作目录与原始消息不变").font(.system(size: 10)).foregroundStyle(p.secondary)
                            Text("保留源码目录\n" + project.roots.joined(separator: "\n")).font(.system(size: 10, design: .monospaced)).foregroundStyle(p.muted).textSelection(.enabled)
                        }.padding(15).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 7))
                    }
                    Text("本次范围 · \(plan.targets.count) 段对话").font(.system(size: 11, weight: .medium))
                    if plan.targets.isEmpty { Text("空项目：仅移除项目记录。").font(.system(size: 11)).foregroundStyle(p.secondary) }
                    else { ScrollView {
                        LazyVStack(alignment: .leading, spacing: 13) {
                        ForEach(plan.targets) { item in HStack(spacing: 12) { Icon(name: "messages-square", size: 18); VStack(alignment: .leading, spacing: 5) { Text(item.title.isEmpty ? "未命名会话 · " + String(item.id.prefix(8)) : item.title).font(.system(size: 12, weight: .medium)).lineLimit(2); Text(item.id).font(.system(size: 9, design: .monospaced)).foregroundStyle(p.muted).textSelection(.enabled) }; Spacer(minLength: 0) } }
                        }.padding(15)
                    }.frame(height: min(210, max(65, CGFloat(plan.targets.count) * 58 + 17))).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 7)).overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(p.line))
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        check("先备份现存历史及关联索引记录")
                        check(plan.action == "repair" ? "只更新已确认的元数据字段" : "按上方预检范围执行，不修改项目文件")
                        check("执行后重新验证状态并记录结果")
                    }
                    if plan.dependencyOrdered == true {
                        Text("已按历史引用排序：先处理分叉，再处理来源。下列顺序即执行顺序。").font(.system(size: 11)).foregroundStyle(p.secondary)
                    }
                    if isDelete, let missing = plan.missingHistories, !missing.isEmpty {
                        Text("\(missing.count) 条会话的历史文件已缺失。本次会备份仍存在的文件及全部索引记录，再删除残留会话；缺失的历史内容不计作已备份，也不要求先修复。")
                            .font(.system(size: 11)).foregroundStyle(p.secondary).lineSpacing(5)
                            .padding(13).frame(maxWidth: .infinity, alignment: .leading)
                            .background(p.soft, in: RoundedRectangle(cornerRadius: 6))
                    }
                    if isDelete {
                        Text("删除后没有一键撤销。备份用于人工恢复，本操作不删除云端副本。").font(.system(size: 11)).foregroundStyle(p.red).lineSpacing(5).padding(13).frame(maxWidth: .infinity, alignment: .leading).background(p.redSoft, in: RoundedRectangle(cornerRadius: 6))

                    }
                    if ["repair", "relocate", "migrate", "link"].contains(plan.action) { Text("执行前请结束任务并退出正在写入数据的 Codex / ChatGPT 客户端与 CLI。修复本地引用不代表远端请求异常已解决。").font(.system(size: 10)).foregroundStyle(p.muted).lineSpacing(5) }
                    if plan.batch == true { Text("整批只确认一次；逐项备份与验证。执行遇到失败即停止后续，不会自动重试。").font(.system(size: 11)).foregroundStyle(p.secondary).lineSpacing(4) }
                    if let blocked = plan.blocked, !blocked.isEmpty {
                        Text("以下 \(blocked.count) 条预检受限，不会执行").font(.system(size: 12, weight: .medium)).foregroundStyle(p.amber)
                        ScrollView { LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(blocked) { item in
                            VStack(alignment: .leading, spacing: 6) { Text(item.title.isEmpty ? "未命名会话 · " + String(item.id.prefix(8)) : item.title).font(.system(size: 11, weight: .medium)); Text(item.reason).font(.system(size: 10)).foregroundStyle(p.muted).lineLimit(4) }.padding(12).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 7))
                        }
                        }}.frame(height: min(170, CGFloat(blocked.count) * 100))
                    }
                    if plan.destination != nil { Text(pathSummary).font(.system(size: 10, design: .monospaced)).foregroundStyle(p.secondary).textSelection(.enabled) }
                    if plan.externalDestination == true { Text("目标位于 Codex 标准历史目录之外。后续归档仍需先归位，删除可使用现有外置历史流程。").font(.system(size: 10)).foregroundStyle(p.amber).lineSpacing(4) }
                    if let count = plan.messageCount { Text("新会话：\(plan.newTitle ?? "")\n按以上顺序合并 \(count) 条消息，包含 \(plan.imageCount ?? 0) 张图片、\(plan.audioCount ?? 0) 段音频；排除 \(plan.excludedCount ?? 0) 条系统、工具或内部记录。保留全部来源会话，不调用模型。").font(.system(size: 11)).foregroundStyle(p.secondary).lineSpacing(5) }
                    HStack(spacing: 7) { Icon(name: "hard-drive", size: 12); Text("\(plan.ids.count) 段对话 · \(ByteCountFormatter.string(fromByteCount: plan.fileBytes, countStyle: .file)) 历史文件") }.font(.system(size: 9)).foregroundStyle(p.muted)
                    if store.busy { HStack(spacing: 11) { ProgressView().controlSize(.small); Text("正在备份、执行与验证，请保持应用打开。").font(.system(size: 11)) } }
                }.padding(29)
            }
            if isDelete {
                HStack(spacing: 14) {
                    Text(plan.action == "project_delete" ? "输入项目名称确认" : "输入「删除」以确认").font(.system(size: 11)).foregroundStyle(p.secondary)
                    TextField(requiredConfirmation, text: $confirmation).textFieldStyle(.plain).font(.system(size: 12))
                        .padding(10).background(p.soft, in: RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(p.border))
                        .focused($focused).accessibilityIdentifier("delete-confirmation")
                }.padding(.horizontal, 29).padding(.vertical, 12).overlay(alignment: .top) { Line() }
            }
            ModalFooter { Text("计划有效期 5 分钟").font(.system(size: 9)).foregroundStyle(p.muted); Spacer(); ActionButton(title: "取消") { store.closeModal() }.disabled(store.busy); ActionButton(title: buttonTitle, icon: isDelete ? "trash-2" : nil, primary: true, destructive: isDelete) { Task { await store.execute(plan, confirmation: plan.confirmation) } }.disabled((isDelete && confirmation != requiredConfirmation) || store.busy || !plan.canExecute) }
        }.onAppear { if isDelete { focused = true } }
    }
    private var pathSummary: String {
        let source = plan.source.map { "当前索引\n" + $0 + "\n\n" } ?? ""
        let destination = "目标索引\n" + (plan.destination ?? "")
        let disposition = plan.keepSource == false ? "校验完成并更新索引后移除来源文件；备份保留。" : "来源文件保留，不修改目录软链接。"
        return source + destination + "\n\n" + disposition
    }
    private var explanation: String {
        switch plan.action {
        case "relocate": return "复制当前这一条历史到 Codex 标准目录，逐字节校验后更新索引引用。请先退出正在写入历史的 Codex / ChatGPT 与 CLI。保留外置原件，不改目录软链接。"
        case "merge": return "将以下会话的文字、图片和音频按顺序合并成一条新会话。保留用户与助手角色；工具执行链、系统指令及内部上下文不合并。来源原件不变。"
        case "repair": return "只更新已确认的索引字段，保留原始消息和会话 ID。"
        case "migrate": return "将此会话历史复制到选择的目录，完整校验后更新索引。保持会话 ID、项目、归档状态和消息内容不变。"
        case "link": return "仅将此会话的索引指向已选历史文件，不搬动文件、不创建软链接。已核对会话 ID 与完整历史内容。"
        case "archive": return "将对话移入归档，保留历史记录。可在「已归档」中取消归档。"
        case "unarchive": return "将归档中的对话恢复到会话列表，保留已有历史。"
        case "project_assign": return "将所选对话移动到此项目。同步官方项目归属与 Codex 桌面登记，不移动源码或历史文件，不复制生成另一条对话。"
        case "project_create": return "使用所选文件夹在 Codex 登记新项目，并将这些对话移入项目。文件夹需已存在，不移动源码，也不修改其他会话归属。"
        case "project_delete": return "永久删除此项目的全部本地会话、已归档会话及项目登记。不是仅从列表移除；磁盘源码和云端项目保留。"
        case "delete": return plan.externalDelete == true ? "删除范围包含外置历史。先备份，再由 Codex 官方接口直接删除原文件及关联记录，无需先归位或退出整个 Codex。保留备份，不修改月份软链接及其他会话。" : "永久删除以下本地对话及关联子线程。请核对预检范围。"
        case "rename": return "只修改所选对话标题，保留历史内容与会话 ID。"
        default: return "确认下方操作范围。"
        }
    }
    private func check(_ text: String) -> some View { HStack(spacing: 9) { Icon(name: "check", size: 13).foregroundStyle(p.accent); Text(text).font(.system(size: 11)).foregroundStyle(p.secondary) } }
}
struct AboutPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 0) {
                AppLogo(size: 66).padding(.top, 4).padding(.bottom, 22)
                Text("续言").font(.system(size: 27, weight: .medium)).tracking(3)
                Text("ReThread").font(.system(size: 13)).tracking(2).foregroundStyle(p.secondary).padding(.top, 7)
                Text("Codex 对话管理工具").font(.system(size: 11)).tracking(1).foregroundStyle(p.muted).padding(.top, 18)
                HStack(spacing: 9) { Text("\(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.2.0") (\(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "开发版"))"); Text("·"); Text("macOS") }.font(.system(size: 9, design: .monospaced)).foregroundStyle(p.muted).padding(.top, 14).padding(.bottom, 22)
                Line()
                HStack(spacing: 12) {
                    Text("Z").font(.system(size: 15, weight: .medium)).frame(width: 37, height: 37).background(p.soft, in: Circle()).overlay(Circle().strokeBorder(p.border))
                    VStack(alignment: .leading, spacing: 5) { Text("设计与开发").font(.system(size: 9)).foregroundStyle(p.muted); Text("Zijiu522").font(.system(size: 14, weight: .medium)) }
                    Spacer(); Text("INDEPENDENT").font(.system(size: 7, design: .monospaced)).tracking(1).foregroundStyle(p.muted).padding(5).overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(p.border))
                }.padding(.horizontal, 3).padding(.vertical, 19)
                Line()
                Text("原生 macOS 应用\n对话管理 · 归档 · 删除 · Agent 辅助修复").font(.system(size: 11)).foregroundStyle(p.secondary).lineSpacing(8).multilineTextAlignment(.center).padding(.top, 23).padding(.bottom, 17)
                Label("本地管理 · Agent 按需调用", systemImage: "checkmark.shield").font(.system(size: 8)).foregroundStyle(p.muted)
                Text("非 OpenAI 官方应用").font(.system(size: 8)).foregroundStyle(p.muted).padding(.top, 6)
            }.padding(.horizontal, 29).padding(.top, 29).padding(.bottom, 26)
                .overlay(alignment: .topTrailing) { ToolButton(icon: "x", title: "关闭关于页") { store.showAbout = false }.padding(14) }
            ModalFooter { Text("© 2026 Zijiu522").font(.system(size: 9)).foregroundStyle(p.muted); Spacer(); ActionButton(title: "继续使用", primary: true) { store.showAbout = false } }
        }
    }
}
struct SettingsPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            AdaptiveModalScroll {
                VStack(alignment: .leading, spacing: 22) {
                    ModalHeading(title: "偏好设置", icon: "settings-2")
                    Text("外观").font(.system(size: 11, weight: .medium))
                    HStack(spacing: 10) { theme("浅色", icon: "sun", key: "light"); theme("深色", icon: "moon", key: "dark"); theme("跟随系统", icon: "monitor", key: "system") }
                    Line()
                    VStack(alignment: .leading, spacing: 12) {
                        Text("会话目录").font(.system(size: 11, weight: .medium))
                        Text(store.codexHome).font(.system(size: 10, design: .monospaced)).foregroundStyle(p.muted).textSelection(.enabled)
                        HStack { ActionButton(title: "选择目录", icon: "folder-open") { store.chooseHome() }.disabled(store.busy || store.agentRunning); Spacer(); ActionButton(title: "重新连接", icon: "refresh-cw") { store.reconnect() }.disabled(store.busy || store.agentRunning) }
                    }
                    Line()
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Agent 模型").font(.system(size: 11, weight: .medium))
                        TextField("留空使用 Codex 默认模型", text: $store.agentModel).font(.system(size: 11, design: .monospaced)).textFieldStyle(.plain).padding(10).background(p.soft, in: RoundedRectangle(cornerRadius: 6)).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(p.border))
                        Text("仅在你确认发送脱敏诊断后调用，不自动上传历史消息。").font(.system(size: 10)).foregroundStyle(p.muted).lineSpacing(4)
                    }
                    Line()
                    VStack(alignment: .leading, spacing: 8) { Text("本地引擎").font(.system(size: 11, weight: .medium)); Text("Python  ·  \(store.pythonPath)\nCodex   ·  \(store.codexPath)").font(.system(size: 9, design: .monospaced)).foregroundStyle(p.muted).textSelection(.enabled); Text(store.isConnected ? "已连接 · \(store.status?.codexVersion ?? "")" : "尚未连接").font(.system(size: 10)).foregroundStyle(store.isConnected ? p.accent : p.amber) }
                }.padding(29)
            }
            ModalFooter { Text("⌘ K 搜索 · ⌘ R 刷新 · Esc 关闭").font(.system(size: 9)).foregroundStyle(p.muted); Spacer(); ActionButton(title: "完成", primary: true) { store.showSettings = false } }
        }
    }
    private func theme(_ text: String, icon: String, key: String) -> some View {
        Button { store.appearance = key } label: { VStack(spacing: 11) { Icon(name: icon, size: 22); Text(text).font(.system(size: 11)) }.foregroundStyle(store.appearance == key ? p.text : p.muted).frame(maxWidth: .infinity).frame(height: 78).background(store.appearance == key ? p.selected : p.soft, in: RoundedRectangle(cornerRadius: 8)).overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(store.appearance == key ? p.accent : p.border)) }.buttonStyle(HoverButton()).accessibilityLabel(text + "外观")
    }
}
struct AgentConsentPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    @State private var consent = false
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 20) {
                ModalHeading(title: "调用 Agent 分析", icon: "terminal")
                Text("通过官方 Codex CLI 调用模型服务，可能消耗模型额度。请确认以下发送范围。").font(.system(size: 12)).foregroundStyle(p.secondary).lineSpacing(6)
                Text(store.detail?.title ?? "所选对话").font(.system(size: 12, weight: .medium)).padding(15).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 7))
                VStack(alignment: .leading, spacing: 12) {
                    Label("检查结果与错误代码", systemImage: "checkmark")
                    Label("允许修复的字段名", systemImage: "checkmark")
                    Label("不含原始对话、文件路径或密钥", systemImage: "xmark").foregroundStyle(p.muted)
                }.font(.system(size: 11)).foregroundStyle(p.secondary)
                Toggle("我同意将上述脱敏诊断发送给 Codex 模型服务。", isOn: $consent).toggleStyle(.checkbox).font(.system(size: 11)).padding(13).frame(maxWidth: .infinity, alignment: .leading).overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(p.border))
                Text("Agent 只生成建议。应用修复前仍需确认、备份并校验。").font(.system(size: 10)).foregroundStyle(p.muted)
            }.padding(29)
            ModalFooter { Spacer(); ActionButton(title: "暂不分析") { store.showAgentConsent = false }; ActionButton(title: "开始分析", icon: "terminal", primary: true) { Task { await store.startAgent() } }.disabled(!consent || store.agentRunning) }
        }
    }
}
struct RenamePanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    @State private var title = ""
    @FocusState private var focused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 23) { ModalHeading(title: "重命名对话", icon: "file-text"); TextField("对话标题", text: $title).textFieldStyle(.plain).font(.system(size: 12)).padding(12).background(Palette(scheme).soft, in: RoundedRectangle(cornerRadius: 6)).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Palette(scheme).border)).focused($focused); Text("仅修改标题，保留会话 ID 和历史内容。").font(.system(size: 10)).foregroundStyle(Palette(scheme).muted) }.padding(29)
            ModalFooter { Spacer(); ActionButton(title: "取消") { store.showRename = false }; ActionButton(title: "下一步", primary: true) { store.showRename = false; Task { await store.prepare("rename", title: title.trimmingCharacters(in: .whitespacesAndNewlines)) } }.disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || title.count > 200 || store.busy) }
        }.onAppear { title = store.detail?.title ?? ""; focused = true }
    }
}
struct MergePanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    @State private var title = "合并会话"
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            AdaptiveModalScroll {
                VStack(alignment: .leading, spacing: 19) {
                    ModalHeading(title: "合并会话", icon: "messages-square")
                    Text("合并数量、总大小和消息条数不设固定上限。保留文字、图片和音频，按下方顺序写入 Codex；完成后自动打开。来源原件保留，消息与附件完整核验。").font(.system(size: 11)).foregroundStyle(Palette(scheme).muted).lineSpacing(5)
                    TextField("新会话标题", text: $title).textFieldStyle(.roundedBorder)
                    Text("来源顺序 · 第一项决定新会话的工作目录").font(.system(size: 10)).foregroundStyle(Palette(scheme).muted)
                    ScrollView { LazyVStack(spacing: 8) {
                    ForEach(Array(store.mergeIDs.enumerated()), id: \.element) { index, id in
                        HStack(spacing: 12) {
                            Text(String(format: "%02d", index + 1)).font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette(scheme).muted)
                            Text(store.sessions.first { $0.id == id }?.title ?? id).font(.system(size: 12)).lineLimit(2)
                            Spacer()
                            Button { store.mergeIDs.swapAt(index, index-1) } label: { Image(systemName: "arrow.up") }.disabled(index == 0)
                            Button { store.mergeIDs.swapAt(index, index+1) } label: { Image(systemName: "arrow.down") }.disabled(index == store.mergeIDs.count-1)
                        }.buttonStyle(.plain).padding(12).background(Palette(scheme).soft, in: RoundedRectangle(cornerRadius: 7))
                    }
                    }}.frame(height: min(280, CGFloat(store.mergeIDs.count) * 56))
                }.padding(29)
            }
            ModalFooter { Spacer(); ActionButton(title: "取消") { store.closeModal() }; ActionButton(title: store.busy ? "正在预检…" : "预检合并", primary: true) { Task { await store.prepareMerge(title: title) } }.disabled(store.busy || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || title.count > 200) }
        }
    }
}
struct SuccessPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let result: ActionResult
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            AdaptiveModalScroll {
                VStack(alignment: .leading, spacing: 20) {
                    ModalHeading(title: result.action == "desktop_sync" ? "Codex 同步结果" : result.ok ? "操作完成" : "批量处理结果", icon: result.ok ? "circle-check" : "circle-alert")
                    Text(result.action == "project_delete" ? (result.ok ? "项目与会话已删除 · 源码保留" : "项目删除未完成，请检查记录") : result.action == "desktop_sync" ? "已核验同步 \(result.affected) 段对话" : "已验证处理 \(result.affected) 段对话").font(.system(size: 13, weight: .medium))
                    Text(result.verification).font(.system(size: 11)).foregroundStyle(Palette(scheme).secondary).lineSpacing(5)
                    if let sync = result.desktopSync {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(sync.message).font(.system(size: 11)).foregroundStyle(sync.needsRetry ? Palette(scheme).amber : Palette(scheme).secondary).lineSpacing(4)
                            if sync.needsRetry { ActionButton(title: store.busy ? "正在同步…" : result.action == "merge" ? "在 Codex 打开并核验" : "仅重试同步", icon: "refresh-cw") { Task { if result.action == "merge" { await store.openAndVerifyMerge(result) } else { await store.syncDesktop() } } }.disabled(store.busy) }
                        }.padding(14).frame(maxWidth: .infinity, alignment: .leading).background(Palette(scheme).soft, in: RoundedRectangle(cornerRadius: 7))
                    }
                    if let outcomes = result.outcomes, !outcomes.isEmpty {
                    ScrollView { LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(outcomes) { item in
                        VStack(alignment: .leading, spacing: 7) {
                            HStack { Text(item.title.isEmpty ? "未命名会话 · " + String(item.id.prefix(8)) : item.title).lineLimit(2); Spacer(); Text(item.statusText).foregroundStyle(item.status == "verified" ? Palette(scheme).accent : Palette(scheme).amber) }.font(.system(size: 11, weight: .medium))
                            Text(item.detail).font(.system(size: 10)).foregroundStyle(Palette(scheme).muted).textSelection(.enabled)
                            if let sync = item.desktopSync { Text(sync.message).font(.system(size: 10)).foregroundStyle(sync.needsRetry ? Palette(scheme).amber : Palette(scheme).secondary) }
                        }.padding(12).background(Palette(scheme).soft, in: RoundedRectangle(cornerRadius: 7))
                    }
                    }}.frame(height: min(260, CGFloat(outcomes.count) * 110))
                    }
                    if let id = result.newId { ActionButton(title: "在 Codex 打开合并会话", icon: "external-link") { openCodex(id) } }
                    Text("备份与记录\n" + result.backup).font(.system(size: 10, design: .monospaced)).textSelection(.enabled).foregroundStyle(Palette(scheme).muted)
                }.padding(29)
            }
            ModalFooter { ActionButton(title: "查看备份", icon: "folder-open") { store.revealBackup(result.backup) }; Spacer(); ActionButton(title: "操作记录") { store.lastResult = nil; Task { await store.loadOperations() } }; ActionButton(title: "完成", primary: true) { store.lastResult = nil } }
        }
    }
}
struct ErrorPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let message: String
    private var externalHistory: Bool { store.detail?.storageRestricted == true && message.contains("归位历史") }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 22) { ModalHeading(title: externalHistory ? "历史位于外置存储" : "操作未完成", icon: externalHistory ? "hard-drive" : "circle-alert"); ScrollView { Text(message).font(.system(size: 12)).foregroundStyle(Palette(scheme).secondary).lineSpacing(6).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 210) }.padding(29)
            ModalFooter {
                if externalHistory { ActionButton(title: "查看归位方案", icon: "hard-drive") { store.error = nil; Task { await store.prepare("relocate") } } }
                Spacer(); ActionButton(title: "知道了", primary: true) { store.error = nil }
            }
        }
    }
}
struct OperationsView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("操作记录").font(.system(size: 25, weight: .semibold))
                Spacer()
                ActionButton(title: store.busy ? "正在同步…" : "同步 Codex", icon: "refresh-cw") { Task { await store.syncDesktop() } }.disabled(store.busy || !store.isConnected)
            }.padding(.top, 32).padding(.bottom, 12)
            Text("本机维护结果与原始备份。").font(.system(size: 11)).foregroundStyle(p.muted).padding(.bottom, 27)
            if store.operations.isEmpty { EmptyPanel(icon: "history", title: "还没有操作记录", description: "完成修复、归档或删除后，结果会出现在这里。") }
            else {
                AdaptiveModalScroll {
                    VStack(spacing: 0) {
                        ForEach(store.operations) { item in
                            HStack(alignment: .top, spacing: 13) {
                                Icon(name: item.status == "verified" ? "circle-check" : "circle-alert", size: 18).foregroundStyle(item.status == "verified" ? p.accent : p.amber)
                                VStack(alignment: .leading, spacing: 7) { Text(Action.title(item.action)).font(.system(size: 13, weight: .medium)); Text(item.statusText).font(.system(size: 10)).foregroundStyle(p.muted); if let sync = item.desktopSync { Text(sync.message).font(.system(size: 10)).foregroundStyle(sync.needsRetry ? p.amber : p.secondary) }; if let error = item.error { Text(error).font(.system(size: 10)).foregroundStyle(p.amber).textSelection(.enabled) }; Text(item.createdAt).font(.system(size: 9, design: .monospaced)).foregroundStyle(p.muted) }
                                Spacer(); ActionButton(title: "查看备份") { store.revealBackup(item.backup) }
                            }.padding(.vertical, 19).overlay(alignment: .bottom) { Line() }
                        }
                    }
                }
            }
            Text("备份含私人对话，请谨慎保管。操作日志不代表远端模型往返已验证。").font(.system(size: 9)).foregroundStyle(p.muted).padding(.vertical, 20)
        }.padding(.horizontal, 33)
    }
}

private struct ModalHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}
/// Scroll only when the dialog content exceeds the window's compact-height budget.
struct AdaptiveModalScroll<Content: View>: View {
    var maxHeight: CGFloat = 560
    @ViewBuilder var content: () -> Content
    @State private var measuredHeight: CGFloat = 560
    var body: some View {
        ScrollView {
            content().background(GeometryReader { proxy in
                Color.clear.preference(key: ModalHeightKey.self, value: proxy.size.height)
            })
        }.frame(height: min(maxHeight, max(1, measuredHeight)))
            .onPreferenceChange(ModalHeightKey.self) { measuredHeight = ceil($0) }
    }
}

struct BatchProgressPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let job: BatchJob
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 20) {
                ModalHeading(title: job.title, icon: "list-filter")
                VStack(alignment: .leading, spacing: 9) {
                    HStack {
                        Text(job.mode == "plan" ? "预检进度" : "会话组进度")
                        Spacer()
                        Text("\(job.completed) / \(job.total)").monospacedDigit()
                    }.font(.system(size: 11)).foregroundStyle(p.secondary)
                    if job.total > 0 { ProgressView(value: Double(job.completed), total: Double(job.total)) }
                    else { ProgressView().controlSize(.small) }
                }
                VStack(alignment: .leading, spacing: 12) {
                    if let title = job.currentTitle {
                        HStack(alignment: .firstTextBaseline) {
                            Text(job.currentIndex.map { "第 \($0) 组" } ?? "当前会话").foregroundStyle(p.muted)
                            Text(title).lineLimit(2).foregroundStyle(p.text)
                        }.font(.system(size: 12, weight: .medium))
                    }
                    HStack(spacing: 9) {
                        ProgressView().controlSize(.mini)
                        Text(job.message ?? "正在准备").font(.system(size: 11)).foregroundStyle(p.secondary)
                    }
                    if job.phase == "backup", let done = job.bytesDone, let total = job.bytesTotal, total > 0 {
                        VStack(alignment: .leading, spacing: 7) {
                            ProgressView(value: Double(done), total: Double(total))
                            Text("\(ByteCountFormatter.string(fromByteCount: done, countStyle: .file)) / \(ByteCountFormatter.string(fromByteCount: total, countStyle: .file))")
                                .font(.system(size: 10)).monospacedDigit().foregroundStyle(p.muted)
                        }
                    }
                    if job.phase == "official_rpc", job.diagnosticsAvailable == true {
                        Text("调用时间线与引擎日志保存在本项备份目录").font(.system(size: 10)).foregroundStyle(p.muted)
                    }
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        HStack {
                            Text("本步 \(BatchJob.elapsedLabel(since: job.phaseStartedAt, now: context.date))")
                            Spacer()
                            Text("总计 \(BatchJob.elapsedLabel(since: job.startedAt, now: context.date))")
                        }.font(.system(size: 10)).monospacedDigit().foregroundStyle(p.muted)
                    }
                }.padding(15).frame(maxWidth: .infinity, alignment: .leading)
                    .background(p.soft, in: RoundedRectangle(cornerRadius: 8))
                Text(explanation).font(.system(size: 11)).foregroundStyle(p.muted).lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }.padding(29)
            if job.mode == "execute" {
                ModalFooter {
                    Text("已完成的操作不会重复执行").font(.system(size: 10)).foregroundStyle(p.muted)
                    Spacer()
                    ActionButton(title: job.stopRequested == true ? "正在等待当前项结束" : "完成当前项后停止") {
                        Task { await store.stopBatchAfterCurrent() }
                    }.disabled(job.stopRequested == true)
                }
            }
        }
    }
    private var explanation: String {
        if let notice = store.batchConnectionNotice { return notice }
        if job.stopRequested == true { return "已请求停止。当前项仍会完成备份、执行与验证，后续会话保持不动。" }
        if job.phase == "official_rpc" {
            return "Codex 正在核对历史引用与写入锁，删除最长等待 10 分钟。长等待会保存引擎日志和进程采样；超时后核对实际状态，不自动重试。"
        }
        if (job.scopeCount ?? 0) > 1 { return "当前组包含 \(job.scopeCount!) 条关联会话，全部验证结束后计为一组完成。" }
        return "按备份、官方操作、结果验证依次执行；进度只显示实际完成的工作。"
    }
}
