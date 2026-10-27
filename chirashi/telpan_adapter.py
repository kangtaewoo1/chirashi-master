"""Telpan's public login/write UI only. No database or browser-session copying."""
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN = 'https://www.telpan.co.kr'
_lock = threading.Lock()
_test_lock = threading.Lock()
_signup_lock = threading.Lock()
_knowledge_lock = threading.RLock()

# Site recipe: reusable field roles and routes, never account values or passwords.
FLOW = {
    'origin': ORIGIN, 'adapter': 'telpan',
    'routes': {'signup': ORIGIN+'/signup', 'signup_submit': ORIGIN+'/api/signup',
               'login': ORIGIN+'/signup', 'write': ORIGIN+'/board/ads/write'},
    'fields': [
        {'name':'nickname', 'role':'account_nickname', 'type':'text', 'required':True, 'selector':'input[placeholder="닉네임 입력"]'},
        {'name':'password', 'role':'account_password', 'type':'password', 'required':True, 'selector':'input[placeholder="비밀번호 입력"]'},
        {'name':'email', 'role':'owner_email', 'type':'email', 'required':True, 'selector':'input[type="email"]'},
        {'name':'snsId', 'role':'contact_sns', 'type':'text', 'required':True, 'selector':'input[placeholder="@telegram 또는 카카오톡 ID"]'}],
    'limits': {'title_utf16':40, 'body_utf16':500, 'signup_attempts_per_request':1,
               'retry_uncertain_submission':False, 'ip_restriction_bypass':False}
}


def remember_flow(ctx, event=None):
    from form_schema import merge_flow_knowledge
    path = Path(ctx['DATA_DIR']) / 'site_flow_knowledge.json'
    lock = ctx.get('_json_lock', lambda p:_knowledge_lock)(path)
    with lock:
        knowledge = ctx['load_json'](path, {})
        knowledge[ORIGIN] = merge_flow_knowledge(knowledge.get(ORIGIN), FLOW, event)
        private_write(path, knowledge)
        return knowledge[ORIGIN]


def learned_signup_profile():
    """Bridge the custom SPA adapter into the app's existing signup-learning interface."""
    from form_schema import merge_flow_knowledge
    known = merge_flow_knowledge({}, FLOW)
    fields = [dict(f, hidden=False) for f in known['fields']]
    return {'signup_url':FLOW['routes']['signup'], 'form_url':FLOW['routes']['signup'],
            'form_action':FLOW['routes']['signup_submit'], 'form_method':'POST', 'fields':fields,
            'rules':{}, 'rule_sources':{}, 'captcha':False, 'email_verification_required':None,
            'fingerprint':known['fingerprint'], 'measured_at':datetime.now().isoformat(timespec='seconds'),
            'schema':{'schema_version':2, 'state':'adapter_documented', 'adapter':'telpan'},
            'evidence_level':'observed_ui_and_public_client_code',
            'live_signup_verified':False, 'missing_profile_roles':[f['role'] for f in fields]}


def resume_known_account(site, ctx, submit=True):
    """Generic pipeline dispatches here instead of treating Telpan as a Gnuboard."""
    known = remember_flow(ctx)
    if not submit:
        return False, '텔판 가입 경로 학습 완료 — 가입·로그인·글 제출은 수행하지 않았습니다.'
    path = Path(ctx['DATA_DIR']) / 'telpan_login_private.json'
    if not path.exists():
        roles = [f['role'] for f in known['fields'] if f['required']]
        site.update(signup_status='profile_required', signup_missing_roles=roles,
                    signup_url=FLOW['routes']['signup'])
        return False, '텔판 가입 프로필 미등록 — 학습된 필수 항목: 닉네임·비밀번호·본인 이메일·SNS 연락처'
    try:
        verify_login(ctx)
        cred = json.loads(path.read_text(encoding='utf-8'))
        site.update(mb_id=cred['nickname'], signup_status='complete', login_required=True)
        remember_flow(ctx, {'stage':'login', 'state':'verified'})
        return True, '저장된 텔판 계정의 서버 로그인·글쓰기 화면 재확인 완료 — 실제 발행은 별도 검증'
    except Exception:
        remember_flow(ctx, {'stage':'login', 'state':'review'})
        return False, '텔판 기존 계정 로그인 확인 필요 — 새 계정 반복 생성 중지'


