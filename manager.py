"""Codex Conversation Manager: indexed read model + explicit maintenance operations.
UI and official CLI adapter reused from leungWHu/codex-history-manager (MIT).
Explicit opt-in Agent analysis receives redacted diagnostics only. No silent backend switching.
"""
from __future__ import annotations
import argparse
import sys
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import sqlite3
import subprocess
import threading
import queue
import time
import uuid
import webbrowser
from history_index import scan_rollouts, history_references, dependency_order
from datetime import datetime, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from contextlib import contextmanager, closing
import fcntl
import server as upstream
from agent_repair import AgentRepairService, CodexAnalysisRunner
from desktop_sync import DesktopSync
from project_catalog import ProjectCatalog
from history_maintenance import HistoryMaintenance
from project_maintenance import ProjectMaintenance
from merge_stream import StreamingMerge
from project_assignment import ProjectAssignment

VERSION = '0.2.0+17'
MAX_SCAN = 1024 * 1024 * 1024
REQUIRED = {'id', 'rollout_path', 'title', 'cwd', 'created_at', 'updated_at', 'archived', 'has_user_event', 'source'}


def atomic_json(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode()
    tmp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    with tmp.open('xb') as f:
        os.chmod(tmp, 0o600)
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def stamp(path):
    try:
        s = path.stat()
        return [str(path.resolve()), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]
    except FileNotFoundError:
        return None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else ''


def runtime_processes():
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,comm='], capture_output=True, text=True, timeout=5, check=True)
    found = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2: continue
        pid, name = parts
        base = Path(name).name.lower()
        if base in {'codex', 'codex-app-server', 'chatgpt'} or 'codex helper' in base or 'chatgpt helper' in base:
            found.append({'pid': int(pid), 'name': Path(name).name})
    return found


def resolve_maintenance_command(explicit=None, environ=None, desktop_roots=None):
    """One engine per connection. Never retry a mutation through another engine."""
    env = os.environ if environ is None else environ
    requested = explicit or env.get('CODEX_BINARY') or env.get('CODEX_CLI_PATH')
    if requested:
        found = shutil.which(str(Path(requested).expanduser()), path=env.get('PATH'))
        if not found:
            raise upstream.OperationError(f'指定的 Codex 引擎不存在或不可执行：{requested}；请修正路径后重新连接')
        return str(Path(found).resolve())
    roots = desktop_roots if desktop_roots is not None else (
        [Path('/Applications'), Path.home()/'Applications'] if sys.platform == 'darwin' else [])
    for root in roots:
        for app in ('Codex.app', 'ChatGPT.app'):
            for relative in ('Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex', 'Contents/Resources/codex'):
                candidate = Path(root)/app/relative
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate.resolve())
    found = shutil.which('codex', path=env.get('PATH'))
    if not found:
        raise upstream.OperationError('未找到 Codex 引擎；请安装 Codex 桌面端或指定 CODEX_BINARY')
    return str(Path(found).resolve())


class OfficialRPCError(upstream.OperationError):
    def __init__(self, method, code, message):
        self.method, self.code = method, code
        super().__init__(f'Codex {method} 失败（{code}）：{str(message)[:2000]}')


class OfficialController(upstream.CodexController):
    """Respect the initialize response barrier; keep stdin open until RPC reply."""
    def __init__(self, home, command=None, sqlite_home=None):
        super().__init__(home, resolve_maintenance_command(command))
        self.sqlite_home=Path(sqlite_home or home).expanduser().resolve()
        self._engine_info = None
        self.delete_timeout = 600.0
        self.rpc_timeout = 90.0
        self.sample_after = 30.0
        self._observation = threading.local()

    @contextmanager
    def observing(self, callback):
        previous=getattr(self._observation,'callback',None)
        self._observation.callback=callback
        try: yield
        finally: self._observation.callback=previous

    @contextmanager
    def diagnostics(self, folder):
        previous=getattr(self._observation,'diagnostic_folder',None)
        self._observation.diagnostic_folder=Path(folder)
        try:yield
        finally:self._observation.diagnostic_folder=previous

    def report(self, phase, **detail):
        callback=getattr(self._observation,'callback',None)
        if callback:callback(phase,**detail)

    def engine_info(self):
        if self._engine_info is None:
            result = subprocess.run([self._require_command(), '--version'], env=self._environment(),
                                    capture_output=True, text=True, timeout=10, check=False)
            if result.returncode:
                raise upstream.OperationError('Codex 引擎版本检查失败，请检查所选引擎后重新连接')
            self._engine_info = {'binary': self.command, 'version': result.stdout.strip()[:120],
                                 'delete_transport': 'app-server:thread/delete'}
        return dict(self._engine_info)

    def delete(self, session_id):
        self.validate_id(session_id)
        with self.exclusive():
            self._rpc_unlocked('thread/delete', {'threadId': session_id})

    def _environment(self):
        env=super()._environment();env['CODEX_SQLITE_HOME']=str(self.sqlite_home)
        for k in ('CODEX_THREAD_ID','CODEX_SESSION_ID'):env.pop(k,None)
        return env

    def _rpc_unlocked(self, method, params):
        with self.rpc_session() as call:
            return call(method, params)

    @contextmanager
    def rpc_session(self, stream_history_path=None):
        self.report('engine_start', message='正在启动 Codex 官方维护引擎')
        command=self._require_command()
        diagnostic_folder=getattr(self._observation,'diagnostic_folder',None)
        env=self._environment()
        env['RUST_LOG']='codex_thread_store=debug,codex_rollout=debug,codex_state=warn'
        started=time.monotonic();events=[];samples=[]
        def event(kind, **fields):
            events.append({'event':kind,'elapsed_seconds':round(time.monotonic()-started,3),**fields})
        process=subprocess.Popen([command,'app-server','--listen','stdio://'],stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                                 encoding='utf-8',errors='replace',bufsize=1,env=env)
        event('process_started',pid=process.pid)
        def stderr_reader():
            written=0
            log=(diagnostic_folder/'official-stderr.log').open('xb') if diagnostic_folder else None
            if log:os.chmod(log.name,0o600)
            try:
                while chunk:=process.stderr.readline(65536):
                    chunk=re.sub(r'(?i)(bearer\s+)[^\s"\x27]+',r'\1[redacted]',chunk)
                    chunk=re.sub(r'\bsk-[A-Za-z0-9_-]+','[redacted]',chunk)
                    if log and written<2*1024*1024:
                        part=chunk.encode('utf-8')[:2*1024*1024-written].decode('utf-8',errors='ignore').encode('utf-8');log.write(part);log.flush();written+=len(part)
            finally:
                if log:log.close()
        errors_worker=threading.Thread(target=stderr_reader,daemon=True);errors_worker.start()
        inbox=queue.Queue()
        def reader():
            try:
                if stream_history_path is not None:
                    from stream_json import responses
                    for value in responses(process.stdout,stream_history_path):inbox.put(value)
                else:
                    for line in process.stdout:
                        try: inbox.put(json.loads(line))
                        except ValueError: pass
            except Exception as exc:inbox.put(exc)
            finally: inbox.put(None)
        worker=threading.Thread(target=reader,daemon=True);worker.start()
        deadline=time.monotonic()+30
        def send(message):
            process.stdin.write(json.dumps(message)+'\n');process.stdin.flush()
        def receive(i, method):
            nonlocal sampled
            wait_started=time.monotonic()
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    event('response_timeout',method=method)
                    raise upstream.OperationError(f'官方 {method} 响应超时；结果未确认，已停止后续操作，请检查备份与状态；未自动重试')
                elapsed=time.monotonic()-wait_started
                if method=='thread/delete' and elapsed>=self.sample_after and diagnostic_folder and not sampled:
                    sampled=True
                    sample_path=diagnostic_folder/'official-process.sample.txt'
                    if Path('/usr/bin/sample').exists():
                        samples.append(subprocess.Popen(['/usr/bin/sample',str(process.pid),'1','-file',str(sample_path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
                        event('process_sample_requested',pid=process.pid)
                self.report('engine_start' if method=='initialize' else 'official_rpc',
                            rpc_method=method, wait_remaining_seconds=max(0,int(remaining)),engine_pid=process.pid,
                            rpc_elapsed_seconds=round(elapsed),diagnostics_available=diagnostic_folder is not None,
                            message='等待 Codex 核对历史引用并执行删除' if method=='thread/delete' else '等待 Codex 官方响应')
                try:value=inbox.get(timeout=min(1,remaining))
                except queue.Empty:continue
                if isinstance(value,Exception):raise upstream.OperationError('官方响应读取失败：'+str(value))
                if value is None:raise upstream.OperationError('官方 app-server 在回复前退出')
                if value.get('id')==i:
                    event('response_received',method=method,success='error' not in value)
                    if 'error' in value:
                        error = value['error']
                        raise OfficialRPCError(method, error.get('code', 'unknown'), error.get('message', '官方 RPC 失败'))
                    return value.get('result')
                if 'id' in value and 'method' in value:
                    send({'id':value['id'],'error':{'code':-32601,'message':'This client supports maintenance RPC only'}})
        next_id = 1
        sampled=False
        def call(method, params):
            nonlocal next_id, deadline
            next_id += 1; deadline = time.monotonic() + (self.delete_timeout if method=='thread/delete' else self.rpc_timeout)
            event('request_sent',method=method,timeout_seconds=self.delete_timeout if method=='thread/delete' else self.rpc_timeout)
            send({'id':next_id,'method':method,'params':params})
            return receive(next_id, method)
        try:
            send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'codex_conversation_manager','version':VERSION},'capabilities':{'experimentalApi':True}}})
            receive(1, 'initialize');send({'method':'initialized','params':{}})
            yield call
        finally:
            self.report('engine_cleanup',message='正在关闭本次维护引擎')
            if process.stdin:process.stdin.close()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            worker.join(timeout=1)
            errors_worker.join(timeout=2)
            if process.stdout:process.stdout.close()
            if process.stderr:process.stderr.close()
            for sample in samples:
                try:sample.wait(timeout=3)
                except subprocess.TimeoutExpired:sample.terminate();sample.wait()
            event('process_exited',return_code=process.returncode)
            if diagnostic_folder:
                for sample_path in diagnostic_folder.glob('official-process.sample.txt'):os.chmod(sample_path,0o600)
                atomic_json(diagnostic_folder/'official-events.json',{'events':events,'delete_timeout_seconds':self.delete_timeout,'stderr_byte_limit':2*1024*1024})


