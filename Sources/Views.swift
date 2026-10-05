import SwiftUI
import AppKit

struct RootView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var systemReducedMotion
    private var reducedMotion: Bool { systemReducedMotion || ProcessInfo.processInfo.arguments.contains("--snapshot") }
    private var p: Palette { Palette(scheme) }
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .trailing) {
                HStack(spacing: 0) {
                    SidebarView().frame(width: geo.size.width < 1200 ? 224 : 240)
                    Rectangle().fill(p.line).frame(width: 1)
                    LibraryView()
                }.blur(radius: store.hasModal ? 5 : 0).disabled(store.inspectorOpen || store.hasModal).accessibilityHidden(store.inspectorOpen || store.hasModal)
                if store.inspectorOpen {
                    Color.black.opacity(scheme == .dark ? 0.35 : 0.12).onTapGesture { if !store.hasModal { store.closeInspector() } }
                    InspectorView().frame(width: min(650, max(520, geo.size.width * 0.6)))
                        .background(p.surface).overlay(alignment: .leading) { Rectangle().fill(p.border).frame(width: 1) }
                        .compositingGroup().shadow(color: .black.opacity(0.18), radius: 24, x: -10)
                        .transition(.move(edge: .trailing).combined(with: .opacity))
                        .blur(radius: store.hasModal ? 5 : 0).disabled(store.hasModal).accessibilityHidden(store.hasModal)
                }
                if let notice = store.notice, !store.hasModal {
                    VStack { Spacer(); HStack(spacing: 9) { Icon(name: "circle-check", size: 15); Text(notice).font(.system(size: 11)); ToolButton(icon: "x", title: "关闭提示") { store.notice = nil } }
                        .padding(.leading, 13).padding(.trailing, 4).padding(.vertical, 5).background(p.surface, in: RoundedRectangle(cornerRadius: 8)).overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(p.border)).shadow(color: .black.opacity(0.15), radius: 12).padding(.bottom, 24) }.frame(maxWidth: .infinity).allowsHitTesting(true)
                }
                if store.hasModal {
                    Color(hex: 0x07080a).opacity(0.533).ignoresSafeArea()
                    ModalHost().frame(maxWidth: .infinity, maxHeight: .infinity).transition(.opacity)
                }
            }.background(p.surface).foregroundStyle(p.text)
                .animation(reducedMotion ? nil : .easeOut(duration: 0.23), value: store.inspectorOpen)
                .animation(reducedMotion ? nil : .easeOut(duration: 0.16), value: store.hasModal)
        }
        .preferredColorScheme(store.appearance == "system" ? nil : store.appearance == "dark" ? .dark : .light)
        .onExitCommand { if store.hasModal { store.closeModal() } else { store.closeInspector() } }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in store.stop() }
        .task { AppDelegate.store = store; store.start() }
    }
}

