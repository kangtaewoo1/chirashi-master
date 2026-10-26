"""Bounded SERP collector. Results remain unapproved; no posting or auto-registration."""
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit,urlunsplit,parse_qs
import requests

# Require the operator in the URL, rather than matching tutorials that mention it.
DISCOVERY_QUERIES=(
    '"홍보게시판" inurl:board.php inurl:bo_table',
    '"광고등록" inurl:board.php inurl:bo_table',
    '"홍보게시판" inurl:board/list.html inurl:board_no',
)


def board_quality(row):
    p=urlsplit(row['url']);q=parse_qs(p.query)
    if p.hostname in ('google.com','www.google.com'):
        return {'board_candidate':False,'priority':0,'reason':'search_redirect'}
    gnu=p.path.endswith('/board.php') and bool(q.get('bo_table'))
    cafe=p.path.endswith('/board/list.html') and bool(q.get('board_no'))
    if not (gnu or cafe):
        return {'board_candidate':False,'priority':0,'reason':'unsupported_or_article_url'}
    text=row.get('title','')+' '+row.get('snippet','')
    explicit=any(x in text for x in ('홍보게시판','홍보 게시판','광고등록','광고 등록','홍보할 수'))
    return {'board_candidate':True,'priority':80 if explicit else 30,
            'platform':'gnuboard' if gnu else 'cafe24',
            'reason':'promotion_board_hint' if explicit else 'board_url_only',
            'permission_proven':False}


def rank_board_candidates(rows):
    """URL/title hints rank inspection only; they never prove write permission."""
    candidates=[];excluded=[]
    for row in rows:
        item=dict(row,quality=board_quality(row))
        (candidates if item['quality']['board_candidate'] else excluded).append(item)
    return sorted(candidates,key=lambda x:x['quality']['priority'],reverse=True),excluded


def parse_results(data):
    if not isinstance(data,dict) or not isinstance(data.get('organic'),list):
        raise RuntimeError('SERP JSON organic 결과가 없습니다. HTML/빈 응답을 수집 성공으로 처리하지 않습니다.')
    rows=[];seen=set()
    for item in data['organic']:
        if not isinstance(item,dict):continue
        url=str(item.get('link') or '');p=urlsplit(url)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:continue
        url=urlunsplit((p.scheme,p.netloc,p.path,p.query,''))
        if url in seen:continue
        seen.add(url)
        rows.append({'url':url,'domain':p.hostname.lower().removeprefix('www.'),
                     'title':str(item.get('title') or ''),'snippet':str(item.get('description') or ''),
                     'permission':False,'status':'pending_review','source':'brightdata_serp'})
    return rows


def search(cfg,query,request=requests.post):
    key=str(cfg.get('brightdata_serp_api_key') or '').strip()
    zone=str(cfg.get('brightdata_serp_zone') or '').strip()
    if not key or not zone:raise RuntimeError('Bright Data SERP API 키와 존 이름이 필요합니다.')
    url='https://www.google.com/search?'+urlencode({'q':query,'hl':'ko','gl':'kr','brd_json':'1'})
    try:
        response=request('https://api.brightdata.com/request',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
                         json={'zone':zone,'url':url,'format':'raw'},timeout=60)
    except requests.RequestException:
        raise RuntimeError('SERP 연결 실패. 비밀값을 포함할 수 있는 원본 오류는 출력하지 않습니다.') from None
    if response.status_code!=200:raise RuntimeError('SERP 응답 HTTP '+str(response.status_code)+' — 재시도하지 않았습니다.')
    try:data=response.json()
    except ValueError:raise RuntimeError('SERP 응답이 JSON이 아닙니다.') from None
    return parse_results(data)


def collect(cfg,queries,known_domains,output,request=requests.post):
    # Explicit proof run, never inherit unlimited goal-mode or start a background loop.
    if not 1<=len(queries)<=3:raise ValueError('실측은 1~3개 검색어로만 실행합니다.')
    found=[];successful=0
    fresh={}
    known={str(d).lower().removeprefix('www.') for d in known_domains}
    for query in queries:
        results=search(cfg,query,request=request);successful+=1
        for row in results:
            if row['domain'] in known:continue
            previous=fresh.get(row['domain'])
            if previous is None or board_quality(row)['priority']>board_quality(previous)['priority']:
                fresh[row['domain']]=dict(row,query=query)
    found=list(fresh.values())
    board_candidates,excluded=rank_board_candidates(found)
    report={'measured_at_utc':datetime.now(timezone.utc).isoformat(),'successful_requests':successful,
            'estimated_usd_at_public_payg':successful*0.0015,'cost_is_estimate_not_invoice':True,
            'new_candidate_domains':len(found),'publishable_sites_added':0,'candidates':found,
            'supported_board_url_candidates':len(board_candidates),'supported_board_candidates':board_candidates,
            'excluded_from_board_review':len(excluded)}
    Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
