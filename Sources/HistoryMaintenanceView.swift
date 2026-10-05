import SwiftUI

struct HistoryActions: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        let p = Palette(scheme)
        VStack(alignment: .leading, spacing: 10) {
            Text("存储与索引").font(.system(size: 10)).foregroundStyle(p.muted)
            HStack(spacing: 9) {
                ActionButton(title: "会话迁移", icon: "hard-drive") { store.openHistoryTool("migrate") }
                ActionButton(title: store.diagnosing ? "正在检查…" : "索引修复", icon: "wrench") { Task { await store.repairIndex() } }
                ActionButton(title: "索引链接", icon: "link") { store.openHistoryTool("link") }
            }.disabled(store.busy || store.diagnosing)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct HistoryPathPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    private var migration: Bool { store.historyAction == "migrate" }
    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ModalHeading(title: migration ? "会话迁移" : "索引链接", icon: migration ? "hard-drive" : "link")
                    Text(migration ? "选择新的历史存储目录，预检后确认迁移。只处理此会话，不移动项目文件。" : "选择这段会话已有的 JSONL 历史文件。仅修改索引引用，不搬动文件，也不创建软链接。")
                        .font(.system(size: 12)).foregroundStyle(p.secondary).lineSpacing(5)
                    VStack(alignment: .leading, spacing: 8) {
                        Text(store.detail?.title ?? "所选会话").font(.system(size: 12, weight: .medium)).lineLimit(2)
                        Text("当前索引").font(.system(size: 10)).foregroundStyle(p.muted)
                        Text(store.detail?.localPath ?? "").font(.system(size: 10, design: .monospaced)).foregroundStyle(p.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }.padding(14).frame(maxWidth: .infinity, alignment: .leading).background(p.soft, in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text(migration ? "目标目录" : "历史文件").font(.system(size: 11, weight: .medium))
                            Spacer()
                            ActionButton(title: "选择…", icon: "folder-open", height: 28) { store.chooseHistoryTarget() }.disabled(store.busy)
                        }
                        TextField(migration ? "选择或输入完整目录路径" : "选择或输入 rollout-…jsonl 路径", text: $store.historyTarget, axis: .vertical)
                            .font(.system(size: 11, design: .monospaced)).textFieldStyle(.plain).lineLimit(2...4).padding(12)
                            .background(p.soft, in: RoundedRectangle(cornerRadius: 6)).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(p.border)).disabled(store.busy)
                    }
                    if migration {
                        Toggle("保留来源副本", isOn: $store.keepHistorySource).font(.system(size: 11)).toggleStyle(.checkbox).disabled(store.busy)
                        Text(store.keepHistorySource ? "默认保留原件。目标文件校验通过后，后续读取使用新索引。" : "目标文件与索引均验证后，再移除来源文件；独立备份仍保留。分叉共享历史要求保留来源。")
                            .font(.system(size: 10)).foregroundStyle(p.muted).lineSpacing(4)
                    } else {
                        Text("文件名和首条元数据必须对应当前会话 ID；已有历史与所选副本内容不同时，预检会停止。").font(.system(size: 10)).foregroundStyle(p.muted).lineSpacing(4)
                    }
                    HStack(alignment: .top, spacing: 8) {
                        Icon(name: "shield-check", size: 14)
                        Text("预检只读。实际执行前请结束任务并退出 Codex / ChatGPT 及 CLI，避免同时写入索引。")
                            .font(.system(size: 10)).lineSpacing(4)
                    }.foregroundStyle(p.muted)
                    if store.busy { ProgressView("正在核对文件、索引与存储状态…").font(.system(size: 11)) }
                }.padding(28)
            }.frame(maxHeight: 580)
            ModalFooter {
                Text("确认前不修改会话").font(.system(size: 9)).foregroundStyle(p.muted)
                Spacer()
                ActionButton(title: "取消") { store.closeModal() }.disabled(store.busy)
                ActionButton(title: store.busy ? "正在预检…" : "预检方案", primary: true) { Task { await store.prepareHistory() } }
                    .disabled(store.busy || store.historyTarget.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }
}
