import json,sqlite3,sys,time,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import test_manager
import test_build8
from manager import OfficialController
from history_index import scan_rollouts,history_references,dependency_order
SID=test_manager.SID
class Build11Tests(unittest.TestCase):
 def setUp(self):
  self.c=test_manager.ManagerTests();self.c.setUp();self.m=self.c.m;self.s=self.c.store
  h=test_build8.Build8Tests();h.case=self.c;h.m=self.m;h.store=self.s;self.h=h
 def tearDown(self):self.c.tearDown()
 def fork(self,child,parent,base=True):
  p=self.s.path(self.s.row(child));lines=p.read_text().splitlines();e=json.loads(lines[0]);e['payload']['forked_from_id']=parent
  if base:e['payload']['history_base']={'thread_id':parent,'end_byte_offset':10,'end_ordinal_exclusive':1}
  lines[0]=json.dumps(e);p.write_text('\n'.join(lines)+'\n')
 def test_selected_fork_runs_before_source_in_any_selection_order(self):
  child=self.h.add(1)[0];self.fork(child,SID)
  for ids in [[SID,child],[child,SID]]:
   p=self.m.batch_plan('delete',ids);self.assertTrue(p['dependency_ordered']);self.assertEqual(p['blocked'],[])
   self.assertEqual([m['id'] for m in self.m.plans[p['token']]['members']],[child,SID])
  with patch.object(self.c.controller,'delete',side_effect=self.h.delete) as d:r=self.m.execute(p['token'],p['confirmation'])
  self.assertTrue(r['ok']);self.assertEqual([c.args[0] for c in d.call_args_list],[child,SID])
 def test_unselected_reference_is_preflight_blocked_not_auto_selected(self):
  child=self.h.add(1)[0];self.fork(child,SID);p=self.m.batch_plan('delete',[SID])
  self.assertEqual(p['ids'],[]);self.assertEqual(p['blocked'][0]['id'],SID);self.assertIn(child[:8],p['blocked'][0]['reason'])
  with self.assertRaisesRegex(Exception,'未选择'):self.m.plan('delete',SID)
 def test_archived_fork_is_included_in_dependencies(self):
  child=self.h.add(1)[0];self.fork(child,SID);src=self.s.path(self.s.row(child));dst=self.c.home/'archived_sessions'/src.name;dst.parent.mkdir();src.rename(dst)
  with closing(sqlite3.connect(self.s.db_path)) as c,c:c.execute('UPDATE threads SET archived=1,rollout_path=? WHERE id=?',(str(dst),child))
  p=self.m.batch_plan('delete',[SID,child]);self.assertEqual(p['targets'][0]['id'],child)
 def test_legacy_copied_fork_without_history_base_does_not_block(self):
  child=self.h.add(1)[0];self.fork(child,SID,base=False)
  self.assertEqual(self.m.plan('delete',SID)['delete_dependencies'],[])
 def test_multilevel_reverse_dependency_order(self):
  b,c=self.h.add(2);self.fork(b,SID);self.fork(c,b);p=self.m.batch_plan('delete',[SID,b,c]);self.assertEqual([t['id'] for t in p['targets']],[c,b,SID])
 def test_cycle_stops_before_mutation(self):
  child=self.h.add(1)[0];self.fork(child,SID);self.fork(SID,child)
  with self.assertRaisesRegex(ValueError,'循环'):self.m.batch_plan('delete',[SID,child])
 def test_new_reference_after_confirmation_stops_whole_batch(self):
  child=self.h.add(1)[0];p=self.m.batch_plan('delete',[SID]);self.fork(child,SID)
  with patch.object(self.c.controller,'delete') as d:
   with self.assertRaisesRegex(Exception,'未选择|变化'):self.m.execute(p['token'],p['confirmation'])
   d.assert_not_called()
 def test_scope_does_not_invent_fork_cascade_for_archive(self):
  child=self.h.add(1)[0];self.fork(child,SID);self.assertEqual(self.m.plan('archive',SID)['ids'],[SID])
 def test_symlink_cycles_and_duplicate_roots_terminate(self):
  (self.c.home/'sessions/loop').symlink_to(self.c.home/'sessions',target_is_directory=True)
  files=scan_rollouts([self.c.home/'sessions',self.c.home/'sessions/loop']);self.assertEqual(files,[self.c.p])
 def test_unindexed_fork_still_blocks_source(self):
  child=self.h.add(1)[0];self.fork(child,SID)
  with closing(sqlite3.connect(self.s.db_path)) as c,c:c.execute('DELETE FROM threads WHERE id=?',(child,))
  with self.assertRaisesRegex(Exception,'未选择'):self.m.plan('delete',SID)
 def test_active_append_keeps_unchanged_reference_header_valid(self):
  child=self.h.add(1)[0];self.fork(child,SID);path=self.s.path(self.s.row(child))
  real=Path.open;changed=[]
  class Reader:
   def __init__(self,f):self.f=f
   def __enter__(self):return self
   def __exit__(self,*a):self.f.close()
   def readline(self,n):
    line=self.f.readline(n)
    if not changed:
     changed.append(True)
     with real(path,'ab') as f:f.write(b'{"type":"event_msg","payload":{}}\n')
    return line
  def opened(p,*a,**kw):
   f=real(p,*a,**kw)
   return Reader(f) if p==path and a and a[0]=='rb' else f
  with patch.object(Path,'open',new=opened):refs=history_references([path])
  self.assertEqual(refs,[(child,SID)])
 def test_conflicting_rollout_ownership_stops_preflight(self):
  child=self.h.add(1)[0];p=self.c.root/'other'/self.c.p.name;p.parent.mkdir();p.write_text(self.c.p.read_text().replace(SID,child))
  with self.assertRaisesRegex(ValueError,'不同会话'):history_references([self.c.p,p])
 def test_malformed_rollout_filename_is_not_hidden_from_lookup_guard(self):
  p=self.c.home/'sessions/rollout-malformed.jsonl';p.write_text('{}\n')
  self.assertIn(p,scan_rollouts([self.c.home/'sessions']))
 def engine(self,body):
  p=self.c.root/'engine';p.write_text('#!'+sys.executable+'\nimport json,sys,time\nfor line in sys.stdin:\n m=json.loads(line)\n if m.get("method")=="initialize":print(json.dumps({"id":m["id"],"result":{}}),flush=True)\n elif m.get("method")=="thread/delete":\n'+body);p.chmod(0o700);return OfficialController(self.c.home,str(p))
 def test_delete_deadline_is_independent_of_normal_rpc(self):
  ctl=self.engine('  time.sleep(.25);print(json.dumps({"id":m["id"],"result":{}}),flush=True)\n');ctl.rpc_timeout=.05;ctl.delete_timeout=1
  d=self.c.root/'logs';d.mkdir()
  with ctl.diagnostics(d):ctl.delete(SID)
  events=json.loads((d/'official-events.json').read_text())['events'];self.assertTrue(any(e['event']=='response_received' and e['success'] for e in events))
 def test_timeout_has_diagnostics_without_resubmitting(self):
  marker=self.c.root/'calls';ctl=self.engine(f'  open({str(marker)!r},"a").write("once\\n");print("Bearer SECRET_VALUE sk-secretvalue",file=sys.stderr,flush=True);time.sleep(2)\n');ctl.delete_timeout=.1
  d=self.c.root/'logs';d.mkdir()
  with ctl.diagnostics(d):
   with self.assertRaisesRegex(Exception,'超时'):ctl.delete(SID)
  self.assertEqual(marker.read_text(),'once\n');log=(d/'official-stderr.log').read_text();self.assertNotIn('SECRET_VALUE',log);self.assertNotIn('sk-secretvalue',log)
  self.assertIn('response_timeout',[e['event'] for e in json.loads((d/'official-events.json').read_text())['events']])
