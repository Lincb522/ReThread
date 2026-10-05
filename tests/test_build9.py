"""Per-stage batch progress, queue revision semantics and cooperative stop."""
import json,sqlite3,threading,time,unittest,sys
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
import test_manager
from manager import BatchJobs,OfficialController
SID=test_manager.SID
class Build9Tests(unittest.TestCase):
    def setUp(self):
        self.case=test_manager.ManagerTests();self.case.setUp();self.m=self.case.m;self.store=self.case.store
    def tearDown(self):self.case.tearDown()
    def remove(self,sid):
        p=self.store.path(self.store.row(sid))
        with closing(sqlite3.connect(self.store.db_path)) as c,c:c.execute('DELETE FROM threads WHERE id=?',(sid,))
        p.unlink()
    def wait(self,jobs,jid):
        jobs.worker.join(10);self.assertFalse(jobs.worker.is_alive());return jobs.get(jid)
    def queue(self,payload=False):
        with closing(sqlite3.connect(self.case.home/'queue_1.sqlite')) as c,c:
            c.execute('CREATE TABLE queued_thread_revisions (revision INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL UNIQUE)')
            c.execute('INSERT INTO queued_thread_revisions(thread_id) VALUES (?)',(SID,))
            if payload:
                c.execute('CREATE TABLE queued_items(thread_id TEXT,payload_json TEXT)')
                c.execute('INSERT INTO queued_items VALUES (?,?)',(SID,'message still here'))
    def test_revision_watermark_is_backed_up_and_retained_not_failure(self):
        self.queue();p=self.m.plan('delete',SID)
        with patch.object(self.case.controller,'delete',side_effect=self.remove):r=self.m.execute(p['token'],p['confirmation'])
        self.assertTrue(r['ok']);d=json.loads((Path(r['backup'])/'manifest.json').read_text())
        self.assertEqual(d['status'],'verified');self.assertEqual(len(d['retained_bookkeeping']),1)
        self.assertEqual(len(self.m.scoped_records([SID])),1)
        with closing(sqlite3.connect(Path(r['backup'])/'metadata.sqlite')) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM records WHERE table_name='queued_thread_revisions'").fetchone()[0],1)
    def test_real_queued_content_still_blocks_verification(self):
        self.queue(payload=True);p=self.m.plan('delete',SID)
        with patch.object(self.case.controller,'delete',side_effect=self.remove):
            with self.assertRaisesRegex(Exception,'queued_items'):self.m.execute(p['token'],p['confirmation'])
    def test_unknown_revision_schema_not_ignored(self):
        records=[('queue_1.sqlite','queued_thread_revisions',json.dumps({'thread_id':SID,'revision':1,'payload':'unknown'})),('state_5.sqlite','queued_thread_revisions',json.dumps({'thread_id':SID,'revision':1}))]
        self.assertEqual(self.m.delete_bookkeeping(records),[])
    def test_current_item_stages_visible_and_durable_before_completion(self):
        entered=threading.Event();release=threading.Event();jobs=BatchJobs(self.m);p=self.m.batch_plan('delete',[SID])
        def deleting(sid):
            self.m.report('official_rpc',message='waiting for official reply',wait_remaining_seconds=90)
            entered.set();release.wait(10);self.remove(sid)
        try:
            with patch.object(self.case.controller,'delete',side_effect=deleting):
                j=jobs.start('execute',{'token':p['token'],'confirmation':p['confirmation']})
                self.assertTrue(entered.wait(5));current=jobs.get(j['id'])
                self.assertEqual(current['completed'],0);self.assertEqual(current['current_index'],1)
                self.assertEqual(current['current_title'],'Original');self.assertEqual(current['phase'],'official_rpc')
                persisted=json.loads((jobs.root/(j['id']+'.json')).read_text())
                self.assertEqual(persisted['phase'],'official_rpc');self.assertGreater(persisted['updated_at'],0)
                self.assertEqual(BatchJobs(self.m).get(j['id'])['state'],'interrupted')
                release.set();done=self.wait(jobs,j['id']);self.assertTrue(done['result']['ok'])
        finally:release.set();jobs.shutdown()
    def test_stop_finishes_current_item_and_does_not_start_next(self):
        from test_build8 import Build8Tests
        helper=Build8Tests();helper.case=self.case;helper.store=self.store;helper.m=self.m
        ids=helper.add(2);p=self.m.batch_plan('delete',ids);jobs=BatchJobs(self.m)
        entered=threading.Event();release=threading.Event()
        def deleting(sid):entered.set();release.wait(10);self.remove(sid)
        try:
            with patch.object(self.case.controller,'delete',side_effect=deleting) as delete:
                j=jobs.start('execute',{'token':p['token'],'confirmation':p['confirmation']});self.assertTrue(entered.wait(5))
                self.assertTrue(jobs.stop(j['id'])['stop_requested']);release.set();done=self.wait(jobs,j['id'])
                self.assertEqual(delete.call_count,1)
                self.assertEqual([o['status'] for o in done['result']['outcomes']],['verified','not_executed'])
                self.assertFalse(done['result']['ok'])
        finally:release.set();jobs.shutdown()
    def test_backup_reports_real_byte_counts_and_checksums(self):
        updates=[];p=self.m.plan('delete',SID)
        with self.m.observing(lambda phase,**d:updates.append((phase,d))):folder,manifest=self.m.backup(p)
        values=[d for phase,d in updates if phase=='backup' and 'bytes_done' in d]
        self.assertEqual(values[0]['bytes_done'],0);self.assertEqual(values[-1]['bytes_done'],self.case.p.stat().st_size)
        self.assertEqual(values[-1]['bytes_total'],self.case.p.stat().st_size)
        self.assertIn('backup_verify',[phase for phase,d in updates]);self.assertIn('metadata_backup',[phase for phase,d in updates])
    def test_official_rpc_heartbeats_while_waiting_without_resubmit(self):
        engine=self.case.root/'engine';engine.write_text('#!'+sys.executable+'\n'+'''import sys,json,time
for line in sys.stdin:
 m=json.loads(line)
 if m.get('method')=='initialize':print(json.dumps({'id':m['id'],'result':{}}),flush=True)
 elif m.get('method')=='thread/delete':
  time.sleep(2.1);print(json.dumps({'id':m['id'],'result':{}}),flush=True)
''');engine.chmod(0o700)
        ctl=OfficialController(self.case.home,str(engine));updates=[]
        with ctl.observing(lambda phase,**d:updates.append((phase,d))):ctl.delete(SID)
        waiting=[d for phase,d in updates if phase=='official_rpc']
        self.assertGreaterEqual(len(waiting),3);self.assertGreater(waiting[0]['wait_remaining_seconds'],waiting[-1]['wait_remaining_seconds'])
        self.assertEqual(updates[-1][0],'engine_cleanup')
