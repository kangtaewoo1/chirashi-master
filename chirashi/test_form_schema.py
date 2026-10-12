import unittest
from form_schema import measure_html, changes_between

class FormSchemaTest(unittest.TestCase):
    def test_custom_labels_split_phone_and_select(self):
        html='''<form action="/join"><label for="ident">아이디 필수</label><input id="ident" minlength="5" required>
        <label>비밀번호<input name="password" type="password"></label><input type="password" name="password_confirm">
        <label for="mobile1">휴대전화 필수</label><select id="mobile1"><option value="010">010</option></select>
        <input name="mobile2"><input name="mobile3"></form>'''
        p=measure_html(html,'https://example.org/member/join.html','signup')
        fields=p['form']['fields']
        self.assertEqual([f['role'] for f in fields],['id','password','password_confirm','phone','phone','phone'])
        self.assertTrue(fields[0]['required']); self.assertEqual(fields[3]['options'][0]['value'],'010')
        self.assertFalse(p['auto_ready']) # unidentified requirement stays unknown
    def test_required_options_and_action_changes_detected(self):
        base='<form action="/write"><input name="wr_subject"><textarea name="wr_content"></textarea><select name="category"><option value="a">A</option></select></form>'
        old=measure_html(base,'https://example.org/write','write')
        new=measure_html(base.replace('name="wr_subject"','name="wr_subject" required').replace('value="a"','value="b"'),'https://example.org/write','write')
        self.assertNotEqual(old['fingerprint'],new['fingerprint'])
        delta=changes_between(old,new)
        self.assertIn('required',delta['changed']['[name="wr_subject"]'])
        self.assertIn('options',delta['changed']['[name="category"]'])
    def test_values_do_not_leak_or_change_fingerprint(self):
        html='<form action="/write"><input name="csrf" type="hidden" value="SECRET"><input name="wr_subject" value="PRIVATE"><textarea name="wr_content">PRIVATE</textarea></form>'
        a=measure_html(html,'https://example.org/write','write')
        b=measure_html(html.replace('SECRET','OTHER').replace('PRIVATE','OTHER'),'https://example.org/write','write')
        self.assertEqual(a['fingerprint'],b['fingerprint'])
        self.assertNotIn('SECRET',str(a)); self.assertNotIn('PRIVATE',str(a))
    def test_login_is_not_signup(self):
        p=measure_html('<form action="/login"><input name="member_id"><input type="password"></form>','https://example.org/login','signup')
        self.assertEqual(p['state'],'form_unavailable')
    def test_agreement_and_challenge_are_not_forms(self):
        self.assertEqual(measure_html('<form action="/join">이용약관 동의<input name="agree" type="checkbox"></form>','https://example.org/join','signup')['state'],'agreement_required')
        self.assertEqual(measure_html('<div class="cf-turnstile"></div>','https://example.org/join','signup')['state'],'human_verification_required')
    def test_odd_id_selector_and_confirm(self):
        p=measure_html('<form action="/join"><input id="pw:one" type="password"><input type="password" name="user_passwd_re"></form>','https://example.org/join','signup')
        self.assertEqual(p['form']['fields'][0]['selector'],'[id="pw:one"]')
        self.assertEqual(p['form']['fields'][1]['role'],'password_confirm')

    def test_transient_url_tokens_are_redacted(self):
        html='<form action="/write?token=SECRET&bo_table=free"><input name="wr_subject"><textarea name="wr_content"></textarea></form>'
        p=measure_html(html,'https://example.org/write?auth=PRIVATE&bo_table=free','write')
        self.assertNotIn('SECRET',str(p));self.assertNotIn('PRIVATE',str(p))
        self.assertIn('bo_table=free',p['form']['action'])
    def test_title_only_is_not_complete_write_form(self):
        self.assertEqual(measure_html('<form><input name="wr_subject"></form>','https://example.org/write','write')['state'],'form_unavailable')

if __name__=='__main__': unittest.main()
