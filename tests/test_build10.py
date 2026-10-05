"""Deleting indexed records with missing children, external history and stale paths."""
import json,sqlite3,unittest,shutil
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import test_manager
from manager import OfficialController,digest
SID=test_manager.SID
class Build10Tests(unittest.TestCase):
    def setUp(self):
        self.c=test_manager.ManagerTests();self.c.setUp();self.m=self.c.m;self.s=self.c.store
    def tearDown(self):self.c.tearDown()
    def missing(self):self.c.p.unlink()
    def erase_records(self,ids):
        with closing(sqlite3.connect(self.s.db_path)) as c,c:
            c.executemany('DELETE FROM threads WHERE id=?',[(i,) for i in ids])
            c.executemany('DELETE FROM thread_spawn_edges WHERE parent_thread_id=? OR child_thread_id=?',[(i,i) for i in ids])
    def test_all_missing_can_delete_metadata_with_explicit_backup(self):
        self.missing();p=self.m.plan('delete',SID);self.assertEqual(p['missing_histories'],[SID]);self.assertEqual(p['file_bytes'],0)
        with patch.object(self.c.controller,'delete',side_effect=lambda sid:self.erase_records([sid])):r=self.m.execute(p['token'],p['confirmation'])
        self.assertTrue(r['ok']);d=json.loads((Path(r['backup'])/'manifest.json').read_text())
        self.assertEqual(d['missing_histories'],[SID]);self.assertEqual(d['files'],[]);self.assertEqual(len(d['rows']),1)
        self.assertTrue((Path(r['backup'])/'metadata.sqlite').is_file())
    def test_external_parent_and_missing_local_child_do_not_expand_root(self):
        child='00000000-0000-0000-0000-000000000202';original=self.c.p
        external=self.c.root/'disk/history';external.mkdir(parents=True);src=external/original.name;original.rename(src)
        link=self.c.home/'sessions/external';link.symlink_to(external,target_is_directory=True)
        self.c.update(rollout_path=str(link/src.name))
        with closing(sqlite3.connect(self.s.db_path)) as c,c:
            c.execute('INSERT INTO threads SELECT ?,?,title,cwd,created_at,updated_at,archived,has_user_event,source,history_mode,first_user_message FROM threads WHERE id=?',(child,str(original.with_name(original.name.replace(SID,child))),SID))
            c.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',(SID,child))
        p=self.m.batch_plan('delete',[SID]);self.assertEqual(set(p['ids']),{SID,child});self.assertEqual(p['blocked'],[]);self.assertEqual(p['missing_histories'],[child])
        member=self.m.plans[p['token']]['members'][0];self.assertEqual(member['delete_roots'],{'sessions':str(external)})
        before=digest(src)
        def rpc(controller,method,params):self.erase_records([SID,child]);src.unlink()
        with patch.object(OfficialController,'_rpc_unlocked',new=rpc):r=self.m.execute(p['token'],p['confirmation'])
        self.assertTrue(r['ok']);self.assertEqual(r['affected'],2);self.assertTrue(link.is_symlink())
        manifest=json.loads(next((self.m.root/'backups').glob('*/manifest.json')).read_text())
        self.assertEqual(len(manifest['files']),1);self.assertEqual(manifest['files'][0]['sha256'],before)
    def test_stale_index_finds_and_backs_up_same_id_before_official_delete(self):
        self.c.break_path();p=self.m.plan('delete',SID);self.assertEqual(p['missing_histories'],[])
        source=self.c.p;before=digest(source)
        def delete(sid):self.erase_records([sid]);source.unlink()
        with patch.object(self.c.controller,'delete',side_effect=delete):r=self.m.execute(p['token'],p['confirmation'])
        manifest=json.loads((Path(r['backup'])/'manifest.json').read_text());self.assertEqual(manifest['files'][0]['sha256'],before)
    def test_file_reappearing_after_confirmation_stops_before_deletion(self):
        data=self.c.p.read_bytes();self.missing();p=self.m.plan('delete',SID);self.c.p.write_bytes(data)
        with patch.object(self.c.controller,'delete') as delete:
            with self.assertRaisesRegex(Exception,'变化'):self.m.execute(p['token'],p['confirmation'])
            delete.assert_not_called()
    def test_new_alternate_file_after_confirmation_stops(self):
        data=self.c.p.read_bytes();self.missing();p=self.m.plan('delete',SID)
        alt=self.c.home/'archived_sessions'/self.c.p.name;alt.parent.mkdir();alt.write_bytes(data)
        with patch.object(self.c.controller,'delete') as delete:
            with self.assertRaisesRegex(Exception,'范围已变化'):self.m.execute(p['token'],p['confirmation'])
            delete.assert_not_called()
    def test_offline_directory_alias_is_not_missing_history(self):
        self.missing();link=self.c.home/'sessions/offline';link.symlink_to(self.c.root/'disk-disconnected',target_is_directory=True)
        self.c.update(rollout_path=str(link/self.c.p.name))
        with self.assertRaisesRegex(Exception,'离线'):self.m.plan('delete',SID)
    def test_mismatched_recovered_history_stops(self):
        data=self.c.p.read_text();self.c.break_path();self.c.p.write_text(data.replace(SID,'00000000-0000-0000-0000-000000000303'))
        with self.assertRaisesRegex(Exception,'ID 不匹配'):self.m.plan('delete',SID)
    def test_missing_exact_id_fuzzy_candidate_is_rejected(self):
        other='00000000-0000-0000-0000-0000000001012'
        # Valid other UUID embeds the zero-heavy selected query as a subsequence across path/name.
        self.missing();p=self.c.home/'sessions'/('rollout-2026-10-05T00-00-00-00000000-0000-0000-0000-000000001012.jsonl');p.write_text('{}\n')
        with self.assertRaisesRegex(Exception,'相近 ID'):self.m.plan('delete',SID)
