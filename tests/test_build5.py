import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
import test_manager
from manager import digest
SID=test_manager.SID
SECOND='00000000-0000-0000-0000-000000000202'
class Build5Tests(unittest.TestCase):
 def setUp(self):
  self.c=test_manager.ManagerTests();self.c.setUp();self.m=self.c.m;self.s=self.c.store
 def tearDown(self):self.c.tearDown()
 def add(self,sid=SECOND):
  p=self.c.p.with_name(self.c.p.name.replace(SID,sid));p.write_text(self.c.p.read_text().replace(SID,sid))
  with sqlite3.connect(self.s.db_path) as c:
   c.execute('INSERT INTO threads SELECT ?,?,title,cwd,created_at,updated_at,archived,has_user_event,source,history_mode,first_user_message FROM threads WHERE id=?',(sid,str(p),SID))
  return p
 def external(self):
  e=self.c.root/'offloaded';e.mkdir();link=self.c.home/'sessions/old-month';link.symlink_to(e,target_is_directory=True)
  dest=e/self.c.p.name;self.c.p.rename(dest);self.c.p=dest;self.c.update(rollout_path=str(link/dest.name));return link,dest
 def test_external_content_is_healthy(self):
  self.external();d=self.s.diagnose(SID);self.assertEqual(d['health'],'healthy');self.assertEqual(d['changes'],{});self.assertTrue(d['storage_restricted']);self.assertTrue(self.s.list()[0][0]['storage_restricted'])
 def test_zero_visibility_not_corrupt(self):
  self.c.update(has_user_event=0);d=self.s.diagnose(SID);self.assertEqual(d['health'],'healthy');self.assertFalse(d['changes'])
 def test_large_file_is_incomplete_not_repair(self):
  with patch('manager.MAX_SCAN',1):d=self.s.diagnose(SID)
  self.assertEqual(d['health'],'incomplete');self.assertFalse(d['changes'])
 def test_projection_warning_not_content_corrupt(self):
  self.c.update(history_mode='paginated');d=self.s.diagnose(SID);self.assertEqual(d['health'],'healthy');self.assertFalse(d['changes']);self.assertIn('missing_projection',[i['code'] for i in d['issues']])
 def test_true_corruption_has_no_unsafe_repair(self):
  self.c.p.write_text(self.c.p.read_text()+'{');d=self.s.diagnose(SID);self.assertEqual(d['health'],'corrupt');self.assertFalse(d['changes'])
 def test_relocate_preserves_source_and_symlink(self):
  link,src=self.external();before=digest(src);p=self.m.plan('relocate',SID);r=self.m.execute(p['token'],p['confirmation']);self.assertTrue(r['ok']);self.assertTrue(link.is_symlink());self.assertEqual(digest(src),before);self.assertEqual(digest(Path(p['destination'])),before);self.assertFalse(self.s.diagnose(SID)['storage_restricted']);self.assertEqual(self.m.plan('delete',SID)['ids'],[SID])
 def test_relocate_runtime_guard(self):
  self.external();p=self.m.plan('relocate',SID);self.m.runtime_probe=lambda:[1]
  with self.assertRaisesRegex(Exception,'退出'):self.m.execute(p['token'],p['confirmation'])
  self.assertFalse(Path(p['destination']).exists())
 def test_relocate_does_not_overwrite_destination(self):
  self.external();p=self.m.plan('relocate',SID);dst=Path(p['destination']);dst.parent.mkdir();dst.write_text('keep')
  with self.assertRaisesRegex(Exception,'已存在'):self.m.execute(p['token'],p['confirmation'])
  self.assertEqual(dst.read_text(),'keep');self.assertTrue(self.s.storage_boundary_issue(self.s.row(SID)))
 def test_batch_deduplicates_parent_child(self):
  self.add()
  with sqlite3.connect(self.s.db_path) as c:c.execute('INSERT INTO thread_spawn_edges VALUES(?,?)',(SID,SECOND))
  p=self.m.batch_plan('delete',[SECOND,SID]);self.assertEqual(len(p['targets']),2);self.assertEqual(len(self.m.plans[p['token']]['members']),1)
 def test_batch_lists_blocked_separately(self):
  self.add();self.external();p=self.m.batch_plan('archive',[SID,SECOND]);self.assertEqual(p['ids'],[SECOND]);self.assertEqual(p['blocked'][0]['id'],SID)
 def test_batch_stale_aborts_before_mutation(self):
  self.add();p=self.m.batch_plan('delete',[SID,SECOND]);self.c.update(title='change')
  with patch.object(self.c.controller,'delete') as delete:
   with self.assertRaisesRegex(Exception,'变化'):self.m.execute(p['token'],p['confirmation'])
   delete.assert_not_called()
 def test_batch_failure_stops_once_no_retry(self):
  self.add();p=self.m.batch_plan('delete',[SID,SECOND])
  with patch.object(self.c.controller,'delete',side_effect=RuntimeError('engine failed')) as delete:
   r=self.m.execute(p['token'],p['confirmation']);self.assertFalse(r['ok']);self.assertEqual(delete.call_count,1)
  self.assertEqual([i['status'] for i in r['outcomes']],['failed','not_executed'])
 def test_batch_single_confirm_deletes_both(self):
  self.add();p=self.m.batch_plan('delete',[SID,SECOND])
  def delete(sid):
   path=self.s.path(self.s.row(sid))
   with sqlite3.connect(self.s.db_path) as c:c.execute('DELETE FROM threads WHERE id=?',(sid,))
   path.unlink()
  with patch.object(self.c.controller,'delete',side_effect=delete):r=self.m.execute(p['token'],p['confirmation'])
  self.assertTrue(r['ok']);self.assertEqual(r['affected'],2);self.assertEqual(len(r['outcomes']),2)
  with self.assertRaises(Exception):self.m.execute(p['token'],p['confirmation'])
 def test_merge_messages_and_order(self):
  self.add();messages,excluded=self.m.merge_content([SECOND,SID]);self.assertEqual([m['source'] for m in messages],[SECOND,SID]);self.assertEqual(excluded,0)
 def test_merge_images_preserved_not_silently_dropped(self):
  p=self.add();e={'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_image','image_url':'private'}]}};p.write_text(p.read_text()+json.dumps(e)+'\n')
  messages,_=self.m.merge_content([SID,SECOND]);self.assertEqual(messages[-1]['content'],e['payload']['content']);self.assertEqual(messages[-1]['text'],'')
 def test_merge_full_history_not_preview_limit(self):
  p=self.add();line=json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Full text'}]}})+'\n';p.write_text(p.read_text()+line*90)
  messages,_=self.m.merge_content([SID,SECOND]);self.assertEqual(len(messages),92)
 def test_selection_rejects_duplicates(self):
  with self.assertRaises(ValueError):self.m.batch_plan('delete',[SID,SID])
if __name__=='__main__':unittest.main()
