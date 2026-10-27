import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from cozit_adapter import PROJECT_URL, block, content, register, valid_project_url


class CozitTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,28,0,0,tzinfo=timezone.utc)
        self.settings={'project_url':PROJECT_URL,'permission':True,'daily_limit':1,
                       'min_interval_minutes':1440,'verified_submissions':[]}

    def test_only_owner_project_https_destination_is_allowed(self):
        for url in ('https://evil.example/','http://cozit.app/','https://cozit.app@evil.example/',
                    PROJECT_URL+'?token=x',PROJECT_URL+'/edit'):
            self.assertFalse(valid_project_url(url))
            self.assertEqual(block(dict(self.settings,project_url=url),{},self.now),'owner_permission_required')

    def test_permission_and_human_holds_are_not_credentials(self):
        self.assertTrue(block(dict(self.settings,permission=False),{},self.now))
        for key,value in [('has_captcha',True),('maintenance_hold',True),('manual_review_only',True),
                          ('write_access_state','admin_only'),('write_access_state','login_required'),
                          ('write_access_state','human_verification_required')]:
            self.assertTrue(block(dict(self.settings,**{key:value}),{},self.now))

    def test_uncertain_or_inflight_submit_cannot_be_retried(self):
        for state in ('submitting','submitted','uncertain'):
            self.assertEqual(block(self.settings,{'state':state},self.now),'previous_submission_requires_review')

    def test_verified_journal_preserves_limit_if_history_write_was_interrupted(self):
        journal={'state':'verified','submitted_at':(self.now-timedelta(minutes=1)).isoformat()}
        self.assertEqual(block(self.settings,journal,self.now),'daily_limit')

    def test_korean_day_limit_does_not_use_utc_date(self):
        settings=copy.deepcopy(self.settings)
        settings['verified_submissions']=[datetime(2026,10,27,23,50,tzinfo=timezone.utc).isoformat()]
        self.assertEqual(block(settings,{'state':'verified'},self.now),'daily_limit')

    def test_minimum_interval_survives_day_boundary(self):
        settings=copy.deepcopy(self.settings)
        settings['verified_submissions']=[datetime(2026,10,27,14,59,tzinfo=timezone.utc).isoformat()]
        self.assertEqual(block(settings,{'state':'verified'},self.now),'minimum_interval')

    def test_limits_can_never_disable_guard(self):
        self.assertEqual(block(dict(self.settings,daily_limit=0),{},self.now),'invalid_limits')
        self.assertEqual(block(dict(self.settings,min_interval_minutes=0),{},self.now),'invalid_limits')

    def test_title_and_body_are_not_silently_truncated(self):
        self.assertEqual(content(' title ',' body '),('title','body'))
        for title,body in [('', 'body'),('title',''),('a'*101,'body'),('title','a'*601),('😀'*51,'body')]:
            with self.assertRaises(ValueError):content(title,body)

    def test_route_requires_csrf_and_explicit_owner_update_intent(self):
        from flask import Flask
        app=Flask(__name__);app.secret_key='test-only';register(app,{})
        client=app.test_client()
        with patch('cozit_adapter.publish_update') as publish:
            self.assertEqual(client.post('/api/cozit/project-update',json={}).status_code,403)
            with client.session_transaction() as session:session['cozit_csrf']='test-token'
            headers={'X-Cozit-CSRF':'test-token'}
            self.assertEqual(client.post('/api/cozit/project-update',json={'title':'x','body':'y'},headers=headers).status_code,400)
            publish.assert_not_called()
            publish.return_value={'ok':False,'reason':'daily_limit','submitted_posts':0}
            response=client.post('/api/cozit/project-update',headers=headers,
                json={'title':'x','body':'y','owner_project_update_confirmed':True})
            self.assertEqual(response.status_code,409)
            publish.assert_called_once()


if __name__=='__main__':unittest.main()