def signup_profile(data):
    """Only fields used by Telpan's own signup form; never invent an identity."""
    names = [f['name'] for f in FLOW['fields'] if f['name'] != 'password']
    profile = {key: str(data.get(key) or '').strip() for key in names}
    profile['password'] = str(data.get('password') or '')
    if (not profile['nickname'] or len(profile['nickname']) > 100
            or not profile['password'] or len(profile['password']) > 256
            or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', profile['email'])
            or not profile['snsId'] or len(profile['snsId']) > 200):
        raise ValueError('닉네임·새 비밀번호·본인 이메일·SNS 아이디가 모두 필요합니다.')
    return profile


def signup_once(profile, post=None):
    """The same public endpoint as the official form. No retries or IP switching."""
    import requests
    try:
        r = (post or requests.post)(FLOW['routes']['signup_submit'], json=profile,
                                   timeout=25, allow_redirects=False)
        if 300 <= r.status_code < 400:
            return 'unknown', '가입 응답이 다른 주소로 이동했습니다. 재가입 요청을 중지했습니다.'
        data = r.json()
        if r.status_code == 200 and data.get('success') is True:
            return 'created', '텔판이 회원가입 성공을 확인했습니다. 서버 로그인을 확인합니다.'
        # Do not echo arbitrary remote messages: they may contain submitted private fields.
        message = str(data.get('message') or '')
        if any(x in message for x in ('IP', '아이피', '1계정')):
            return 'restricted', '텔판의 IP당 가입 제한으로 중지했습니다. IP를 바꾸거나 우회하지 않습니다.'
        if any(x in message for x in ('이미', '중복', '존재')):
            return 'duplicate', '이미 사용 중인 가입 정보입니다. 추가 가입 요청을 중지했습니다.'
        if any(x in message.lower() for x in ('captcha', '인증', '보안문자')):
            return 'human_required', '텔판에서 본인 인증 또는 보안 확인을 요구하여 중지했습니다.'
        if r.status_code >= 500:
            return 'unknown', '텔판 응답을 확인하지 못했습니다. 중복 가입 방지를 위해 재요청하지 않습니다.'
        return 'rejected', '텔판이 가입 요청을 승인하지 않았습니다. 가입 조건을 확인해주세요.'
    except Exception:
        return 'unknown', '가입 결과 확인이 필요합니다. 중복 가입 방지를 위해 재요청하지 않습니다.'


