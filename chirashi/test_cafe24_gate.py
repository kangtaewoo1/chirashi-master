import unittest
import ast
from pathlib import Path
from cafe24_gate import access_reason, guarded_publish, stop_on_alerts, result_is_new, publishing_block

class GateTests(unittest.TestCase):
    def test_queue_requires_credentials_and_verified_board(self):
        site={'platform':'cafe24','site_url':'https://example.com/board/product/list.html?board_no=6',
              'bo_table':'6','login_required':True,'mb_id':'member',
              'write_test_status':'passed','verified_post_url':'https://example.com/article/test/6/42/'}
        self.assertEqual(publishing_block(site),'credentials_required')
        site['mb_pass_enc']='encrypted-credential'
        self.assertEqual(publishing_block(site),'')
        site['verified_post_url']='https://example.com/article/test/5/42/'
        self.assertEqual(publishing_block(site),'invalid_post_evidence')
        site['write_access_state']='human_verification_required'
        self.assertEqual(publishing_block(site),'human_verification_required')

    def test_router_does_not_fallback_after_access_denial(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        router=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='do_post')
        scope={'_strip_non_bmp':lambda x:x,'resolve_platform':lambda s:'cafe24',
               'cafe24_post':lambda *a,**k:(False,'[admin_only] denied')}
        exec(compile(ast.Module(body=[router],type_ignores=[]),'<router-test>','exec'),scope)
        result=scope['do_post']({'_manual_review_job':True,'learned':{'write_url':'ignored','subject_sel':'ignored','content_sel':'ignored'}},'test','test')
        self.assertEqual(result,(False,'[admin_only] denied'))

    def test_admin_denial_stops_publish(self):
        reached=[]
        @guarded_publish
        def publish():
            stop_on_alerts(['관리자에게만 글쓰기 권한이 있습니다.'])
            reached.append('submit')
            return True, 'posted'
        ok, message=publish()
        self.assertFalse(ok)
        self.assertIn('[admin_only]',message)
        self.assertEqual(reached,[])

    def test_confirmed_success_without_access_denial_still_publishes(self):
        # A successful response must remain usable when there is no denial alert.
        reached=[]
        @guarded_publish
        def publish():
            stop_on_alerts(['정상적으로 등록되었습니다.'])
            reached.append('submit')
            return True, 'posted'
        ok, message = publish()
        self.assertTrue(ok)  # 발행 성공
        self.assertEqual(reached,['submit'])  # 함수가 실행됨

    def test_comment_denial_does_not_deny_new_posts(self):
        @guarded_publish
        def publish():
            stop_on_alerts(['관리자에게만 댓글 작성 권한이 있습니다.'])
            return True, 'allowed'
        self.assertTrue(publish()[0])

    def test_guard_context_is_restored(self):
        @guarded_publish
        def publish():
            stop_on_alerts(['관리자만 글쓰기'])
        publish()
        stop_on_alerts(['관리자만 글쓰기'])

    def test_result_rejects_list_login_and_other_host(self):
        base='https://www.brandl.co.kr/'
        for url in ('https://www.brandl.co.kr/board/product/list.html?board_no=6',
                    'https://www.brandl.co.kr/member/login.html',
                    'https://other.test/article/qa/6/99/', '등록됨', ''):
            with self.subTest(url=url):
                self.assertFalse(result_is_new(base,url,6))

    def test_same_or_older_article_rejected(self):
        prev='https://www.brandl.co.kr/article/qa/6/8837/'
        for n in (8836,8837):
            self.assertFalse(result_is_new(prev,f'https://brandl.co.kr/article/qa/6/{n}/'))
        self.assertTrue(result_is_new(prev,'https://brandl.co.kr/article/qa/6/8838/'))

    def test_wrong_board_rejected(self):
        self.assertFalse(result_is_new('https://brandl.co.kr/','https://brandl.co.kr/article/qa/4/9999/',6))

    def test_read_url_supported(self):
        self.assertTrue(result_is_new('https://brandl.co.kr/','https://brandl.co.kr/board/product/read.html?board_no=6&no=9999',6))

    def test_admin_only_board_with_credentials_and_old_evidence_stays_blocked(self):
        # Old proof and saved credentials cannot override a current access denial.
        site = {
            'platform': 'cafe24',
            'login_required': True,
            'mb_id': 'real_user',  # 실제 사용자 ID
            'mb_pass_enc': 'encrypted_password',  # 실제 암호
            'write_access_state': 'admin_only',  # 관리자 전용 게시판
            'write_test_status': 'passed',
            'site_url': 'https://example.com/board/product/list.html?board_no=6',
            'verified_post_url': 'https://example.com/article/test/6/42/'
        }
        self.assertEqual(publishing_block(site), 'admin_only')
        # After a real revalidation clears the denial, valid evidence can allow it.
        site['write_access_state'] = 'normal'
        self.assertEqual(publishing_block(site), '')

    def test_access_reason_admin_only(self):
        # 기존 테스트 케이스
        self.assertEqual(access_reason('관리자에게만 글쓰기 권한이 있습니다.'), 'admin_only')
        self.assertEqual(access_reason('관리자만 글쓰기'), 'admin_only')
        self.assertEqual(access_reason('관리자에게만 작성 권한이 있습니다.'), 'admin_only')

        # 새로운 테스트 케이스: 관리자 전용 게시판 판단을 더 정확하게
        self.assertEqual(access_reason('관리자에게만 글쓰기 권한이 있습니다.\\n\\n'), 'admin_only')
        self.assertEqual(access_reason('관리자에게만 글쓰기 권한이 있습니다.\\r\\n\\r\\n'), 'admin_only')
        self.assertEqual(access_reason('[admin_only]'), 'admin_only')
        self.assertEqual(access_reason('관리자 전용 게시판입니다.'), 'admin_only')
        self.assertEqual(access_reason('관리자 전용 게시판입니다.\\n\\n'), 'admin_only')
        self.assertEqual(access_reason('관리자 전용 게시판입니다.\\r\\n\\r\\n'), 'admin_only')
        self.assertEqual(access_reason('관리자 전용\n게시판입니다.'), 'admin_only')
        self.assertEqual(access_reason('관리자에게만\t글쓰기\n권한이 있습니다.'), 'admin_only')

    def test_publishing_block_admin_only_with_credentials(self):
        # Merely storing credentials must not skip the remaining verification.
        site = {
            'platform': 'cafe24',
            'write_access_state': 'admin_only',
            'mb_id': 'test_user',
            'mb_pass': 'test_password'
        }
        self.assertEqual(publishing_block(site), 'admin_only')
        site['write_access_state'] = 'normal'
        self.assertEqual(publishing_block(site), 'verification_required')

    def test_publishing_block_admin_only_without_credentials(self):
        # 실제 권한이 없는 경우 관리자 전용 게시판은 발행 불가능
        site = {
            'platform': 'cafe24',
            'write_access_state': 'admin_only'
        }
        self.assertEqual(publishing_block(site), 'admin_only')

    def test_publishing_block_normal_site(self):
        # 일반 사이트는 문제 없음
        site = {
            'platform': 'cafe24',
            'write_access_state': 'normal',
            'write_test_status': 'passed',
            'site_url': 'https://example.com/board/product/list.html?board_no=6',
            'bo_table': '6',
            'verified_post_url': 'https://example.com/article/test/6/42/',
        }
        self.assertEqual(publishing_block(site), '')

    def test_publishing_block_maintenance_hold(self):
        # 점검 중인 경우 점검 중으로 판단
        site = {
            'platform': 'cafe24',
            'maintenance_hold': True
        }
        self.assertEqual(publishing_block(site), 'maintenance_hold')

    def test_publishing_block_login_required(self):
        # 로그인 필요하고 인증 정보가 없는 경우
        site = {
            'platform': 'cafe24',
            'login_required': True,
            'mb_id': '',
            'mb_pass': None
        }
        self.assertEqual(publishing_block(site), 'credentials_required')

    def test_publishing_block_verification_required(self):
        # 글쓰기 검증 실패
        site = {
            'platform': 'cafe24',
            'write_test_status': 'failed'
        }
        self.assertEqual(publishing_block(site), 'verification_required')

    def test_publishing_block_invalid_post_evidence(self):
        # 게시물 증거가 유효하지 않은 경우
        site = {
            'platform': 'cafe24',
            'write_test_status': 'passed',
            'site_url': 'http://example.com',
            'verified_post_url': 'http://invalid.com',
            'bo_table': '1'
        }
        self.assertEqual(publishing_block(site), 'invalid_post_evidence')

    def test_empty_and_non_string_alerts_are_not_denials(self):
        for value in (None, '', 0, 42, False):
            with self.subTest(value=value):
                self.assertEqual(access_reason(value), '')

    def test_whitespace_credentials_are_not_usable(self):
        self.assertEqual(publishing_block({
            'platform': 'cafe24', 'login_required': True,
            'mb_id': '  ', 'mb_pass': 'dummy',
        }), 'credentials_required')

    def test_hold_applies_to_all_platforms(self):
        for platform in ('gnuboard', 'cafe24'):
            with self.subTest(platform=platform):
                self.assertEqual(publishing_block({
                    'platform': platform, 'maintenance_hold': True,
                }), 'maintenance_hold')

    def test_nested_guard_restores_outer_guard(self):
        @guarded_publish
        def inner():
            return True, 'allowed'
        @guarded_publish
        def outer():
            inner()
            stop_on_alerts(['관리자만 글쓰기'])
            self.fail('Outer guard must still be active')
        self.assertFalse(outer()[0])
        stop_on_alerts(['관리자만 글쓰기'])


