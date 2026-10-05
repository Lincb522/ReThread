"""Official project creation/assignment plus explicit offline desktop registry sync.
No source-directory move; no history rewrite; no direct SQLite membership writes.
"""
from __future__ import annotations
import copy,json,os,secrets,time,uuid
from pathlib import Path
import server as upstream

KEYS=('local-projects','project-order','thread-project-assignments','projectless-thread-ids',
      'thread-project-membership-host-ids','app-server-project-id-by-legacy-project-id-by-host',
      'thread-workspace-root-hints')

class ProjectAssignment:
    def __init__(self,m,atomic_json,digest):
        self.m=m;self.store=m.store;self.atomic_json=atomic_json;self.digest=digest
        self.state_path=self.store.codex_home/'.codex-global-state.json'

    def state(self):return self.m.project_maintenance.desktop_state()

    def guard(self):
        if self.m.runtime_probe():raise upstream.OperationBusy('项目归属同步需要先结束任务并完全退出 Codex / ChatGPT 与 CLI。退出后回到续言重新预检执行；再次打开 Codex 即读取新项目归属，避免旧缓存写回。')

    def snapshot(self,ids,pid):
        cat=self.store.project_catalog();state=self.state()
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM threads ORDER BY id')]
            columns={r[1] for r in db.execute('PRAGMA table_info(threads)')}
        if 'project_id' not in columns:raise upstream.OperationError('当前 Codex 索引版本尚未支持项目归属，请先更新 Codex 并启动一次')
        for sid in ids:
            if cat.hosts.get(sid,'local')!='local':raise upstream.OperationError('所选会话不属于本机项目，未执行跨主机移动')
        project=cat.projects.get(pid) if pid else None
        if pid and project is None:raise upstream.OperationError('目标项目已不存在，请刷新')
        return {'project':project,'aliases':cat.aliases,'state':{k:state[k] for k in KEYS if k in state},
                'memberships':{r['id']:cat.resolve(r)['project_key'] for r in rows}}

    def plan(self,ids,pid=None,name=None,root=None,progress=None):
        ids=self.m.selected_ids(ids)
        if self.store.sqlite_home!=self.store.codex_home:raise upstream.OperationError("项目同步要求历史与索引属于同一 Codex 主目录；请先连接对应的完整会话库")
        if os.environ.get('CODEX_THREAD_ID') in ids:raise upstream.OperationBusy('请结束当前正在工作的会话后调整其项目')
        if pid is not None and (not isinstance(pid,str) or not pid):raise ValueError('请选择有效项目')
        if pid and (name is not None or root is not None):raise ValueError('现有项目与新建项目参数不可同时提交')
        if not pid:
            if not isinstance(name,str) or not 1<=len(name.strip())<=200:raise ValueError('项目名称须为 1–200 个字符')
            if not isinstance(root,str) or not Path(root).is_absolute() or not Path(root).is_dir():raise ValueError('请选择已存在的绝对目录作为项目根目录')
            root=str(Path(root).resolve())
        before=self.store.fingerprint(ids);snap=self.snapshot(ids,pid)
        project=snap['project'] or {'id':'new','name':name.strip(),'roots':[root],'order':0,'source':'codex_sqlite'}
        if pid and all(snap['memberships'].get(i)==pid for i in ids):raise ValueError('所选会话已经属于此项目')
        if before!=self.store.fingerprint(ids):raise upstream.OperationBusy('预检期间会话变化，请重新预检')
        return self.m.save_plan({'action':'project_assign' if pid else 'project_create','batch':True,'requires_exit':True,
            'id':ids[0],'ids':ids,'project':project,'project_snapshot':snap,'fingerprint':before,
            'file_bytes':sum(self.store.path(self.store.row(i)).stat().st_size for i in ids if self.store.path(self.store.row(i)).is_file()),
            'targets':[{'id':i,'title':self.store.listed(self.store.row(i))['title']} for i in ids],
            'blocked':[],'diagnostic':None})

    def patch_state(self,state,snap,ids,project):
        out=copy.deepcopy(state);pid=project['id'];aliases=snap['aliases']
        legacy=next((k for k,v in aliases.items() if v==pid),pid)
        roots=[v['path'] for v in project['roots']]
        now=int(time.time()*1000)
        local=out.setdefault('local-projects',{})
        if legacy not in local:
            local[legacy]={'id':legacy,'name':project['name'],'rootPaths':roots,'createdAt':now,'updatedAt':now}
            order=out.setdefault('project-order',[])
            if legacy not in order:order.append(legacy)
        key='local:'+str(self.store.codex_home)
        out.setdefault('app-server-project-id-by-legacy-project-id-by-host',{}).setdefault(key,{})[legacy]=pid
        assignments=out.setdefault('thread-project-assignments',{})
        projectless=out.setdefault('projectless-thread-ids',[])
        hosts=out.setdefault('thread-project-membership-host-ids',{})
        # Freeze implicit memberships before introducing a new root. Other chats
        # must not accidentally move just because their working directory matches.
        if snap['project'] is None:
            for sid,old in snap['memberships'].items():
                if sid in ids or sid in assignments or sid in projectless:continue
                if old=='projectless':projectless.append(sid)
                else:
                    old_legacy=next((k for k,v in aliases.items() if v==old),old)
                    assignments[sid]={'projectKind':'local','projectId':old_legacy};hosts[sid]='local'
        for sid in ids:
            assignments[sid]={'projectKind':'local','projectId':legacy};hosts[sid]='local'
        out['projectless-thread-ids']=[sid for sid in projectless if sid not in set(ids)]
        return out

    def execute(self,token,confirmation,progress=None):
        p=self.m.plans.pop(token,None)
        if not p or p['expires']<time.monotonic() or not secrets.compare_digest(p['confirmation'],str(confirmation)):raise upstream.OperationError('项目计划过期或确认不匹配，请重新预检')
        self.guard();snap=p['project_snapshot'];pid=snap['project']['id'] if snap['project'] else None
        if self.snapshot(p['ids'],pid)!=snap or self.store.fingerprint(p['ids'])!=p['fingerprint']:raise upstream.OperationBusy('项目或会话已变化，尚未执行')
        if pid:
            project=self.m.controller._rpc_unlocked('project/read',{'projectId':pid})['project']
            if project['name']!=p['project']['name'] or [v['path'] for v in project['roots']]!=p['project']['roots']:raise upstream.OperationBusy('官方项目与预检不同，尚未执行')
        self.guard();folder,manifest=self.m.backup(p)
        self.atomic_json(folder/'project-metadata.json',snap)
        manifest.update(project_metadata_sha256=self.digest(folder/'project-metadata.json'),project=p['project'],assigned_ids=[])
        self.atomic_json(folder/'manifest.json',manifest)
        try:
            if self.snapshot(p['ids'],pid)!=snap or not self.m.unchanged_since_backup(p,folder,manifest):raise upstream.OperationBusy('备份后状态变化，尚未执行')
            self.guard()
            if not pid:
                project=self.m.controller._rpc_unlocked('project/create',{'idempotencyKey':str(uuid.uuid4()),'name':p['project']['name'],'roots':[{'path':v} for v in p['project']['roots']]})['project']
                pid=project['id'];manifest['created_project_id']=pid;self.atomic_json(folder/'manifest.json',manifest)
            for n,sid in enumerate(p['ids']):
                if self.m.stop_requested():raise upstream.OperationBusy('已按请求停止后续项目移动；已执行的归属记录保存在备份中')
                self.guard()
                if progress:progress('executing',n,len(p['ids']))
                result=self.m.controller._rpc_unlocked('thread/metadata/update',{'threadId':sid,'projectId':pid})['thread']
                if result['id']!=sid or result.get('projectId')!=pid:raise upstream.OperationError('官方项目归属返回值不一致')
                manifest['assigned_ids'].append(sid);self.atomic_json(folder/'manifest.json',manifest)
            self.guard();state=self.state()
            if {k:state[k] for k in KEYS if k in state}!=snap['state']:raise upstream.OperationBusy('桌面项目登记发生变化，停止写入；官方操作状态已记录')
            after=self.patch_state(state,snap,p['ids'],project)
            # Retain unknown preferences without backing up unrelated credentials.
            before_hash=self.digest(self.state_path) if self.state_path.exists() else None
            self.guard()
            if (self.digest(self.state_path) if self.state_path.exists() else None)!=before_hash:raise upstream.OperationBusy('桌面状态文件变化，停止写入')
            self.atomic_json(self.state_path,after)
            with self.m.controller.rpc_session() as call:
                for sid in p['ids']:
                    t=call('thread/read',{'threadId':sid,'includeTurns':False})['thread']
                    if t['projectId']!=pid:raise upstream.OperationError('官方重新读取项目归属不一致')
            cat=self.store.project_catalog()
            for sid in p['ids']:
                if cat.resolve(self.store.row(sid))['project_key']!=pid:raise upstream.OperationError('桌面项目归属核验失败')
            for sid,old in snap['memberships'].items():
                if sid not in p['ids'] and cat.resolve(self.store.row(sid))['project_key']!=old:raise upstream.OperationError('其他会话归属发生变化，详见记录')
            for row in manifest['rows']:
                current=self.store.row(row['id'])
                if any(current.get(k)!=row.get(k) for k in ('cwd','rollout_path','title','archived','id')):raise upstream.OperationError('会话其他字段发生变化，请检查记录')
            if any(self.digest(Path(f['source']))!=f['sha256'] for f in manifest['files']):raise upstream.OperationError('原历史文件发生变化，请检查记录')
            manifest.update(status='verified',target_project_id=pid);self.atomic_json(folder/'manifest.json',manifest)
            if progress:progress('executing',len(p['ids']),len(p['ids']))
            return {'ok':True,'action':p['action'],'affected':len(p['ids']),'backup':str(folder),'verification':f"已同步到 Codex 项目「{project['name']}」；官方记录与桌面项目登记核验一致。再次打开 Codex 即可查看。会话 ID、标题、历史、工作目录及源码保持不变。",'desktop_sync':{'status':'persisted','affected':len(p['ids']),'message':'Codex 项目与会话归属已持久化；重新打开 Codex 后生效。'}}
        except Exception as exc:
            manifest.update(status='failed_or_partial',error=str(exc));self.atomic_json(folder/'manifest.json',manifest)
            raise upstream.OperationError(f'{exc}；未自动重试，已执行范围与备份：{folder}') from exc
