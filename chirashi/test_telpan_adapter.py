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
                    '_encrypt_password': lambda pw:'ciphertext-for-test', 'load_json': lambda *a:{}}
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


def threading_lock():
    return __import__('threading').RLock()


if __name__ == '__main__': unittest.main()
