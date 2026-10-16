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

    def test_hidden_cafe24_rows_and_required_icons(self):
        html='<form action="/join"><input name="member_id" required><input type="password"><input name="password_confirm" type="password"><div class="displaynone"><table><tr><th><img alt="필수">직업</th><td><input name="job"></td></tr></table></div><table><tr><th>휴대전화<img class="displaynone" alt="필수"></th><td><input name="mobile2"></td></tr></table></form>'
        fields=measure_html(html,'https://example.org/join','signup')['form']['fields']
        job=next(f for f in fields if f.get('name')=='job'); phone=next(f for f in fields if f.get('name')=='mobile2')
        self.assertTrue(job['hidden']);self.assertIsNone(phone['required'])
    def test_cafe24_nearby_password_length_notice(self):
        html='<form action="/join"><table><tr><th>비밀번호</th><td><input type="password">10자~16자</td></tr><tr><td><input type="password" name="password_confirm"></td></tr></table></form>'
        p=measure_html(html,'https://example.org/join','signup')
        self.assertEqual(p['form']['fields'][0]['text_constraints']['min_length'],10)
        self.assertEqual(p['form']['fields'][0]['text_constraints']['max_length'],16)

if __name__=='__main__': unittest.main()


class AccessDenialRegression(__import__('unittest').TestCase):
    def test_plain_access_denial_is_not_missing_form(self):
        from form_schema import measure_html
        for body in ('접근 불가합니다.', '<html><body>Access Denied</body></html>'):
            result=measure_html(body,'http://example.com/bbs/write.php','write')
            self.assertEqual(result['state'],'access_denied')
            self.assertFalse(result['auto_ready'])
    def test_normal_form_with_denial_in_help_is_not_blocked(self):
        from form_schema import measure_html
        result=measure_html('<form action="write_update.php"><input name="wr_subject"><textarea name="wr_content"></textarea></form><p>접근 불가합니다.</p>','http://example.com/bbs/write.php','write')
        self.assertEqual(result['state'],'measured')


class SiteFailureClassificationTest(__import__('unittest').TestCase):
    def test_database_error_is_not_a_missing_field_and_does_not_retain_user_identifiers(self):
        import json
        from form_schema import measure_html
        result=measure_html("Warning: mysqli_connect(): Access denied for user 'private-user'@'localhost'",'http://approved.example/bbs/write.php','write')
        self.assertEqual(result['state'],'site_database_error')
        self.assertNotIn('private-user',json.dumps(result))

    def test_hosting_denial_and_login_alert_are_distinct(self):
        from form_schema import measure_html
        self.assertEqual(measure_html('<title>Unavailable</title>','http://hosting.webtro.kr/servicedeny.html','write')['state'],'hosting_service_restricted')
        self.assertEqual(measure_html('<title>404</title>','http://hosting.webtro.kr/404.html','write')['state'],'hosting_unavailable')
        page="<script>alert('글을 쓸 권한이 없습니다. 로그인 후 이용해 주세요.');location.replace('./login.php');</script>"
        self.assertEqual(measure_html(page,'https://approved.example/bbs/write.php','write')['state'],'login_required')
        self.assertEqual(measure_html('<a href="login.php">로그인</a>','https://approved.example','write')['state'],'form_unavailable')

    def test_tls_errors_are_explicit_and_never_disable_validation(self):
        from form_schema import connection_error_state
        self.assertEqual(connection_error_state('certificate verify failed: certificate has expired'),'tls_certificate_expired')
        self.assertEqual(connection_error_state('CERTIFICATE_VERIFY_FAILED self signed certificate'),'tls_validation_error')
