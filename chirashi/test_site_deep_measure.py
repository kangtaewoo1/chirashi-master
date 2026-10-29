import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from site_deep_measure import followup_urls,board_context,enrich,render_public,summarize_deep
from site_verify_worker import add_job,database,inspect


class DeepMeasurementTests(unittest.TestCase):
    def test_rendered_consent_gate_supersedes_static_form(self):
        base={'url':'https://a.example/write','stage':'write','form_state':'measured','fields':[],'write_mode':'new_post_requested'}
        rendered=dict(base,form_state='agreement_required',rendered_browser_checked=True)
        ancillary_login={'url':'https://a.example/login','stage':'login','form_state':'measured','fields':[]}
        r=summarize_deep([base,rendered,ancillary_login])
        self.assertEqual(r['state'],'agreement_review_required')
        self.assertEqual(len(r['flow_profile']['write']['boards']),1)
        self.assertFalse(r['publication_verified'])

    def test_rendered_login_redirect_supersedes_static_guest_form(self):
        base={'url':'https://a.example/write','stage':'write','form_state':'measured','fields':[],'write_mode':'new_post_requested'}
        rendered=dict(base,url='https://a.example/login',requested_url=base['url'],form_state='login_required',rendered_browser_checked=True)
        self.assertEqual(summarize_deep([base,rendered])['state'],'account_verification_pending')

    def test_board_list_rechecked_but_existing_post_not_used_as_new_write(self):
        r={'flow_profile':{'write':{'boards':[
            {'url':'https://a.example/bbs/write.php?bo_table=qa','write_mode':'new_post_requested'},
            {'url':'https://a.example/bbs/write.php?bo_table=x&wr_id=1','write_mode':'existing_item_related'}]}}}
        urls=followup_urls(r)
        self.assertEqual(urls[0],'https://a.example/bbs/board.php?bo_table=qa')
        self.assertFalse(any('wr_id' in u for u in urls))

    def test_context_preserves_login_hold_and_does_not_grant_promotion(self):
        pages=[{'url':'https://a.example/bbs/board.php?bo_table=a','stage':'entry','title':'상담신청'},
               {'url':'https://a.example/bbs/write.php?bo_table=a','stage':'write','title':'로그인','form_state':'login_required'}]
        result=board_context(pages)[1]
        self.assertEqual(result['form_state'],'login_required')
        self.assertEqual(result['purpose_hint']['kind'],'contact_or_application_form')
        self.assertFalse(result['purpose_hint']['permission_decision'])

    def test_runtime_missing_never_claims_browser_success(self):
        with patch('site_deep_measure.Path.is_file',return_value=False):
            r=render_public({'domain':'a.example','urls':['https://a.example/']},[])
        self.assertEqual(r['state'],'browser_runtime_unavailable')
        self.assertFalse(r['authenticated']);self.assertEqual(r['pages'],[])

    def test_existing_human_hold_never_triggers_browser_retry(self):
        result={'pages':[{'url':'https://a.example/write','stage':'write','human_check':{'scope':'write','required_step_confirmed':True}}]}
        with patch('site_deep_measure.render_public') as render:
            r=enrich(result,{'domain':'a.example','batch_id':'b'})
        render.assert_not_called();self.assertFalse(r['publication_verified'])
        self.assertFalse(r['actual_login_verified'])

    def test_seed_new_urls_preserves_explicit_deep_job_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            c=database(Path(folder));add_job(c,{'domain':'a.example','urls':['https://a.example/'],'inspection_depth':'deep_public','batch_id':'b'})
            add_job(c,{'domain':'a.example','urls':['https://a.example/new']})
            entry=json.loads(c.execute('SELECT input FROM jobs').fetchone()[0]);c.close()
        self.assertEqual(entry['inspection_depth'],'deep_public');self.assertEqual(entry['batch_id'],'b')

    def test_deep_list_urls_checked_before_generic_discovered_links(self):
        calls=[]
        def reader(url,*args):
            calls.append(url)
            return {'url':url,'http':200,'html':'<a href="/write">글쓰기</a>'}
        entry={'domain':'a.example','urls':['https://a.example/list'+str(n) for n in range(8)],'inspection_depth':'deep_public'}
        inspect(entry,reader=reader,max_pages=8)
        self.assertEqual(calls,entry['urls'])
