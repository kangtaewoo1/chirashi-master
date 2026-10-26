"""The acquisition goal is not a concurrency setting or a historical site count."""
import ast
from pathlib import Path
import unittest
from cafe24_gate import publishing_block, unattended_block


class SiteGoalTests(unittest.TestCase):
    def setUp(self):
        self.tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        names={'_apply_site_goal_50','_site_goal_progress','is_autopostable','is_permitted'}
        functions=[n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.saved=[]
        self.env={'publishing_block':publishing_block,'unattended_block':unattended_block,
                  'load_config':lambda:{'unattended_gate_enabled':True},
                  '_is_error_or_demo_site':lambda site:False,'save_config':lambda cfg:self.saved.append(dict(cfg))}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'site-goal-test','exec'),self.env)

    def valid(self):
        return {'permission':True,'registration_source':'manual_admin','platform':'gnuboard','write_test_status':'passed',
                'verified_post_url':'https://example.com/bbs/board.php?bo_table=free&wr_id=42',
                'site_url':'https://example.com','bo_table':'free','unattended_audit':{'state':'ready'}}

    def test_migration_sets_50_once_without_increasing_browser_or_changing_permissions(self):
        cfg={'site_goal':500,'publish_fanout':10,'publish_max_chromes':12,'unattended_gate_enabled':True}
        updated=self.env['_apply_site_goal_50'](cfg)
        self.assertEqual(updated,dict(cfg,site_goal=50,site_goal_50_applied=True))
        self.assertEqual(cfg['site_goal'],500)
        updated['site_goal']=80
        self.assertEqual(self.env['_apply_site_goal_50'](updated)['site_goal'],80)
        self.assertEqual(len(self.saved),1)

    def test_historical_registrations_do_not_falsely_complete_the_goal(self):
        blocked=dict(self.valid(),write_access_state='admin_only',platform='cafe24')
        progress=self.env['_site_goal_progress']({'site_goal':50},[self.valid() for _ in range(14)]+[blocked for _ in range(97)])
        self.assertEqual(progress['site_perm'],111)
        self.assertEqual(progress['site_done'],14)
        self.assertEqual(progress['site_remaining'],36)
        self.assertFalse(progress['site_goal_reached'])

    def test_only_fifty_qualified_sites_reach_the_goal(self):
        progress=self.env['_site_goal_progress']({'site_goal':50},[self.valid() for _ in range(49)])
        self.assertFalse(progress['site_goal_reached'])
        progress=self.env['_site_goal_progress']({'site_goal':50},[self.valid() for _ in range(50)])
        self.assertTrue(progress['site_goal_reached'])
        self.assertEqual(progress['site_remaining'],0)

    def test_existing_access_denial_and_maintenance_guards_remain(self):
        for change in ({'maintenance_hold':True},{'permission':False},{'status':'rejected'},
                       {'secret_forced':True},{'login_required':True,'mb_id':''},
                       {'write_access_state':'admin_only'},{'write_access_state':'human_verification_required'}):
            with self.subTest(change=change):
                progress=self.env['_site_goal_progress']({'site_goal':50},[dict(self.valid(),**change)])
                self.assertEqual(progress['site_done'],0)

    def test_goal_does_not_raise_the_chrome_concurrency_limit(self):
        fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='_publish_one_combo')
        value=next(n.value for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='fan' for t in n.targets))
        self.assertEqual(eval(compile(ast.Expression(value),'fanout-test','eval'),{'cfg':{'publish_fanout':50}}),12)

    def test_missing_or_invalid_goal_uses_fifty(self):
        for cfg in ({},{'site_goal':None},{'site_goal':'invalid'}):
            self.assertEqual(self.env['_site_goal_progress'](cfg,[])['site_goal'],50)


if __name__=='__main__': unittest.main()
