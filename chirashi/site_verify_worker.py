"""One durable navigation/form verification worker. No accounts, POSTs or permission changes."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import heapq
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from site_reaudit import Parser, clean_url, fetch, host, now, view_page

ELIGIBLE = {'write_route_not_observed', 'write_form_not_observed', 'login_required', 'embedded_form_requires_review'}
LABELS = {'pending':'대기', 'running':'검사 중', 'human_review_required':'사람 확인 필요',
          'access_review_required':'권한·이용 목적 검토', 'write_form_observed':'작성 양식 발견·발행 미검증',
          'login_profile_observed':'로그인 화면 확인·로그인 미실행', 'navigation_review_required':'추가 화면 탐색 필요',
          'network_review':'접속 상태 추가 확인', 'worker_error':'검사 오류·추가 확인'}


def database(folder):
    folder.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(folder/'jobs.sqlite3', timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS jobs(domain TEXT PRIMARY KEY,input TEXT,priority INTEGER,state TEXT,result TEXT,started TEXT,finished TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS observations(domain TEXT,result TEXT,saved_at TEXT)')
    return c


def add_job(c, entry, priority=0):
    urls = list(dict.fromkeys(u for value in entry.get('urls', []) if (u:=clean_url(value)) and host(u)==entry['domain']))
    if not urls:return
    # Only diagnostic input; do not copy source credentials or user field values.
    safe = {'domain':entry['domain'], 'urls':urls[:100], 'source':entry.get('source','candidate')}
    old=c.execute('SELECT input,state,result FROM jobs WHERE domain=?',(safe['domain'],)).fetchone()
    if old:
        previous=json.loads(old[0]);fresh=set(safe['urls'])-set(previous['urls'])
        if fresh and len(previous['urls'])<100:
            safe['urls']=list(dict.fromkeys(safe['urls']+previous['urls']))[:100]
            if old[2]:c.execute('INSERT INTO observations VALUES(?,?,?)',(safe['domain'],old[2],now()))
            c.execute("UPDATE jobs SET input=?,priority=MAX(priority,?),state='pending',result=NULL WHERE domain=?",(json.dumps(safe,ensure_ascii=False),priority,safe['domain']))
        return
    c.execute("INSERT OR IGNORE INTO jobs VALUES(?,?,?,'pending',NULL,NULL,NULL)",
              (safe['domain'],json.dumps(safe,ensure_ascii=False),priority))


def seed(c, data):
    prior = data/'site_reaudit_20261030/audit.sqlite3'
    if prior.exists():
        with closing(sqlite3.connect('file:'+str(prior)+'?mode=ro',uri=True)) as old:
            for inp, raw in old.execute("SELECT input,result FROM jobs WHERE state='done'"):
                r=json.loads(raw); e=json.loads(inp)
                if r.get('category') in ELIGIBLE:
                    add_job(c, dict(e,source='reaudit_navigation_followup'),20 if any(x.get('source')=='registered' for x in e.get('records',[])) else 0)
    path=data/'candidates.json'
    if path.exists():
        for r in json.loads(path.read_text(encoding='utf-8')):
            if r.get('status')=='rejected':continue
            u=clean_url(r.get('url') or '')
            if u:
                title=str(r.get('title') or r.get('name') or '')
                priority=100 if re.search(r'홍보\s*게시판|무료\s*홍보|광고\s*게시판',title) else 5
                add_job(c,{'domain':host(u),'urls':[u],'source':'new_candidate'},priority)
    c.commit()


def link_kind(url, label=''):
    path=urlsplit(url).path
    if re.search(r'login|signin|로그인',path+' '+label,re.I):return 'login'
    if re.search(r'register|signup|/join|회원가입',path+' '+label,re.I):return 'signup'
    if re.search(r'write|dispBoardWrite|mod=editor|글쓰기',url+' '+label,re.I):return 'write'
    return 'entry'


def menu_links(markup, base, domain):
    p=Parser();p.feed(markup);p.close();result=[]
    for n in p.nodes:
        if n.tag!='a':continue
        label=n.text()[:100];u=clean_url(urljoin(base,n.attrs.get('href') or ''))
        if not u or host(u)!=domain:continue
        kind=link_kind(u,label)
        if kind=='write':priority=100
        elif re.search(r'홍보\s*게시판|광고\s*게시판|무료\s*홍보',label):priority=90
        elif re.search(r'자유\s*게시판|커뮤니티|게시판|community|forum',label,re.I):priority=60
        elif kind in ('login','signup'):priority=40
        elif re.search(r'/(?:bbs|boards?|community|forum)(?:/|[.?])',urlsplit(u).path,re.I):priority=20
        else:continue
        result.append((priority,u,kind,label))
    return sorted(set(result),reverse=True)[:12]


def inspect(entry, heartbeat=lambda **kw:None, reader=fetch, max_pages=8):
    domain=entry['domain'];pending=[];seen=set();pages=[];requests_count=0
    def push(u, priority=0, kind='entry'):
        if clean_url(u) and host(u)==domain and u not in seen:heapq.heappush(pending,(-priority,u,kind))
    for i,u in enumerate(entry['urls'][:6]):push(u,30-i,link_kind(u))
    origin=urlsplit(entry['urls'][0]);push(origin.scheme+'://'+origin.netloc+'/',5)
    started=time.monotonic();state='navigation_review_required'
    with requests.Session() as session:
        session.headers['User-Agent']='Mozilla/5.0 (site navigation diagnostic; GET only)'
        real_get=session.get
        def counted_get(*args,**kwargs):
            nonlocal requests_count
            requests_count+=1
            return real_get(*args,**kwargs)
        session.get=counted_get
        while pending and len(pages)<max_pages:
            _,url,kind=heapq.heappop(pending)
            if url in seen:continue
            seen.add(url);heartbeat(domain=domain,url=url,pages=len(pages),phase='reading')
            try:
                raw=reader(url,session,domain);page=view_page(raw,kind);page['stage']=kind
                markup=raw.get('html','');p=Parser();p.feed(markup);p.close()
                text=p.root.text()
                page['headings']=[n.text()[:160] for n in p.nodes if n.tag in ('h1','h2','h3')][:12]
                page['restricted_membership_hint']=bool(re.search(r'조합원.{0,10}(코드|가능)|회원\s*전용\s*코드|초대\s*코드',text))
                page['promotion_permission_verified']=False
                pages.append(page)
                if page.get('human_verification'):
                    state='human_review_required';break
                if page.get('access_reason') or page.get('restricted_membership_hint'):
                    state='access_review_required';break
                for priority,target,target_kind,label in menu_links(markup,raw.get('url',url),domain):push(target,priority,target_kind)
            except Exception as exc:
                pages.append({'url':url,'stage':kind,'error_type':type(exc).__name__})
            heartbeat(domain=domain,url=url,pages=len(pages),phase='recording')
    if state not in ('human_review_required','access_review_required'):
        if any(p.get('stage')=='write' and p.get('form_state')=='measured' for p in pages):state='write_form_observed'
        elif any(p.get('stage')=='login' and p.get('form_state')=='measured' for p in pages):state='login_profile_observed'
        elif not any(p.get('http',0) in range(200,400) for p in pages):state='network_review'
    return {'domain':domain,'checked_at':now(),'state':state,'pages':pages,'requests':requests_count,
            'elapsed_ms':round(1000*(time.monotonic()-started)),'max_pages':max_pages,
            'signup_submitted':False,'login_submitted':False,'new_posts_submitted':0,'permission_changed':False,
            'paid_api_calls':0,'publication_verified':False,'auto_ready':False,
            'next_action':{'human_review_required':'사람 확인 대기. 자동 해결·다른 경로 재시도 안 함.',
                           'access_review_required':'전용 권한·게시판 목적을 검토한 뒤 다음 단계 결정.',
                           'write_form_observed':'입력칸과 홍보 허용을 검토한 뒤 별도의 발행·공개조회 검증 필요.',
                           'login_profile_observed':'로그인 양식만 확인했습니다. 허용된 계정의 실제 로그인은 별도 검증해야 합니다.'}.get(state,'메뉴·동적 화면 또는 접속 원인을 추가 확인해야 합니다.')}


def write_status(c,folder,**extra):
    counts=dict(c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'))
    status={'heartbeat_at':now(),'verification_workers':1,'mode':'GET-only menu navigation and form learning',
            'counts':counts,'completed':counts.get('done',0),'pending':counts.get('pending',0),
            'signup_submissions':0,'login_submissions':0,'posts_submitted':0,'paid_api_calls':0,**extra}
    temp=folder/'status.tmp';temp.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(folder/'status.json')


def run(data):
    import fcntl
    folder=data/'site_verification_worker';c=database(folder)
    lock=(folder/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    c.execute("UPDATE jobs SET state='pending' WHERE state='running'");c.commit();last_seed=0
    while True:
        if time.monotonic()-last_seed>300:seed(c,data);last_seed=time.monotonic()
        item=c.execute("SELECT domain,input FROM jobs WHERE state='pending' ORDER BY priority DESC,domain LIMIT 1").fetchone()
        if not item:write_status(c,folder,phase='waiting_for_candidates');time.sleep(10);continue
        domain,raw=item;c.execute("UPDATE jobs SET state='running',started=? WHERE domain=?",(now(),domain));c.commit()
        try:result=inspect(json.loads(raw),lambda **kw:write_status(c,folder,**kw))
        except Exception as exc:result={'domain':domain,'state':'worker_error','error_type':type(exc).__name__,'checked_at':now(),'new_posts_submitted':0}
        c.execute('INSERT INTO observations VALUES(?,?,?)',(domain,json.dumps(result,ensure_ascii=False),now()))
        c.execute("UPDATE jobs SET state='done',result=?,finished=? WHERE domain=?",(json.dumps(result,ensure_ascii=False),now(),domain));c.commit()
        write_status(c,folder,phase='between_candidates',last_domain=domain,last_result=result['state']);time.sleep(2)


PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>사이트 검증 전용 워커</title><style>body{background:#0b1018;color:#e6ecf6;font:16px/1.6 system-ui;margin:28px}main{max-width:1400px;margin:auto}a{color:#8bb7ff}section{background:#121d2c;border:1px solid #2d4057;border-radius:12px;padding:20px;margin:16px 0}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #2d4057;vertical-align:top}small{color:#a8bad1}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;color:#8bb7ff}</style><main><a href="/">운영 화면</a> · <a href="/site-reaudit">전수검사 기록</a><h1>사이트 검증 전용 워커 1개</h1><p>발행 작업실 5개와 분리해 메뉴·글쓰기·가입·로그인 화면을 확인합니다. 계정 입력·가입·로그인 제출·글 발행은 하지 않으며 권한·CAPTCHA 제한을 유지합니다.</p><section id="status">상태 읽는 중</section><section><table><thead><tr><th>사이트</th><th>검사 결과</th><th>학습 증거</th></tr></thead><tbody id="rows"></tbody></table></section></main><script>const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function load(){const r=await fetch('/api/site-verification-worker');if(!r.ok)return;const d=await r.json(),s=d.status;document.getElementById('status').innerHTML='<b>검증 워커 '+(d.alive?'실행 중':'응답 확인 필요')+'</b> · 완료 '+(s.completed||0)+'곳 · 대기 '+(s.pending||0)+'곳<br>현재 사이트: '+esc(s.domain||s.last_domain||'후보 대기')+' · 읽은 화면 '+(s.pages||0)+'개<br><small>최근 상태 저장 '+esc(s.heartbeat_at?new Date(s.heartbeat_at).toLocaleString('ko-KR'):'아직 없음')+' · 유료 API 호출 0 · 이 워커의 신규 발행 0</small>';document.getElementById('rows').innerHTML=d.items.map(x=>'<tr><td>'+esc(x.domain)+'<br><small>'+esc(x.finished?new Date(x.finished).toLocaleString('ko-KR'):'검사 중')+'</small></td><td>'+esc(d.labels[x.result?.state||x.state]||x.state)+'<br><small>'+esc(x.result?.next_action||'')+'</small></td><td><details><summary>화면 '+(x.result?.pages?.length||0)+'개 · 상세 증거</summary><pre>'+esc(JSON.stringify(x.result,null,2))+'</pre></details></td></tr>').join('')}load();setInterval(load,10000);</script></html>'''


def register(app,ctx):
    from flask import jsonify
    folder=Path(ctx['DATA_DIR'])/'site_verification_worker'
    @app.route('/site-verification-worker',methods=['GET'])
    def verification_worker_page():return PAGE
    @app.route('/api/site-verification-worker',methods=['GET'])
    def verification_worker_status():
        status=json.loads((folder/'status.json').read_text(encoding='utf-8')) if (folder/'status.json').exists() else {}
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(status['heartbeat_at'])).total_seconds() if status.get('heartbeat_at') else 999999
        items=[]
        if (folder/'jobs.sqlite3').exists():
            with closing(sqlite3.connect('file:'+str(folder/'jobs.sqlite3')+'?mode=ro',uri=True)) as c:
                items=[{'domain':d,'state':s,'result':json.loads(r) if r else None,'finished':f} for d,s,r,f in c.execute("SELECT domain,state,result,finished FROM jobs WHERE state!='pending' ORDER BY COALESCE(finished,started) DESC LIMIT 20")]
        return jsonify(status=status,alive=age<120,items=items,labels=LABELS)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',default='/opt/chirashi/data');args=parser.parse_args();run(Path(args.data))
