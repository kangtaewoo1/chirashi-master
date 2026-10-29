"""Checkpointed GET-only re-audit. Never registers, logs in, submits, solves challenges or revives sites."""
import argparse
from contextlib import closing
import concurrent.futures
try:
    import fcntl
except ImportError:
    fcntl=None
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import statistics
import threading
import time
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlunsplit,urljoin,parse_qs,urlencode
import requests
from form_schema import Parser,measure_html,human_challenge_present
from cafe24_gate import access_reason

ROOT=Path('/opt/chirashi')
QUERY_KEYS={'bo_table','board_no','mid','page_id','kboard_id','mod','act','wr_id','document_srl','no'}

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def clean_url(value):
    try:
        p=urlsplit(str(value));port=p.port
    except ValueError:return ''
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or (port is not None and not 1<=port<=65535):return ''
    if re.search(r'delete|logout|remove|update|check|agree|confirm|unsubscribe',p.path,re.I):return ''
    q=parse_qs(p.query)
    if any(re.search(r'delete|logout|remove|update|proc.*insert|agree',v,re.I) for v in q.get('act',[])+q.get('mod',[])):return ''
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode({k:v[0] for k,v in q.items() if k in QUERY_KEYS}),''))

def host(url):return (urlsplit(url).hostname or '').lower().removeprefix('www.')
def safe_public(url,resolver=socket.getaddrinfo):
    if not clean_url(url):raise ValueError('unsafe_url')
    p=urlsplit(url)
    addresses=resolver(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):raise ValueError('nonpublic_address')

def fetch(url,session,domain):
    started=time.monotonic();size=0
    for _ in range(4):
        safe_public(url)
        if host(url)!=domain:return {'state':'different_domain_redirect','url':clean_url(url),'elapsed_ms':int((time.monotonic()-started)*1000)}
        r=session.get(url,timeout=(4,8),allow_redirects=False,stream=True)
        try:
            if r.is_redirect:
                target=urljoin(url,r.headers.get('Location',''))
                if not clean_url(target):return {'state':'unsafe_redirect','http':r.status_code,'url':clean_url(url)}
                url=target;continue
            chunks=[]
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>750000:return {'state':'response_too_large','http':r.status_code,'url':clean_url(url),'bytes':size}
                chunks.append(chunk)
            raw=b''.join(chunks);head=r.headers.get('Content-Type','')
            if head and not any(x in head.lower() for x in ['html','text','xhtml']):return {'state':'non_html','http':r.status_code,'url':clean_url(url)}
            match=re.search(br'charset\s*=\s*["\x27]?\s*([\w-]+)',raw[:8192],re.I)
            encoding=(match.group(1).decode() if match else r.encoding) or 'utf-8'
            if encoding.lower() in ('euc-kr','ks_c_5601-1987','euc_kr'):encoding='cp949'
            try:html=raw.decode(encoding,errors='replace')
            except LookupError:encoding='utf-8';html=raw.decode(encoding,errors='replace')
            return {'state':'http_response','http':r.status_code,'url':clean_url(url),'encoding':encoding,'bytes':size,'elapsed_ms':int((time.monotonic()-started)*1000),'html':html}
        finally:r.close()
    return {'state':'redirect_limit','url':clean_url(url)}

def snapshot(data):
    domains={};rejected=json.loads((data/'rejected_domains.json').read_text());rejected={'domains':rejected} if isinstance(rejected,list) else rejected
    old_reasons={x.get('domain'):str(x.get('reason',''))[:160] for x in rejected.get('log',[])}
    for source,name in [('registered','sites.json'),('candidate','candidates.json')]:
        for row in json.loads((data/name).read_text()):
            url=clean_url(row.get('site_url') or row.get('url') or '')
            if not url:
                raw=str(row.get('site_url') or row.get('url') or '')
                try: domain=host(raw)
                except ValueError: domain=''
                if domain:
                    e=domains.setdefault(domain,{'domain':domain,'urls':[],'records':[],'rejected':False})
                    e['records'].append({'id':row.get('id'),'source':source,'status':row.get('status'),'invalid_saved_url':True})
                continue
            domain=host(url);entry=domains.setdefault(domain,{'domain':domain,'urls':[],'records':[],'rejected':False})
            for candidate in [url,clean_url(row.get('write_url') or '')]:
                if candidate and host(candidate)==domain and candidate not in entry['urls']:entry['urls'].append(candidate)
            entry['records'].append({'id':row.get('id'),'source':source,'status':row.get('status'),'platform':row.get('platform'),
                'bo_table':row.get('bo_table'),'url':url,'has_saved_account':bool(row.get('mb_id')),
                'permission_saved':bool(row.get('permission')),'old_audit_state':(row.get('unattended_audit') or {}).get('state'),
                'old_login_required':bool(row.get('login_required')),'old_captcha':bool(row.get('has_captcha')),
                'old_reject_reason':str(row.get('reject_reason') or '')[:160],
                'verified_post_url':clean_url(row.get('verified_post_url') or '')})
    for value in rejected.get('domains',[]):
        url=clean_url('https://'+str(value).strip()+'/')
        if not url:
            domain='invalid-record-'+hashlib.sha256(str(value).encode()).hexdigest()[:12]
            e=domains.setdefault(domain,{'domain':domain,'urls':[],'records':[]})
            e['invalid_saved_address']=True
        else:
            domain=host(url)
            e=domains.setdefault(domain,{'domain':domain,'urls':[url],'records':[]})
        e['rejected']=True;e['old_domain_reject_reason']=old_reasons.get(str(value),old_reasons.get(domain,'reason_not_retained'))
    return sorted(domains.values(),key=lambda e:(not any(r['source']=='registered' for r in e['records']),e['rejected'],e['domain']))

