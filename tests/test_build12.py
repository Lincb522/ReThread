"""Desktop transport contract and separation of verified deletion from UI notification."""
import json,os,socket,sqlite3,struct,tempfile,threading,time,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import test_manager,test_build8
from desktop_sync import DesktopSync
SID=test_manager.SID

class IPCServer:
 def __init__(self,home,mode='ok',catalog=True):
  self.home=home;self.mode=mode;self.messages=[];self.errors=[]
  (home/'ipc').mkdir(mode=0o700)
  self.path=home/'ipc/ipc.sock';self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.bind(str(self.path));self.path.chmod(0o600);self.sock.listen(1);self.sock.settimeout(2)
  if catalog:
   (home/'sqlite').mkdir()
   with sqlite3.connect(home/'sqlite/codex-dev.db') as db:
    db.execute('CREATE TABLE local_thread_catalog(host_id TEXT,thread_id TEXT)')
    db.execute('INSERT INTO local_thread_catalog VALUES (?,?)',('local',SID))
  self.thread=threading.Thread(target=self.run);self.thread.start()
 def run(self):
  try:
   with self.sock.accept()[0] as c:
    c.settimeout(2)
    def exact(n):
     b=b''
     while len(b)<n:
      part=c.recv(n-len(b))
      if not part:raise EOFError()
      b+=part
     return b
    while True:
     m=json.loads(exact(struct.unpack('<I',exact(4))[0]));self.messages.append(m)
     if m['type']=='request':
      if self.mode=='stall':time.sleep(.35);return
      if self.mode=='oversized':c.sendall(struct.pack('<I',8*1024*1024));return
      reply={'type':'response','requestId':m['requestId'],'resultType':'error' if self.mode=='denied' else 'success','method':'initialize','result':{'clientId':'client'}}
      b=json.dumps(reply).encode();wire=struct.pack('<I',len(b))+b
      for i in range(0,len(wire),7):c.sendall(wire[i:i+7])
     if m.get('method')=='thread-archived' and self.mode=='ok' and (self.home/'sqlite/codex-dev.db').exists():
      with sqlite3.connect(self.home/'sqlite/codex-dev.db') as db:db.execute('DELETE FROM local_thread_catalog WHERE host_id=? AND thread_id=?',('local',m['params']['conversationId']))
  except (EOFError,BrokenPipeError,ConnectionResetError,socket.timeout):pass
  except Exception as e:self.errors.append(e)
  finally:self.sock.close()
 def close(self):self.thread.join(timeout=3);assert not self.thread.is_alive();assert not self.errors,self.errors

class DesktopTransportTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='rt12-');self.home=Path(self.tmp.name);self.sync=DesktopSync(self.home,self.home,timeout=.2)
 def tearDown(self):self.tmp.cleanup()
 def run_server(self,mode='ok',catalog=True):
  s=IPCServer(self.home,mode,catalog)
  try:r=self.sync.sync_deleted([{'id':SID,'cwd':'/workspace'}])
  finally:s.close()
  return r,s.messages
 def test_fragmented_frames_eviction_and_barrier(self):
  r,m=self.run_server();self.assertEqual(r['status'],'synced');self.assertTrue(r['catalog_verified']);self.assertEqual(r['removed_cached_count'],1)
  self.assertEqual([x['method'] for x in m],['initialize','thread-archived','query-cache-invalidate','query-cache-invalidate','initialize'])
  self.assertEqual(m[1]['version'],2);self.assertEqual(m[1]['params'],{'hostId':'local','conversationId':SID,'cwd':'/workspace'})
  self.assertFalse(r['mutation_rpc_sent']);self.assertEqual(self.sync.catalog_rows([SID]),set())
 def test_offline_is_pending_without_mutation(self):
  r=self.sync.sync_deleted([{'id':SID}]);self.assertEqual(r['status'],'pending');self.assertFalse(r['mutation_rpc_sent'])
 def test_denied_handshake_sends_no_broadcast(self):
  r,m=self.run_server('denied');self.assertEqual(r['status'],'pending');self.assertEqual(len(m),1)
 def test_unbounded_frame_rejected(self):
  r,m=self.run_server('oversized');self.assertEqual(r['status'],'pending');self.assertIn('长度异常',r['message']);self.assertEqual(len(m),1)
 def test_timeout_is_bounded_and_never_deletes(self):
  r,m=self.run_server('stall');self.assertEqual(r['status'],'pending');self.assertLess(r['elapsed_seconds'],.4);self.assertEqual(len(m),1)
 def test_router_ack_without_catalog_eviction_is_not_success(self):
  r,m=self.run_server('no_eviction');self.assertEqual(r['status'],'pending');self.assertFalse(r['catalog_verified']);self.assertEqual(self.sync.catalog_rows([SID]),{SID})
 def test_absent_catalog_is_only_sent_not_verified(self):
  r,m=self.run_server(catalog=False);self.assertEqual(r['status'],'sent');self.assertFalse(r['catalog_verified'])
 def test_catalog_disappearing_during_verification_not_success(self):
  with patch.object(self.sync,'catalog_rows',side_effect=[{SID},None]):r,m=self.run_server()
  self.assertEqual(r['status'],'pending');self.assertFalse(r['catalog_verified'])
 def test_wrong_permissions_never_connects(self):
  s=IPCServer(self.home);s.path.chmod(0o666)
  try:r=self.sync.sync_deleted([{'id':SID}])
  finally:s.close()
  self.assertEqual(r['status'],'pending');self.assertEqual(s.messages,[])
 def test_distinct_sqlite_library_no_notification(self):
  sync=DesktopSync(self.home,self.home/'different');self.assertEqual(sync.sync_deleted([{'id':SID}])['status'],'not_applicable')
 def test_no_records_no_notification(self):self.assertEqual(self.sync.sync_deleted([])['status'],'not_needed')
 def test_invalid_id_no_notification(self):self.assertEqual(self.sync.sync_deleted([{'id':'invalid'}])['status'],'pending')

