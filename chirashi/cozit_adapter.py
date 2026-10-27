"""Cozit owner project updates through the normal, password-authenticated UI.

This dedicated route never joins the general advertising queue. No session import,
Google security bypass, duplicate project creation, or uncertain-submit retries.
"""
import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN = 'https://cozit.app'
PROJECT_URL = ORIGIN + '/projects/d00239cd-4081-4717-8728-0f84a8e84ecf'
_lock = threading.Lock()


def valid_project_url(url):
    return str(url) == PROJECT_URL


def content(title, body):
    title, body = str(title).strip(), str(body).strip()
    units = lambda s: len(s.encode('utf-16-le')) // 2
    if not title or not body or units(title) > 100 or units(body) > 600:
        raise ValueError('프로젝트 소식은 제목 100자·본문 600자 이내여야 합니다.')
    return title, body


def block(settings, journal, now=None):
    now = now or datetime.now(timezone.utc)
    if not valid_project_url(settings.get('project_url')) or settings.get('permission') is not True:
        return 'owner_permission_required'
    if settings.get('maintenance_hold') or settings.get('manual_review_only'):
        return 'maintenance_hold'
    if settings.get('has_captcha') or settings.get('write_access_state') in (
            'admin_only', 'human_verification_required', 'login_required'):
        return 'permission_or_human_verification_required'
    if journal.get('state') in ('submitting', 'uncertain', 'submitted'):
        return 'previous_submission_requires_review'
    events = list(settings.get('verified_submissions') or [])
    # A crash between receipt persistence and history persistence cannot reopen the limit.
    if journal.get('state') == 'verified' and journal.get('submitted_at'):
        events.append(journal['submitted_at'])
    events = list(set(events))
    daily = int(settings.get('daily_limit', 1))
    interval = int(settings.get('min_interval_minutes', 1440))
    if daily < 1 or interval < 1:
        return 'invalid_limits'
    # Current Korean publishing limits use UTC+09:00 (no DST).
    kst = timezone(timedelta(hours=9), 'Asia/Seoul')
    stamps = [datetime.fromisoformat(t) for t in events]
    if any(t.tzinfo is None for t in stamps):
        return 'invalid_submission_history'
    if sum(t.astimezone(kst).date() == now.astimezone(kst).date() for t in stamps) >= daily:
        return 'daily_limit'
    if stamps and (now - max(stamps)).total_seconds() < interval * 60:
        return 'minimum_interval'
    return ''


def human_check(d):
    from selenium.webdriver.common.by import By
    return any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR,
        'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],.cf-turnstile,input[name="captcha"]'))


def login(d, email, password):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    wait = WebDriverWait(d, 25)
    d.get(ORIGIN + '/login')
    field = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[type="email"]')))
    if urlsplit(d.current_url).netloc != 'cozit.app' or human_check(d):
        raise ValueError('login_permission_or_human_verification_required')
    field.send_keys(email)
    d.find_element(By.CSS_SELECTOR, 'input[type="password"]').send_keys(password)
    d.find_element(By.XPATH, '//button[normalize-space()="Log in"]').click()
    wait.until(lambda x: urlsplit(x.current_url).netloc == 'cozit.app'
               and urlsplit(x.current_url).path != '/login')
    wait.until(EC.visibility_of_element_located((By.XPATH, '//button[normalize-space()="Log out"]')))


def open_timeline(d, owner=False):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    wait = WebDriverWait(d, 25)
    d.get(PROJECT_URL)
    wait.until(EC.visibility_of_element_located((By.TAG_NAME, 'h1')))
    if not valid_project_url(d.current_url) or human_check(d):
        raise ValueError('project_permission_or_human_verification_required')
    if owner and not d.find_elements(By.CSS_SELECTOR,
            'a[href="/projects/d00239cd-4081-4717-8728-0f84a8e84ecf/edit"]'):
        raise ValueError('project_owner_permission_required')
    wait.until(EC.element_to_be_clickable((By.XPATH, '//button[normalize-space()="Timeline"]'))).click()
    return wait


