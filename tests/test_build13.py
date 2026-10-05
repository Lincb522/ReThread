import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
import test_manager
from project_catalog import ProjectCatalog
SID=test_manager.SID
class ProjectIdentityTests(unittest.TestCase):
 def setUp(self):
  self.c=test_manager.ManagerTests();self.c.setUp();self.store=self.c.store;self.home=self.c.home
  self.state={'local-projects':{'named':{'name':'真正的项目名','rootPaths':['/work/random']}},'project-order':['named']}
 def tearDown(self):self.c.tearDown()
 def save(self):(self.home/'.codex-global-state.json').write_text(json.dumps(self.state,ensure_ascii=False))
 def resolve(self,**kwargs):
  self.save();row=self.store.row(SID)|kwargs;return self.store.project_catalog().resolve(row)
 def sql_projects(self):
  with sqlite3.connect(self.store.db_path) as db:
   db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,position INT)')
   db.execute('CREATE TABLE project_roots(project_id TEXT,path TEXT,position INT)')
   db.execute("INSERT INTO projects VALUES('new-id','新版项目',0)")
   db.execute("INSERT INTO project_roots VALUES('new-id','/work/new',0)")
 def test_named_project_uses_saved_name_not_folder(self):
  r=self.resolve(cwd='/work/random');self.assertEqual(r['project_name'],'真正的项目名');self.assertEqual(r['project_key'],'named')
 def test_descendant_and_component_boundary(self):
  self.assertEqual(self.resolve(cwd='/work/random/src')['project_key'],'named')
  self.assertFalse(self.resolve(cwd='/work/randomized')['project_is_saved'])
 def test_explicit_projectless_not_grouped_by_repo(self):
  self.state['projectless-thread-ids']=[SID];r=self.resolve(cwd='/work/random');self.assertEqual(r['project_name'],'独立对话');self.assertFalse(r['project_is_saved'])
 def test_generated_cwd_is_not_a_project(self):
  for cwd in ['/work/s','/work/cpa-cpa','/work/wo-x']:
   r=self.resolve(cwd=cwd);self.assertEqual(r['project_key'],'projectless');self.assertEqual(r['project_name'],'独立对话')
 def test_assignment_resolves_worktree_not_temporary_path(self):
  self.state['thread-project-assignments']={SID:{'projectKind':'local','projectId':'named'}}
  self.assertEqual(self.resolve(cwd='/work/.worktrees/a13f')['project_name'],'真正的项目名')
 def test_explicit_assignment_wins_over_stale_projectless(self):
  self.state['projectless-thread-ids']=[SID];self.state['thread-project-assignments']={SID:{'projectKind':'local','projectId':'named'}}
  self.assertTrue(self.resolve()['project_is_saved'])
 def test_nested_project_uses_longest_root(self):
  self.state['local-projects']['nested']={'name':'子项目','rootPaths':['/work/random/sub']}
  self.assertEqual(self.resolve(cwd='/work/random/sub/code')['project_name'],'子项目')
 def test_multiple_roots_share_one_identity(self):
  self.state['local-projects']['named']['rootPaths'].append('/second')
  self.assertEqual(self.resolve(cwd='/second/sub')['project_key'],self.resolve(cwd='/work/random')['project_key'])
 def test_same_names_stay_distinct(self):
  self.state['local-projects']['other']={'name':'真正的项目名','rootPaths':['/second']}
  self.assertNotEqual(self.resolve(cwd='/second')['project_key'],self.resolve(cwd='/work/random')['project_key'])
 def test_ambiguous_root_not_randomly_assigned(self):
  self.state['local-projects']['other']={'name':'其他','rootPaths':['/work/random']}
  self.assertEqual(self.resolve(cwd='/work/random')['project_source'],'ambiguous_root')
 def test_missing_explicit_project_not_reassigned_from_cwd(self):
  self.state['thread-project-assignments']={SID:{'projectKind':'local','projectId':'deleted'}}
  self.assertEqual(self.resolve(cwd='/work/random')['project_source'],'missing_project')
 def test_other_host_membership_not_applied_to_local(self):
  self.state['thread-project-membership-host-ids']={SID:'remote'}
  self.assertEqual(self.resolve(cwd='/work/random')['project_source'],'other_host')
 def test_migration_id_alias_deduplicates(self):
  self.sql_projects();self.state['app-server-project-id-by-legacy-project-id-by-host']={'local:'+str(self.home):{'named':'new-id'}}
  self.state['thread-project-assignments']={SID:{'projectKind':'local','projectId':'named'}}
  r=self.resolve();self.assertEqual(r['project_key'],'new-id');self.assertEqual(r['project_name'],'真正的项目名');self.assertEqual(len(self.store.project_catalog().public),1)
 def test_completed_migration_reads_sql_membership_and_name(self):
  self.sql_projects();self.state['app-server-project-id-by-legacy-project-id-by-host']={'local:'+str(self.home):{'named':'new-id'}}
  self.state['app-server-projects-migration-by-host']={'local:'+str(self.home):{'threadAssignmentsMigrated':True}}
  self.assertEqual(self.resolve(project_id='new-id')['project_name'],'新版项目')
  self.assertFalse(self.resolve(project_id=None,cwd='/work/new')['project_is_saved'])
 def test_sql_only_library_supported(self):
  self.sql_projects();self.state={};self.assertEqual(self.resolve(project_id='new-id')['project_name'],'新版项目')
 def test_rename_and_order_refresh_without_restart(self):
  self.resolve();self.state['local-projects']['named']['name']='新名字';self.assertEqual(self.resolve(cwd='/work/random')['project_name'],'新名字')
  self.state['local-projects']['first']={'name':'首位','rootPaths':['/first']};self.state['project-order']=['first','named'];self.save()
  self.assertEqual([p['name'] for p in self.store.project_catalog().public],['首位','新名字'])
 def test_invalid_json_is_visible_error_not_directory_names(self):
  (self.home/'.codex-global-state.json').write_text('{bad')
  with self.assertRaisesRegex(ValueError,'读取失败'):self.store.list()
 def test_invalid_metadata_is_visible_error(self):
  self.state['local-projects']={ 'bad':{'name':'','rootPaths':['relative']} };self.save()
  with self.assertRaises(ValueError):self.store.list()
 def test_single_snapshot_for_whole_list_and_no_original_write(self):
  self.save();before=(self.home/'.codex-global-state.json').read_bytes();dbbefore=self.store.db_path.read_bytes()
  with patch.object(self.store,'project_catalog',wraps=self.store.project_catalog) as catalog:rows,total=self.store.list(limit=0)
  self.assertEqual(catalog.call_count,1);self.assertEqual(before,(self.home/'.codex-global-state.json').read_bytes());self.assertEqual(dbbefore,self.store.db_path.read_bytes())
  self.assertEqual(rows[0]['project_name'],'独立对话')
 def test_unknown_old_library_stays_ungrouped(self):
  self.state={};self.assertFalse(self.resolve()['project_is_saved'])
if __name__=='__main__':unittest.main(verbosity=2)
