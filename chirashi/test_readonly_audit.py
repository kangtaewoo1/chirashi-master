import ast
import re
import sys
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch
from form_schema import measure_html

class ReadOnlyAuditTest(unittest.TestCase):
    def test_http_captcha_form_stops_before_solver_or_submission(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post_http')
        calls=[]
        html='<form action="write_update.php"><input name="wr_subject"><input name="captcha_key"><textarea name="wr_content"></textarea></form>'
        session=types.SimpleNamespace(headers={},get=lambda *a,**kw:(calls.append('GET') or types.SimpleNamespace(status_code=200,text=html,apparent_encoding='utf-8',encoding='utf-8',url="http://approved.example/bbs/write.php")),post=lambda *a,**kw:self.fail('No submission allowed'))
        fake=types.ModuleType('requests');fake.Session=lambda:session
        env={'re':re,'load_config':lambda:{},'_HTTP_UA':'test','_pick_writer':lambda s:'Test','_post_password':lambda c:'test',
             '_brand_email':lambda *a:'','_strip_non_bmp':lambda s:s,'_ocr_available':lambda:False}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),env)
        with patch.dict(sys.modules,{'requests':fake}):
            ok,message=env['gnuboard_post_http']({'site_url':'http://approved.example','bo_table':'free'},'Functional test','Test body')
        self.assertIsNone(ok);self.assertIn('노드 위임',message);self.assertEqual(calls,['GET'])
    def test_uncertain_submission_and_human_wait_are_never_reposted(self):
        import threading
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_do_publish_job')
        for message in ('등록 확인 불가','CAPTCHA 수동 입력 대기시간 만료','게시 권한 없음','무인 발행 보류','결과 검증 대기'):
            calls=[];updates=[]
            def post(*args):calls.append(args);return False,message
            env={'under_daily_limit':lambda *a:True,'under_min_interval':lambda *a:(True,0),'history_update':lambda *a,**kw:updates.append(kw),
                 'do_post':post,'classify_fail':lambda m:('temporary','temporary',True),'RETRY_MAX':3,'wk_active':True,'wk_paused':False,
                 'STATS_LOCK':threading.Lock(),'wk_stats':{'fail':0,'done':0},'post_queue':types.SimpleNamespace(qsize=lambda:0),
                 'finalize_post':lambda *a,**kw:None,'_persist_remove':lambda *a:None,'add_log':lambda *a:None,
                 'reset_driver':lambda: self.fail('Must not retry uncertain/human failures'),'schedule_retry':lambda *a:self.fail('Must not schedule repost')}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),env)
            env['_do_publish_job']({'title':'Functional test','content':'Test body'}, {'id':'approved'}, {'post_delay':0},'Test','job','history')
            self.assertEqual(len(calls),1)
            self.assertEqual(updates[-1]['status'],'failed')
    def test_gnuboard_tries_auto_solver_then_waits_for_human_in_same_worker_session(self):
        # 2026-10-16: 자동해결(전용 OCR→2captcha)을 먼저 시도하고, 실패할 때만 같은 워커 세션에서 사람 입력을 기다린다.
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post')
        calls={n.func.id for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertIn('wait_for_manual_captcha',calls)
        self.assertIn('solve_captcha_with_2captcha',calls)
        messages=[n.value for n in ast.walk(fn) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertFalse(any('로그인 필요 추정' in x or '로그인 필요/비표준' in x for x in messages))
    def measure(self,response):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_read_form_schema')
        calls=[]; fake=types.ModuleType('requests')
        fake.get=lambda url,**kw:(calls.append((url,kw)) or response)
        env={'_signup_origin':lambda s:'https://approved.example','urllib':urllib,'re':re,'measure_html':measure_html}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),env)
        with patch.dict(sys.modules,{'requests':fake}):
            result=env['_read_form_schema']({'platform':'cafe24','bo_table':'6'},'signup')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0],'https://approved.example/member/join.html')
        return result
    def response(self,html,status=200,url='https://approved.example/member/join.html'):
        return types.SimpleNamespace(text=html,content=html.encode(),headers={'Content-Type':'text/html; charset=utf-8'},url=url,status_code=status)
    def test_agreement_is_read_without_submission(self):
        result=self.measure(self.response('<form action="/join">이용약관 동의<input name="agree" type="checkbox"></form>'))
        self.assertEqual(result['state'],'agreement_required')
    def test_challenge_redirect_has_no_token_leak(self):
        result=self.measure(self.response('<div class="cf-turnstile"></div>',url='https://challenge.example/challenge?auth=SECRET'))
        self.assertEqual(result['state'],'human_verification_required')
        self.assertNotIn('SECRET',str(result))
    def test_http_error_does_not_include_redirect_token(self):
        with self.assertRaisesRegex(RuntimeError,'HTTP 403') as error:
            self.measure(self.response('',status=403,url='https://challenge.example/?auth=SECRET'))
        self.assertNotIn('SECRET',str(error.exception))

if __name__=='__main__': unittest.main()



class AuditSettingsPreservationTest(unittest.TestCase):
    def test_reactivation_does_not_replace_site_posting_limits(self):
        import datetime
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_unattended_audit')
        fn.decorator_list=[];updates=[]
        site={'id':'site','platform':'gnuboard','site_url':'https://approved.example','bo_table':'free','daily_limit':37,'min_interval_minutes':17}
        env={'request':types.SimpleNamespace(get_json=lambda **k:{'site_ids':['site'],'activate':True}),
             'jsonify':lambda x:x,'load_sites':lambda:[site],'is_permitted':lambda s:True,'publishing_block':lambda s:'',
             '_signup_origin':lambda s:'https://approved.example','urllib':__import__('urllib'),'measure_html':measure_html,
             'set_site_flag':lambda sid,**kw:updates.append(kw),'datetime':datetime.datetime}
        response=types.SimpleNamespace(raise_for_status=lambda:None,apparent_encoding='utf-8',url='https://approved.example/bbs/write.php',text='<form><input name="wr_subject"><textarea name="wr_content"></textarea></form>')
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'audit-test','exec'),env)
        with patch.dict(sys.modules,requests=types.SimpleNamespace(get=lambda *a,**k:response)):
            result=env['api_unattended_audit']()
        self.assertTrue(result['ok'])
        self.assertEqual(updates[0]['unattended_audit']['state'],'ready')
        self.assertNotIn('daily_limit',updates[0])
        self.assertNotIn('min_interval_minutes',updates[0])