def public_receipt(driver_factory, title, body, screenshot_path=None):
    """Fresh anonymous browser; inspect rendered timeline, never login/copy cookies."""
    import requests
    from selenium.webdriver.common.by import By
    response = requests.get(PROJECT_URL, headers={'User-Agent':'Mozilla/5.0'},
                            timeout=25, allow_redirects=False)
    if response.status_code != 200 or not valid_project_url(response.url):
        raise ValueError('public_read_failed')
    d = driver_factory()
    try:
        wait = open_timeline(d)
        norm = lambda s: ' '.join(s.split())
        # Both exact strings must be visible in the same smallest update card.
        def card(x):
            return x.execute_script("""
                const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
                const title=arguments[0],body=arguments[1];
                const starts=[...document.querySelectorAll('h2,h3,h4,p,div,span')]
                  .filter(e=>e.getClientRects().length && norm(e.innerText)===title);
                for(let e of starts){for(let p=e.parentElement,n=0;p&&n<5;p=p.parentElement,n++){
                  if([...p.querySelectorAll('p,div,span')].some(b=>b.getClientRects().length&&norm(b.innerText)===body))return true;
                }}return false;
            """, norm(title), norm(body))
        wait.until(card)
        if d.find_elements(By.XPATH, '//button[normalize-space()="Log out"]'):
            raise ValueError('anonymous_verification_requires_no_login')
        if screenshot_path:
            Path(screenshot_path).parent.mkdir(parents=True,exist_ok=True)
            d.save_screenshot(str(screenshot_path))
        return {'state':'verified', 'transport':'cozit_server_browser', 'url':PROJECT_URL,
                'http':200, 'checked_at':datetime.now(timezone.utc).isoformat(),
                'title':title, 'title_match':True, 'body_match':True,
                'public_read_without_authentication':True, 'content_kind':'project_update',
                'body_chars':len(body), 'body_sha256':hashlib.sha256(body.encode()).hexdigest()}
    finally:
        try:d.quit()
        except Exception:pass


def publish_update(ctx, title, body):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from telpan_adapter import new_driver, private_write
    if not _lock.acquire(blocking=False):
        return {'ok':False,'reason':'job_in_progress','submitted_posts':0}
    d = None
    submitted = False
    root = Path(ctx['DATA_DIR'])
    journal_path = root / 'cozit_submission.json'
    settings_path = root / 'cozit_settings.json'
    try:
        # OS lock also covers separate workers / diagnostic processes.
        import fcntl
        with (root/'cozit_publish.lock').open('a') as process_lock:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            load = lambda path: json.loads(path.read_text()) if path.exists() else {}
            settings, journal = load(settings_path), load(journal_path)
            reason = block(settings, journal)
            if reason:return {'ok':False,'reason':reason,'submitted_posts':0}
            title, body = content(title, body)
            cred = load(root/'cozit_login_private.json')
            password = ctx['_decrypt_password'](cred.get('password_enc',''))
            if not cred.get('email') or not password:
                return {'ok':False,'reason':'credentials_required','submitted_posts':0}
            d = new_driver(ctx)
            login(d, cred['email'], password)
            wait = open_timeline(d, owner=True)
            wait.until(EC.element_to_be_clickable((By.XPATH, '//button[normalize-space()="Post an update"]'))).click()
            title_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR,
                'input[placeholder="e.g. Just shipped dark mode"]')))
            title_input.send_keys(title)
            d.find_element(By.CSS_SELECTOR, 'textarea[placeholder="What changed? Why does it matter?"]').send_keys(body)
            if human_check(d):raise ValueError('human_verification_required')
            # Recheck the owner's configured limits immediately before the only click.
            reason = block(load(settings_path), load(journal_path))
            if reason:raise ValueError(reason)
            journal = {'state':'submitting','submitted_at':datetime.now(timezone.utc).isoformat(),
                       'title':title,'body':body,'submissions':1,'project_url':PROJECT_URL}
            private_write(journal_path,journal)
            submitted = True  # An uncertain click must never be retried.
            d.find_element(By.XPATH, '//button[normalize-space()="Post"]').click()
            wait.until(lambda x: not any(e.is_displayed() for e in x.find_elements(By.CSS_SELECTOR,
                'input[placeholder="e.g. Just shipped dark mode"]')))
            journal['state']='submitted';private_write(journal_path,journal)
            d.quit();d=None
            receipt = public_receipt(lambda:new_driver(ctx),title,body,
                                     root.parent/'work/cozit-server/public-update.png')
            journal.update(state='verified',receipt=receipt);private_write(journal_path,journal)
            settings.setdefault('verified_submissions',[]).append(journal['submitted_at'])
            private_write(settings_path,settings)
            from form_schema import merge_flow_knowledge
            path=root/'site_flow_knowledge.json'
            with ctx.get('_json_lock',lambda p:threading.RLock())(path):
                db=load(path);flow=db[ORIGIN]
                flow=merge_flow_knowledge(flow,flow,{'stage':'login','state':'verified'})
                flow=merge_flow_knowledge(flow,flow,{'stage':'server_post','state':'verified','receipt':receipt})
                # Dedicated owner-update adapter is connected; this is not a general ad destination.
                flow['implementation_state']='adapter_implemented';db[ORIGIN]=flow;private_write(path,db)
            return {'ok':True,'submitted_posts':1,'receipt':receipt}
    except Exception as exc:
        if d:
            try:
                proof_dir=root.parent/'work/cozit-server';proof_dir.mkdir(parents=True,exist_ok=True)
                d.save_screenshot(str(proof_dir/'publish-review.png'))
            except Exception:pass
        if submitted:
            journal.update(state='uncertain',reason='submission_or_public_verification_requires_review')
            private_write(journal_path,journal)
        return {'ok':False,'reason':'submission_requires_review' if submitted else 'login_or_write_preflight_failed',
                'error_type':type(exc).__name__,'submitted_posts':int(submitted)}
    finally:
        if d:
            try:d.quit()
            except Exception:pass
        _lock.release()


