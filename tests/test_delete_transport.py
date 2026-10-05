import json,os,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_manager
SID = test_manager.SID
from manager import OfficialController,OfficialRPCError,resolve_maintenance_command

class EngineSelectionTests(unittest.TestCase):
 def test_explicit_missing_does_not_select_another_engine(self):
  with self.assertRaisesRegex(Exception,'指定的 Codex'):
   resolve_maintenance_command('/missing/rethread-codex',environ={'PATH':'/usr/local/bin'})
 def test_desktop_engine_precedes_standalone_path(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);desktop=root/'apps/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
   desktop.parent.mkdir(parents=True);desktop.write_text('#!/bin/sh\n');desktop.chmod(0o700)
   cli=root/'bin/codex';cli.parent.mkdir();cli.write_text('#!/bin/sh\n');cli.chmod(0o700)
   self.assertEqual(resolve_maintenance_command(environ={'PATH':str(cli.parent)},desktop_roots=[root/'apps']),str(desktop.resolve()))
   self.assertEqual(resolve_maintenance_command(str(cli),environ={},desktop_roots=[root/'apps']),str(cli.resolve()))
 def test_delete_has_one_rpc_and_no_cli_retry(self):
  ctl=OfficialController(Path('/tmp'),'/usr/local/bin/codex')
  with patch.object(ctl,'_rpc_unlocked',side_effect=OfficialRPCError('thread/delete',-32000,'database is locked')) as rpc,patch.object(ctl,'_run_cli_unlocked') as cli:
   with self.assertRaisesRegex(OfficialRPCError,'database is locked'):ctl.delete(SID)
   rpc.assert_called_once_with('thread/delete',{'threadId':SID});cli.assert_not_called()

class DeletionStateTests(unittest.TestCase):
 def setUp(self):
  self.case=test_manager.ManagerTests();self.case.setUp();self.m=self.case.m;self.home=self.case.home
 def tearDown(self):self.case.tearDown()
 def test_rpc_failure_preserves_error_and_marks_unchanged(self):
  plan=self.m.plan('delete',SID)
  with patch.object(self.case.controller,'_rpc_unlocked',side_effect=OfficialRPCError('thread/delete',-32000,'database is locked')):
   with self.assertRaisesRegex(Exception,'database is locked'):self.m.execute(plan['token'],plan['confirmation'])
  manifest=json.loads(next((self.case.root/'data/backups').glob('*/manifest.json')).read_text())
  self.assertEqual(manifest['status'],'failed_unchanged');self.assertEqual(manifest['rpc_error']['code'],-32000)
  self.assertIn('binary',manifest['engine']);self.assertTrue(self.case.p.exists());self.case.store.row(SID)
 def test_partial_change_is_not_reported_unchanged(self):
  plan=self.m.plan('delete',SID)
  def fail(*a):
   self.case.update(title='changed during operation')
   raise OfficialRPCError('thread/delete',-32000,'partial operation')
  with patch.object(self.case.controller,'_rpc_unlocked',side_effect=fail):
   with self.assertRaises(Exception):self.m.execute(plan['token'],plan['confirmation'])
  manifest=json.loads(next((self.case.root/'data/backups').glob('*/manifest.json')).read_text())
  self.assertEqual(manifest['status'],'failed_or_partial')
 def test_auxiliary_rows_scoped_and_verified(self):
  other='00000000-0000-0000-0000-000000000999'
  for name in ('memories_1.sqlite','goals_1.sqlite','queue_1.sqlite','thread_history_1.sqlite'):
   with sqlite3.connect(self.home/name) as c:
    c.execute('CREATE TABLE related(thread_id TEXT,value TEXT)')
    c.executemany('INSERT INTO related VALUES (?,?)',[(SID,'target'),(other,'leave alone')])
  records=self.m.scoped_records([SID]);self.assertEqual(len(records),5)
  self.assertTrue(all(other not in r[2] for r in records))
  plan=self.m.plan('delete',SID);folder,manifest=self.m.backup(plan)
  with sqlite3.connect(folder/'metadata.sqlite') as c:self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],5)
  with sqlite3.connect(self.home/'state_5.sqlite') as c:c.execute('DELETE FROM threads WHERE id=?',(SID,))
  with self.assertRaisesRegex(Exception,'关联记录'):self.m.verify(plan)
  for name in ('memories_1.sqlite','goals_1.sqlite','queue_1.sqlite','thread_history_1.sqlite'):
   with sqlite3.connect(self.home/name) as c:c.execute('DELETE FROM related WHERE thread_id=?',(SID,))
  self.m.verify(plan)

class StorageBoundaryTests(unittest.TestCase):
 def setUp(self):self.case=test_manager.ManagerTests();self.case.setUp()
 def tearDown(self):self.case.tearDown()
 def test_nested_symlink_delete_preflight_supported_archive_requires_relocation(self):
  nested=self.case.home/'sessions/offloaded';external=self.case.root/'external';external.mkdir();nested.symlink_to(external,target_is_directory=True)
  dest=nested/self.case.p.name;self.case.p.rename(dest);self.case.update(rollout_path=str(dest))
  for action in ('archive',):
   with self.assertRaisesRegex(Exception,'软链接'):self.case.m.plan(action,SID)
  self.assertTrue(self.case.m.plan('delete',SID)['external_delete'])
  self.assertTrue(dest.exists());self.assertFalse((self.case.root/'data/backups').exists())
  self.assertIn('external_storage_path',[x['code'] for x in self.case.store.diagnose(SID)['issues']])
 def test_whole_storage_root_symlink_is_supported(self):
  sessions=self.case.home/'sessions';external=self.case.root/'all-sessions';sessions.rename(external);sessions.symlink_to(external,target_is_directory=True)
  self.assertIsNone(self.case.store.storage_boundary_issue(self.case.store.row(SID)))
  self.assertEqual(self.case.m.plan('delete',SID)['ids'],[SID])
