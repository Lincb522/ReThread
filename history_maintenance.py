"""Explicit, scoped rollout relocation and index linking; reuses Maintenance backup/lock.

No history regeneration, project changes, symlink creation or repair-on-failure.
Migration publishes an exact copy before committing the index, then optionally
removes the original. Interrupted phases remain explicit in the backup journal.
"""
import hashlib,json,os,secrets,shutil,sqlite3,stat
from pathlib import Path
from contextlib import closing
import server as upstream

class HistoryMaintenance:
    def __init__(self,owner,stamp,digest,atomic_json):
        self.owner=owner;self.store=owner.store;self.stamp=stamp;self.digest=digest;self.write=atomic_json

    def validate(self,path,sid):
        if not path.is_absolute() or not path.name.startswith('rollout-') or not path.name.endswith('.jsonl') or sid not in path.name:
            raise upstream.OperationError('请选择文件名包含当前完整会话 ID 的 rollout JSONL 历史文件')
        self.owner.assert_storage_available(path)
        before=self.stamp(path)
        if before is None or not path.is_file():raise upstream.OperationError('历史文件不存在或不是普通文件')
        if before[3]>1024*1024*1024:raise upstream.OperationError('历史超过 1 GiB，未完整校验，暂不生成路径修改计划')
        h=hashlib.sha256();header=None;lines=0
        with path.open('rb') as f:
            for n,line in enumerate(f,1):
                h.update(line);lines=n
                if not line.strip():continue
                try:e=json.loads(line)
                except (ValueError,UnicodeError) as exc:raise upstream.OperationError(f'历史第 {n} 行格式异常，未修改索引') from exc
                if not isinstance(e,dict):raise upstream.OperationError(f'历史第 {n} 行不是记录对象')
                if header is None:
                    header=e.get('payload')
                    if e.get('type')!='session_meta' or not isinstance(header,dict) or header.get('id')!=sid:
                        raise upstream.OperationError('历史首条元数据与所选会话 ID 不一致')
        if header is None:raise upstream.OperationError('历史文件为空')
        if self.stamp(path)!=before:raise upstream.OperationBusy('历史文件正在变化，请稳定后重新预检')
        return {'stamp':before,'sha256':h.hexdigest(),'bytes':before[3],'lines':lines,'header':header}

    def projection(self,row,evidence):
        if row.get('history_mode')!='paginated':return
        db=self.store.sqlite_home/'thread_history_1.sqlite'
        if not db.is_file():raise upstream.OperationError('分页历史数据库缺失，保留现场，不重建分页索引')
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
            r=c.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state WHERE thread_id=?',(row['id'],)).fetchone()
        if r and (type(r[0]) is not int or not 0<=r[0]<=evidence['bytes']):
            raise upstream.OperationError('分页投影偏移超出历史范围，先处理分页索引再关联文件')

    def parent_identity(self,p):
        s=p.stat()
        if not stat.S_ISDIR(s.st_mode):raise upstream.OperationError('迁移目标不是目录')
        return [str(p.resolve(strict=True)),s.st_dev,s.st_ino]

    def check_removal(self,src,sid):
        if src.is_symlink():raise upstream.OperationError('来源是文件软链接，请保留来源副本；本操作不删除链接的外部目标')
        with self.store.db() as c:
            for row in c.execute('SELECT id,rollout_path FROM threads WHERE id!=?',(sid,)):
                p=Path(row[1])
                if p.exists() and p.samefile(src):raise upstream.OperationError('另一个会话索引共享此文件，请保留来源副本')
        if any(parent==sid and child!=sid for child,parent in self.owner.references()):
            raise upstream.OperationError('存在分叉历史引用，请勾选保留来源副本；本次不改写引用方')

    def plan(self,action,sid,target_path,keep_source):
        row=self.store.row(sid)
        if sid==os.environ.get('CODEX_THREAD_ID'):raise upstream.OperationBusy('当前正在工作的会话请结束任务后再维护')
        if not isinstance(target_path,str) or not target_path.strip() or '\x00' in target_path:raise ValueError('请选择目标目录或历史文件')
        if type(keep_source) is not bool:raise ValueError('保留来源选项格式无效')
        target=Path(target_path).expanduser()
        if not target.is_absolute():raise ValueError('目标路径必须是绝对路径')
        src=self.store.path(row);fingerprint=self.store.fingerprint([sid])
        self.owner.assert_storage_available(src);self.owner.assert_storage_available(target)
        file=src if action=='migrate' else target
        evidence=self.validate(file,sid);self.projection(row,evidence)
        if action=='link':
            if src.exists():
                if src.samefile(target):raise upstream.OperationError('索引已经指向这个文件，无需链接')
                if self.digest(src)!=evidence['sha256']:raise upstream.OperationError('所选副本与当前历史内容不同，停止链接，避免切换到旧版或其他内容')
            opposite=self.store.codex_home/('sessions' if row['archived'] else 'archived_sessions')
            if target.resolve().is_relative_to(opposite.resolve()):raise upstream.OperationError('目标目录与会话归档状态不一致，请使用归档操作')
            dst=target.resolve(strict=True)
            if not dst.name.startswith('rollout-') or not dst.name.endswith('.jsonl') or sid not in dst.name:raise upstream.OperationError('真实目标文件名与索引规则不一致')
            keep_source=True;parent=None
        else:
            parent=self.parent_identity(target)
            if src.parent.resolve()==target.resolve():raise upstream.OperationError('历史已位于所选目录，无需迁移')
            opposite=self.store.codex_home/('sessions' if row['archived'] else 'archived_sessions')
            if target.resolve().is_relative_to(opposite.resolve()):raise upstream.OperationError('目标目录与归档状态冲突，请使用归档操作')
            if target.resolve().is_relative_to(self.owner.root):raise upstream.OperationError('迁移目标不能放进续言的备份或维护目录')
            if not keep_source:self.check_removal(src,sid)
            if shutil.disk_usage(target).free<evidence['bytes']+64*1024*1024:raise upstream.OperationError('目标磁盘空间不足')
            dst=target.resolve()/('rethread-'+sid+'-'+secrets.token_hex(4))/src.name
        if self.store.fingerprint([sid])!=fingerprint:raise upstream.OperationBusy('预检期间索引已变化')
        records=self.owner.scoped_records([sid])
        return {'action':action,'id':sid,'ids':[sid],'title':None,'diagnostic':None,
            'targets':[{'id':sid,'title':row.get('name') or row['title']}],
            'source':str(src),'destination':str(dst),'keep_source':keep_source,'file_bytes':evidence['bytes'],
            'history_file':str(file),'history_evidence':evidence,'parent_identity':parent,
            'fingerprint':fingerprint,'related_records':records,'original_row':row,
            'external_destination':not dst.is_relative_to((self.store.codex_home/('archived_sessions' if row['archived'] else 'sessions')).resolve())}

    def fresh(self,plan):
        if self.owner.runtime_probe():raise upstream.OperationBusy('检测到 Codex 写入进程，请退出后重新预检')
        file=Path(plan['history_file'])
        if self.validate(file,plan['id'])!=plan['history_evidence']:raise upstream.OperationBusy('历史校验依据发生变化，请重新预检')
        if self.store.row(plan['id'])!=plan['original_row'] or self.owner.scoped_records(plan['ids'])!=plan['related_records']:
            raise upstream.OperationBusy('会话或关联索引已变化，请重新预检')
        if plan['action']=='migrate':
            parent=Path(plan['parent_identity'][0])
            if self.parent_identity(parent)!=plan['parent_identity']:raise upstream.OperationBusy('目标目录已变化')
            dst=Path(plan['destination'])
            if os.path.lexists(dst.parent):raise upstream.OperationError('迁移目标已存在，未覆盖文件')
            if not plan['keep_source']:self.check_removal(Path(plan['source']),plan['id'])

    def execute(self,plan,folder,manifest):
        self.fresh(plan);dst=Path(plan['destination']);src=Path(plan['source']);expected=plan['history_evidence']['sha256']
        manifest.update(source=str(src),destination=str(dst),keep_source=plan['keep_source'],history_sha256=expected,phase='validated')
        self.write(folder/'manifest.json',manifest)
        if plan['action']=='migrate':
            if shutil.disk_usage(dst.parent.parent).free<plan['file_bytes']+64*1024*1024:raise upstream.OperationError('目标磁盘剩余空间不足，索引未更新')
            dst.parent.mkdir(mode=0o700);tmp=dst.with_suffix('.jsonl.partial')
            manifest['phase']='copying';self.write(folder/'manifest.json',manifest)
            with src.open('rb') as source,tmp.open('xb') as target:
                os.chmod(tmp,0o600);shutil.copyfileobj(source,target);target.flush();os.fsync(target.fileno())
            if self.digest(tmp)!=expected or self.stamp(src)!=plan['history_evidence']['stamp'] or self.digest(src)!=expected:
                raise upstream.OperationBusy('复制校验失败，索引未更新；保留文件供核查')
            if os.path.lexists(dst):raise upstream.OperationError('目标文件意外出现，未覆盖')
            os.replace(tmp,dst)
            self.sync_dir(dst.parent)
            manifest['phase']='copied';self.write(folder/'manifest.json',manifest)
        if self.owner.runtime_probe():raise upstream.OperationBusy('Codex 进程重新启动，索引未更新')
        if self.digest(dst)!=expected:raise upstream.OperationBusy('目标内容在提交前发生变化')
        if self.store.fingerprint(plan['ids'])!=plan['fingerprint']:raise upstream.OperationBusy('提交前原始历史或索引已变化')
        if self.owner.scoped_records(plan['ids'])!=plan['related_records']:raise upstream.OperationBusy('关联索引在提交前发生变化')
        with closing(sqlite3.connect(self.store.db_path,timeout=5)) as c,c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
            actual=c.execute('SELECT * FROM threads WHERE id=?',(plan['id'],)).fetchone()
            if actual is None or dict(actual)!=plan['original_row']:raise upstream.OperationBusy('会话索引已变化')
            if self.digest(dst)!=expected:raise upstream.OperationBusy('目标历史已变化')
            c.execute('UPDATE threads SET rollout_path=? WHERE id=?',(str(dst),plan['id']))
        manifest['phase']='index_updated';self.write(folder/'manifest.json',manifest)
        self.verify(plan,source_cleanup=False)
        if plan['action']=='migrate' and not plan['keep_source']:
            if self.owner.runtime_probe():raise upstream.OperationBusy('Codex 已重新启动，索引已更新但来源保留，请检查记录')
            if self.stamp(src)!=plan['history_evidence']['stamp'] or self.digest(src)!=expected or self.digest(dst)!=expected:
                raise upstream.OperationBusy('清理来源前文件已变化；索引已更新，来源保留')
            self.check_removal(src,plan['id']);src.unlink();self.sync_dir(src.parent)
            manifest['phase']='source_removed';self.write(folder/'manifest.json',manifest)
        self.verify(plan)

    @staticmethod
    def sync_dir(path):
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def verify(self,plan,source_cleanup=True):
        expected=dict(plan['original_row'],rollout_path=plan['destination'])
        if self.store.row(plan['id'])!=expected:raise upstream.OperationError('操作后索引字段校验失败')
        if self.digest(Path(plan['destination']))!=plan['history_evidence']['sha256']:raise upstream.OperationError('操作后历史内容校验失败')
        before=[r for r in plan['related_records'] if not(r[0]==self.store.db_path.name and r[1]=='threads')]
        after=[r for r in self.owner.scoped_records(plan['ids']) if not(r[0]==self.store.db_path.name and r[1]=='threads')]
        if before!=after:raise upstream.OperationError('操作后关联记录发生变化，请核对备份')
        if source_cleanup and plan['action']=='migrate' and not plan['keep_source'] and os.path.lexists(plan['source']):raise upstream.OperationError('索引已更新但来源清理未完成')