struct SidebarView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    private var p: Palette { Palette(scheme) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            WindowDragRegion().frame(height: 51)
            HStack(spacing: 11) {
                AppLogo()
                VStack(alignment: .leading, spacing: 4) {
                    Text("续言").font(.system(size: 20, weight: .semibold)).foregroundStyle(p.text)
                    Text("R E T H R E A D").font(.system(size: 7, design: .monospaced)).tracking(1.6).foregroundStyle(p.muted)
                }
            }.padding(.horizontal, 21).padding(.top, 15).padding(.bottom, 20)
            // A single scroll surface keeps both groups reachable in short windows.
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    section("workspace", title: "工作空间") {
                        VStack(spacing: 3) {
                            nav("全部对话", icon: "inbox", scope: "all", count: store.sessions.count)
                            nav("独立对话", icon: "messages-square", scope: "projectless", count: store.independentCount)
                            nav("置顶对话", icon: "pin", scope: "pinned", count: store.pinnedCount)
                            nav("可修复", icon: "wrench", scope: "repair", count: store.attentionCount)
                            nav("旧会话", icon: "clock", scope: "old", count: store.sessions.filter { $0.updated < Date().addingTimeInterval(-90 * 86400) }.count)
                            nav("已归档", icon: "archive", scope: "archived", count: store.archivedCount)
                        }
                    }
                    section("projects", title: "项目", count: store.projects.count) {
                        LazyVStack(spacing: 3) {
                            ForEach(Array(store.projects.enumerated()), id: \.element.0) { index, entry in
                                Button { store.navigate("all", project: entry.0) } label: {
                                    HStack(alignment: .center, spacing: 10) {
                                        RoundedRectangle(cornerRadius: 1.5).fill(projectColor(index)).frame(width: 6, height: 6).padding(.horizontal, 5)
                                        Text(store.projectName(entry.0)).lineLimit(2).multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                                        Spacer(minLength: 3)
                                        Text("\(entry.1)").font(.system(size: 10, design: .monospaced)).foregroundStyle(p.muted)
                                    }.font(.system(size: 12)).padding(.horizontal, 12).padding(.vertical, 8).frame(minHeight: 36)
                                        .contentShape(Rectangle()).background(store.project == entry.0 ? p.selected : .clear, in: RoundedRectangle(cornerRadius: 6))
                                }.buttonStyle(HoverButton()).help(store.projectHelp(entry.0)).accessibilityIdentifier("project-\(entry.0)")
                                .contextMenu {
                                    Button("删除整个项目…", role: .destructive) { Task { await store.prepareProjectDelete(entry.0) } }
                                        .disabled(store.busy || !store.isConnected)
                                }
                            }
                            if store.projects.isEmpty {
                                Text(store.isConnected ? "Codex 中暂无已保存项目" : "等待项目连接")
                                    .font(.system(size: 10)).foregroundStyle(p.muted).frame(maxWidth: .infinity, alignment: .leading).padding(12)
                            }
                        }
                    }
                }.padding(.horizontal, 11).padding(.bottom, 12)
            }
            Line().padding(.horizontal, 22)
            section("health", title: "本地会话状态", summary: store.isConnected ? "\(store.diagnosisCache.count)/\(store.sessions.count)" : "—") {
                VStack(alignment: .leading, spacing: 9) {
                    HStack(spacing: 3) {
                        ForEach(0..<12) { i in Rectangle().fill(i < Int((Double(store.diagnosisCache.count) / Double(max(1, store.sessions.count))) * 12) ? p.accent.opacity(0.65) : p.border).frame(height: 2) }
                    }
                    Text(store.scanning ? "已检查 \(store.scanCompleted) / \(store.scanTotal)" : store.isConnected ? "\(store.attentionCount) 段待处理 · \(store.diagnosisCache.count) 段已检查" : "尚未连接")
                        .font(.system(size: 8)).foregroundStyle(p.muted)
                }.padding(.horizontal, 12).padding(.bottom, 9)
            }.padding(.horizontal, 11).padding(.top, 8)
            section("management", title: "管理") {
                VStack(spacing: 3) {
                    sidebarAction("操作记录", icon: "history") { Task { await store.loadOperations() } }
                    sidebarAction("偏好设置", icon: "settings-2", shortcut: "⌘ ,") { store.showSettings = true }
                    Line().padding(.top, 6)
                    Button { store.showAbout = true } label: {
                        HStack(spacing: 10) {
                            Text("Z").font(.system(size: 11, weight: .medium)).frame(width: 26, height: 26).background(p.soft, in: Circle()).overlay(Circle().strokeBorder(p.border))
                            VStack(alignment: .leading, spacing: 4) { Text("Zijiu522").font(.system(size: 10)); Text("关于续言").font(.system(size: 8)).foregroundStyle(p.muted) }
                            Spacer(); Icon(name: "chevron-right", size: 11).foregroundStyle(p.muted)
                        }.padding(.horizontal, 11).padding(.vertical, 12).contentShape(Rectangle())
                    }.buttonStyle(HoverButton()).accessibilityLabel("关于续言")
                }
            }.padding(.horizontal, 11).padding(.top, 4).padding(.bottom, 9)
        }.foregroundStyle(p.secondary).background(p.sidebar)
    }
    private func section<Content: View>(_ id: String, title: String, count: Int? = nil, summary: String? = nil, @ViewBuilder content: @escaping () -> Content) -> some View {
        DisclosureGroup(isExpanded: Binding(get: { store.sectionExpanded(id) }, set: { store.setSectionExpanded(id, $0) }), content: content) {
            HStack {
                Text(title)
                Spacer(minLength: 2)
                if let summary { Text(summary).font(.system(size: 9, design: .monospaced)) }
                else if let count { Text(store.isConnected ? "\(count)" : "—").font(.system(size: 9, design: .monospaced)) }
            }.font(.system(size: 10)).foregroundStyle(p.muted)
        }.disclosureGroupStyle(SidebarDisclosureStyle()).accessibilityIdentifier("sidebar-section-\(id)")
    }
    private func nav(_ text: String, icon: String, scope: String, count: Int) -> some View {
        let selected = store.scope == scope && store.project == nil
        return Button { store.navigate(scope) } label: {
            HStack(spacing: 11) { Icon(name: icon); Text(text); Spacer(); Text(store.isConnected ? "\(count)" : "—").font(.system(size: 10, design: .monospaced)).foregroundStyle(p.muted) }
                .font(.system(size: 12, weight: selected ? .medium : .regular)).padding(.horizontal, 12).frame(height: 36)
                .background(selected ? p.selected : .clear, in: RoundedRectangle(cornerRadius: 6)).contentShape(Rectangle()).foregroundStyle(selected ? p.text : p.secondary)
        }.buttonStyle(HoverButton()).accessibilityIdentifier("nav-\(scope)")
    }
    private func sidebarAction(_ text: String, icon: String, shortcut: String = "", action: @escaping () -> Void) -> some View {
        Button(action: action) { HStack(spacing: 11) { Icon(name: icon); Text(text); Spacer(); Text(shortcut).font(.system(size: 9, design: .monospaced)).foregroundStyle(p.muted) }.font(.system(size: 12)).padding(.horizontal, 12).frame(height: 32).contentShape(Rectangle()) }.buttonStyle(HoverButton())
    }
}
struct SidebarDisclosureStyle: DisclosureGroupStyle {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Button {
                withAnimation(reduceMotion ? nil : .spring(response: 0.25, dampingFraction: 1)) { configuration.isExpanded.toggle() }
            } label: {
                HStack(spacing: 7) {
                    Icon(name: "chevron-right", size: 10).rotationEffect(.degrees(configuration.isExpanded ? 90 : 0)).foregroundStyle(Palette(scheme).muted)
                    configuration.label
                }.padding(.horizontal, 10).frame(height: 24).contentShape(Rectangle())
            }.buttonStyle(HoverButton()).accessibilityValue(configuration.isExpanded ? "已展开" : "已折叠")
            if configuration.isExpanded { configuration.content }
        }
    }
}
func projectColor(_ index: Int) -> Color { [Color(hex: 0x94a6be), Color(hex: 0xb2a3b6), Color(hex: 0xbcaf95), Color(hex: 0x8ea99f)][index % 4] }