def register(app,ctx):
    from flask import jsonify,request,session,render_template_string
    @app.route('/cozit')
    def cozit_setup():
        session.setdefault('cozit_csrf',secrets.token_urlsafe(32))
        return render_template_string(PAGE,csrf=session['cozit_csrf'])

    @app.route('/api/cozit/status')
    def cozit_status():
        root=Path(ctx['DATA_DIR'])
        load=lambda p:json.loads(p.read_text()) if p.exists() else {}
        settings=load(root/'cozit_settings.json');journal=load(root/'cozit_submission.json')
        return jsonify(ok=True,credentials_saved=(root/'cozit_login_private.json').exists(),
                       project_url=PROJECT_URL,reason=block(settings,journal),
                       state=journal.get('state','not_submitted'),receipt=journal.get('receipt'),
                       general_ad_queue_enabled=False)

    @app.route('/api/cozit/project-update',methods=['POST'])
    def cozit_project_update():
        if not session.get('cozit_csrf') or not secrets.compare_digest(
                request.headers.get('X-Cozit-CSRF',''),session['cozit_csrf']):
            return jsonify(ok=False,reason='reload_setup_page'),403
        data=request.get_json(silent=True) or {}
        if data.get('owner_project_update_confirmed') is not True:
            return jsonify(ok=False,reason='owner_project_update_confirmation_required'),400
        try:title,body=content(data.get('title',''),data.get('body',''))
        except ValueError as exc:return jsonify(ok=False,reason=str(exc)),400
        result=publish_update(ctx,title,body)
        return jsonify(result),200 if result['ok'] else 409


PAGE='''<!doctype html><meta charset="utf-8"><title>Cozit 프로젝트 소식 연결</title>
<main style="max-width:760px;margin:40px auto;font-family:sans-serif"><h1>Cozit 프로젝트 소식</h1>
<p>기존 프로젝트의 Timeline에 개발 소식 1건을 등록하고 로그인 없는 제목·본문 조회까지 확인합니다.</p>
<p>일반 광고 발행 큐에는 연결하지 않습니다. 권한·인증·일일 한도·최소 간격을 유지하며 결과가 불명확하면 재제출을 중지합니다.</p>
<p><a href="'''+PROJECT_URL+'''">대상 프로젝트</a> · <a href="/site-knowledge">학습 내역</a></p>
<pre id="status">상태 확인 중</pre>
<label>개발 소식 제목<input id="title" maxlength="100" style="display:block;width:100%"></label><br>
<label>개발 소식 본문<textarea id="body" maxlength="600" rows="9" style="display:block;width:100%"></textarea></label>
<p><label><input id="confirm" type="checkbox">이 프로젝트에 대한 실제 개발 소식을 공개 등록합니다.</label></p>
<button id="publish">서버에서 소식 1건 등록·공개 조회 확인</button>
<script>
const status=document.getElementById('status');
const reasons={daily_limit:'하루 발행 한도에 도달했습니다.',minimum_interval:'다음 발행까지 최소 간격을 기다려야 합니다.',previous_submission_requires_review:'이전 발행 결과 확인이 필요합니다. 재제출하지 마세요.',owner_permission_required:'프로젝트 소유자의 사용 권한 확인이 필요합니다.',login_or_write_preflight_failed:'로그인 또는 글쓰기 권한 확인에 실패했습니다.',submission_requires_review:'제출 결과 확인이 필요합니다. 다시 누르지 마세요.'};
function show(d){const receipt=d.receipt;if(receipt&&receipt.title_match&&receipt.body_match){status.textContent='서버 발행·공개 제목·본문 조회 확인 완료\\n제목: '+receipt.title+'\\n본문: '+receipt.body_chars+'자 일치\\n글 주소(Timeline): '+receipt.url+(d.reason?'\\n'+(reasons[d.reason]||'현재 발행 보류'):'');}else{status.textContent=(d.credentials_saved?'이메일 로그인 정보 저장됨. 실제 발행은 별도 확인합니다.':'로그인 정보 확인 필요')+(d.reason?'\\n'+(reasons[d.reason]||'현재 발행 보류'):'');}}
async function refresh(){let r=await fetch('/api/cozit/status');let d=await r.json();show(d);document.getElementById('publish').disabled=Boolean(d.reason);}
document.getElementById('publish').onclick=async()=>{const b=document.getElementById('publish');b.disabled=true;
try{const r=await fetch('/api/cozit/project-update',{method:'POST',headers:{'Content-Type':'application/json','X-Cozit-CSRF':{{csrf|tojson}}},body:JSON.stringify({title:document.getElementById('title').value,body:document.getElementById('body').value,owner_project_update_confirmed:document.getElementById('confirm').checked})});show(await r.json());}
catch(e){status.textContent='결과 확인 필요 — 다시 누르지 마세요.';}finally{b.disabled=false;}};refresh();
</script></main>'''
