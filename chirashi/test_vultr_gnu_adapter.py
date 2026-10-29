import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from vultr_gnu_adapter import publish,valid_result,SID,ORIGIN

class VultrTransportTests(unittest.TestCase):
    def environment(self,folder):
        site={'id':SID,'site_url':ORIGIN,'bo_table':'freeboard','publish_executor':'vultr_server',
              'verified_post_url':ORIGIN+'/bbs/board.php?bo_table=freeboard&wr_id=1','_maintenance_job_id':'one',
              'unattended_audit':{'state':'ready'}}
        ctx={'DATA_DIR':Path(folder),'is_autopostable':lambda s:True,'publishing_block':lambda s:'',
             'unattended_block':lambda s:'','under_daily_limit':lambda *a:True,'under_min_interval':lambda s:(True,0),
             'load_config':lambda:{},'gnuboard_post_http':Mock(return_value=(True,ORIGIN+'/bbs/board.php?bo_table=freeboard&wr_id=2')),
             'set_site_flag':Mock(),'history_update':Mock()}
        response=Mock(status_code=200,headers={'Content-Type':'text/html; charset=euc-kr'},content=b'',text='<h1>Title</h1><div id="writeContents">Body</div>')
        return site,ctx,Mock(return_value=response)
    def test_one_server_submission_and_exact_public_receipt(self):
        with tempfile.TemporaryDirectory() as f:
            s,c,get=self.environment(f);self.assertTrue(publish(s,'Title','<p>Body</p>',c,get)[0])
            self.assertFalse(publish(s,'Title','<p>Body</p>',c,get)[0]);c['gnuboard_post_http'].assert_called_once()
            self.assertFalse(get.call_args.kwargs['allow_redirects']);self.assertNotEqual(get.call_args.kwargs.get('verify'),False)
            self.assertEqual(c['set_site_flag'].call_args.kwargs['unattended_result']['transport'],'vultr_direct_https')
    def test_permissions_and_unknown_result_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as f:
            s,c,get=self.environment(f);c['publishing_block']=lambda s:'human_verification_required'
            self.assertFalse(publish(s,'Title','Body',c,get)[0]);c['gnuboard_post_http'].assert_not_called()
            c['publishing_block']=lambda s:'';c['gnuboard_post_http'].return_value=(None,'timeout')
            self.assertFalse(publish(s,'Title','Body',c,get)[0]);self.assertFalse(publish(s,'Title','Body',c,get)[0])
            c['gnuboard_post_http'].assert_called_once();get.assert_not_called()
    def test_wrong_body_is_not_success(self):
        with tempfile.TemporaryDirectory() as f:
            s,c,get=self.environment(f)
            self.assertFalse(publish(s,'Title','Different body',c,get)[0]);c['set_site_flag'].assert_not_called()
    def test_old_or_foreign_urls_are_not_new_posts(self):
        old=ORIGIN+'/bbs/board.php?bo_table=freeboard&wr_id=10'
        for url in [old,old.replace('dnc77.cafe24.com','example.com'),old.replace('freeboard','secret'),old.replace('https:','http:')]:
            self.assertFalse(valid_result(url,old))
    def test_server_ownership_survives_old_queue_snapshot(self):
        import ast
        source=Path(__file__).with_name('app.py').read_text(encoding='utf-8')
        fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_server_queue_site')
        env={'load_sites':lambda:[{'id':SID,'publish_executor':'vultr_server'}]}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'routing','exec'),env)
        self.assertTrue(env['_server_queue_site']({'id':SID}))
        self.assertFalse(env['_server_queue_site']({'id':'other'}))