struct LibraryView: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    @FocusState private var searchFocused: Bool
    private var p: Palette { Palette(scheme) }
    var body: some View {
        GeometryReader { geo in
            let compact = geo.size.width < 950
            let inset: CGFloat = compact ? 27 : 34
            VStack(spacing: 0) {
                HStack(spacing: 11) {
                    Icon(name: "panel-left", size: 15); Text("个人工作区"); Icon(name: "chevron-right", size: 10); Text("资料库").foregroundStyle(p.secondary)
                    Spacer()
                    ActionButton(title: "Agent 修复", icon: "terminal", height: 25) { Task { await store.openAgent() } }.disabled(!store.isConnected || store.filtered.isEmpty)
                    HStack(spacing: 6) { Circle().fill(store.isConnected ? Color(hex: 0x8a9f96) : p.amber).frame(width: 4, height: 4); Text(store.isConnected ? "本地工作区" : "尚未连接").font(.system(size: 9)) }.padding(.leading, 7)
                    ToolButton(icon: "settings-2", title: "资料库设置") { store.showSettings = true }
                }.font(.system(size: 10)).foregroundStyle(p.muted).padding(.horizontal, inset).frame(height: 51).background(WindowDragRegion()).overlay(alignment: .bottom) { Line() }
                HStack {
                    Text(store.title).font(.system(size: 27, weight: .semibold)).tracking(-0.8)
                    Spacer()
                    if let project = store.project {
                        ActionButton(title: "删除项目", icon: "trash-2") { Task { await store.prepareProjectDelete(project) } }
                            .disabled(store.busy || !store.isConnected).accessibilityIdentifier("delete-project")
                    }
                    ActionButton(title: store.scanning ? "停止检查 \(store.scanCompleted)/\(store.scanTotal)" : "检查会话", icon: store.scanning ? "x" : "scan-line", primary: true) { store.scanAll() }.disabled(!store.isConnected)
                }.padding(.horizontal, inset).padding(.top, geo.size.height < 760 ? 31 : 35).padding(.bottom, 26)
                HStack(spacing: 14) {
                    stat("全部", value: store.sessions.count)
                    Text("│").foregroundStyle(p.border)
                    stat("可修复", value: store.attentionCount, warning: true)
                    Text("│").foregroundStyle(p.border)
                    stat("已归档", value: store.archivedCount)
                    Spacer()
                    if store.loading { ProgressView().controlSize(.small) }
                }.padding(.bottom, 21).overlay(alignment: .bottom) { Line() }.padding(.horizontal, inset)
                HStack(spacing: 12) {
                    if store.scope == "old" {
                        Menu {
                            ForEach([0, 30, 90, 180, 365], id: \.self) { days in Button(days == 0 ? "不限时间" : "\(days) 天未更新") { store.oldDays = days } }
                        } label: { Text(store.oldDays == 0 ? "不限时间" : "\(store.oldDays) 天未更新").font(.system(size: 10)) }.fixedSize()
                        Menu {
                            Button("全部存储") { store.oldKind = "all" }
                            Button("外置存储") { store.oldKind = "external" }
                            Button("旧版历史") { store.oldKind = "legacy" }
                        } label: { Text(["all":"全部存储","external":"外置存储","legacy":"旧版历史"][store.oldKind] ?? "全部存储").font(.system(size: 10)) }.fixedSize()
                    } else {
                        filter("全部时间", selected: !store.recentOnly) { store.recentOnly = false }
                        filter("最近 7 天", selected: store.recentOnly) { store.recentOnly = true }
                    }
                    Spacer(minLength: 3)
                    HStack(spacing: 7) {
                        Icon(name: "search", size: 13)
                        TextField("搜索对话…", text: $store.query).font(.system(size: 10)).textFieldStyle(.plain).focused($searchFocused).accessibilityLabel("搜索对话或项目")
                        if store.query.isEmpty { Text("⌘ K").font(.system(size: 8, design: .monospaced)) }
                        else { Button { store.query = "" } label: { Icon(name: "x", size: 11) }.buttonStyle(.plain) }
                    }.foregroundStyle(p.muted).padding(.horizontal, 10).frame(width: compact ? 168 : 220, height: 30).background(p.surface, in: RoundedRectangle(cornerRadius: 6)).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(p.border))
                    ActionButton(title: store.batchMode ? "完成选择" : "批量选择", icon: store.batchMode ? "check" : "list-filter", primary: store.batchMode, height: 30) { store.batchMode.toggle(); if !store.batchMode { store.batchIDs = [] } }
                        .fixedSize().accessibilityIdentifier("batch-selection-toggle")
                    Button { store.ascending.toggle() } label: { HStack(spacing: 5) { Icon(name: "arrow-up-down", size: 12); Text(store.ascending ? "最早" : "最新").font(.system(size: 10)) }.padding(8).overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(p.border)) }.buttonStyle(HoverButton()).foregroundStyle(p.secondary)
                }.padding(.horizontal, inset).padding(.vertical, 21)
                if store.scope == "old" {
                    Text("管理当前索引中的历史会话。按更新时间筛选；外置历史可在详情中单独归位，原件保留。")
                        .font(.system(size: 10)).foregroundStyle(p.muted).frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, inset).padding(.bottom, 15)
                }
                tableHeader(compact: compact).padding(.horizontal, inset)
                if store.batchMode { batchToolbar.padding(.horizontal, inset).padding(.vertical, 9).background(p.soft) }
                if store.filtered.isEmpty {
                    EmptyPanel(icon: store.isConnected ? "search" : "database", title: store.isConnected ? "没有匹配的对话" : store.startupText, description: store.isConnected ? "调整筛选条件或搜索关键词。" : "在偏好设置中检查本地连接。")
                } else {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(store.filtered) { item in
                                ConversationRow(item: item, compact: compact)
                            }
                        }.padding(.horizontal, inset)
                    }
                }
                HStack { Text(store.isConnected ? "\(store.filtered.count) 段对话" : "读取本地索引"); Spacer(); Text("选择对话查看详情"); Icon(name: "corner-down-left", size: 11) }.font(.system(size: 9)).foregroundStyle(p.muted).padding(.horizontal, inset + 8).frame(height: 38).overlay(alignment: .top) { Line() }
            }
        }.onChange(of: store.focusSearch) { _, _ in searchFocused = true }
    }
    private func stat(_ label: String, value: Int, warning: Bool = false) -> some View { HStack(spacing: 9) { Text(label).font(.system(size: 11)).foregroundStyle(p.muted); Text(store.isConnected ? "\(value)" : "—").font(.system(size: 14, weight: .medium)).monospacedDigit().foregroundStyle(warning ? p.amber : p.secondary) } }
    private func filter(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) { HStack(spacing: 5) { Text(title); if title == "全部时间" { Text("\(store.filtered.count)").font(.system(size: 8, design: .monospaced)).foregroundStyle(p.muted) } }.font(.system(size: 10)).padding(.horizontal, 9).frame(height: 27).foregroundStyle(selected ? p.text : p.muted).background(selected ? p.selected : .clear, in: RoundedRectangle(cornerRadius: 5)).overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(selected ? p.line : .clear)) }.buttonStyle(HoverButton())
    }
    private func tableHeader(compact: Bool) -> some View {
        HStack(spacing: 12) { Text("对话").frame(maxWidth: .infinity, alignment: .leading); Text("项目").frame(width: compact ? 89 : 107, alignment: .leading); Text("状态").frame(width: compact ? 84 : 110, alignment: .leading); Text("最近更新").frame(width: compact ? 71 : 76, alignment: .leading); Spacer().frame(width: 9) }.font(.system(size: 9)).foregroundStyle(p.muted).padding(.horizontal, 12).padding(.bottom, 15).overlay(alignment: .bottom) { Line() }
    }
    private var batchToolbar: some View {
        HStack(spacing: 11) {
            Button(store.allFilteredSelected ? "取消全选" : "全选") { store.toggleSelectAll() }.buttonStyle(.plain)
            Text("已选 \(store.batchIDs.count)").foregroundStyle(p.muted)
            Spacer()
            Button("归档") { Task { await store.prepareBatch("archive") } }.disabled(store.batchIDs.isEmpty || store.busy)
            Button("取消归档") { Task { await store.prepareBatch("unarchive") } }.disabled(store.batchIDs.isEmpty || store.busy)
            Button("移动到项目…") { store.openProjectAssignment(store.batchIDs.sorted()) }.disabled(store.batchIDs.isEmpty || store.busy)
            Button("合并…") { store.openMerge() }.disabled(store.batchIDs.count < 2 || store.busy)
            Button("删除…") { Task { await store.prepareBatch("delete") } }.foregroundStyle(p.red).disabled(store.batchIDs.isEmpty || store.busy)
        }.font(.system(size: 10)).buttonStyle(.plain)
    }
}

