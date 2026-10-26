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

    def test_remote_queue_rejects_holds_even_with_legacy_gate_disabled(self):
        self.env['load_config']=lambda:{'unattended_gate_enabled':False,'sbr_enabled':True}
        self.assertTrue(self.env['is_autopostable'](self.valid()))
        for change in ({'has_captcha':True},{'write_access_state':'admin_only'},
                       {'write_access_state':'human_verification_required'},
                       {'unattended_audit':{'state':'result_review'}},
                       {'unattended_audit':{'state':'tls_certificate_expired'}},
                       {'last_fail_reason':'무인 발행 보류 — 사람 확인 필요, 재제출 금지'},
                       {'last_fail_reason':'로그인이 필요한 게시판입니다 — 비회원 글쓰기 불가(권한 alert)'},
                       {'last_fail_reason':'결과 검증 대기 — 제목 불일치, 재제출 금지'},
                       {'manual_review_only':True}):
            with self.subTest(change=change):
                self.assertFalse(self.env['is_autopostable'](dict(self.valid(),**change)))

    def test_missing_or_invalid_goal_uses_fifty(self):
        for cfg in ({},{'site_goal':None},{'site_goal':'invalid'}):
            self.assertEqual(self.env['_site_goal_progress'](cfg,[])['site_goal'],50)

    def test_dashboard_uses_qualified_sites_not_historical_registrations(self):
        import json, subprocess
        source=Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        statement=next(line for line in source.splitlines() if line.startswith('if(r.site_goal){'))
        script="const r={site_goal:50,site_done:15,site_perm:112}; const el={style:{}}; const $=id=>el;"+statement+"; console.log(JSON.stringify(el));"
        result=subprocess.run(['node','-e',script],check=True,capture_output=True,encoding='utf-8')
        value=json.loads(result.stdout)
        self.assertIn('15/50곳 (30%)',value['textContent'])
        self.assertIn('추가 35곳 필요',value['textContent'])
        self.assertEqual(value['style']['color'],'var(--p)')


if __name__=='__main__': unittest.main()
