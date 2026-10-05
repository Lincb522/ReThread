import SwiftUI
import MarkdownUI

struct InspectorView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(spacing: 0) {
            toolbar
            if store.showOperations { OperationsView() }
            else if store.loadingDetail { EmptyPanel(icon: "loader-circle", title: "正在读取对话", description: "读取本地历史文件与索引。") }
            else if let item = store.detail {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        intro(item)
                        HistoryActions().padding(.horizontal, 33).padding(.top, 18)
                        if item.storageRestricted == true {
                            VStack(alignment: .leading, spacing: 10) {
                                Label("历史在外置存储", systemImage: "externaldrive").font(.system(size: 12, weight: .medium))
                                Text("这不是内容损坏。删除可直接备份后执行，无需归位；归档前仍需归位。")
                                    .font(.system(size: 11)).foregroundStyle(p.muted).lineSpacing(4)
                                HStack(spacing: 10) {
                                    ActionButton(title: "删除…", icon: "trash-2", destructive: true) { Task { await store.prepare("delete") } }.disabled(store.busy)
                                    ActionButton(title: "归位历史…", icon: "hard-drive") { Task { await store.prepare("relocate") } }.disabled(store.busy)
                                }
                            }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 8)).padding(.horizontal, 33).padding(.top, 20)
                        }
                        Group {
                            if store.detailTab == "info" { ConversationInfo(item: item) }
                            else if store.detailTab == "diagnosis" { DiagnosisPanel() }
                            else { ConversationPreview(item: item) }
                        }.padding(.horizontal, 33).padding(.top, 24).padding(.bottom, 32)
                    }
                }
                HStack(spacing: 7) {
                    Icon(name: "shield-check", size: 12); Text("只读预览 · 历史内容保持原样").font(.system(size: 9)); Spacer(minLength: 1)
                    if store.detailTab == "diagnosis", store.repairMode == "local", let d = store.diagnosis, !d.changes.isEmpty {
                        ActionButton(title: "查看修复方案", icon: "wrench", primary: true) { Task { await store.prepare("repair") } }.disabled(store.busy)
                    } else if let bytes = item.fileBytes { Text(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)).font(.system(size: 9)) }
                }.foregroundStyle(p.muted).padding(.horizontal, 24).frame(height: 52).overlay(alignment: .top) { Line() }
            } else { EmptyPanel(icon: "messages-square", title: "未读取到对话", description: "返回列表重新选择，或查看错误信息。") }
        }.accessibilityElement(children: .contain).accessibilityLabel("对话详情")
    }
    private var toolbar: some View {
        HStack(spacing: 11) {
            ToolButton(icon: "x", title: "关闭对话详情") { store.closeInspector() }
            Icon(name: store.showOperations ? "history" : "folder", size: 13)
            Text(store.showOperations ? "工作区" : store.detail?.project ?? "对话").lineLimit(1)
            Icon(name: "chevron-right", size: 10)
            Text(store.showOperations ? "操作记录" : "对话详情")
            Spacer(minLength: 0)
            if let item = store.detail, !store.showOperations {
                ToolButton(icon: item.isArchived ? "undo-2" : "archive", title: item.isArchived ? "取消归档" : "归档对话") { Task { await store.prepare(item.isArchived ? "unarchive" : "archive") } }.disabled(store.busy)
                ToolButton(icon: "file-text", title: "会话信息") { store.detailTab = "info" }
                Menu {
                    Button("在 Codex 中打开") { openCodex(item.id) }
                Button("移动到项目…") { store.openProjectAssignment([item.id]) }
                Button("基于此对话新建项目…") { store.openProjectAssignment([item.id], create: true) }
                    Button("复制会话 ID") { copyText(item.id) }
                    Button("重命名对话") { store.showRename = true }
                    Divider()
                    Button("会话迁移…") { store.openHistoryTool("migrate") }
                    Button("索引修复…") { Task { await store.repairIndex() } }
                    Button("索引链接…") { store.openHistoryTool("link") }
                    Divider()
                    Button("删除对话…", role: .destructive) { Task { await store.prepare("delete") } }
                } label: { Icon(name: "more-horizontal", size: 17).frame(width: 25, height: 28) }.menuStyle(.borderlessButton).fixedSize().help("更多操作").disabled(store.busy)
            }
        }.font(.system(size: 10)).foregroundStyle(p.muted).padding(.horizontal, 20).frame(height: 51).overlay(alignment: .bottom) { Line() }
    }
    private func intro(_ item: Conversation) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(item.project).font(.system(size: 8, design: .monospaced)).tracking(1.6).foregroundStyle(p.muted).padding(.bottom, 14)
            HStack(alignment: .top, spacing: 12) {
                Text(item.title).font(.system(size: 23, weight: .semibold)).tracking(-0.7).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                Spacer(minLength: 0)
                ToolButton(icon: "pin", title: store.pins.contains(item.id) ? "取消置顶" : "置顶对话") { store.togglePin(item.id) }.foregroundStyle(store.pins.contains(item.id) ? p.accent : p.muted)
            }
            HStack(spacing: 9) { Icon(name: "clock", size: 12); Text(item.updated.formatted(.dateTime.year().month().day())); Text("/"); if let n = item.messageCount { Text("\(n) 条预览消息"); Text("/") }; if let m = item.model, !m.isEmpty { CapsuleLabel(text: m) }; CapsuleLabel(text: "本地") }.font(.system(size: 10)).foregroundStyle(p.muted).padding(.top, 15)
            HStack(spacing: 26) { tab("对话预览", key: "preview"); tab("检查与维护", key: "diagnosis"); tab("会话信息", key: "info"); Spacer(minLength: 0) }.padding(.top, 25).overlay(alignment: .bottom) { Line() }
        }.padding(.horizontal, 33).padding(.top, 29)
    }
    private func tab(_ title: String, key: String) -> some View {
        Button {
            store.detailTab = key
            if key == "diagnosis", store.diagnosis == nil { Task { await store.diagnose() } }
        } label: { Text(title).font(.system(size: 11, weight: store.detailTab == key ? .medium : .regular)).foregroundStyle(store.detailTab == key ? p.text : p.muted).padding(.bottom, 14).overlay(alignment: .bottom) { Rectangle().fill(store.detailTab == key ? p.accent : .clear).frame(height: 2) } }.buttonStyle(.plain).accessibilityIdentifier("tab-\(key)")
    }
}
struct ConversationPreview: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let item: Conversation
    private var p: Palette { Palette(scheme) }
    var body: some View {
        LazyVStack(alignment: .leading, spacing: 25) {
            if item.localPathExists == false {
                HStack(alignment: .top, spacing: 11) { Icon(name: "circle-alert", size: 17); VStack(alignment: .leading, spacing: 6) { Text("历史路径引用失效").font(.system(size: 12, weight: .medium)); Text("索引路径不存在，需检查原始历史位置。").font(.system(size: 10)) }; Spacer(); Button("查看诊断") { store.detailTab = "diagnosis"; Task { await store.diagnose() } }.buttonStyle(.plain).font(.system(size: 10)) }.foregroundStyle(p.amber).padding(15).background(p.amberSoft, in: RoundedRectangle(cornerRadius: 7))
            }
            if item.isArchived { Label("对话已归档", systemImage: "archivebox").font(.system(size: 11)).foregroundStyle(p.secondary) }
            Text(item.previewLimited == true ? "最近 80 条以内 · 非完整历史" : "本地历史消息").font(.system(size: 9)).foregroundStyle(p.muted).frame(maxWidth: .infinity)
            if let errors = item.previewErrors, errors > 0 { Text("预览中发现 \(errors) 条无效记录，请查看修复诊断。").font(.system(size: 11)).foregroundStyle(p.amber) }
            if (item.messages ?? []).isEmpty { EmptyPanel(icon: "file-text", title: "暂无可显示的消息", description: item.localPathExists == false ? "先检查历史文件路径。" : "当前读取范围内没有用户或助手消息。").frame(minHeight: 170) }
            ForEach(Array((item.messages ?? []).enumerated()), id: \.offset) { _, message in NativeMessageView(message: message) }
        }
    }
}
struct NativeMessageView: View {
    @Environment(\.colorScheme) private var scheme
    let message: ChatMessage
    @State private var expanded = false
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 9) {
                Group { if message.role == "user" { Text("U").font(.system(size: 11, weight: .medium)) } else { Icon(name: "terminal", size: 14) } }.frame(width: 26, height: 26).background(p.soft, in: RoundedRectangle(cornerRadius: 7)).overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(p.border))
                Text(message.role == "user" ? "你" : "Codex").font(.system(size: 11, weight: .medium))
                if message.kind == "progress" { CapsuleLabel(text: "进度") }
                Spacer()
                if let date = Conversation.parseDate(message.timestamp) { Text(date.formatted(.dateTime.hour().minute())).font(.system(size: 9)).foregroundStyle(p.muted) }
                ToolButton(icon: "copy", title: "复制消息") { copyText(message.text) }
            }
            Markdown(expanded ? message.text : String(message.text.prefix(6000)))
                .markdownTextStyle(\.text) { FontSize(12); ForegroundColor(p.secondary); BackgroundColor(.clear) }
                .markdownTextStyle(\.code) { FontFamilyVariant(.monospaced); FontSize(11); ForegroundColor(p.text); BackgroundColor(p.soft) }
                .markdownBlockStyle(\.codeBlock) { configuration in
                    VStack(alignment: .leading, spacing: 0) {
                        HStack { Icon(name: "file-code-2", size: 12); Text(configuration.language ?? "代码").font(.system(size: 9, design: .monospaced)); Spacer(); Button("复制") { copyText(configuration.content) }.font(.system(size: 9)).buttonStyle(.plain) }.foregroundStyle(p.muted).padding(11)
                        Line()
                        ScrollView(.horizontal) { configuration.label.markdownTextStyle { FontFamilyVariant(.monospaced); FontSize(11); ForegroundColor(p.secondary); BackgroundColor(.clear) }.fixedSize(horizontal: true, vertical: false).padding(14) }
                    }.background(p.soft, in: RoundedRectangle(cornerRadius: 7)).overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(p.border)).markdownMargin(top: 12, bottom: 12)
                }
                .markdownTheme(.gitHub)
                .markdownImageProvider(NoRemoteImages())
                .textSelection(.enabled)
                .environment(\.openURL, OpenURLAction { url in
                    guard ["https", "http"].contains(url.scheme?.lowercased() ?? "") else { return .discarded }
                    return .systemAction
                })
            if message.text.count > 6000 { Button(expanded ? "收起消息" : "展开完整消息") { expanded.toggle() }.font(.system(size: 10)).buttonStyle(.plain).foregroundStyle(p.accent) }
        }.padding(.bottom, 8)
    }
}
struct NoRemoteImages: ImageProvider {
    func makeImage(url: URL?) -> some View {
        Label("消息图片未自动加载", systemImage: "photo").font(.system(size: 10)).foregroundStyle(.secondary).padding(8)
    }
}
struct ConversationInfo: View {
    @Environment(\.colorScheme) private var scheme
    let item: Conversation
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            row("会话 ID", item.id, mono: true)
            row("所属项目", item.project)
            row("工作目录", item.cwd, mono: true)
            row("历史文件", item.localPath, mono: true)
            row("预览消息", item.messageCount.map { "\($0) 条（读取范围内）" } ?? "未读取")
            row("文件大小", item.fileBytes.map { ByteCountFormatter.string(fromByteCount: $0, countStyle: .file) } ?? "未读取")
            row("归档状态", item.isArchived ? "已归档" : "未归档")
            row("历史模式", item.historyMode)
            row("数据来源", "本机 Codex 索引")
            ActionButton(title: "在 Codex 中打开", icon: "external-link") { openCodex(item.id) }.padding(.top, 23)
        }
    }
    private func row(_ key: String, _ value: String, mono: Bool = false) -> some View { HStack(alignment: .top, spacing: 16) { Text(key).font(.system(size: 10)).foregroundStyle(p.muted).frame(width: 78, alignment: .leading); Text(value).font(.system(size: 11, design: mono ? .monospaced : .default)).foregroundStyle(p.secondary).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.padding(.vertical, 16).overlay(alignment: .bottom) { Line() } }
}
struct DiagnosisPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 26) {
            HStack(spacing: 3) { mode("本地检查", icon: "scan-line", key: "local"); mode("Agent 分析", icon: "terminal", key: "agent") }.padding(3).background(p.soft, in: RoundedRectangle(cornerRadius: 7)).overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(p.border))
            if store.repairMode == "agent" { AgentPanel() }
            else if store.diagnosing { HStack(spacing: 12) { ProgressView().controlSize(.small); Text("正在检查历史结构与索引").font(.system(size: 12)) }.padding(.vertical, 24) }
            else if let d = store.diagnosis { DiagnosisView(diagnosis: d) }
            else { EmptyPanel(icon: "scan-line", title: "尚未检查", description: "读取历史结构、会话身份和路径引用。"); ActionButton(title: "开始检查", icon: "scan-line", primary: true) { Task { await store.diagnose() } } }
        }
    }
    private func mode(_ name: String, icon: String, key: String) -> some View {
        Button { store.repairMode = key; if key == "local", store.diagnosis == nil { Task { await store.diagnose() } } } label: { HStack(spacing: 7) { Icon(name: icon, size: 13); Text(name); if key == "agent" { Text("AI").font(.system(size: 7, design: .monospaced)).padding(2).overlay(RoundedRectangle(cornerRadius: 2).strokeBorder(p.border)) } }.font(.system(size: 10)).frame(maxWidth: .infinity).frame(height: 29).background(store.repairMode == key ? p.surface : .clear, in: RoundedRectangle(cornerRadius: 5)).foregroundStyle(store.repairMode == key ? p.text : p.muted) }.buttonStyle(HoverButton())
    }
}
struct DiagnosisView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let diagnosis: Diagnosis
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 14) {
                Icon(name: diagnosis.changes.isEmpty ? "shield-check" : "wrench", size: 21).foregroundStyle(p.accent).frame(width: 43, height: 43).background(p.soft, in: RoundedRectangle(cornerRadius: 11)).overlay(RoundedRectangle(cornerRadius: 11).strokeBorder(p.border))
                VStack(alignment: .leading, spacing: 7) {
                    Text(diagnosis.statusTitle).font(.system(size: 16, weight: .medium))
                    Text("\(diagnosis.issues.count) 项检查结果 · \(diagnosis.scan.lines) 行记录").font(.system(size: 10)).foregroundStyle(p.muted)
                }
                Spacer(minLength: 0)
            }.padding(.bottom, 30)
            HStack { Text("检查结果").font(.system(size: 11, weight: .medium)); Spacer(); Text("只读检查").font(.system(size: 9)).foregroundStyle(p.muted) }.padding(.bottom, 10)
            ForEach(diagnosis.issues) { issue in
                HStack(alignment: .top, spacing: 13) {
                    Icon(name: issue.level == "ok" ? "circle-check" : "circle-alert", size: 17).foregroundStyle(issue.level == "ok" ? p.accent : p.amber)
                    VStack(alignment: .leading, spacing: 6) { Text(issueTitle(issue.code)).font(.system(size: 12, weight: .medium)); Text(issue.text).font(.system(size: 11)).foregroundStyle(p.muted).fixedSize(horizontal: false, vertical: true).lineSpacing(4) }
                    Spacer(minLength: 1)
                    Text(issue.level == "ok" ? "通过" : issue.level == "repair" ? "可修复" : issue.level == "info" ? "信息" : issue.level == "warning" ? "待核实" : "异常").font(.system(size: 9)).foregroundStyle(issue.level == "ok" ? p.muted : p.amber)
                }.padding(.vertical, 18).overlay(alignment: .bottom) { Line() }
            }
            if !diagnosis.changes.isEmpty {
                VStack(alignment: .leading, spacing: 13) {
                    ForEach(diagnosis.changes.keys.sorted(), id: \.self) { key in VStack(alignment: .leading, spacing: 6) { Text(key).font(.system(size: 9, design: .monospaced)).foregroundStyle(p.muted); Text(diagnosis.changes[key]?.text ?? "").font(.system(size: 10, design: .monospaced)).textSelection(.enabled).foregroundStyle(p.secondary) } }
                }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 7)).padding(.top, 22)
            }
            VStack(alignment: .leading, spacing: 9) { ForEach(diagnosis.boundaries, id: \.self) { Text($0).font(.system(size: 10)).foregroundStyle(p.muted).lineSpacing(4) } }.padding(.top, 22)
            ActionButton(title: "重新检查", icon: "refresh-cw") { Task { await store.diagnose() } }.padding(.top, 20).disabled(store.diagnosing)
        }
    }
    private func issueTitle(_ code: String) -> String { ["external_storage_path":"存储位置", "live_history":"历史正在更新", "missing_file":"历史路径引用", "stale_path":"原始历史位置", "history_valid":"历史结构检查", "hidden_thread":"列表可见性", "id_mismatch":"会话身份不一致", "invalid_jsonl":"历史记录格式", "missing_projection":"分页历史缺失", "projection_ahead":"分页投影偏移", "ambiguous":"多个历史候选", "archive_mismatch":"归档状态不一致", "oversize":"历史文件大小"][code] ?? code }
}
struct AgentPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(spacing: 11) { Icon(name: "terminal", size: 18).frame(width: 34, height: 34).background(p.soft, in: RoundedRectangle(cornerRadius: 9)).overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(p.border)); Text("CODEX AGENT").font(.system(size: 8, design: .monospaced)).tracking(1.4); Spacer(); CapsuleLabel(text: "按需调用") }
            Text("Agent 辅助修复").font(.system(size: 22, weight: .medium)).tracking(-0.5)
            Text("分析脱敏诊断，生成修复建议。\n确认方案后，再备份并执行。").font(.system(size: 12)).foregroundStyle(p.muted).lineSpacing(6)
            HStack(spacing: 17) { Label("只读分析", systemImage: "checkmark.shield"); Label("脱敏诊断", systemImage: "doc.text"); Label("执行前确认", systemImage: "checkmark") }.font(.system(size: 9)).foregroundStyle(p.secondary)
            VStack(spacing: 13) { fact("执行引擎", "官方 Codex CLI"); fact("发送内容", "检查项、错误类型、可修复字段"); fact("不发送", "原始消息、文件路径、登录凭证") }.padding(.vertical, 17).overlay(alignment: .top) { Line() }.overlay(alignment: .bottom) { Line() }
            if store.agentStarting { ProgressView("整理诊断证据…").font(.system(size: 11)) }
            if let job = store.agentJob {
                if job.threadId != store.selectedID { Text("以下为另一段对话的分析结果").font(.system(size: 10)).foregroundStyle(p.amber) }
                if job.isRunning {
                    HStack(spacing: 10) { ProgressView().controlSize(.small); Text(job.state == "cancelling" ? "正在取消分析" : "Agent 正在分析诊断结果").font(.system(size: 12)); Spacer(); Button("取消") { Task { await store.cancelAgent() } }.buttonStyle(.plain).font(.system(size: 11)) }
                } else if let report = job.report {
                    Text(report.summary).font(.system(size: 15, weight: .medium)).textSelection(.enabled)
                    Text(report.reason).font(.system(size: 11)).foregroundStyle(p.secondary).lineSpacing(5).textSelection(.enabled)
                    ForEach(report.risks, id: \.self) { risk in Label(risk, systemImage: "exclamationmark.circle").font(.system(size: 10)).foregroundStyle(p.amber) }
                    if job.canApply { ActionButton(title: "审阅修复方案", icon: "arrow-right", primary: true) { Task { await store.prepareAgentPlan() } }.disabled(store.busy) }
                    else { Text(report.needsManualReview ? "此结果需要人工审阅，未生成自动修复计划。" : "没有可应用的修复动作。").font(.system(size: 10)).foregroundStyle(p.muted) }
                } else { Text(job.error ?? (job.state == "cancelled" ? "分析已取消" : "分析未完成")).font(.system(size: 11)).foregroundStyle(p.amber).textSelection(.enabled) }
            }
            if !store.agentRunning { ActionButton(title: store.agentJob == nil ? "调用 Agent 分析" : "重新分析", icon: "terminal", primary: true) { store.showAgentConsent = true }.disabled(store.selectedID == nil) }
            Text("调用前需同意发送脱敏诊断，可能消耗模型额度。分析不会直接修改对话。").font(.system(size: 9)).foregroundStyle(p.muted).lineSpacing(5)
        }
    }
    private func fact(_ key: String, _ value: String) -> some View { HStack { Text(key).foregroundStyle(p.muted); Spacer(); Text(value).foregroundStyle(p.secondary) }.font(.system(size: 10)) }
}
