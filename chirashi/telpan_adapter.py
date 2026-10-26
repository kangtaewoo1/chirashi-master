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
    if not title or not body or units(title) > 40 or units(body) > 500:
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

    def current():
        return next((s for s in ctx['load_sites']() if s.get('id') == sid), {})

    def csrf_ok():
        return bool(session.get('telpan_csrf') and secrets.compare_digest(
            str(request.headers.get('X-Telpan-CSRF', '')), session['telpan_csrf']))

    @app.route('/telpan')
    def telpan_setup():
        session.setdefault('telpan_csrf', secrets.token_urlsafe(32))
        return render_template_string(PAGE, csrf=session['telpan_csrf'])

    @app.route('/api/telpan/status')
    def telpan_status():
        site = current()
        return jsonify(ok=True, login_saved=path.exists(), enabled=bool(site.get('telpan_enabled')),
                       ready=not bool(block(site)), reason=block(site),
                       test_status=site.get('write_test_status', ''),
                       receipt=site.get('unattended_result') or {}, failure=site.get('last_fail_reason', ''))

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


PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>텔판 자동발행 연결</title><style>body{font:18px/1.7 sans-serif;background:#101827;color:#eef3fb;max-width:680px;margin:40px auto;padding:20px}input,button{font:inherit;box-sizing:border-box;width:100%;padding:12px;margin:8px 0;border-radius:8px}button{background:#4085ff;color:white;border:0;cursor:pointer}a{color:#8bbaff}pre{white-space:pre-wrap;overflow-wrap:anywhere}label{display:block}</style>
<h1>텔판 자동발행 연결</h1><p>가입한 텔판 계정을 한 번 저장해주세요. 비밀번호는 서버에 암호화해 보관하며 화면에 다시 표시하지 않습니다.</p>
<form id="account"><label>텔판 닉네임<input id="nickname" autocomplete="username" required></label>
<label>텔판 기존 비밀번호<input id="password" type="password" autocomplete="current-password" required></label>
<button>계정 저장</button></form><p>계정 저장 후 서버 검증을 통과해야 자동발행 대상으로 들어갑니다. 제목 40자·본문 500자 제한을 유지합니다.</p>
<button id="test">서버에서 무료 테스트 글 1건 발행·조회</button><pre id="result" role="status"></pre><a href="/">대시보드로 돌아가기</a>
<script>const result=document.getElementById('result');const csrf={{csrf|tojson}};
async function send(url,data){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Telpan-CSRF':csrf},body:JSON.stringify(data)});let v=await r.json();result.textContent=v.message||v.error;return v}
document.getElementById('account').onsubmit=async e=>{e.preventDefault();await send('/api/telpan/setup',{nickname:document.getElementById('nickname').value,password:document.getElementById('password').value});document.getElementById('password').value=''};
document.getElementById('test').onclick=()=>send('/api/telpan/test',{});
async function status(){let r=await fetch('/api/telpan/status');let s=await r.json();if(s.receipt&&s.receipt.state==='verified'){result.textContent='서버 발행·공개 조회 검증 완료\n'+s.receipt.url+'\n제목 일치: '+s.receipt.title_match+' / 본문 일치: '+s.receipt.body_match}else if(s.failure){result.textContent=s.failure}else if(!s.login_saved){result.textContent='텔판 계정 저장이 필요합니다.'}}status();setInterval(status,5000);</script></html>'''
