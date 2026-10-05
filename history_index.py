"""Read-only rollout inventory. No message bodies, credential files or SQL mutations."""
import os,re,json,time
from pathlib import Path
from graphlib import TopologicalSorter,CycleError

ROLLOUT=re.compile(r"rollout-.+-([0-9a-fA-F-]{36})\.jsonl(\.zst)?$")

def candidate_name(name):
    return name.startswith('rollout-') and name.endswith(('.jsonl','.jsonl.zst'))

def scan_rollouts(roots, report=lambda *a,**k:None):
    started=time.monotonic();stack=[Path(p).resolve() for p in roots];seen=set();files={};last=0
    while stack:
        folder=stack.pop();key=str(folder)
        if key in seen:continue
        seen.add(key)
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        target=Path(entry.path).resolve()
                        if target.is_dir():stack.append(target)
                        elif target.is_file() and candidate_name(entry.name):files[str(target)]=target
                    elif entry.is_dir(follow_symlinks=False):stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and candidate_name(entry.name):files[entry.path]=Path(entry.path)
        except FileNotFoundError:
            if folder in [Path(p).resolve() for p in roots]:continue
            raise RuntimeError('历史目录在扫描过程中发生变化，请重新预检')
        now=time.monotonic()
        if now-last>=1:
            report('lookup',message=f'已检查 {len(seen):,} 个目录 · {len(files):,} 个历史文件',scan_directories=len(seen),scan_files=len(files))
            last=now
    report('lookup',message=f'历史扫描完成 · {len(seen):,} 个目录 · {len(files):,} 个文件',scan_directories=len(seen),scan_files=len(files),scan_elapsed_seconds=round(time.monotonic()-started,3))
    return sorted(files.values())

def history_references(paths):
    # Use history_base, NOT forked_from_id: a fully copied legacy fork has no live dependency.
    owned={};raw=[]
    for path in paths:
        if str(path).endswith('.zst'):
            raise ValueError('历史目录含压缩 JSONL，当前引用预检未读取其内容；请先解压后核对，尚未删除')
        before=path.stat()
        with path.open('rb') as f:line=f.readline(4*1024*1024+1)
        if len(line)>4*1024*1024:raise ValueError('历史首行超过 4 MiB，引用预检已停止，未截断元数据')
        after=path.stat()
        if (before.st_ino,before.st_mtime_ns,before.st_size)!=(after.st_ino,after.st_mtime_ns,after.st_size):
            # Appending turns does not change a fork's header. Re-read the exact reference
            # evidence rather than blocking unrelated deletions on every active append.
            with path.open('rb') as f:confirmed=f.readline(4*1024*1024+1)
            if before.st_ino!=after.st_ino or after.st_size<before.st_size or confirmed!=line:
                raise ValueError('历史引用在预检时发生变化，请重新预检')
        try:e=json.loads(line)
        except (ValueError,UnicodeError):continue  # The official reference index also ignores unreadable metadata.
        m=e.get('payload') if isinstance(e,dict) and e.get('type')=='session_meta' else None
        if not isinstance(m,dict) or not isinstance(m.get('id'),str):continue
        match=ROLLOUT.fullmatch(path.name)
        if not match:continue
        rollout=match[1];owner=m['id']
        if rollout in owned and owned[rollout]!=owner:raise ValueError('同一历史 ID 对应不同会话，引用预检已停止')
        owned[rollout]=owner
        base=m.get('history_base')
        if base:
            if not isinstance(base,dict) or not isinstance(base.get('thread_id'),str):raise ValueError('历史引用字段格式异常，请检查记录')
            raw.append((owner,base['thread_id'],rollout))
    return sorted({(owner,owned.get(base,base)) for owner,base,rollout in raw if base!=rollout})

def dependency_order(members,references):
    owner={sid:m['id'] for m in members for sid in m['ids']};deps={m['id']:set() for m in members}
    for child,parent in references:
        if parent in owner and child not in owner:raise ValueError(f'仍有未选择的分叉会话 {child} 引用 {parent}；请同时选择引用方，或先删除引用方')
        if parent in owner and child in owner and owner[parent]!=owner[child]:deps[owner[parent]].add(owner[child])
    try:order=list(TopologicalSorter(deps).static_order())
    except CycleError as exc:raise ValueError('历史引用形成循环，已停止生成删除计划，未执行任何删除') from exc
    by_id={m['id']:m for m in members}
    return [by_id[sid] for sid in order]
