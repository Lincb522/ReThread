import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
import test_build5
from manager import OfficialController,OfficialRPCError,digest
SID=test_build5.SID
class ExternalDeletionTests(unittest.TestCase):
 def setUp(self):
  self.b=test_build5.Build5Tests();self.b.setUp();self.m=self.b.m;self.s=self.b.s;self.c=self.b.c
 def tearDown(self):self.b.tearDown()
 def test_preflight_and_batch_allow_external_delete(self):
  self.b.external();p=self.m.plan('delete',SID);self.assertTrue(p['external_delete']);self.assertEqual(p['ids'],[SID]);self.assertEqual(p['delete_roots']['sessions'],str(self.c.p.parent));self.b.add();p=self.m.batch_plan('delete',[SID,test_build5.SECOND]);self.assertTrue(p['external_delete']);self.assertFalse(p['blocked'])
 def test_official_delete_without_relocation_or_runtime_shutdown(self):
  link,src=self.b.external();before=digest(src);self.m.runtime_probe=lambda:[{'pid':42,'name':'Codex'}];p=self.m.plan('delete',SID);original_path=self.s.row(SID)['rollout_path'];parent_contents=set(src.parent.iterdir())
  def rpc(controller,method,params):
   self.assertEqual(method,'thread/delete');self.assertEqual(params,{'threadId':SID});self.assertEqual(self.s.row(SID)['rollout_path'],original_path)
   self.assertEqual(controller.sqlite_home,self.s.sqlite_home);self.assertEqual(controller.command,self.c.controller.command)
   self.assertEqual((controller.codex_home/'sessions').resolve(),src.parent);self.assertEqual(digest(src),before)
   self.assertEqual(len(list((self.m.root/'backups').glob('*/rollout-0.jsonl'))),1)
   with sqlite3.connect(self.s.db_path) as c:c.execute('DELETE FROM threads WHERE id=?',(SID,))
   src.unlink()
  with patch.object(OfficialController,'_rpc_unlocked',new=rpc):r=self.m.execute(p['token'],p['confirmation'])
  self.assertTrue(r['ok']);self.assertTrue(link.is_symlink());self.assertTrue(src.parent.exists());self.assertEqual(set(src.parent.iterdir()),parent_contents-{src})
  backup=Path(r['backup']);self.assertEqual(digest(backup/'rollout-0.jsonl'),before);self.assertFalse((backup/'official-delete-home/sessions').exists());self.assertEqual(json.loads((backup/'manifest.json').read_text())['status'],'verified')
 def test_rpc_error_no_retry_or_live_metadata_rewrite(self):
  link,src=self.b.external();before=digest(src);p=self.m.plan('delete',SID)
  with patch.object(OfficialController,'_rpc_unlocked',side_effect=OfficialRPCError('thread/delete',-32000,'engine failure')) as rpc:
   with self.assertRaisesRegex(Exception,'engine failure'):self.m.execute(p['token'],p['confirmation'])
   self.assertEqual(rpc.call_count,1)
  m=json.loads(next((self.m.root/'backups').glob('*/manifest.json')).read_text());self.assertEqual(m['status'],'failed_unchanged');self.assertTrue(m['temporary_aliases_removed']);self.assertEqual(digest(src),before);self.assertTrue(link.is_symlink())
 def test_symlink_retarget_after_confirmation_stops_before_rpc(self):
  link,src=self.b.external();p=self.m.plan('delete',SID);new=self.c.root/'new';new.mkdir();(new/src.name).write_bytes(src.read_bytes());link.unlink();link.symlink_to(new,target_is_directory=True)
  with patch.object(OfficialController,'_rpc_unlocked') as rpc:
   with self.assertRaisesRegex(Exception,'变化'):self.m.execute(p['token'],p['confirmation'])
   rpc.assert_not_called()
  self.assertTrue(src.exists());self.assertTrue((new/src.name).exists())
 def test_no_silent_success_when_official_keeps_file(self):
  self.b.external();p=self.m.plan('delete',SID)
  def rpc(*args):
   with sqlite3.connect(self.s.db_path) as c:c.execute('DELETE FROM threads WHERE id=?',(SID,))
  with patch.object(OfficialController,'_rpc_unlocked',new=rpc):
   with self.assertRaisesRegex(Exception,'原历史文件仍存在'):self.m.execute(p['token'],p['confirmation'])
  self.assertEqual(json.loads(next((self.m.root/'backups').glob('*/manifest.json')).read_text())['status'],'failed_or_partial')
 def test_mismatched_identity_stops_before_delete(self):
  self.b.external();self.c.p.write_text(self.c.p.read_text().replace(SID,test_build5.SECOND));p=self.m.plan('delete',SID)
  with patch.object(OfficialController,'_rpc_unlocked') as rpc:
   with self.assertRaisesRegex(Exception,'ID 不匹配'):self.m.execute(p['token'],p['confirmation'])
   rpc.assert_not_called()
if __name__=='__main__':unittest.main()
