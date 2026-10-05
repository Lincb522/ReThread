"""Project deletion scope, partial failures and migration-state regressions."""
import json,sqlite3,unittest,os,time
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import test_manager as base_tests
import test_build8 as batch_tests
from manager import BatchJobs
SID=base_tests.SID
class ProjectDeleteTests(unittest.TestCase):
 def setUp(self):
  self.c=base_tests.ManagerTests();self.c.setUp();self.m=self.c.m;self.s=self.c.store;self.h=self.c.home
  self.helper=batch_tests.Build8Tests();self.helper.case=self.c;self.helper.m=self.m;self.helper.store=self.s
  self.state={'local-projects':{'chosen':{'name':'完整项目','rootPaths':['/workspace']},'other':{'name':'完整项目','rootPaths':['/another']}},'project-order':['chosen','other'],'pinned-project-ids':['chosen','other'],'selected-project':{'type':'local','projectId':'chosen'},'unrelated':{'settings':123}}
  self.save()
 def tearDown(self):self.c.tearDown()
 def save(self):(self.h/'.codex-global-state.json').write_text(json.dumps(self.state))
 def plan(self,pid='chosen'):return self.m.project_maintenance.plan(pid)
 def execute(self,p):
  with patch.object(self.c.controller,'delete',side_effect=self.helper.delete):return self.m.execute(p['token'],p['confirmation'])
 def test_whole_project_deletion_keeps_other_same_named_project(self):
  p=self.plan();r=self.execute(p);self.assertTrue(r['ok']);self.assertEqual(r['affected'],1)
  state=json.loads((self.h/'.codex-global-state.json').read_text());self.assertEqual(state['project-order'],['other']);self.assertEqual(state['pinned-project-ids'],['other']);self.assertNotIn('selected-project',state);self.assertEqual(state['unrelated'],self.state['unrelated'])
  self.assertEqual(set(self.s.project_catalog().projects),{'other'});self.assertEqual(json.loads((Path(r['backup'])/'manifest.json').read_text())['status'],'verified');self.assertEqual(self.m.history()[0]['action'],'project_delete')
 def test_empty_project_can_be_deleted(self):
  p=self.plan('other');self.assertEqual(p['ids'],[]);r=self.execute(p);self.assertTrue(r['ok']);self.assertEqual(r['affected'],0);self.assertEqual(self.s.row(SID)['id'],SID)
 def test_public_plan_does_not_leak_snapshot(self):
  p=self.plan();self.assertNotIn('project_snapshot',p);self.assertNotIn('members',p);self.assertTrue(p['requires_exit'])
 def test_archived_and_hidden_rows_not_filtered(self):
  ids=self.helper.add(2)
  with closing(sqlite3.connect(self.s.db_path)) as db,db:
   db.execute('UPDATE threads SET archived=1 WHERE id=?',(ids[0],));db.execute('UPDATE threads SET source=? WHERE id=?',('{"subagent":"review"}',ids[1]))
  p=self.plan();self.assertEqual(set(p['ids']),{SID,*ids});self.assertEqual(p['archived_count'],1);self.assertEqual(p['hidden_count'],1)
 def test_large_project_has_no_selection_limit(self):
  ids=self.helper.add(80);p=self.plan();self.assertEqual(len(p['ids']),81)
 def test_explicit_projectless_preserved(self):
  self.state['projectless-thread-ids']=[SID];self.save();p=self.plan();self.assertEqual(p['ids'],[]);self.assertTrue(self.execute(p)['ok']);self.assertEqual(self.s.row(SID)['id'],SID)
 def test_current_chat_blocks_entire_project(self):
  with patch.dict(os.environ,{'CODEX_THREAD_ID':SID}):p=self.plan()
  self.assertTrue(p['blocked'])
  with patch.object(self.c.controller,'delete') as delete:
   with self.assertRaisesRegex(Exception,'预检受限'):self.m.execute(p['token'],p['confirmation'])
   delete.assert_not_called()
 def test_cross_project_child_stops_before_plan(self):
  child=self.helper.add(1)[0]
  with closing(sqlite3.connect(self.s.db_path)) as db,db:db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',(SID,child));db.execute('UPDATE threads SET cwd=? WHERE id=?',('/another',child))
  with self.assertRaisesRegex(Exception,'其他项目'):self.plan()
 def test_runtime_guard_before_any_mutation(self):
  p=self.plan();self.m.runtime_probe=lambda:[{'pid':1}]
  with patch.object(self.c.controller,'delete') as delete:
   with self.assertRaisesRegex(Exception,'退出'):self.m.execute(p['token'],p['confirmation'])
   delete.assert_not_called()
  self.assertTrue(self.c.p.exists());self.assertTrue('chosen' in self.s.project_catalog().projects)
 def test_membership_added_after_confirmation_blocks(self):
  p=self.plan();self.helper.add(1)
  with self.assertRaisesRegex(Exception,'变化'):self.execute(p)
  self.assertTrue(self.c.p.exists())
 def test_membership_moved_after_confirmation_blocks(self):
  p=self.plan();self.state['projectless-thread-ids']=[SID];self.save()
  with self.assertRaisesRegex(Exception,'变化'):self.execute(p)
 def test_renamed_project_invalidates_confirmation(self):
  p=self.plan();self.state['local-projects']['chosen']['name']='renamed';self.save()
  with self.assertRaisesRegex(Exception,'变化'):self.execute(p)
 def test_wrong_confirmation_and_replay(self):
  p=self.plan()
  with self.assertRaisesRegex(Exception,'确认'):self.m.execute(p['token'],'wrong')
  with self.assertRaises(Exception):self.execute(p)
 def test_expired_plan(self):
  p=self.plan();self.m.plans[p['token']]['expires']=0
  with self.assertRaisesRegex(Exception,'过期'):self.execute(p)
 def test_first_failure_stops_all_and_keeps_project(self):
  self.helper.add(2);p=self.plan()
  with patch.object(self.c.controller,'delete',side_effect=RuntimeError('rejected')) as delete:r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);self.assertEqual(delete.call_count,1);self.assertEqual([o['status'] for o in r['outcomes']],['failed','not_executed','not_executed']);self.assertIn('chosen',self.s.project_catalog().projects)
 def test_later_failure_reports_verified_count(self):
  self.helper.add(1);p=self.plan();calls=[]
  def delete(sid):
   calls.append(sid)
   if len(calls)>1:raise RuntimeError('rejected')
   self.helper.delete(sid)
  with patch.object(self.c.controller,'delete',side_effect=delete):r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);self.assertEqual(r['affected'],1);self.assertIn('chosen',self.s.project_catalog().projects)
 def test_runtime_restarts_between_members_stops_remaining(self):
  self.helper.add(1);p=self.plan()
  def delete(sid):self.helper.delete(sid);self.m.runtime_probe=lambda:[{'pid':2}]
  with patch.object(self.c.controller,'delete',side_effect=delete) as d:r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);self.assertEqual(d.call_count,1);self.assertEqual(r['affected'],1);self.assertIn('chosen',self.s.project_catalog().projects)
 def test_stop_after_current_keeps_project(self):
  self.helper.add(1);p=self.plan();stop=[]
  def delete(sid):self.helper.delete(sid);stop.append(True)
  with self.m.observing(lambda *a,**kw:None,lambda:bool(stop)),patch.object(self.c.controller,'delete',side_effect=delete):r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);self.assertEqual(r['affected'],1);self.assertIn('chosen',self.s.project_catalog().projects)
 def test_assignment_changes_during_batch_stops_next(self):
  extra=self.helper.add(1)[0];p=self.plan()
  def delete(sid):
   self.helper.delete(sid);self.state['thread-project-assignments']={extra:{'projectKind':'local','projectId':'other'}};self.save()
  with patch.object(self.c.controller,'delete',side_effect=delete) as d:r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);self.assertEqual(d.call_count,1);self.assertIn('chosen',self.s.project_catalog().projects)
 def test_source_directory_never_removed(self):
  root=self.c.root/'source';root.mkdir();(root/'a.swift').write_text('keep');self.state['local-projects']['other']['rootPaths']=[str(root)];self.save();self.execute(self.plan('other'));self.assertEqual((root/'a.swift').read_text(),'keep')
 def test_metadata_backup_is_scoped(self):
  self.state['private-credentials']='SECRET_DO_NOT_COPY';self.save();r=self.execute(self.plan());data=(Path(r['backup'])/'project-metadata.json').read_text();self.assertNotIn('SECRET_DO_NOT_COPY',data);self.assertNotIn('private-credentials',data)
 def test_state_symlink_rejected(self):
  path=self.h/'.codex-global-state.json';target=self.h/'state.json';path.rename(target);path.symlink_to(target)
  with self.assertRaisesRegex(Exception,'软链接'):self.plan()
 def test_async_project_plan_and_execute(self):
  jobs=BatchJobs(self.m)
  try:
   job=self.helper.wait(jobs,jobs.start('plan',{'action':'project_delete','project_id':'other'}));self.assertEqual(job['state'],'completed');p=job['plan'];self.assertEqual(p['ids'],[])
   job=self.helper.wait(jobs,jobs.start('execute',{'token':p['token'],'confirmation':p['confirmation']}));self.assertTrue(job['result']['ok'])
  finally:jobs.shutdown()
 def test_sql_identity_probe_rejection_before_delete(self):
  with closing(sqlite3.connect(self.s.db_path)) as db,db:
   db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,position INT)');db.execute('CREATE TABLE project_roots(project_id TEXT,path TEXT,position INT)');db.execute("INSERT INTO projects VALUES('chosen','完整项目',0)");db.execute("INSERT INTO project_roots VALUES('chosen','/workspace',0)")
  p=self.plan()
  with patch.object(self.c.controller,'_rpc_unlocked',side_effect=RuntimeError('project/read unsupported')),patch.object(self.c.controller,'delete') as delete:
   with self.assertRaisesRegex(Exception,'unsupported'):self.execute(p)
   delete.assert_not_called()
 def test_project_rpc_failure_does_not_use_sql_delete(self):
  with closing(sqlite3.connect(self.s.db_path)) as db,db:
   db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,position INT)');db.execute('CREATE TABLE project_roots(project_id TEXT,path TEXT,position INT)');db.execute("INSERT INTO projects VALUES('chosen','完整项目',0)");db.execute("INSERT INTO project_roots VALUES('chosen','/workspace',0)")
  p=self.plan();calls=[]
  def rpc(method,params):
   calls.append(method)
   if method=='project/read':return {'project':{'id':'chosen','name':'完整项目','roots':[{'path':'/workspace'}]}}
   raise RuntimeError('official project delete rejected')
  with patch.object(self.c.controller,'_rpc_unlocked',side_effect=rpc):r=self.execute(p)
  self.assertFalse(r['ok']);self.assertEqual(r['affected'],1);self.assertEqual(calls,['project/read','project/delete'])
  with self.s.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM projects WHERE id='chosen'").fetchone()[0],1)
  self.assertIn('chosen',json.loads((self.h/'.codex-global-state.json').read_text())['local-projects'])
 def test_unrelated_settings_changed_during_batch_are_preserved(self):
  p=self.plan()
  def delete(sid):self.helper.delete(sid);self.state['unrelated']['settings']=456;self.save()
  with patch.object(self.c.controller,'delete',side_effect=delete):r=self.m.execute(p['token'],p['confirmation'])
  self.assertTrue(r['ok']);self.assertEqual(json.loads((self.h/'.codex-global-state.json').read_text())['unrelated']['settings'],456)
 def test_nested_project_added_between_members_protects_new_owner(self):
  extra=self.helper.add(1)[0]
  with closing(sqlite3.connect(self.s.db_path)) as db,db:db.execute('UPDATE threads SET cwd=? WHERE id=?',('/workspace/sub',extra))
  p=self.plan()
  def delete(sid):
   self.helper.delete(sid);self.state['local-projects']['nested']={'name':'新子项目','rootPaths':['/workspace/sub']};self.save()
  with patch.object(self.c.controller,'delete',side_effect=delete) as d:r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);self.assertEqual(d.call_count,1);self.assertEqual(self.s.row(extra)['id'],extra)
 def test_membership_change_during_backup_stops_before_mutation(self):
  p=self.plan();original=self.m.backup
  def backup(plan):
   value=original(plan);self.state['projectless-thread-ids']=[SID];self.save();return value
  with patch.object(self.m,'backup',side_effect=backup),patch.object(self.c.controller,'delete') as d:r=self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(r['ok']);d.assert_not_called();self.assertTrue(self.c.p.exists())
 def test_selected_remote_project_preserved(self):
  self.state['selected-project']={'type':'remote','projectId':'chosen'};self.save();self.execute(self.plan());self.assertEqual(json.loads((self.h/'.codex-global-state.json').read_text())['selected-project']['type'],'remote')
if __name__=='__main__':unittest.main(verbosity=2)
