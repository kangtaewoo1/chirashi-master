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
    def test_gnuboard_waits_for_human_in_same_worker_session(self):
        tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post')
        calls={n.func.id for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        self.assertIn('wait_for_manual_captcha',calls)
        self.assertNotIn('solve_captcha_with_2captcha',calls)
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
