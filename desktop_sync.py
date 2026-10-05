"""Codex desktop IPC cache synchronization, separate from destructive maintenance.

The installed desktop uses the version-2 thread-archived broadcast to evict both
archived and already-missing histories (its inactive-thread cleanup uses it too).
This is a cache notification, NOT thread/archive or thread/delete RPC. We only
publish after the caller re-verifies deletion. No app patch, restart, DB write,
credential access, fake deletion event, or alternate deletion engine is involved.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import time
import uuid
from contextlib import closing


class DesktopSync:
    def __init__(self, home, sqlite_home, timeout=4.0):
        self.home=Path(home).resolve(); self.sqlite_home=Path(sqlite_home).resolve()
        self.timeout=timeout

    def catalog_rows(self, ids):
        path=self.home/'sqlite/codex-dev.db'
        if not path.exists():return None
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
            fields={r[1] for r in db.execute('PRAGMA table_info(local_thread_catalog)')}
            if not {'host_id','thread_id'}<=fields:raise ValueError('Codex 桌面目录结构已变化')
            targets=set(ids)
            return {r[0] for r in db.execute("SELECT thread_id FROM local_thread_catalog WHERE host_id='local'") if r[0] in targets}

    def sync_deleted(self, rows):
        started=time.monotonic();ids=[r['id'] for r in rows]
        if not ids:return {'status':'not_needed','affected':0,'message':'没有待同步的已删除会话'}
        if self.sqlite_home!=self.home:
            return {'status':'not_applicable','affected':0,'message':'独立数据库未连接桌面端；未向其他会话库发送通知'}
        result={'status':'pending','affected':len(ids),'ids':ids,'transport':'codex-desktop-ipc-v2',
                'mutation_rpc_sent':False,'catalog_verified':False}
        try:
            for sid in ids:
                if str(uuid.UUID(sid))!=sid:raise ValueError('同步会话 ID 无效')
            path=self.home/'ipc/ipc.sock'
            if not path.exists():
                result['message']='对话已删除；Codex 未运行或本地同步接口未就绪，可稍后仅重试同步'
                return result
            for p,kind in [(path.parent,stat.S_ISDIR),(path,stat.S_ISSOCK)]:
                st=p.lstat()
                if not kind(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:
                    raise ValueError('Codex 同步接口的类型、所有者或权限不匹配')
            before=self.catalog_rows(ids)
            deadline=started+self.timeout
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                def budget():
                    left=deadline-time.monotonic()
                    if left<=0:raise TimeoutError('桌面同步响应超时')
                    sock.settimeout(left)
                budget();sock.connect(str(path))
                def send(message):
                    budget();body=json.dumps(message,separators=(',',':')).encode()
                    sock.sendall(struct.pack('<I',len(body))+body)
                def exact(n):
                    chunks=[]
                    while n:
                        budget();chunk=sock.recv(n)
                        if not chunk:raise EOFError('桌面同步连接已关闭')
                        chunks.append(chunk);n-=len(chunk)
                    return b''.join(chunks)
                def initialize():
                    request_id=str(uuid.uuid4())
                    send({'type':'request','requestId':request_id,'method':'initialize','version':0,'params':{'clientType':'rethread'}})
                    while True:
                        length=struct.unpack('<I',exact(4))[0]
                        if not 0<length<=4*1024*1024:raise ValueError('桌面同步数据帧长度异常')
                        value=json.loads(exact(length))
                        if not isinstance(value,dict):raise ValueError('桌面同步消息格式异常')
                        if value.get('type')=='client-discovery-request':
                            send({'type':'client-discovery-response','requestId':value['requestId'],'response':{'canHandle':False}})
                        if value.get('type')=='response' and value.get('requestId')==request_id:
                            if value.get('resultType')!='success' or value.get('method')!='initialize':raise ValueError('Codex 拒绝桌面同步连接')
                            client=value.get('result',{}).get('clientId')
                            if not isinstance(client,str) or not client:raise ValueError('桌面同步连接未返回客户端 ID')
                            return client
                client=initialize()
                for row in rows:
                    send({'type':'broadcast','method':'thread-archived','version':2,'sourceClientId':client,
                          'params':{'hostId':'local','conversationId':row['id'],'cwd':row.get('cwd') or '/'}})
                for key in [['archived-threads','local'],['command-menu-thread-search','local']]:
                    send({'type':'broadcast','method':'query-cache-invalidate','version':0,'sourceClientId':client,'params':{'queryKey':key}})
                # FIFO router barrier. It confirms forwarding, not receipt by every UI.
                if initialize()!=client:raise ValueError('桌面同步连接身份发生变化')
                result['notification_sent']=True
                if before is None:
                    result.update(status='sent',message='已通知 Codex 刷新列表；此版本未提供可核验的桌面目录')
                    return result
                while True:
                    remaining=self.catalog_rows(ids)
                    if remaining is None:raise ValueError('核验期间桌面目录消失，尚未确认同步')
                    if not remaining:break
                    budget();time.sleep(min(.05,max(0,deadline-time.monotonic())))
                result.update(status='synced',catalog_verified=True,removed_cached_count=len(before),
                              message=f'已同步 Codex 列表 · {len(ids)} 条，桌面目录核验通过')
        except Exception as exc:
            result['message']='对话已删除；桌面同步待重试：'+str(exc)[:300]
        finally:
            result['elapsed_seconds']=round(time.monotonic()-started,3)
        return result