struct ConversationRow: View {
    @EnvironmentObject var store: ConversationStore
    @Environment(\.colorScheme) private var scheme
    let item: Conversation
    let compact: Bool
    @State private var hovered = false
    private var p: Palette { Palette(scheme) }
    var body: some View {
        Button {
            if store.batchMode { if store.batchIDs.contains(item.id) { store.batchIDs.remove(item.id) } else { store.batchIDs.insert(item.id) } }
            else { store.detailTab = "preview"; Task { await store.select(item.id) } }
        } label: {
            HStack(spacing: 12) {
                HStack(spacing: 12) {
                    if store.batchMode { Image(systemName: store.batchIDs.contains(item.id) ? "checkmark.square.fill" : "square").font(.system(size: 16)).foregroundStyle(p.accent).frame(width: 32) }
                    else { Icon(name: item.isArchived ? "archive" : "messages-square", size: 17).foregroundStyle(p.secondary).frame(width: 32, height: 32).background(p.soft, in: RoundedRectangle(cornerRadius: 8)).overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(p.border, lineWidth: 0.7)) }
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 5) { Text(item.title).font(.system(size: 12.5, weight: .medium)).lineLimit(1); if store.pins.contains(item.id) { Icon(name: "pin", size: 10).foregroundStyle(p.muted) } }
                        Text((item.previewText?.isEmpty == false ? item.previewText! : item.cwd.isEmpty ? "独立对话" : item.cwd.replacingOccurrences(of: NSHomeDirectory(), with: "~"))).font(.system(size: 10)).foregroundStyle(p.muted).lineLimit(1)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
                HStack(spacing: 5) { RoundedRectangle(cornerRadius: 1).fill(projectColor(store.projectIndexes[item.projectKey] ?? 0)).frame(width: 5, height: 5); Text(item.project).lineLimit(1) }.font(.system(size: 10)).foregroundStyle(p.secondary).frame(width: compact ? 89 : 107, alignment: .leading)
                HStack(spacing: 5) { Circle().fill(store.needsAttention(item) ? p.amber : p.muted.opacity(0.6)).frame(width: 4, height: 4); Text(store.rowStatus(item)).lineLimit(1) }.font(.system(size: 10)).foregroundStyle(store.needsAttention(item) ? p.amber : p.muted).frame(width: compact ? 84 : 110, alignment: .leading)
                Text(dateText).font(.system(size: 10)).foregroundStyle(p.muted).frame(width: compact ? 71 : 76, alignment: .leading)
                Icon(name: "chevron-right", size: 9).opacity(hovered ? 1 : 0).foregroundStyle(p.muted)
            }.padding(.horizontal, 12).frame(height: 77).background(hovered || store.batchIDs.contains(item.id) ? p.hover.opacity(0.65) : .clear).contentShape(Rectangle())
        }.buttonStyle(.plain).onHover { hovered = $0 }.overlay(alignment: .bottom) { Line() }
            .accessibilityLabel("打开对话：\(item.title)").accessibilityIdentifier("thread-\(item.id)")
            .contextMenu {
                Button("在 Codex 中打开") { openCodex(item.id) }
                Button("移动到项目…") { store.openProjectAssignment([item.id]) }
                Button("基于此对话新建项目…") { store.openProjectAssignment([item.id], create: true) }
                Button(store.pins.contains(item.id) ? "取消置顶" : "置顶对话") { store.togglePin(item.id) }
                Button("复制对话 ID") { copyText(item.id) }
            }
    }
    private var dateText: String { Calendar.current.isDateInToday(item.updated) ? item.updated.formatted(.dateTime.hour().minute()) : item.updated.formatted(.dateTime.month(.twoDigits).day(.twoDigits)) }
}
