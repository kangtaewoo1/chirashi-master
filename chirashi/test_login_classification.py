"""Transient posting/signup failures must not manufacture a login requirement."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest


class LoginClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))

    def report(self, message, login_required=False, signup_failed=False):
        from cafe24_gate import login_requirement_confirmed
        fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_pipeline_report_site')
        fn.decorator_list=[]
        site={'id':'registered','platform':'gnuboard','permission':True,'signup_fail_count':2,
              'login_required':login_required,'write_test_status':'failed'}
        changes=[]
        def update(sid,**kw):
            changes.append(kw);site.update(kw)
        env={'request':SimpleNamespace(get_json=lambda **kw:{'results':[{'site_id':'registered','ok':False,'msg':message,'signup_failed':signup_failed}]}),
             '_kst_now':datetime.now,'load_sites':lambda:[site],'set_site_flag':update,
             'finalize_post':lambda *a,**kw:None,'add_log':lambda *a:None,'_node_beat':lambda *a,**kw:None,
             'jsonify':lambda value:value,'datetime':datetime,'login_requirement_confirmed':login_requirement_confirmed}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'report-site-test','exec'),env)
        result=env['api_pipeline_report_site']()
        self.assertTrue(result['ok'])
        return site,changes

    def test_third_signup_timeout_does_not_create_login_requirement(self):
        for message in ('자동가입 타임아웃','글쓰기 페이지 못찾음','Turnstile 확인 필요'):
            with self.subTest(message=message):
                site,_=self.report(message)
                self.assertFalse(site['login_required'])
                self.assertTrue(site['signup_exhausted'])
                self.assertEqual(site['signup_fail_count'],3)
                self.assertEqual(site['write_test_status'],'failed')

    def test_signup_failed_boolean_does_not_prove_login_required(self):
        site,_=self.report('네트워크 연결 실패',signup_failed=True)
        self.assertFalse(site['login_required'])
        self.assertTrue(site['signup_exhausted'])

    def test_existing_login_requirement_is_preserved(self):
        site,_=self.report('자동가입 타임아웃',login_required=True)
        self.assertTrue(site['login_required'])

    def test_explicit_login_requirement_is_recorded(self):
        site,_=self.report('글쓰기 페이지 못찾음: 로그인 후 이용해 주세요')
        self.assertTrue(site['login_required'])

    def test_permission_and_captcha_failures_do_not_imply_login(self):
        from cafe24_gate import login_requirement_confirmed
        for message in ('게시 권한 없음','권한 alert','관리자만 작성할 수 있습니다',
                        '글쓰기 폼(wr_subject) 없음 — 로그인 필요/비표준 스킨',
                        'CAPTCHA 수동 입력 대기시간 만료','Turnstile 확인 필요','자동가입 타임아웃'):
            self.assertFalse(login_requirement_confirmed(message),message)
        for message in ('로그인이 필요합니다','로그인 후 이용해 주세요','회원만 글을 작성할 수 있습니다'):
            self.assertTrue(login_requirement_confirmed(message),message)

    def test_hybrid_path_uses_explicit_evidence_not_generic_permission(self):
        assignment=next(n for n in ast.walk(self.tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_real_login' for t in n.targets))
        from cafe24_gate import login_requirement_confirmed
        for message,expected in [('게시 권한 없음',False),('로그인이 필요합니다',True),('권한 alert',False)]:
            actual=eval(compile(ast.Expression(assignment.value),'hybrid-login-test','eval'),{'_m':message,'login_requirement_confirmed':login_requirement_confirmed})
            self.assertEqual(actual,expected)


if __name__=='__main__': unittest.main()