class IndexedStore:
    def __init__(self, home, sqlite_home=None):
        self.codex_home = Path(home).expanduser().resolve()
        self.sqlite_home = Path(sqlite_home or home).expanduser().resolve()
        paths = sorted(self.sqlite_home.glob('state_*.sqlite'))
        if len(paths) != 1:
            raise upstream.OperationError('需要唯一的 state_*.sqlite；请明确选择当前 Codex 数据库目录，未进行自动猜测')
        self.db_path = paths[0]
        with self.db() as c:
            fields = {r['name'] for r in c.execute('PRAGMA table_info(threads)')}
            if not REQUIRED <= fields: raise upstream.OperationError('threads 表结构不受支持，已停止读取')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        try: yield c
        finally: c.close()

    def row(self, sid):
        upstream.CodexController.validate_id(sid)
        with self.db() as c:
            r = c.execute('SELECT * FROM threads WHERE id=?', (sid,)).fetchone()
        if r is None: raise FileNotFoundError('会话记录不存在')
        return dict(r)

    def project_catalog(self):
        with self.db() as c:
            return ProjectCatalog(self.codex_home,c,use_desktop=self.sqlite_home==self.codex_home)

    def listed(self, r, catalog=None):
        path_error=None;exists=False;size=None
        try: p=self.path(r)
        except upstream.OperationError as exc: path_error=str(exc)
        else:
            try:
                stat=p.stat();exists=p.is_file();size=stat.st_size if exists else None
            except FileNotFoundError: pass
        return (catalog or self.project_catalog()).resolve(r) | {'id': r['id'], 'title': r.get('name') or r['title'] or '未命名会话',
                'cwd': r['cwd'], 'created_at': iso(r['created_at']), 'updated_at': iso(r['updated_at']),
                'is_archived': bool(r['archived']), 'is_temporary': upstream.is_temporary_workspace(r['cwd']),
                'message_count': None, 'local_path': r['rollout_path'], 'local_path_exists': exists, 'path_error': path_error,
                'preview_text': (r.get('first_user_message') or '')[:500],
                'storage_restricted': bool(self.storage_boundary_issue(r)) if exists else False,
                'file_bytes': size,
                'history_mode': r.get('history_mode', 'legacy'), 'model': r.get('model'),
                'model_provider': r.get('model_provider'), 'visibility_flag': r['has_user_event']}

    def list(self, query='', limit=1000, scope='all', catalog=None):
        catalog=catalog or self.project_catalog()
        with self.db() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM threads ORDER BY updated_at DESC')]
        rows = [r for r in rows if not (r.get('thread_source') == 'subagent' or 'subagent' in r['source'])]
        if scope == 'active': rows = [r for r in rows if not r['archived']]
        elif scope == 'archived': rows = [r for r in rows if r['archived']]
        q = query.casefold().strip()
        if q: rows = [r for r in rows if q in ' '.join(str(r.get(k) or '') for k in ('id','name','title','cwd','first_user_message')).casefold()]
        if limit < 0: raise ValueError('读取数量无效')
        return [self.listed(r,catalog) for r in (rows if limit == 0 else rows[:limit])], len(rows)

    def path(self, r):
        p = Path(r['rollout_path']).expanduser()
        if not p.is_absolute() or not p.name.startswith('rollout-') or not p.name.endswith('.jsonl') or r['id'] not in p.name:
            raise upstream.OperationError('历史路径与会话 ID 不匹配，停止读取')
        return p

    def storage_boundary_issue(self, row):
        path = self.path(row)
        if not path.is_file(): return None
        root = self.codex_home/('archived_sessions' if row['archived'] else 'sessions')
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            return ('历史文件位于 Codex 标准目录之外，可能来自目录软链接或外置迁移。这是存储位置限制，不是内容损坏。'
                    '删除可直接备份后执行，无需归位；归档前请选择「归位历史」。月份软链接保持不变。'
                    f'\n索引路径：{path}\n真实路径：{resolved}')
        return None

    def preview(self, p, sid):
        # Preview is explicitly bounded, never advertised as complete history.
        with p.open('rb') as f:
            head = f.readline(2 * 1024 * 1024)
            first = json.loads(head)
            if first.get('type') != 'session_meta' or first.get('payload', {}).get('id') != sid:
                raise upstream.OperationError('文件头会话 ID 不匹配')
            size = p.stat().st_size
            truncated = size > 2 * 1024 * 1024
            f.seek(max(0, size - 2 * 1024 * 1024))
            if truncated: f.readline()
            messages, malformed = [], 0
            for line in f:
                try:
                    e = json.loads(line)
                    if not isinstance(e, dict): raise ValueError('record')
                except (ValueError, UnicodeError): malformed += 1; continue
                v = e.get('payload', {})
                if not isinstance(v, dict): continue
                if e.get('type') != 'response_item' or v.get('type') != 'message' or v.get('role') not in {'user', 'assistant'}: continue
                text = upstream.extract_text(v.get('content'), exclude_internal=v['role'] == 'user')
                if not text: continue
                phase = v.get('phase', '')
                messages.append({'role':v['role'],'text':text[:50000], 'timestamp':e.get('timestamp',''),
                                 'phase':phase,'kind':'user' if v['role']=='user' else ('progress' if phase=='commentary' else 'final')})
            return messages[-80:], truncated or len(messages)>80 or any(len(m['text'])>=50000 for m in messages), malformed

    def get(self, sid):
        r = self.row(sid); result = self.listed(r); p = self.path(r)
        result.update(messages=[], local_path_exists=p.is_file(), preview_limited=False, preview_errors=0)
        if p.is_file():
            messages, limited, errors = self.preview(p, sid)
            result.update(messages=messages, message_count=len(messages), preview_limited=limited, preview_errors=errors)
        return result

    def graph(self):
        with self.db() as c:
            c.execute('BEGIN')
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone():
                raise upstream.OperationError('缺少子线程关系表，停止生成级联操作计划')
            known={r[0] for r in c.execute('SELECT id FROM threads')}
            children={}
            for parent,child in c.execute('SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges'):
                children.setdefault(parent,set()).add(child)
        return children,known

    def descendants(self, ids, graph=None):
        children,known=graph if graph is not None else self.graph()
        found=set(ids); pending=list(ids)
        while pending:
            for child in children.get(pending.pop(),()):
                if child not in found: found.add(child);pending.append(child)
        if not found <= known: raise FileNotFoundError('关联会话记录不存在，停止生成级联计划')
        return sorted(found)

    def fingerprint(self, ids):
        values=[]
        for sid in ids:
            r=self.row(sid); values.append([r, stamp(self.path(r))])
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def candidates(self, sid):
        result=[]
        for name in upstream.SESSION_DIR_NAMES:
            root=(self.codex_home/name).resolve()
            if not root.is_dir(): continue
            for folder, dirs, files in os.walk(root, followlinks=False):
                for filename in files:
                    if sid in filename and filename.startswith('rollout-') and filename.endswith('.jsonl'):
                        p=(Path(folder)/filename).resolve()
                        if p.is_relative_to(root) and p not in result: result.append(p)
        return result

    def diagnose(self, sid):
        r=self.row(sid); p=self.path(r); issues=[]; changes={}; selected=None
        if not p.is_file():
            matches=self.candidates(sid)
            issues.append({'level':'error','code':'missing_file','text':'数据库记录的历史路径不存在。'})
            if len(matches)==1:
                selected=matches[0]
                changes['rollout_path']=str(selected)
                issues.append({'level':'repair','code':'stale_path','text':'找到唯一同 ID 历史文件，可修正数据库路径。'})
            elif len(matches)>1:
                issues.append({'level':'error','code':'ambiguous','text':'存在多个候选历史文件，保留现场，不自动选取。'})
        else: selected=p
        scan={'lines':0,'invalid_lines':[], 'complete':False, 'sha256':None, 'user_event':False}
        if selected:
            before=stamp(selected)
            if selected.stat().st_size>MAX_SCAN:
                issues.append({'level':'warning','code':'oversize','text':'文件超过 1 GiB，本轮未完整扫描；修复保持禁用。'})
            else:
                h=hashlib.sha256(); meta_ok=False; first_record=True
                with selected.open('rb') as f:
                    for n,line in enumerate(f,1):
                        h.update(line); scan['lines']=n
                        if not line.strip(): continue
                        try:
                            e=json.loads(line)
                            if not isinstance(e,dict): raise ValueError('record')
                            v=e.get('payload',{})
                            if not isinstance(v,dict): v={}
                            if first_record:
                                meta_ok=e.get('type')=='session_meta' and v.get('id')==sid;first_record=False
                            if e.get('type')=='response_item' and v.get('type')=='message' and v.get('role')=='user':
                                text=upstream.extract_text(v.get('content'),exclude_internal=True)
                                if text and not text.startswith('# AGENTS.md instructions'): scan['user_event']=True
                        except (ValueError,UnicodeError):
                            if len(scan['invalid_lines'])<30: scan['invalid_lines'].append(n)
                scan.update(complete=True,sha256=h.hexdigest())
                if before!=stamp(selected):
                    scan['complete']=False; changes={}
                    issues.append({'level':'info','code':'live_history','text':'会话正在写入，本次检查未形成稳定快照，请稍后重查。'})
                if not meta_ok: issues.append({'level':'error','code':'id_mismatch','text':'首行元数据与会话 ID 不一致；禁用自动修复。'})
                if scan['invalid_lines']: issues.append({'level':'error' if scan['complete'] else 'info','code':'invalid_jsonl','text':'发现无效 JSONL 记录，已列出行号；未跳过记录执行修复。' if scan['complete'] else '写入中的快照含不完整记录，等待会话稳定后复查。'})
                if meta_ok and not scan['invalid_lines']:
                    if not r['has_user_event'] and scan['user_event'] and 'subagent' not in r['source'] and r.get('thread_source')!='subagent':
                        issues.append({'level':'info','code':'hidden_thread','text':'可见性标志为 0；这是索引状态，单凭该标志和消息存在不足以判定损坏，不自动改写。'})
                else: changes={}
        if not scan['complete']: changes={}
        if selected and 'rollout_path' in changes:
            archived_root=(self.codex_home/'archived_sessions').resolve()
            if selected.resolve().is_relative_to(archived_root)!=bool(r['archived']):
                issues.append({'level':'error','code':'archive_mismatch','text':'候选文件位置与归档状态不一致，停止自动修复。'});changes={}
        if selected and scan['complete'] and not scan['invalid_lines'] and not any(i['code']=='id_mismatch' for i in issues):
            issues.append({'level':'ok','code':'history_valid','text':f"历史 JSONL 结构校验完成，共 {scan['lines']} 行。"})
        if r.get('history_mode')=='paginated':
            hp=self.sqlite_home/'thread_history_1.sqlite'
            if not hp.exists():
                issues.append({'level':'warning','code':'missing_projection','text':'分页历史数据库不存在；本版不重建分页历史。'});changes={}
            else:
                with closing(sqlite3.connect(hp.as_uri()+'?mode=ro',uri=True)) as c, c:
                    state=c.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state WHERE thread_id=?',(sid,)).fetchone()
                if state and selected and state[0]>selected.stat().st_size:
                    issues.append({'level':'warning','code':'projection_ahead','text':'分页投影偏移超过文件大小；本版保留投影现场，不直接重置。'});changes={}
        boundary = self.storage_boundary_issue(r)
        if boundary:
            issues.append({'level':'info','code':'external_storage_path','text':'历史位于标准目录之外；内容检查与存储位置独立。可以直接备份并删除外置原文件；归档前仍需归位。'}); changes={}
        health = ('repairable' if changes else 'missing' if selected is None else 'incomplete' if not scan['complete'] else 'corrupt' if any(i['code'] in {'invalid_jsonl','id_mismatch'} for i in issues) else 'healthy')
        return {'health':health,'storage_restricted':bool(boundary),'id':sid,'title':r.get('name') or r['title'],'history_mode':r.get('history_mode','legacy'),
                'issues':issues,'scan':scan,'changes':changes,'candidate':str(selected) if selected else None,
                'fingerprint':self.fingerprint([sid]),
                'boundaries':['本地结构正常不代表远端 HTTP 400 已修复。','notLoaded 可能只是未加载状态；本工具不把它当作文件损坏。','不删除消息、不清理凭证、不替换会话 ID、不改项目文件。']}


