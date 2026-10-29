import tempfile,json,socket,unittest
from pathlib import Path
from unittest.mock import Mock
from site_reaudit import clean_url,safe_public,snapshot,view_page

class SiteReauditTests(unittest.TestCase):
    def test_get_routes_cannot_transmit_credentials_or_trigger_mutations(self):
        for u in ['https://a.example/logout','https://a.example/write_update.php','https://user:secret@a.example/','file:///etc/passwd','https://a.example:abc/','https://a.example/?act=procBoardInsertDocument']:
            self.assertEqual(clean_url(u),'')
        self.assertEqual(clean_url('https://a.example/bbs/write.php?bo_table=free&token=private'),'https://a.example/bbs/write.php?bo_table=free')
    def test_nonpublic_addresses_are_blocked_before_get(self):
        resolver=lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with self.assertRaises(ValueError):safe_public('https://a.example/',resolver)
    def test_snapshot_covers_rejected_only_and_deduplicates_without_secrets(self):
        with tempfile.TemporaryDirectory() as f:
            p=Path(f)
            (p/'sites.json').write_text(json.dumps([{'id':'s','site_url':'https://a.example/','mb_id':'owner','mb_pass':'secret','permission':True}]))
            (p/'candidates.json').write_text(json.dumps([{'id':'c','url':'https://www.a.example/bbs/board.php?bo_table=free','status':'rejected'}]))
            (p/'rejected_domains.json').write_text(json.dumps({'domains':['a.example','b.example'],'log':[]}))
            rows=snapshot(p);self.assertEqual(len(rows),2);self.assertNotIn('secret',json.dumps(rows));self.assertTrue(all(x['rejected'] for x in rows))
            self.assertEqual(len(next(x for x in rows if x['domain']=='a.example')['records']),2)
    def test_comment_form_is_not_a_write_form(self):
        r={'html':'<form><textarea name="wr_content"></textarea></form>','url':'https://a.example/','http':200,'state':'http_response'}
        self.assertEqual(view_page(r,'entry')['form_state'],'form_unavailable')
    def test_measurement_stores_field_definitions_not_values(self):
        r={'html':'<form action="write_update.php"><input name="wr_subject" required><input type="password" name="wr_password" value="private"><textarea name="wr_content" required></textarea></form>','url':'https://a.example/bbs/write.php','http':200,'state':'http_response'}
        out=view_page(r);self.assertEqual(out['form_state'],'measured');self.assertNotIn('private',json.dumps(out));self.assertNotIn('html',out)

class ReauditStatusTests(unittest.TestCase):
    def test_readonly_progress_api_and_export(self):
        import sqlite3
        from flask import Flask
        from site_reaudit import register,database
        with tempfile.TemporaryDirectory() as f:
            folder=Path(f)/'site_reaudit_20261030';folder.mkdir()
            (folder/'progress.json').write_text(json.dumps({'total':1,'completed':1,'state':'complete'}))
            c=database(folder/'audit.sqlite3')
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',('a.example',0,json.dumps({'records':[]}), 'done',json.dumps({'category':'login_required','requests':1}),'now','now'));c.commit();c.close()
            app=Flask('audit-test');register(app,{'DATA_DIR':Path(f)});client=app.test_client()
            response=client.get('/api/site-reaudit?category=login_required');self.assertEqual(response.status_code,200);self.assertEqual(response.json['filtered_total'],1)
            self.assertEqual(client.get('/api/site-reaudit/export').status_code,200)
            self.assertEqual(client.post('/api/site-reaudit').status_code,405)
    def test_known_nonstandard_port_is_not_a_scan_or_tls_bypass(self):
        self.assertEqual(clean_url('https://a.example:8443/bbs/write.php?bo_table=free'),'https://a.example:8443/bbs/write.php?bo_table=free')

class ReauditSummaryTests(unittest.TestCase):
    def test_contact_form_is_not_a_verified_public_board(self):
        from site_reaudit import refine_result
        row={'category':'write_form_observed_not_publish_verified','pages':[{'stage':'write','url':'https://a.example/bbs/write.php?bo_table=consult'}]}
        self.assertEqual(refine_result(row)['category'],'contact_or_application_form')
        row={'category':'write_form_observed_not_publish_verified','pages':[{'stage':'write','url':'https://a.example/bbs/write.php?bo_table=freeboard'}]}
        self.assertEqual(refine_result(row)['category'],'write_form_observed_not_publish_verified')
    def test_final_summary_preserves_actual_verification_boundary(self):
        from site_reaudit import database,write_summary
        with tempfile.TemporaryDirectory() as f:
            folder=Path(f);(folder/'manifest.json').write_text(json.dumps({'created_at':'2026-10-29T15:00:00+00:00'}))
            c=database(folder/'audit.sqlite3')
            for i,category in enumerate(['write_form_observed_not_publish_verified','login_required']):
                domain=str(i)+'.example';row={'domain':domain,'category':category,'requests':2,'pages':[]}
                inp={'records':[{'source':'registered'}],'rejected':True}
                c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(domain,i,json.dumps(inp),'done',json.dumps(row),'now','now'))
            c.commit();result=write_summary(c,folder);c.close()
            self.assertEqual(result['domains_measured'],2)
            self.assertEqual(result['previously_rejected_domains_measured'],2)
            self.assertEqual(result['write_forms_pending_publication_verification'],1)
            for field in ['paid_api_calls','new_posts_submitted','signup_submissions','login_submissions','permissions_changed']:
                self.assertEqual(result[field],0)
            self.assertTrue((folder/'form-families.json').exists())
            self.assertTrue((folder/'report.txt').exists())

