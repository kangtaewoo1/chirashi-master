import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import telpan_adapter as t
from cafe24_gate import unattended_block


def site():
    url = t.ORIGIN + '/board/ads/88P7T1gFVnXxvjB4OjQO'
    return {'id': 'telpan-ads', 'site_url': t.ORIGIN, 'platform': 'telpan',
            'permission': True, 'registration_source': 'manual_admin', 'telpan_enabled': True,
            'telpan_login_saved': True, 'mb_id': 'account', 'login_required': True,
            'write_test_status': 'passed', 'verified_post_url': url,
            'unattended_result': {'state': 'verified', 'transport': 'telpan_server_browser',
                                  'url': url, 'title_match': True, 'body_match': True}}


class AdmissionTests(unittest.TestCase):
    def test_only_server_receipt_admits_queue(self):
        s = site(); self.assertEqual(unattended_block(s), '')
        s['unattended_result']['transport'] = 'user_authenticated_browser'
        self.assertEqual(unattended_block(s), 'telpan_server_verification_required')

    def test_permission_captcha_holds_and_credentials_are_not_bypassed(self):
        for change in ({'permission': False}, {'has_captcha': True}, {'maintenance_hold': True},
                       {'manual_review_only': True}, {'telpan_login_saved': False},
                       {'write_access_state': 'admin_only'}, {'status': 'rejected'},
                       {'unattended_audit': {'state': 'result_review'}},
                       {'last_fail_reason': '결과 검증 대기 — 재제출 금지'},
                       {'last_fail_reason': '사람 확인 필요'}):
            with self.subTest(change=change):
                self.assertTrue(t.block(dict(site(), **change), bootstrap=True))

    def test_bootstrap_does_not_manufacture_receipt(self):
        s = site(); s.pop('unattended_result'); s['verified_post_url'] = ''; s['write_test_status'] = 'pending'
        self.assertEqual(t.block(s, bootstrap=True), '')
        self.assertTrue(t.block(s))

    def test_urls_require_exact_origin_board_and_unique_id(self):
        self.assertTrue(t.valid_post_url(site()['verified_post_url']))
        for url in ('http://www.telpan.co.kr/board/ads/' + 'a'*20,
                    'https://evil.test/board/ads/' + 'a'*20,
                    t.ORIGIN + '/board/ads/write', t.ORIGIN + '/board/free/' + 'a'*20,
                    site()['verified_post_url'] + '?token=secret'):
            self.assertFalse(t.valid_post_url(url))


class ContentTests(unittest.TestCase):
    def test_plain_conversion_and_utf16_limits(self):
        self.assertEqual(t.content(' 제목 ', '<p>본문 &amp; 내용</p><script>secret</script>'), ('제목', '본문 & 내용'))
        for title, body in [('x'*41, 'body'), ('title', 'x'*501), ('😀'*21, 'body'), ('', 'body')]:
            with self.assertRaises(ValueError): t.content(title, body)

    def test_only_visible_post_title_and_body_count(self):
        url = site()['verified_post_url']
        def get(markup, code=200):
            return lambda *a, **kw: SimpleNamespace(status_code=code, url=url, text=markup)
        html = '<h1 class="post-title">제목</h1><div class="post-content"><pre>실제 본문</pre></div>'
        r = t.verify_public(url, '제목', '실제 본문', get(html))
        self.assertTrue(r['public_read_without_authentication'])
        for markup in ('<script>제목 실제 본문</script>',
                       '<h1 class="post-title">다른 제목</h1><div class="post-content">실제 본문</div>',
                       '<h1 class="post-title">제목</h1><div>실제 본문</div>'):
            with self.assertRaises(ValueError): t.verify_public(url, '제목', '실제 본문', get(markup))
        with self.assertRaises(ValueError): t.verify_public(url, '제목', '실제 본문', get(html, 302))