def view_page(response,kind='write'):
    result={k:v for k,v in response.items() if k!='html'}
    html=response.get('html','')
    if not html:return result
    parser=Parser();parser.feed(html);parser.close()
    schema=measure_html(html,response['url'],kind if kind in ('signup','login') else 'write')
    fields=(schema.get('form') or {}).get('fields',[])
    result.update(form_state=schema['state'],fingerprint=schema['fingerprint'],human_verification=human_challenge_present(html),
        access_reason=access_reason(html),title=next((n.text()[:160] for n in parser.nodes if n.tag=='title'),''),
        fields=[{k:f.get(k) for k in ('name','role','required','required_source','type','selector','label','minlength','maxlength','pattern','text_constraints')} for f in fields],
        unresolved=schema.get('unresolved',[]),login_controls=any(n.attrs.get('type')=='password' and re.search(r'mb_password|passwd|password',n.attrs.get('name',''),re.I) for n in parser.nodes),
        promotion_allowed_hint=bool(re.search(r'(광고|홍보).{0,12}(허용|가능|등록하세요|환영)',html)),
        promotion_banned_hint=bool(re.search(r'(광고|홍보).{0,12}(금지|불가|삭제|차단)',html)),
        identity_verification_hint=bool(re.search(r'본인인증|휴대폰.{0,5}인증|아이핀|i-PIN',html,re.I)),
        email_verification_hint=bool(re.search(r'이메일.{0,8}인증|메일.{0,8}인증',html)),
        oauth_providers=[x for x in ('google','kakao','naver','github') if re.search(r'(oauth|auth|login).{0,100}'+x+'|'+x+r'.{0,100}(oauth|auth|login)',html,re.I)])
    return result