class DeleteSyncTests(unittest.TestCase):
 def setUp(self):
  self.c=test_manager.ManagerTests();self.c.setUp();self.m=self.c.m
  self.h=test_build8.Build8Tests();self.h.case=self.c;self.h.m=self.m;self.h.store=self.c.store
 def tearDown(self):self.c.tearDown()
 def deleted(self):
  p=self.m.plan('delete',SID)
  with patch.object(self.c.controller,'delete',side_effect=self.h.delete):return self.m.execute(p['token'],p['confirmation'])
 def synced(self,rows):return {'status':'synced','affected':len(rows),'message':'同步已核验','catalog_verified':True,'mutation_rpc_sent':False}
 def test_verified_delete_offline_stays_success(self):
  r=self.deleted();self.assertTrue(r['ok']);self.assertEqual(r['desktop_sync']['status'],'pending')
  m=json.loads((Path(r['backup'])/'manifest.json').read_text());self.assertEqual(m['status'],'verified');self.assertEqual(m['database'],str(self.c.store.db_path))
  self.assertEqual(self.m.history()[0]['desktop_sync']['status'],'pending')
 def test_sync_exception_never_changes_verified_deletion(self):
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=RuntimeError('transport')):r=self.deleted()
  self.assertTrue(r['ok']);self.assertEqual(json.loads((Path(r['backup'])/'manifest.json').read_text())['status'],'verified')
 def test_failed_deletion_never_notifies(self):
  p=self.m.plan('delete',SID)
  with patch.object(self.c.controller,'delete',side_effect=RuntimeError('delete failed')),patch.object(self.m.desktop_sync,'sync_deleted') as sync:
   with self.assertRaisesRegex(Exception,'delete failed'):self.m.execute(p['token'],p['confirmation'])
   sync.assert_not_called()
 def test_retry_only_notification_never_rpc_and_keeps_manifest(self):
  r=self.deleted();path=Path(r['backup'])/'manifest.json';before=path.read_bytes()
  with patch.object(self.c.controller,'delete') as delete,patch.object(self.c.controller,'set_archived') as archive,patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:
   result=self.m.retry_desktop_sync();self.assertTrue(result['ok']);delete.assert_not_called();archive.assert_not_called();self.assertEqual(sync.call_args.args[0][0]['id'],SID)
  self.assertEqual(path.read_bytes(),before);self.assertEqual(self.m.history()[0]['desktop_sync']['status'],'synced')
 def test_reappeared_state_not_synced(self):
  row=self.c.store.row(SID);self.deleted()
  with sqlite3.connect(self.c.store.db_path) as db:db.execute('INSERT INTO threads VALUES('+','.join('?'*len(row))+')',list(row.values()))
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:r=self.m.retry_desktop_sync()
  self.assertEqual(sync.call_args.args[0],[]);self.assertEqual(r['outcomes'][0]['status'],'not_executed')
 def test_reappeared_file_not_synced(self):
  self.deleted();self.c.p.write_text('restored')
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:self.m.retry_desktop_sync()
  self.assertEqual(sync.call_args.args[0],[])
 def test_wrong_library_not_synced(self):
  r=self.deleted();p=Path(r['backup'])/'manifest.json';m=json.loads(p.read_text());m['database']='/other/state.sqlite';p.write_text(json.dumps(m))
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:self.m.retry_desktop_sync()
  self.assertEqual(sync.call_args.args[0],[])
 def test_legacy_record_in_other_home_not_synced(self):
  r=self.deleted();p=Path(r['backup'])/'manifest.json';m=json.loads(p.read_text());del m['database'];del m['codex_home'];p.write_text(json.dumps(m))
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:self.m.retry_desktop_sync()
  self.assertEqual(sync.call_args.args[0],[])
 def test_failed_manifest_not_synced(self):
  r=self.deleted();p=Path(r['backup'])/'manifest.json';m=json.loads(p.read_text());m['status']='failed_or_partial';p.write_text(json.dumps(m))
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:self.m.retry_desktop_sync()
  self.assertEqual(sync.call_args.args[0],[])
 def test_sidecar_write_error_does_not_change_delete_result(self):
  from manager import atomic_json
  def write(p,v):
   if p.name=='desktop-sync.json':raise OSError('disk full')
   return atomic_json(p,v)
  with patch('manager.atomic_json',side_effect=write):r=self.deleted()
  self.assertTrue(r['ok']);self.assertIn('record_error',r['desktop_sync']);self.assertEqual(self.m.history()[0]['status'],'verified')
 def test_batch_sync_between_each_delete_even_when_pending(self):
  ids=[SID]+self.h.add(2);p=self.m.batch_plan('delete',ids);order=[]
  def delete(sid):order.append(('delete',sid));self.h.delete(sid)
  def sync(rows):order.append(('sync',rows[0]['id']));return {'status':'pending','affected':len(rows),'message':'offline'}
  with patch.object(self.c.controller,'delete',side_effect=delete),patch.object(self.m.desktop_sync,'sync_deleted',side_effect=sync):r=self.m.execute(p['token'],p['confirmation'])
  self.assertEqual([x[0] for x in order],['delete','sync']*3);self.assertTrue(r['ok']);self.assertEqual(r['affected'],3);self.assertEqual(r['desktop_sync']['pending'],3)
  for i in range(0,6,2):self.assertEqual(order[i][1],order[i+1][1])
 def test_retry_no_fifty_record_limit(self):
  r=self.deleted();folder=Path(r['backup']);m=json.loads((folder/'manifest.json').read_text())
  for i in range(55):
   d=folder.parent/f'copy-{i}';d.mkdir();(d/'manifest.json').write_text(json.dumps(m))
  with patch.object(self.m.desktop_sync,'sync_deleted',side_effect=self.synced) as sync:r=self.m.retry_desktop_sync()
  self.assertEqual(len(r['outcomes']),56);self.assertEqual(len(sync.call_args.args[0]),1)
if __name__=='__main__':unittest.main(verbosity=2)
