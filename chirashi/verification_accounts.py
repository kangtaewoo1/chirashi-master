"""One-worker, explicit-account signup/login and authenticated write-form verification.

No posting, fabricated identity, CAPTCHA solving, permission changes or TLS bypass.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from urllib.parse import urljoin,urlsplit
from site_reaudit import clean_url,host,now,safe_public,view_page
from site_verification_detail import annotate,board_key
from form_schema import Parser,measure_html,_anc

LABELS={'queued':'워커 실행 대기','running':'서버 계정 검증 중','needs_credentials':'계정 정보 입력 필요',
        'waiting_terms':'약관 확인·동의 대기','waiting_human':'사람 확인 필요 · 제출 안 함',
        'needs_profile':'가입 필수 정보 입력 필요','signup_uncertain':'가입 결과 불명확 · 재제출 금지',
        'login_uncertain':'로그인 결과 확인 필요 · 자동 재시도 안 함','login_rejected':'로그인 확인 실패 · 계정 확인 필요',
        'authenticated_write_verified':'서버 로그인·원래 작성 화면 확인 완료',
        'authenticated_write_review':'서버 로그인 확인 · 작성 화면 추가 검토',
        'permission_hold':'권한 제한 유지 · 확인 대기','interrupted_review':'중단된 요청 · 결과 확인 필요',
        'form_review':'양식 추가 검토','connection_review':'접속·인증서 확인 필요','waiting_email':'이메일 인증 완료 후 로그인 재검증 필요'}


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS account_jobs(id TEXT PRIMARY KEY,domain TEXT,spec TEXT,secret TEXT,state TEXT,result TEXT,updated_at TEXT)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS account_one_active ON account_jobs(domain) WHERE state IN (\'queued\',\'running\')')


def cipher(folder):
    from cryptography.fernet import Fernet
    folder.mkdir(parents=True,exist_ok=True);path=folder/'account_store.key'
    try:
        fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:pass
    else:
        with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def same_origin(url,origin):
    p=urlsplit(url);base=urlsplit(origin)
    return p.scheme=='https' and p.netloc==base.netloc and not p.username and not p.password


def validate_spec(spec):
    u=clean_url(spec.get('write_url',''));p=urlsplit(u)
    origin=p.scheme+'://'+p.netloc
    if not u or p.scheme!='https' or not re.search(r'write|dispBoardWrite|mod=editor',u,re.I):raise ValueError('HTTPS 새 글 작성 주소가 필요합니다.')
    if re.search(r'[?&](wr_id|document_srl)=',u):raise ValueError('기존 글 수정 주소는 사용할 수 없습니다.')
    result={'write_url':u,'origin':origin,'mode':spec.get('mode','login')}
    if result['mode'] not in ('signup','login'):raise ValueError('가입 또는 기존 로그인 모드를 선택하세요.')
    for key in ('login_url','signup_url'):
        value=clean_url(spec.get(key,''))
        if value and not same_origin(value,origin):raise ValueError('같은 HTTPS 사이트의 가입·로그인 주소만 사용합니다.')
        result[key]=value
    return result


def terms_form(markup,url):
    p=Parser();p.feed(markup);p.close();forms=[n for n in p.nodes if n.tag=='form'];matches=[]
    for i,form in enumerate(forms):
        fields=[n for n in p.nodes if n.tag=='input' and _anc(n,'form') is form]
        boxes=[n for n in fields if n.attrs.get('type')=='checkbox' and re.search(r'agree|consent',n.attrs.get('name',''),re.I)]
        if not boxes or any(n.attrs.get('type')=='password' for n in fields):continue
        names=[n.attrs['name'] for n in boxes];action=urljoin(url,form.attrs.get('action',''))
        score=100*int({'agree','agree2'}.issubset(names))+50*int(bool(re.search(r'register|signup|/join',urlsplit(action).path,re.I)))
        if score:matches.append((score,{'form_index':i,'names':names,'fallback_text':form.text()[:60000],'url':clean_url(url),'action':clean_url(action)}))
    matches.sort(key=lambda x:x[0],reverse=True)
    return matches[0][1] if matches and (len(matches)==1 or matches[0][0]>matches[1][0]) else None


def seal_profile(folder,profile):
    # Keep all supplied personal fields in the encrypted column, never results/history.
    allowed={k:str(v)[:512] for k,v in profile.items() if k in ('id','password','name','nickname','email','phone') and v}
    if profile.get('_auto_credentials'):allowed['_auto_credentials']=True
    pattern=str(profile.get('email_pattern') or '')
    if pattern and not allowed.get('email'):
        if not re.fullmatch(r'[^@\s{}]*\{tag\}[^@\s{}]*@[^@\s{}]+\.[^@\s{}]+',pattern):raise ValueError('본인이 수신할 수 있는 별칭 규칙에 {tag}를 넣어주세요.')
        allowed['email']=pattern.replace('{tag}','verify'+secrets.token_hex(5))
    return cipher(folder).encrypt(json.dumps(allowed).encode()).decode()


def enqueue(c,folder,spec,profile):
    spec=validate_spec(spec);init(c);domain=host(spec['write_url'])
    if c.execute("SELECT 1 FROM account_jobs WHERE domain=? AND state IN ('queued','running')",(domain,)).fetchone():raise ValueError('이 사이트의 계정 검증이 이미 진행 중입니다.')
    if spec['mode']=='signup' and any(json.loads(r).get('signup_submitted') for (r,) in c.execute('SELECT result FROM account_jobs WHERE domain=?',(domain,))):raise ValueError('이 사이트에 이전 가입 제출 기록이 있습니다. 중복 가입 대신 기존 계정 로그인을 사용하세요.')
    token=seal_profile(folder,profile)
    jid=secrets.token_hex(12)
    c.execute('INSERT INTO account_jobs VALUES(?,?,?,?,?,?,?)',(jid,domain,json.dumps(spec),token,'queued','{}',now()));c.commit()
    return jid


class Browser:
    def __init__(self,folder,heartbeat):self.folder=folder;self.heartbeat=heartbeat;self.driver=None;self.temp=None
    def __enter__(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        self.temp=tempfile.TemporaryDirectory(prefix='account-check-')
        opt=webdriver.ChromeOptions();opt.binary_location='/usr/bin/google-chrome'
        for flag in ('--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-first-run','--disable-extensions','--lang=ko-KR','--window-size=1280,900'):opt.add_argument(flag)
        opt.add_argument('--user-data-dir='+self.temp.name);opt.page_load_strategy='eager'
        opt.set_capability('acceptInsecureCerts',False);opt.set_capability('unhandledPromptBehavior','ignore')
        self.driver=webdriver.Chrome(service=Service('/opt/chirashi/runtime/chromedriver',log_output=os.devnull),options=opt)
        self.driver.set_page_load_timeout(18);return self
    def __exit__(self,*args):
        if self.driver:
            try:self.driver.quit()
            except Exception:pass
        if self.temp:self.temp.cleanup()
    def open(self,url,origin):
        if not same_origin(url,origin):raise ValueError('origin_hold')
        safe_public(url);self.heartbeat(url=clean_url(url),phase='account_page')
        self.driver.get(url);time.sleep(.8)
        if not same_origin(self.driver.current_url,origin):raise ValueError('origin_hold')
        if re.search(r'NET::ERR_CERT_|Privacy error|연결이 비공개',self.driver.page_source):raise ValueError('tls_hold')
    def read(self,stage,requested):
        markup=self.driver.page_source;url=clean_url(self.driver.current_url)
        # No raw markup, values, cookies, screenshots or tokens are persisted.
        page=view_page({'url':url,'state':'authenticated_browser_render','html':markup},stage)
        page.update(stage=stage,requested_url=requested)
        page=annotate(page,markup)
        from selenium.webdriver.common.by import By
        fields=self.driver.find_elements(By.CSS_SELECTOR,'input[name="cf-turnstile-response"],textarea[name="g-recaptcha-response"],input[name="g-recaptcha-response"]')
        page['challenge_widget_present']=bool(fields)
        page['challenge_response_present']=bool(fields) and all(bool(e.get_attribute('value')) for e in fields)
        inputs=self.driver.find_elements(By.CSS_SELECTOR,'input[name="captcha_key"],input[name="captcha"],input[name="wr_key"]')
        page['captcha_input_empty']=any(e.is_displayed() and not e.get_attribute('value') for e in inputs)
        page['captcha_input_present']=any(e.is_displayed() for e in inputs)
        page['captcha_input_answer_present']=any(e.is_displayed() and bool(e.get_attribute('value')) for e in inputs)
        page['captcha_image_observed']=any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR,'img[id*="captcha"],img[src*="captcha"]'))
        return page,measure_html(markup,url,stage)
    def logged_in(self):
        from selenium.webdriver.common.by import By
        return any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR,'a[href*="logout"]'))
    def terms(self):
        from selenium.webdriver.common.by import By
        chosen=terms_form(self.driver.page_source,self.driver.current_url)
        if not chosen:return None
        form=self.driver.find_elements(By.TAG_NAME,'form')[chosen['form_index']]
        docs=[e.get_attribute('value') or e.text for e in form.find_elements(By.TAG_NAME,'textarea') if e.is_displayed()]
        chosen['text']='\n\n'.join(docs+[chosen.pop('fallback_text')])[:60000]
        chosen['fingerprint']=hashlib.sha256(json.dumps([chosen['text'],chosen['names'],chosen['action']],ensure_ascii=False).encode()).hexdigest()
        return chosen
    def submit_terms(self,terms,origin):
        from selenium.webdriver.common.by import By
        if not same_origin(terms['action'],origin):raise ValueError('origin_hold')
        if not set(terms['names']).issubset({'agree','agree2'}):raise ValueError('unsupported_terms_form')
        form=self.driver.find_elements(By.TAG_NAME,'form')[terms['form_index']]
        for name in terms['names']:
            if not re.fullmatch(r'[A-Za-z0-9_\[\]-]+',name):raise ValueError('form_review')
            element=form.find_element(By.NAME,name)
            if not element.is_selected():element.click()
        self.click_submit(form,origin)
    def fill(self,schema,profile,kind):
        from selenium.webdriver.common.by import By
        missing=[];filled=[];fields=(schema.get('form') or {}).get('fields',[])
        for f in fields:
            if f.get('hidden') or f.get('disabled'):continue
            role=f['role'];required=f.get('required') is True
            if role=='agreement':
                missing.append('가입 양식의 추가 동의')
                continue
            if role in ('captcha','access_code'):
                if role=='captcha' and f.get('selector'):
                    elements=self.driver.find_elements(By.CSS_SELECTOR,f['selector'])
                    if len(elements)==1 and elements[0].get_attribute('value'):continue
                missing.append('사람 확인 또는 전용 코드');continue
            key='password' if role=='password_confirm' else role
            value=profile.get(key)
            if not value:
                if required or role in ('id','password','password_confirm'):missing.append(f.get('label') or role)
                continue
            if role not in ('id','password','password_confirm','name','nickname','email','phone'):continue
            field=self.driver.find_element(By.CSS_SELECTOR,f['selector'])
            if not field.is_displayed():
                if required:missing.append(f.get('label') or role)
                continue
            if f.get('pattern'):
                try:
                    if not re.fullmatch(f['pattern'],value):missing.append(f.get('label') or role);continue
                except re.error:missing.append('입력 규칙 추가 확인');continue
            if f.get('minlength') and len(value)<int(f['minlength']):missing.append(f.get('label') or role);continue
            if f.get('maxlength') and len(value)>int(f['maxlength']):missing.append(f.get('label') or role);continue
            field.clear();field.send_keys(value);filled.append(role)
        if kind=='login' and not {'id','password'}.issubset(filled):missing.append('로그인 아이디·비밀번호 입력칸')
        return list(dict.fromkeys(missing))
    def submit(self,schema,origin):
        from selenium.webdriver.common.by import By
        f=schema['form'];forms=self.driver.find_elements(By.TAG_NAME,'form')
        matches=[x for x in forms if (f.get('id') and x.get_attribute('id')==f['id']) or (not f.get('id') and clean_url(x.get_attribute('action'))==f['action'])]
        if len(matches)!=1:raise ValueError('form_review')
        self.click_submit(matches[0],origin)
    def click_submit(self,form,origin):
        from selenium.webdriver.common.by import By
        action=form.get_attribute('action')
        if not same_origin(action,origin) or form.get_attribute('method').lower()!='post':raise ValueError('unsafe_form_action')
        buttons=[x for x in form.find_elements(By.CSS_SELECTOR,'button[type="submit"],input[type="submit"],button:not([type])') if x.is_displayed() and x.is_enabled()]
        if len(buttons)!=1:raise ValueError('form_review')
        buttons[0].click();time.sleep(1)
        if not same_origin(self.driver.current_url,origin):raise ValueError('origin_hold')
    def signup_success(self):
        from selenium.webdriver.common.by import By
        if self.logged_in():return True
        text=self.driver.find_element(By.TAG_NAME,'body').text
        return bool(re.search(r'register_result|join_complete|signup.success',self.driver.current_url,re.I)
                    and re.search(r'회원가입.{0,20}(완료|축하)|가입을.{0,10}축하|registration (?:is )?(?:successful|complete)',text,re.I)
                    and not re.search(r'가입.{0,15}(실패|오류|완료되지)',text))
    def needs_email(self):
        from selenium.webdriver.common.by import By
        text=self.driver.find_element(By.TAG_NAME,'body').text
        return bool(re.search(r'인증\s*메일.{0,20}(발송|전송)|이메일\s*인증.{0,15}(완료|필요)|메일\s*인증\s*후',text))
    def write_controls(self,schema):
        from selenium.webdriver.common.by import By
        roles=set()
        for f in (schema.get('form') or {}).get('fields',[]):
            if f.get('role') not in ('title','content') or not f.get('selector') or f.get('hidden'):continue
            if any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR,f['selector'])):roles.add(f['role'])
        if any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR,'iframe[id*="editor"],iframe[id*="se2"],iframe.cke_wysiwyg_frame,[contenteditable="true"]')):roles.add('content')
        return sorted(roles)
    def solve_image_once(self,schema,before_request=lambda:None):
        from selenium.webdriver.common.by import By
        from verification_captcha import solve_image
        cfg=json.loads((self.folder.parent/'config.json').read_text())
        key=str(cfg.get('twocaptcha_api_key') or '')
        if not cfg.get('twocaptcha_enabled') or not key:raise ValueError('captcha_provider_not_configured')
        selected=schema.get('form') or {};forms=self.driver.find_elements(By.TAG_NAME,'form')
        forms=[f for f in forms if selected.get('id') and f.get_attribute('id')==selected['id']]
        if len(forms)!=1:raise ValueError('captcha_form_review')
        images=[e for e in forms[0].find_elements(By.CSS_SELECTOR,'img[id*="captcha"],img[src*="captcha"]') if e.is_displayed()]
        inputs=[e for e in forms[0].find_elements(By.CSS_SELECTOR,'input[name="captcha_key"],input[name="captcha"],input[name="wr_key"]') if e.is_displayed()]
        if len(images)!=1 or len(inputs)!=1:raise ValueError('captcha_type_manual_review')
        body=images[0].screenshot_as_base64
        if len(body)>130000:raise ValueError('captcha_image_too_large')
        before_request()
        answer=solve_image(key,body,heartbeat=lambda:self.heartbeat(phase='approved_image_captcha'))
        if not re.fullmatch(r'[A-Za-z0-9]{1,12}',answer['text']):raise ValueError('captcha_answer_review')
        inputs[0].clear();inputs[0].send_keys(answer['text'])
        return {'provider':'2captcha_image','actual_cost_usd':answer['actual_cost_usd'],'attempts':1}


def blocked(page,stage):
    if page.get('access_reason'):return 'permission_hold'
    h=page.get('human_check',{})
    complete=(h.get('scope')==stage and (page.get('challenge_widget_present') or page.get('captcha_input_present'))
              and (not page.get('challenge_widget_present') or page.get('challenge_response_present'))
              and (not page.get('captcha_input_present') or page.get('captcha_input_answer_present')) and not page.get('captcha_input_empty'))
    if h.get('required_step_confirmed') and h.get('scope') in ('page_access',stage) and not complete:return 'waiting_human'
    return None


def generate_missing_account(schema,profile,persist):
    if not profile.get('_auto_credentials') or schema.get('state')!='measured':return
    fields=(schema.get('form') or {}).get('fields',[])
    changed=False
    for role in ('id','password'):
        if profile.get(role):continue
        f=next((f for f in fields if f.get('role')==role and not f.get('hidden')),None)
        if not f:continue
        hints=f.get('text_constraints') or {};minimum=int(f.get('minlength') or hints.get('min_length') or (3 if role=='id' else 10))
        maximum=min(64,int(f.get('maxlength') or hints.get('max_length') or (20 if role=='id' else 32)))
        if minimum>maximum or maximum<3 or (role=='password' and maximum<10):raise ValueError('credential_rules_review')
        length=max(minimum,min(maximum,12 if role=='id' else 16))
        value=('v'+secrets.token_hex(32))[:length] if role=='id' else 'Aa7!'+''.join(secrets.choice('abcdefghijkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!@#$') for _ in range(length-4))
        if f.get('pattern'):
            try:
                if not re.fullmatch(f['pattern'],value):continue
            except re.error:continue
        profile[role]=value;changed=True
    if not profile.get('nickname') and profile.get('id'):profile['nickname']=profile['id'];changed=True
    if changed:persist(profile)


def execute(spec,profile,prior,approval,browser,journal,persist_profile=lambda p:None):
    """Every mutation is journaled before click. Never performs a post submission."""
    result={'pages':[],'signup_submitted':bool(prior.get('signup_submitted')),'signup_verified':bool(prior.get('signup_verified')),
            'login_submitted':False,'login_verified':False,'authenticated_write_verified':False,'new_posts_submitted':0,'permission_changed':False}
    origin=spec['origin']
    def finish(state,**kw):return dict(result,state=state,checked_at=now(),**kw)
    result['captcha_attempts']=int(prior.get('captcha_attempts') or 0)
    result['captcha_provider_calls']=int(prior.get('captcha_provider_calls') or 0)
    def human_hold(page,schema,stage,url):
        hold=blocked(page,stage);consent=spec.get('captcha_approval') or {}
        if hold=='waiting_human' and page.get('human_check',{}).get('scope')==stage and (not page.get('challenge_widget_present') or page.get('challenge_response_present')) and stage in ('signup','login') and consent.get('stage')==stage and consent.get('max_tasks')==1 and time.time()-consent.get('at',0)<1800 and result['captcha_attempts']==0 and profile.get('id') and profile.get('password'):
            missing=[f.get('label') or f['role'] for f in (schema.get('form') or {}).get('fields',[]) if f.get('required') is True and not f.get('hidden') and f.get('role') not in ('captcha','agreement','password_confirm') and not profile.get(f['role'])]
            if missing:result['missing']=missing;return page,schema,'needs_profile'
            result['captcha_attempts']=1;journal(finish('waiting_human',human_stage=stage))
            def charge_journal():
                result['captcha_provider_calls']+=1;journal(finish('waiting_human',human_stage=stage))
            result['captcha_receipt']=browser.solve_image_once(schema,charge_journal)
            page,schema=browser.read(stage,url);result['pages'].append(page);hold=blocked(page,stage)
        return page,schema,hold
    if spec['mode']=='signup' and not result['signup_verified']:
        if result['signup_submitted']:return finish('signup_uncertain')
        if not spec.get('signup_url'):return finish('form_review')
        browser.open(spec['signup_url'],origin);page,schema=browser.read('signup',spec['signup_url']);result['pages'].append(page)
        generate_missing_account(schema,profile,persist_profile)
        page,schema,hold=human_hold(page,schema,'signup',spec['signup_url'])
        if hold:return finish(hold,human_stage='signup')
        terms=browser.terms()
        if terms:
            if not approval or approval.get('fingerprint')!=terms['fingerprint'] or time.time()-approval.get('at',0)>1800:return finish('waiting_terms',terms=terms)
            browser.submit_terms(terms,origin)
            page,schema=browser.read('signup',spec['signup_url']);result['pages'].append(page)
            generate_missing_account(schema,profile,persist_profile)
            page,schema,hold=human_hold(page,schema,'signup',spec['signup_url'])
            if hold:return finish(hold,human_stage='signup')
        if schema.get('state')!='measured':return finish('form_review')
        if not same_origin(schema['form']['action'],origin) or schema['form']['method']!='post':return finish('permission_hold')
        if not profile.get('id') or not profile.get('password'):return finish('needs_credentials')
        missing=browser.fill(schema,profile,'signup')
        if missing:return finish('needs_profile',missing=missing)
        result['signup_submitted']=True;journal(finish('signup_uncertain'))
        browser.submit(schema,origin)
        result['signup_verified']=browser.signup_success()
        if browser.needs_email():return finish('waiting_email')
        if not result['signup_verified']:return finish('signup_uncertain')
        journal(finish('running'))
    if not browser.logged_in():
        if not profile.get('id') or not profile.get('password'):return finish('needs_credentials')
        if not spec.get('login_url'):return finish('form_review')
        browser.open(spec['login_url'],origin);page,schema=browser.read('login',spec['login_url']);result['pages'].append(page)
        page,schema,hold=human_hold(page,schema,'login',spec['login_url'])
        if hold:return finish(hold,human_stage='login')
        if schema.get('state')!='measured':return finish('form_review')
        if not same_origin(schema['form']['action'],origin) or schema['form']['method']!='post':return finish('permission_hold')
        missing=browser.fill(schema,profile,'login')
        if missing:return finish('needs_profile',missing=missing)
        result['login_submitted']=True;journal(finish('login_uncertain'))
        browser.submit(schema,origin)
        if not browser.logged_in():return finish('waiting_email' if browser.needs_email() else 'login_rejected')
    result['login_verified']=True
    browser.open(spec['write_url'],origin);page,schema=browser.read('write',spec['write_url']);result['pages'].append(page)
    hold=blocked(page,'write')
    if hold:return finish(hold,human_stage='write')
    controls=browser.write_controls(schema)
    result['visible_write_roles']=controls
    result['original_board_match']=board_key(page['url'])==board_key(spec['write_url'])
    result['authenticated_write_verified']=result['original_board_match'] and schema.get('state')=='measured' and {'title','content'}.issubset(controls)
    return finish('authenticated_write_verified' if result['authenticated_write_verified'] else 'authenticated_write_review')


def step(c,folder,heartbeat):
    init(c)
    row=c.execute("SELECT id,spec,secret,result FROM account_jobs WHERE state='queued' ORDER BY updated_at LIMIT 1").fetchone()
    if not row:return False
    jid,raw,token,previous=row;spec=json.loads(raw);prior=json.loads(previous)
    c.execute("UPDATE account_jobs SET state='running',updated_at=? WHERE id=?",(now(),jid));c.commit()
    result={}
    def journal(value):
        nonlocal result
        result=value;c.execute('UPDATE account_jobs SET result=?,updated_at=? WHERE id=?',(json.dumps(value,ensure_ascii=False),now(),jid));c.commit()
    try:
        profile=json.loads(cipher(folder).decrypt(token.encode()))
        def persist_profile(value):
            c.execute('UPDATE account_jobs SET secret=? WHERE id=?',(seal_profile(folder,value),jid));c.commit()
        with Browser(folder,lambda **kw:heartbeat(domain=host(spec['write_url']),account_job=jid,**kw)) as browser:
            result=execute(spec,profile,prior,spec.get('terms_approval'),browser,journal,persist_profile)
    except Exception as exc:
        result={**result,'state':'signup_uncertain' if result.get('signup_submitted') and not result.get('signup_verified') else 'login_uncertain' if result.get('login_submitted') else 'connection_review',
                'error_type':type(exc).__name__,'checked_at':now(),'new_posts_submitted':0,'permission_changed':False}
    c.execute('UPDATE account_jobs SET state=?,result=?,updated_at=? WHERE id=?',(result['state'],json.dumps(result,ensure_ascii=False),now(),jid));c.commit()
    old=c.execute('SELECT record FROM review_checkpoints WHERE domain=?',(host(spec['write_url']),)).fetchone()
    if old:
        record=json.loads(old[0]);record['account_verification']={k:result.get(k) for k in ('state','checked_at','signup_verified','login_verified','authenticated_write_verified')}
        record['return_to_write']={'urls':[spec['write_url']],'authenticated_recheck_done':bool(result.get('login_verified') and any(p.get('stage')=='write' for p in result.get('pages',[])))}
        c.execute('UPDATE review_checkpoints SET record=?,updated_at=? WHERE domain=?',(json.dumps(record,ensure_ascii=False),now(),host(spec['write_url'])));c.commit()
    return True


def register(app,ctx,folder):
    from flask import jsonify,request,session,render_template_string
    import sqlite3
    from contextlib import contextmanager
    @contextmanager
    def connection():
        folder.mkdir(parents=True,exist_ok=True)
        c=sqlite3.connect(folder/'jobs.sqlite3',timeout=30);init(c)
        try:
            yield c;c.commit()
        except Exception:
            c.rollback();raise
        finally:c.close()
    def csrf():return bool(session.get('verification_csrf') and secrets.compare_digest(str(request.headers.get('X-Verification-CSRF','')),session['verification_csrf']))
    @app.route('/site-verification-account',methods=['GET'])
    def account_page():
        session.setdefault('verification_csrf',secrets.token_urlsafe(32))
        return render_template_string(ACCOUNT_PAGE,csrf=session['verification_csrf'])
    @app.route('/api/site-verification-account',methods=['GET'])
    def account_status():
        domain=str(request.args.get('domain',''))[:150]
        with connection() as c:
            checkpoint=c.execute('SELECT record FROM review_checkpoints WHERE domain=?',(domain,)).fetchone()
            row=c.execute('SELECT id,spec,state,result,updated_at FROM account_jobs WHERE domain=? ORDER BY updated_at DESC LIMIT 1',(domain,)).fetchone()
            private=c.execute('SELECT secret FROM account_jobs WHERE domain=? ORDER BY updated_at DESC LIMIT 1',(domain,)).fetchone()
        record=json.loads(checkpoint[0]) if checkpoint else {};urls=record.get('saved_urls',[])+record.get('original_write_urls',[])
        writing=next((u for u in urls if re.search(r'write|dispBoardWrite|mod=editor',u,re.I) and not re.search(r'[?&](wr_id|document_srl)=',u)),'')
        signup=next(iter(record.get('signup',{}).get('urls',[])),'');login=next(iter(record.get('login',{}).get('urls',[])),'')
        if writing:
            origin=urlsplit(writing).scheme+'://'+urlsplit(writing).netloc
            # Only measured routes are offered; no guessed login/signup endpoint.
            signup=signup if same_origin(signup,origin) else '';login=login if same_origin(login,origin) else ''
        stored=False
        if private:
            profile=json.loads(cipher(folder).decrypt(private[0].encode()));stored=bool(profile.get('id') and profile.get('password'))
        if writing and ctx.get('load_sites'):
            stored=stored or any(host(s.get('site_url',''))==domain and s.get('mb_id') and s.get('mb_pass') for s in ctx['load_sites']())
        job=None
        if row:
            job={'id':row[0],'spec':{k:v for k,v in json.loads(row[1]).items() if k!='terms_approval'},'state':row[2],'result':json.loads(row[3]),'updated_at':row[4]}
        cfg=ctx.get('load_config',lambda:{})()
        captcha={'configured':bool(cfg.get('twocaptcha_enabled') and cfg.get('twocaptcha_api_key')),'image_estimate_usd':float(cfg.get('twocaptcha_price_image_usd') or .0005),'max_tasks_per_approval':1}
        return jsonify(domain=domain,routes={'write_url':writing,'signup_url':signup,'login_url':login},job=job,labels=LABELS,stored_account_available=bool(stored),captcha=captcha)
    @app.route('/api/site-verification-account',methods=['POST'])
    def account_action():
        if not csrf():return jsonify(error='화면을 새로 열어주세요.'),403
        data=request.get_json(silent=True) or {};action=data.get('action')
        try:
            with connection() as c:
                if action in ('start','inspect_signup'):
                    if action=='start' and data.get('authorized') is not True:return jsonify(error='표시된 사이트로 계정 정보를 전송하는 데 동의해주세요.'),400
                    spec=validate_spec(data.get('spec') or {})
                    if action=='inspect_signup':spec['mode']='signup'
                    profile=data.get('profile') or {}
                    if action=='start' and spec['mode']=='signup':profile['_auto_credentials']=True
                    if data.get('use_saved') is True:
                        sites=ctx.get('load_sites',lambda:[])();s=next((s for s in sites if host(s.get('site_url',''))==host(spec['write_url']) and s.get('mb_id') and s.get('mb_pass')),None)
                        if s:profile={'id':s['mb_id'],'password':s['mb_pass']}
                        else:
                            for raw_spec,secret in c.execute('SELECT spec,secret FROM account_jobs WHERE domain=? ORDER BY updated_at DESC',(host(spec['write_url']),)):
                                if json.loads(raw_spec)['origin']!=spec['origin']:continue
                                candidate=json.loads(cipher(folder).decrypt(secret.encode()))
                                if candidate.get('id') and candidate.get('password'):profile=candidate;break
                            else:raise ValueError('이 사이트에 저장된 계정이 없습니다.')
                        spec['mode']='login'
                    resume=str(data.get('resume_id') or '')
                    row=c.execute('SELECT spec,secret,state,result FROM account_jobs WHERE id=?',(resume,)).fetchone() if resume else None
                    if row:
                        oldspec=json.loads(row[0]);prior=json.loads(row[3])
                        if row[2] in ('queued','running','authenticated_write_verified') or oldspec['write_url']!=spec['write_url']:raise ValueError('현재 요청은 이 입력으로 다시 시작할 수 없습니다.')
                        if spec['mode']=='signup' and prior.get('signup_submitted') and not prior.get('signup_verified'):raise ValueError('이전 가입 결과를 확인한 뒤 기존 계정 로그인으로 진행해주세요.')
                        oldprofile=json.loads(cipher(folder).decrypt(row[1].encode()));oldprofile.update({k:v for k,v in profile.items() if v})
                        oldspec.update(spec);jid=resume
                        c.execute("UPDATE account_jobs SET spec=?,secret=?,state='queued',updated_at=? WHERE id=?",(json.dumps(oldspec),seal_profile(folder,oldprofile),now(),jid));c.commit()
                    else:jid=enqueue(c,folder,spec,profile)
                elif action=='approve_terms':
                    row=c.execute('SELECT spec,state,result FROM account_jobs WHERE id=?',(str(data.get('id','')),)).fetchone()
                    if not row or row[1]!='waiting_terms':raise ValueError('현재 약관 승인 대기 상태가 아닙니다.')
                    spec=json.loads(row[0]);terms=json.loads(row[2]).get('terms',{})
                    if not data.get('fingerprint') or not secrets.compare_digest(str(data['fingerprint']),str(terms.get('fingerprint',''))):raise ValueError('약관 내용이 변경됐습니다. 다시 확인해주세요.')
                    spec['terms_approval']={'fingerprint':terms['fingerprint'],'at':time.time()}
                    jid=str(data['id']);c.execute("UPDATE account_jobs SET spec=?,state='queued',updated_at=? WHERE id=?",(json.dumps(spec),now(),jid));c.commit()
                elif action in ('approve_image_captcha','continue_login'):
                    jid=str(data.get('id',''));row=c.execute('SELECT spec,state,result,secret FROM account_jobs WHERE id=?',(jid,)).fetchone()
                    if not row or row[1] in ('queued','running','authenticated_write_verified'):raise ValueError('현재 이어서 진행할 수 있는 상태가 아닙니다.')
                    spec=json.loads(row[0]);result=json.loads(row[2])
                    if action=='approve_image_captcha':
                        cfg=ctx.get('load_config',lambda:{})()
                        if row[1]!='waiting_human' or result.get('human_stage') not in ('signup','login') or result.get('captcha_attempts'):raise ValueError('승인할 이미지 CAPTCHA 단계가 아닙니다.')
                        page=next((p for p in reversed(result.get('pages',[])) if p.get('stage')==result['human_stage']),{})
                        if not page.get('captcha_image_observed') or not page.get('captcha_input_empty') or (page.get('challenge_widget_present') and not page.get('challenge_response_present')):raise ValueError('현재 사람 확인 유형은 자동 이미지 처리 대상이 아닙니다.')
                        if not cfg.get('twocaptcha_enabled') or not cfg.get('twocaptcha_api_key'):raise ValueError('설정에서 2Captcha 연결을 먼저 확인해주세요.')
                        if not data.get('cost_approved'):raise ValueError('이미지 전달과 유료 요청 1회에 동의해주세요.')
                        spec['captcha_approval']={'stage':result['human_stage'],'at':time.time(),'max_tasks':1,'estimated_cost_usd':float(cfg.get('twocaptcha_price_image_usd') or .0005)}
                    else:
                        profile=json.loads(cipher(folder).decrypt(row[3].encode()))
                        if not profile.get('id') or not profile.get('password'):raise ValueError('사용할 아이디·비밀번호를 먼저 입력해주세요.')
                        spec['mode']='login'
                    c.execute("UPDATE account_jobs SET spec=?,state='queued',updated_at=? WHERE id=?",(json.dumps(spec),now(),jid));c.commit()
                else:raise ValueError('지원하지 않는 작업입니다.')
            return jsonify(ok=True,id=jid)
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='요청을 저장하지 못했습니다. 중복 제출하지 말고 상태를 확인해주세요.'),409


ACCOUNT_PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>가입·로그인 후 작성 화면 검증</title><style>body{font:16px/1.6 system-ui;background:#0b1018;color:#e6ecf6;margin:24px}main{max-width:1000px;margin:auto}section{background:#121d2c;padding:20px;margin:16px 0;border:1px solid #2d4057;border-radius:12px}a{color:#8bb7ff}label{display:block;margin:10px 0}input,select,button{padding:10px;max-width:95%;box-sizing:border-box}input[type=url]{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}button{cursor:pointer}</style><main><a href="/site-verification-checkpoints">전체 단계 대기표</a><h1>가입·로그인 후 원래 글쓰기 화면 검증</h1><p>검증 워커 1개가 순서대로 처리합니다. 입력 정보는 선택한 HTTPS 사이트에만 전송하고 서버에 암호화 저장합니다. 실제 글 제출은 하지 않으며 홍보 허용·발행 성공 판정은 별도입니다.</p><section><label>도메인 <input id="domain"></label><button onclick="load(true)">경로·상태 불러오기</button><label>원래 글쓰기 주소 <input type="url" id="write_url"></label><label>측정된 가입 주소 <input type="url" id="signup_url"></label><label>측정된 로그인 주소 <input type="url" id="login_url"></label><label>진행 방법 <select id="mode" onchange="modeChanged()"><option value="login">기존 계정 로그인</option><option value="signup">새 계정 가입 후 로그인</option></select></label><p id="stored"></p><button onclick="start(true)">이 사이트에 저장된 계정으로 검증</button></section><section><h2>사용할 계정 정보</h2><p>현재 운영 사이트의 로그인 정보와 구분해, 해당 대상 사이트에 가입한 계정을 입력하세요. 기존 계정은 아이디·비밀번호만 입력하세요. 새 가입의 아이디·비밀번호는 비워 두면 측정된 규칙에 맞춰 생성·암호화 저장합니다. 이름·수신 가능한 이메일 등 사이트가 요구하는 본인 정보는 필요합니다. 수신 불가능한 이메일이나 타인의 연락처를 임의로 만들지 않습니다.</p><label>아이디 <input id="account_id" autocomplete="off" readonly onfocus="this.removeAttribute('readonly')"></label><label>비밀번호 <input id="password" type="password" autocomplete="new-password" readonly onfocus="this.removeAttribute('readonly')"></label><div id="signup_fields" hidden><label>이름 <input id="name"></label><label>닉네임 <input id="nickname"></label><label>본인 이메일 <input id="email" type="email"></label><label>수신 가능한 이메일 별칭 규칙(선택 · 본인이 관리하는 주소만)<input id="email_pattern" placeholder="my+{tag}@example.com"></label><label>본인 연락처(필요한 경우) <input id="phone"></label></div><label><input id="authorized" type="checkbox"> 위 계정 정보를 표시된 사이트에 전송·저장하고 검증 워커에서 가입 또는 로그인하도록 요청합니다.</label><button onclick="start(false)">계정 저장 · 워커 검증 요청</button> <button onclick="inspectSignup()">계정 입력 없이 가입 절차 먼저 확인</button></section><section><h2>워커 결과</h2><div id="state">사이트를 불러오세요.</div><div id="terms"></div><details><summary>자세한 검증 기록</summary><pre id="result"></pre></details></section></main><script>const csrf={{csrf|tojson}};let job=null,captchaInfo={};const $=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));function spec(){return {write_url:$('write_url').value,signup_url:$('signup_url').value,login_url:$('login_url').value,mode:$('mode').value}}async function send(data){const r=await fetch('/api/site-verification-account',{method:'POST',headers:{'Content-Type':'application/json','X-Verification-CSRF':csrf},body:JSON.stringify(data)});const d=await r.json();if(!r.ok){$('state').textContent=d.error;return false}$('password').value='';await load(false);return true}function modeChanged(){$('signup_fields').hidden=$('mode').value!=='signup';load(false)}async function start(saved){if(!$('authorized').checked){$('state').textContent='계정 정보 전송·저장 동의를 체크해주세요.';return}await send({action:'start',resume_id:job&&job.spec.write_url===spec().write_url&&!['authenticated_write_verified','login_rejected'].includes(job.state)?job.id:'',spec:spec(),authorized:true,use_saved:saved,profile:{id:$('account_id').value,password:$('password').value,name:$('name').value,nickname:$('nickname').value,email:$('email').value,email_pattern:$('email_pattern').value,phone:$('phone').value}})}async function inspectSignup(){await send({action:'inspect_signup',spec:spec()})}async function approve(){if(job)await send({action:'approve_terms',id:job.id,fingerprint:job.result.terms.fingerprint})}async function approveImage(){if(job)await send({action:'approve_image_captcha',id:job.id,cost_approved:true})}async function continueLogin(){if(job)await send({action:'continue_login',id:job.id})}async function load(routes){const r=await fetch('/api/site-verification-account?domain='+encodeURIComponent($('domain').value.trim()));if(!r.ok){$('state').textContent='상태를 불러오지 못했습니다.';return}const d=await r.json();if(routes)for(const k of ['write_url','signup_url','login_url'])$(k).value=d.routes[k]||'';$('stored').textContent=d.stored_account_available?'이 사이트의 저장된 계정이 있습니다.':'이 사이트의 저장된 계정이 없습니다.';job=d.job;captchaInfo=d.captcha||{};$('state').textContent=job?(job.state==='waiting_terms'&&$('mode').value==='login'?'기존 계정 로그인 선택: 위에 해당 사이트 아이디·비밀번호를 입력하고 검증을 요청하세요.':d.labels[job.state]||job.state):'아직 계정 검증 요청이 없습니다.';$('terms').innerHTML=job?.state==='waiting_terms'&&$('mode').value==='signup'?'<h3>이번 사이트에서 요청한 약관</h3><p>'+esc(job.result.terms.url)+'</p><pre>'+esc(job.result.terms.text)+'</pre><p>위 약관과 개인정보 처리 동의 항목 '+esc(job.result.terms.names.join(', '))+'에 동의하는 경우에만 진행합니다.</p><button onclick="approve()">위 약관에 동의하고 이 요청 계속</button>':'';if(job?.state==='waiting_human'&&['signup','login'].includes(job.result.human_stage)&&!job.result.captcha_attempts&&captchaInfo.configured&&(job.result.pages||[]).some(p=>p.stage===job.result.human_stage&&p.captcha_image_observed&&p.captcha_input_empty&&(!p.challenge_widget_present||p.challenge_response_present)))$('terms').innerHTML+='<p>이 사이트의 이미지 CAPTCHA만 2Captcha에 전달해 1회 처리합니다. 설정 단가 기준 예상 $'+esc(captchaInfo.image_estimate_usd)+'이며 실제 비용은 제공자 응답으로 기록합니다. Cloudflare 접근 차단·권한 제한은 우회하지 않습니다.</p><button onclick="approveImage()">이미지 전달·유료 처리 1회 승인</button>';if(job&&['waiting_human','waiting_email','signup_uncertain','needs_profile'].includes(job.state))$('terms').innerHTML+='<p>사이트에서 직접 가입·필요한 인증을 완료한 경우:</p><button onclick="continueLogin()">가입·인증 완료 · 저장 계정으로 서버 로그인 재검증</button>';$('result').textContent=job?JSON.stringify(job.result,null,2):''}$('domain').value=new URLSearchParams(location.search).get('domain')||'';if($('domain').value)load(true);setInterval(()=>{if($('domain').value)load(false)},5000);</script></html>'''