def audit(entry):
    if not entry['urls']:
        return {'domain':entry['domain'],'checked_at':now(),'category':'saved_address_requires_review','pages':[],'requests':0,'new_posts_submitted':0,'permission_changed':False,'next_action':'Repair malformed saved address; no unsafe request made.'}
    started=time.monotonic();domain=entry['domain'];pages=[];visited=set();session=requests.Session();session.headers['User-Agent']='Mozilla/5.0 (site diagnostic; GET only)'
    request_count=0
    actual_get=session.get
    def counted_get(*a,**k):
        nonlocal request_count
        request_count+=1
        return actual_get(*a,**k)
    session.get=counted_get
    def read(url,kind):
        if url in visited:return None
        visited.add(url)
        try:r=fetch(url,session,domain);result=view_page(r,kind)
        except requests.exceptions.SSLError:result={'state':'tls_validation_failed','url':clean_url(url)};r={}
        except requests.exceptions.ConnectTimeout:result={'state':'connect_timeout','url':clean_url(url)};r={}
        except requests.exceptions.ReadTimeout:result={'state':'read_timeout','url':clean_url(url)};r={}
        except requests.RequestException:result={'state':'connection_failed','url':clean_url(url)};r={}
        except (ValueError,OSError):result={'state':'address_or_parse_error','url':clean_url(url)};r={}
        except Exception as exc:result={'state':'measurement_error','error_type':type(exc).__name__,'url':clean_url(url)};r={}
        pages.append(dict(result,stage=kind));return r
    first=read(entry['urls'][0],'entry');first=first or {};markup=first.get('html','')
    links=[]
    if markup:
        parser=Parser();parser.feed(markup);parser.close()
        for n in parser.nodes:
            if n.tag=='a':
                u=clean_url(urljoin(first['url'],n.attrs.get('href','')))
                if u and host(u)==domain:links.append((u,n.text()[:60]))
    write_urls=[]
    for record in entry['records']:
        u=record.get('url','');part=urlsplit(u);q=parse_qs(part.query);origin=part.scheme+'://'+part.netloc
        if q.get('bo_table'):
            path=part.path.rsplit('/',1)[0]+'/write.php';write_urls.append(urlunsplit((part.scheme,part.netloc,path,urlencode({'bo_table':q['bo_table'][0]}),'')))
        elif record.get('platform')=='gnuboard' and record.get('bo_table'):write_urls.append(origin+'/bbs/write.php?'+urlencode({'bo_table':record['bo_table']}))
        elif record.get('platform')=='cafe24' and record.get('bo_table'):write_urls.append(origin+'/board/write.html?'+urlencode({'board_no':record['bo_table']}))
        elif q.get('board_no'):write_urls.append(origin+'/board/write.html?'+urlencode({'board_no':q['board_no'][0]}))
    write_urls=[u for u,t in links if re.search(r'(?:/write(?:\.php|\.html)?|dispBoardWrite|mod=editor)',u,re.I) or '글쓰기' in t]+entry['urls'][1:]+write_urls
    for url in list(dict.fromkeys(write_urls)):
        read(url,'write')
    for kind,pattern in [('login',r'login|signin|로그인'),('signup',r'register(?:\.php|\.html)|/join(?:\.html)?|/signup|회원가입')]:
        urls=[u for u,t in links if re.search(pattern,u+' '+t,re.I)]
        if urls:read(urls[0],kind)
    for record in entry['records']:
        u=record.get('verified_post_url')
        if u:read(u,'existing_post')
    session.close()
    writes=[p for p in pages if p['stage']=='write' or (p['stage']=='entry' and p.get('form_state')=='measured')];signals=[]
    access_pages=writes or [p for p in pages if p['stage']=='entry']
    if any(p.get('human_verification') for p in access_pages):signals.append('human_verification_required')
    if any(p.get('access_reason') for p in access_pages):signals.append('access_restriction_observed')
    if any(p.get('promotion_banned_hint') for p in pages):signals.append('promotion_policy_review')
    measured=any(p.get('form_state')=='measured' and p.get('http',0)<400 for p in writes)
    login=any(p.get('form_state')=='login_required' or (p.get('login_controls') and p['stage']=='write') for p in access_pages)
    if measured and not signals:category='write_form_observed_not_publish_verified'
    elif 'human_verification_required' in signals:category='human_verification_required'
    elif login:category='login_required'
    elif any(p.get('http',0)>=400 for p in pages):category='http_error'
    elif any(p.get('state')=='tls_validation_failed' for p in pages):category='tls_validation_failed'
    elif not markup:category=pages[0].get('state','unavailable')
    elif signals:category='policy_or_access_review'
    elif not write_urls:category='write_route_not_observed'
    else:category='write_form_not_observed'
    contact=any(re.search(r'(?:bo_table|mid)=(?:consult|counsel|estimate|inquiry|contact|request|recruit|apply|form0)',p.get('url',''),re.I) for p in writes)
    if category=='write_form_observed_not_publish_verified' and contact:
        category='contact_or_application_form'
    next_action={'contact_or_application_form':'Contact or application form is not proof of a public promotion board; verify purpose first.','write_form_observed_not_publish_verified':'Review permission and missing fields before any single live publishing test.',
                 'human_verification_required':'Owner must complete site challenge; no automatic solving.',
                 'login_required':'Check approved account and normal login flow.',
                 'tls_validation_failed':'Repair site certificate or wait; do not bypass TLS.',
                 'write_route_not_observed':'Inspect site navigation or supported adapter before retry.',
                 'policy_or_access_review':'Review site policy or access restriction before any publishing.'}.get(category,'Inspect measured errors; retry only after a relevant change.')
    return {'domain':domain,'checked_at':now(),'category':category,'signals':signals,'next_action':next_action,'pages':pages,
        'elapsed_ms':int((time.monotonic()-started)*1000),'requests':request_count,'url_checks':len(visited),'bytes':sum(p.get('bytes',0) for p in pages),
        'write_form_observed':measured,'old_rejected':entry.get('rejected',False),'new_posts_submitted':0,'login_submitted':False,
        'signup_submitted':False,'permission_changed':False,'paid_api_calls':0,'existing_post_is_new_publish_proof':False,'public_promotion_permission_verified':False,'account_and_post_stages_pending':True}

