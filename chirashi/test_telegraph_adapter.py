import json
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock
from telegraph_adapter import ORIGIN,FLOW,block,prepare,call,valid_url,verify_public
from form_schema import merge_flow_knowledge


class TelegraphTests(unittest.TestCase):
    def setUp(self):
        self.settings={'origin':ORIGIN,'permission':True,'daily_limit':1,'min_interval_minutes':1440}
        self.now=datetime(2026,10,28,tzinfo=timezone.utc)

    def test_no_credentialed_cross_origin_or_query_receipts(self):
        for url in ['http://telegra.ph/a','https://evil.example/a','https://telegra.ph@evil.example/a',
                    'https://u:secret@telegra.ph/a','https://telegra.ph/a?token=secret','https://telegra.ph/api']:
            self.assertFalse(valid_url(url))
        self.assertTrue(valid_url('https://telegra.ph/project-note-10-28'))

    def test_content_is_plain_paragraph_nodes_and_never_silently_cut(self):
        title,body,nodes=prepare('title','first\n\nsecond')
        self.assertEqual(nodes,[{'tag':'p','children':['first']},{'tag':'p','children':['second']}])
        for title,body in [('', 'x'),('x',''),('a'*257,'x'),('x','한'*30000)]:
            with self.assertRaises(ValueError):prepare(title,body)

    def test_limits_and_every_hold_stay_enforced(self):
        for key,value in [('permission',False),('origin','https://evil.example'),('maintenance_hold',True),
                          ('has_captcha',True),('manual_review_only',True),('write_access_state','admin_only'),
                          ('write_access_state','human_verification_required'),('write_access_state','login_required')]:
            self.assertTrue(block(dict(self.settings,**{key:value}),{},self.now))
        for state in ('submitting','submitted','uncertain'):
            self.assertEqual(block(self.settings,{'state':state},self.now),'previous_submission_requires_review')

    def test_verified_journal_does_not_reopen_daily_limit(self):
        journal={'state':'verified','submitted_at':(self.now-timedelta(minutes=5)).isoformat()}
        self.assertEqual(block(self.settings,journal,self.now),'daily_limit')
        earlier={'state':'verified','submitted_at':datetime(2026,10,27,14,59,tzinfo=timezone.utc).isoformat()}
        self.assertEqual(block(self.settings,earlier,self.now),'minimum_interval')

    def test_api_token_never_enters_url_and_no_redirect_is_followed(self):
        post=Mock(return_value=Mock(status_code=200,json=lambda:{'ok':True,'result':{'short_name':'notes'}}))
        call('getAccountInfo',{'access_token':'private-token'},post)
        args,kw=post.call_args
        self.assertEqual(args[0],'https://api.telegra.ph/getAccountInfo')
        self.assertNotIn('private-token',args[0]);self.assertFalse(kw['allow_redirects'])
        self.assertEqual(kw['data']['access_token'],'private-token')
        self.assertNotEqual(kw.get('verify'),False)

    def test_rejected_provider_does_not_return_private_error(self):
        post=Mock(return_value=Mock(status_code=200,json=lambda:{'ok':False,'error':'private-token'}))
        with self.assertRaises(ValueError) as caught:call('createPage',{},post)
        self.assertNotIn('private-token',str(caught.exception));post.assert_called_once()

    def test_public_api_and_html_must_both_match_the_full_body(self):
        api=Mock(return_value={'title':'Title','content':[{'tag':'p','children':['full body']}]})
        def response(html):return Mock(status_code=200,url=ORIGIN+'/note-10-28',text=html)
        get=Mock(return_value=response('<article><h1>Title</h1><p>full body</p></article>'))
        receipt=verify_public(ORIGIN+'/note-10-28','Title','full body',api,get)
        self.assertEqual(receipt['transport'],'telegraph_server_api');self.assertTrue(receipt['body_match'])
        self.assertNotIn('access_token',api.call_args.args[1]);self.assertNotIn('cookies',get.call_args.kwargs)
        get.return_value=response('<article><h1>Title</h1><p>truncated</p></article>')
        with self.assertRaises(ValueError):verify_public(ORIGIN+'/note-10-28','Title','full body',api,get)

    def test_api_transport_is_separate_from_browser_only_proof(self):
        receipt={'url':ORIGIN+'/note-10-28','title_match':True,'body_match':True,
                 'public_read_without_authentication':True,'transport':'telegraph_server_api'}
        row=merge_flow_knowledge({},FLOW,{'stage':'server_post','state':'verified','receipt':receipt})
        self.assertTrue(row['server_publish_verified'])
        for transport in ['user_authenticated_browser','cozit_server_api']:
            receipt['transport']=transport
            with self.assertRaises(ValueError):merge_flow_knowledge({},FLOW,{'stage':'server_post','state':'verified','receipt':receipt})


if __name__=='__main__':unittest.main()
