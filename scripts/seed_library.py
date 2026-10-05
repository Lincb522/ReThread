from pathlib import Path
import sqlite3,json,time,shutil,uuid
import sys,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manager import OfficialController
parser=argparse.ArgumentParser(description="Create NEW disposable conversations for native acceptance only")
parser.add_argument('directory',type=Path);parser.add_argument('--codex',default='/usr/local/bin/codex');args=parser.parse_args()
root=args.directory.expanduser().resolve()
if root.exists():raise SystemExit('Destination must not exist; existing conversation libraries are never replaced.')
root.mkdir(parents=True)
OfficialController(root,args.codex,root)._rpc_unlocked('thread/list',{'limit':1})
if not (root/'state_5.sqlite').exists():raise SystemExit('This acceptance seed requires the Codex 0.153.2 state_5 schema.')
(root/'.rethread-acceptance').write_text('Disposable acceptance data created explicitly by seed_library.py.\n')
titles=['重构 SwiftUI 导航与状态管理','为新版本打磨桌面端交互','修复音频队列的并发问题','整理组件库的设计变量','搭建本地 Markdown 索引','优化图片缓存与预加载','为设置页补充键盘操作','实现窗口恢复与状态持久化','每周阅读笔记自动整理','完成 1.0 版本的发布检查','设计第一版应用图标','配置开发环境与格式化规则']
previews=['保留原有交互，把导航状态从视图中解耦。','侧栏收起时，内容区需要更自然的过渡。','先定位重复入队的来源，再验证播放状态。','统一间距、圆角和深浅色的语义颜色。','目录迁移后，已有索引的引用路径失效。','减少重复请求，保留可观测的缓存命中记录。','让焦点顺序与页面阅读顺序保持一致。','先保存工作区，再恢复当前选中的文档。','根据主题分组，不打乱原始记录顺序。','构建、签名和发布说明均已检查完成。','图形保持简单，在小尺寸下也清晰可辨。','项目的基础环境已经就绪。']
projects=['Orbit','Studio','Orbit','Studio','个人项目','Orbit','Studio','Orbit','个人项目','Orbit','Studio','个人项目']
now=int(time.time())
for i,(title,preview,project) in enumerate(zip(titles,previews,projects)):
 sid=str(uuid.uuid4());folder=root/('archived_sessions' if i>=9 else 'sessions')/'2026/10/05';folder.mkdir(parents=True,exist_ok=True);p=folder/f'rollout-2026-10-05T00-00-00-{sid}.jsonl'
 events=[{'timestamp':'2026-10-05T10:38:00Z','type':'session_meta','payload':{'id':sid,'timestamp':'2026-10-05T10:38:00Z','cwd':f'/Developer/{project}','originator':'codex_cli_rs','source':'cli','cli_version':'0.153.2'}}, {'timestamp':'2026-10-05T10:38:00Z','type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'我们先把导航状态从视图中解耦。保留现有的侧栏和页面切换效果，同时让深层页面也能正确恢复。'}]}},{'timestamp':'2026-10-05T10:42:00Z','type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'我检查了当前的导航结构。问题不在转场本身，而在于多个视图分别维护了自己的选择状态。\n\n这次调整保持界面不变，只收敛状态的来源：\n\n- 用一个 `NavigationStore` 管理当前路由\n- 侧栏选中项与详情页使用同一份状态\n- 保留窗口恢复和原有的转场动画\n\n```swift\n@Observable\nfinal class NavigationStore {\n    var selection: WorkspaceRoute = .library\n    var path: [WorkspaceRoute] = []\n}\n```'}]}}]
 p.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in events)+'\n')
 stored=p if i not in [0,4] else root/'old'/p.name
 with sqlite3.connect(root/'state_5.sqlite') as c:
  c.execute('INSERT INTO threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,has_user_event,archived,cli_version,first_user_message,model,thread_source,history_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,str(stored),now-i*86400,now-i*8500,'cli','openai',f'/Developer/{project}',title,'{"type":"read-only"}','never',1,int(i>=9),'0.153.2',preview,'gpt-5','cli','legacy'))
print(root)