def purpose_assessment(row):
    pages=[p for p in row.get('pages',[]) if p.get('stage') in ('entry','write') and p.get('form_state')=='measured']
    for p in pages:
        text=p.get('title','')+' '+p.get('url','')
        if re.search(r'문의|상담|견적|신청|제휴|이력서|고객의.?소리|의회에.?바란다|대관|장애접수|무엇이든.?물어|\bInquiry\b|/contact|/maintenance|/customer/inquiry|(?:bo_table|mid)=(?:consult|counsel|estimate|inquiry|contact|request|recruit|apply|form0)',text,re.I):
            return {'kind':'contact_or_application_form','reason':'문의·상담·신청 목적 신호가 있어 공개 홍보 게시판으로 집계할 수 없습니다.','url':p.get('url'),'title':p.get('title'),'permission_decision':False}
        if re.search(r'후기|리뷰|입학|공지사항|주변관광|질문답변|방명록|(?:bo_table|mid)=(?:review|notice|tour|bo_resume)|/board/product/|[?&]bo_table=.*_qa(?:&|$)',text,re.I):
            return {'kind':'purpose_specific_board','reason':'후기·공지·질문 등 특정 용도 게시판입니다. 홍보 허용은 별도로 확인해야 합니다.','url':p.get('url'),'title':p.get('title'),'permission_decision':False}
    if pages and not any(p.get('stage')=='write' and re.search(r'/write[^/]*|mod=editor|dispBoardWrite',p.get('url',''),re.I) and not re.search(r'오류안내|오류.?페이지|error',p.get('title',''),re.I) for p in pages):
        return {'kind':'embedded_form_requires_review','reason':'홈·기존 글·오류 화면에서 양식이 감지되었습니다. 실제 새 글 작성 화면인지 추가 검토해야 합니다.','permission_decision':False}
    return {'kind':'purpose_not_verified','reason':'양식 존재만으로 홍보 허용이나 발행 성공을 판단하지 않습니다.','permission_decision':False}


def refine_result(row):
    # A site's login page is expected to contain login controls. It does not
    # establish that the independently fetched writing page requires an account.
    if row.get('category')=='login_required':
        pages=row.get('pages',[])
        writes=[p for p in pages if p.get('stage')=='write' or (p.get('stage')=='entry' and p.get('form_state')=='measured')]
        access_pages=writes or [p for p in pages if p.get('stage')=='entry']
        if not any(p.get('form_state')=='login_required' or (p.get('login_controls') and p.get('stage')=='write') for p in access_pages):
            if any(p.get('http',0)>=400 for p in pages):row['category']='http_error'
            elif any(p.get('state')=='tls_validation_failed' for p in pages):row['category']='tls_validation_failed'
            elif row.get('signals'):row['category']='policy_or_access_review'
            elif writes:row['category']='write_form_not_observed'
            else:row['category']='write_route_not_observed'
            row['next_action']='Inspect the writing route itself; login navigation alone is not proof of a login requirement.'
    if row.get('category')=='write_form_observed_not_publish_verified':
        if any(re.search(r'(?:bo_table|mid)=(?:consult|counsel|estimate|inquiry|contact|request|recruit|apply|form0)',p.get('url',''),re.I) for p in row.get('pages',[]) if p.get('stage') in ('entry','write')):
            row['category']='contact_or_application_form'
            row['next_action']='상담·신청 양식의 목적을 확인하세요. 공개 홍보 게시판으로 집계하지 않습니다.'
    if row.get('category')=='write_form_observed_not_publish_verified':
        fields=[f for p in row.get('pages',[]) if p.get('stage') in ('entry','write') and p.get('form_state')=='measured' for f in p.get('fields',[])]
        if any(re.search(r'조합원\s*코드|회원\s*전용\s*코드|초대\s*코드',f.get('label') or '') for f in fields):
            row['category']='membership_code_required';row['next_action']='조합원·회원 전용 코드가 필요합니다. 권한이 있는 사용자만 접근할 수 있으며 일반 발행 대상으로 집계하지 않습니다.'
        elif any(re.search(r'SPAM\s*방지',f.get('label') or '',re.I) and f.get('type') not in ('hidden','submit','button') for f in fields):
            row['category']='human_verification_required';row['next_action']='구형 SPAM 방지 입력칸 감지. 사람 확인 없이 자동 제출하지 않습니다.'
        elif any(f.get('role')=='title' and f.get('type') in ('radio','checkbox','select') for f in fields):
            row['category']='contact_or_application_form';row['next_action']='제목처럼 저장된 필드가 선택 항목입니다. 신청·상담 양식 여부를 확인해야 합니다.'
    if row.get('category') in ('write_form_observed_not_publish_verified','contact_or_application_form'):
        assessment=purpose_assessment(row);row['board_purpose_hint']=assessment
        if assessment['kind']!='purpose_not_verified':
            row['category']=assessment['kind'];row['next_action']=assessment['reason']
    return row