class NoSubmissionTests(unittest.TestCase):
    def test_daily_and_interval_rejections_never_start_browser(self):
        for daily, interval in ((False, True), (True, False)):
            ctx = {'_fresh_site': lambda s:s, 'under_daily_limit': lambda *a:daily,
                   'under_min_interval': lambda *a:(interval, 600), 'load_config': lambda:{}}
            with tempfile.TemporaryDirectory() as tmp, patch.object(t, 'new_driver') as driver:
                ctx['DATA_DIR'] = Path(tmp)
                ok, msg = t.publish(site(), '제목', '본문', ctx)
                self.assertFalse(ok); driver.assert_not_called()

    def test_restart_uncertainty_prevents_another_submit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(t, 'new_driver') as driver:
            t.private_write(Path(tmp)/'telpan_submission.json', {'state': 'submitting'})
            ctx = {'DATA_DIR': Path(tmp), '_fresh_site': lambda s:s, 'under_daily_limit': lambda *a:True,
                   'under_min_interval': lambda *a:(True, 0), 'load_config': lambda:{}}
            ok, msg = t.publish(site(), '제목', '본문', ctx)
            self.assertFalse(ok); self.assertIn('재제출 금지', msg); driver.assert_not_called()

    def test_private_file_is_replaced_without_secret_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'private.json'; t.private_write(path, {'password_enc': 'encrypted'})
            self.assertEqual(__import__('json').loads(path.read_text()), {'password_enc': 'encrypted'})


class SetupTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sites = []
        self.app = Flask(__name__); self.app.secret_key = 'test-only'
        self.ctx = {'DATA_DIR': Path(self.tmp.name), 'POST_LOCK': threading_lock(),
                    'load_sites': lambda:copy.deepcopy(self.sites),
                    'save_sites': lambda value:self.sites.__setitem__(slice(None), copy.deepcopy(value)),
                    '_encrypt_password': lambda pw:'ciphertext-for-test',
                    'load_json': lambda path, default:__import__('json').loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default}
        t.register(self.app, self.ctx)
        self.client = self.app.test_client()

    def test_setup_requires_csrf_and_never_returns_password(self):
        body = {'nickname':'user', 'password':'not-a-real-password'}
        self.assertEqual(self.client.post('/api/telpan/setup', json=body).status_code, 403)
        self.assertFalse((Path(self.tmp.name)/'telpan_login_private.json').exists())
        with self.client.session_transaction() as sess: sess['telpan_csrf'] = 'test-csrf'
        r = self.client.post('/api/telpan/setup', json=body, headers={'X-Telpan-CSRF':'test-csrf'})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(body['password'], r.text)
        self.assertNotIn(body['password'], (Path(self.tmp.name)/'telpan_login_private.json').read_text())
        self.assertNotIn(body['password'], self.client.get('/api/telpan/status').text)
        self.assertTrue(t.block(self.sites[0]))

    def test_credentials_refresh_preserves_existing_restrictions_and_invalidates_proof(self):
        s = site(); s.update(maintenance_hold=True, daily_limit=1, min_interval_minutes=1440, posted_today=1)
        self.sites.append(s)
        with self.client.session_transaction() as sess: sess['telpan_csrf'] = 'test-csrf'
        self.client.post('/api/telpan/setup', json={'nickname':'user','password':'not-a-real-password'},
                         headers={'X-Telpan-CSRF':'test-csrf'})
        after = self.sites[0]
        self.assertTrue(after['maintenance_hold']); self.assertEqual(after['min_interval_minutes'],1440)
        self.assertEqual(after['daily_limit'],1); self.assertEqual(after['posted_today'],1)
        self.assertEqual(after['verified_post_url'],''); self.assertNotIn('unattended_result',after)

    def test_rendered_page_script_is_valid_javascript(self):
        import re, shutil, subprocess
        node = shutil.which('node')
        if not node: self.skipTest('Node unavailable for browser-script syntax check')
        page = self.client.get('/telpan').text
        script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
        p = subprocess.run([node, '--check'], input=script, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_signup_requires_complete_identity_and_explicit_request(self):
        with self.client.session_transaction() as sess: sess['telpan_csrf'] = 'test-csrf'
        with patch.object(t, 'signup_once') as call:
            for data in ({}, {'signup_confirmed':True,'nickname':'u','password':'pw'}):
                r=self.client.post('/api/telpan/signup', json=data, headers={'X-Telpan-CSRF':'test-csrf'})
                self.assertEqual(r.status_code,400)
            call.assert_not_called()

    def test_unknown_signup_is_durable_and_never_repeated(self):
        with self.client.session_transaction() as sess: sess['telpan_csrf'] = 'test-csrf'
        data={'signup_confirmed':True,'nickname':'new-user','password':'test-password',
              'email':'user@example.test','snsId':'test-id'}
        with patch.object(t,'signup_once',return_value=('unknown','결과 확인 필요')) as call:
            self.client.post('/api/telpan/signup',json=data,headers={'X-Telpan-CSRF':'test-csrf'})
            r=self.client.post('/api/telpan/signup',json=data,headers={'X-Telpan-CSRF':'test-csrf'})
            self.assertEqual(r.status_code,409);self.assertEqual(call.call_count,1)
        self.assertFalse(self.sites)
        self.assertNotIn(data['password'],self.client.get('/api/telpan/status').text)

    def test_created_then_login_verified_still_does_not_count_as_published(self):
        with self.client.session_transaction() as sess: sess['telpan_csrf'] = 'test-csrf'
        data={'signup_confirmed':True,'nickname':'new-user','password':'test-password',
              'email':'user@example.test','snsId':'test-id'}
        with patch.object(t,'signup_once',return_value=('created','가입 성공')), \
             patch.object(t,'verify_login',return_value={'login_verified':True,'write_form_visible':True,'submitted_posts':0}), \
             patch.object(t.threading,'Thread',side_effect=lambda target,**kw:SimpleNamespace(start=target)):
            r=self.client.post('/api/telpan/signup',json=data,headers={'X-Telpan-CSRF':'test-csrf'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/telpan/status').json['signup']['state'],'login_verified')
        self.assertEqual(self.sites[0]['write_test_status'],'pending')
        self.assertEqual(self.sites[0]['verified_post_url'],'');self.assertTrue(t.block(self.sites[0]))


class SignupTransportTests(unittest.TestCase):
    def test_uses_the_official_form_endpoint_once_with_no_redirect_or_proxy(self):
        profile=t.signup_profile({'nickname':'name','password':'password','email':'a@example.test','snsId':'contact'})
        post=Mock(return_value=SimpleNamespace(status_code=200,json=lambda:{'success':True}))
        self.assertEqual(t.signup_once(profile,post)[0],'created')
        post.assert_called_once_with(t.ORIGIN+'/api/signup',json=profile,timeout=25,allow_redirects=False)

    def test_ip_restriction_and_private_errors_do_not_leak_or_retry(self):
        for raw,state in [('동일 IP에서는 1계정만 가입 가능합니다','restricted'),
                          ('이미 존재하는 닉네임입니다','duplicate'),('CAPTCHA 인증 필요','human_required'),
                          ('unexpected password=secret email=private','rejected')]:
            post=Mock(return_value=SimpleNamespace(status_code=400,json=lambda:{'success':False,'message':raw}))
            result,message=t.signup_once({},post);self.assertEqual(result,state);post.assert_called_once()
            self.assertNotIn('secret',message);self.assertNotIn('private',message)

    def test_timeout_and_redirect_hold_for_review(self):
        for post in (Mock(side_effect=TimeoutError('secret')),Mock(return_value=SimpleNamespace(status_code=302))):
            self.assertEqual(t.signup_once({},post)[0],'unknown');post.assert_called_once()


def threading_lock():
    return __import__('threading').RLock()


if __name__ == '__main__': unittest.main()