class Maintenance:
    def __init__(self, store, controller, data_dir, runtime_probe=runtime_processes):
        self.store=store; self.controller=controller; self.root=Path(data_dir).resolve()
        self.root.mkdir(parents=True,exist_ok=True); os.chmod(self.root,0o700)
        self.plans={}; self.lock=threading.Lock(); self.runtime_probe=runtime_probe
        self._observation=threading.local()
        self.desktop_sync=DesktopSync(store.codex_home,store.sqlite_home)
        self.history_paths=HistoryMaintenance(self,stamp,digest,atomic_json)
        self.project_maintenance=ProjectMaintenance(self,atomic_json,digest)
        self.merger=StreamingMerge(self,atomic_json,stamp,iso)
        self.project_assignment=ProjectAssignment(self,atomic_json,digest)

    @contextmanager
    def observing(self, callback, should_stop=lambda:False):
        previous=getattr(self._observation,'callback',None),getattr(self._observation,'should_stop',None)
        self._observation.callback=callback;self._observation.should_stop=should_stop
        try:yield
        finally:self._observation.callback,self._observation.should_stop=previous

    def report(self, phase, **detail):
        callback=getattr(self._observation,'callback',None)
        if callback:callback(phase,**detail)

    def stop_requested(self):
        check=getattr(self._observation,'should_stop',None)
        return bool(check and check())

    @contextmanager
    def exclusive(self):
        if not self.lock.acquire(False): raise upstream.OperationBusy('另一个维护操作正在运行')
        try:
            with (self.root/'operation.lock').open('a') as f:
                try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: raise upstream.OperationBusy('另一个管理器进程正在运行维护操作')
                yield
        finally: self.lock.release()

    def references(self):
        self.report('lookup',message='检查活动、归档及外置历史的分叉引用')
        paths=scan_rollouts([self.store.codex_home/name for name in ('sessions','archived_sessions')],self.report)
        with self.store.db() as c:
            indexed=[Path(r[0]) for r in c.execute('SELECT rollout_path FROM threads')]
        paths=sorted(set(paths)|{p.resolve() for p in indexed if p.is_file() and p.name.startswith('rollout-')})
        return history_references(paths)

    def assert_references(self, ids, allowed=None, references=None):
        refs=self.references() if references is None else references
        allowed=set(ids if allowed is None else allowed)
        blocking=[(child,parent) for child,parent in refs if parent in ids and child not in allowed]
        if blocking:
            names=[]
            for child,parent in blocking:
                try:r=self.store.row(child);name=r.get('name') or r['title'] or child[:8]
                except FileNotFoundError:name=child[:8]
                names.append(f'{name}（{child[:8]} → {parent[:8]}）')
            raise upstream.OperationError('仍有未选择的分叉历史引用：'+ '、'.join(names)+'。请先删除引用方，或同时选择后批量删除；未扩大范围')
        return sorted([list(edge) for edge in refs if edge[1] in ids])

    def plan(self, action, sid, title=None, target_path=None, keep_source=True):
        if action in {'migrate','link'}:return self.save_plan(self.history_paths.plan(action,sid,target_path,keep_source))
        return self.save_plan(self._build_plan(action,sid,title))

    def _build_plan(self, action, sid, title=None, ids=None, allowed_delete_ids=None, references=None):
        if action not in {'archive','unarchive','delete','repair','rename','relocate'}: raise ValueError('未知操作')
        r=self.store.row(sid)
        if sid==os.environ.get('CODEX_THREAD_ID'):raise upstream.OperationBusy('这是当前正在工作的对话，先结束任务再维护')
        ids=ids if ids is not None else (self.store.descendants([sid]) if action in {'archive','delete'} else [sid])
        if os.environ.get('CODEX_THREAD_ID') in ids: raise upstream.OperationBusy('操作范围包含当前任务')
        if action=='relocate':
            if not self.store.storage_boundary_issue(r): raise upstream.OperationError('此历史已在标准目录内，无需归位')
            d=self.store.diagnose(sid)
            if d['health']!='healthy': raise upstream.OperationError('请先处理历史内容或读取问题，再归位文件')
        if action in {'archive','unarchive'}:
            for target in ids:
                boundary = self.store.storage_boundary_issue(self.store.row(target))
                if boundary: raise upstream.OperationError(boundary)
        diagnostic=self.store.diagnose(sid) if action=='repair' else None
        if diagnostic and not diagnostic['changes']: raise upstream.OperationError('没有可自动应用的元数据修复；请查看诊断结论')
        if action=='rename' and (not isinstance(title,str) or not title.strip() or len(title)>200): raise ValueError('标题须为 1–200 个字符')
        token=secrets.token_urlsafe(32); phrase=f'{action}-{sid[:8]}'
        plan={'token':token,'action':action,'id':sid,'ids':ids,'title':title,'confirmation':phrase,
              'fingerprint':self.store.fingerprint(ids),'diagnostic':diagnostic,
              'targets':[{'id':i,'title':self.store.row(i).get('name') or self.store.row(i)['title']} for i in ids],
              'expires':time.monotonic()+300,'file_bytes':sum((self.store.path(self.store.row(i)).stat().st_size if self.store.path(self.store.row(i)).is_file() else 0) for i in ids)}
        if action=='delete':
            plan['delete_dependencies']=self.assert_references(ids,allowed_delete_ids,references)
            context=self.delete_context(ids)
            lookup_files=context.pop('lookup_inventory',None)
            plan.update(context)
            plan['file_bytes']=sum(f['stamp'][3] for f in plan['delete_files'])
            plan['external_delete']=bool(plan['delete_roots'])
            self.check_delete_lookup(ids,plan['delete_roots'],lookup_files)
        if action=='repair':
            plan['source']=r['rollout_path'];plan['destination']=diagnostic['changes'].get('rollout_path');plan['keep_source']=True
        if action=='relocate':
            root=(self.store.codex_home/('archived_sessions' if r['archived'] else 'sessions')).resolve()
            plan['destination']=str(root/('rethread-'+sid)/self.store.path(r).name)
        return plan

    def assert_storage_available(self, path):
        # Absence of a file is not absence of a disk or a broken directory alias.
        for parent in path.parents:
            if parent.is_symlink() and not parent.exists():
                raise upstream.OperationError(f'历史目录软链接当前离线：{parent}；请接回存储后再删除，尚未执行修改')
        resolved=path.resolve()
        if len(resolved.parts)>2 and resolved.parts[1]=='Volumes':
            volume=Path(*resolved.parts[:3])
            if not volume.is_mount():
                raise upstream.OperationError(f'历史所在磁盘未挂载：{volume}；保留会话记录，尚未执行修改')

    def delete_context(self, ids):
        rows=[self.store.row(i) for i in ids]
        files={};missing=[];inventory=None
        for row in rows:
            path=self.store.path(row);self.assert_storage_available(path)
            info=stamp(path)
            if info is not None:
                if not path.is_file():raise upstream.OperationError('历史路径不是普通文件')
                files[str(path.resolve())]={'id':row['id'],'source':str(path),'stamp':info}
            else:missing.append(row['id'])
        provisional=self.external_delete_roots(ids)
        if missing:
            self.report('lookup',message='查找缺失路径对应的同 ID 历史文件')
            # Search exact UUID filenames; do not guess an unrelated/fuzzy match or rewrite live rows.
            roots=[self.store.codex_home/name for name in ('sessions','archived_sessions')]
            roots += [Path(value) for value in provisional.values()]
            wanted=set(missing)
            inventory=scan_rollouts(roots,self.report)
            for path in inventory:
                match=re.fullmatch(r'rollout-.+-([0-9a-fA-F-]{36})\.jsonl(\.zst)?',path.name)
                if not match or match[1] not in wanted:continue
                if match[2]:raise upstream.OperationError('找到同 ID 的压缩历史；需先解压并核对备份，尚未删除')
                self.assert_storage_available(path);self.store.preview(path,match[1])
                files[str(path.resolve())]={'id':match[1],'source':str(path),'stamp':stamp(path)}
            missing=sorted(wanted-{f['id'] for f in files.values()})
        entries=sorted(files.values(),key=lambda f:(f['id'],f['source']))
        roots=self.external_delete_roots(ids,entries)
        return {'delete_files':entries,'missing_histories':missing,'delete_roots':roots,'lookup_inventory':inventory}

    def external_delete_roots(self, ids, files=None):
        """Select official storage roots before confirmation, never after a failed delete."""
        rows=[self.store.row(i) for i in ids]
        if files is not None:
            by_id={r['id']:r for r in rows}
            rows=[dict(by_id[f['id']],rollout_path=f['source']) for f in files]
        if not any(self.store.storage_boundary_issue(r) for r in rows):return {}
        roots={}
        for name,archived in (('sessions',False),('archived_sessions',True)):
            paths=[self.store.path(r).resolve(strict=True) for r in rows if bool(r['archived'])==archived and self.store.path(r).is_file()]
            if not paths:continue
            root=Path(os.path.commonpath([str(p.parent) for p in paths]))
            # Do not turn an entire filesystem/volume namespace into a history root.
            if root==Path(root.anchor) or root in {Path('/Users'),Path('/Volumes'),Path.home()} or (root.parent==Path('/Volumes')):
                raise upstream.OperationError('关联历史跨越多个存储根目录，请分别处理这些会话；未扩大删除目录范围')
            if not root.is_dir():raise upstream.OperationError('外置历史目录未就绪')
            roots[name]=str(root)
        return roots

    def check_delete_lookup(self, ids, roots, inventory=None):
        # The official locator may fuzzy-search the opposite storage category when the exact
        # UUID is absent. Reject ambiguity BEFORE invoking it; keep its complete reference
        # scan and live-writer locks intact rather than hiding other histories from the engine.
        rows=[self.store.row(i) for i in ids]
        for name,archived in (('sessions',False),('archived_sessions',True)):
            queries=[r['id'] for r in rows if bool(r['archived'])!=archived or not self.store.path(r).is_file()]
            root=Path(roots[name]) if name in roots else (None if roots else self.store.codex_home/name)
            if not queries or root is None or not root.is_dir():continue
            exact=set();ambiguous=set()
            candidates=scan_rollouts([root],self.report) if inventory is None else [p for p in inventory if p.is_relative_to(root.resolve())]
            for candidate in candidates:
                filename=candidate.name
                for sid in queries:
                    if sid in filename:exact.add(sid);continue
                    chars=iter(str(candidate).casefold())
                    if all(char in chars for char in sid.casefold()):ambiguous.add(sid)
            if ambiguous-exact:
                raise upstream.OperationError('官方历史查找可能将相近 ID 匹配到范围外文件，已在删除前停止；未修改任何文件。请保留该会话并检查历史文件名。')

    def delete_history(self, plan, folder, manifest):
        # Full exact-ID discovery already ran immediately before backup. Revalidate the
        # approved files and storage aliases here, without repeating a whole-disk discovery.
        for sid in plan['ids']:self.assert_storage_available(self.store.path(self.store.row(sid)))
        if any(stamp(Path(f['source']))!=f['stamp'] for f in plan['delete_files']):
            raise upstream.OperationBusy('备份后的历史文件已变化，请重新预检')
        self.assert_references(plan['ids'])
        roots=self.external_delete_roots(plan['ids'],plan['delete_files'])
        if roots!=plan.get('delete_roots',{}):raise upstream.OperationBusy('历史存储根目录已变化，请重新预检')
        self.report('lookup',message='核对官方查找路径与删除范围')
        self.check_delete_lookup(plan['ids'],roots)
        if not roots:
            with self.controller.observing(self.report),self.controller.diagnostics(folder):self.controller.delete(plan['id'])
            return
        # Dedicated CODEX_HOME contains root aliases, not copies or rewritten live rows.
        # CODEX_SQLITE_HOME still points at the selected real state/history databases.
        home=folder/'official-delete-home';home.mkdir(mode=0o700)
        aliases=[]
        try:
            for name in ('sessions','archived_sessions'):
                alias=home/name
                if name in roots:
                    alias.symlink_to(roots[name],target_is_directory=True);aliases.append(alias)
                else:alias.mkdir(mode=0o700)
            manifest['delete_transport']='official-app-server:thread/delete:external-roots'
            manifest['delete_roots']=roots;atomic_json(folder/'manifest.json',manifest)
            if self.store.fingerprint(plan['ids'])!=plan['fingerprint'] or self.store.descendants([plan['id']])!=plan['ids']:
                raise upstream.OperationBusy('官方删除前记录或关联范围已变化，未执行')
            if any(digest(Path(f['source']))!=f['sha256'] for f in manifest['files']):
                raise upstream.OperationBusy('官方删除前历史内容已变化，未执行')
            if self.external_delete_roots(plan['ids'],plan['delete_files'])!=roots:raise upstream.OperationBusy('存储路径发生变化，未执行')
            controller=OfficialController(home,self.controller.command,sqlite_home=self.store.sqlite_home)
            with self.controller.exclusive(),controller.observing(self.report),controller.diagnostics(folder):controller.delete(plan['id'])
        finally:
            # Unlink ONLY our temporary aliases. Never traverse or remove their target folders.
            for alias in aliases:
                if alias.is_symlink():alias.unlink()
            manifest['temporary_aliases_removed']=all(not a.is_symlink() for a in aliases)
            atomic_json(folder/'manifest.json',manifest)

    def backup(self, plan):
        folder=self.root/'backups'/f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{plan['action']}-{uuid.uuid4().hex[:8]}"
        folder.mkdir(parents=True,mode=0o700)
        # Only scoped history/metadata; no auth/config or whole multi-GB databases.
        records=[self.store.row(i) for i in plan['ids']]
        paths=([Path(f['source']) for f in plan['delete_files']] if plan['action']=='delete' and 'delete_files' in plan else [self.store.path(r) for r in records])
        d=plan.get('diagnostic')
        if d and d.get('candidate'): paths.append(Path(d['candidate']))
        if plan.get('history_file'):paths.append(Path(plan['history_file']))
        paths=list(dict.fromkeys(paths))
        needed=sum(p.stat().st_size for p in paths if p.is_file())
        if shutil.disk_usage(folder).free < needed+64*1024*1024: raise upstream.OperationError('备份磁盘空间不足，尚未执行修改')
        manifest={'version':1,'action':plan['action'],'ids':plan['ids'],'rows':records,'files':[],
                  'codex_home':str(self.store.codex_home),'database':str(self.store.db_path),
                  'created_at':datetime.now(timezone.utc).isoformat(), 'status':'preparing',
                  'missing_histories':plan.get('missing_histories',[]),
                  'engine':self.controller.engine_info(),
                  'scope':'Scoped rollout files and related state/history/memory/goal/queue rows. No credentials. No automatic global database restore.'}
        atomic_json(folder/'manifest.json',manifest)
        copied=0
        self.report('backup',message='备份历史文件',bytes_done=0,bytes_total=needed)
        for n,p in enumerate(paths):
            if not p.exists(): continue
            original=stamp(p); self.store.preview(p, next(r['id'] for r in records if r['id'] in p.name))
            target=folder/f'rollout-{n}.jsonl';source_hash=hashlib.sha256()
            with p.open('rb') as source,target.open('xb') as dest:
                os.chmod(target,0o600)
                for chunk in iter(lambda:source.read(1024*1024),b''):
                    dest.write(chunk);source_hash.update(chunk);copied+=len(chunk)
                    self.report('backup',message='备份历史文件',bytes_done=copied,bytes_total=needed)
                dest.flush();os.fsync(dest.fileno())
            self.report('backup_verify',message='校验备份与原始历史内容')
            checksum=digest(target)
            if original!=stamp(p) or checksum!=source_hash.hexdigest() or checksum!=digest(p): raise upstream.OperationBusy('备份期间历史发生变化，尚未执行修改')
            manifest['files'].append({'source':str(p),'backup':target.name,'sha256':checksum,'bytes':target.stat().st_size})
        # Scoped SQLite snapshot is deliberately not a replacement of the live DB.
        self.report('metadata_backup',message='备份本次会话的关联记录')
        snap=folder/'metadata.sqlite'
        with closing(sqlite3.connect(snap)) as dest, dest:
            dest.execute('CREATE TABLE records (database_name TEXT, table_name TEXT, row_json TEXT)')
            for record in self.scoped_records(plan['ids']):
                dest.execute('INSERT INTO records VALUES (?,?,?)', record)
        os.chmod(snap,0o600)
        manifest['metadata_sha256']=digest(snap);manifest['status']='backed_up';atomic_json(folder/'manifest.json',manifest)
        return folder,manifest

    def scoped_records(self, ids):
        records = []
        if not ids: return records
        databases = [self.store.db_path] + [self.store.sqlite_home/name for name in (
            'thread_history_1.sqlite', 'memories_1.sqlite', 'goals_1.sqlite', 'queue_1.sqlite')]
        for database in databases:
            if not database.exists(): continue
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as c, c:
                c.row_factory = sqlite3.Row
                c.execute('CREATE TEMP TABLE rethread_scope (id TEXT PRIMARY KEY) WITHOUT ROWID')
                c.executemany('INSERT INTO rethread_scope VALUES (?)',((i,) for i in set(ids)))
                tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    if not re.fullmatch(r'[a-zA-Z0-9_]+', table): continue
                    cols = [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
                    keys = [k for k in cols if k in {'thread_id','parent_thread_id','child_thread_id'}
                            or (table=='threads' and k=='id') or (table=='jobs' and k=='job_key')]
                    if not keys: continue
                    where = ' OR '.join(f'"{k}" IN (SELECT id FROM rethread_scope)' for k in keys)
                    for row in c.execute(f'SELECT * FROM "{table}" WHERE {where}'):
                        records.append((database.name, table, json.dumps(dict(row), ensure_ascii=False, sort_keys=True)))
        return sorted(records)

    def unchanged_since_backup(self, plan, folder, manifest):
        if plan['action']=='migrate' and Path(plan['destination']).parent.exists():return False
        if self.store.fingerprint(plan['ids']) != plan['fingerprint']: return False
        if any(not Path(f['source']).is_file() or digest(Path(f['source'])) != f['sha256'] for f in manifest['files']):
            return False
        with closing(sqlite3.connect((folder/'metadata.sqlite').as_uri()+'?mode=ro', uri=True)) as c, c:
            before = sorted(c.execute('SELECT database_name, table_name, row_json FROM records').fetchall())
        return before == self.scoped_records(plan['ids'])

    def execute(self, token, confirmation, progress=None):
        with self.exclusive():
            plan=self.plans.get(token)
            if plan and plan['action']=='project_delete': return self.project_maintenance.execute(token,confirmation,progress)
            if plan and plan['action']=='merge': return self.execute_merge(token,confirmation,progress)
            if plan and plan['action'] in {'project_assign','project_create'}: return self.project_assignment.execute(token,confirmation,progress)
            if plan and plan.get('batch'): return self.execute_batch(token,confirmation,progress)
            return self._execute_single(token,confirmation)

    def _execute_single(self, token, confirmation):
        return self._execute_prepared(self.plans.pop(token,None),confirmation)

    def _execute_prepared(self, plan, confirmation, pre_mutation=None):
            if not plan or plan['expires']<time.monotonic(): raise upstream.OperationError('计划过期或已使用，请重新预检')
            if not secrets.compare_digest(plan['confirmation'],str(confirmation)): raise upstream.OperationError('确认文本不匹配，未执行操作')
            if plan['action'] in {'archive','delete'} and self.store.descendants([plan['id']])!=plan['ids']:
                raise upstream.OperationBusy('子线程范围已变化，请重新生成计划')
            if self.store.fingerprint(plan['ids'])!=plan['fingerprint']: raise upstream.OperationBusy('会话已变化，请重新生成计划')
            if plan['action'] in {'repair','relocate','migrate','link'} and self.runtime_probe(): raise upstream.OperationBusy('请先结束任务并完全退出 Codex / ChatGPT 及 Codex CLI，再应用元数据修复；当前仅诊断')
            if plan['action'] in {'archive','unarchive'}:
                for target in plan['ids']:
                    boundary = self.store.storage_boundary_issue(self.store.row(target))
                    if boundary: raise upstream.OperationError(boundary)
            if plan['action']=='delete':
                current=self.delete_context(plan['ids']);current.pop('lookup_inventory',None)
                if any(current[k]!=plan.get(k) for k in current):raise upstream.OperationBusy('历史文件范围已变化，请重新预检；未执行删除')
            self.report('backup',message='准备备份')
            folder,manifest=self.backup(plan)
            try:
                if self.store.fingerprint(plan['ids'])!=plan['fingerprint']: raise upstream.OperationBusy('备份后状态已变化，未执行修改')
                if plan['action'] in {'archive','delete'} and self.store.descendants([plan['id']])!=plan['ids']:
                    raise upstream.OperationBusy('备份后子线程范围已变化，未执行修改')
                if pre_mutation:pre_mutation()
                action=plan['action']; sid=plan['id']
                if action=='repair':
                    if self.runtime_probe(): raise upstream.OperationBusy('检测到 Codex 进程重新启动，未执行修改')
                    current=self.store.diagnose(sid)
                    expected=plan['diagnostic']
                    if current['changes']!=expected['changes'] or current['scan']['sha256']!=expected['scan']['sha256']:
                        raise upstream.OperationBusy('修复依据已变化，请重新预检')
                    changes=current['changes']
                    with closing(sqlite3.connect(self.store.db_path,timeout=5)) as c, c:
                        c.execute('BEGIN IMMEDIATE')
                        row=c.execute('SELECT * FROM threads WHERE id=?',(sid,)).fetchone()
                        keys=[x[1] for x in c.execute('PRAGMA table_info(threads)')]
                        if dict(zip(keys,row))!=manifest['rows'][0]:raise upstream.OperationBusy('数据库记录已变化')
                        if not set(changes)<={'rollout_path','has_user_event'}:raise upstream.OperationError('修复字段超出许可范围')
                        c.execute('UPDATE threads SET '+','.join(f'{key}=?' for key in changes)+' WHERE id=?',list(changes.values())+[sid])
                    actual=self.store.row(sid)
                    if not all(actual[k]==v for k,v in changes.items()):raise upstream.OperationError('修复后验证失败')
                    if digest(Path(current['candidate']))!=expected['scan']['sha256']:raise upstream.OperationError('修复后历史校验失败')
                elif action in {'migrate','link'}:self.history_paths.execute(plan,folder,manifest)
                elif action=='relocate': self.relocate(plan,folder,manifest)
                elif action=='archive': self.controller.set_archived(sid,True)
                elif action=='unarchive': self.controller.set_archived(sid,False)
                elif action=='delete': self.delete_history(plan,folder,manifest)
                elif action=='rename': self.controller.rename(sid,plan['title'])
                self.report('verification',message='核对删除结果与原历史文件' if action=='delete' else '核对操作结果')
                self.verify(plan)
                if action=='delete':
                    manifest['retained_bookkeeping']=self.delete_bookkeeping(self.scoped_records(plan['ids']))
                    for f in manifest['files']:
                        if Path(f['source']).exists(): raise upstream.OperationError('删除后原历史文件仍存在')
                manifest['status']='verified'; manifest['changes']=plan.get('diagnostic',{}).get('changes',{}) if plan.get('diagnostic') else {}
                atomic_json(folder/'manifest.json',manifest)
                result={'ok':True,'action':action,'affected':len(plan['ids']),'backup':str(folder),
                        'verification':'本地状态验证通过；未验证远端模型往返'}
            except Exception as exc:
                manifest['status']='failed_or_partial'
                try:
                    if self.unchanged_since_backup(plan, folder, manifest): manifest['status']='failed_unchanged'
                except Exception as verification_error:
                    manifest['state_check_error']=str(verification_error)[:1000]
                manifest['error']=str(exc)[:2400]
                if isinstance(exc, OfficialRPCError):
                    manifest['rpc_error']={'method':exc.method,'code':exc.code}
                atomic_json(folder/'manifest.json',manifest)
                raise upstream.OperationError(f'{exc}。备份与状态记录：{folder}') from exc
            # The mutation is verified and durably recorded before best-effort notification.
            # A notification error must never relabel a successful deletion as failed.
            if action=='delete':
                self.report('desktop_sync',message='正在同步 Codex 列表')
                result['desktop_sync']=self.sync_verified_delete(folder,manifest)
            return result

    @staticmethod
    def delete_bookkeeping(records):
        # Codex queue deletion bumps a durable revision even when the queue is empty.
        # Retain this notification watermark; it is not queued content or thread state.
        # Exact schema only: never ignore queued_items, unknown tables or added payloads.
        kept=[]
        for record in records:
            database,table,raw=record
            if database!='queue_1.sqlite' or table!='queued_thread_revisions':continue
            row=json.loads(raw)
            if set(row)=={'thread_id','revision'} and type(row['revision']) is int and row['revision']>0:
                kept.append(record)
        return kept

    def verify(self, plan):
        if plan['action']=='delete':
            records=self.scoped_records(plan['ids'])
            bookkeeping=self.delete_bookkeeping(records)
            remaining=[r for r in records if r not in bookkeeping]
            if remaining:
                tables=', '.join(sorted({f'{r[0]}/{r[1]}' for r in remaining}))
                raise upstream.OperationError(f'官方删除返回后仍有目标关联记录（{tables}），已保留备份；请检查状态，未自动重试')
        for sid in plan['ids']:
            if plan['action']=='delete':
                try:self.store.row(sid)
                except FileNotFoundError:continue
                raise upstream.OperationError('官方返回后数据库仍存在目标线程，请查看操作记录')
            r=self.store.row(sid)
            if plan['action'] in {'archive','unarchive'}:
                if bool(r['archived'])!=(plan['action']=='archive') or not self.store.path(r).is_file():
                    raise upstream.OperationError('归档验证未通过或子线程未全部归档')
            if plan['action'] in {'migrate','link'}:self.history_paths.verify(plan)
            if plan['action']=='relocate' and (r['rollout_path']!=plan['destination'] or self.store.storage_boundary_issue(r)):
                raise upstream.OperationError('归位后路径验证失败')
            if plan['action']=='rename' and (r.get('name') or r['title'])!=plan['title'].strip(): raise upstream.OperationError('标题验证未通过')

    def save_plan(self, plan):
        plan.update(token=secrets.token_urlsafe(32),expires=time.monotonic()+300)
        plan['confirmation']=plan['action']+'-'+plan['token'][:8]
        with self.lock:
            self.plans={k:v for k,v in self.plans.items() if v['expires']>time.monotonic()}
            if len(self.plans)>=64: raise upstream.OperationBusy('待确认计划过多')
            self.plans[plan['token']]=plan
        return {k:v for k,v in plan.items() if k not in {'fingerprint','expires','members','content_hash','delete_files','history_evidence','related_records','original_row','parent_identity','history_file','project_snapshot'}}

    @staticmethod
    def selected_ids(ids, minimum=1, maximum=None):
        if not isinstance(ids,list) or len(ids)<minimum or (maximum is not None and len(ids)>maximum):
            raise ValueError(f'请选择 {minimum}–{maximum} 条会话' if maximum is not None else f'请至少选择 {minimum} 条会话')
        for sid in ids: upstream.CodexController.validate_id(sid)
        if len(set(ids))!=len(ids): raise ValueError('请勿重复选择同一条会话')
        return ids

    def batch_plan(self, action, ids, progress=None):
        if action not in {'delete','archive','unarchive'}: raise ValueError('批量操作类型无效')
        ids=self.selected_ids(ids)
        graph=self.store.graph() if action in {'delete','archive'} else None
        scopes={sid:set(self.store.descendants([sid],graph) if graph else [sid]) for sid in ids}
        roots=[]; covered=set()
        for sid in sorted(ids,key=lambda i:(-len(scopes[i]),i)):
            if sid not in covered: roots.append(sid); covered|=scopes[sid]
        references=self.references() if action=='delete' else None
        allowed=set().union(*scopes.values())
        members=[]; blocked=[]; targets=[]; size=0
        for n,sid in enumerate(roots):
            if progress: progress('preflight',n,len(roots))
            self.report('preflight',current_id=sid,current_title=self.store.row(sid).get('name') or self.store.row(sid)['title'])
            try:
                p=self._build_plan(action,sid,ids=sorted(scopes[sid]),allowed_delete_ids=allowed,references=references)
                members.append(p);targets+=p['targets'];size+=p['file_bytes']
            except (upstream.OperationError,FileNotFoundError,ValueError) as exc:
                for i in sorted(scopes[sid]):
                    r=self.store.row(i);blocked.append({'id':i,'title':r.get('name') or r['title'],'reason':str(exc)})
        if action=='delete':
            # Propagate blocked dependencies rather than starting a source whose referencing
            # group failed preflight. Never silently add an unselected fork to the scope.
            while True:
                eligible={i for m in members for i in m['ids']};removed=[]
                for m in members:
                    try:self.assert_references(m['ids'],eligible,references)
                    except upstream.OperationError as exc:
                        removed.append(m)
                        blocked.extend({'id':t['id'],'title':t['title'],'reason':str(exc)} for t in m['targets'])
                if not removed:break
                members=[m for m in members if m not in removed]
            members=dependency_order(members,references)
            targets=[t for m in members for t in m['targets']];size=sum(m['file_bytes'] for m in members)
        if progress: progress('preflight',len(roots),len(roots))
        return self.save_plan({'action':action,'id':roots[0],'ids':sorted({t['id'] for t in targets}),
            'targets':targets,'file_bytes':size,'diagnostic':None,'batch':True,'members':members,'blocked':blocked,
            'dependency_ordered':action=='delete' and any(m.get('delete_dependencies') for m in members),
            'delete_dependencies':sorted({tuple(e) for m in members for e in m.get('delete_dependencies',[])}),
            'external_delete':any(m.get('external_delete',False) for m in members),
            'missing_histories':sorted({sid for m in members for sid in m.get('missing_histories',[])})})

    def execute_batch(self, token, confirmation, progress=None, before_member=None):
        p=self.plans.pop(token)
        if p['expires']<time.monotonic() or not secrets.compare_digest(p['confirmation'],str(confirmation)):
            raise upstream.OperationError('批量计划过期或确认不匹配')
        members=p['members']
        if not members: raise upstream.OperationError('预检中没有可执行的会话')
        if p['action']=='delete':
            refs=self.references();scope={i for m in members for i in m['ids']}
            current=self.assert_references(scope,scope,refs)
            if sorted(map(tuple,current))!=sorted(map(tuple,p.get('delete_dependencies',[]))):
                raise upstream.OperationBusy('分叉引用已变化，请重新预检；整批尚未执行')
            if [m['id'] for m in dependency_order(members,refs)]!=[m['id'] for m in members]:
                raise upstream.OperationBusy('删除依赖顺序已变化，请重新预检')
        graph=self.store.graph() if p['action'] in {'archive','delete'} else None
        for n,m in enumerate(members):
            if progress: progress('validating',n,len(members))
            if self.store.fingerprint(m['ids'])!=m['fingerprint']: raise upstream.OperationBusy('会话发生变化，请重新预检；整批尚未执行')
            if graph is not None and self.store.descendants([m['id']],graph)!=m['ids']:
                raise upstream.OperationBusy('关联范围已变化；整批尚未执行')
        outcomes=[]; affected=0; stopped=False; cancelled=False
        for n,m in enumerate(members):
            if progress: progress('executing',n,len(members))
            title=next(t['title'] for t in m['targets'] if t['id']==m['id'])
            if self.stop_requested():cancelled=True
            if stopped or cancelled:
                outcomes.append({'id':m['id'],'title':title,'status':'not_executed','detail':'已按要求停止，未执行' if cancelled else '前一项失败，后续停止，未自动重试'});continue
            self.report('validating',current_id=m['id'],current_title=title,current_index=n+1,
                        scope_count=len(m['ids']),message='核对当前会话状态',bytes_done=None,bytes_total=None,wait_remaining_seconds=None)
            try:
                if before_member:before_member(m)
                m['expires']=float('inf')
                r=self._execute_prepared(m,m['confirmation'],pre_mutation=(lambda:before_member(m)) if before_member else None);affected+=r['affected']
                outcomes.append({'id':m['id'],'title':title,'status':'verified','detail':r['backup'],'desktop_sync':r.get('desktop_sync')})
            except Exception as exc:
                stopped=True;outcomes.append({'id':m['id'],'title':title,'status':'failed','detail':str(exc)})
        if progress: progress('executing',len(members),len(members))
        for b in p['blocked']:outcomes.append({'id':b['id'],'title':b['title'],'status':'blocked','detail':b['reason']})
        result={'ok':not stopped and not cancelled and not p['blocked'],'action':p['action'],'affected':affected,
                'backup':str(self.root/'backups'),'outcomes':outcomes,
                'verification':f"已验证 {affected} 条；失败 {sum(o['status']=='failed' for o in outcomes)} 组；未执行 {sum(o['status']=='not_executed' for o in outcomes)} 组；预检受限 {len(p['blocked'])} 条。"}
        if p['action']=='delete':
            result['desktop_sync']=self.summarize_sync([o['desktop_sync'] for o in outcomes if o.get('desktop_sync')])
        report=self.root/'batch-results';report.mkdir(exist_ok=True)
        atomic_json(report/(uuid.uuid4().hex+'.json'),result)
        return result

    def relocate(self, plan, folder, manifest):
        if self.runtime_probe(): raise upstream.OperationBusy('请退出 Codex / ChatGPT 和 CLI 后执行历史归位')
        sid=plan['id'];old=self.store.row(sid);src=self.store.path(old);dst=Path(plan['destination'])
        root=(self.store.codex_home/('archived_sessions' if old['archived'] else 'sessions')).resolve()
        if not dst.parent.resolve().is_relative_to(root) or dst.parent.exists():
            raise upstream.OperationError('归位目标目录已存在或路径边界发生变化，请检查操作记录')
        manifest['destination']=str(dst);manifest['source_preserved']=str(src);atomic_json(folder/'manifest.json',manifest)
        dst.parent.mkdir(mode=0o700)
        with src.open('rb') as source,dst.open('xb') as target:
            os.chmod(dst,0o600);shutil.copyfileobj(source,target);target.flush();os.fsync(target.fileno())
        expected=next(f['sha256'] for f in manifest['files'] if f['source']==str(src))
        if digest(dst)!=expected or digest(src)!=expected or self.store.fingerprint([sid])!=plan['fingerprint']:
            raise upstream.OperationBusy('归位复制校验或原记录核对失败，索引尚未修改')
        if self.runtime_probe():raise upstream.OperationBusy('Codex 进程重新启动，保留复制文件但未更新引用')
        with closing(sqlite3.connect(self.store.db_path,timeout=5)) as c, c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
            if dict(c.execute('SELECT * FROM threads WHERE id=?',(sid,)).fetchone())!=old:raise upstream.OperationBusy('索引已变化')
            c.execute('UPDATE threads SET rollout_path=? WHERE id=?',(str(dst),sid))
        if digest(src)!=expected or digest(dst)!=expected: raise upstream.OperationError('归位后内容核对失败')

    def merge_content(self, ids):
        # Compatibility reader for small callers; production uses streaming scan.
        stats={}
        messages=list(self.merger.messages(ids,stats))
        return messages,stats['excluded']

    def merge_plan(self, ids, title, progress=None):
        return self.merger.plan(ids,title,progress)

    def execute_merge(self, token, confirmation, progress=None):
        return self.merger.execute(token,confirmation,progress)

    def verify_merge_desktop_sync(self,sid):
        upstream.CodexController.validate_id(sid)
        if self.store.sqlite_home!=self.store.codex_home:raise ValueError('独立索引未连接默认 Codex 桌面目录，未报告同步成功')
        for path in sorted((self.root/'backups').glob('*/manifest.json'),reverse=True):
            manifest=json.loads(path.read_text())
            if manifest.get('action')!='merge' or manifest.get('new_id')!=sid or manifest.get('status')!='verified':continue
            if manifest.get('codex_home')!=str(self.store.codex_home) or manifest.get('database')!=str(self.store.db_path):raise ValueError('合并记录属于其他会话库')
            self.store.row(sid)
            rows=self.desktop_sync.catalog_rows([sid])
            result={'status':'synced' if rows and sid in rows else 'pending','affected':1,
                    'catalog_verified':bool(rows and sid in rows),
                    'message':'合并会话已同步至 Codex，桌面目录核验通过。' if rows and sid in rows else '合并会话已保存至 Codex；桌面目录尚未确认，请在 Codex 打开后重新核验。'}
            return self.write_sync_record(path.parent,result)
        raise ValueError('没有找到已验证的合并记录，未报告同步成功')

    def check_deleted_manifest(self, manifest):
        if manifest.get('action')!='delete' or manifest.get('status')!='verified':
            raise ValueError('仅同步已通过删除验证的记录')
        ids=manifest.get('ids',[]);rows=manifest.get('rows',[])
        if not ids or len(set(ids))!=len(ids) or len(rows)!=len(ids) or {r['id'] for r in rows}!=set(ids):
            raise ValueError('删除记录的会话范围不完整')
        for sid in ids:
            if str(uuid.UUID(sid))!=sid:raise ValueError('删除记录的会话 ID 无效')
        if manifest.get('codex_home') or manifest.get('database'):
            if (manifest.get('codex_home')!=str(self.store.codex_home)
                or manifest.get('database')!=str(self.store.db_path)):
                raise ValueError('删除记录属于另一个会话库，未发送同步通知')
        else:
            # Prior builds omitted library identity. Only accept their records in the
            # original app data directory against the standard local Codex library.
            if (self.store.codex_home!=(Path.home()/'.codex').resolve()
                or self.store.sqlite_home!=self.store.codex_home
                or self.root!=(Path.home()/'Library/Application Support/Codex Conversations').resolve()):
                raise ValueError('旧删除记录缺少会话库标识，未向其他会话库发送通知')
        self.verify({'action':'delete','ids':ids})
        for item in manifest.get('files',[]):
            if Path(item['source']).exists():raise ValueError('原历史文件再次出现，暂不发送删除同步通知')
        return rows

    def write_sync_record(self, folder, result):
        record=dict(result,updated_at=datetime.now(timezone.utc).isoformat())
        try:atomic_json(folder/'desktop-sync.json',record)
        except Exception as exc:
            # Preserve the actual transport result even when its separate journal fails.
            record['record_error']=str(exc)[:300]
            record['message']+='；同步记录写入失败：'+str(exc)[:200]
        return record

    def sync_verified_delete(self, folder, manifest):
        try:
            rows=self.check_deleted_manifest(manifest)
            result=self.desktop_sync.sync_deleted(rows)
        except Exception as exc:
            result={'status':'pending','affected':len(manifest.get('ids',[])),
                    'message':'删除已验证；桌面同步未执行：'+str(exc)[:300], 'mutation_rpc_sent':False}
        return self.write_sync_record(folder,result)

    @staticmethod
    def summarize_sync(records):
        affected=sum(r.get('affected',0) for r in records)
        synced=sum(r.get('affected',0) for r in records if r['status']=='synced')
        pending=sum(r.get('affected',0) for r in records if r['status']=='pending')
        sent=sum(r.get('affected',0) for r in records if r['status']=='sent')
        status=('pending' if pending else 'sent' if sent else 'synced' if synced else
                'not_applicable' if records else 'not_needed')
        return {'status':status,'affected':affected,'synced':synced,'pending':pending,'sent':sent,
                'catalog_verified':status=='synced','mutation_rpc_sent':False,
                'message':f'Codex 列表：已核验同步 {synced} 条；待同步 {pending} 条；已通知未核验 {sent} 条。'
                    if status not in {'not_needed','not_applicable'} else
                    ('没有待同步的已删除会话' if status=='not_needed' else '独立数据库未连接桌面端，未发送同步通知')}

    def retry_desktop_sync(self):
        # Notification-only. No deletion engine calls, no restore, no retries of writes.
        with self.exclusive():
            groups=[];outcomes=[];all_rows={}
            for path in sorted((self.root/'backups').glob('*/manifest.json')):
                try:manifest=json.loads(path.read_text())
                except (OSError,ValueError) as exc:
                    outcomes.append({'id':str(path.parent),'title':'删除记录读取异常','status':'not_executed','detail':str(exc)[:300]});continue
                if manifest.get('action')!='delete' or manifest.get('status')!='verified':continue
                try:rows=self.check_deleted_manifest(manifest)
                except Exception as exc:
                    outcomes.append({'id':str(path.parent),'title':'未同步此删除记录','status':'not_executed','detail':str(exc)[:300]});continue
                groups.append((path.parent,manifest))
                all_rows.update((r['id'],r) for r in rows)
            try:result=self.desktop_sync.sync_deleted(list(all_rows.values()))
            except Exception as exc:result={'status':'pending','affected':len(all_rows),'message':'桌面同步待重试：'+str(exc)[:300]}
            for folder,manifest in groups:
                scoped=dict(result,ids=manifest['ids'],affected=len(manifest['ids']),request_affected=len(all_rows))
                if scoped['status']=='synced':scoped['message']=f"已同步 Codex 列表 · {len(manifest['ids'])} 条，桌面目录核验通过"
                if 'removed_cached_count' in scoped:scoped['request_removed_cached_count']=scoped.pop('removed_cached_count')
                record=self.write_sync_record(folder,scoped)
                outcomes.append({'id':str(folder),'title':manifest['rows'][0].get('title') or '已删除会话',
                    'status':'verified' if result['status']=='synced' else result['status'],
                    'detail':str(folder),'desktop_sync':record})
            skipped=sum(o['status']=='not_executed' for o in outcomes)
            return {'ok':result['status'] in {'synced','not_needed'} and not skipped,'action':'desktop_sync',
                    'affected':len(all_rows) if result['status']=='synced' else 0,
                    'backup':str(self.root/'backups'),'outcomes':outcomes,'desktop_sync':result,
                    'verification':f'本次仅同步已验证的删除记录，没有再次删除、归档或修改对话。跳过 {skipped} 组记录。'}

    def history(self):
        records=[]
        for p in sorted((self.root/'backups').glob('*/manifest.json'),reverse=True)[:50]:
            d=json.loads(p.read_text())
            record={k:d.get(k) for k in ('action','ids','created_at','status','error')}|{'backup':str(p.parent)}
            sidecar=p.parent/'desktop-sync.json'
            if sidecar.is_file():
                try:record['desktop_sync']=json.loads(sidecar.read_text())
                except (OSError,ValueError):record['desktop_sync']={'status':'pending','affected':len(d.get('ids',[])),'message':'同步记录读取异常，可重新同步'}
            records.append(record)
        return records


class BatchJobs:
    """One background batch at a time. Polls are read-only; failed writes are never retried."""
    def __init__(self, maintenance):
        self.maintenance=maintenance;self.lock=threading.RLock();self.active=None;self.jobs={};self.worker=None
        self.root=maintenance.root/'batch-jobs';self.root.mkdir(exist_ok=True,mode=0o700)

    def start(self, mode, body):
        if mode not in {'plan','execute'}: raise ValueError('未知批量任务')
        if mode=='plan' and body.get('action')!='project_delete': self.maintenance.selected_ids(body.get('ids'))
        with self.lock:
            if self.active: raise upstream.OperationBusy('已有批量任务正在运行')
            self.jobs.clear()  # Completed jobs remain on disk; never a per-item memory slot limit.
            jid=uuid.uuid4().hex
            job={'id':jid,'mode':mode,'state':'queued','phase':'preflight' if mode=='plan' else 'validating',
                 'completed':0,'total':0,'error':None,'plan':None,'result':None,
                 'started_at':time.time(),'updated_at':time.time(),'phase_started_at':time.time(),'stop_requested':False}
            atomic_json(self.root/(jid+'.json'),job)
            self.jobs[jid]=job;self.active=jid
            self.worker=threading.Thread(target=self._run,args=(jid,mode,body),daemon=False)
            self.worker.start()
            return dict(job)

    def get(self, jid):
        if not re.fullmatch(r'[a-f0-9]{32}',jid):raise ValueError('批量任务 ID 无效')
        with self.lock:
            if jid in self.jobs:return dict(self.jobs[jid])
        path=self.root/(jid+'.json')
        if not path.is_file():raise FileNotFoundError('批量任务不存在')
        job=json.loads(path.read_text())
        if job['state'] in {'queued','running'}:
            job.update(state='interrupted',error='服务曾中断，请检查逐条备份和操作记录；未自动重试。')
        return job

    def stop(self, jid):
        if not isinstance(jid,str) or not re.fullmatch(r'[a-f0-9]{32}',jid):raise ValueError('批量任务 ID 无效')
        with self.lock:
            if jid not in self.jobs or jid!=self.active:raise upstream.OperationError('任务已结束或不在当前服务中')
            job=self.jobs[jid]
            if job['mode']!='execute':raise ValueError('此任务不是执行任务')
            job['stop_requested']=True
            atomic_json(self.root/(jid+'.json'),job)
            return dict(job)

    def _run(self, jid, mode, body):
        last_persist=0
        def detail(phase,**values):
            nonlocal last_persist
            now=time.time()
            with self.lock:
                job=self.jobs[jid];changed=phase!=job['phase']
                if changed:
                    job.setdefault('timeline',[]).append({'phase':job['phase'],'ended_at':now,'elapsed_seconds':round(now-job['phase_started_at'],3)})
                    job['phase_started_at']=now
                job.update(state='running',phase=phase,updated_at=now,**values)
                if changed or now-last_persist>=1:
                    atomic_json(self.root/(jid+'.json'),job);last_persist=now
        def progress(phase,completed,total):
            detail(phase,completed=completed,total=total)
        def should_stop():
            with self.lock:return self.jobs[jid]['stop_requested']
        try:
            with self.maintenance.observing(detail,should_stop):
                if mode=='plan' and body.get('action')=='project_delete':value=self.maintenance.project_maintenance.plan(body.get('project_id'),progress)
                elif mode=='plan' and body.get('action')=='merge':value=self.maintenance.merge_plan(body.get('ids'),body.get('title'),progress)
                elif mode=='plan' and body.get('action') in {'project_assign','project_create'}:value=self.maintenance.project_assignment.plan(body.get('ids'),body.get('project_id'),body.get('name'),body.get('root'),progress)
                elif mode=='plan':value=self.maintenance.batch_plan(body.get('action'),body.get('ids'),progress)
                else:value=self.maintenance.execute(str(body.get('token','')),str(body.get('confirmation','')),progress)
            with self.lock:self.jobs[jid].update(state='completed',**{'plan' if mode=='plan' else 'result':value})
        except Exception as exc:
            with self.lock:self.jobs[jid].update(state='failed',error=str(exc))
        finally:
            with self.lock:
                try:atomic_json(self.root/(jid+'.json'),self.jobs[jid])
                finally:self.active=None

    def shutdown(self):
        if self.worker:self.worker.join()  # Finish the already confirmed operation before orderly exit.


class ManagerHandler(upstream.Handler):
    maintenance: Maintenance
    agent: AgentRepairService
    batches: BatchJobs
    def do_GET(self):
        if not self._validate_transport():return
        p=urlparse(self.path)
        try:
            if p.path=='/api/status':
                self.send_json({'api_version':17,'version':VERSION,'codex_home':str(self.store.codex_home),'database':str(self.store.db_path),
                                'codex_binary':self.controller.command,'codex_version':self.controller.engine_info()['version'],
                                'codex_cli_available':self.controller.available,'csrf_token':self.csrf_token,'storage':'sqlite-index','scope':'local','agent_available':self.controller.available});return
            if p.path=='/api/sessions':
                q=parse_qs(p.query);catalog=self.store.project_catalog()
                rows,total=self.store.list(q.get('q',[''])[0],int(q.get('limit',['1000'])[0]),q.get('scope',['all'])[0],catalog)
                self.send_json({'sessions':rows,'total':total,'projects':catalog.public});return
            if p.path=='/api/operations':self.send_json({'operations':self.maintenance.history()});return
            m=re.fullmatch(r'/api/batch/jobs/([a-f0-9]{32})',p.path)
            if m:self.send_json(self.batches.get(m[1]));return
            m=re.fullmatch(r'/api/agent/jobs/([a-f0-9]{32})',p.path)
            if m:self.send_json(self.agent.get(m[1]));return
            m=re.fullmatch(r'/api/sessions/([^/]+)/diagnosis',p.path)
            if m:self.send_json(self.store.diagnose(m[1]));return
            m=re.fullmatch(r'/api/sessions/([^/]+)',p.path)
            if m:self.send_json(self.store.get(m[1]));return
            if p.path.startswith('/api/'):self.send_json({'error':'接口不存在'},404);return
            if p.path not in {'/','/index.html','/style.css','/app.js','/manager.js','/manager.css'}:
                self.send_json({'error':'文件不存在'},404);return
            return super().do_GET()
        except Exception as exc:self._send_operation_error(exc)

    def do_POST(self):
        if not self._validate_transport() or not self._require_csrf():return
        try:
            max_bytes=16384
            if self.path in {'/api/batch/plans','/api/batch/jobs/plan','/api/merge/plans'}:
                # A UUID selection can span the whole current library. Budget tracks library size,
                # not a fixed batch count; unrelated payload/whitespace still has a byte guard.
                with self.store.db() as c: count=c.execute('SELECT COUNT(*) FROM threads').fetchone()[0]
                max_bytes=max(max_bytes,count*64+4096)
            body=self._read_json(max_bytes)
            if self.path=='/api/agent/jobs':self.send_json(self.agent.start(body.get('id'),body.get('consent'),body.get('model','')),202)
            elif self.path=='/api/agent/cancel':self.send_json(self.agent.cancel(body.get('job_id')))
            elif self.path=='/api/agent/plan':self.send_json(self.agent.plan(body.get('job_id')))
            elif self.path=='/api/batch/jobs/plan':self.send_json(self.batches.start('plan',body),202)
            elif self.path=='/api/batch/jobs/execute':self.send_json(self.batches.start('execute',body),202)
            elif self.path=='/api/batch/jobs/stop':self.send_json(self.batches.stop(body.get('job_id')))
            elif self.path=='/api/batch/plans':self.send_json(self.maintenance.batch_plan(body.get('action'),body.get('ids')))
            elif self.path=='/api/merge/plans':self.send_json(self.maintenance.merge_plan(body.get('ids'),body.get('title')))
            elif self.path=='/api/plans':self.send_json(self.maintenance.plan(body.get('action'),body.get('id'),body.get('title'),body.get('target_path'),body.get('keep_source',True)))
            elif self.path=='/api/merge/sync':self.send_json(self.maintenance.verify_merge_desktop_sync(body.get('id')))
            elif self.path=='/api/desktop-sync':self.send_json(self.maintenance.retry_desktop_sync())
            elif self.path=='/api/execute':self.send_json(self.maintenance.execute(str(body.get('token','')),str(body.get('confirmation',''))))
            else:self.send_json({'error':'请使用预检计划接口，直接修改接口已禁用'},405)
        except Exception as exc:self._send_operation_error(exc)


def main():
    parser=argparse.ArgumentParser(description='续言 ReThread · 本地修复、归档与删除')
    parser.add_argument('--codex-home',type=Path,default=upstream.DEFAULT_CODEX_HOME)
    parser.add_argument('--sqlite-home',type=Path,default=os.environ.get('CODEX_SQLITE_HOME'))
    parser.add_argument('--data-dir',type=Path,default=Path(__file__).parent/'data')
    parser.add_argument('--codex-bin',help='Explicit engine; otherwise use the desktop engine before PATH');parser.add_argument('--port',type=int,default=0);parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    store=IndexedStore(args.codex_home,args.sqlite_home)
    controller=OfficialController(args.codex_home,args.codex_bin,store.sqlite_home)
    ManagerHandler.store=store;ManagerHandler.controller=controller
    ManagerHandler.maintenance=Maintenance(store,controller,args.data_dir)
    ManagerHandler.batches=BatchJobs(ManagerHandler.maintenance)
    ManagerHandler.agent=AgentRepairService(store,ManagerHandler.maintenance,CodexAnalysisRunner(controller._require_command(),args.codex_home))
    ManagerHandler.csrf_token=secrets.token_urlsafe(32)
    try: http=ThreadingHTTPServer(('127.0.0.1',args.port),ManagerHandler)
    except OSError as exc: parser.error(f'端口绑定失败：{exc}；可显式指定 --port 0 让系统分配端口')
    print(f'续言 ReThread {VERSION}\nhttp://127.0.0.1:{http.server_port}\n读取：{store.codex_home}\n备份：{args.data_dir.resolve()}\nCtrl+C 停止服务',flush=True)
    if args.open:webbrowser.open(f'http://127.0.0.1:{http.server_port}')
    def shutdown(signum, frame):
        threading.Thread(target=http.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        ManagerHandler.batches.shutdown()
        ManagerHandler.agent.shutdown()
        http.server_close()

if __name__=='__main__':main()