class QueueEngineGateTests(unittest.TestCase):
    """The actual PC queue loads its own gate; test that path as well."""
    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = Path(__file__).with_name('pc_queue_engine') / 'cafe24_gate.py'
        spec = importlib.util.spec_from_file_location('queue_gate_under_test', path)
        cls.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gate)

    def test_normalized_alerts_match_primary_gate(self):
        for text in (None, '', 42, '관리자 전용\n게시판입니다.',
                     '관리자에게만\t글쓰기 권한이 있습니다.', '[admin_only]',
                     '관리자에게만 댓글 작성 권한이 있습니다.'):
            with self.subTest(text=text):
                self.assertEqual(self.gate.access_reason(text), access_reason(text))

    def test_credentials_do_not_override_denial(self):
        site = {'platform': 'cafe24', 'write_access_state': 'admin_only',
                'mb_id': 'member', 'mb_pass': 'dummy'}
        self.assertEqual(self.gate.publishing_block(site), 'admin_only')

    def test_verified_normal_board_is_allowed(self):
        site = {'platform': 'cafe24', 'write_access_state': 'normal',
                'write_test_status': 'passed', 'bo_table': '6',
                'site_url': 'https://example.com/board/product/list.html?board_no=6',
                'verified_post_url': 'https://example.com/article/test/6/42/'}
        self.assertEqual(self.gate.publishing_block(site), '')
        site['verified_post_url'] = 'https://other.example/article/test/6/42/'
        self.assertEqual(self.gate.publishing_block(site), 'invalid_post_evidence')

    def test_denial_stops_queue_publish(self):
        @self.gate.guarded_publish
        def publish():
            self.gate.stop_on_alerts(['관리자 전용\n게시판입니다.'])
            self.fail('Must stop before submission')
        ok, message = publish()
        self.assertFalse(ok)
        self.assertIn('[admin_only]', message)

    def test_unattended_policy_is_not_bypassed(self):
        self.assertEqual(self.gate.unattended_block({
            'platform': 'cafe24', 'write_access_state': 'admin_only',
        }), 'human_verification_required')

