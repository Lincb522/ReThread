import hashlib,json,os,sqlite3,unittest,tracemalloc
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import test_manager as base
import test_build8 as batch
from manager import BatchJobs
from merge_stream import feed
SID=base.SID

class Build16Tests(unittest.TestCase):
 def setUp(self):
  self.c=base.ManagerTests();self.c.setUp();self.m=self.c.m;self.s=self.c.store
  self.helper=batch.Build8Tests();self.helper.case=self.c;self.helper.m=self.m;self.helper.store=self.s
  self.c.update(cwd=str(self.c.root))
  with closing(sqlite3.connect(self.s.db_path)) as d,d:
   d.execute('ALTER TABLE threads ADD COLUMN project_id TEXT')
   d.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,position INTEGER)')
   d.execute('CREATE TABLE project_roots(project_id TEXT,position INTEGER,path TEXT)')
   d.execute('INSERT INTO projects VALUES (?,?,?)',('p1','同名项目',0));d.execute('INSERT INTO project_roots VALUES (?,?,?)',('p1',0,str(self.c.root)))
  self.state={'local-projects':{'old':{'id':'old','name':'同名项目','rootPaths':[str(self.c.root)]}},'project-order':['old'],'projectless-thread-ids':[SID], 'app-server-project-id-by-legacy-project-id-by-host':{'local:'+str(self.c.home):{'old':'p1'}},'secret-unrelated':'must remain'}
  self.save();self.calls=[]
 def tearDown(self):self.c.tearDown()
 def save(self):(self.c.home/'.codex-global-state.json').write_text(json.dumps(self.state))
 def ids(self,n):
  import uuid
  ids=[SID]
  with closing(sqlite3.connect(self.s.db_path)) as d,d:
   for _ in range(n-1):
    sid=str(uuid.uuid4());ids.append(sid);path=self.c.p.with_name('rollout-'+sid+'.jsonl');path.write_text(self.c.p.read_text().replace(SID,sid))
    d.execute('INSERT INTO threads SELECT ?,?,title,cwd,created_at,updated_at,archived,has_user_event,source,history_mode,first_user_message,project_id FROM threads WHERE id=?',(sid,str(path),SID))
  return ids
 def rpc(self,method,p):
  self.calls.append((method,p))
  with closing(sqlite3.connect(self.s.db_path)) as d,d:
   d.row_factory=sqlite3.Row
   if method=='project/create':
    d.execute('INSERT INTO projects VALUES (?,?,?)',('new',p['name'],1));d.execute('INSERT INTO project_roots VALUES (?,?,?)',('new',0,p['roots'][0]['path']));return {'project':{'id':'new','name':p['name'],'roots':p['roots']}}
   if method=='project/read':
    row=dict(d.execute('SELECT * FROM projects WHERE id=?',(p['projectId'],)).fetchone());row['roots']=[{'path':x[0]} for x in d.execute('SELECT path FROM project_roots WHERE project_id=?',(p['projectId'],))];return {'project':row}
   if method=='thread/metadata/update':d.execute('UPDATE threads SET project_id=? WHERE id=?',(p['projectId'],p['threadId']))
   row=dict(d.execute('SELECT * FROM threads WHERE id=?',(p['threadId'],)).fetchone());return {'thread':{'id':row['id'],'projectId':row['project_id']}}
 def execute(self,p):
  from contextlib import contextmanager
  @contextmanager
  def session():yield self.rpc
  with patch.object(self.c.controller,'_rpc_unlocked',side_effect=self.rpc),patch.object(self.c.controller,'rpc_session',session):return self.m.execute(p['token'],p['confirmation'])
 def test_merge_more_than_ten(self):
  p=self.m.merge_plan(self.ids(12),'merged');self.assertEqual(len(p['ids']),12);self.assertTrue(p['batch']);self.assertNotIn('content_hash',p)
 def test_merge_over_ten_thousand_messages(self):
  ids=self.ids(2);line=json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'text','text':'full'}]}})+'\n'
  with self.c.p.open('a') as f:f.write(line*10010)
  p=self.m.merge_plan(ids,'merged');self.assertEqual(p['message_count'],10012)
 def test_merge_above_64_mib(self):
  ids=self.ids(2);line=json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'text','text':'x'*(1024*1024)}]}})+'\n'
  with self.c.p.open('a') as f:
   for _ in range(65):f.write(line)
  p=self.m.merge_plan(ids,'merged');self.assertGreater(p['file_bytes'],64*1024*1024);self.assertEqual(p['message_count'],67)
 def test_merge_rejects_one(self):
  with self.assertRaises(ValueError):self.m.merge_plan([SID],'merged')
 def test_merge_rejects_duplicate(self):
  with self.assertRaises(ValueError):self.m.merge_plan([SID,SID],'merged')
 def test_merge_stale_prevents_backup_and_fork(self):
  p=self.m.merge_plan(self.ids(2),'merged');self.c.update(title='changed')
  with patch.object(self.m,'backup') as backup:
   with self.assertRaisesRegex(Exception,'变化'):self.m.execute(p['token'],p['confirmation'])
   backup.assert_not_called()
 def test_merge_stop_during_scan(self):
  with self.m.observing(lambda *a,**kw:None,lambda:True):
   with self.assertRaisesRegex(Exception,'停止'):self.m.merge_plan(self.ids(2),'merged')
 def test_streamed_exact_verification(self):
  h=hashlib.sha256();feed(h,['user','第一条']);feed(h,['assistant','最后一条'])
  items=iter([{'type':'userMessage','content':[{'type':'text','text':'第一条'}]},{'type':'agentMessage','text':'最后一条'}])
  self.m.merger.verify_items(items,h.hexdigest(),2)
 def test_stream_transport_preserves_full_unicode(self):
  import io
  from stream_json import responses,visible_items,thread_identity
  path=self.c.root/'response.json';raw={'id':2,'result':{'thread':{'id':SID,'name':'中文名称','turns':[{'items':[{'type':'agentMessage','text':'中文'*40000}]}]}}}
  out=list(responses(io.StringIO(json.dumps(raw,ensure_ascii=False)+'\n'),path));self.assertEqual(out[0]['result']['stream_path'],str(path));self.assertEqual(next(visible_items(path))['text'],'中文'*40000);self.assertEqual(thread_identity(path),{'id':SID,'name':'中文名称'})
 def test_parser_buffer_does_not_accumulate_consumed_tokens(self):
  from stream_json import parser
  path=self.c.root/'long.json'
  with path.open('w') as f:
   f.write('[')
   for i in range(40):f.write((',' if i else '')+json.dumps('完整'+('x'*98304)))
   f.write(']')
  tracemalloc.start()
  with path.open('rb') as f:
   count=sum(1 for prefix,event,value in parser.parse(f) if prefix=='item' and event=='string')
  _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();self.assertEqual(count,40);self.assertLess(peak,8*1024*1024)
 def test_merge_sync_requires_verified_manifest(self):
  with self.assertRaisesRegex(Exception,'已验证'):self.m.verify_merge_desktop_sync(SID)
 def test_merge_sync_does_not_fake_desktop_confirmation(self):
  folder=self.m.root/'backups/merge-sync';folder.mkdir(parents=True)
  (folder/'manifest.json').write_text(json.dumps({'action':'merge','new_id':SID,'status':'verified','codex_home':str(self.s.codex_home),'database':str(self.s.db_path)}))
  with patch.object(self.m.desktop_sync,'catalog_rows',return_value=set()):self.assertEqual(self.m.verify_merge_desktop_sync(SID)['status'],'pending')
  with patch.object(self.m.desktop_sync,'catalog_rows',return_value={SID}):self.assertTrue(self.m.verify_merge_desktop_sync(SID)['catalog_verified'])
 def test_stream_transport_rejects_truncation(self):
  import io
  from stream_json import responses
  with self.assertRaises(ValueError):list(responses(io.StringIO('{"id":2'),self.c.root/'response.json'))
 def test_stream_transport_surfaces_official_errors(self):
  import io
  from stream_json import responses
  values=list(responses(io.StringIO('{"id":2,"error":{"code":-1,"message":"error"}}\n'),self.c.root/'response.json'));self.assertEqual(values[0]['error']['message'],'error');self.assertFalse((self.c.root/'response.json').exists())
 def test_changed_message_rejected(self):
  with self.assertRaisesRegex(Exception,'不一致'):self.m.merger.verify_items(iter([]),'bad',1)
 def test_project_plan_private_snapshot_hidden(self):
  p=self.m.project_assignment.plan([SID],'p1');self.assertTrue(p['requires_exit']);self.assertNotIn('project_snapshot',p);self.assertNotIn('secret-unrelated',json.dumps(p))
 def test_project_move_persists_official_and_legacy(self):
  p=self.m.project_assignment.plan([SID],'p1');before=self.c.p.read_bytes();r=self.execute(p);self.assertTrue(r['ok']);self.assertEqual(self.c.p.read_bytes(),before)
  state=json.loads((self.c.home/'.codex-global-state.json').read_text());self.assertEqual(state['thread-project-assignments'][SID]['projectId'],'old');self.assertNotIn(SID,state['projectless-thread-ids']);self.assertEqual(state['secret-unrelated'],'must remain');self.assertEqual(self.s.project_catalog().resolve(self.s.row(SID))['project_key'],'p1')
 def test_new_project_does_not_capture_other_chats(self):
  ids=self.ids(2);other=ids[1];before=self.s.project_catalog().resolve(self.s.row(other))['project_key'];p=self.m.project_assignment.plan([SID],None,'新项目',str(self.c.root));r=self.execute(p);self.assertTrue(r['ok']);self.assertEqual(self.s.project_catalog().resolve(self.s.row(SID))['project_key'],'new');self.assertEqual(self.s.project_catalog().resolve(self.s.row(other))['project_key'],before)
 def test_runtime_guard_before_any_rpc(self):
  p=self.m.project_assignment.plan([SID],'p1');self.m.runtime_probe=lambda:[1]
  with self.assertRaisesRegex(Exception,'退出'):self.execute(p)
  self.assertFalse(self.calls)
 def test_project_stale_aborts(self):
  p=self.m.project_assignment.plan([SID],'p1');self.c.update(title='new title')
  with self.assertRaisesRegex(Exception,'变化'):self.execute(p)
  self.assertFalse(self.calls)
 def test_project_one_use_plan(self):
  p=self.m.project_assignment.plan([SID],'p1');self.execute(p)
  with self.assertRaises(Exception):self.execute(p)
 def test_project_unknown_target_rejected(self):
  with self.assertRaisesRegex(Exception,'不存在'):self.m.project_assignment.plan([SID],'absent')
 def test_project_bad_root_rejected(self):
  with self.assertRaises(ValueError):self.m.project_assignment.plan([SID],None,'new','relative')
 def test_project_conflicting_parameters_rejected(self):
  with self.assertRaises(ValueError):self.m.project_assignment.plan([SID],'p1','new',str(self.c.root))
 def test_project_current_thread_rejected(self):
  with patch.dict(os.environ,{'CODEX_THREAD_ID':SID}):
   with self.assertRaisesRegex(Exception,'当前'):self.m.project_assignment.plan([SID],'p1')
 def test_official_failure_not_masked_by_legacy_write(self):
  p=self.m.project_assignment.plan([SID],None,'new',str(self.c.root));before=(self.c.home/'.codex-global-state.json').read_bytes()
  with patch.object(self.c.controller,'_rpc_unlocked',side_effect=RuntimeError('official failure')) as rpc:
   with self.assertRaisesRegex(Exception,'official failure'):self.m.execute(p['token'],p['confirmation'])
   self.assertEqual(rpc.call_count,1)
  self.assertEqual(before,(self.c.home/'.codex-global-state.json').read_bytes())
 def test_background_merge_preflight(self):
  jobs=BatchJobs(self.m);job=jobs.start('plan',{'action':'merge','ids':self.ids(12),'title':'background'})
  jobs.worker.join(10);result=jobs.get(job['id']);self.assertEqual(result['state'],'completed');self.assertEqual(result['plan']['action'],'merge');self.assertEqual(result['plan']['message_count'],12)
if __name__=='__main__':unittest.main()
