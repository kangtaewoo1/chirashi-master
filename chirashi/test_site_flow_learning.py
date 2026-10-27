import ast
import copy
import json
import tempfile
import threading
import unittest
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import urllib.parse

from form_schema import merge_flow_knowledge
from telpan_adapter import FLOW, learned_signup_profile, resume_known_account


class FlowKnowledgeTests(unittest.TestCase):
    def test_browser_login_recipe_does_not_claim_server_connection(self):
        from flask import Flask, render_template_string
        from telpan_adapter import KNOWLEDGE_PAGE
        flow=copy.deepcopy(FLOW)
        flow['implementation_state']='browser_recipe_measured'
        row=merge_flow_knowledge({},flow,{'stage':'browser_login','state':'verified'})
        self.assertFalse(row['server_publish_verified'])
        self.assertNotIn('login',row['evidence'])
        with Flask(__name__).app_context():
            page=render_template_string(KNOWLEDGE_PAGE,rows=[row])
        self.assertIn('서버 자동발행 미연결',page)
        self.assertNotIn('전용 가입·로그인 처리 연결됨',page)
        self.assertIn('실제 브라우저 로그인</dt><dd>확인 완료',page)
        self.assertIn('실제 서버 로그인</dt><dd>아직 검증되지 않음',page)

    def test_telpan_field_meanings_and_unknown_verification_are_explicit(self):
        profile=learned_signup_profile()
        self.assertEqual({f['name']:f['role'] for f in profile['fields']},
                         {'nickname':'account_nickname','password':'account_password',
                          'email':'owner_email','snsId':'contact_sns'})
        self.assertIsNone(profile['email_verification_required'])
        self.assertFalse(profile['live_signup_verified'])

    def test_same_recipe_reuses_version_changed_recipe_invalidates_evidence(self):
        first=merge_flow_knowledge({},FLOW,{'stage':'login','state':'verified'})
        second=merge_flow_knowledge(first,FLOW)
        self.assertEqual(first['version'],second['version'])
        changed=copy.deepcopy(FLOW);changed['limits']['title_utf16']=39
        third=merge_flow_knowledge(second,changed)
        self.assertEqual(third['version'],second['version']+1)
        self.assertFalse(third['evidence']);self.assertTrue(third['history'][-1]['evidence'])

    def test_signup_and_login_are_not_server_post_evidence(self):
        p=merge_flow_knowledge({},FLOW,{'stage':'signup','state':'created'})
        p=merge_flow_knowledge(p,FLOW,{'stage':'login','state':'verified'})
        self.assertFalse(p['server_publish_verified'])

    def test_browser_post_and_server_post_stay_separate(self):
        receipt={'url':FLOW['origin']+'/board/ads/88P7T1gFVnXxvjB4OjQO','title_match':True,'body_match':True,
                 'public_read_without_authentication':True,'transport':'user_authenticated_browser'}
        p=merge_flow_knowledge({},FLOW,{'stage':'browser_post','state':'verified','receipt':receipt})
        self.assertFalse(p['server_publish_verified'])
        with self.assertRaises(ValueError):
            merge_flow_knowledge(p,FLOW,{'stage':'server_post','state':'verified','receipt':receipt})
        receipt['transport']='telpan_server_browser'
        p=merge_flow_knowledge(p,FLOW,{'stage':'server_post','state':'verified','receipt':receipt})
        self.assertTrue(p['server_publish_verified'])

    def test_knowledge_never_copies_actual_field_values_or_private_errors(self):
        flow=copy.deepcopy(FLOW)
        flow['fields'][0].update(value='private-contact',password='secret')
        p=merge_flow_knowledge({},flow,{'stage':'signup','state':'rejected','message':'private-error'})
        text=json.dumps(p)
        for forbidden in ('private-contact','secret','private-error'):self.assertNotIn(forbidden,text)

    def test_cross_origin_or_credentialed_routes_rejected(self):
        for url in ('https://evil.example/api/signup','https://user:secret@www.telpan.co.kr/api/signup',
                    FLOW['origin']+'/api/signup?token=secret'):
            flow=copy.deepcopy(FLOW);flow['routes']['signup_submit']=url
            with self.assertRaises(ValueError):merge_flow_knowledge({},flow)

    def test_missing_identity_reports_roles_without_inventing_an_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx={'DATA_DIR':Path(tmp),'load_json':lambda path,d:json.loads(Path(path).read_text()) if Path(path).exists() else d}
            site={};ok,message=resume_known_account(site,ctx)
            self.assertFalse(ok);self.assertEqual(site['signup_status'],'profile_required')
            self.assertIn('owner_email',site['signup_missing_roles'])
            self.assertFalse((Path(tmp)/'telpan_login_private.json').exists())

    def test_generic_signup_learning_dispatches_to_telpan_and_reuses_cache(self):
        source=(Path(__file__).parent/'app.py').read_text(encoding='utf-8')
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='learn_signup_profile')
        profiles={}
        env={'urllib':urllib,'datetime':datetime,'_signup_origin':lambda s:FLOW['origin'],
             '_signup_learn_locks':defaultdict(threading.RLock),'load_signup_profiles':lambda:profiles,
             'save_signup_profiles':lambda p:profiles.update(p),
             '_signup_form_measure':lambda s:self.fail('Must not use Gnuboard signup route')}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        site={'platform':'telpan','site_url':FLOW['origin']}
        first=env['learn_signup_profile'](site)
        second=env['learn_signup_profile'](site)
        self.assertEqual(first['form_action'],FLOW['routes']['signup_submit'])
        self.assertEqual(first['version'],second['version']);self.assertTrue(second['cached'])
        self.assertEqual(first['success_count'],0)


if __name__=='__main__':unittest.main()
