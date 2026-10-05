"""Storage/transport regression tests. Destructive paths use newly created local data only."""
import json,sqlite3,sys,tempfile,threading,time,unittest,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manager import IndexedStore,Maintenance,OfficialController,ManagerHandler,digest
from agent_repair import AgentRepairService,redacted_evidence,validate_report
from http.server import ThreadingHTTPServer
SID='00000000-0000-0000-0000-000000000101'
class ManagerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.home=self.root/'home';self.home.mkdir()
  self.p=self.home/'sessions'/f'rollout-2026-10-05-{SID}.jsonl';self.p.parent.mkdir()
  records=[{'type':'session_meta','payload':{'id':SID,'timestamp':'2026-10-05T00:00:00Z','cwd':'/workspace','originator':'codex_cli_rs','cli_version':'0.153.2','source':'cli'}},{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'Inspect the existing navigation.'}]}}]
  self.p.write_text('\n'.join(json.dumps(x) for x in records)+'\n')
  with sqlite3.connect(self.home/'state_5.sqlite') as c:
   c.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, title TEXT, cwd TEXT, created_at INTEGER,updated_at INTEGER,archived INTEGER,has_user_event INTEGER,source TEXT,history_mode TEXT,first_user_message TEXT)')
   c.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
   c.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?,?)',(SID,str(self.p),'Original','/workspace',1700000000,1700000000,0,1,'cli','legacy','Private message'))
  self.store=IndexedStore(self.home);self.controller=OfficialController(self.home,'/usr/local/bin/codex');self.m=Maintenance(self.store,self.controller,self.root/'data',runtime_probe=lambda:[])
 def tearDown(self):self.tmp.cleanup()
 def update(self,**kwargs):
  with sqlite3.connect(self.store.db_path) as c:c.execute('UPDATE threads SET '+','.join(k+'=?' for k in kwargs)+' WHERE id=?',list(kwargs.values())+[SID])
 def break_path(self):self.update(rollout_path=str(self.home/'old'/self.p.name))
 def test_healthy_scan(self):
  d=self.store.diagnose(SID);self.assertEqual(d['changes'],{});self.assertTrue(d['scan']['complete']);self.assertEqual(d['scan']['sha256'],digest(self.p))
 def test_list_and_preview(self):
  rows,total=self.store.list();self.assertEqual(total,1);self.assertTrue(rows[0]['local_path_exists']);self.assertEqual(len(self.store.get(SID)['messages']),1)
 def test_search_summary(self):self.assertEqual(self.store.list('Private')[1],1)
 def test_stale_path_exact_candidate(self):
  self.break_path();self.assertEqual(self.store.diagnose(SID)['changes'],{'rollout_path':str(self.p)})
 def test_visibility_restore(self):
  self.update(has_user_event=0);self.assertEqual(self.store.diagnose(SID)['changes'],{});self.assertEqual(self.store.diagnose(SID)['health'],'healthy')
 def test_repair_backups_preserves_history(self):
  self.break_path();before=digest(self.p);plan=self.m.plan('repair',SID);result=self.m.execute(plan['token'],plan['confirmation']);self.assertTrue(result['ok']);self.assertEqual(digest(self.p),before);self.assertEqual(self.store.row(SID)['rollout_path'],str(self.p));manifest=json.loads((Path(result['backup'])/'manifest.json').read_text());self.assertEqual(manifest['status'],'verified');self.assertEqual(manifest['files'][0]['sha256'],before)
 def test_runtime_guard(self):
  self.break_path();self.m.runtime_probe=lambda:[{'pid':1,'name':'codex'}];p=self.m.plan('repair',SID)
  with self.assertRaisesRegex(Exception,'退出'):self.m.execute(p['token'],p['confirmation'])
  self.assertFalse((self.root/'data/backups').exists())
 def test_wrong_confirmation(self):
  self.break_path();p=self.m.plan('repair',SID)
  with self.assertRaisesRegex(Exception,'确认'):self.m.execute(p['token'],'wrong')
 def test_stale_plan(self):
  self.break_path();p=self.m.plan('repair',SID);self.update(title='Changed')
  with self.assertRaisesRegex(Exception,'变化'):self.m.execute(p['token'],p['confirmation'])
 def test_plan_replay(self):
  self.break_path();p=self.m.plan('repair',SID);self.m.execute(p['token'],p['confirmation'])
  with self.assertRaisesRegex(Exception,'过期|使用'):self.m.execute(p['token'],p['confirmation'])
 def test_invalid_json_blocks(self):
  self.break_path();self.p.write_text(self.p.read_text()+'{broken\n');d=self.store.diagnose(SID);self.assertFalse(d['changes']);self.assertEqual(d['scan']['invalid_lines'],[3])
 def test_id_mismatch_blocks(self):
  self.break_path();self.p.write_text(self.p.read_text().replace(SID,'00000000-0000-0000-0000-000000000102'));self.assertFalse(self.store.diagnose(SID)['changes'])
 def test_multiple_candidates_block(self):
  self.break_path();other=self.home/'sessions/sub'/self.p.name;other.parent.mkdir();other.write_bytes(self.p.read_bytes());self.assertFalse(self.store.diagnose(SID)['changes'])
 def test_projection_missing_blocks(self):
  self.break_path();self.update(history_mode='paginated');self.assertFalse(self.store.diagnose(SID)['changes'])
 def test_bad_path_does_not_hide_library(self):
  self.update(rollout_path='bad');rows,total=self.store.list();self.assertEqual(total,1);self.assertIsNotNone(rows[0]['path_error']);self.assertFalse(rows[0]['local_path_exists'])
 def test_redaction(self):
  self.break_path();e=redacted_evidence(self.store.diagnose(SID));s=json.dumps(e);self.assertNotIn(SID,s);self.assertNotIn(str(self.home),s);self.assertNotIn('Original',s);self.assertNotIn('Private',s);self.assertEqual(e['allowed_repairs'],['repair_path'])
 def test_agent_action_whitelist(self):
  r={'summary':'x','reason':'x','actions':['delete_all'],'risks':[],'needs_manual_review':False}
  with self.assertRaises(Exception):validate_report(r,{'allowed_repairs':[]})
 def test_agent_conflicting_report(self):
  r={'summary':'x','reason':'x','actions':['repair_path','no_change'],'risks':[],'needs_manual_review':False}
  with self.assertRaises(Exception):validate_report(r,{'allowed_repairs':['repair_path']})
 def test_agent_consent(self):
  service=AgentRepairService(self.store,self.m,None)
  with self.assertRaises(ValueError):service.start(SID,False)
 def test_http_host_origin_csrf(self):
  class Handler(ManagerHandler):
   def log_message(self,*args):pass
  Handler.store=self.store;Handler.controller=self.controller;Handler.maintenance=self.m;Handler.csrf_token='test-only-token';Handler.agent=AgentRepairService(self.store,self.m,None)
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever);thread.start();base=f'http://127.0.0.1:{http.server_port}'
  client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  try:
   result=json.load(client.open(base+'/api/sessions'));self.assertEqual(result['total'],1)
   for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'}]:
    with self.assertRaises(urllib.error.HTTPError):client.open(urllib.request.Request(base+'/api/sessions',headers=headers))
   data=json.dumps({'action':'archive','id':SID}).encode()
   with self.assertRaises(urllib.error.HTTPError):client.open(urllib.request.Request(base+'/api/plans',data=data,headers={'Content-Type':'application/json'}))
   request=urllib.request.Request(base+'/api/plans',data=data,headers={'Content-Type':'application/json','X-Codex-CSRF':Handler.csrf_token});self.assertEqual(json.load(client.open(request))['action'],'archive')
  finally:http.shutdown();thread.join();http.server_close();Handler.agent.shutdown()
if __name__=='__main__':unittest.main(verbosity=2)
