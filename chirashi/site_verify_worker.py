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
from site_verification_detail import ANALYSIS_VERSION,annotate,recovery_path,summarize

NETWORK_REVIEW = {'tls_validation_failed','connect_timeout','read_timeout','connection_failed','address_or_parse_error','redirect_limit','http_error','different_domain_redirect'}
LABELS = {'pending':'대기', 'running':'검사 중', 'human_review_required':'사람 확인 필요',
          'access_review_required':'권한·이용 목적 검토', 'write_form_observed':'작성 양식 발견·발행 미검증',
          'login_profile_observed':'로그인 화면 확인·로그인 미실행', 'navigation_review_required':'추가 화면 탐색 필요',
          'network_review':'접속 상태 추가 확인', 'worker_error':'검사 오류·추가 확인'}
LABELS.update(account_verification_pending='계정 검증 대기 · 로그인 후 글쓰기 재확인 필요',
              agreement_review_required='작성 화면 동의 조건 검토 · 미제출',
              page_access_human_required='페이지 접근 단계 · 사람 확인 필요',write_human_required='글쓰기 단계 · 사람 확인 필요',
              purpose_review_required='게시판 용도·홍보 허용 검토',signup_route_observed='가입 경로 확인 · 실제 가입 미실행',human_scope_review='보안 신호 위치 추가 확인 · 글쓰기 불가 판정 아님')


