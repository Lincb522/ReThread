import SwiftUI
import AppKit

struct ProjectAssignmentPanel: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    @State private var projectID = ""
    @State private var name = ""
    @State private var root = ""
    @State private var search = ""
    private var p: Palette { Palette(scheme) }
    private var creating: Bool { store.createProjectFromConversation }
    private var candidates: [ProjectDescriptor] {
        store.projectCatalog.filter { search.isEmpty || $0.name.localizedCaseInsensitiveContains(search) || $0.roots.contains { $0.localizedCaseInsensitiveContains(search) } }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            AdaptiveModalScroll(maxHeight: 550) {
            VStack(alignment: .leading, spacing: 20) {
                ModalHeading(title: creating ? "基于对话新建项目" : "移动到项目", icon: "folder")
                Picker("项目操作", selection: $store.createProjectFromConversation) {
                    Text("现有项目").tag(false)
                    Text("新建项目").tag(true)
                }.pickerStyle(.segmented).labelsHidden()
                Text("已选 \(store.projectAssignmentIDs.count) 段对话 · 保留 ID、历史与工作目录")
                    .font(.system(size: 11)).foregroundStyle(p.secondary)
                if creating {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("项目名称").font(.system(size: 11, weight: .medium))
                        TextField("输入项目名称", text: $name).textFieldStyle(.roundedBorder)
                            .accessibilityIdentifier("project-name")
                        Text("项目文件夹").font(.system(size: 11, weight: .medium)).padding(.top, 8)
                        HStack(spacing: 10) {
                            TextField("选择已有文件夹", text: $root).textFieldStyle(.roundedBorder)
                                .accessibilityIdentifier("project-root")
                            ActionButton(title: "选择…", icon: "folder-open") { chooseFolder() }
                        }
                        Text("默认使用这段对话的工作目录；也可选择其他已有文件夹。仅登记项目，不搬动文件。")
                            .font(.system(size: 10)).foregroundStyle(p.muted).lineSpacing(4)
                    }.padding(16).background(p.soft, in: RoundedRectangle(cornerRadius: 8))
                } else {
                    TextField("搜索项目名称或路径", text: $search).textFieldStyle(.roundedBorder)
                    ScrollView {
                        LazyVStack(spacing: 5) {
                            ForEach(candidates) { project in
                                Button { projectID = project.id } label: {
                                    HStack(spacing: 12) {
                                        Image(systemName: projectID == project.id ? "checkmark.circle.fill" : "circle")
                                            .foregroundStyle(projectID == project.id ? p.accent : p.muted)
                                        VStack(alignment: .leading, spacing: 5) {
                                            Text(project.name).font(.system(size: 12, weight: .medium)).foregroundStyle(p.text)
                                            Text(project.roots.joined(separator: " · ")).font(.system(size: 10)).foregroundStyle(p.muted).lineLimit(2)
                                        }
                                        Spacer(minLength: 0)
                                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                                        .background(projectID == project.id ? p.hover : p.soft, in: RoundedRectangle(cornerRadius: 7))
                                        .contentShape(Rectangle())
                                }.buttonStyle(.plain).accessibilityIdentifier("choose-project-\(project.id)")
                            }
                            if candidates.isEmpty { Text("没有匹配的项目").font(.system(size: 12)).foregroundStyle(p.muted).padding(24) }
                        }
                    }.frame(height: 225)
                }
                Text("同步官方项目归属与 Codex 桌面登记。执行时需先退出 Codex，完成后重新打开即可查看；不只是续言中的本地分组。")
                    .font(.system(size: 11)).foregroundStyle(p.secondary).lineSpacing(5).fixedSize(horizontal: false, vertical: true)
            }.padding(29)
            }
            ModalFooter {
                Spacer()
                ActionButton(title: "取消") { store.closeModal() }.disabled(store.busy)
                ActionButton(title: store.busy ? "正在预检…" : "预检并同步", primary: true) {
                    Task { await store.prepareProjectAssignment(projectID: creating ? nil : projectID, name: name, root: root) }
                }.disabled(store.busy || (creating ? name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 200 || root.isEmpty : projectID.isEmpty))
            }
        }.onAppear {
            if let item = store.sessions.first(where: { store.projectAssignmentIDs.first == $0.id }) {
                root = item.cwd
                name = String(item.title.prefix(200))
                if name.isEmpty { name = "新项目" }
            }
        }
    }
    private func chooseFolder() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.allowsMultipleSelection = false; panel.canCreateDirectories = false; panel.prompt = "选择项目文件夹"
        if !root.isEmpty { panel.directoryURL = URL(fileURLWithPath: root) }
        if panel.runModal() == .OK, let url = panel.url { root = url.path }
    }
}