if __name__=='__main__':
    unittest.main()


class MaintenanceHoldRegression(__import__('unittest').TestCase):
    def test_hold_applies_to_all_platforms(self):
        from cafe24_gate import publishing_block
        for platform in ('gnuboard','cafe24'):
            self.assertEqual(publishing_block({'platform':platform,'maintenance_hold':True}), 'maintenance_hold')


class UnattendedTests(unittest.TestCase):
    def test_unknown_and_human_boards_never_admitted(self):
        from cafe24_gate import unattended_block
        site={'platform':'gnuboard'}
        self.assertEqual(unattended_block(site),'unattended_audit_required')
        site['unattended_audit']={'state':'ready'}
        self.assertEqual(unattended_block(site),'')
        for key,value in [('has_captcha',True),('manual_review_only',True),('login_required',True)]:
            self.assertTrue(unattended_block(dict(site,**{key:value})))
        self.assertEqual(unattended_block(dict(site,platform='cafe24')),'unattended_adapter_unverified')

    def test_unused_legacy_script_is_not_an_active_challenge(self):
        from form_schema import human_challenge_present
        self.assertFalse(human_challenge_present('<script src="jquery.kcaptcha.js"></script><form><input name="wr_subject"><textarea name="wr_content"></textarea></form>'))
        for markup in ('<input name="captcha_key">','<input name="wr_key">','<div class="cf-turnstile"></div>','<img src="kcaptcha_image.php">','<input type="hidden" name="g-recaptcha-response">'):
            self.assertTrue(human_challenge_present(markup),markup)

    def test_new_captcha_stops_before_solver_and_submission(self):
        from types import SimpleNamespace
        import sys
        from unittest.mock import patch
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post_http')
        class Session:
            headers={}
            def get(self,*a,**k):
                assert k['verify'] is True
                return SimpleNamespace(status_code=200,apparent_encoding='utf-8',encoding='utf-8',url='https://example.com/bbs/write.php',text='<form action="write_update.php"><input name="wr_subject"><textarea name="wr_content"></textarea><input name="captcha_key"></form>')
            def post(self,*a,**k): raise AssertionError('must not submit')
        env={'load_config':lambda:{},'re':__import__('re'),'_HTTP_UA':'test','set_site_flag':lambda *a,**k:None,'datetime':__import__('datetime').datetime}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'http-test','exec'),env)
        with patch.dict(sys.modules,requests=SimpleNamespace(Session=Session)):
            ok,message=env['gnuboard_post_http']({'id':'test','site_url':'https://example.com','bo_table':'free','_unattended_job':True},'title','body')
        self.assertFalse(ok)


