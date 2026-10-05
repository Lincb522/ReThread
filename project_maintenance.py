"""Whole local-project deletion. Official thread/project RPCs, offline desktop metadata.

The desktop's removeLocal operation keeps chats and has an undo timer. That is
not whole-project deletion. Here histories are verified first, then project/delete
is sent once, then the scoped legacy registration is removed while Codex is closed.
Source roots are descriptive only: never walked, unlinked or recursively deleted.
"""
from __future__ import annotations
import copy
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import server as upstream


class ProjectMaintenance:
    def __init__(self, maintenance, atomic_json, digest):
        self.m=maintenance;self.store=maintenance.store;self.atomic_json=atomic_json;self.digest=digest
        self.state_path=self.store.codex_home/'.codex-global-state.json'

    def desktop_state(self):
        if self.store.sqlite_home!=self.store.codex_home or not self.state_path.exists():return {}
        if self.state_path.is_symlink():raise upstream.OperationError('项目状态文件是软链接，停止修改')
        value=json.loads(self.state_path.read_text())
        if not isinstance(value,dict):raise ValueError('Codex 项目状态格式错误')
        return value

    def clean_state(self, state, pid, aliases, ids):
        # Change only exact project IDs / deleted thread IDs. Keep other projects,
        # source-root preferences, automation targets, accounts and settings intact.
        state=copy.deepcopy(state);changes=[];keys=set(aliases)|{pid};ids=set(ids)
        def replace(path,value):
            node=state
            for k in path[:-1]:node=node[k]
            old=node[path[-1]]
            if old==value:return
            changes.append({'path':path,'before':copy.deepcopy(old),'after':copy.deepcopy(value)})
            node[path[-1]]=value
        def remove(path):
            node=state
            for k in path[:-1]:node=node[k]
            changes.append({'path':path,'before':copy.deepcopy(node[path[-1]]),'removed':True})
            del node[path[-1]]
        for key in ('local-projects','project-appearances','project-files','project-writable-roots'):
            values=state.get(key,{})
            if not isinstance(values,dict):raise ValueError('项目状态格式错误：'+key)
            for k in keys&values.keys():remove([key,k])
        for key in ('project-order','pinned-project-ids'):
            if key in state:
                if not isinstance(state[key],list):raise ValueError('项目状态格式错误：'+key)
                replace([key],[v for v in state[key] if v not in keys])
        selected=state.get('selected-project')
        if isinstance(selected,dict) and selected.get('type',selected.get('projectKind',selected.get('kind')))=='local' and selected.get('projectId') in keys:
            remove(['selected-project'])
        host='local:'+str(self.store.codex_home.resolve())
        for key in ('app-server-project-id-by-legacy-project-id-by-host','app-server-pending-project-deletions-by-host'):
            hosts=state.get(key,{})
            if not isinstance(hosts,dict):raise ValueError('项目迁移状态格式错误')
            values=hosts.get(host,{})
            if not isinstance(values,dict):raise ValueError('项目迁移状态格式错误')
            for k in keys&values.keys():remove([key,host,k])
        orders=state.get('sidebar-project-thread-orders',{})
        if not isinstance(orders,dict):raise ValueError('项目排序状态格式错误')
        for k in list(orders):
            if k in keys or k in {'local:'+i for i in keys}:remove(['sidebar-project-thread-orders',k])
        assignments=state.get('thread-project-assignments',{})
        if not isinstance(assignments,dict):raise ValueError('项目归属状态格式错误')
        hosts=state.get('thread-project-membership-host-ids',{})
        for sid,value in list(assignments.items()):
            if sid in ids or (isinstance(value,dict) and value.get('projectKind')=='local' and value.get('projectId') in keys and hosts.get(sid,'local')=='local'):
                remove(['thread-project-assignments',sid])
        for key in ('thread-project-membership-host-ids','thread-workspace-root-hints'):
            values=state.get(key,{})
            if not isinstance(values,dict):raise ValueError('会话项目状态格式错误：'+key)
            for sid in ids&values.keys():remove([key,sid])
        if 'projectless-thread-ids' in state:
            replace(['projectless-thread-ids'],[i for i in state['projectless-thread-ids'] if i not in ids])
        migration=state.get('app-server-projects-migration-by-host',{}).get(host,{})
        if 'pendingThreadAssignmentIds' in migration:
            replace(['app-server-projects-migration-by-host',host,'pendingThreadAssignmentIds'],[i for i in migration['pendingThreadAssignmentIds'] if i not in ids])
        return state,changes

    def snapshot(self, pid):
        if not isinstance(pid,str) or not pid or len(pid)>200:raise ValueError('请选择一个已保存的本地项目')
        catalog=self.store.project_catalog()
        project=catalog.projects.get(pid)
        if project is None:raise upstream.OperationError('项目记录已不存在，请刷新项目列表')
        aliases=sorted(k for k,v in catalog.aliases.items() if v==pid)
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM threads ORDER BY id')]
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sql=[dict(r) for r in db.execute('SELECT * FROM projects WHERE id=?',(pid,))] if 'projects' in tables else []
            roots=[dict(r) for r in db.execute('SELECT * FROM project_roots WHERE project_id=? ORDER BY position',(pid,))] if sql else []
        # Full library, including archived, old, and hidden sub-agent records.
        members=[r for r in rows if catalog.resolve(r)['project_key']==pid]
        ids=[r['id'] for r in members]
        _,patch=self.clean_state(self.desktop_state(),pid,aliases,ids)
        return {'project':project,'aliases':aliases,'sql':sql,'roots':roots,'ids':ids,'patch':patch,
                'archived_count':sum(bool(r['archived']) for r in members),
                'hidden_count':sum(str(r.get('source','')).startswith('{') and 'subagent' in str(r.get('source','')) for r in members)}

    def plan(self,pid,progress=None):
        snap=self.snapshot(pid);ids=snap['ids'];graph=self.store.graph()
        expanded=set(self.store.descendants(ids,graph)) if ids else set()
        extra=expanded-set(ids)
        if extra:
            names=[self.store.row(i).get('title') or i for i in sorted(extra)]
            raise upstream.OperationError('项目包含归属其他项目或独立对话的子线程：'+ '、'.join(names)+'。请先调整归属或单独处理；本次没有扩大删除范围')
        if ids:
            public=self.m.batch_plan('delete',ids,progress)
            base=self.m.plans.pop(public['token'])
        else:base={'targets':[],'ids':[],'file_bytes':0,'members':[],'blocked':[]}
        if self.snapshot(pid)!=snap:raise upstream.OperationBusy('预检期间项目或归属发生变化，请重新预检')
        base.update(action='project_delete',id=pid,batch=True,project_snapshot=snap,project=snap['project'],
                    archived_count=snap['archived_count'],hidden_count=snap['hidden_count'],requires_exit=True)
        return self.m.save_plan(base)

    def guard(self):
        if self.m.runtime_probe():
            raise upstream.OperationBusy('删除整个项目前，请先结束任务并完全退出 Codex / ChatGPT 和 Codex CLI。项目登记仍由客户端缓存管理；退出后回到续言重新预检并确认，避免项目记录被写回。整项目删除不移除源码目录。')

    def execute(self,token,confirmation,progress=None):
        p=self.m.plans.pop(token,None)
        if not p or p['expires']<time.monotonic():raise upstream.OperationError('项目删除计划过期或已使用，请重新预检')
        if not secrets.compare_digest(p['confirmation'],str(confirmation)):raise upstream.OperationError('确认文本不匹配')
        if p['blocked']:raise upstream.OperationError('项目内仍有预检受限会话；整项目尚未执行，请先处理所列问题')
        self.guard()
        snap=p['project_snapshot'];pid=p['id']
        if self.snapshot(pid)!=snap:raise upstream.OperationBusy('项目或会话归属已变化，请重新预检；尚未执行')
        # Check RPC support and exact identity before deleting even the first chat.
        if snap['sql']:
            official=self.m.controller._rpc_unlocked('project/read',{'projectId':pid})['project']
            if official['id']!=pid or official['name']!=snap['sql'][0]['name'] or [r['path'] for r in official['roots']]!=[r['path'] for r in snap['roots']]:
                raise upstream.OperationBusy('官方项目记录与预检不一致，尚未执行')
        self.guard()
        folder=self.m.root/'backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-project-delete-'+uuid.uuid4().hex[:8])
        folder.mkdir(parents=True,mode=0o700)
        self.atomic_json(folder/'project-metadata.json',snap)
        manifest={'version':1,'action':'project_delete','ids':snap['ids'],'project':snap['project'],
                  'created_at':datetime.now(timezone.utc).isoformat(),'status':'backed_up','files':[],
                  'project_metadata_sha256':self.digest(folder/'project-metadata.json'),
                  'scope':'Project registration and all indexed member conversations. Source directories preserved.',
                  'source_files_preserved':True}
        self.atomic_json(folder/'manifest.json',manifest)
        batch=None
        try:
            if self.snapshot(pid)!=snap:raise upstream.OperationBusy('备份后项目状态变化，尚未删除')
            if p['members']:
                child=dict(p,action='delete');child.pop('project_snapshot',None)
                child['expires']=float('inf');child['token']=secrets.token_urlsafe(32)
                self.m.plans[child['token']]=child
                def before_member(member):
                    self.guard()
                    # Membership may shrink only through this operation; no new/changed project.
                    current=self.snapshot(pid)
                    if current['project']!=snap['project'] or current['sql']!=snap['sql'] or current['aliases']!=snap['aliases'] or set(current['ids'])-set(snap['ids']) or not set(member['ids'])<=set(current['ids']):
                        raise upstream.OperationBusy('执行期间项目或归属发生变化，已停止后续操作')
                    _,current_patch=self.clean_state(self.desktop_state(),pid,snap['aliases'],snap['ids'])
                    if current_patch!=snap['patch']:raise upstream.OperationBusy('项目登记或归属在执行期间变化，已停止后续操作')
                def progress_only(phase,n,total):
                    if progress:progress(phase,n,total+1)
                try:batch=self.m.execute_batch(child['token'],child['confirmation'],progress_only,before_member)
                finally:self.m.plans.pop(child['token'],None)
                manifest['conversation_result']=batch;self.atomic_json(folder/'manifest.json',manifest)
                if not batch['ok']:raise upstream.OperationError('部分会话未完成删除，项目记录保留；详情见逐项结果')
            self.guard()
            if self.m.stop_requested():raise upstream.OperationBusy('已停止，项目记录保留')
            current=self.snapshot(pid)
            if current['ids']:raise upstream.OperationBusy('项目仍有会话，项目记录保留；请重新预检')
            if any(current[k]!=snap[k] for k in ('project','aliases','sql','roots')):raise upstream.OperationBusy('项目记录发生变化，停止移除登记')
            # Snapshot scoped state just before mutation; engine RPCs never write desktop state.
            before=self.desktop_state();after,patch=self.clean_state(before,pid,snap['aliases'],snap['ids'])
            if patch!=snap['patch']:raise upstream.OperationBusy('桌面项目登记发生变化，停止移除登记')
            self.m.report('project_delete',message='会话已验证删除，正在移除项目记录',current_title=snap['project']['name'])
            manifest['status']='removing_project';self.atomic_json(folder/'manifest.json',manifest)
            if snap['sql']:
                self.m.controller._rpc_unlocked('project/delete',{'projectId':pid})
                with self.store.db() as db:
                    if db.execute('SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone():raise upstream.OperationError('官方项目删除后记录仍存在')
            self.guard()
            if self.desktop_state()!=before:raise upstream.OperationBusy('桌面状态在移除登记期间变化，已停止写入；请检查记录')
            if patch:self.atomic_json(self.state_path,after)
            if pid in self.store.project_catalog().projects:raise upstream.OperationError('项目删除验证未通过')
            with self.store.db() as db:
                remaining={r[0] for r in db.execute('SELECT id FROM threads')} & set(snap['ids'])
            if remaining:raise upstream.OperationError('删除后会话记录仍存在')
            manifest['status']='verified'
            result={'ok':True,'action':'project_delete','affected':len(snap['ids']),'backup':str(folder),
                    'verification':f"已删除项目「{snap['project']['name']}」及 {len(snap['ids'])} 段会话（含已归档 {snap['archived_count']} 段）。源码目录保留；重新打开 Codex 后读取更新后的项目记录。",
                    'outcomes':(batch or {}).get('outcomes',[]),'desktop_sync':(batch or {}).get('desktop_sync')}
            if progress:progress('project_delete',len(p['members'])+1,len(p['members'])+1)
        except Exception as exc:
            manifest.update(status='failed_or_partial',error=str(exc))
            result={'ok':False,'action':'project_delete','affected':(batch or {}).get('affected',0),'backup':str(folder),
                    'verification':'整项目删除未完成：'+str(exc)+'。未自动重试，源码目录保留。',
                    'outcomes':(batch or {}).get('outcomes',[]),'desktop_sync':(batch or {}).get('desktop_sync')}
        manifest['result']=result;self.atomic_json(folder/'manifest.json',manifest)
        return result
