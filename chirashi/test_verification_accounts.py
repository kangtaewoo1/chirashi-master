import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from flask import Flask
from site_verify_worker import database
from verification_accounts import execute,validate_spec,blocked,enqueue,cipher,register
from verification_captcha import solve_image

SPEC={'mode':'login','origin':'https://a.example','write_url':'https://a.example/bbs/write.php?bo_table=free','login_url':'https://a.example/login','signup_url':'https://a.example/signup'}
LOGIN={'state':'measured','form':{'action':'https://a.example/login','method':'post','fields':[]}}
WRITE={'state':'measured','form':{'action':'https://a.example/write_update','method':'post','fields':[]}}


class FakeBrowser:
    def __init__(self,human=None,terms=None):self.opened=[];self.submitted=0;self.inputs=0;self.auth=False;self.human=human;self.terms_value=terms;self.terms_submitted=0
    def open(self,url,origin):self.opened.append(url)
    def read(self,stage,url):
        return {'url':url,'stage':stage,'human_check':{'scope':stage if self.human==stage else 'not_observed','required_step_confirmed':self.human==stage}},WRITE if stage=='write' else LOGIN
    def logged_in(self):return self.auth
    def terms(self):return self.terms_value
    def submit_terms(self,*args):self.terms_submitted+=1
    def fill(self,*args):self.inputs+=1;return []
    def submit(self,schema,origin):self.submitted+=1;self.auth=True
    def signup_success(self):return True
    def needs_email(self):return False
    def write_controls(self,schema):return ['title','content']


class AccountWorkerTests(unittest.TestCase):
    def test_actual_login_returns_to_exact_original_board_without_post(self):
        b=FakeBrowser();journal=[]
        r=execute(SPEC,{'id':'PRIVATEID','password':'SECRET'}, {},None,b,journal.append)
        self.assertEqual(b.opened,[SPEC['login_url'],SPEC['write_url']]);self.assertEqual(b.submitted,1)
        self.assertTrue(r['login_verified']);self.assertTrue(r['authenticated_write_verified'])
        self.assertEqual(r['new_posts_submitted'],0);self.assertNotIn('SECRET',json.dumps(r))
        self.assertEqual(journal[0]['state'],'login_uncertain')
    def test_captcha_stops_before_credentials_and_no_solver_without_approval(self):
        b=FakeBrowser(human='login');r=execute(SPEC,{'id':'a','password':'b'}, {},None,b,lambda r:None)
        self.assertEqual(r['state'],'waiting_human');self.assertEqual(b.inputs,0);self.assertEqual(b.submitted,0)
    def test_signup_terms_require_matching_current_fingerprint(self):
        b=FakeBrowser(terms={'fingerprint':'new','text':'terms','names':['agree']})
        r=execute(dict(SPEC,mode='signup'),{}, {},{'fingerprint':'old','at':time.time()},b,lambda r:None)
        self.assertEqual(r['state'],'waiting_terms');self.assertEqual(b.terms_submitted,0)
    def test_matching_site_terms_allow_one_signup_then_write_recheck(self):
        b=FakeBrowser(terms={'fingerprint':'same'});journal=[]
        r=execute(dict(SPEC,mode='signup'),{'id':'a','password':'b'}, {},{'fingerprint':'same','at':time.time()},b,journal.append)
        self.assertTrue(r['signup_verified']);self.assertTrue(r['authenticated_write_verified'])
        self.assertEqual(b.submitted,1);self.assertEqual(b.terms_submitted,1)
        self.assertEqual(journal[0]['state'],'signup_uncertain')
    def test_ambiguous_signup_is_never_resubmitted(self):
        b=FakeBrowser();r=execute(dict(SPEC,mode='signup'),{}, {'signup_submitted':True},None,b,lambda r:None)
        self.assertEqual(r['state'],'signup_uncertain');self.assertEqual(b.opened,[])
    def test_remote_form_action_checked_before_credentials(self):
        b=FakeBrowser()
        original=b.read
        b.read=lambda stage,url:(original(stage,url)[0],{'state':'measured','form':{'action':'https://other.example/login','method':'post'}})
        r=execute(SPEC,{'id':'a','password':'b'}, {},None,b,lambda r:None)
        self.assertEqual(r['state'],'permission_hold');self.assertEqual(b.inputs,0)
    def test_completed_widget_does_not_override_unsolved_image_or_page_access(self):
        p={'human_check':{'scope':'signup','required_step_confirmed':True},'challenge_widget_present':True,'challenge_response_present':True,'captcha_input_present':True,'captcha_input_empty':True}
        self.assertEqual(blocked(p,'signup'),'waiting_human')
        p.update(captcha_input_empty=False,captcha_input_answer_present=True)
        self.assertIsNone(blocked(p,'signup'))
        p['human_check']['scope']='page_access';self.assertEqual(blocked(p,'signup'),'waiting_human')
    def test_credentials_and_alias_are_encrypted_and_duplicates_blocked(self):
        with tempfile.TemporaryDirectory() as f:
            folder=Path(f);c=database(folder)
            jid=enqueue(c,folder,SPEC,{'id':'PRIVATEID','password':'SECRET','email_pattern':'owner+{tag}@example.com'})
            row=c.execute('SELECT spec,secret FROM account_jobs WHERE id=?',(jid,)).fetchone()
            self.assertNotIn('PRIVATEID',str(row));self.assertNotIn('SECRET',str(row))
            p=json.loads(cipher(folder).decrypt(row[1].encode()));self.assertTrue(p['email'].startswith('owner+verify'))
            with self.assertRaises(ValueError):enqueue(c,folder,SPEC,{})
            c.close()
    def test_rejects_http_existing_post_and_external_auth_routes(self):
        for s in [dict(SPEC,write_url='http://a.example/write'),dict(SPEC,write_url=SPEC['write_url']+'&wr_id=2'),dict(SPEC,login_url='https://other.example/login')]:
            with self.assertRaises(ValueError):validate_spec(s)
    def test_api_requires_csrf_and_never_exposes_saved_secrets(self):
        with tempfile.TemporaryDirectory() as f:
            folder=Path(f);c=database(folder);c.close();app=Flask('accounts');app.secret_key='test-only'
            register(app,{},folder);client=app.test_client()
            self.assertEqual(client.post('/api/site-verification-account',json={}).status_code,403)
            self.assertEqual(client.get('/site-verification-account').status_code,200)
            with client.session_transaction() as session:token=session['verification_csrf']
            response=client.post('/api/site-verification-account',headers={'X-Verification-CSRF':token},json={'action':'start','spec':SPEC,'authorized':True,'profile':{'id':'PRIVATEID','password':'SECRET'}})
            self.assertEqual(response.status_code,200)
            public=client.get('/api/site-verification-account?domain=a.example').json
            self.assertNotIn('SECRET',json.dumps(public));self.assertNotIn('PRIVATEID',json.dumps(public))
    def test_provider_creates_only_one_task_and_reports_actual_cost(self):
        class Response:
            def __init__(self,d):self.d=d
            def raise_for_status(self):pass
            def json(self):return self.d
        class Transport:
            def __init__(self):self.calls=[]
            def post(self,url,**kw):
                self.calls.append(url);return Response({'taskId':1} if url.endswith('createTask') else {'status':'ready','solution':{'text':'abc'},'cost':'0.00025'})
        t=Transport()
        with patch('verification_captcha.time.sleep'):
            result=solve_image('PRIVATEKEY','image',transport=t)
        self.assertEqual(len(t.calls),2);self.assertEqual(result['actual_cost_usd'],'0.00025')