def database(folder):
    folder.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(folder/'jobs.sqlite3', timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS jobs(domain TEXT PRIMARY KEY,input TEXT,priority INTEGER,state TEXT,result TEXT,started TEXT,finished TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS observations(domain TEXT,result TEXT,saved_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS review_checkpoints(domain TEXT PRIMARY KEY,record TEXT,updated_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS user_reports(domain TEXT,record TEXT,saved_at TEXT)')
    return c


def add_job(c, entry, priority=0):
    urls = list(dict.fromkeys(u for value in entry.get('urls', []) if (u:=clean_url(value)) and host(u)==entry['domain']))
    if not urls:return
    # Only diagnostic input; do not copy source credentials or user field values.
    safe = {'domain':entry['domain'], 'urls':urls[:100], 'source':entry.get('source','candidate')}
    if entry.get('probe_budget')==1:safe['probe_budget']=1
    if entry.get('inspection_depth')=='deep_public':
        safe.update(inspection_depth='deep_public',batch_id=str(entry.get('batch_id',''))[:80])
    old=c.execute('SELECT input,state,result FROM jobs WHERE domain=?',(safe['domain'],)).fetchone()
    if old:
        c.execute('UPDATE jobs SET priority=MAX(priority,?) WHERE domain=?',(priority,safe['domain']))
        previous=json.loads(old[0]);fresh=set(safe['urls'])-set(previous['urls'])
        if previous.get('probe_budget')==1 and 'probe_budget' not in safe:safe['probe_budget']=1
        if previous.get('inspection_depth')=='deep_public' and 'inspection_depth' not in safe:
            safe.update(inspection_depth='deep_public',batch_id=previous.get('batch_id'))
        if fresh and len(previous['urls'])<100:
            safe['urls']=list(dict.fromkeys(safe['urls']+previous['urls']))[:100]
            if old[2]:c.execute('INSERT INTO observations VALUES(?,?,?)',(safe['domain'],old[2],now()))
            c.execute("UPDATE jobs SET input=?,priority=MAX(priority,?),state='pending',result=NULL WHERE domain=?",(json.dumps(safe,ensure_ascii=False),priority,safe['domain']))
        return
    c.execute("INSERT OR IGNORE INTO jobs VALUES(?,?,?,'pending',NULL,NULL,NULL)",
              (safe['domain'],json.dumps(safe,ensure_ascii=False),priority))


def seed(c, data):
    from verification_checkpoint import save
    prior = data/'site_reaudit_20261030/audit.sqlite3'
    if prior.exists():
        with closing(sqlite3.connect('file:'+str(prior)+'?mode=ro',uri=True)) as old:
            for inp, raw in old.execute("SELECT input,result FROM jobs WHERE state='done'"):
                r=json.loads(raw); e=json.loads(inp)
                save(c,e,r,overwrite=False)
                network=r.get('category') in NETWORK_REVIEW
                entry=dict(e,source='reaudit_navigation_followup')
                if network:entry['probe_budget']=1
                priority=-50 if network else 20 if any(x.get('source')=='registered' for x in e.get('records',[])) else 0
                add_job(c,entry,priority)
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
        if not u or host(u)!=domain or recovery_path(u,label):continue
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
        if clean_url(u) and host(u)==domain and u not in seen and not recovery_path(u):heapq.heappush(pending,(-priority,u,kind))
    deep=entry.get('inspection_depth')=='deep_public'
    for i,u in enumerate(entry['urls'][:12 if deep else 6]):push(u,(1000 if deep else 30)-i,link_kind(u))
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
                raw=reader(url,session,domain);page=view_page(raw,kind);page['stage']=kind;page['requested_url']=url
                markup=raw.get('html','');p=Parser();p.feed(markup);p.close()
                text=p.root.text()
                page['headings']=[n.text()[:160] for n in p.nodes if n.tag in ('h1','h2','h3')][:12]
                page['restricted_membership_hint']=bool(re.search(r'조합원.{0,10}(코드|가능)|회원\s*전용\s*코드|초대\s*코드',text))
                page['promotion_permission_verified']=False
                page=annotate(page,markup)
                pages.append(page)
                if page['human_check']['scope'] in ('page_access','write'):
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
    result={'domain':domain,'checked_at':now(),'state':state,'pages':pages,'requests':requests_count,
            'elapsed_ms':round(1000*(time.monotonic()-started)),'max_pages':max_pages,
            'signup_submitted':False,'login_submitted':False,'new_posts_submitted':0,'permission_changed':False,
            'paid_api_calls':0,'publication_verified':False,'auto_ready':False,
            'next_action':{'human_review_required':'사람 확인 대기. 자동 해결·다른 경로 재시도 안 함.',
                           'access_review_required':'전용 권한·게시판 목적을 검토한 뒤 다음 단계 결정.',
                           'write_form_observed':'입력칸과 홍보 허용을 검토한 뒤 별도의 발행·공개조회 검증 필요.',
                           'login_profile_observed':'로그인 양식만 확인했습니다. 허용된 계정의 실제 로그인은 별도 검증해야 합니다.'}.get(state,'메뉴·동적 화면 또는 접속 원인을 추가 확인해야 합니다.')}
    result.update(summarize(pages))
    return result


def write_status(c,folder,**extra):
    counts=dict(c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'))
    deep=dict(c.execute("SELECT state,COUNT(*) FROM jobs WHERE json_extract(input,'$.inspection_depth')='deep_public' GROUP BY state"))
    coverage=c.execute('SELECT COUNT(*) FROM review_checkpoints').fetchone()[0]
    status={'heartbeat_at':now(),'verification_workers':1,'mode':'public pages and anonymous browser inspection; no account or form submission',
            'counts':counts,'completed':counts.get('done',0),'pending':counts.get('pending',0),
            'deep_total':sum(deep.values()),'deep_completed':deep.get('done',0),
            'coverage_records':coverage,'navigation_jobs':sum(counts.values()),
            'signup_submissions':0,'login_submissions':0,'posts_submitted':0,'paid_api_calls':0,**extra}
    temp=folder/'status.tmp';temp.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(folder/'status.json')


def run(data):
    import fcntl
    folder=data/'site_verification_worker';c=database(folder)
    lock=(folder/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    # Keep previous observations while upgrading the depth of incomplete checks.
    for domain,raw in c.execute("SELECT domain,result FROM jobs WHERE state='done'").fetchall():
        if json.loads(raw).get('analysis_version',0)<ANALYSIS_VERSION:
            c.execute('INSERT INTO observations VALUES(?,?,?)',(domain,raw,now()))
            c.execute("UPDATE jobs SET state='pending',result=NULL,priority=MAX(priority,200) WHERE domain=?",(domain,))
    c.execute("UPDATE jobs SET state='pending' WHERE state='running'");c.commit();last_seed=0
    write_status(c,folder,phase='loading_candidate_queue')
    while True:
        if time.monotonic()-last_seed>300:seed(c,data);last_seed=time.monotonic()
        item=c.execute("SELECT domain,input FROM jobs WHERE state='pending' ORDER BY priority DESC,domain LIMIT 1").fetchone()
        if not item:write_status(c,folder,phase='waiting_for_candidates');time.sleep(10);continue
        domain,raw=item;c.execute("UPDATE jobs SET state='running',started=? WHERE domain=?",(now(),domain));c.commit()
        try:
            entry=json.loads(raw);heartbeat=lambda **kw:write_status(c,folder,**kw)
            result=inspect(entry,heartbeat,max_pages=14 if entry.get('inspection_depth')=='deep_public' else entry.get('probe_budget',8))
            if entry.get('inspection_depth')=='deep_public':
                from site_deep_measure import enrich
                result=enrich(result,entry,heartbeat)
        except Exception as exc:result={'domain':domain,'state':'worker_error','error_type':type(exc).__name__,'checked_at':now(),'new_posts_submitted':0,'analysis_version':ANALYSIS_VERSION}
        from verification_checkpoint import save
        save(c,json.loads(raw),result)
        c.execute('INSERT INTO observations VALUES(?,?,?)',(domain,json.dumps(result,ensure_ascii=False),now()))
        c.execute("UPDATE jobs SET state='done',result=?,finished=? WHERE domain=?",(json.dumps(result,ensure_ascii=False),now(),domain));c.commit()
        write_status(c,folder,phase='between_candidates',last_domain=domain,last_result=result['state'],pages=len(result.get('pages',[])));time.sleep(2)


PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>사이트 검증 전용 워커</title><style>body{background:#0b1018;color:#e6ecf6;font:16px/1.6 system-ui;margin:28px}main{max-width:1400px;margin:auto}a{color:#8bb7ff}section{background:#121d2c;border:1px solid #2d4057;border-radius:12px;padding:20px;margin:16px 0}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #2d4057;vertical-align:top}small{color:#a8bad1}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;color:#8bb7ff}</style><main><a href="/">운영 화면</a> · <a href="/site-reaudit">전수검사 기록</a> · <a href="/site-verification-checkpoints">전체 단계별 대기표</a><h1>사이트 검증 전용 워커 1개</h1><p>발행 작업실 5개와 분리해 메뉴·글쓰기·가입·로그인 화면을 확인합니다. 현재는 비로그인 화면 조사 단계이며 우선 대상은 서버의 익명 브라우저로도 확인합니다. 가입·로그인 후 작성 화면 재검증은 별도 단계로 남깁니다. 계정 입력·가입·로그인 제출·글 발행은 하지 않으며 권한·CAPTCHA 제한을 유지합니다. 미확인 결과는 탈락 판정이 아닙니다.</p><section id="status">상태 읽는 중</section><section><p><a href="/site-verification-worker?batch=deep36_20261030">우선 심층 대상 36곳</a> · <a href="/site-verification-worker">최근 전체 검사</a></p><label>사이트 검색 <input id="siteQuery" placeholder="예: royalcompany.co.kr"></label> <button onclick="document.querySelectorAll('#rows details[open]').forEach(d=>d.open=false);load()">조회</button><p><small>상세 내역을 펼친 동안에는 목록을 유지합니다. 미확인 상태는 탈락이 아닙니다.</small></p><table><thead><tr><th>사이트</th><th>검사 결과</th><th>학습 증거</th></tr></thead><tbody id="rows"></tbody></table></section></main><script>const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const stepNames={form_observed:'양식 확인',terms_gate_observed:'약관 동의 화면 확인',route_observed:'주소만 확인',not_discovered:'아직 찾지 못함',authentication_required_before_recheck:'정상 로그인 후 같은 게시판 재확인 필요'};
const scopeNames={page_access:'페이지 접근 단계',write:'글쓰기 단계',signup:'가입 단계',login:'로그인 단계',account_recovery_unrelated:'비밀번호 복구용 · 글쓰기와 별개',comment_or_embedded_unconfirmed:'댓글·내장 기능 · 글쓰기 요구 미확인',integration_only_unconfirmed:'연동 스크립트만 감지 · 실제 확인 절차 미확인',not_observed:'이번 화면에서 감지되지 않음'};
function profile(z){const p=z.flow_profile;if(!p)return '<p>이전 방식의 기록입니다. 상세 단계 재검사를 기다립니다.</p>';return (z.browser_check?'<p>서버 익명 브라우저: '+esc(z.browser_check.state)+' · 화면 '+(z.browser_check.pages||[]).length+'개 · 계정 입력/제출 0</p>':'')+'<p><b>현재 검증 범위: 비로그인 화면 조사</b><br>미확인은 탈락이 아닙니다. 가입·로그인 후 작성 화면은 별도로 확인해야 합니다.</p><p>① 가입: '+esc(stepNames[p.signup.status]||p.signup.status)+' · 실제 가입 미실행 · 약관 동의 미실행<br>인증 문구: '+(p.signup.identity_verification_hint?'본인 인증 신호 있음':'본인 인증 요건 미확인')+' / '+(p.signup.email_verification_hint?'이메일 인증 신호 있음':'이메일 인증 요건 미확인')+'<br>② 로그인: '+esc(stepNames[p.login.status]||p.login.status)+' · 실제 로그인 미실행<br>③ 글쓰기: '+esc(stepNames[p.write.status]||p.write.status)+'<br>④ 홍보 허용: 미검증<br>⑤ 실제 발행·공개 제목/본문 조회: 미실행</p>'+((z.human_checks||[]).length?'<b>사람 확인 위치</b>'+z.human_checks.map(h=>'<p>'+esc(scopeNames[h.scope]||h.scope)+'<br><small>'+esc(h.url)+'</small></p>').join(''):'<p>사람 확인: 이번에 읽은 화면에서는 감지되지 않음. 인증 후 절차는 미확인.</p>')+(p.write.boards||[]).map(b=>'<p><b>게시판별 기록</b><br><a href="'+esc(b.url)+'" target="_blank" rel="noopener">'+esc(b.url)+'</a><br>'+(b.write_mode==='existing_item_related'?'기존 글 관련 화면 · 신규 글쓰기 증거 아님':'새 글 작성 경로 조사')+'<br>필수 입력칸: '+esc(b.required_fields.map(f=>(f.label||f.name)+' ['+f.name+']').join(', ')||'아직 확인되지 않음')+(b.purpose_hint?'<br>용도: '+esc(b.purpose_hint.reason):'')+'</p>').join('')+(z.pages||[]).filter(p=>p.stage==='signup'||p.stage==='login'||p.actual_page_role==='login').map(p=>'<p><b>'+(p.stage==='signup'?'가입':'로그인')+' 화면 입력칸</b><br><small>'+esc(p.url)+'</small><br>'+esc((p.authentication_fields||p.fields||[]).filter(f=>f.type!=='hidden').map(f=>(f.label||f.name||'이름 없는 입력칸')+' · '+(f.required===true?'필수':f.required===false?'선택':'필수 여부 미확인')+(f.minlength?' · 최소 '+f.minlength+'자':'')+(f.maxlength?' · 최대 '+f.maxlength+'자':'')).join(' / ')||'입력 폼 미확인 · 동의/동적 화면일 수 있음')+'</p>').join('')}
async function load(){const r=await fetch('/api/site-verification-worker?q='+encodeURIComponent(document.getElementById('siteQuery').value)+'&batch='+encodeURIComponent(new URLSearchParams(location.search).get('batch')||''));if(!r.ok)return;const d=await r.json(),s=d.status;document.getElementById('status').innerHTML='<b>검증 워커 '+(d.alive?'실행 중':'응답 확인 필요')+'</b> · 상세 화면 조사 '+(s.completed||0)+'곳 · 대기 '+(s.pending||0)+'곳<br>재검토 대기표 '+(s.coverage_records||0)+'곳 · 화면 조사 대상 '+(s.navigation_jobs||0)+'곳<br><b>우선 심층 검사 '+(s.deep_completed||0)+' / '+(s.deep_total||0)+'곳</b><br>현재 사이트: '+esc(s.domain||s.last_domain||'후보 대기')+' · 읽은 화면 '+(s.pages||0)+'개<br><small>최근 상태 저장 '+esc(s.heartbeat_at?new Date(s.heartbeat_at).toLocaleString('ko-KR'):'아직 없음')+' · 유료 API 호출 0 · 이 워커의 신규 발행 0</small>';if(document.querySelector('#rows details[open]'))return;document.getElementById('rows').innerHTML=d.items.map(x=>'<tr><td>'+esc(x.domain)+'<br><small>'+esc(x.finished?new Date(x.finished).toLocaleString('ko-KR'):'검사 중')+'</small></td><td>'+esc(d.labels[x.result?.state||x.state]||x.state)+'<br><small>'+esc(x.result?.next_action||'')+'</small></td><td><details><summary>화면 '+(x.result?.pages?.length||0)+'개 · 단계별 검증 내역</summary>'+profile(x.result||{})+'<details><summary>원본 증거</summary><pre>'+esc(JSON.stringify(x.result,null,2))+'</pre></details></details></td></tr>').join('')}load();setInterval(load,10000);</script></html>'''


def register(app,ctx):
    from flask import jsonify,request
    folder=Path(ctx['DATA_DIR'])/'site_verification_worker'
    @app.route('/site-verification-worker',methods=['GET'])
    def verification_worker_page():return PAGE
    @app.route('/site-verification-checkpoints',methods=['GET'])
    def verification_checkpoint_page():return CHECKPOINT_PAGE
    @app.route('/api/site-verification-checkpoints',methods=['GET'])
    def verification_checkpoint_status():
        from site_reaudit import CATEGORY_LABELS
        records=[];reports=[];coverage=0;queued=0;unaddressed=0
        if (folder/'jobs.sqlite3').exists():
            with closing(sqlite3.connect('file:'+str(folder/'jobs.sqlite3')+'?mode=ro',uri=True)) as c:
                q=str(request.args.get('q',''))[:120]
                coverage=c.execute('SELECT COUNT(*) FROM review_checkpoints').fetchone()[0]
                queued=c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
                unaddressed=c.execute("SELECT COUNT(*) FROM review_checkpoints WHERE json_array_length(record,'$.saved_urls')=0").fetchone()[0]
                records=[json.loads(r) for (r,) in c.execute('SELECT record FROM review_checkpoints WHERE domain LIKE ? ORDER BY updated_at DESC LIMIT 100',('%'+q+'%',))]
                reports=[json.loads(r) for (r,) in c.execute('SELECT record FROM user_reports WHERE domain LIKE ? ORDER BY saved_at DESC LIMIT 30',('%'+q+'%',))]
        return jsonify(records=records,user_reports=reports,coverage=coverage,queued=queued,address_review=unaddressed,labels={**CATEGORY_LABELS,**LABELS})
    @app.route('/api/site-verification-worker',methods=['GET'])
    def verification_worker_status():
        status=json.loads((folder/'status.json').read_text(encoding='utf-8')) if (folder/'status.json').exists() else {}
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(status['heartbeat_at'])).total_seconds() if status.get('heartbeat_at') else 999999
        items=[]
        if (folder/'jobs.sqlite3').exists():
            with closing(sqlite3.connect('file:'+str(folder/'jobs.sqlite3')+'?mode=ro',uri=True)) as c:
                q=str(request.args.get('q',''))[:120]
                batch=str(request.args.get('batch',''))[:80]
                query="SELECT domain,state,result,finished FROM jobs WHERE domain LIKE ?"
                params=['%'+q+'%']
                if batch:
                    query+=" AND json_extract(input,'$.batch_id')=?";params.append(batch)
                else:query+=" AND state!='pending'"
                query+=" ORDER BY COALESCE(finished,started) DESC LIMIT 100" if batch else " ORDER BY COALESCE(finished,started) DESC LIMIT 20"
                items=[{'domain':d,'state':s,'result':json.loads(r) if r else None,'finished':f} for d,s,r,f in c.execute(query,params)]
        return jsonify(status=status,alive=age<120,items=items,labels=LABELS)


CHECKPOINT_PAGE=r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>전체 사이트 단계별 대기표</title><style>body{background:#0b1018;color:#e6ecf6;font:16px/1.6 system-ui;margin:24px}main{max-width:1400px;margin:auto}a{color:#8bb7ff}section{background:#121d2c;border:1px solid #2d4057;border-radius:12px;padding:20px;margin:16px 0}td,th{text-align:left;padding:12px;border-bottom:1px solid #2d4057;vertical-align:top}table{width:100%;border-collapse:collapse}small{color:#a8bad1}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><main><a href="/site-verification-worker">검증 워커</a> · <a href="/site-knowledge">학습 내역</a><h1>전체 사이트 단계별 대기표</h1><p>가입·로그인·사람 확인·접속 오류를 자동 탈락으로 처리하지 않고 다음 확인 단계를 남깁니다. 대기표 등록은 실제 가입·로그인·발행 성공이 아닙니다. 원래 작성 주소를 보존하며 권한·인증서 검증·사람 확인 제한은 유지합니다.</p><section id="totals">읽는 중</section><section><label>도메인 검색 <input id="q" placeholder="royalcompany.co.kr"></label> <button onclick="load()">조회</button><div id="reports"></div><p><small>최근 100곳을 표시합니다. 도메인을 검색하면 해당 사이트의 단계를 확인할 수 있습니다.</small></p><table><thead><tr><th>사이트</th><th>현재 단계</th><th>재개 경로와 증거</th></tr></thead><tbody id="rows"></tbody></table></section></main><script>const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function load(){const r=await fetch('/api/site-verification-checkpoints?q='+encodeURIComponent(document.getElementById('q').value));if(!r.ok)return;const d=await r.json();document.getElementById('totals').innerHTML='<b>대기표 '+d.coverage+'곳</b> · 화면 조사 큐 '+d.queued+'곳 · 주소 수정 필요 '+d.address_review+'곳<br>각 사이트의 가입·로그인·원래 글쓰기 복귀·발행·공개 조회 단계를 별도로 보관합니다.';document.getElementById('reports').innerHTML=d.user_reports.map(x=>'<p><b>사용자 제공 증거 · '+esc(x.domain)+'</b><br>'+esc(x.description)+'<br><small>서버 로그인·실제 발행 성공 증거와 구분해 보관합니다.</small></p>').join('');document.getElementById('rows').innerHTML=d.records.map(x=>'<tr><td>'+esc(x.domain)+'</td><td>'+esc(d.labels[x.state]||x.state)+'<br><small>'+esc(x.next_action)+'</small></td><td><a href="/site-verification-worker?q='+encodeURIComponent(x.domain)+'">상세 화면 조사 기록</a><br>'+[...(x.original_write_urls||[]),...(x.saved_urls||[])].filter((u,i,a)=>a.indexOf(u)===i).slice(0,8).map(u=>'<small>'+esc(u)+'</small>').join('<br>')+'<details><summary>단계별 대기 기록</summary><pre>'+esc(JSON.stringify(x,null,2))+'</pre></details></td></tr>').join('')}load();</script></html>'''

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',default='/opt/chirashi/data');args=parser.parse_args();run(Path(args.data))
