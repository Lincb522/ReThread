"""Whole-library batching: no fixed count limit, async progress, scoped persistence."""
import json, sqlite3, threading, time, unittest, urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import test_manager
from manager import BatchJobs, ManagerHandler
from server import OperationError

class Build8Tests(unittest.TestCase):
    def setUp(self):
        self.case=test_manager.ManagerTests();self.case.setUp()
        self.m=self.case.m;self.store=self.case.store
    def tearDown(self):self.case.tearDown()
    def add(self,count,files=True):
        ids=[];source=self.case.p.read_text()
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            for n in range(count):
                sid=f'00000000-0000-0000-0000-{1001+n:012d}';ids.append(sid)
                path=self.case.p.with_name(f'rollout-2026-10-05-{sid}.jsonl')
                if files:path.write_text(source.replace(test_manager.SID,sid))
                c.execute('INSERT INTO threads SELECT ?,?,title,cwd,created_at,updated_at,archived,has_user_event,source,history_mode,first_user_message FROM threads WHERE id=?',(sid,str(path),test_manager.SID))
        return ids
    def wait(self,jobs,job):
        deadline=time.monotonic()+30
        while job['state'] in {'queued','running'}:
            if time.monotonic()>deadline:self.fail('Background batch did not finish')
            time.sleep(.01);job=jobs.get(job['id'])
        return job
    def delete(self,sid):
        path=self.store.path(self.store.row(sid))
        with closing(sqlite3.connect(self.store.db_path)) as c,c:c.execute('DELETE FROM threads WHERE id=?',(sid,))
        path.unlink()
    def test_80_members_use_one_pending_plan(self):
        ids=self.add(80)
        with patch.object(self.store,'graph',wraps=self.store.graph) as graph:
            plan=self.m.batch_plan('delete',ids)
        self.assertEqual(graph.call_count,1)
        self.assertEqual(set(plan['ids']),set(ids));self.assertEqual(len(plan['targets']),80)
        self.assertEqual(len(self.m.plans),1)
        self.assertEqual(len(self.m.plans[plan['token']]['members']),80)
    def test_75_execute_with_expired_member_timestamps(self):
        ids=self.add(75);p=self.m.batch_plan('delete',ids)
        for member in self.m.plans[p['token']]['members']:member['expires']=0
        progress=[]
        with patch.object(self.case.controller,'delete',side_effect=self.delete):
            result=self.m.execute(p['token'],p['confirmation'],lambda *v:progress.append(v))
        self.assertTrue(result['ok']);self.assertEqual(result['affected'],75)
        self.assertEqual(progress[-1],('executing',75,75));self.assertEqual(self.store.list()[1],1)
        self.assertEqual(len(list((self.m.root/'backups').glob('*/manifest.json'))),75)
    def test_whole_library_more_than_10000(self):
        self.add(10005,files=False)
        rows,total=self.store.list(limit=0)
        self.assertEqual(total,10006);self.assertEqual(len(rows),total)
        self.assertEqual(len(self.store.list(limit=10003)[0]),10003)
    def test_cascade_over_1000_and_scoped_sql(self):
        ids=self.add(1005,files=False)
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            c.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',[(test_manager.SID,i) for i in ids])
            c.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',(ids[-1],test_manager.SID))
        self.assertEqual(len(self.store.descendants([test_manager.SID])),1006)
        records=self.m.scoped_records([test_manager.SID]+ids)
        self.assertEqual(len([r for r in records if r[1]=='threads']),1006)
        self.assertEqual(len([r for r in records if r[1]=='thread_spawn_edges']),1006)
    def test_async_large_http_request_and_result(self):
        ids=self.add(440);jobs=BatchJobs(self.m)
        class Handler(ManagerHandler):
            def log_message(self,*args):pass
        Handler.store=self.store;Handler.maintenance=self.m;Handler.batches=jobs;Handler.csrf_token='test-batch-token'
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=http.serve_forever);worker.start()
        base=f'http://127.0.0.1:{http.server_port}'
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body=json.dumps({'action':'delete','ids':ids}).encode();self.assertGreater(len(body),16384)
        try:
            req=urllib.request.Request(base+'/api/batch/jobs/plan',data=body,headers={'Content-Type':'application/json','X-Codex-CSRF':Handler.csrf_token})
            with client.open(req) as response:
                self.assertEqual(response.status,202);job=json.load(response)
            job=self.wait(jobs,job);self.assertEqual(job['state'],'completed',job['error'])
            self.assertEqual(set(job['plan']['ids']),set(ids));self.assertEqual(job['completed'],440)
            with client.open(base+'/api/batch/jobs/'+job['id']) as response:self.assertEqual(json.load(response)['state'],'completed')
        finally:jobs.shutdown();http.shutdown();worker.join();http.server_close()
    def test_async_execution_once_and_failure_report(self):
        ids=self.add(3);p=self.m.batch_plan('delete',ids);jobs=BatchJobs(self.m)
        try:
            with patch.object(self.case.controller,'delete',side_effect=RuntimeError('engine rejected')) as delete:
                job=self.wait(jobs,jobs.start('execute',{'token':p['token'],'confirmation':p['confirmation']}))
                self.assertEqual(delete.call_count,1)
            self.assertEqual(job['state'],'completed');self.assertFalse(job['result']['ok'])
            self.assertEqual([o['status'] for o in job['result']['outcomes']],['failed','not_executed','not_executed'])
            replay=self.wait(jobs,jobs.start('execute',{'token':p['token'],'confirmation':p['confirmation']}))
            self.assertEqual(replay['state'],'failed')
            self.assertFalse(jobs.get(job['id'])['result']['ok'])
        finally:jobs.shutdown()
    def test_async_busy_and_durable_interruption(self):
        jobs=BatchJobs(self.m);entered=threading.Event();release=threading.Event()
        def work(*args):entered.set();release.wait(10);raise RuntimeError('planned stop')
        try:
            with patch.object(self.m,'batch_plan',side_effect=work):
                job=jobs.start('plan',{'ids':[test_manager.SID],'action':'delete'})
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(Exception,'正在运行'):jobs.start('plan',{'ids':[test_manager.SID]})
                persisted=BatchJobs(self.m).get(job['id']);self.assertEqual(persisted['state'],'interrupted')
                release.set();self.assertEqual(self.wait(jobs,job)['state'],'failed')
        finally:release.set();jobs.shutdown()
    def test_ambiguous_opposite_storage_stops_before_delete(self):
        sid='00000000-0000-0000-0000-000000000001'
        other='00000000-0000-0000-0000-000000000012'
        source=self.case.p.with_name(self.case.p.name.replace(test_manager.SID,sid))
        source.write_text(self.case.p.read_text().replace(test_manager.SID,sid))
        archived=self.case.home/'archived_sessions'/('rollout-2026-10-05T00-00-00-'+other+'.jsonl')
        archived.parent.mkdir();archived.write_text(self.case.p.read_text().replace(test_manager.SID,other))
        with closing(sqlite3.connect(self.store.db_path)) as c,c:
            c.execute('UPDATE threads SET id=?,rollout_path=? WHERE id=?',(sid,str(source),test_manager.SID))
        self.case.p.unlink()
        before=archived.read_bytes()
        with patch.object(self.case.controller,'delete') as delete:
            with self.assertRaisesRegex(OperationError,'相近 ID'):self.m.plan('delete',sid)
            delete.assert_not_called()
        self.assertEqual(archived.read_bytes(),before);self.assertTrue(source.is_file())
        plan=self.m.batch_plan('delete',[sid]);self.assertEqual(plan['ids'],[])
        self.assertEqual(plan['blocked'][0]['id'],sid)

    def test_merge_keeps_independent_limit(self):
        with self.assertRaises(ValueError):self.m.selected_ids(self.add(11),2,10)
    def test_empty_duplicate_invalid_ids(self):
        for ids in [[],[test_manager.SID]*2,['bad'],[{}]]:
            with self.assertRaises((ValueError,TypeError,OperationError)):self.m.selected_ids(ids)

if __name__=='__main__':unittest.main()
