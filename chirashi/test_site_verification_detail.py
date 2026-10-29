import unittest
from site_verification_detail import annotate,summarize
from site_verify_worker import inspect,menu_links


class StageDetailTests(unittest.TestCase):
    def page(self,url,stage,html='',state='form_unavailable'):
        return annotate({'url':url,'stage':stage,'http':200,'form_state':state},html)
    def test_password_recovery_captcha_is_unrelated_to_writing(self):
        recovery=self.page('https://a.example/bbs/password_lost.php','entry','<input name="captcha_key">')
        write=self.page('https://a.example/write','write',state='login_required')
        result=summarize([write,recovery])
        self.assertEqual(recovery['human_check']['scope'],'account_recovery_unrelated')
        self.assertEqual(result['state'],'account_verification_pending')
        self.assertTrue(result['flow_profile']['write']['authenticated_recheck_required'])
        self.assertFalse(result['flow_profile']['login']['actual_login_verified'])
        self.assertEqual(menu_links('<a href="/bbs/password_lost.php">비밀번호 찾기</a>','https://a.example/','a.example'),[])
    def test_script_only_signal_does_not_establish_a_required_challenge(self):
        p=self.page('https://a.example/login','login','<script src="https://www.google.com/recaptcha/api.js"></script>',state='measured')
        self.assertEqual(p['human_check']['scope'],'integration_only_unconfirmed')
        self.assertFalse(p['human_verification'])
        self.assertEqual(summarize([p])['state'],'login_profile_observed')
    def test_signup_challenge_does_not_hide_account_pending_state(self):
        signup=self.page('https://a.example/signup','signup','<form><input name="captcha_key"></form>')
        write=self.page('https://a.example/write','write',state='login_required')
        result=summarize([signup,write])
        self.assertEqual(signup['human_check']['scope'],'signup')
        self.assertEqual(result['state'],'account_verification_pending')
        self.assertFalse(result['flow_profile']['signup']['actual_signup_verified'])
    def test_writing_challenge_is_a_hard_hold(self):
        p=self.page('https://a.example/write','write','<form><input name="captcha_key"></form>',state='measured')
        self.assertEqual(summarize([p])['state'],'write_human_required')
    def test_terms_gate_is_not_reported_as_signup_success(self):
        p=self.page('https://a.example/signup','signup','<form><label>약관 동의<input name="agree" type="checkbox"></label></form>')
        s=summarize([p])['flow_profile']['signup']
        self.assertEqual(s['status'],'terms_gate_observed');self.assertFalse(s['terms_accepted']);self.assertFalse(s['actual_signup_verified'])
    def test_existing_item_edit_form_is_not_new_posting_proof(self):
        p=self.page('https://a.example/write.php?bo_table=free&wr_id=7','write',state='measured')
        r=summarize([p]);self.assertNotEqual(r['state'],'write_form_observed')
        self.assertFalse(r['publication_verified'])
    def test_realistic_login_then_write_inspection_never_follows_recovery(self):
        calls=[]
        docs={'https://a.example/':'<a href="/write">글쓰기</a><a href="/login">로그인</a><a href="/signup">회원가입</a><a href="/password_lost.php">비밀번호 찾기</a>',
              'https://a.example/write':'<form action="/login"><input name="mb_id"><input type="password" name="mb_password"></form>',
              'https://a.example/login':'<form action="/login"><input name="mb_id"><input type="password" name="mb_password"></form>',
              'https://a.example/signup':'<form><label>약관 동의<input name="agree" type="checkbox"></label></form>'}
        def reader(url,*args):calls.append(url);return {'url':url,'http':200,'state':'http_response','html':docs[url]}
        r=inspect({'domain':'a.example','urls':['https://a.example/']},reader=reader)
        self.assertEqual(r['state'],'account_verification_pending')
        self.assertEqual(r['flow_profile']['signup']['status'],'terms_gate_observed')
        self.assertFalse(any('password_lost' in u for u in calls))
        self.assertFalse(r['automatic_rejection']);self.assertEqual(r['new_posts_submitted'],0)

    def test_write_redirect_keeps_board_url_but_assigns_challenge_to_login(self):
        p=annotate({'url':'https://a.example/login.php','requested_url':'https://a.example/write.php?bo_table=free','stage':'write','http':200,'form_state':'login_required'},'<form><input name="captcha_key"></form>')
        self.assertEqual(p['human_check']['scope'],'login')
        result=summarize([p]);self.assertEqual(result['state'],'account_verification_pending')
        self.assertEqual(result['flow_profile']['write']['boards'][0]['url'],'https://a.example/write.php?bo_table=free')
    def test_footer_captcha_in_another_form_does_not_block_writing_form(self):
        html='<form><input name="wr_subject"><textarea name="wr_content"></textarea></form><form><input name="captcha_key"></form>'
        p=annotate({'url':'https://a.example/write','stage':'write','http':200,'form_state':'measured','fields':[{'name':'wr_subject','role':'title','type':'text'},{'name':'wr_content','role':'content','type':'textarea'}]},html)
        self.assertEqual(p['human_check']['scope'],'comment_or_embedded_unconfirmed')
        self.assertEqual(summarize([p])['state'],'write_form_observed')