class LegacyGuestFormTest(unittest.TestCase):
    def test_unquoted_fields_and_hidden_token_submit_once(self):
        from types import SimpleNamespace
        import sys, re, urllib.parse
        from unittest.mock import patch
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post_http')
        calls=[]
        class Session:
            headers={}
            def get(self,*a,**k):
                return SimpleNamespace(status_code=200,apparent_encoding='cp949',encoding='cp949',url='https://example.com/bbs/write.php?bo_table=free',text='<form action=write_update.php><input name=uid value=trusted-token><input name=wr_subject><textarea name=wr_content></textarea></form><script src="jquery.kcaptcha.js"></script>')
            def post(self,*a,**k):
                calls.append(k)
                return SimpleNamespace(url='https://example.com/bbs/board.php?bo_table=free&wr_id=123',text='posted')
        env={'load_config':lambda:{},'re':re,'urllib':__import__('urllib'),'_HTTP_UA':'test','_pick_writer':lambda s:'test','_post_password':lambda c:'test','_brand_email':lambda *a:'','_strip_non_bmp':lambda s:s,'set_site_flag':lambda *a,**k:None,'datetime':__import__('datetime').datetime}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'http-test','exec'),env)
        with patch.dict(sys.modules,requests=SimpleNamespace(Session=Session)):
            ok,result=env['gnuboard_post_http']({'site_url':'https://example.com','bo_table':'free','_unattended_job':True},'기능확인','body')
        self.assertTrue(ok)
        self.assertEqual(len(calls),1)
        self.assertTrue(calls[0]['verify'])
        self.assertIn(b'uid=trusted-token',calls[0]['data'])
        self.assertIn(urllib.parse.quote('기능확인',encoding='cp949').encode('ascii'),calls[0]['data'])


class ServerQueueOnlyTest(unittest.TestCase):
    def test_legacy_node_claim_cannot_bypass_unattended_gate_or_hold_site_lock(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_pipeline_claim_sites')
        fn.decorator_list=[]
        import threading, time
        from types import SimpleNamespace
        for blocked in ({'write_access_state':'admin_only'}, {'maintenance_hold':True},
                        {'secret_forced':True}, {'signup_phone_cert':True}):
            site=dict(id='blocked', platform='cafe24', mb_id='saved-user', **blocked)
            env={'jsonify':lambda value:value,
                 'request':SimpleNamespace(get_json=lambda **kw:{}), 'time':time,
                 'load_config':lambda:{}, 'POST_LOCK':threading.RLock(),
                 'load_sites':lambda:[site], 'is_permitted':lambda s:True,
                 'load_json':lambda *a:[], 'WORKROOMS_FILE':Path('/unused'),
                 'save_sites':lambda *a:self.fail('Blocked site must not be claimed'),
                 '_decrypt_password':lambda *a:self.fail('Blocked credentials must not be read')}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'claim-test','exec'),env)
            with self.subTest(blocked=blocked):
                self.assertEqual(env['api_pipeline_claim_sites']()['sites'],[])
                self.assertNotIn('pc_claim_by',site)


class ExplicitAuditHoldTest(unittest.TestCase):
    def test_historical_success_cannot_override_current_hold_or_uncertain_submission(self):
        from cafe24_gate import unattended_block
        legacy={'platform':'gnuboard','write_test_status':'passed','verified_post_url':'https://example.com/bbs/board.php?bo_table=free&wr_id=42'}
        self.assertEqual(unattended_block(legacy),'')
        for state in ('human_verification_required','admin_only','manual_review','result_review','site_database_error','hosting_service_restricted','hosting_unavailable','credentials_required','tls_certificate_expired','tls_validation_error'):
            self.assertTrue(unattended_block(dict(legacy,unattended_audit={'state':state})),state)
        self.assertEqual(unattended_block(dict(legacy,unattended_audit={'state':'ready'})),'')
