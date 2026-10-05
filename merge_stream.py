"""Streaming multimodal merge. No aggregate size/message/selection limit; official fork/read.

Only a single JSONL record and one streamed official item are held by this layer.
The Codex engine and available disk/memory remain real resource constraints.
"""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,time,uuid
from datetime import datetime,timezone
from pathlib import Path
import server as upstream
from merge_media import content_parts, media_values, visible_value, raw_value, history_event, official_value


def feed(h,value):
    h.update(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode());h.update(b'\n')


class StreamingMerge:
    def __init__(self,m,atomic_json,stamp,iso):
        self.m=m;self.store=m.store;self.atomic_json=atomic_json;self.stamp=stamp;self.iso=iso

    def messages(self,ids,stats,phase='preflight'):
        stats.update(count=0,excluded=0,bytes=0,image_count=0,audio_count=0)
        for index,sid in enumerate(ids):
            if self.m.stop_requested():raise upstream.OperationBusy('已停止合并，来源未修改；已写备份保留')
            row=self.store.row(sid);p=self.store.path(row);before=self.stamp(p)
            if before is None:raise upstream.OperationError('合并来源历史文件缺失，请先修复引用')
            count=0
            self.m.report(phase,message=f'读取来源 {index+1}/{len(ids)}',current_id=sid,completed=index,total=len(ids))
            with p.open('rb') as f:
                for n,line in enumerate(f,1):
                    stats['bytes']+=len(line)
                    if n%256==0:
                        if self.m.stop_requested():raise upstream.OperationBusy('已停止合并，来源未修改；已写备份保留')
                        self.m.report(phase,message=f"已读取 {stats['count']} 条消息",bytes_done=stats['bytes'])
                    try:e=json.loads(line)
                    except (ValueError,UnicodeError):raise upstream.OperationError(f'来源 {sid[:8]} 第 {n} 行格式异常，未合并')
                    if not isinstance(e,dict) or not isinstance(e.get('payload'),dict):raise upstream.OperationError('历史记录结构异常')
                    v=e['payload']
                    if n==1 and (e.get('type')!='session_meta' or v.get('id')!=sid):raise upstream.OperationError('合并来源身份校验失败')
                    if e.get('type')!='response_item':continue
                    if v.get('type')!='message' or v.get('role') not in {'user','assistant'} or v.get('channel')=='analysis':stats['excluded']+=1;continue
                    content=content_parts(v.get('content'),v['role'],f'来源 {sid[:8]} 第 {n} 行')
                    text='\n\n'.join(c['text'] for c in content if c['type'] in {'input_text','output_text','text'})
                    images=sum(c['type']=='input_image' for c in content)
                    audio=sum(c['type']=='input_audio' for c in content)
                    if not text.strip() and not images and not audio:stats['excluded']+=1;continue
                    count+=1;stats['count']+=1;stats['image_count']+=images;stats['audio_count']+=audio
                    yield {'role':v['role'],'text':text,'content':content,'phase':v.get('phase'),'timestamp':e.get('timestamp') or self.iso(row['created_at']),'source':sid}
            if not count:raise upstream.OperationError(f'来源 {sid[:8]} 没有可合并的消息')
            if before!=self.stamp(p):raise upstream.OperationBusy('合并来源正在变化，请稍后重试')
        self.m.report(phase,message=f"已读取 {stats['count']} 条消息",completed=len(ids),total=len(ids))

    def plan(self,ids,title,progress=None):
        ids=self.m.selected_ids(ids,2)
        if os.environ.get('CODEX_THREAD_ID') in ids:raise upstream.OperationBusy('当前正在工作的会话请结束后再合并')
        if not isinstance(title,str) or not 1<=len(title.strip())<=200:raise ValueError('新标题须为 1–200 个字符')
        if not Path(self.store.row(ids[0])['cwd']).is_dir():raise upstream.OperationError('第一项来源的工作目录不存在，请将有效目录的会话排在第一项')
        before=self.store.fingerprint(ids);stats={};h=hashlib.sha256()
        for msg in self.messages(ids,stats):feed(h,msg)
        if before!=self.store.fingerprint(ids):raise upstream.OperationBusy('来源变化，请重新预检')
        return self.m.save_plan({'action':'merge','batch':True,'id':ids[0],'ids':ids,'title':title.strip(),
            'targets':[{'id':i,'title':self.store.listed(self.store.row(i))['title']} for i in ids],
            'file_bytes':stats['bytes'],'diagnostic':None,'fingerprint':before,'content_hash':h.hexdigest(),
            'message_count':stats['count'],'image_count':stats['image_count'],'audio_count':stats['audio_count'],'excluded_count':stats['excluded'],'new_title':title.strip()})

    def verify_items(self,items,expected,count):
        h=hashlib.sha256();actual=0
        for item in items:
            value=official_value(item)
            if value is None:continue
            feed(h,value);actual+=1
            if actual%64==0:self.m.report('verify',message=f'官方完整历史核验 {actual}/{count} 条消息',completed=actual,total=count)
        if actual!=count or h.hexdigest()!=expected:raise upstream.OperationError('官方历史消息数、顺序、文本或附件核验不一致，保留新会话和记录，未报告成功')
        self.m.report('verify',message=f'官方完整历史核验 {actual}/{count} 条消息',completed=actual,total=count)

    def execute(self,token,confirmation,progress=None):
        p=self.m.plans.pop(token)
        if p['expires']<time.monotonic() or not secrets.compare_digest(p['confirmation'],str(confirmation)):raise upstream.OperationError('合并计划过期或确认不匹配')
        if self.store.fingerprint(p['ids'])!=p['fingerprint']:raise upstream.OperationBusy('合并来源已变化')
        folder,manifest=self.m.backup(p);new_id=None
        try:
            if not self.m.unchanged_since_backup(p,folder,manifest):raise upstream.OperationBusy('备份后来源变化')
            if shutil.disk_usage(folder).free<p['file_bytes']*6+64*1024*1024:raise upstream.OperationError('合并持久化所需磁盘空间不足，来源未修改')
            stage=folder/'merge-input.jsonl';ts=datetime.now(timezone.utc).isoformat();sid=str(uuid.uuid4())
            cwd=self.store.row(p['ids'][0])['cwd'];stats={};h=hashlib.sha256();visible=hashlib.sha256();raw_hash=hashlib.sha256()
            with stage.open('x') as f:
                os.chmod(stage,0o600)
                def event(kind,payload,when=ts):f.write(json.dumps({'timestamp':when,'type':kind,'payload':payload},ensure_ascii=False)+'\n')
                event('session_meta',{'id':sid,'timestamp':ts,'cwd':cwd,'originator':'rethread','source':'cli','cli_version':self.m.controller.engine_info()['version'].split()[-1]})
                for msg in self.messages(p['ids'],stats,'merge_write'):
                    feed(h,msg);feed(visible,visible_value(msg['role'],msg['text'],media_values(msg['content'])));feed(raw_hash,raw_value(msg))
                    role=msg['role'];text=msg['text'];when=msg['timestamp']
                    event('event_msg',history_event(msg),when)
                    event('response_item',{'type':'message','role':role,'content':msg['content'],**({'phase':msg['phase']} if msg['phase'] else {})},when)
                event('event_msg',{'type':'task_complete','last_agent_message':None,'turn_id':str(uuid.uuid4())})
                f.flush();os.fsync(f.fileno())
            if h.hexdigest()!=p['content_hash'] or not self.m.unchanged_since_backup(p,folder,manifest):raise upstream.OperationBusy('合并内容已变化；尚未创建新会话')
            manifest.update(merge_sources=p['targets'],message_count=stats['count'],image_count=stats['image_count'],audio_count=stats['audio_count'],excluded_count=stats['excluded'],visible_hash=visible.hexdigest(),raw_content_hash=raw_hash.hexdigest())
            self.atomic_json(folder/'manifest.json',manifest)
            with self.m.controller.exclusive(),self.m.controller.rpc_session() as call:
                result=call('thread/fork',{'threadId':sid,'path':str(stage),'cwd':cwd,'approvalPolicy':'never','sandbox':'read-only','deferGoalContinuation':True,'excludeTurns':True})
                new_id=result['thread']['id'];manifest['new_id']=new_id;self.atomic_json(folder/'manifest.json',manifest)
                call('thread/name/set',{'threadId':new_id,'name':p['title']})
            # This installed engine exposes items/list in schema but rejects it at runtime.
            # Use supported thread/read, spooling its complete response and parsing with
            # ijson rather than materializing the response or silently changing APIs.
            from stream_json import visible_items,thread_identity
            evidence=folder/'official-history.json'
            with self.m.controller.rpc_session(stream_history_path=evidence) as call:
                reply=call('thread/read',{'threadId':new_id,'includeTurns':True})
                if reply.get('stream_path')!=str(evidence):raise upstream.OperationError('官方读取证据未完成')
            self.verify_items(visible_items(evidence),visible.hexdigest(),stats['count'])
            actual_raw=hashlib.sha256();actual_stats={}
            for message in self.messages([new_id],actual_stats,'verify'):feed(actual_raw,raw_value(message))
            if actual_stats['count']!=stats['count'] or actual_raw.hexdigest()!=raw_hash.hexdigest():
                raise upstream.OperationError('新会话原始内容块或附件数据核验不一致；未报告成功')
            thread=thread_identity(evidence)
            if thread.get('id')!=new_id or thread.get('name')!=p['title']:raise upstream.OperationError('新会话身份或名称核验失败')
            if not self.m.unchanged_since_backup(p,folder,manifest):raise upstream.OperationError('来源会话已发生变化，请查看记录')
            manifest['status']='verified';self.atomic_json(folder/'manifest.json',manifest)
            return {'ok':True,'action':'merge','affected':len(p['ids']),'backup':str(folder),'new_id':new_id,
                'verification':f"已写入 Codex，并通过官方完整读取核验 {stats['count']} 条消息、{stats['image_count']} 张图片、{stats['audio_count']} 段音频及标题；来源会话保持原样。即将在 Codex 打开新会话。工具执行链、系统指令不合并；未发起模型请求。"}
        except Exception as exc:
            manifest.update(status='failed_or_partial',error=str(exc),new_id=new_id);self.atomic_json(folder/'manifest.json',manifest)
            raise upstream.OperationError(f'{exc}；备份与新会话状态：{folder}') from exc
