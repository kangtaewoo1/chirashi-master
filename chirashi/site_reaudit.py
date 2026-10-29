"""Checkpointed GET-only re-audit. Never registers, logs in, submits, solves challenges or revives sites."""
import argparse
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
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or port not in (None,80,443):return ''
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
            if not url:continue
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
        domain=str(value).lower().removeprefix('www.')
        if not clean_url('https://'+domain):continue
        e=domains.setdefault(domain,{'domain':domain,'urls':['https://'+domain+'/'],'records':[]})
        e['rejected']=True;e['old_domain_reject_reason']=old_reasons.get(domain,'reason_not_retained')
    return sorted(domains.values(),key=lambda e:(not any(r['source']=='registered' for r in e['records']),e['rejected'],e['domain']))

def view_page(response,kind='write'):
    result={k:v for k,v in response.items() if k!='html'}
    html=response.get('html','')
    if not html:return result
    parser=Parser();parser.feed(html);parser.close()
    schema=measure_html(html,response['url'],'signup' if kind=='signup' else 'write')
    fields=(schema.get('form') or {}).get('fields',[])
    result.update(form_state=schema['state'],fingerprint=schema['fingerprint'],human_verification=human_challenge_present(html),
        access_reason=access_reason(html),title=next((n.text()[:160] for n in parser.nodes if n.tag=='title'),''),
        fields=[{k:f.get(k) for k in ('name','role','required','required_source','type','selector')} for f in fields],
        unresolved=schema.get('unresolved',[]),login_controls=any(n.attrs.get('type')=='password' and re.search(r'mb_password|passwd|password',n.attrs.get('name',''),re.I) for n in parser.nodes),
        promotion_allowed_hint=bool(re.search(r'(광고|홍보).{0,12}(허용|가능|등록하세요|환영)',html)),
        promotion_banned_hint=bool(re.search(r'(광고|홍보).{0,12}(금지|불가|삭제|차단)',html)),
        identity_verification_hint=bool(re.search(r'본인인증|휴대폰.{0,5}인증|아이핀|i-PIN',html,re.I)),
        email_verification_hint=bool(re.search(r'이메일.{0,8}인증|메일.{0,8}인증',html)),
        oauth_providers=[x for x in ('google','kakao','naver','github') if re.search(r'(oauth|auth|login).{0,100}'+x+'|'+x+r'.{0,100}(oauth|auth|login)',html,re.I)])
    return result

def audit(entry):
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
        u=record['url'];part=urlsplit(u);q=parse_qs(part.query);origin=part.scheme+'://'+part.netloc
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
    login=any(p.get('form_state')=='login_required' or (p.get('login_controls') and p['stage']=='write') for p in pages)
    if measured and not signals:category='write_form_observed_not_publish_verified'
    elif 'human_verification_required' in signals:category='human_verification_required'
    elif login:category='login_required'
    elif any(p.get('http',0)>=400 for p in pages):category='http_error'
    elif any(p.get('state')=='tls_validation_failed' for p in pages):category='tls_validation_failed'
    elif not markup:category=pages[0].get('state','unavailable')
    elif signals:category='policy_or_access_review'
    elif not write_urls:category='write_route_not_observed'
    else:category='write_form_not_observed'
    next_action={'write_form_observed_not_publish_verified':'Review permission and missing fields before any single live publishing test.',
                 'human_verification_required':'Owner must complete site challenge; no automatic solving.',
                 'login_required':'Check approved account and normal login flow.',
                 'tls_validation_failed':'Repair site certificate or wait; do not bypass TLS.',
                 'write_route_not_observed':'Inspect site navigation or supported adapter before retry.',
                 'policy_or_access_review':'Review site policy or access restriction before any publishing.'}.get(category,'Inspect measured errors; retry only after a relevant change.')
    return {'domain':domain,'checked_at':now(),'category':category,'signals':signals,'next_action':next_action,'pages':pages,
        'elapsed_ms':int((time.monotonic()-started)*1000),'requests':request_count,'url_checks':len(visited),'bytes':sum(p.get('bytes',0) for p in pages),
        'write_form_observed':measured,'old_rejected':entry.get('rejected',False),'new_posts_submitted':0,'login_submitted':False,
        'signup_submitted':False,'permission_changed':False,'paid_api_calls':0,'existing_post_is_new_publish_proof':False}

def database(path):
    c=sqlite3.connect(path);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE IF NOT EXISTS jobs(domain TEXT PRIMARY KEY, position INTEGER, input TEXT, state TEXT, result TEXT, started TEXT, finished TEXT)');return c

def run(folder,workers):
    assert fcntl is not None,'Linux audit runner required'
    folder.mkdir(parents=True,exist_ok=True);lock=(folder/'run.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    c=database(folder/'audit.sqlite3');entries=snapshot(ROOT/'data')
    if not c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]:
        for i,e in enumerate(entries):c.execute('INSERT INTO jobs VALUES(?,?,?,\'pending\',NULL,NULL,NULL)',(e['domain'],i,json.dumps(e,ensure_ascii=False)))
        c.commit();(folder/'manifest.json').write_text(json.dumps({'created_at':now(),'domains':len(entries),'mode':'GET-only; no account or publication changes','workers':workers},ensure_ascii=False,indent=2))
    c.execute("UPDATE jobs SET state='pending' WHERE state='running'");c.commit();started=time.monotonic();last_write=0
    def progress(force=False):
        nonlocal last_write
        if not force and time.monotonic()-last_write<4:return
        counts=dict(c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'));categories={};requests_count=0
        for raw, in c.execute("SELECT result FROM jobs WHERE state='done'"):
            row=json.loads(raw);categories[row['category']]=categories.get(row['category'],0)+1;requests_count+=row['requests']
        result={'updated_at':now(),'total':sum(counts.values()),'completed':counts.get('done',0),'running':counts.get('running',0),'pending':counts.get('pending',0),'categories':categories,'requests':requests_count,'paid_api_calls':0,'new_posts_submitted':0,'elapsed_seconds':round(time.monotonic()-started),'state':'complete' if counts.get('done',0)==sum(counts.values()) else 'running'}
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
                try:result=future.result()
                except Exception as exc:result={'domain':domain,'checked_at':now(),'category':'worker_error','error_type':type(exc).__name__,'requests':0,'pages':[]}
                c.execute("UPDATE jobs SET state='done',result=?,finished=? WHERE domain=?",(json.dumps(result,ensure_ascii=False),now(),domain));c.commit()
            fill();progress()
    progress(True)
    with (folder/'results.jsonl').open('w') as f:
        for row, in c.execute('SELECT result FROM jobs ORDER BY position'):f.write(row+'\n')
    c.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--folder',default='/opt/chirashi/data/site_reaudit_20261030');parser.add_argument('--workers',type=int,default=8);args=parser.parse_args();run(Path(args.folder),max(1,min(8,args.workers)))
