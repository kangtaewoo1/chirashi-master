import ast
from datetime import datetime
import hashlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import Mock,patch
from cafe24_gate import publishing_block,unattended_block,browser_connection_error


class GnuboardBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree=ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))

    def site(self,**changes):
        return dict({'id':'approved','permission':True,'platform':'gnuboard','site_url':'https://approved.example',
                     'bo_table':'free','write_test_status':'passed','unattended_audit':{'state':'ready'},
                     'verified_post_url':'https://approved.example/bbs/board.php?bo_table=free&wr_id=10'},**changes)

    def run_post(self,site=None,result=None,body='Unique title Unique body',response_url=None,error=None):
        site=site or self.site()
        url='https://approved.example/bbs/board.php?bo_table=free&wr_id=11'
        post=Mock(return_value=result if result is not None else (True,url))
        reset=Mock();updates=Mock();hist=Mock()
        get=Mock(return_value=SimpleNamespace(text=body,url=response_url or url,apparent_encoding='utf-8',raise_for_status=lambda:None))
        if error:get.side_effect=error
        env={'is_autopostable':lambda s:s.get('permission') and s.get('status')!='rejected',
             'publishing_block':publishing_block,'unattended_block':unattended_block,'gnuboard_post':post,
             'urllib':urllib,'datetime':datetime,'hashlib':hashlib,'set_site_flag':updates,
             'history_update':hist,'browser_connection_error':browser_connection_error,'reset_driver':reset}
        fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='_post_gnuboard_brightdata')
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'gnu-browser-test','exec'),env)
        with patch.dict(sys.modules,requests=SimpleNamespace(get=get)):
            result=env[fn.name](site,'Unique title','<p>Unique body</p>')
        return result,post,get,reset,updates,hist

    def test_confirmed_new_post_requires_title_body_and_public_read(self):
        result,post,get,reset,updates,hist=self.run_post(site=self.site(_maintenance_history_id='job'))
        self.assertTrue(result[0]);post.assert_called_once();get.assert_called_once();reset.assert_called_once()
        receipt=updates.call_args.kwargs['unattended_result']
        self.assertEqual(receipt['transport'],'brightdata_browser_api')
        self.assertTrue(receipt['title_match'] and receipt['body_match']);hist.assert_called_once()

    def test_permission_and_human_holds_never_start_remote_submission(self):
        for change in ({'permission':False},{'status':'rejected'},{'maintenance_hold':True},
                       {'has_captcha':True},{'manual_review_only':True},{'write_access_state':'admin_only'},
                       {'write_access_state':'human_verification_required'},{'login_required':True},
                       {'unattended_audit':{'state':'tls_certificate_expired'}}):
            with self.subTest(change=change):
                result,post,get,reset,updates,_=self.run_post(site=self.site(**change))
                self.assertFalse(result[0]);post.assert_not_called();get.assert_not_called();updates.assert_not_called()

    def test_previous_or_foreign_result_is_not_a_new_post(self):
        for url in ('https://approved.example/bbs/board.php?bo_table=free&wr_id=10',
                    'https://approved.example/bbs/board.php?bo_table=free&wr_id=0',
                    'https://approved.example/bbs/board.php?bo_table=other&wr_id=11',
                    'https://other.example/bbs/board.php?bo_table=free&wr_id=11'):
            with self.subTest(url=url):
                result,post,get,reset,updates,_=self.run_post(result=(True,url))
                self.assertFalse(result[0]);post.assert_called_once();get.assert_not_called();updates.assert_not_called();reset.assert_called_once()

    def test_missing_title_or_body_does_not_pass_or_retry(self):
        for body in ('Unique title','Unique body','Other post'):
            result,post,get,reset,updates,_=self.run_post(body=body)
            self.assertFalse(result[0]);self.assertIn('재제출 금지',result[1]);post.assert_called_once();updates.assert_not_called();reset.assert_called_once()

    def test_redirect_or_read_failure_does_not_pass_or_repeat(self):
        for kwargs in ({'response_url':'https://other.example/login'},{'error':RuntimeError('407 secret-endpoint')}):
            result,post,get,reset,updates,_=self.run_post(**kwargs)
            self.assertFalse(result[0]);self.assertNotIn('secret-endpoint',result[1]);self.assertIn('재제출 금지',result[1]);post.assert_called_once();reset.assert_called_once()

    def test_remote_failure_has_no_http_fallback(self):
        result,post,get,reset,updates,_=self.run_post(result=(False,'사람 확인 필요'))
        self.assertFalse(result[0]);post.assert_called_once();get.assert_not_called();reset.assert_called_once();updates.assert_not_called()

    def test_router_uses_remote_before_recipe_or_http(self):
        fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='do_post')
        end=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.If) and '_post_gnuboard_brightdata' in ast.unparse(n))
        routed=ast.FunctionDef(name=fn.name,args=fn.args,body=fn.body[:end+1],decorator_list=[])
        remote=Mock(return_value=(True,'new-url'))
        env={'load_config':lambda:{'sbr_enabled':True},'resolve_platform':lambda s:s['platform'],'_post_gnuboard_brightdata':remote}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[routed],type_ignores=[])),'routing-test','exec'),env)
        self.assertEqual(env['do_post'](self.site(),'title','body'),(True,'new-url'));remote.assert_called_once()
        remote.reset_mock();self.assertFalse(env['do_post'](self.site(maintenance_hold=True),'title','body')[0]);remote.assert_not_called()

    def test_gnuboard_selects_real_remote_driver(self):
        fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post')
        expression=next(n.value for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='d' for t in n.targets))
        driver=Mock()
        eval(compile(ast.Expression(expression),'driver-choice-test','eval'),{'get_driver':driver,'_use_sbr':True})
        driver.assert_called_once_with(remote=True)

    def test_detected_challenge_is_saved_before_any_form_fill(self):
        fn=next(n for n in self.tree.body if isinstance(n,ast.FunctionDef) and n.name=='gnuboard_post')
        branch=next(n for n in fn.body if isinstance(n,ast.If) and ast.unparse(n.test)=='_use_sbr and _cap')
        probe=ast.FunctionDef(name='probe',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[branch],decorator_list=[])
        updates=Mock()
        env={'_use_sbr':True,'_cap':'recaptcha','site':self.site(),'set_site_flag':updates,'datetime':datetime}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[probe],type_ignores=[])),'challenge-test','exec'),env)
        self.assertFalse(env['probe']()[0])
        updates.assert_called_once()
        self.assertTrue(updates.call_args.kwargs['has_captcha'])
        self.assertEqual(updates.call_args.kwargs['write_access_state'],'human_verification_required')


if __name__=='__main__': unittest.main()