def write_summary(c,folder):
    rows=[];inputs={}
    for domain,raw,inp in c.execute("SELECT domain,result,input FROM jobs WHERE state='done'").fetchall():
        row=refine_result(json.loads(raw));rows.append(row);inputs[domain]=json.loads(inp)
        c.execute('UPDATE jobs SET result=? WHERE domain=?',(json.dumps(row,ensure_ascii=False),domain))
    c.commit();categories={};templates={};priority=[]
    for row in rows:
        key=row['category'];categories[key]=categories.get(key,0)+1
        for page in row.get('pages',[]):
            fields=page.get('fields') or []
            if not fields:continue
            shape=[{k:f.get(k) for k in ('name','role','type','required')} for f in fields]
            signature=hashlib.sha256(json.dumps(shape,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
            t=templates.setdefault(signature,{'fingerprint':signature,'domains':set(),'fields':shape})
            t['domains'].add(row['domain'])
        if key=='write_form_observed_not_publish_verified':
            priority.append({'domain':row['domain'],'previously_rejected':inputs[row['domain']].get('rejected',False),
                'saved_permission':any(x.get('permission_saved') for x in inputs[row['domain']]['records']),
                'has_saved_account':any(x.get('has_saved_account') for x in inputs[row['domain']]['records']),
                'write_pages':[{'url':p['url'],'fields':p.get('fields',[]),'unresolved':p.get('unresolved',[])} for p in row.get('pages',[]) if p.get('stage') in ('write','entry') and p.get('form_state')=='measured'],
                'next_action':'먼저 게시판 목적·홍보 허용·필수 입력칸을 검토한 뒤, 허용된 곳만 1건씩 등록·공개조회 검증'})
    manifest=json.loads((folder/'manifest.json').read_text())
    result={'completed_at':now(),'started_at':manifest.get('created_at'),'domains_measured':len(rows),'registered_domain_records':sum(any(x['source']=='registered' for x in e['records']) for e in inputs.values()),
        'previously_rejected_domains_measured':sum(e.get('rejected',False) for e in inputs.values()),'categories':categories,
        'http_requests':sum(r.get('requests',0) for r in rows),'response_bytes':sum(r.get('bytes',0) for r in rows),
        'median_domain_elapsed_ms':round(statistics.median([r.get('elapsed_ms',0) for r in rows])) if rows else 0,
        'paid_api_calls':0,'new_posts_submitted':0,'signup_submissions':0,'login_submissions':0,'permissions_changed':0,
        'write_forms_pending_publication_verification':len(priority),'rejected_domains_with_write_form_to_review':sum(x['previously_rejected'] for x in priority),
        'scope':'Vultr GET-only live connectivity and form measurements; no signup/login/publishing success inferred',
        'next_priority':'정상 공개 게시판과 상담·신청 양식을 구분하고, 허용된 게시판의 양식 매핑·계정 검증을 먼저 처리',
        'capacity_note':'워커 수 증가는 권한·계정·사람 확인 차단을 해소하지 않습니다. 실제 발행 검증 후 큐 대기·메모리·건당 비용을 보고 증설합니다.'}
    template_rows=[dict(v,domains=sorted(v['domains']),domain_count=len(v['domains'])) for v in templates.values()]
    template_rows.sort(key=lambda x:x['domain_count'],reverse=True)
    for name,value in [('summary.json',result),('review-priority.json',priority),('form-families.json',template_rows)]:
        (folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    report='전체 사이트 재측정 결과\n\n완료: '+result['completed_at']+'\n검사 도메인: '+str(len(rows))+'개\n이전 도메인 탈락 기록 포함: '+str(result['previously_rejected_domains_measured'])+'개\n\n'
    report+='이번 검사는 접속·글쓰기·가입·로그인 화면을 읽어서 구조와 차단 이유를 기록한 것입니다. 실제 신규 가입·로그인·글 등록은 0건이며, 발행 성공 수를 늘리지 않았습니다.\n\n'
    report+='분류별 결과\n'+''.join('- '+k+': '+str(v)+'개\n' for k,v in categories.items())
    report+='\n양식 확인 후 추가 검토 대상: '+str(len(priority))+'개\n유료 API 호출: 0회\nHTTP 요청: '+str(result['http_requests'])+'회\n\n'+result['next_priority']+'\n'+result['capacity_note']+'\n'
    (folder/'report.txt').write_text(report)
    return result


def database(path):
    c=sqlite3.connect(path);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS jobs(domain TEXT PRIMARY KEY, position INTEGER, input TEXT, state TEXT, result TEXT, started TEXT, finished TEXT)');return c

def run(folder,workers):
    assert fcntl is not None,'Linux audit runner required'
    folder.mkdir(parents=True,exist_ok=True);lock=(folder/'run.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    c=database(folder/'audit.sqlite3');entries=snapshot(ROOT/'data')
    if not c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]:
        for i,e in enumerate(entries):c.execute('INSERT INTO jobs VALUES(?,?,?,\'pending\',NULL,NULL,NULL)',(e['domain'],i,json.dumps(e,ensure_ascii=False)))
        c.commit();(folder/'manifest.json').write_text(json.dumps({'created_at':now(),'domains':len(entries),'mode':'GET-only; no account or publication changes','workers':workers},ensure_ascii=False,indent=2))
    for i,e in enumerate(entries):
        old=c.execute('SELECT input FROM jobs WHERE domain=?',(e['domain'],)).fetchone()
        if old:
            previous=json.loads(old[0])
            if set(e['urls'])-set(previous['urls']):
                c.execute("UPDATE jobs SET state='pending',result=NULL WHERE domain=?",(e['domain'],))
            c.execute('UPDATE jobs SET input=? WHERE domain=?',(json.dumps(e,ensure_ascii=False),e['domain']))
        else:c.execute("INSERT INTO jobs VALUES(?,?,?,'pending',NULL,NULL,NULL)",(e['domain'],i,json.dumps(e,ensure_ascii=False)))
    c.commit()
    for domain,raw in c.execute("SELECT domain,result FROM jobs WHERE state='done'").fetchall():
        row=refine_result(json.loads(raw))
        c.execute('UPDATE jobs SET result=? WHERE domain=?',(json.dumps(row,ensure_ascii=False),domain))
    c.execute("UPDATE jobs SET state='pending' WHERE state='running'");c.commit();started=time.monotonic();last_write=0
    def progress(force=False):
        nonlocal last_write
        if not force and time.monotonic()-last_write<4:return
        counts=dict(c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'));categories={};requests_count=0
        for raw, in c.execute("SELECT result FROM jobs WHERE state='done'"):
            row=json.loads(raw);categories[row['category']]=categories.get(row['category'],0)+1;requests_count+=row['requests']
        result={'updated_at':now(),'total':sum(counts.values()),'completed':counts.get('done',0),'running':counts.get('running',0),'pending':counts.get('pending',0),'categories':categories,'requests':requests_count,'paid_api_calls':0,'new_posts_submitted':0,'elapsed_seconds':round((datetime.now(timezone.utc)-datetime.fromisoformat(json.loads((folder/'manifest.json').read_text())['created_at'])).total_seconds()),'state':'complete' if counts.get('done',0)==sum(counts.values()) else 'running'}
        temp=folder/'progress.tmp';temp.write_text(json.dumps(result,ensure_ascii=False,indent=2));temp.replace(folder/'progress.json');last_write=time.monotonic()
    pending=c.execute("SELECT domain,input FROM jobs WHERE state='pending' ORDER BY position").fetchall()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        active={};cursor=iter(pending)
        def fill():
            while len(active)<workers:
                try:domain,raw=next(cursor)
                except StopIteration:break
                c.execute("UPDATE jobs SET state='running',started=? WHERE domain=?",(now(),domain));c.commit();active[pool.submit(audit,json.loads(raw))]=domain
        fill();progress(True)
        while active:
            done,_=concurrent.futures.wait(active,timeout=5,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                domain=active.pop(future)
                try:result=refine_result(future.result())
                except Exception as exc:result={'domain':domain,'checked_at':now(),'category':'worker_error','error_type':type(exc).__name__,'requests':0,'pages':[]}
                c.execute("UPDATE jobs SET state='done',result=?,finished=? WHERE domain=?",(json.dumps(result,ensure_ascii=False),now(),domain));c.commit()
            fill();progress()
    write_summary(c,folder)
    progress(True)
    with (folder/'results.jsonl').open('w') as f:
        for row, in c.execute('SELECT result FROM jobs ORDER BY position'):f.write(row+'\n')
    c.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--folder',default='/opt/chirashi/data/site_reaudit_20261030');parser.add_argument('--workers',type=int,default=8);args=parser.parse_args();run(Path(args.folder),max(1,min(8,args.workers)))

AUDIT_PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>전체 사이트 재측정</title><style>
body{margin:0;background:#0b1018;color:#e4eaf5;font:15px/1.6 system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:28px}a{color:#7eafff}h1{font-size:25px;margin:10px 0}.note{color:#aab7cc}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}.card,section{background:#111a28;border:1px solid #29384c;border-radius:12px;padding:18px}.number{font-size:30px;font-weight:750}progress{width:100%;height:14px;accent-color:#518dff}input,select,button{background:#142136;color:#e4eaf5;border:1px solid #354860;padding:10px;border-radius:7px;font:inherit}input{min-width:260px}.tools{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:12px;text-align:left;border-bottom:1px solid #29384c;vertical-align:top}th{color:#aab7cc}small{color:#9badc5}summary{cursor:pointer;color:#83b1ff}.wrap{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}#categories{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.chip{background:#182841;padding:5px 10px;border-radius:7px}button{cursor:pointer}</style><main>
<a href="/">← 운영 화면</a><h1>전체 사이트 재측정</h1><div class="note">등록·후보·탈락 목록을 합쳐 다시 확인합니다. 현재 단계는 접속과 양식 검사입니다. 가입·로그인·글 제출은 하지 않으며 권한이나 탈락 상태를 자동 변경하지 않습니다.</div>
<div class="cards"><div class="card">전체 도메인<div class="number" id="total">—</div></div><div class="card">검사 완료<div class="number" id="done">—</div></div><div class="card">검사 중 · 대기<div class="number" id="pending">—</div></div><div class="card">유료 API 호출<div class="number">0</div><small>별도 유료 검색·브라우저 사용 안 함</small></div></div>
<progress id="bar" value="0" max="100"></progress><div id="updated" class="note"></div><div id="categories"></div>
<section><div class="tools"><input id="q" placeholder="도메인 검색"><select id="category"><option value="">전체 결과</option></select><button onclick="offset=0;load()">조회</button><a href="/api/site-reaudit/export">검사 결과 다운로드</a> · <a href="/api/site-reaudit/report">완료 보고서</a></div><div class="wrap"><table><thead><tr><th>사이트</th><th>이전 상태</th><th>이번 실측</th><th>다음 조치 · 세부 내역</th></tr></thead><tbody id="rows"></tbody></table></div><div class="tools"><button onclick="offset=Math.max(0,offset-30);load()">이전</button><span id="paging"></span><button onclick="offset+=30;load()">다음</button></div></section></main>
<script>
const names={membership_code_required:'회원·조합원 전용 코드 필요',purpose_specific_board:'특정 용도 게시판 · 홍보 허용 미확인',embedded_form_requires_review:'내장 양식 · 새 글 화면 추가 확인',contact_or_application_form:'상담·신청 양식 · 홍보 대상 미확인',saved_address_requires_review:'저장 주소 수정 필요',write_form_observed_not_publish_verified:'글쓰기 양식 확인 · 발행 미검증',human_verification_required:'사람 확인 필요',login_required:'로그인 필요',http_error:'사이트 응답 오류',tls_validation_failed:'인증서 확인 실패',different_domain_redirect:'다른 도메인으로 이동',connection_failed:'접속 실패',connect_timeout:'접속 시간 초과',read_timeout:'응답 시간 초과',write_route_not_observed:'글쓰기 주소 추가 확인',write_form_not_observed:'글쓰기 양식 추가 확인',policy_or_access_review:'이용 규칙·권한 검토',address_or_parse_error:'주소·이름 해석 확인',response_too_large:'큰 페이지 · 추가 확인',worker_error:'검사 실행 오류',measurement_error:'화면 해석 추가 확인',redirect_limit:'연속 이동 · 추가 확인',http_response:'응답 내용 추가 확인'};
const stages={entry:'기본 접속',write:'글쓰기 화면',login:'로그인 화면',signup:'가입 화면',existing_post:'기존 글 조회'};const forms={measured:'양식 확인',form_unavailable:'양식 미확인',login_required:'로그인 필요',access_denied:'접근 제한',site_database_error:'사이트 DB 오류',http_response:'응답 받음'};
const actions={membership_code_required:'전용 코드가 필요한 권한 제한 · 일반 발행 대상 제외',purpose_specific_board:'후기·공지·질문 등 게시판 용도와 홍보 허용을 확인',embedded_form_requires_review:'댓글·내장 양식과 실제 새 글 화면을 구분해 확인',contact_or_application_form:'상담·신청 양식은 공개 홍보 게시판과 구분해 검토',write_form_observed_not_publish_verified:'홍보 허용과 필수 항목을 검토한 후 1건 발행 검증',human_verification_required:'해당 사이트에서 사람이 확인해야 합니다.',login_required:'허용된 계정으로 정상 로그인 확인',tls_validation_failed:'인증서 문제 해결 후 재검사',write_route_not_observed:'메뉴·동적 화면에서 실제 글쓰기 주소 확인',write_form_not_observed:'동적 화면 또는 게시판 구조 추가 확인',policy_or_access_review:'운영 규칙과 접근 권한을 먼저 확인'};
const esc=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));let offset=0;
async function load(){const q=document.getElementById('q').value,category=document.getElementById('category').value;const r=await fetch('/api/site-reaudit?q='+encodeURIComponent(q)+'&category='+encodeURIComponent(category)+'&offset='+offset);if(!r.ok)return;const d=await r.json(),s=d.progress||{};document.getElementById('total').textContent=s.total??0;document.getElementById('done').textContent=s.completed??0;document.getElementById('pending').textContent=(s.running||0)+'개 · '+(s.pending||0)+'개';document.getElementById('bar').value=s.total?100*s.completed/s.total:0;document.getElementById('updated').textContent=(s.state==='complete'?'전체 접속·양식 검사 완료':'검사 진행 중')+' · 마지막 저장 '+(s.updated_at?new Date(s.updated_at).toLocaleString('ko-KR'):'준비 중')+' · 실제 발행 성공 수와 별도 집계';document.getElementById('categories').innerHTML=Object.entries(s.categories||{}).map(([k,v])=>'<span class="chip">'+esc(names[k]||k)+' '+v+'</span>').join('');const sel=document.getElementById('category');Object.keys(s.categories||{}).forEach(k=>{if(!Array.from(sel.options).some(o=>o.value===k)){const o=new Option(names[k]||k,k);sel.add(o)}});document.getElementById('paging').textContent=(offset+1)+'–'+Math.min(offset+d.items.length,d.filtered_total)+' / '+d.filtered_total;document.getElementById('rows').innerHTML=d.items.map(x=>{const z=x.result||{},e=x.input||{};return '<tr><td><b>'+esc(x.domain)+'</b><br><small>'+esc(e.records?.map(r=>r.source==='registered'?'등록':r.status).filter((v,i,a)=>a.indexOf(v)===i).join(' · ')||'도메인 탈락 목록')+'</small></td><td>'+esc(e.rejected?'도메인 탈락 기록 있음':'도메인 탈락 기록 없음')+'<br><small>'+esc(e.old_domain_reject_reason||e.records?.map(r=>r.old_audit_state||r.old_reject_reason||'').filter(Boolean).join(' · ')||'이유 기록 없음')+'</small></td><td>'+esc(names[z.category]||({'pending':'검사 대기','running':'검사 중'}[x.state])||z.category||x.state)+'<br><small>'+esc(z.checked_at?new Date(z.checked_at).toLocaleString('ko-KR'):'')+' · '+(z.requests||0)+'회 요청</small></td><td>'+esc(actions[z.category]||'접속·양식 증거를 검토한 뒤 다음 검사 결정')+'<details><summary>단계별 증거 '+(z.pages?.length||0)+'개</summary>'+((z.pages||[]).map(p=>'<p><b>'+esc(stages[p.stage]||p.stage)+'</b> · HTTP '+esc(p.http||'—')+' · '+esc(forms[p.form_state||p.state]||p.form_state||p.state)+'<br><a href="'+esc(p.url)+'" target="_blank" rel="noopener">'+esc(p.url)+'</a><br><small>필수 항목: '+esc((p.fields||[]).filter(f=>f.required).map(f=>f.name+' ('+f.role+')').join(', ')||'미확인')+' · 사람 확인: '+(p.human_verification?'감지':'미감지')+'</small></p>').join(''))+'</details></td></tr>'}).join('')}
load();setInterval(load,10000);
</script></html>'''


def register(app,ctx):
    from flask import jsonify,request,Response
    folder=Path(ctx['DATA_DIR'])/'site_reaudit_20261030'
    @app.route('/site-reaudit',methods=['GET'])
    def site_reaudit_page():return AUDIT_PAGE
    @app.route('/api/site-reaudit',methods=['GET'])
    def site_reaudit_status():
        progress=json.loads((folder/'progress.json').read_text()) if (folder/'progress.json').exists() else {}
        path=folder/'audit.sqlite3'
        if not path.exists():return jsonify({'progress':progress,'items':[],'filtered_total':0})
        try:offset=max(0,int(request.args.get('offset',0)))
        except ValueError:offset=0
        q=str(request.args.get('q',''))[:100];category=str(request.args.get('category',''))[:100]
        sql=' WHERE domain LIKE ?';args=['%'+q+'%']
        if category:sql+=" AND json_extract(result,'$.category')=?";args.append(category)
        with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as c:
            total=c.execute('SELECT COUNT(*) FROM jobs'+sql,args).fetchone()[0]
            rows=c.execute('SELECT domain,input,state,result FROM jobs'+sql+' ORDER BY position LIMIT 30 OFFSET ?',args+[offset]).fetchall()
        summary=json.loads((folder/'summary.json').read_text()) if (folder/'summary.json').exists() else None
        items=[{'domain':r[0],'input':json.loads(r[1]),'state':r[2],'result':json.loads(r[3]) if r[3] else None} for r in rows]
        return jsonify({'progress':progress,'items':items,'filtered_total':total,'summary':summary})
    @app.route('/api/site-reaudit/export',methods=['GET'])
    def site_reaudit_export():
        path=folder/'audit.sqlite3'
        if not path.exists():return jsonify({'error':'아직 검사 기록이 없습니다.'}),404
        with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as c:rows=c.execute("SELECT result FROM jobs WHERE state='done' ORDER BY position").fetchall()
        return Response('\n'.join(r[0] for r in rows),mimetype='application/x-ndjson',headers={'Content-Disposition':'attachment; filename=site-reaudit-20261030.jsonl'})

    @app.route('/api/site-reaudit/report',methods=['GET'])
    def site_reaudit_report():
        path=folder/'report.txt'
        if not path.exists():return Response('전체 재측정이 진행 중입니다. 완료되면 이 주소에 종합 결과를 저장합니다.',status=202,mimetype='text/plain')
        return Response(path.read_text(encoding='utf-8'),mimetype='text/plain',headers={'Content-Disposition':'inline; filename=site-reaudit-report.txt'})
