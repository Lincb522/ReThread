"""Read-only Codex project identity, including the desktop's in-progress migration.

Never infer a project name from a generated chat directory. Explicit membership
wins over path matching; projectless sessions stay projectless even under a repo.
Only project metadata is returned; no other desktop preferences leave this reader.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

class ProjectCatalog:
    def __init__(self, home, db, use_desktop=True):
        self.projects={};self.aliases={};self.assignments={};self.projectless=set();self.hints={};self.hosts={}
        state={};path=Path(home)/'.codex-global-state.json'
        if use_desktop and path.exists():
            try:
                state=json.loads(path.read_text())
                if not isinstance(state,dict):raise ValueError('根节点应为对象')
            except (OSError,ValueError) as exc:raise ValueError('Codex 项目信息读取失败：'+str(exc)) from exc
        def field(key,kind,default):
            value=state.get(key,default)
            if not isinstance(value,kind):raise ValueError('Codex 项目信息格式不匹配：'+key)
            return value
        local=field('local-projects',dict,{})
        self.assignments=field('thread-project-assignments',dict,{})
        self.projectless=set(field('projectless-thread-ids',list,[]))
        self.hints=field('thread-workspace-root-hints',dict,{})
        self.hosts=field('thread-project-membership-host-ids',dict,{})
        migration_key='local:'+str(Path(home).resolve())
        migrated=field('app-server-project-id-by-legacy-project-id-by-host',dict,{}).get(migration_key,{})
        if not isinstance(migrated,dict):raise ValueError('Codex 项目迁移映射格式不匹配')
        stages=field('app-server-projects-migration-by-host',dict,{}).get(migration_key,{})
        if not isinstance(stages,dict):raise ValueError('Codex 项目迁移状态格式不匹配')
        self.use_sql_membership=stages.get('threadAssignmentsMigrated') is True
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'projects' in tables:
            if 'project_roots' not in tables:raise ValueError('Codex 缺少项目根目录表')
            for r in db.execute('SELECT id,name,position FROM projects ORDER BY position,id'):
                roots=[x[0] for x in db.execute('SELECT path FROM project_roots WHERE project_id=? ORDER BY position',(r[0],))]
                self.add(r[0],r[1],roots,r[2],'codex_sqlite')
        # The desktop still reads legacy membership until the migration is complete.
        # Bridge the IDs, rather than displaying both copies as separate projects.
        for old,p in local.items():
            if not isinstance(p,dict):raise ValueError('Codex 本地项目条目格式不匹配')
            if p.get('hostId') not in (None,'local'):continue
            pid=migrated.get(old,old);self.aliases[old]=pid
            if not self.use_sql_membership or pid not in self.projects:
                self.add(pid,p.get('name'),p.get('rootPaths'),len(self.projects),'codex_desktop')
        order=field('project-order',list,[])
        rank={self.aliases.get(k,k):n for n,k in enumerate(order) if isinstance(k,str)}
        if not self.use_sql_membership:
            for pid,p in self.projects.items():p['order']=rank.get(pid,len(rank)+p['order'])
        self.public=sorted(self.projects.values(),key=lambda p:(p['order'],p['name'],p['id']))

    def add(self,pid,name,roots,order,source):
        if not isinstance(pid,str) or not pid or not isinstance(name,str) or not name.strip():raise ValueError('Codex 项目 ID 或名称缺失')
        if not isinstance(roots,list) or any(not isinstance(r,str) or not os.path.isabs(r) for r in roots):raise ValueError('Codex 项目根目录格式不匹配')
        self.projects[pid]={'id':pid,'name':name,'roots':roots,'order':order,'source':source}

    @staticmethod
    def ungrouped(source='projectless'):
        return {'project_key':'projectless','project_name':'独立对话','project_roots':[],
                'project_source':source,'project_order':2147483647,'project_is_saved':False}

    def assigned(self,pid):
        p=self.projects.get(self.aliases.get(pid,pid))
        if p is None:return self.ungrouped('missing_project')
        return {'project_key':p['id'],'project_name':p['name'],'project_roots':p['roots'],
                'project_source':p['source'],'project_order':p['order'],'project_is_saved':True}

    def resolve(self,row):
        sid=row['id'];pid=row.get('project_id')
        if self.use_sql_membership:
            return self.assigned(pid) if pid else self.ungrouped()
        if self.hosts.get(sid,'local')!='local':return self.ungrouped('other_host')
        assignment=self.assignments.get(sid)
        if assignment is not None:
            if not isinstance(assignment,dict):raise ValueError('Codex 会话项目归属格式不匹配')
            if assignment.get('projectKind')=='local' and isinstance(assignment.get('projectId'),str):
                return self.assigned(assignment['projectId'])
            return self.ungrouped('other_project_kind')
        if sid in self.projectless:return self.ungrouped()
        if pid:return self.assigned(pid)
        # Older sessions without explicit membership use the most specific saved
        # project root (a path-component match, not an unsafe string prefix).
        cwd=self.hints.get(sid) or row.get('cwd','')
        if not isinstance(cwd,str) or not os.path.isabs(cwd):return self.ungrouped('unassigned')
        cwd=os.path.normpath(cwd);matches=[]
        for p in self.public:
            for root in p['roots']:
                normalized=os.path.normpath(root)
                if cwd==normalized or (normalized!='/' and cwd.startswith(normalized+os.sep)):
                    matches.append((len(normalized),p['id']))
        if not matches:return self.ungrouped('unassigned')
        longest=max(length for length,_ in matches);ids={pid for length,pid in matches if length==longest}
        return self.assigned(next(iter(ids))) if len(ids)==1 else self.ungrouped('ambiguous_root')