def verify_login(ctx):
    """Prove ordinary login and write-form access without posting or copying sessions."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    cred = json.loads((Path(ctx['DATA_DIR']) / 'telpan_login_private.json').read_text(encoding='utf-8'))
    d = None
    try:
        d = new_driver(ctx); wait = WebDriverWait(d, 25)
        d.get(ORIGIN + '/signup')
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="닉네임 입력"]')))
        if urlsplit(d.current_url).netloc != 'www.telpan.co.kr': raise ValueError('origin')
        d.find_element(By.CSS_SELECTOR, 'input[placeholder="닉네임 입력"]').send_keys(cred['nickname'])
        d.find_element(By.CSS_SELECTOR, 'input[placeholder="비밀번호 입력"]').send_keys(ctx['_decrypt_password'](cred['password_enc']))
        d.find_element(By.XPATH, '//button[@type="submit" and normalize-space()="로그인"]').click()
        wait.until(lambda x: urlsplit(x.current_url).path == '/')
        wait.until(lambda x: any(cred['nickname'] in e.text for e in x.find_elements(By.CSS_SELECTOR, '.user-profile')))
        d.get(ORIGIN + '/board/ads/write')
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="제목을 입력하세요"]')))
        if d.current_url != ORIGIN + '/board/ads/write': raise ValueError('write permission')
        if any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR,
                'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],.cf-turnstile,input[name="captcha"]')):
            raise ValueError('human required')
        return {'login_verified': True, 'write_form_visible': True, 'submitted_posts': 0}
    finally:
        if d:
            try: d.quit()
            except Exception: pass


def valid_post_url(url):
    p = urlsplit(str(url or ''))
    return bool(p.scheme == 'https' and p.netloc == 'www.telpan.co.kr'
                and re.fullmatch(r'/board/ads/[A-Za-z0-9]{20}', p.path)
                and not p.query and not p.fragment)


def block(site, bootstrap=False):
    if site.get('site_url') != ORIGIN or site.get('platform') != 'telpan':
        return 'telpan_invalid_destination'
    if (not site.get('permission') or site.get('registration_source') != 'manual_admin'
            or not site.get('telpan_enabled') or site.get('status') == 'rejected'):
        return 'telpan_not_enabled'
    if site.get('maintenance_hold') or site.get('manual_review_only'):
        return 'maintenance_hold'
    if site.get('has_captcha') or site.get('secret_forced') or site.get('write_access_state') in (
            'admin_only', 'human_verification_required'):
        return 'human_verification_required'
    previous = str(site.get('last_fail_reason') or '')
    if '결과 검증 대기' in previous and '재제출 금지' in previous:
        return 'unattended_result_review'
    if '사람 확인 필요' in previous:
        return 'human_verification_required'
    state = (site.get('unattended_audit') or {}).get('state')
    if state not in (None, '', 'ready'):
        return 'unattended_' + str(state)
    if not site.get('telpan_login_saved'):
        return 'credentials_required'
    if not bootstrap:
        r = site.get('unattended_result') or {}
        if not (site.get('write_test_status') == 'passed' and r.get('state') == 'verified'
                and r.get('transport') == 'telpan_server_browser'
                and valid_post_url(r.get('url')) and r.get('url') == site.get('verified_post_url')
                and r.get('title_match') is True and r.get('body_match') is True):
            return 'telpan_server_verification_required'
    return ''


def content(title, body_html):
    from form_schema import Parser
    p = Parser(); p.feed(str(body_html)); p.close()
    body = p.root.text().strip()
    title = str(title).strip()
    # Javascript maxlength counts UTF-16 code units. Never silently truncate a post.
    units = lambda s: len(s.encode('utf-16-le')) // 2
    if not title or not body or units(title) > FLOW['limits']['title_utf16'] or units(body) > FLOW['limits']['body_utf16']:
        raise ValueError('텔판은 제목 40자·본문 500자 이내의 글이 필요합니다. 원문을 자르지 않고 보류했습니다.')
    return title, body


def private_write(path, value):
    import os
    path = Path(path)
    tmp = path.with_name('.' + path.name + '.' + secrets.token_hex(6))
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists(): tmp.unlink()


def verify_public(url, title, body, get=None):
    import requests
    from form_schema import Parser
    if not valid_post_url(url): raise ValueError('invalid post URL')
    r = (get or requests.get)(url, timeout=25, allow_redirects=False)
    if r.status_code != 200 or not valid_post_url(r.url): raise ValueError('public read failed')
    r.encoding = 'utf-8'
    p = Parser(); p.feed(r.text); p.close()
    titles = [n.text() for n in p.nodes if n.tag == 'h1' and 'post-title' in n.attrs.get('class', '').split()]
    bodies = [n.text() for n in p.nodes if 'post-content' in n.attrs.get('class', '').split()]
    norm = lambda s: ' '.join(s.split())
    if len(titles) != 1 or len(bodies) != 1 or norm(titles[0]) != norm(title) or norm(bodies[0]) != norm(body):
        raise ValueError('public title/body mismatch')
    return {'state': 'verified', 'transport': 'telpan_server_browser', 'url': url,
            'checked_at': datetime.now().isoformat(timespec='seconds'), 'http': 200,
            'public_read_without_authentication': True, 'title_match': True, 'body_match': True,
            'body_sha256': hashlib.sha256(body.encode()).hexdigest()}


def new_driver(ctx):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    import os
    options = webdriver.ChromeOptions()
    for arg in ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--lang=ko-KR',
                '--window-size=1280,1000'):
        options.add_argument(arg)
    options.set_capability('acceptInsecureCerts', False)
    options.set_capability('unhandledPromptBehavior', 'ignore')
    if os.environ.get('CHROME_BIN'): options.binary_location = os.environ['CHROME_BIN']
    d = webdriver.Chrome(service=Service(ctx['_get_driver_path']()), options=options)
    d.set_page_load_timeout(35)
    return d


def publish(site, title, body_html, ctx, bootstrap=False):
    """One submit, with a durable fence before clicking. Never retry uncertain writes."""
    if not _lock.acquire(blocking=False): return False, '무인 발행 보류 — 텔판 작업 진행 중'
    d = None; submitted = False
    journal = Path(ctx['DATA_DIR']) / 'telpan_submission.json'
    try:
        site = ctx['_fresh_site'](site)
        reason = block(site, bootstrap)
        if reason: return False, '무인 발행 보류 — ' + reason
        if not ctx['under_daily_limit'](site, ctx['load_config']()):
            return False, '무인 발행 보류 — 사이트 일일 한도'
        if not ctx['under_min_interval'](site)[0]:
            return False, '무인 발행 보류 — 사이트 최소 발행 간격'
        if journal.exists() and json.loads(journal.read_text(encoding='utf-8')).get('state') in ('submitting', 'uncertain'):
            return False, '결과 검증 대기 — 이전 텔판 제출 확인 필요, 재제출 금지'
        title, body = content(title, body_html)
        cred = json.loads((Path(ctx['DATA_DIR']) / 'telpan_login_private.json').read_text(encoding='utf-8'))
        nickname = cred['nickname']; password = ctx['_decrypt_password'](cred['password_enc'])
        if not nickname or not password or nickname != site.get('mb_id'):
            return False, '무인 발행 보류 — 로그인 정보 필요'
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import NoAlertPresentException
        d = new_driver(ctx)
        wait = WebDriverWait(d, 25)

        def check_page():
            if urlsplit(d.current_url).netloc != 'www.telpan.co.kr':
                raise ValueError('unexpected origin')
            text = d.find_element(By.TAG_NAME, 'body').text
            if any(x in text for x in ('보안문자', '자동등록방지', '접근이 차단', '권한이 없습니다')):
                ctx['set_site_flag'](site['id'], unattended_audit={'state': 'manual_review'})
                raise ValueError('access or human verification required')
            if any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR,
                    'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],.cf-turnstile,input[name="captcha"]')):
                ctx['set_site_flag'](site['id'], has_captcha=True, write_access_state='human_verification_required')
                raise ValueError('human verification required')

        d.get(ORIGIN + '/signup')
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="닉네임 입력"]')))
        check_page()
        d.find_element(By.CSS_SELECTOR, 'input[placeholder="닉네임 입력"]').send_keys(nickname)
        d.find_element(By.CSS_SELECTOR, 'input[placeholder="비밀번호 입력"]').send_keys(password)
        wait.until(EC.element_to_be_clickable((By.XPATH, '//button[@type="submit" and normalize-space()="로그인"]'))).click()
        # Only visible successful-login navigation establishes authentication.
        wait.until(lambda x: urlsplit(x.current_url).path != '/signup')
        d.get(ORIGIN + '/board/ads/write')
        title_el = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[placeholder="제목을 입력하세요"]')))
        check_page()
        body_el = d.find_element(By.CSS_SELECTOR, 'textarea[placeholder="내용을 입력하세요"]')
        if title_el.get_attribute('maxlength') != '40' or body_el.get_attribute('maxlength') != '500':
            raise ValueError('write form changed')
        checks = d.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
        if len(checks) != 1 or checks[0].is_selected(): raise ValueError('unexpected paid option')
        title_el.send_keys(title); body_el.send_keys(body)
        if title_el.get_attribute('value') != title or body_el.get_attribute('value') != body:
            raise ValueError('input mismatch')
        check_page()
        latest = ctx['_fresh_site'](site)
        if (block(latest, bootstrap) or not ctx['under_daily_limit'](latest, ctx['load_config']())
                or not ctx['under_min_interval'](latest)[0]):
            return False, '무인 발행 보류 — 제출 직전 권한 또는 발행 한도 변경'
        record = {'state': 'submitting', 'title': title, 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
                  'started_at': datetime.now().isoformat(timespec='seconds'), 'submissions': 1}
        private_write(journal, record)
        submitted = True
        d.find_element(By.XPATH, '//button[@type="submit" and normalize-space()="등록"]').click()
        alert = wait.until(EC.alert_is_present())
        alert_text = alert.text
        # Never accept a new agreement, CAPTCHA, payment, or other unexpected prompt.
        if '게시글이 등록되었습니다' not in alert_text:
            alert.dismiss(); raise ValueError('unexpected submit response')
        alert.accept()
        wait.until(lambda x: urlsplit(x.current_url).path == '/board/ads')
        links = wait.until(lambda x: [a for a in x.find_elements(By.CSS_SELECTOR, 'a[href^="/board/ads/"]')
                                    if a.text.strip() == title and valid_post_url(a.get_attribute('href'))])
        urls = list(dict.fromkeys(a.get_attribute('href') for a in links))
        if len(urls) != 1 or urls[0] == site.get('verified_post_url'):
            raise ValueError('new post not uniquely identified')
        receipt = verify_public(urls[0], title, body)
        remember_flow(ctx, {'stage':'server_post', 'state':'verified', 'receipt':receipt})
        private_write(journal, dict(record, state='verified', receipt=receipt))
        ctx['set_site_flag'](site['id'], unattended_result=receipt,
                             last_publish_transport='telpan_server_browser')
        return True, urls[0]
    except Exception:
        # Never include Selenium errors, pages, passwords, or account records in logs/responses.
        if submitted:
            private_write(journal, dict(record, state='uncertain'))
            ctx['set_site_flag'](site['id'], unattended_audit={'state': 'result_review'})
            return False, '결과 검증 대기 — 텔판 제출 후 공개 조회 확인 필요, 재제출 금지'
        return False, '무인 발행 보류 — 텔판 로그인·권한·보안 확인 또는 입력 형식 확인 필요'
    finally:
        if d:
            try: d.quit()
            except Exception: pass
        _lock.release()


def register(app, ctx):
    from flask import request, jsonify, render_template_string, session
    path = Path(ctx['DATA_DIR']) / 'telpan_login_private.json'
    sid = 'telpan-ads'
    remember_flow(ctx)

    def current():
        return next((s for s in ctx['load_sites']() if s.get('id') == sid), {})

    def csrf_ok():
        return bool(session.get('telpan_csrf') and secrets.compare_digest(
            str(request.headers.get('X-Telpan-CSRF', '')), session['telpan_csrf']))

    @app.route('/api/telpan/knowledge')
    def telpan_knowledge():
        return jsonify(ok=True, knowledge=remember_flow(ctx))

    @app.route('/site-knowledge')
    def site_knowledge():
        remember_flow(ctx)
        knowledge=ctx['load_json'](Path(ctx['DATA_DIR'])/'site_flow_knowledge.json',{})
        return render_template_string(KNOWLEDGE_PAGE, rows=list(knowledge.values()))

    @app.route('/telpan')
    def telpan_setup():
        session.setdefault('telpan_csrf', secrets.token_urlsafe(32))
        return render_template_string(PAGE, csrf=session['telpan_csrf'])

    @app.route('/api/telpan/status')
    def telpan_status():
        site = current()
        signup = ctx['load_json'](Path(ctx['DATA_DIR']) / 'telpan_signup_private.json', {})
        return jsonify(ok=True, login_saved=path.exists(), enabled=bool(site.get('telpan_enabled')),
                       ready=not bool(block(site)), reason=block(site),
                       test_status=site.get('write_test_status', ''),
                       receipt=site.get('unattended_result') or {}, failure=site.get('last_fail_reason', ''),
                       signup={k: signup[k] for k in ('state', 'message', 'login_verified', 'write_form_visible') if k in signup})

    @app.route('/api/telpan/signup', methods=['POST'])
    def telpan_signup():
        if not csrf_ok(): return jsonify(ok=False, error='화면을 새로 열어주세요'), 403
        data = request.get_json(silent=True) or {}
        if data.get('signup_confirmed') is not True:
            return jsonify(ok=False, error='텔판 가입 요청 및 계정 저장을 확인해주세요'), 400
        try: profile = signup_profile(data)
        except ValueError as exc: return jsonify(ok=False, error=str(exc)), 400
        if not _signup_lock.acquire(blocking=False):
            return jsonify(ok=False, error='가입 결과 확인 중입니다. 다시 제출하지 마세요.'), 409
        journal = Path(ctx['DATA_DIR']) / 'telpan_signup_private.json'
        try:
            prior = ctx['load_json'](journal, {})
            if prior.get('state') in ('requesting', 'unknown', 'created', 'login_verified', 'login_review'):
                return jsonify(ok=False, error='이전 가입 요청이 있습니다. 중복 생성 대신 기존 계정을 확인해주세요.'), 409
            if _lock.locked() or _test_lock.locked():
                return jsonify(ok=False, error='기존 텔판 작업 완료 후 진행해주세요'), 409
            # A private pending credential survives an ambiguous signup response. Do not activate it yet.
            private_write(Path(ctx['DATA_DIR']) / 'telpan_signup_pending_private.json', {
                'nickname': profile['nickname'], 'password_enc': ctx['_encrypt_password'](profile['password'])})
            record = {'state': 'requesting', 'message': '텔판에 정상 가입 요청을 1회 보내는 중입니다.',
                      'requested_at': datetime.now().isoformat(timespec='seconds'), 'requests': 1}
            private_write(journal, record)
            state, message = signup_once(profile)
            remember_flow(ctx, {'stage':'signup', 'state':state})
            record.update(state=state, message=message); private_write(journal, record)
            if state != 'created': return jsonify(ok=False, state=state, error=message), 409
            # Same request context as the existing secure-save route; permission holds are preserved.
            saved = telpan_save()
            if isinstance(saved, tuple):
                record.update(state='login_review', message='가입은 완료됐지만 서버 계정 저장을 확인해야 합니다.')
                private_write(journal, record)
                return jsonify(ok=False, error=record['message']), 409

            def login_check():
                try:
                    proof = verify_login(ctx)
                    remember_flow(ctx, {'stage':'login', 'state':'verified'})
                    record.update(state='login_verified', message='신규 가입·서버 로그인·글쓰기 화면 확인 완료. 실제 발행 검증은 아직 대기 중입니다.', **proof)
                except Exception:
                    remember_flow(ctx, {'stage':'login', 'state':'review'})
                    record.update(state='login_review', message='가입은 완료됐지만 서버 로그인 확인이 필요합니다. 새 계정을 반복 생성하지 않습니다.')
                finally:
                    private_write(journal, record)
                    _test_lock.release()
            if not _test_lock.acquire(blocking=False):
                record.update(state='login_review', message='가입은 완료됐습니다. 진행 중인 서버 작업이 끝난 뒤 로그인 확인이 필요합니다.')
                private_write(journal, record)
                return jsonify(ok=True, message=record['message'])
            threading.Thread(target=login_check, name='TELPAN_LOGIN', daemon=True).start()
            return jsonify(ok=True, message=message)
        finally:
            _signup_lock.release()

    @app.route('/api/telpan/setup', methods=['POST'])
    def telpan_save():
        if not csrf_ok(): return jsonify(ok=False, error='화면을 새로 열어주세요'), 403
        data = request.get_json(silent=True) or {}
        nickname = str(data.get('nickname') or '').strip()
        password = str(data.get('password') or '')
        if not nickname or not password or len(nickname) > 100 or len(password) > 256:
            return jsonify(ok=False, error='텔판의 닉네임과 기존 비밀번호를 입력해주세요'), 400
        if _lock.locked() or _test_lock.locked(): return jsonify(ok=False, error='텔판 검증 작업 완료 후 저장해주세요'), 409
        private_write(path, {'nickname': nickname, 'password_enc': ctx['_encrypt_password'](password)})
        with ctx['POST_LOCK']:
            sites = ctx['load_sites'](); site = next((s for s in sites if s.get('id') == sid), None)
            if site is None:
                site = {'id': sid, 'site_url': ORIGIN, 'platform': 'telpan', 'bo_table': 'ads', 'name': '텔판 자유홍보',
                        'permission': True, 'registration_source': 'manual_admin',
                        'permission_note': '사용자 요청 및 자유홍보 게시판 확인',
                        'daily_limit': 2, 'min_interval_minutes': 1, 'status': 'idle',
                        'write_test_status': 'pending', 'verified_post_url': ''}
                # Carry over the independently verified browser post only for rate accounting.
                # It is deliberately NOT server-verification evidence.
                campaign = ctx['load_json'](Path(ctx['DATA_DIR']) / 'brightdata_discovery_campaign.json', {})
                prior = [r for r in campaign.get('verified_published_sites', []) if r.get('domain') == 'telpan.co.kr']
                if prior:
                    from zoneinfo import ZoneInfo
                    dates = []
                    for r in prior:
                        try: dates.append(datetime.fromisoformat(r['verified_at']).astimezone(ZoneInfo('Asia/Seoul')))
                        except (KeyError, ValueError): pass
                    if dates:
                        last = max(dates)
                        site.update(last_post_at=last.strftime('%Y-%m-%d %H:%M:%S'),
                                    posted_date=last.strftime('%Y-%m-%d'),
                                    posted_today=sum(d.date() == last.date() for d in dates))
                sites.append(site)
            # Existing holds, intervals, receipts and counts are preserved.
            site.update(telpan_enabled=True, telpan_login_saved=True, mb_id=nickname, login_required=True)
            site.update(write_test_status='pending', verified_post_url='')
            site.pop('unattended_result', None)
            ctx['save_sites'](sites)
        return jsonify(ok=True, message='계정을 암호화해 저장했습니다. 서버 검증 전에는 자동발행 대기 상태입니다.')

    @app.route('/api/telpan/test', methods=['POST'])
    def telpan_test():
        if not csrf_ok(): return jsonify(ok=False, error='화면을 새로 열어주세요'), 403
        site = current()
        reason = block(site, bootstrap=True)
        if reason: return jsonify(ok=False, error='발행 보류: ' + reason), 409
        if not ctx['under_daily_limit'](site, ctx['load_config']()):
            return jsonify(ok=False, error='오늘 발행 한도에 도달했습니다'), 409
        allowed, remain = ctx['under_min_interval'](site)
        if not allowed: return jsonify(ok=False, error=f'기존 발행 간격 유지: 약 {(remain+59)//60}분 후 가능'), 409
        if not _test_lock.acquire(blocking=False): return jsonify(ok=False, error='텔판 검증 진행 중'), 409

        def run():
            try:
                code = 'TP-SERVER-' + secrets.token_hex(6).upper()
                title = '[서버 테스트] 등록·조회 확인 ' + code[-6:]
                body = 'google.twseo.kr 서버의 게시판 연결 검증용 테스트 글입니다.\n제목과 본문이 공개 글 주소에서 유지되는지 확인합니다.\n검증 코드: ' + code
                ok, result = publish(site, title, body, ctx, bootstrap=True)
                if ok:
                    ctx['finalize_post'](site, True)
                    ctx['set_site_flag'](sid, status='done', write_test_status='passed', verified_post_url=result,
                                         last_fail_reason='', verified_at=datetime.now().isoformat(timespec='seconds'))
                else:
                    ctx['set_site_flag'](sid, write_test_status='failed', last_fail_reason=result)
            finally:
                _test_lock.release()
        threading.Thread(target=run, name='TELPAN_TEST', daemon=True).start()
        return jsonify(ok=True, message='서버에서 무료 테스트 글 1건의 등록·공개 조회 검증을 시작했습니다.')


PAGE = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>텔판 자동발행 연결</title><style>body{font:18px/1.7 sans-serif;background:#101827;color:#eef3fb;max-width:680px;margin:40px auto;padding:20px}input,button{font:inherit;box-sizing:border-box;width:100%;padding:12px;margin:8px 0;border-radius:8px}button{background:#4085ff;color:white;border:0;cursor:pointer}a{color:#8bbaff}pre{white-space:pre-wrap;overflow-wrap:anywhere}label{display:block}</style>
<h1>텔판 가입 · 서버 연결</h1><p>아래 정보를 직접 입력하고 가입 요청을 누르면, 서버가 텔판 가입과 로그인을 확인합니다. 비밀번호는 암호화해 보관하며 다시 표시하지 않습니다.</p>
<p><a href="/site-knowledge">사이트별로 학습한 가입·로그인 방식과 실제 검증 결과 보기</a></p>
<form id="signupAccount"><h2>새 계정으로 가입</h2>
<label>새 닉네임<input id="newNickname" autocomplete="off" required maxlength="100"></label>
<label>새 비밀번호<input id="newPassword" type="password" autocomplete="new-password" required maxlength="256"></label>
<label>본인 이메일<input id="newEmail" type="email" autocomplete="email" required></label>
<label>텔레그램 또는 카카오톡 ID<input id="newSns" autocomplete="off" required maxlength="200"></label>
<label><input id="signupConfirmed" type="checkbox" required style="width:auto">입력한 정보로 텔판에 가입을 요청하고, 계정을 이 서버에 암호화해 저장합니다.</label>
<button id="signupButton">텔판 가입 요청 · 서버 로그인 확인</button></form>
<p>중복 가입·IP당 가입 제한·보안 인증이 나오면 자동으로 멈춥니다. 글은 아래 별도 검증 버튼을 눌러야 등록됩니다.</p>
<details><summary>이미 가입한 계정 연결</summary>
<form id="account"><label>텔판 닉네임<input id="nickname" autocomplete="username" required></label>
<label>텔판 기존 비밀번호<input id="password" type="password" autocomplete="current-password" required></label>
<button>계정 저장</button></form></details><p>서버 발행 검증을 통과해야 자동발행 대상으로 들어갑니다. 제목 40자·본문 500자 제한을 유지합니다.</p>
<button id="test">서버에서 무료 테스트 글 1건 발행·조회</button><pre id="result" role="status"></pre><a href="/">대시보드로 돌아가기</a>
<script>const result=document.getElementById('result');const csrf={{csrf|tojson}};
async function send(url,data){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Telpan-CSRF':csrf},body:JSON.stringify(data)});let v=await r.json();result.textContent=v.message||v.error;return v}
document.getElementById('account').onsubmit=async e=>{e.preventDefault();await send('/api/telpan/setup',{nickname:document.getElementById('nickname').value,password:document.getElementById('password').value});document.getElementById('password').value=''};
document.getElementById('signupAccount').onsubmit=async e=>{e.preventDefault();const b=document.getElementById('signupButton');b.disabled=true;try{await send('/api/telpan/signup',{nickname:document.getElementById('newNickname').value,password:document.getElementById('newPassword').value,email:document.getElementById('newEmail').value,snsId:document.getElementById('newSns').value,signup_confirmed:document.getElementById('signupConfirmed').checked})}catch(e){result.textContent='응답 확인이 필요합니다. 다시 가입 요청하지 말고 결과를 확인해주세요.'}finally{document.getElementById('newPassword').value='';b.disabled=false}};
document.getElementById('test').onclick=()=>send('/api/telpan/test',{});
async function refreshStatus(){let r=await fetch('/api/telpan/status');let s=await r.json();if(s.receipt&&s.receipt.state==='verified'){result.textContent='서버 발행·공개 조회 검증 완료\n'+s.receipt.url+'\n제목 일치: '+s.receipt.title_match+' / 본문 일치: '+s.receipt.body_match}else if(s.signup&&s.signup.message){result.textContent=s.signup.message}else if(s.failure){result.textContent=s.failure}else if(!s.login_saved){result.textContent='신규 가입 정보를 입력하거나 기존 계정을 연결해주세요.'}}refreshStatus();setInterval(refreshStatus,5000);</script></html>'''

KNOWLEDGE_PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>사이트별 가입·로그인 학습</title>
<style>body{font:17px/1.6 sans-serif;background:#101827;color:#eef3fb;max-width:1050px;margin:30px auto;padding:20px}article{background:#1c293c;padding:20px;border-radius:12px;margin:20px 0}a{color:#8bbaff}dt{font-weight:bold;color:#9fc0ff}dd{margin:0 0 15px}li{margin:5px 0}</style>
<h1>사이트별 가입·로그인 학습</h1><p>필수 입력값과 처리 경로를 저장해 다음 작업에 재사용합니다. 가입·로그인·발행은 각각 실제 확인된 단계만 표시합니다. 비밀번호와 연락처는 학습 기록에 저장하지 않습니다.</p>
{% for r in rows %}<article><h2>{{r.origin}} · 학습 v{{r.version}}</h2><dl>
<dt>필수 정보의 의미</dt><dd><ul>{% for f in r.fields %}<li>{{ {'account_nickname':'닉네임: 로그인할 계정 이름','account_password':'비밀번호: 해당 계정의 비밀번호','owner_email':'이메일: 본인이 답변·인증을 받을 주소','contact_sns':'SNS 아이디: 본인 텔레그램 또는 카카오톡 연락처','public_destination_url':'바로가기 링크: 홍보할 공개 사이트 주소','post_title':'제목: 목록에 표시할 글 제목','post_summary':'한 줄 소개: 목록에 표시할 짧은 설명','post_body':'상세 설명: 게시글 본문'}.get(f.role,f.role) }}</li>{% endfor %}</ul></dd>
{% if r.adapter=='thehongbo' %}<dt>성공한 순서</dt><dd>Google 계정 로그인 → 자유홍보 선택 → 링크·제목·한 줄 소개·상세 설명 입력 → 기본 이미지로 등록 → 공개 글에서 제목·본문 확인. 연락처 공개는 선택하지 않았습니다.</dd>{% endif %}
<dt>구현 상태</dt><dd>{{'브라우저에서 확인한 처리 방식 저장됨 · 서버 자동발행 미연결' if r.implementation_state=='browser_recipe_measured' else '전용 가입·로그인 처리 연결됨'}}</dd>
<dt>실제 가입 결과</dt><dd>{{(r.evidence.get('signup') or {}).get('state','아직 검증되지 않음')}}</dd>
<dt>실제 브라우저 로그인</dt><dd>{{'확인 완료' if (r.evidence.get('browser_login') or {}).get('state')=='verified' else '아직 검증되지 않음'}}</dd>
<dt>실제 브라우저 발행·공개 제목·본문 조회</dt><dd>{% if (r.evidence.get('browser_post') or {}).get('state')=='verified' %}확인 완료 · <a href="{{r.evidence.browser_post.url}}">검증한 글 보기</a>{% else %}아직 검증되지 않음{% endif %}</dd>
<dt>실제 서버 로그인</dt><dd>{% if (r.evidence.get('login') or {}).get('reason')=='google_signin_security_block' %}Google 보안 차단으로 서버 로그인 실패 · 새 글 제출 안 함{% elif (r.evidence.get('login') or {}).get('state')=='verified' %}확인 완료{% else %}아직 검증되지 않음{% endif %}</dd>
<dt>실제 서버 발행·공개 제목·본문 조회</dt><dd>{% if r.server_publish_verified %}확인 완료 · <a href="{{r.evidence.server_post.url}}">검증한 글 보기</a>{% else %}아직 검증되지 않음{% endif %}</dd>
<dt>유지하는 제한</dt><dd>IP당 가입 제한·인증·게시 권한 유지. 결과 불확실 시 재제출 중지.{% if r.adapter=='telpan' %} 텔판 제목 40자·본문 500자.{% endif %}</dd></dl></article>{% endfor %}
<a href="/telpan">텔판 연결</a> · <a href="/">운영 대시보드</a></html>'''
