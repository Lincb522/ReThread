import json,os,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_manager as base_tests
SID=base_tests.SID
from manager import digest
class HistoryMaintenanceTests(unittest.TestCase):
 def setUp(self):
  self.base=base_tests.ManagerTests();self.base.setUp();self.m=self.base.m;self.s=self.base.store;self.src=self.base.p
  self.dest=self.base.root/'new-disk';self.dest.mkdir();self.original=self.s.row(SID);self.hash=digest(self.src)
 def tearDown(self):self.base.tearDown()
 def plan(self,action='migrate',keep=True,target=None):return self.m.plan(action,SID,target_path=str(target or self.dest),keep_source=keep)
 def execute(self,p):return self.m.execute(p['token'],p['confirmation'])
 def copy(self):
  f=self.dest/self.src.name;f.write_bytes(self.src.read_bytes());return f
 def test_copy_migration_preserves_source_fields_and_bytes(self):
  p=self.plan();r=self.execute(p);self.assertTrue(r['ok']);self.assertEqual(self.s.row(SID),dict(self.original,rollout_path=p['destination']));self.assertEqual(digest(self.src),self.hash);self.assertEqual(digest(Path(p['destination'])),self.hash)
  m=json.loads((Path(r['backup'])/'manifest.json').read_text());self.assertEqual(m['status'],'verified');self.assertTrue(m['keep_source']);self.assertEqual(m['destination'],p['destination'])
 def test_move_removes_only_source_after_index_verified(self):
  sibling=self.src.parent/'keep.txt';sibling.write_text('keep');p=self.plan(keep=False);r=self.execute(p)
  self.assertFalse(self.src.exists());self.assertEqual(sibling.read_text(),'keep');self.assertEqual(digest(Path(p['destination'])),self.hash);self.assertEqual(json.loads((Path(r['backup'])/'manifest.json').read_text())['phase'],'source_removed')
 def test_link_missing_index_backups_target_without_moving(self):
  f=self.copy();self.base.break_path();p=self.plan('link',target=f);r=self.execute(p)
  self.assertEqual(self.s.row(SID)['rollout_path'],str(f));self.assertEqual(digest(f),self.hash);self.assertTrue(self.src.exists());self.assertEqual(len(json.loads((Path(r['backup'])/'manifest.json').read_text())['files']),1)
 def test_link_identical_existing_copy(self):
  f=self.copy();self.execute(self.plan('link',target=f));self.assertEqual(digest(self.src),self.hash);self.assertEqual(digest(f),self.hash)
 def test_link_different_content_blocked(self):
  f=self.copy();f.write_text(f.read_text()+'{"type":"event_msg","payload":{}}\n')
  with self.assertRaisesRegex(Exception,'内容不同'):self.plan('link',target=f)
  self.assertEqual(self.s.row(SID),self.original)
 def test_wrong_identity_rejected(self):
  f=self.copy();f.write_text(f.read_text().replace(SID,'00000000-0000-0000-0000-000000000999'))
  with self.assertRaisesRegex(Exception,'ID'):self.plan('link',target=f)
 def test_bad_json_rejected(self):
  self.src.write_text(self.src.read_text()+'{broken\n')
  with self.assertRaisesRegex(Exception,'格式'):self.plan()
 def test_bad_filename_rejected(self):
  f=self.dest/'history.jsonl';f.write_bytes(self.src.read_bytes())
  with self.assertRaisesRegex(Exception,'文件名'):self.plan('link',target=f)
 def test_same_file_noop_rejected(self):
  with self.assertRaisesRegex(Exception,'无需'):self.plan('link',target=self.src)
 def test_relative_path_rejected(self):
  with self.assertRaisesRegex(Exception,'绝对'):self.m.plan('migrate',SID,target_path='relative')
 def test_wrong_keep_type_rejected(self):
  with self.assertRaisesRegex(Exception,'格式'):self.m.plan('migrate',SID,target_path=str(self.dest),keep_source='false')
 def test_stale_candidate_stops_before_index_change(self):
  f=self.copy();self.base.break_path();old=self.s.row(SID);p=self.plan('link',target=f);f.write_text(f.read_text()+'{}\n')
  with self.assertRaisesRegex(Exception,'变化'):self.execute(p)
  self.assertEqual(self.s.row(SID),old)
 def test_target_not_overwritten(self):
  p=self.plan();dst=Path(p['destination']);dst.parent.mkdir();dst.write_text('keep')
  with self.assertRaisesRegex(Exception,'已存在'):self.execute(p)
  self.assertEqual(dst.read_text(),'keep');self.assertEqual(self.s.row(SID),self.original)
 def test_target_directory_replaced_rejected(self):
  p=self.plan();self.dest.rename(self.dest.with_name('previous'));self.dest.mkdir()
  with self.assertRaisesRegex(Exception,'目录已变化'):self.execute(p)
 def test_writer_blocks_before_backup(self):
  p=self.plan();self.m.runtime_probe=lambda:[1]
  with self.assertRaisesRegex(Exception,'退出'):self.execute(p)
  self.assertFalse((self.base.root/'data/backups').exists());self.assertEqual(self.s.row(SID),self.original)
 def test_changed_related_records_rejected(self):
  p=self.plan()
  with sqlite3.connect(self.s.db_path) as c:c.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',(SID,'00000000-0000-0000-0000-000000000102'))
  with self.assertRaisesRegex(Exception,'关联索引'):self.execute(p)
 def test_shared_fork_requires_preserve(self):
  with patch.object(self.m,'references',return_value=[('child',SID)]):
   with self.assertRaisesRegex(Exception,'分叉'):self.plan(keep=False)
   self.assertTrue(self.plan(keep=True)['keep_source'])
 def test_archived_destination_mismatch(self):
  folder=self.s.codex_home/'archived_sessions';folder.mkdir()
  with self.assertRaisesRegex(Exception,'归档'):self.plan(target=folder)
 def test_archive_flag_preserved(self):
  self.base.update(archived=1);p=self.plan();self.execute(p);self.assertEqual(self.s.row(SID)['archived'],1)
 def test_backup_destination_rejected(self):
  folder=self.m.root/'backups';folder.mkdir()
  with self.assertRaisesRegex(Exception,'维护目录'):self.plan(target=folder)
 def test_paginated_missing_database(self):
  self.base.update(history_mode='paginated')
  with self.assertRaisesRegex(Exception,'分页历史数据库缺失'):self.plan()
 def projection(self,offset):
  self.base.update(history_mode='paginated')
  with sqlite3.connect(self.s.sqlite_home/'thread_history_1.sqlite') as c:
   c.execute('CREATE TABLE thread_history_projection_state(thread_id TEXT,next_rollout_byte_offset INTEGER)');c.execute('INSERT INTO thread_history_projection_state VALUES (?,?)',(SID,offset))
 def test_projection_out_of_bounds_blocked(self):
  self.projection(999999)
  with self.assertRaisesRegex(Exception,'投影偏移'):self.plan()
 def test_projection_preserved_during_migration(self):
  self.projection(self.src.stat().st_size);p=self.plan();self.execute(p)
  with sqlite3.connect(self.s.sqlite_home/'thread_history_1.sqlite') as c:self.assertEqual(c.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state').fetchone()[0],self.src.stat().st_size)
 def test_low_disk_blocks_preflight(self):
  with patch('history_maintenance.shutil.disk_usage',return_value=type('Usage',(),{'free':0})()):
   with self.assertRaisesRegex(Exception,'空间'):self.plan()
 def test_copy_interruption_preserves_index_and_source_with_journal(self):
  p=self.plan()
  with patch('history_maintenance.shutil.copyfileobj',side_effect=OSError('copy interrupted')):
   with self.assertRaisesRegex(Exception,'copy interrupted'):self.execute(p)
  self.assertEqual(self.s.row(SID),self.original);self.assertEqual(digest(self.src),self.hash)
  m=json.loads(next((self.m.root/'backups').glob('*/manifest.json')).read_text());self.assertEqual(m['phase'],'copying');self.assertEqual(m['status'],'failed_or_partial')
 def test_source_removal_failure_not_reported_success(self):
  p=self.plan(keep=False);unlink=Path.unlink
  def fail(path,*a,**kw):
   if path==self.src:raise OSError('source cleanup denied')
   return unlink(path,*a,**kw)
  with patch.object(Path,'unlink',fail):
   with self.assertRaisesRegex(Exception,'source cleanup denied'):self.execute(p)
  self.assertEqual(self.s.row(SID)['rollout_path'],p['destination']);self.assertTrue(self.src.exists());self.assertEqual(digest(Path(p['destination'])),self.hash)
 def test_index_verification_happens_before_source_cleanup(self):
  with sqlite3.connect(self.s.db_path) as c:
   c.execute("CREATE TRIGGER changed_title AFTER UPDATE OF rollout_path ON threads BEGIN UPDATE threads SET title='changed' WHERE id=NEW.id; END")
  p=self.plan(keep=False)
  with self.assertRaisesRegex(Exception,'索引字段校验'):self.execute(p)
  self.assertTrue(self.src.is_file());self.assertEqual(digest(self.src),self.hash)
 def test_public_plan_excludes_internal_records(self):
  p=self.plan();self.assertNotIn('related_records',p);self.assertNotIn('history_evidence',p);self.assertNotIn('original_row',p)
 def test_file_symlink_requires_preservation(self):
  f=self.copy();self.src.unlink();self.src.symlink_to(f)
  with self.assertRaisesRegex(Exception,'软链接'):self.plan(keep=False)
 def test_filename_alias_to_invalid_canonical_name(self):
  f=self.dest/'raw.jsonl';f.write_bytes(self.src.read_bytes());alias=self.dest/self.src.name;alias.symlink_to(f)
  with self.assertRaisesRegex(Exception,'文件名'):self.plan('link',target=alias)
if __name__=='__main__':unittest.main()