class LoginEvidenceTests(unittest.TestCase):
    def test_login_page_alone_does_not_block_independent_writing_page(self):
        from site_reaudit import refine_result
        pages=[{'stage':'write','form_state':'form_unavailable','http':200},{'stage':'login','form_state':'login_required','login_controls':True,'http':200}]
        row=refine_result({'category':'login_required','pages':pages})
        self.assertEqual(row['category'],'write_form_not_observed')
        pages[0]['form_state']='login_required'
        self.assertEqual(refine_result({'category':'login_required','pages':pages})['category'],'login_required')

class PurposeEvidenceTests(unittest.TestCase):
    def test_private_inquiry_and_review_are_not_public_ad_boards(self):
        from site_reaudit import refine_result
        for title,expected in [('견적문의 글쓰기','contact_or_application_form'),('이용후기 글쓰기','purpose_specific_board'),('자유게시판 글쓰기','write_form_observed_not_publish_verified')]:
            row={'category':'write_form_observed_not_publish_verified','pages':[{'stage':'write','url':'https://a.example/bbs/write.php?bo_table=free','title':title,'form_state':'measured'}]}
            self.assertEqual(refine_result(row)['category'],expected)
            self.assertFalse(row['board_purpose_hint']['permission_decision'])
    def test_article_embedded_form_is_not_a_verified_new_posting_route(self):
        from site_reaudit import refine_result
        row={'category':'write_form_observed_not_publish_verified','pages':[{'stage':'entry','url':'https://a.example/post/12','title':'기존 글','form_state':'measured'}]}
        self.assertEqual(refine_result(row)['category'],'embedded_form_requires_review')

class LoginSchemaTests(unittest.TestCase):
    def test_login_fields_are_learned_without_field_values(self):
        r={'html':'<form action="/login"><input name="mb_id" required value="private-id"><input name="mb_password" type="password" required value="private-password"></form>','url':'https://a.example/login','http':200,'state':'http_response'}
        out=view_page(r,'login')
        self.assertEqual(out['form_state'],'measured')
        self.assertEqual({f['role'] for f in out['fields']},{'id','password'})
        self.assertNotIn('private-id',json.dumps(out))
        self.assertNotIn('private-password',json.dumps(out))
    def test_signup_form_is_not_a_login_recipe(self):
        r={'html':'<form action="/signup"><input name="mb_id"><input name="mb_password" type="password"><input name="mb_password_re" type="password"></form>','url':'https://a.example/signup','http':200,'state':'http_response'}
        self.assertNotEqual(view_page(r,'login')['form_state'],'measured')

class MeasuredCustomSkinTests(unittest.TestCase):
    def test_donation_gender_and_address_are_not_post_title_and_body(self):
        from form_schema import measure_html
        html='<form><label>성별<input type="radio" name="wr_subject"></label><label>주소<input name="wr_content" required></label></form>'
        self.assertEqual(measure_html(html,'https://a.example/write','write')['state'],'form_unavailable')
    def test_member_code_is_a_restriction_not_an_ordinary_post_password(self):
        from form_schema import measure_html
        html='<form><label>조합원 코드<input name="nado_code" type="password" required></label><input name="wr_subject" required><textarea name="wr_content" required></textarea></form>'
        result=measure_html(html,'https://a.example/write','write')
        self.assertIn('access_code',{f['role'] for f in result['form']['fields']})
        self.assertFalse(result['auto_ready'])
    def test_legacy_spam_control_is_a_human_challenge_not_an_auto_fill_field(self):
        from form_schema import measure_html,human_challenge_present
        html='<form><input name="wr_subject" required><textarea name="wr_content" required></textarea><table><tr><td>SPAM 방지</td><td><input type="hidden" name="spams"><input name="sFullString1" required></td></tr></table></form>'
        self.assertTrue(human_challenge_present(html))
        self.assertFalse(measure_html(html,'https://a.example/write','write')['auto_ready'])
        self.assertFalse(human_challenge_present('<p>SPAM 방지 안내</p><input type="hidden" name="spams">'))

class LegacyEvidenceReclassificationTests(unittest.TestCase):
    def test_saved_measurements_can_be_refined_without_more_site_requests(self):
        from site_reaudit import refine_result
        for field,expected in [({'label':'조합원 코드 필수','type':'password','role':'password'},'membership_code_required'),({'label':'SPAM 방지','type':'text','role':'unknown'},'human_verification_required'),({'label':'성별','type':'radio','role':'title'},'contact_or_application_form')]:
            row={'category':'write_form_observed_not_publish_verified','pages':[{'stage':'write','form_state':'measured','url':'https://a.example/bbs/write.php?bo_table=free','fields':[field]}]}
            self.assertEqual(refine_result(row)['category'],expected)

class BareHtmlAttributeTests(unittest.TestCase):
    def test_empty_style_and_class_attributes_do_not_abort_a_site_measurement(self):
        from form_schema import human_challenge_present
        self.assertFalse(human_challenge_present('<input style class type name autocomplete placeholder>'))
        out=view_page({'html':'<form style class><input name="wr_subject" style class required><textarea name="wr_content" required></textarea></form>','url':'https://a.example/write','http':200,'state':'http_response'})
        self.assertEqual(out['form_state'],'measured')
