"""Cheap search-result selection. URL hints never grant permission to publish."""
import re
from urllib.parse import urlsplit,urlunsplit,parse_qs,urlencode


def finder_queries(provider='brave'):
    phrases=[
        '"홍보게시판" "글쓰기"',
        '"광고게시판" "글쓰기"',
        '"무료홍보" "비회원" "게시판"',
        '"홍보 게시판" "상업광고" "허용"',
        '"업체홍보" "글쓰기" "board.php"',
        '"홍보게시판" "board_no"',
        '"홍보게시판" "회원가입" "글쓰기"',
        '"광고등록" "게시판" "비회원"',
        '"홍보게시판" "mid="',
        '"홍보게시판" "kboard"',
    ]
    for region in ('서울','경기','인천','부산','대구','대전','광주','울산','강원','충청','전라','경상'):
        phrases.append('"'+region+'" "홍보게시판" "글쓰기"')
    return phrases


def qualify_result(row):
    url=str(row.get('url') or '')
    try:p=urlsplit(url)
    except ValueError:return {'accepted':False,'reason':'invalid_url'}
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:
        return {'accepted':False,'reason':'invalid_url'}
    host=p.hostname.lower().removeprefix('www.')
    if host in {'google.com','search.naver.com','search.daum.net','bing.com','search.brave.com'}:
        return {'accepted':False,'reason':'search_page'}
    q=parse_qs(p.query);path=p.path;platform='unknown';params={}
    if re.search(r'/(?:board|write)\.php$',path,re.I) and q.get('bo_table'):
        path=re.sub(r'write\.php$','board.php',path,flags=re.I);params={'bo_table':q['bo_table'][0]};platform='gnuboard'
    elif q.get('board_no') and re.search(r'/board/(?:list|read|write)\.html$',path,re.I):
        path='/board/list.html';params={'board_no':q['board_no'][0]};platform='cafe24'
    elif (m:=re.match(r'^/article/[^/]+/(\d+)/(?:\d+)?/?$',path,re.I)) or (m:=re.match(r'^/board/[^/]+/(\d+)/?$',path,re.I)):
        path='/board/list.html';params={'board_no':m[1]};platform='cafe24'
    elif q.get('mid'):
        params={'mid':q['mid'][0]};platform='xe'
    elif q.get('kboard_id') or (q.get('mod')==['document'] and q.get('page_id')):
        params={k:q[k][0] for k in ('page_id','kboard_id') if q.get(k)};platform='kboard'
    elif re.search(r'/(?:boards?|bbs|community|forum)(?:/|$)',path,re.I):
        # Preserve unfamiliar board routes for inspection, including login-only boards.
        params={k:q[k][0] for k in ('board_id','board','category') if q.get(k)}
    else:
        return {'accepted':False,'reason':'not_board_url'}
    clean=urlunsplit((p.scheme,p.netloc,path,urlencode(params),''))
    hint=bool(re.search(r'홍보\s*게시판|광고\s*게시판|광고\s*등록|무료\s*홍보|업체\s*홍보',str(row.get('title',''))+' '+str(row.get('snippet',''))))
    return {'accepted':True,'url':clean,'priority':100 if hint else 30,'platform_hint':platform,'permission_proven':False}


def prepare_results(items):
    selected={};skipped=0
    for item in items:
        row=dict(item) if isinstance(item,dict) else {'url':str(item)}
        verdict=qualify_result(row)
        if not verdict['accepted']:skipped+=1;continue
        row['url']=verdict['url'];row['_search_priority']=verdict['priority']
        if row['url'] not in selected or row['_search_priority']>selected[row['url']]['_search_priority']:
            selected[row['url']]=row
    return sorted(selected.values(),key=lambda r:r['_search_priority'],reverse=True),skipped
