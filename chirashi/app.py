"""
찌라시 마스터 v6 - 컴플라이언스형 (정직한 자동 발행)
========================================================
- bm21.com 스타일 리치 HTML 템플릿 (컬러 헤더, 칩 태그, FAQ, 이미지)
- Selenium 기반 그누보드/Cafe24 글쓰기 (정상 동작, 회피코드 없음)
- {지역} {서비스} {브랜드} 3키워드 치환
- 정직한 전화번호 표기 (난독화 없음)
- rate limit + 사이트당 1일 발행 한도로 도배 방지
※ 게시 대상 게시판에 대한 게시 권한(운영자 허락/홍보 전용 게시판)은
  이용자가 보장해야 합니다. 자동 프로그램 금지 규칙이 있는 곳에는 사용 금지.
"""

import sys, os, re, json, time, random, threading, queue, urllib.parse, secrets, hashlib, base64, copy, uuid, html as html_lib
# 콘솔 코드페이지가 cp949 등일 때 이모지 print가 UnicodeEncodeError로 죽는 것 방지.
# (Windows에서 PYTHONIOENCODING 미설정 시 startup print의 ⏰/🔁 등이 크래시 유발)
for _stream in ('stdout', 'stderr'):
    try:
        getattr(sys, _stream).reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import urllib3; urllib3.disable_warnings()

from flask import Flask, request, jsonify, render_template_string, session, redirect, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException

# ==================== 설정 ====================
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE_DIR / 'data'; DATA_DIR.mkdir(exist_ok=True)
SITES_FILE = DATA_DIR / 'sites.json'; CONFIG_FILE = DATA_DIR / 'config.json'; LOG_FILE = DATA_DIR / 'logs.json'
HISTORY_FILE = DATA_DIR / 'history.json'   # 발행 이력 원장(엑셀/결과탭)
QUEUE_FILE = DATA_DIR / 'queue.json'       # 미완료 작업(재시작 복구용)
SCHED_FILE = DATA_DIR / 'schedules.json'   # 예약 발행 스케줄
KEYWORDS_FILE = DATA_DIR / 'keywords.json' # 키워드 풀(엑셀 랜덤 치환)
UNIQ_FILE = DATA_DIR / 'uniq.json'          # 발행 콘텐츠 중복방지(제목/본문 해시)
IMAGES_FILE = DATA_DIR / 'images.json'      # 사용자 이미지 URL 풀(본문 삽입용)
UPLOAD_DIR = DATA_DIR / 'uploads'; UPLOAD_DIR.mkdir(exist_ok=True)
IMAGE_EXTENSIONS = {'.jpg','.jpeg','.png','.gif','.webp'}
MEMBERS_FILE = DATA_DIR / 'members.json'    # 회원(고객) 관리 + 월 정산
CAND_FILE = DATA_DIR / 'candidates.json'    # 발굴 후보(승인 대기함)
REGIONS_FILE = BASE_DIR / 'regions_full.json'  # 전국 시도·시군구·읍면동
DISCO_FILE = DATA_DIR / 'discover.json'     # 발굴 상태(쿼리 커서·일일 카운트)
SIGNUP_PROFILES_FILE = DATA_DIR / 'signup_profiles.json'  # 가입 폼 측정·학습 이력
REJECTED_DOMAINS_FILE = DATA_DIR / 'rejected_domains.json'  # 영구 탈락 도메인(다음 발굴에서 제외)
AI_USAGE_FILE = DATA_DIR / 'openai_usage.json'  # 글 생성별 토큰·예상 비용 원장
CAPTCHA_USAGE_FILE = DATA_DIR / 'captcha_usage.json'  # 2captcha 해결(solve) 1건당 원장(횟수 정확·금액은 설정단가 기준 추정)
BRAVE_USAGE_FILE = DATA_DIR / 'brave_usage.json'      # Brave 검색 호출 1건당 원장(횟수 정확·금액은 설정단가 기준 추정)
WORKROOMS_FILE = DATA_DIR / 'workrooms.json'    # 키워드별 독립 작업실

IMAGES = [f'https://picsum.photos/id/{i}/800/400' for i in [1,20,26,48,60,64,76,91,96,104,152,160,175,180,185,201]]
COLORS = ['#3b1f2b','#2B8A3E','#37474f','#1a5276','#6c3483','#b7950b','#a04000']

_json_locks={}; _json_locks_guard=threading.Lock()
_signup_learn_locks=defaultdict(threading.Lock)
def _json_lock(p):
    key=str(Path(p).resolve())
    with _json_locks_guard:
        return _json_locks.setdefault(key,threading.RLock())

def load_json(p, d=None):
    if d is None: d=[]
    try:
        with _json_lock(p):
            if os.path.exists(p):
                with open(p,'r',encoding='utf-8') as f: return json.load(f)
    except: pass
    return d

def save_json(p, data):
    """같은 파일시스템의 임시 파일에 기록 후 원자 교체하여 JSON 손상을 방지."""
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(f'.{p.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with _json_lock(p):
        try:
            with open(tmp,'w',encoding='utf-8') as f:
                json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
            # ★Windows [WinError 5] 액세스 거부(노드 2026-09-11): 같은 폴더의 다른 프로세스(pc_discovery↔pc_node,
            #   또는 워커 스레드의 load_json)가 그 순간 파일을 열고 있으면 os.replace가 PermissionError로 죽고,
            #   그 예외가 발행 흐름까지 올라와 '처리 예외'로 후보를 날렸음. 잠깐 재시도 후 최후엔 직접 덮어쓰기.
            _last=None
            for _i in range(25):
                try: os.replace(tmp,p); _last=None; break
                except PermissionError as _e:
                    _last=_e; time.sleep(0.04+0.02*_i)
            if _last is not None:
                with open(p,'w',encoding='utf-8') as f:
                    json.dump(data,f,ensure_ascii=False,indent=2)
        finally:
            try:
                if tmp.exists(): tmp.unlink()
            except Exception: pass

# ---- 사용자 이미지 URL 풀 (본문 삽입용) ----
def load_image_urls():
    d=load_json(IMAGES_FILE,[])
    return [u.strip() for u in d if isinstance(u,str) and u.strip().startswith('http')]
def save_image_urls(urls): save_json(IMAGES_FILE,urls)
def _public_base_url():
    """게시글에 삽입할 이미지의 절대 URL 기준 도메인.
       설정 public_base_url이 있으면 그걸, 없으면 google.twseo.kr(운영 도메인)."""
    b=(load_config().get('public_base_url') or '').strip().rstrip('/')
    return b or 'https://google.twseo.kr'

def _abs_media_url(u):
    """상대 /media/... 경로를 외부 게시판에서도 로드되도록 절대 URL로 변환.
       (외부 게시판에 <img src='/media/..'>가 들어가면 그 게시판 도메인 기준으로
        풀려 깨진다 — 이미지는 우리 서버에 있으므로 절대 URL이어야 한다.)"""
    u=str(u or '').strip()
    if u.startswith('http://') or u.startswith('https://'): return u
    if u.startswith('/'): return _public_base_url()+u
    return u

def _workroom_image_urls(workroom_id):
    """작업실 전용 이미지 풀 = 그 작업실 image_urls(외부 URL) + 그 작업실 업로드파일(절대 URL)."""
    wid=str(workroom_id or '').strip()
    if not wid: return []
    room=next((r for r in (load_json(WORKROOMS_FILE,[]) or []) if str(r.get('id'))==wid),None)
    urls=[]
    if room:
        urls+=[u.strip() for u in (room.get('image_urls') or []) if isinstance(u,str) and u.strip().startswith('http')]
    urls+=[_abs_media_url(x['url']) for x in uploaded_images(wid)]   # 업로드 파일 → 절대 URL
    return list(dict.fromkeys(urls))

def pick_images(n, workroom_id=None):
    """본문용 이미지 n개 선택.
       - workroom_id 주면: 그 작업실 전용 풀만 사용. 비어있으면 [](이미지 없이 발행) — 대표님 지시.
       - workroom_id 없으면(테스트/전역): 사용자 URL 풀이 있으면 그걸, 없으면 기본(picsum) 폴백."""
    if workroom_id:
        pool=_workroom_image_urls(workroom_id)
        if not pool: return []            # 작업실에 이미지 없음 → 이미지 없이 발행
        if len(pool)>=n: return random.sample(pool,n)
        return [random.choice(pool) for _ in range(n)]
    pool=load_image_urls() or IMAGES
    if len(pool)>=n: return random.sample(pool,n)
    return [random.choice(pool) for _ in range(n)]   # URL이 부족하면 중복 허용

def _record_openai_usage(model, usage, cfg):
    """Chat Completions 응답 usage를 저장하고 공식 토큰 단가 기준 예상비용을 계산한다."""
    inp=int((usage or {}).get('prompt_tokens') or (usage or {}).get('input_tokens') or 0)
    out=int((usage or {}).get('completion_tokens') or (usage or {}).get('output_tokens') or 0)
    cached=int((((usage or {}).get('prompt_tokens_details') or {}).get('cached_tokens')) or 0)
    pin=float(cfg.get('openai_input_price_per_million') or 0.15)
    pout=float(cfg.get('openai_output_price_per_million') or 0.60)
    pcache=float(cfg.get('openai_cached_input_price_per_million') or 0.075)
    regular=max(0,inp-cached)
    # OpenRouter는 응답 usage.cost(USD 실비용)를 주므로 있으면 그걸 우선(추정 아님).
    _uc=(usage or {}).get('cost')
    estimated=float(_uc) if isinstance(_uc,(int,float)) else (regular*pin+cached*pcache+out*pout)/1_000_000
    rec={'time':datetime.now().astimezone().isoformat(timespec='seconds'),'model':model,
         'input_tokens':inp,'cached_input_tokens':cached,'output_tokens':out,
         'requests':1,'estimated_cost_usd':round(estimated,8)}
    with _json_lock(AI_USAGE_FILE):
        rows=load_json(AI_USAGE_FILE,[])
        if not isinstance(rows,list): rows=[]
        rows.append(rec); save_json(AI_USAGE_FILE,rows[-20000:])
    return rec

def _time_series(rows, cost_key='estimated_cost_usd', days=14):
    """원장 rows(각 항목에 'time' ISO8601)를 일별·시간별로 집계한다.
    - daily: 최근 `days`일 [{date,cost,count}] (오늘 포함, 과거→현재 순)
    - hourly: 오늘 24시간 [{hour(0~23),cost,count}]
    금액은 각 원장이 기록해 둔 cost_key 합계다(원장 성격상 추정치일 수 있음)."""
    now=datetime.now().astimezone()
    def _num(x,k):
        try: return float(x.get(k,0) or 0)
        except Exception: return 0.0
    def _cnt(x):
        try: return int(x.get('requests',1) or 1)
        except Exception: return 1
    daily=[]
    for i in range(days-1,-1,-1):
        d=(now-timedelta(days=i)).strftime('%Y-%m-%d')
        sel=[x for x in rows if str(x.get('time','')).startswith(d)]
        daily.append({'date':d,'cost':round(sum(_num(x,cost_key) for x in sel),6),
                      'count':sum(_cnt(x) for x in sel)})
    today=now.strftime('%Y-%m-%d'); hourly=[]
    for h in range(24):
        pref=f'{today}T{h:02d}'
        sel=[x for x in rows if str(x.get('time','')).startswith(pref)]
        hourly.append({'hour':h,'cost':round(sum(_num(x,cost_key) for x in sel),6),
                       'count':sum(_cnt(x) for x in sel)})
    return {'daily':daily,'hourly':hourly}

def _local_openai_usage_summary(cfg):
    rows=load_json(AI_USAGE_FILE,[]); now=datetime.now().astimezone()
    month=now.strftime('%Y-%m'); today=now.strftime('%Y-%m-%d')
    def agg(items):
        return {'requests':sum(int(x.get('requests',1) or 0) for x in items),
                'input_tokens':sum(int(x.get('input_tokens',0) or 0) for x in items),
                'cached_input_tokens':sum(int(x.get('cached_input_tokens',0) or 0) for x in items),
                'output_tokens':sum(int(x.get('output_tokens',0) or 0) for x in items),
                'estimated_cost_usd':round(sum(float(x.get('estimated_cost_usd',0) or 0) for x in items),6)}
    mr=[x for x in rows if str(x.get('time','')).startswith(month)]
    tr=[x for x in rows if str(x.get('time','')).startswith(today)]
    budget=float(cfg.get('openai_monthly_budget_usd') or 0)
    m=agg(mr); t=agg(tr)
    per_call=round(m['estimated_cost_usd']/m['requests'],6) if m['requests'] else 0.0  # 이번 달 평균 1건당 예상비용
    # ★모델별 이번달 집계(대표님 2026-09-11 '지출 $97.88 vs 예산 $20 이거 맞아?'): 서버가 mini인지 4o인지 원장으로 확정용.
    bym={}
    for x in mr:
        k=str(x.get('model') or '?'); b=bym.setdefault(k,{'requests':0,'input_tokens':0,'output_tokens':0,'estimated_cost_usd':0.0})
        b['requests']+=int(x.get('requests',1) or 0); b['input_tokens']+=int(x.get('input_tokens',0) or 0)
        b['output_tokens']+=int(x.get('output_tokens',0) or 0); b['estimated_cost_usd']=round(b['estimated_cost_usd']+float(x.get('estimated_cost_usd',0) or 0),6)
    series=_time_series(rows,'estimated_cost_usd')
    # 현재 엔진/키 유무(값 아님) — 제공자 전환 후 "AI가 도는지" 관제실·토큰API에서 바로 확인용(2026-09-11 OpenRouter 전환 직후 원장 0건 진단)
    _pv=(cfg.get('llm_provider') or 'openrouter').strip().lower()
    _pk=(cfg.get('nvidia_api_key') if _pv=='nvidia' else (cfg.get('openrouter_api_key') if _pv=='openrouter' else cfg.get('openai_key')))
    # 모델명은 generate_post_gpt가 원장에 기록하는 것과 같은 기본값·strip을 적용해야 '현재 엔진' 집계가 어긋나지 않음(검토 지적)
    _pm=(((cfg.get('nvidia_model') or 'nvidia/nemotron-3-ultra-550b-a55b') if _pv=='nvidia'
          else ((cfg.get('openrouter_model') or 'deepseek/deepseek-v4-flash-0731') if _pv=='openrouter' else (cfg.get('model') or 'gpt-4o-mini')))).strip()
    # ★현재 엔진(모델)만 따로 집계(대표님 2026-09-11 'OpenAI 화면에서 제거·실측으로'): OpenRouter는 응답 usage.cost 실비용,
    #   NVIDIA는 무료($0)라 둘 다 '실측'. 과거 gpt-4o-mini 행은 토큰 추정치라 by_model 표에서만 '추정·종료'로 보임.
    cur_rows=[x for x in rows if str(x.get('model') or '')==str(_pm or '')]
    cm=agg([x for x in cur_rows if str(x.get('time','')).startswith(month)]); ct=agg([x for x in cur_rows if str(x.get('time','')).startswith(today)])
    cur_series=_time_series(cur_rows,'estimated_cost_usd')
    current={'model':_pm,'provider':_pv,'measured':_pv in ('openrouter','nvidia'),
             'month':cm,'today':ct,'per_call_usd':round(cm['estimated_cost_usd']/cm['requests'],6) if cm['requests'] else 0.0,
             'daily':cur_series['daily'],'hourly':cur_series['hourly']}
    return {'source':'local_estimate','month':m,'today':t,'monthly_budget_usd':budget,'by_model':bym,'model_setting':cfg.get('model'),
            'llm_provider':_pv,'llm_model':_pm,'llm_key_set':bool((_pk or '').strip()),'use_gpt':bool(cfg.get('use_gpt')),'current':current,
            'remaining_budget_usd':round(max(0,budget-m['estimated_cost_usd']),6) if budget>0 else None,
            'per_call_usd':per_call,'daily':series['daily'],'hourly':series['hourly'],
            'unit_price':{'input_per_million':float(cfg.get('openai_input_price_per_million') or 0.15),
                          'output_per_million':float(cfg.get('openai_output_price_per_million') or 0.60),
                          'cached_input_per_million':float(cfg.get('openai_cached_input_price_per_million') or 0.075)},
            'note':'프로젝트 키 응답의 토큰 기준 예상치'}

def _record_captcha_usage(cap_type, success, cfg):
    """2captcha 해결 1건을 원장에 기록한다. 성공 건만 과금되므로 성공 시에만 비용을 계산한다.
    2captcha는 solve당 실제 과금액을 응답하지 않으므로, 금액은 설정 단가 × 성공횟수의 추정치다."""
    try:
        if success:
            if cap_type=='recaptcha':
                price=float(cfg.get('twocaptcha_price_recaptcha_usd') or 0.003)
            else:
                price=float(cfg.get('twocaptcha_price_image_usd') or 0.0005)
        else:
            price=0.0
        rec={'time':datetime.now().astimezone().isoformat(timespec='seconds'),
             'type':cap_type or 'unknown','success':bool(success),
             'requests':1,'estimated_cost_usd':round(price,6)}
        with _json_lock(CAPTCHA_USAGE_FILE):
            rows=load_json(CAPTCHA_USAGE_FILE,[])
            if not isinstance(rows,list): rows=[]
            rows.append(rec); save_json(CAPTCHA_USAGE_FILE,rows[-20000:])
    except Exception:
        pass  # 기록 실패가 본 작업(캡차 해결)을 막지 않도록 조용히 무시

def _record_brave_usage(cfg):
    """Brave 검색 호출 1건을 원장에 기록한다. Brave는 사용량 조회 API가 없어 횟수만 정확하고,
    금액은 설정한 쿼리당 단가 × 호출횟수의 추정치다."""
    try:
        price=float(cfg.get('brave_price_per_query_usd') or 0.0)
        rec={'time':datetime.now().astimezone().isoformat(timespec='seconds'),
             'requests':1,'estimated_cost_usd':round(price,6)}
        with _json_lock(BRAVE_USAGE_FILE):
            rows=load_json(BRAVE_USAGE_FILE,[])
            if not isinstance(rows,list): rows=[]
            rows.append(rec); save_json(BRAVE_USAGE_FILE,rows[-20000:])
    except Exception:
        pass

def _captcha_usage_summary(cfg):
    """2captcha 원장 기반 요약(월/오늘/횟수당/일별/시간별). 금액은 추정치."""
    rows=load_json(CAPTCHA_USAGE_FILE,[]); now=datetime.now().astimezone()
    month=now.strftime('%Y-%m'); today=now.strftime('%Y-%m-%d')
    def agg(items):
        ok=[x for x in items if x.get('success')]
        return {'requests':sum(int(x.get('requests',1) or 0) for x in items),
                'success':len(ok),'fail':len(items)-len(ok),
                'estimated_cost_usd':round(sum(float(x.get('estimated_cost_usd',0) or 0) for x in items),6)}
    m=agg([x for x in rows if str(x.get('time','')).startswith(month)])
    t=agg([x for x in rows if str(x.get('time','')).startswith(today)])
    per_call=round(m['estimated_cost_usd']/m['success'],6) if m['success'] else 0.0
    series=_time_series(rows,'estimated_cost_usd')
    return {'source':'local_estimate','month':m,'today':t,'per_call_usd':per_call,
            'daily':series['daily'],'hourly':series['hourly'],
            'unit_price':{'recaptcha_usd':float(cfg.get('twocaptcha_price_recaptcha_usd') or 0.003),
                          'image_usd':float(cfg.get('twocaptcha_price_image_usd') or 0.0005)},
            'note':'해결 성공 횟수 × 설정 단가 기준 추정치(2captcha는 건당 실제 과금액 미제공)'}

def _brave_usage_summary(cfg):
    """Brave 원장 기반 요약(월/오늘/횟수당/일별/시간별). 금액은 추정치."""
    rows=load_json(BRAVE_USAGE_FILE,[]); now=datetime.now().astimezone()
    month=now.strftime('%Y-%m'); today=now.strftime('%Y-%m-%d')
    def agg(items):
        return {'requests':sum(int(x.get('requests',1) or 0) for x in items),
                'estimated_cost_usd':round(sum(float(x.get('estimated_cost_usd',0) or 0) for x in items),6)}
    m=agg([x for x in rows if str(x.get('time','')).startswith(month)])
    t=agg([x for x in rows if str(x.get('time','')).startswith(today)])
    per_call=round(m['estimated_cost_usd']/m['requests'],6) if m['requests'] else 0.0
    price=float(cfg.get('brave_price_per_query_usd') or 0.0)
    series=_time_series(rows,'estimated_cost_usd')
    return {'source':'local_estimate','month':m,'today':t,'per_call_usd':per_call,
            'daily':series['daily'],'hourly':series['hourly'],
            'unit_price':{'per_query_usd':price},
            'note':'검색 호출 횟수 × 설정 단가 기준 추정치(Brave는 사용량 조회 API 미제공)'
                    if price>0 else '단가 미설정 — 설정 탭에서 쿼리당 단가를 입력하면 추정비용이 계산됩니다'}

def _openai_admin_costs(cfg):
    """관리자 키가 있을 때만 공식 조직 Costs API로 이번 달 실제 비용을 조회한다.
    반환: {'total_usd':float, 'daily':[{date,cost}]} — 일별 버킷을 살려 실측 일별 그래프에 쓴다.
    관리자 키가 없으면 None(호출부에서 토큰 기반 추정치로 폴백)."""
    key=(cfg.get('openai_admin_key') or '').strip()
    if not key: return None
    import requests as _rq
    now=datetime.now().astimezone(); start=int(now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp())
    r=_rq.get('https://api.openai.com/v1/organization/costs',params={'start_time':start,'limit':31},
              headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},timeout=20)
    r.raise_for_status(); data=r.json(); total=0.0; daily=[]
    for bucket in data.get('data',[]):
        bstart=bucket.get('start_time')
        try: bdate=datetime.fromtimestamp(int(bstart)).astimezone().strftime('%Y-%m-%d') if bstart else None
        except Exception: bdate=None
        bsum=0.0
        for result in bucket.get('results',[]):
            amount=result.get('amount') or {}
            if str(amount.get('currency','usd')).lower()=='usd':
                v=float(amount.get('value') or 0); bsum+=v; total+=v
        if bdate is not None: daily.append({'date':bdate,'cost':round(bsum,6)})
    return {'total_usd':round(total,6),'daily':daily}

EXRATE_FILE = DATA_DIR / 'exrate_cache.json'  # USD→KRW 환율 캐시(10분)
def _usd_krw(cfg=None):
    """USD→KRW 실시간 환율을 반환한다. 무료 API로 조회하고 10분 캐시,
    실패 시 캐시값 또는 설정값(usdkrw_rate, 기본 1350)으로 폴백한다.
    반환: {'rate':float, 'source':'live'|'cache'|'fallback', 'updated_at':str}"""
    cfg=cfg or load_config()
    fallback=float((cfg or {}).get('usdkrw_rate') or 1350)
    cache=load_json(EXRATE_FILE,{}) or {}
    # 캐시 10분 이내면 그대로
    try:
        if cache.get('rate') and cache.get('ts'):
            age=datetime.utcnow().timestamp()-float(cache['ts'])
            if age < 600:
                return {'rate':float(cache['rate']),'source':'cache','updated_at':cache.get('updated_at','')}
    except Exception: pass
    # 실시간 조회(무료·키불필요). 실패해도 본 기능을 막지 않는다.
    try:
        import requests as _rq
        r=_rq.get('https://open.er-api.com/v6/latest/USD',timeout=6)
        j=r.json(); rate=float(((j.get('rates') or {}).get('KRW')) or 0)
        if rate>0:
            now=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')
            save_json(EXRATE_FILE,{'rate':rate,'ts':datetime.utcnow().timestamp(),'updated_at':now})
            return {'rate':rate,'source':'live','updated_at':now}
    except Exception: pass
    # 폴백: 최근 캐시가 있으면 그걸, 없으면 설정값
    if cache.get('rate'):
        return {'rate':float(cache['rate']),'source':'cache','updated_at':cache.get('updated_at','')}
    return {'rate':fallback,'source':'fallback','updated_at':''}

def _workroom_upload_dir(workroom_id):
    """작업실 전용 업로드 폴더(uploads/wr_<id>). workroom_id 없으면 공통 UPLOAD_DIR."""
    wid=re.sub(r'[^0-9a-zA-Z_-]','',str(workroom_id or ''))
    if not wid: return UPLOAD_DIR
    d=UPLOAD_DIR/('wr_'+wid); d.mkdir(parents=True,exist_ok=True); return d

def uploaded_images(workroom_id=None):
    """업로드된 이미지 목록. workroom_id를 주면 그 작업실 전용 폴더만 본다(공통 폴더 미포함).
       공통(전역)은 UPLOAD_DIR의 파일만(작업실 하위폴더 제외)."""
    base=_workroom_upload_dir(workroom_id)
    prefix=('/media/wr_'+re.sub(r'[^0-9a-zA-Z_-]','',str(workroom_id))+'/') if workroom_id else '/media/'
    out=[]
    if not base.exists(): return out
    for p in sorted(base.iterdir(),key=lambda x:x.stat().st_mtime,reverse=True):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            out.append({'name':p.name,'size':p.stat().st_size,
                        'url':prefix+urllib.parse.quote(p.name),'saved_at':datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds')})
    return out

def pick_attachment_paths(max_n=2, workroom_id=None):
    base=_workroom_upload_dir(workroom_id)
    files=[base/x['name'] for x in uploaded_images(workroom_id)]
    return [str(p.resolve()) for p in files[:max(0,int(max_n))]]

def attach_saved_images(d,max_n=1,workroom_id=None):
    from selenium.webdriver.common.by import By
    # 작업실 전용 업로드 파일만 첨부(없으면 첨부 안 함 — 대표님 지시: 이미지 없으면 넣지 말 것).
    paths=pick_attachment_paths(max_n,workroom_id=workroom_id) if workroom_id else pick_attachment_paths(max_n)
    if not paths: return 0,'저장 이미지 없음'
    inputs=[e for e in d.find_elements(By.CSS_SELECTOR,"input[type='file']") if _sel_vis(e)]
    if not inputs: return 0,'파일 첨부 입력란 없음'
    used=0
    for el,path in zip(inputs,paths):
        try: el.send_keys(path); used+=1
        except Exception: continue
    return used,(f'{used}개 첨부' if used else '첨부 입력 실패')

def _credential_fernet():
    """사이트 로그인정보용 서버측 암호화기. 세션키/전용 env에서 안정적으로 파생한다."""
    try:
        from cryptography.fernet import Fernet
        persisted=''
        try:
            kf=DATA_DIR/'secret.key'
            if kf.exists(): persisted=kf.read_text().strip()
        except Exception: pass
        raw=(os.environ.get('CHIRASHI_CREDENTIAL_KEY') or os.environ.get('CHIRASHI_SECRET') or persisted)
        if not raw: raise RuntimeError('credential key unavailable')
        raw=raw.encode()
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))
    except Exception:
        return None

def _encrypt_password(value):
    if not value: return ''
    if str(value).startswith('fernet:'): return str(value)
    f=_credential_fernet()
    if not f: raise RuntimeError('로그인정보 암호화를 위해 cryptography 패키지가 필요합니다')
    return 'fernet:'+f.encrypt(str(value).encode()).decode()

def _decrypt_password(value):
    if not value: return ''
    if not str(value).startswith('fernet:'): return str(value)  # 기존 평문 데이터 마이그레이션 입력
    f=_credential_fernet()
    if not f: return ''
    try: return f.decrypt(str(value)[7:].encode()).decode()
    except Exception: return ''

def load_sites():
    sites=load_json(SITES_FILE,[])
    for s in sites:
        s['mb_pass']=_decrypt_password(s.get('mb_pass_enc') or s.get('mb_pass',''))
    return sites

def save_sites(sites):
    out=copy.deepcopy(sites)
    for s in out:
        pw=s.pop('mb_pass','') or _decrypt_password(s.get('mb_pass_enc',''))
        if pw: s['mb_pass_enc']=_encrypt_password(pw)
        elif not s.get('mb_pass_enc'): s.pop('mb_pass_enc',None)
    save_json(SITES_FILE,out)

def load_signup_profiles(): return load_json(SIGNUP_PROFILES_FILE,{}) or {}
def save_signup_profiles(d): save_json(SIGNUP_PROFILES_FILE,d)

def _signup_origin(site):
    p=urllib.parse.urlsplit(site.get('site_url',''))
    return f'{p.scheme}://{p.netloc}' if p.scheme and p.netloc else site.get('site_url','').rstrip('/')

def _signup_form_measure(site):
    """가입 폼을 제출 없이 측정한다. 약관 다음 화면 조회까지만 하며 계정 생성은 하지 않는다."""
    from html.parser import HTMLParser
    import requests as _rq
    class FormParser(HTMLParser):
        def __init__(self): super().__init__(); self.forms=[]; self.cur=None; self.text=[]
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if tag=='form': self.cur={'action':a.get('action',''),'method':a.get('method','get').lower(),'fields':[]}
            elif self.cur is not None and tag in ('input','select','textarea','button'):
                safe={k:v for k,v in a.items() if k in ('type','name','id','minlength','maxlength','pattern','required','autocomplete','placeholder')}
                safe['tag']=tag; self.cur['fields'].append(safe)
        def handle_endtag(self,tag):
            if tag=='form' and self.cur is not None: self.forms.append(self.cur); self.cur=None
        def handle_data(self,data):
            t=' '.join(data.split())
            if t: self.text.append(t)
    base=_signup_origin(site); platform=site.get('platform') or 'gnuboard'
    sess=_rq.Session(); hdr={'User-Agent':'Mozilla/5.0 (signup-form-audit; administrator initiated)'}
    def signup_forms(parsed):
        out=[]
        for f in parsed.forms:
            keys=' '.join((x.get('name') or '')+' '+(x.get('id') or '') for x in f['fields']).lower()
            action=(f.get('action') or '').lower()
            pw_count=sum(1 for x in f['fields'] if (x.get('type') or '').lower()=='password')
            has_id=bool(re.search(r'(mb_id|member_id|login_id|user.?id|reg_mb_id)',keys))
            # 로그인 폼 오인 방지: 로그인 액션이 명확하면 제외
            is_login=('login' in action or 'login_check' in action) and pw_count<=1 and 'register' not in action and 'join' not in action
            # 가입 폼 인정 조건(완화): 액션이 가입계열 OR 비번2개(가입/비번확인) OR (아이디필드+비번 존재)
            looks_signup=(
                'register' in action or 'register_form_update' in action or 'member/join' in action or 'join' in action
                or pw_count>=2
                or (has_id and pw_count>=1)
                or (re.search(r'(password_re|password_confirm|passwd_confirm|mb_password_re)',keys) and re.search(r'(email|nick|name)',keys))
            )
            if looks_signup and not is_login: out.append(f)
        return out
    # 가입 URL 후보(설치 경로가 제각각이라 여러 표준 경로를 순차 시도). 첫 폼 발견에서 멈춘다.
    if site.get('signup_url'):
        url_candidates=[site['signup_url']]
    elif platform=='cafe24':
        url_candidates=[base+'/member/join.html',base+'/member/agreement.html',
                        base+'/member/join_step.html',base+'/myshop/join/agreement.html']
    else:
        url_candidates=[base+p for p in ('/bbs/register.php','/register.php',
                        '/gnu/bbs/register.php','/g5/bbs/register.php','/g4/bbs/register.php',
                        '/gnuboard5/bbs/register.php','/board/bbs/register.php','/bbs/register_form.php')]
    # ★가입 URL 자동탐지(대표님 지시 2026-09-08 자동가입 개선): 표준경로가 다 빗나가는 사이트(설치
    #   경로 커스텀) 대비 — 홈/로그인 페이지에서 register 링크를 긁어 후보 맨 앞에 넣는다.
    #   (가입폼 못찾음 실패의 상당수가 비표준 설치경로 때문. 실제 링크를 따라가면 구제됨.)
    if not site.get('signup_url'):
        try:
            _hp=sess.get(base+('/member/login.html' if platform=='cafe24' else '/bbs/login.php'),
                         timeout=12,verify=False,headers=hdr,allow_redirects=True)
            _hh=_hp.text or ''
            if len(_hh)<500:   # 로그인 경로가 없으면 홈에서
                _hh=(sess.get(base+'/',timeout=12,verify=False,headers=hdr).text or '')
            _pat=(r'href=["\']([^"\']*member/(?:join|agreement)[^"\']*)["\']' if platform=='cafe24'
                  else r'href=["\']([^"\']*register(?:_form)?\.php[^"\']*)["\']')
            _seen=set()
            for _h in re.findall(_pat,_hh,re.I):
                _full=urllib.parse.urljoin(base+'/',_h.replace('&amp;','&'))
                if _full not in _seen and 'logout' not in _full.lower():
                    _seen.add(_full); url_candidates.insert(0,_full)
        except Exception: pass
    url_candidates=list(dict.fromkeys(url_candidates))[:8]
    def _decode(r):
        # 한국 그누보드는 EUC-KR(cp949)이 많은데 HTTP 헤더에 charset이 없으면 requests가
        # ISO-8859-1로 오판독해 한글이 깨진다. <meta charset> 우선, 없으면 apparent_encoding.
        try:
            ctype=(r.headers.get('Content-Type') or '').lower()
            if 'charset=' in ctype:
                return r.text  # 헤더에 charset 명시 → requests가 이미 올바로 디코드
            head=r.content[:2048].decode('ascii','ignore').lower()
            m=re.search(r'charset=["\']?\s*([\w-]+)', head)
            enc=(m.group(1) if m else None) or r.apparent_encoding or 'utf-8'
            if enc.lower() in ('euc-kr','ks_c_5601-1987','ksc5601'): enc='cp949'
            return r.content.decode(enc, errors='replace')
        except Exception:
            return r.text
    def _measure_url(url):
        r=sess.get(url,timeout=20,verify=False,headers=hdr,allow_redirects=True); r.raise_for_status()
        html=_decode(r); measured_url=r.url
        # 그누보드 약관 화면이면 동의값을 세션에 전달해 실제 가입 폼까지만 조회한다.
        if platform!='cafe24' and not re.search(r'name=["\']mb_password["\']',html,re.I):
            form_url=urllib.parse.urljoin(r.url,'register_form.php')
            try:
                r2=sess.post(form_url,data={'agree':'1','agree2':'1'},timeout=20,verify=False,headers=hdr,allow_redirects=True)
                r2.raise_for_status(); html=_decode(r2); measured_url=r2.url
            except Exception: pass
        return html, measured_url
    url=url_candidates[0]; html=''; measured_url=url; forms=[]
    for cand in url_candidates:
        try:
            html, measured_url = _measure_url(cand)
        except Exception:
            continue
        p=FormParser(); p.feed(html)
        forms=signup_forms(p)
        if forms: url=cand; break
        url=cand  # 마지막 시도 URL 보존(폼 못 찾아도 Selenium 폴백에서 씀)
    p=FormParser(); p.feed(html)
    forms=signup_forms(p)
    # ★cafe24 실측(2026-09-11 tokyocrafts·honeytem 재현): join.html은 requests에도 Turnstile 챌린지 페이지로 오고,
    #   agreement.html의 약관 폼(action에 join 포함, 비밀번호 칸 없음)이 '가입폼'으로 오인돼 아래 Selenium 분기(챌린지
    #   해결→약관→진짜 폼)를 건너뛰었음 → '가입 폼(비밀번호 입력칸)에 도달 실패'. 비번 칸 없는 폼은 못 찾은 것으로 본다.
    if platform=='cafe24' and forms and not any((x.get('type') or '').lower()=='password' for f in forms for x in f['fields']):
        forms=[]
    # 정적 요청에서 상단 로그인폼만 보이는 사이트는 Selenium으로 약관 다음 화면까지 재측정한다.
    if not forms:
        try:
            from selenium.webdriver.common.by import By
            d=get_driver()
            # ★cafe24 가입페이지 Turnstile(대표님 승인 2026-09-11, 재실측 2회로 확정):
            #   /member/join.html은 requests·크롬 모두 veritas-hub 챌린지로 리다이렉트(JS, 1~3초 뒤). 폼이 안 떠
            #   531행 '가입 입력 폼을 찾지 못했습니다'로 죽던 지점. 1차 수정이 안 먹은 이유 둘: ①위 루프 실패 시
            #   url이 '마지막 후보'(myshop/join/agreement)라 폴백이 엉뚱한 페이지를 봄 ②1.5초 뒤 검사는 리다이렉트
            #   전. → cafe24는 join.html을 명시적으로 열고 최대 8초 리다이렉트를 관찰, 챌린지면 풀고 복귀 후 측정.
            #   실패해도 예외 삼켜 기존 흐름. cafe24에만 적용(그누보드는 기존과 동일).
            if platform=='cafe24':
                _ju=(site.get('signup_url') or base+'/member/join.html')
                def _agree_step():
                    """cafe24 약관 단계: agreement.html이면 동의 체크박스 전부 켜고 다음(회원가입)을 눌러 join.html로.
                       ★노드 실측 2026-09-11: join.html 직행 시 '약관에동의하셔야합니다' alert → agreement로 튕기며
                       Selenium이 alert에 막혀 측정이 죽던 사이트(unexpected alert open). 제출버튼 우선, a[href=join]은 최후."""
                    try:
                        _c=(d.current_url or '').lower()
                        if 'agreement' not in _c and '/agree' not in _c: return False
                        for cb in d.find_elements(By.CSS_SELECTOR,"input[type='checkbox']"):
                            try:
                                nm=((cb.get_attribute('name') or '')+' '+(cb.get_attribute('id') or '')).lower()
                                if ('agree' in nm or 'all' in nm) and not cb.is_selected():
                                    try: cb.click()
                                    except Exception: d.execute_script('arguments[0].checked=true;arguments[0].dispatchEvent(new Event("change",{bubbles:true}));',cb)
                            except Exception: continue
                        for sel in ("button[type='submit']","input[type='submit']","a.btnSubmit","a.btn_submit","button.btnSubmit","a[href*='join']","button","a"):
                            for el in d.find_elements(By.CSS_SELECTOR,sel):
                                try:
                                    if not el.is_displayed(): continue
                                    tx=((el.text or '')+' '+(el.get_attribute('value') or '')+' '+(el.get_attribute('alt') or '')).strip()
                                    if sel in ("button[type='submit']","input[type='submit']") or re.search(r'(다음|동의하고|동의|회원가입|가입하기|확인|next|agree)',tx,re.I):
                                        d.execute_script('arguments[0].click()',el); time.sleep(2); dismiss_alerts(d); return True
                                except Exception: continue
                    except Exception: pass
                    return False
                try:
                    d.get(_ju); time.sleep(1); dismiss_alerts(d)
                    if _agree_step(): time.sleep(1.5)   # 약관 먼저 요구하는 사이트 → 통과 후 join.html
                    _chal=False
                    for _ in range(16):   # 0.5초×16=8초: JS 리다이렉트·위젯 로드 대기
                        dismiss_alerts(d)
                        _cu=(d.current_url or '').lower()
                        if 'veritas-hub' in _cu or 'challenge' in _cu: _chal=True; break
                        if ('agreement' in _cu or '/agree' in _cu) and _agree_step(): time.sleep(1.5); continue
                        try: _ps=(d.page_source or '')[:20000].lower()
                        except Exception: _ps=''
                        if 'cf-turnstile' in _ps or ('turnstile' in _ps and 'sitekey' in _ps): _chal=True; break
                        if _ps and re.search(r'type=["\']?password',_ps): break   # 폼이 이미 떴으면 챌린지 없음
                        time.sleep(0.5)
                    if _chal:
                        _ok,_m,_t,_i=solve_captcha_with_2captcha(d,site,'turnstile',load_config())
                        add_log(f'[가입측정 Turnstile] {site.get("name") or base} — {_m}')
                        for _ in range(15):   # 콜백 제출 후 가입 페이지로 복귀 대기(최대 15초)
                            _c2=(d.current_url or '').lower()
                            if 'veritas-hub' not in _c2 and 'challenge' not in _c2: break
                            time.sleep(1)
                        _c3=(d.current_url or '').lower()
                        if 'veritas-hub' in _c3 or 'challenge' in _c3:
                            d.get(_ju); time.sleep(2)   # 자동 복귀 안 되면 재진입(챌린지 통과 쿠키 유지)
                        else:
                            time.sleep(1.5)
                        dismiss_alerts(d)
                        if _agree_step(): time.sleep(1.5)   # 챌린지 뒤에 약관 alert가 뜨는 사이트
                    url=_ju   # 이후 page_source 측정은 join.html 기준(마지막 후보 아님)
                except Exception as _e:
                    add_log(f'[가입측정 Turnstile 오류] {str(_e)[:80]}')
            else:
                d.get(url); time.sleep(1.5)
            for nm in ('agree','agree2'):
                for el in d.find_elements(By.CSS_SELECTOR,f"input[name='{nm}']"):
                    try:
                        if not el.is_selected(): el.click()
                    except: pass
            submits=d.find_elements(By.CSS_SELECTOR,"form#fregister input[type='submit'],form#fregister button[type='submit'],form[name='fregister'] input[type='submit']")
            if submits: d.execute_script('arguments[0].click()',submits[0]); time.sleep(2)
            html=d.page_source; measured_url=d.current_url
            p=FormParser(); p.feed(html); forms=signup_forms(p)
        except Exception: forms=[]
    if not forms: raise RuntimeError('가입 입력 폼을 찾지 못했습니다')
    # 비밀번호 필드가 있는 폼(=실제 가입폼)을 우선 선택한다. 검색폼·설문폼이 필드 수만
    # 많아 진짜 가입폼을 밀어내던 문제 방지(같은 조건이면 필드 많은 쪽).
    def _pwc(f): return sum(1 for x in f['fields'] if (x.get('type') or '').lower()=='password')
    form=max(forms,key=lambda f:(_pwc(f),len(f['fields']))); fields=[]
    for x in form['fields']:
        name=x.get('name') or ''; fid=x.get('id') or ''; typ=(x.get('type') or x.get('tag') or '').lower()
        role=''
        key=(name+' '+fid).lower()
        if re.search(r'(mb_id|member_id|login_id|user.?id)',key): role='id'
        elif typ=='password' and re.search(r'(_re\b|_confirm\b|confirm_|check)',key): role='password_confirm'
        elif typ=='password': role='password'
        elif re.search(r'(mb_email|e.?mail)',key): role='email'
        elif re.search(r'(mb_nick|nickname)',key): role='nickname'
        elif re.search(r'(mb_name|user.?name|real.?name)',key): role='name'
        elif re.search(r'(captcha|recaptcha|turnstile)',key): role='captcha'
        y=dict(x); y['role']=role; y['selector']=('#'+fid if fid else ('[name="'+name+'"]' if name else ''))
        fields.append(y)
    text=' '.join(p.text); rules={}
    def attr_int(role,key,default):
        vals=[]
        for f in fields:
            if f.get('role')==role:
                try:
                    if f.get(key) is not None: vals.append(int(f[key]))
                except: pass
        return vals[0] if vals else default
    rules['id_min']=attr_int('id','minlength',3 if platform!='cafe24' else 4)
    rules['id_max']=attr_int('id','maxlength',20 if platform!='cafe24' else 16)
    rules['password_min']=attr_int('password','minlength',10)
    rules['password_max']=attr_int('password','maxlength',64)
    # 화면 안내문에서 더 구체적인 최소 길이를 찾으면 반영한다.
    for pat,key in [(r'(?:아이디|ID)[^0-9]{0,30}(\d+)\s*(?:자|글자)\s*이상','id_min'),
                    (r'(?:비밀번호|패스워드)[^0-9]{0,30}(\d+)\s*(?:자|글자)\s*이상','password_min')]:
        m=re.search(pat,text,re.I)
        if m: rules[key]=int(m.group(1))
    rules['require_special']=bool(re.search(r'비밀번호.{0,80}(특수문자|특수 문자)',text,re.I))
    captcha=bool(re.search(r'(captcha|kcaptcha|g-recaptcha|turnstile|자동등록방지)',html,re.I))
    # 이메일 인증 필요 판단은 '필수/해야/완료' 같은 강제 신호가 있을 때만(오탐 축소 — 대표님 전략:
    # 일단 인증 없이 시도하고 정말 필요할 때만 인증. 이메일칸 언급만으론 인증 필요로 보지 않는다).
    email_verification=bool(
        re.search(r'(?:e-?mail|이메일)\s*(?:주소)?\s*(?:인증|확인)(?:을|를|이|가)?\s*(?:반드시|필수|해야|하셔야|완료해야|하여야)',text,re.I) or
        re.search(r'(?:인증\s*(?:메일|이메일)|인증\s*링크).{0,40}(?:발송|보냈|전송|클릭|확인)',text,re.I))
    signature=[(f.get('role'),f.get('name'),f.get('id'),f.get('type'),f.get('minlength'),f.get('maxlength'),f.get('pattern')) for f in fields]
    fingerprint=hashlib.sha256(json.dumps(signature,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'signup_url':url,'form_url':measured_url,'form_action':urllib.parse.urljoin(measured_url,form.get('action','')),
            'form_method':form.get('method','post'),'fields':fields,'rules':rules,'captcha':captcha,
            'email_verification_required':email_verification,
            'fingerprint':fingerprint,'measured_at':datetime.now().isoformat(timespec='seconds')}

def learn_signup_profile(site,force=False):
    host=urllib.parse.urlsplit(_signup_origin(site)).netloc.lower()
    with _signup_learn_locks[host]:
        profiles=load_signup_profiles(); old=profiles.get(host) or {}
        # 올인원 실행 때마다 상대 사이트를 두드리지 않는다. 최근 정상 측정값은 30분 재사용.
        if old and not force:
            try: fresh=(datetime.now()-datetime.fromisoformat(old.get('measured_at',''))).total_seconds()<1800
            except: fresh=False
            if fresh:
                profile=dict(old); profile['cached']=True; changed=False
                site.update({'signup_profile_host':host,'signup_rules':profile['rules'],'signup_url':profile['signup_url'],
                             'signup_profile_version':profile['version'],'signup_profile_changed':False,
                             'signup_profile_measured_at':profile['measured_at'],'signup_has_captcha':profile['captcha'],
                             'signup_email_verification':bool(profile.get('email_verification_required'))})
                return profile
        measured=_signup_form_measure(site); changed=old.get('fingerprint')!=measured['fingerprint']; history=list(old.get('history') or [])
        if old and changed:
            history.append({'fingerprint':old.get('fingerprint'),'rules':old.get('rules',{}),'measured_at':old.get('measured_at')})
        history=history[-20:]
        profile={**measured,'host':host,'version':int(old.get('version',0))+(1 if changed else 0),
                 'seen_count':int(old.get('seen_count',0))+1,'success_count':int(old.get('success_count',0)),
                 'failure_count':int(old.get('failure_count',0)),'history':history,'cached':False}
        profiles[host]=profile; save_signup_profiles(profiles)
        site.update({'signup_profile_host':host,'signup_rules':profile['rules'],'signup_url':profile['signup_url'],
                     'signup_profile_version':profile['version'],'signup_profile_changed':changed,
                     'signup_profile_measured_at':profile['measured_at'],'signup_has_captcha':profile['captcha'],
                     'signup_email_verification':bool(profile.get('email_verification_required'))})
        return profile

def load_config():
    d={'brand':'인천홍마니','phone':'01082755736','phones':'','openai_key':'','model':'gpt-4o-mini',
       'openai_admin_key':'','openai_monthly_budget_usd':20.0,
       # ★글 생성 엔진 제공자(대표님 2026-09-11 '비용 아끼고 싶다'): openai(유료) / nvidia(build.nvidia.com 무료 엔드포인트, OpenAI 호환)
       'llm_provider':'openrouter','nvidia_api_key':'','nvidia_model':'nvidia/nemotron-3-ultra-550b-a55b',
       'openrouter_api_key':'','openrouter_model':'deepseek/deepseek-v4-flash-0731',   # openrouter.ai (저가·OpenAI 호환·실비용 응답)
       'openai_input_price_per_million':0.15,'openai_cached_input_price_per_million':0.075,
       'openai_output_price_per_million':0.60,
       'workers':4,'password':'admin1234','post_delay':30,'daily_limit':0,
       'use_gpt':False,'telegram_token':'','telegram_chat_id':'',
       'notify_done':False,'notify_fail':True,'update_token':'',
       'telegram_control':False,'backup_time':'','verify_enabled':True,'mix_keywords':True,
       'block_unpaid':True,
       'google_api_key':'','google_cx':'','brave_api_key':'','search_provider':'brave','discover_enabled':True,
       'discover_daily_target':500,'discover_query_limit':500,'discover_batch':50,'discover_keywords':'',
       'finder_ratio':1.0,   # 발굴 쿼리 중 '게시판 찾기'(finder) 비중(1.0=100%). 업소 직접키워드는 미사용
       'site_goal':500,   # 실제 발행 가능 사이트 확보 목표(대시보드 진행률 표시용)
       # 웹빌더/템플릿 플랫폼 등 발행 불가 도메인 제외 목록(한 줄에 하나, 발굴에서 즉시 제외). 설정에서 관리.
       'excluded_domains':'isweb.co.kr\nimweb.me\nimweb.io\nmodoo.at\ncreatorlink.net\nwixsite.com\nweebly.com\nblog.me',
       'discover_direct_queries':'','video_url':'','landing_url':'','post_email':'','guest_post_password':'',
       # 실제 이메일(IMAP) 인증 — 지메일 등. 설정 시 임시메일 대신 '내지메일+랜덤@gmail.com' 플러스주소로
       # 유니크 발급하고 IMAP으로 인증메일을 읽는다(일회용 도메인 차단 게시판도 통과). App Password 사용.
       'imap_email':'','imap_password':'','imap_host':'imap.gmail.com',
       'twocaptcha_api_key':'','twocaptcha_enabled':False,
       'http_publish_enabled':False,  # browserless(requests) — gnuboard5 anti-CSRF(token)로 대부분 게시판이 거부. 보류.
       'public_base_url':'https://google.twseo.kr',  # 업로드 이미지 절대 URL 기준 도메인(외부 게시판 로드용)
       'twocaptcha_price_recaptcha_usd':0.003,'twocaptcha_price_image_usd':0.0005,
       'brave_price_per_query_usd':0.005,  # Pro 플랜 기준 쿼리당 $0.005(설정 탭에서 변경 가능)
       'auto_pipeline_enabled':True,'auto_pipeline_batch':20,   # ★배치 대폭↑(대표님 '대기 너무 쌓임'): 유입>소진 병목 해소. 병렬가입으로 감당.
       'min_interval_minutes':1,   # 발행 간격(분): 1=사실상 무간격, daily_limit=0=하루 무제한(대표님 요청)
       'publish_loop_enabled':True,'publish_interval_sec':300,   # 24시간 상시발행 루프(5분 주기 큐 보충)
       'workroom_workers':4,   # 작업실별 전용 발행 워커(=동시 크롬) 수 상한. VPS 사양에 맞게 조절(4vCPU→4)
       'strict_screen':True,   # ★빡센 검수(대표님 지시): 홍보글 흔적 있는 방치·개방 게시판만 ready 통과
       'signup_parallel':4,   # ★자동가입 전담 병렬 수(대표님 지시). 후보들을 동시에 가입 시도. 전역 크롬상한 내에서.

       'publish_fanout':4,   # ★한 조합을 발행가능 사이트들에 '동시에' 뿌리는 병렬 크롬 수(대표님 '속도'). 1=순차
       'publish_max_chromes':6,   # ★전역 동시 크롬 상한(모든 작업실 슬롯×fanout 통틀어). VPS 메모리 보호. 크롬1개~400MB

       'vps_reserve_mb':350,'vps_mb_per_worker':300,   # 메모리 가드 민감도(낮출수록 워커 더 허용·OOM위험↑)

       'discover_interval_sec':600,   # 발굴 주기 10분(크레딧 절약). 목표 도달 시 자동 중단
       'pipeline_interval_sec':60,   # 전환(후보→가입→발행테스트) 전용 루프 주기 — 발굴과 독립. ★120→60(소진속도↑)
       'login_signup_per_cycle':5,    # 로그인 필요 게시판 자동가입 주기당 처리 수(IMAP 설정 후 백로그 소진용)
       # ★Bright Data 프록시(CF 걸린 Cafe24 로그인 우회용) — 크레덴셜 넣으면 활성. 비면 미사용.
       #   Residential Proxies 또는 Web Unlocker의 호스트/포트/유저/비번. CF 사이트에만 선택적 사용(비용↓).
       'proxy_enabled':False,'proxy_host':'','proxy_port':'','proxy_user':'','proxy_pass':'',
       'proxy_only_for_cf':True,   # True=Cloudflare 감지된 사이트에만 프록시 사용(비용 절약), False=전체
       # ★Bright Data Web Unlocker API(CF·캡차 자동해결). 주거용은 회사메일 인증 필요라, 개인가입은
       #   Web Unlocker 사용. POST api.brightdata.com/request로 URL 보내면 뚫린 HTML 반환. 1.5$/1000건.
       'unlocker_enabled':False,'unlocker_api_key':'','unlocker_zone':'web_unlocker1',
       # ★Bright Data Scraping Browser(CF+로그인 사이트용 원격 크롬). Web Unlocker는 로그인 세션
       #   미지원이라, 타카고 등 로그인 필요 Cafe24는 이걸로. Selenium Remote로 brd.superproxy.io:9515 연결.
       #   endpoint 예: brd-customer-xxx-zone-scraping_browser:PASS@brd.superproxy.io:9515
       'sbr_enabled':False,'sbr_endpoint':'',   # 전체 endpoint(user:pass@host:port) 한 줄로 저장
       'sbr_country':'',   # ★Scraping Browser 접속국가. 빈값=주입안함(Browser API 자동IP, 권장).
                           #   'kr' 주입이 endpoint customer name을 깨 'Wrong customer name' 오류 유발(2026-09-09)
                           #   → 기본 끔. Browser API가 자동으로 최적 IP 선택하므로 geo 불필요.
       # ★자동가입 고정계정(대표님 지시 2026-09-08: 랜덤 대신 통일). 비면 기존 랜덤 생성.
       #   설정 시 모든 자동가입에 이 아이디/비번 사용(대표님이 관리·중복ID 감소).
       'signup_fixed_id':'','signup_fixed_pw':'',
       'log_token':'cae3aaa53d6f3576a1c1f6a258f79129'}   # 읽기전용 로그 조회 토큰(?token= 로 /api/logs·/api/worker-log 접근)
    c=load_json(CONFIG_FILE,None)
    if c is None or not isinstance(c,dict): save_json(CONFIG_FILE,d); return d.copy()
    for k,v in d.items():
        if k not in c: c[k]=v
    # OpenAI 퇴출(2026-09-11): 구버전 config의 llm_provider='openai'/빈값은 openrouter로 정규화(설정 select에 openai 항목 없음 → 빈 표시·'' 저장 방지)
    if (c.get('llm_provider') or '').strip().lower() not in ('openrouter','nvidia'): c['llm_provider']='openrouter'
    # 완전자동 재설계 1회 마이그레이션: 기존 config가 자동화를 꺼둔 상태여도 1회만 켠다.
    # (이후 대표님이 의도적으로 끄면 autofull_migrated=True라 다시 켜지 않는다)
    if not c.get('autofull_migrated'):
        c['discover_enabled']=True
        c['auto_pipeline_enabled']=True
        c.setdefault('daily_limit',0)
        c.setdefault('min_interval_minutes',1)
        c.setdefault('publish_loop_enabled',True)
        c.setdefault('publish_interval_sec',300)
        c.setdefault('discover_interval_sec',600)
        c.setdefault('log_token','cae3aaa53d6f3576a1c1f6a258f79129')
        c['autofull_migrated']=True
        try: save_json(CONFIG_FILE,c)
        except Exception: pass
    # 1회 마이그레이션: 발행 간격 1분 · 하루 무제한(daily_limit=0)로 전환(대표님 요청).
    # 기존 config·모든 사이트를 강제로 1/0으로 맞춘다. 이후 개별 조정은 자유.
    if not c.get('interval1_unlimited_migrated'):
        c['min_interval_minutes']=1
        c['daily_limit']=0
        try:
            _ss=load_sites(); _ch=False
            for _s in _ss:
                if _s.get('min_interval_minutes')!=1: _s['min_interval_minutes']=1; _ch=True
                if _s.get('daily_limit')!=0: _s['daily_limit']=0; _ch=True
            if _ch: save_sites(_ss)
        except Exception: pass
        c['interval1_unlimited_migrated']=True
        try: save_json(CONFIG_FILE,c)
        except Exception: pass
    # 1회 마이그레이션: sbr_country='kr' 제거 — endpoint customer name 깨서 'Wrong customer name'
    #   오류로 SBR(Cafe24 로그인 발행) 전부 실패시킴(2026-09-09). Browser API 자동IP로 충분.
    if not c.get('sbr_country_cleared'):
        if str(c.get('sbr_country') or '')=='kr': c['sbr_country']=''
        c['sbr_country_cleared']=True
        try: save_json(CONFIG_FILE,c)
        except Exception: pass
    # 1회 마이그레이션: IMAP 설정 후 쌓인 로그인 게시판 후보 소진 위해 주기당 처리 2→5.
    if not c.get('login5_migrated'):
        if int(c.get('login_signup_per_cycle',0) or 0) in (0,2): c['login_signup_per_cycle']=5
        c['login5_migrated']=True
        try: save_json(CONFIG_FILE,c)
        except Exception: pass
    # 1회 마이그레이션: VPS 업그레이드(4vCPU/8GB)에 맞춰 동시 발행 워커 상한 3→4.
    # (기존 config가 옛 기본값 3이면만 올리고, 대표님이 따로 조정한 값은 존중)
    if not c.get('wr_workers4_migrated'):
        if int(c.get('workroom_workers',0) or 0) in (0,3):
            c['workroom_workers']=4
        c['wr_workers4_migrated']=True
        try: save_json(CONFIG_FILE,c)
        except Exception: pass
    return c

def get_proxies(cfg=None):
    """Bright Data 등 프록시 크레덴셜이 설정돼 있으면 requests용 proxies dict를 반환, 없으면 None(직접연결).
       ★크레덴셜 오면 바로 쓰도록 미리 준비(2026-09-08). 실제 발행 로직 연결은 크레덴셜 확인 후.
       host/port/user/pass 중 host·port만 있어도 동작(인증 없는 프록시 허용)."""
    cfg=cfg or load_config()
    if not cfg.get('proxy_enabled'): return None
    host=str(cfg.get('proxy_host') or '').strip()
    port=str(cfg.get('proxy_port') or '').strip()
    if not host or not port: return None
    user=str(cfg.get('proxy_user') or '').strip()
    pw=str(cfg.get('proxy_pass') or '').strip()
    auth=(urllib.parse.quote(user,safe='')+':'+urllib.parse.quote(pw,safe='')+'@') if user else ''
    url=f'http://{auth}{host}:{port}'
    return {'http':url,'https':url}

def proxy_get(url, cfg=None, use_proxy=None, **kw):
    """requests.get 래퍼 — use_proxy=True면 프록시 경유(CF 우회), None/False면 직접.
       프록시 요청 실패 시 직접연결로 1회 폴백(프록시 죽어도 발행 안 끊기게)."""
    import requests as _rq
    cfg=cfg or load_config()
    kw.setdefault('timeout',15); kw.setdefault('verify',False)
    kw.setdefault('headers',{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'})
    proxies=get_proxies(cfg) if use_proxy else None
    try:
        return _rq.get(url,proxies=proxies,**kw)
    except Exception:
        if proxies:   # 프록시 실패 → 직접연결 폴백
            try: return _rq.get(url,proxies=None,**kw)
            except Exception: return None
        return None

def unlocker_enabled(cfg=None):
    cfg=cfg or load_config()
    return bool(cfg.get('unlocker_enabled') and str(cfg.get('unlocker_api_key') or '').strip())

def unlocker_fetch(url, cfg=None, timeout=90):
    """Bright Data Web Unlocker로 url을 요청해 CF·캡차 통과된 HTML(문자열)을 반환. 실패시 None.
       (CF 걸린 Cafe24 게시판 GET 검증·글목록 확인용. 실제 폼 제출 로그인은 별도.)
       ★2026-09-08 신설: 대표님이 Web Unlocker 존 생성(요청당 과금, CAPTCHA 자동해결)."""
    import requests as _rq
    cfg=cfg or load_config()
    key=str(cfg.get('unlocker_api_key') or '').strip()
    zone=str(cfg.get('unlocker_zone') or 'web_unlocker1').strip()
    if not key: return None
    # ★실측(2026-09-08): 4회 중 3회 CF 통과, 1회 일시 잔존(5KB 챌린지). CF 잔존/빈응답이면 최대 2회 재시도.
    for attempt in range(3):
        try:
            r=_rq.post('https://api.brightdata.com/request',
                       headers={'Content-Type':'application/json','Authorization':f'Bearer {key}'},
                       json={'zone':zone,'url':url,'format':'raw'},timeout=timeout)
            if r.status_code>=400:
                add_log(f'[Unlocker] 오류 {r.status_code}: {(r.text or "")[:80]}','발행')
                return None
            body=r.text or ''
            low=body[:3000].lower()
            cf_left=(len(body)<8000 and ('just a moment' in low or 'cf-chl' in low or '_cf_chl' in low or 'challenge-platform' in low))
            if body and not cf_left:
                return body            # 정상(CF 통과)
            if attempt<2:
                time.sleep(2); continue  # CF 잔존/빈응답 → 재시도
            return body or None          # 마지막 시도 결과 그대로
        except Exception as e:
            if attempt<2: time.sleep(2); continue
            add_log(f'[Unlocker] 예외: {str(e)[:80]}','발행')
            return None

TWOCAPTCHA_CACHE_FILE=os.path.join(DATA_DIR,'twocaptcha_balance.json')

def _twocaptcha_usage_summary(cfg):
    key=(cfg.get('twocaptcha_api_key') or '').strip()
    if not key:
        return {'ok':False,'disabled':True,'error':'2captcha API 키 없음','currency':'USD','balance':0.0,'remaining_usd':0.0,'charged_since_last_check_usd':0.0,'updated_at':None}
    if not cfg.get('twocaptcha_enabled',False):
        return {'ok':False,'disabled':True,'error':'2captcha 비활성화','currency':'USD','balance':0.0,'remaining_usd':0.0,'charged_since_last_check_usd':0.0,'updated_at':None}
    try:
        import requests   # 모듈 전역에 없음 — 지역 import(이게 없어 'requests is not defined'로 잔액조회 크래시→미연결 표시됨)
        r=requests.get('https://2captcha.com/res.php',params={'key':key,'action':'getbalance','json':'1'},timeout=20)
        raw=(r.text or '').strip()
        payload=None
        try:
            payload=r.json()
        except Exception:
            payload=None

        balance=None
        if isinstance(payload,dict):
            if isinstance(payload.get('request'), dict):
                value=payload['request'].get('balance')
                if value is not None: balance=float(value)
            elif payload.get('request') is not None:
                value=payload.get('request')
                if isinstance(value,(int,float,str)):
                    balance=float(str(value))
        if balance is None and raw.startswith('OK|'):
            balance=float(raw.split('|',1)[1].strip())
        if balance is None and raw.startswith('{'):
            try:
                j=json.loads(raw)
                if isinstance(j,dict):
                    req=j.get('request')
                    if isinstance(req,dict):
                        balance=float(req.get('balance',0))
                    elif isinstance(req,(int,float,str)):
                        balance=float(str(req))
            except Exception:
                pass
        if balance is None:
            raise ValueError(f'2captcha 응답 파싱 실패: {raw[:140]}')

        cache=load_json(TWOCAPTCHA_CACHE_FILE,{}) or {}
        prev=cache.get('balance')
        delta=0.0
        if prev is not None and balance < prev:
            delta=round(max(0.0, float(prev)-float(balance)), 6)
        cache={'balance':float(balance),'charged_since_last_check_usd':delta,'updated_at':datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}
        save_json(TWOCAPTCHA_CACHE_FILE,cache)

        return {'ok':True,'source':'2captcha','currency':'USD','balance':round(float(balance),6),'remaining_usd':round(float(balance),6),'charged_since_last_check_usd':round(delta,6),'updated_at':cache['updated_at']}
    except Exception as e:
        return {'ok':False,'disabled':False,'error':str(e)[:180],'currency':'USD','balance':0.0,'remaining_usd':0.0,'charged_since_last_check_usd':0.0,'updated_at':None}

def save_config(c): save_json(CONFIG_FILE,c)
def _log_category(msg):
    """로그 메시지 앞부분으로 작업 종류를 자동 분류(워커 실행로그 탭 필터용).
    기존 add_log 호출을 안 고쳐도 [발굴]/[검수]/[가입]/[발행] 프리픽스로 자동 분류된다."""
    m=str(msg)
    if any(k in m for k in ['[발굴','[후보','[게시판','발굴','Brave']): return '발굴'
    if any(k in m for k in ['[검수','검수','[재검수']): return '검수'
    if any(k in m for k in ['[자동가입','[가입','가입']): return '가입'
    if any(k in m for k in ['[발행','[예약','[워커','[자동등록','발행','등록','제출','2captcha','캡차']): return '발행'
    if any(k in m for k in ['[자동정리','[사이트 정리','탈락','정리','삭제']): return '정리'
    if any(k in m for k in ['[자동파이프라인','[파이프라인','파이프라인']): return '파이프라인'
    return '기타'

def add_log(msg, category=None):
    cat=category or _log_category(msg)
    with _json_lock(LOG_FILE):
        logs=load_json(LOG_FILE,[])
        logs.append({'time':datetime.now().strftime('%H:%M:%S'),'msg':msg,'cat':cat})
        if len(logs)>800: logs=logs[-800:]
        save_json(LOG_FILE,logs)

# ==================== 전화번호 표기 ====================
def format_phone(phone):
    d=re.sub(r'[^0-9]','',phone or '01082755736')
    if len(d)==11: return f'{d[:3]}-{d[3:7]}-{d[7:]}'
    if len(d)==10: return f'{d[:3]}-{d[3:6]}-{d[6:]}'
    return phone

PHONE_SEPS=['↔','●','=','~','-',' ','.','·','_','ㆍ','∼','◆','ㅡ']
TITLE_EXTRAS=['확실한','24시','검증된','재방문200%','인기','추천','친절한','예약가능','빠른안내','만족도높은',
              '실시간','당일예약','프리미엄','가성비','단골많은','후회없는','1위','핫플','고급진','편안한',
              '깔끔한','안전한','합리적인','신상','VIP','친절상담','바로연결','문의환영','강력추천','최고의']
def tel_href(raw):
    """전화번호 → 모바일에서 터치 시 바로 걸리는 tel: 링크(대표님 지시 2026-09-11 '터치하면 전화연결').
       표시용 표기(도배방지로 기호 섞음)는 그대로 두고 href에만 숫자만 넣는다 — 표기 변형과 실제 발신을 분리.
       게시판이 tel: 스킴을 걷어내면 그냥 글자로 남아 지금과 같음(손해 없음)."""
    return 'tel:'+re.sub(r'\D','',str(raw or ''))

def format_phone_random(phone):
    """제목용 번호 랜덤 변형 (매번 다른 기호·표기). 사람은 읽을 수 있게 유지."""
    d=re.sub(r'[^0-9]','',phone or '01082755736')
    if len(d)==11: a,b,c=d[:3],d[3:7],d[7:]
    elif len(d)==10: a,b,c=d[:3],d[3:6],d[6:]
    else: return phone
    s1=random.choice(PHONE_SEPS); s2=random.choice(PHONE_SEPS)
    # O/I 치환 여부(랜덤): 010->O1O / OIO 등, 사람이 읽을 수 있게 유지
    sub=random.choice([lambda x:x, lambda x:x.replace('0','O'),
                       lambda x:x.replace('0','O').replace('1','I')])
    a2,b2,c2=sub(a),sub(b),sub(c)
    style=random.random()
    if style<0.30:      out=f'[{a2}]{s1}{b2}{s2}{c2}'      # [010]↔8275↔5736
    elif style<0.55:    out=f'[{a2}{s1}{b2}{s2}{c2}]'      # [010●8275●5736]
    else:               out=f'{a2}{s1}{b2}{s2}{c2}'        # O1O=2572=3859
    return out

def get_phones(cfg):
    ph=cfg.get('phones','') or ''
    lst=[re.sub(r'\s+','',x) for x in ph.splitlines() if x.strip()]
    if not lst and cfg.get('phone'): lst=[cfg['phone']]
    return lst or ['01082755736']

def pick_phone(cfg):
    return random.choice(get_phones(cfg))

def build_title(r,s,b,cfg,raw=None):
    """★제목 형식 고정(대표님 지시 2026-09-08, 무조건): 메인키워드1 + 번호 + 키워드2 + 키워드3.
       r=키워드1(메인), s=키워드2, b=키워드3. 순서는 절대 안 바꾼다(셔플·강조어 없음).
       도배 방지는 전화번호 표기 변형(기호·O/I 랜덤)만으로 처리한다."""
    raw=raw or pick_phone(cfg)
    ph=format_phone_random(raw)   # 번호 표기만 매번 살짝 변형(순서·키워드는 고정)
    return f'{r} {ph} {s} {b}'.strip()[:140], raw

# ==================== 키워드 풀 (엑셀/CSV 랜덤 치환) ====================
REGION_ORDER=('인천','경기','서울','충남','충북','세종','전북','전남','경상','경북','강원','제주')
_region_order_cache=None

def _province_bucket(name):
    """시도명을 대표님 지정 12개 지역 그룹으로 정규화한다."""
    n=str(name or '').strip()
    rules=(
        ('인천',('인천',)),('경기',('경기',)),('서울',('서울',)),
        ('충남',('충남','충청남','대전')),('충북',('충북','충청북')),
        ('세종',('세종',)),('전북',('전북','전라북')),
        ('전남',('전남','전라남','광주')),
        ('경북',('경북','경상북')),
        ('경상',('경상','경남','경상남','부산','대구','울산')),
        ('강원',('강원',)),('제주',('제주',)),
    )
    for bucket,aliases in rules:
        if any(n.startswith(a) for a in aliases): return bucket
    return ''

def _region_order_map():
    """시·군·구·읍·면·동도 상위 시도 순서로 정렬할 수 있게 역색인을 만든다."""
    global _region_order_cache
    if _region_order_cache is not None: return _region_order_cache
    rank={name:i for i,name in enumerate(REGION_ORDER)}; out={}
    data=load_json(REGIONS_FILE,{})
    for province,districts in (data.items() if isinstance(data,dict) else []):
        bucket=_province_bucket(province); r=rank.get(bucket,99)
        names=[province,bucket]
        for district,dongs in ((districts or {}).items() if isinstance(districts,dict) else []):
            names.extend([district,re.sub(r'(시|군|구)$','',district)])
            for dong in (dongs or []): names.append(str(dong).strip())
        for name in names:
            name=str(name or '').strip()
            if name and (name not in out or r<out[name]): out[name]=r
    for alias in ('인천','경기','서울','충남','충북','세종','전북','전남','경상','경남','경북','강원','제주'):
        out[alias]=rank.get(_province_bucket(alias),99)
    _region_order_cache=out
    return out

def _keyword_region_rank(row):
    region=str((row or {}).get('지역','') or '').strip()
    bucket=_province_bucket(region)
    if bucket: return REGION_ORDER.index(bucket)
    index=_region_order_map()
    if region in index: return index[region]
    # 풀의 첫 칸이 '강남셔츠룸'처럼 지역+업종 전체 키워드인 경우도
    # 가장 긴 행정구역 접두어를 찾아 상위 시도 순서를 적용한다.
    matches=[(len(name),rank) for name,rank in index.items() if len(name)>=2 and region.startswith(name)]
    return max(matches,key=lambda x:x[0])[1] if matches else 99

def order_keywords(rows):
    """동일 지역 안에서는 사용자가 입력한 기존 순서를 그대로 보존한다."""
    return sorted(list(rows or []),key=_keyword_region_rank)

def load_keywords(): return order_keywords(load_json(KEYWORDS_FILE,[]))
def save_keywords(k): save_json(KEYWORDS_FILE,order_keywords(k))

def pool_columns(pool):
    """풀에서 지역/서비스/브랜드 열별 고유값 목록(순서보존)."""
    dd=lambda L:list(dict.fromkeys([x for x in L if x]))
    R=dd([(x.get('지역') or '').strip() for x in pool])
    S=dd([(x.get('서비스') or '').strip() for x in pool])
    B=dd([(x.get('브랜드') or '').strip() for x in pool])
    return R,S,B

def pick_keywords(pool, cfg):
    """키워드1(지역)을 먼저 뽑고, 키워드2·3은 반드시 같은 지역 행 안에서만 조합한다."""
    if not pool: return {'지역':'','서비스':'','브랜드':''}
    row=random.choice(pool)
    if cfg.get('mix_keywords',True):
        region=(row.get('지역') or '').strip()
        matched=[x for x in pool if (x.get('지역') or '').strip()==region]
        services=[(x.get('서비스') or '').strip() for x in matched if (x.get('서비스') or '').strip()]
        brands=[(x.get('브랜드') or '').strip() for x in matched if (x.get('브랜드') or '').strip()]
        return {'지역':region,
                '서비스':(random.choice(services) if services else ((row.get('서비스') or '').strip() or '지역정보')),
                '브랜드':(random.choice(brands) if brands else ((row.get('브랜드') or '').strip() or f'{region}추천'))}
    return {'지역':(row.get('지역') or ''),'서비스':(row.get('서비스') or ''),'브랜드':(row.get('브랜드') or '')}

# ==================== 콘텐츠 중복 방지 (제목/본문 항상 다르게) ====================
_uniq_lock=threading.Lock()
def _norm_text(html):
    """태그·공백 제거한 순수 텍스트(중복 판정 기준)."""
    return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html or '')).strip()

def remember_if_unique(title, body, force=False):
    """제목/본문이 과거에 없던 새 콘텐츠면 기록하고 True. 이미 있으면 False."""
    th=hashlib.sha1((title or '').encode('utf-8')).hexdigest()
    bh=hashlib.sha1(_norm_text(body).encode('utf-8')).hexdigest()
    with _uniq_lock:
        u=load_json(UNIQ_FILE,{'t':[],'b':[]})
        if not isinstance(u,dict): u={'t':[],'b':[]}
        ts=set(u.get('t',[])); bs=set(u.get('b',[]))
        if not force and (th in ts or bh in bs): return False
        u['t']=(u.get('t',[])+[th])[-8000:]; u['b']=(u.get('b',[])+[bh])[-8000:]
        save_json(UNIQ_FILE,u); return True

def force_unique_html(html):
    """(최후수단) 눈에 거의 안 띄는 유니크 토큰을 붙여 강제로 다르게."""
    nonce=secrets.token_hex(5)
    return html+f'<p style="font-size:1px;line-height:1px;color:#ffffff;opacity:0.02;margin:0;">{nonce}</p>'

# ==================== 리치 HTML 생성 (bm21 스타일 · 변형/서비스특화/중복방지) ====================
# 서비스 유형별 특화 어휘 (미등록 서비스는 DEFAULT_FLAVOR 사용)
SERVICE_FLAVOR = {
    '셔츠룸':  {'mood':['깔끔하고 세련된','밝고 활기찬','트렌디한 감성의']},
    '노래방':  {'mood':['신나고 흥겨운','프라이빗하고 아늑한','최신 곡이 가득한']},
    '가라오케':{'mood':['고급스럽고 품격 있는','세련되고 정갈한','차분하고 프라이빗한']},
    '호빠':    {'mood':['활기차고 친근한','편안하고 밝은','유쾌한']},
    '룸싸롱':  {'mood':['프라이빗하고 고급스러운','조용하고 차분한','정중한 응대의']},
    '가요주점':{'mood':['정겹고 흥겨운','편안한','활기찬']},
}
DEFAULT_FLAVOR={'mood':['편안하고 세련된','밝고 활기찬','아늑한']}
RELATED_POOL=['하이퍼블릭','노래빠','쓰리노','가요광장','터치룸','노래클럽','가라오케','호빠','룸싸롱','셔츠룸','퍼블릭','비즈니스바','가요주점']

# ── 메인 키워드 1개 → 지역(구/동)+업종 분리, 서브2·3 자동 생성 (대표님 지시 2026-09-07) ──
# "메인만 한 줄씩" 입력 시: 교동노래방 → 지역='교동', 업종='노래방'.
# 서브2(서비스)·서브3(브랜드)는 그 구/동에 맞춰 매 발행마다 랜덤 조합한다.
SERVICE_SUFFIXES=['하이퍼블릭','다국적노래방','가요주점','비즈니스바','노래클럽','가라오케','룸싸롱','풀싸롱',
                  '셔츠룸','쓰리노','퍼블릭','노래빠','노래방','호빠','룸','바','안마','마사지','테라피',
                  '출장마사지','출장안마','스웨디시','건마']

def _split_main_keyword(main):
    """메인 키워드에서 (지역=구/동, 업종) 분리. 업종 접미사를 뒤에서부터 최장일치로 찾는다.
       예) '교동노래방'→('교동','노래방'), '강남하이퍼블릭'→('강남','하이퍼블릭'),
           업종을 못 찾으면 지역=전체, 업종='' 반환."""
    m=str(main or '').strip()
    for suf in sorted(SERVICE_SUFFIXES,key=len,reverse=True):
        if m.endswith(suf) and len(m)>len(suf):
            return m[:-len(suf)].strip(), suf
    return m, ''

def _fix_dong(region):
    """지역명이 행정구역 접미사(동/읍/면/리/가/구/시/군/역)로 안 끝나고 숫자로도 안 끝나면 '동' 붙임.
       (대표님 지시 2026-09-08: 풍세→풍세동)"""
    r=str(region or '').strip()
    if r and not re.search(r'(동|읍|면|리|가|구|시|군|역)$',r) and not re.search(r'\d$',r):
        return r+'동'
    return r

def _fix_kw_dong(kw):
    """직접입력 3개 조합에서 '지역 접두어'를 찾아 '동' 삽입. 업종 약칭 목록에 의존하면 오교정
       (부여읍하퍼→부여읍하퍼동 같은)이 나므로, 3개 키워드의 '공통 앞부분'을 지역으로 본다.
       예) 풍세미러룸/풍세풀싸롱/풍세유흥주점 → 공통 '풍세' → '풍세동'으로 각 키워드 앞부분 치환.
       (대표님 지시 2026-09-08). 공통접두어가 2글자 이상이고 행정접미사 없을 때만 적용(안전)."""
    if not isinstance(kw,dict): return kw
    vals=[str(kw.get(f) or '').strip() for f in ('지역','서비스','브랜드')]
    present=[v for v in vals if v]
    if len(present)<2: return kw   # 조합 아니면(자동생성 등) 건드리지 않음
    # 공통 접두어 계산
    pre=present[0]
    for v in present[1:]:
        i=0
        while i<len(pre) and i<len(v) and pre[i]==v[i]: i+=1
        pre=pre[:i]
    pre=pre.strip()
    # 공통 접두어가 지역명답고(2~5글자) 행정접미사·숫자로 안 끝나면 '동' 삽입
    if 2<=len(pre)<=5 and not re.search(r'(동|읍|면|리|가|구|시|군|역)$',pre) and not re.search(r'\d$',pre):
        newpre=pre+'동'
        out=dict(kw)
        for f in ('지역','서비스','브랜드'):
            v=str(out.get(f) or '')
            if v.startswith(pre): out[f]=newpre+v[len(pre):]
        return out
    return kw

def _auto_subkeywords(main):
    """메인 1개에서 {지역,서비스,브랜드} 조합을 생성. 서브2·3은 그 구/동에 맞춰 랜덤.
       - 지역 = 구/동 (예: 교동)
       - 서비스 = 메인의 업종(있으면) 또는 관련 업종 랜덤
       - 브랜드 = 구/동 + 관련 업종(랜덤) — 예: 교동가라오케"""
    region,service=_split_main_keyword(main)
    if not region: region=str(main or '').strip()
    # ★대표님 지시(2026-09-08): 자동생성 지역명에 '동'이 빠지는 문제 → 행정구역 접미사(동/읍/면/리/가/구/시)로
    #   끝나지 않으면 '동'을 붙인다. 이미 부여'읍'처럼 접미사가 있으면 그대로 둔다. 숫자로 끝나면(계화동24시 등) 예외.
    if region and not re.search(r'(동|읍|면|리|가|구|시|군|역)$',region) and not re.search(r'\d$',region):
        region=region+'동'
    svc=service or random.choice(RELATED_POOL)
    # 브랜드: 그 동/구에 매칭된 다른 관련 업종을 붙여 지역성 유지(중복 방지 위해 svc와 다르게)
    pool=[x for x in RELATED_POOL if x!=svc] or RELATED_POOL
    brand=region+random.choice(pool)
    return {'지역':region,'서비스':svc,'브랜드':brand}

def generate_rich_html(keywords, cfg, workroom_id=None):
    r=(keywords.get('지역') or '서울').strip()
    s=(keywords.get('서비스') or '셔츠룸').strip()
    b=(keywords.get('브랜드') or cfg.get('brand') or '인천홍마니').strip()
    # 제목: 키워드1 + 랜덤변형 번호 + 키워드2 + 키워드3  / 본문: 선택된 번호의 정상 표기
    title,rawphone=build_title(r,s,b,cfg)
    p=format_phone(rawphone)
    mood=random.choice(SERVICE_FLAVOR.get(s,DEFAULT_FLAVOR)['mood'])
    imgs=pick_images(1,workroom_id=workroom_id)   # 게시물당 이미지 1개(작업실에 이미지 없으면 [] → 이미지 없이 발행)
    # 대표님 지시(2026-09-07): 붙여준 예시처럼 '단일 강조색'을 게시물마다 하나 골라 전체에 일관 적용.
    AC=random.choice(COLORS)              # 강조색 하나(제목·소제목·라벨·별점 등 전부 이 색)
    c1=c2=c3=AC                           # 기존 c1/c2/c3 참조 호환(모두 같은 강조색)
    rel=RELATED_POOL[:]; random.shuffle(rel)
    _upd=_kst_now().strftime('%Y년 %m월')   # 우측 하단 업데이트 표기용
    H=lambda t:f'<h2 style="color:{c1};border-bottom:3px solid {c2};padding-bottom:10px;font-size:24px;margin-top:34px;">{t}</h2>'
    # 이미지 alt = 치환키워드 맨앞(지역) 그대로 (SEO). imgs가 비면 이미지 블록을 아예 넣지 않는다.
    IMG=lambda i,cap='':('' if not imgs else (f'<div style="text-align:center;margin:34px 0;"><img src="{imgs[i%len(imgs)]}" alt="{r}" style="max-width:100%;height:auto;border-radius:8px;" loading="lazy" />'+(f'<p style="color:#888;font-size:13px;margin-top:8px;">▲ {cap}</p>' if cap else '')+'</div>'))

    intro=random.choice([
        f'{r} 지역에서 {s}를 찾고 계신가요? {mood} 분위기의 <strong>{r} {s}</strong>는 회식과 모임 장소로 꾸준히 사랑받는 곳입니다. {b}에서 위치와 이용 정보를 한눈에 정리해 드립니다.',
        f'기업 회식, 지인 모임, 특별한 자리까지 — <strong>{r} {s}</strong>는 다양한 목적에 어울리는 공간입니다. {mood} 인테리어와 좋은 접근성으로 {r} 인근에서 인기가 높습니다. 자세한 안내는 {b}가 도와드립니다.',
        f'{r} {s}를 처음 방문하신다면 어디로 가야 할지 고민되기 마련입니다. {b}는 {r} 지역의 {s} 정보를 정리해 편하게 선택하실 수 있도록 안내합니다. {mood} 공간에서 좋은 시간 보내세요.',
        f'{mood} 분위기와 넓은 공간을 갖춘 <strong>{r} {s}</strong>. 접대와 단체 모임에 적합하며 {r} 중심가에서 가까워 이동이 편리합니다. 예약과 문의는 {b}가 도와드립니다.',
        f'오늘은 {r}에서 {s}를 알아보는 분들을 위해 준비했습니다. <strong>{r} {s}</strong>는 {mood} 분위기로 다양한 자리에 어울려, 어디로 갈지 고민이라면 참고하기 좋습니다. 정리는 {b}가 맡았습니다.',
        f'중요한 자리를 앞두고 {r} {s}를 알아보고 있다면 이 글이 도움이 됩니다. {mood} 공간과 편리한 접근성을 갖춘 <strong>{r} {s}</strong>의 핵심만 {b}가 골라 안내합니다.',
        f'{r} 인근에서 {s}를 고를 때 무엇을 봐야 할지 헷갈리셨죠. <strong>{r} {s}</strong>의 분위기·위치·이용법을 {b}가 알기 쉽게 풀어 드립니다. {mood} 자리에서 좋은 시간 보내세요.',
        f'모임 장소를 정하는 일은 생각보다 신경 쓰입니다. {mood} 분위기의 <strong>{r} {s}</strong>는 {r}에서 무난하게 선택할 수 있는 곳으로, {b}가 이용 포인트를 짚어 드립니다.',
        f'{r} {s} 정보를 찾다가 이 글을 보셨다면 잘 오셨습니다. 접근성 좋고 {mood} 분위기를 갖춘 <strong>{r} {s}</strong>를 중심으로, 방문 전 알아두면 좋은 내용을 {b}가 모았습니다.',
        f'분위기 좋은 {r} {s}를 원하신다면 선택지가 많아 오히려 고민되기 마련입니다. {b}는 {mood} 공간의 <strong>{r} {s}</strong>를 기준으로 위치와 이용 정보를 깔끔하게 정리했습니다.',
        f'{r}에서의 특별한 하루를 계획 중이라면 {s}부터 정해보세요. {mood} 분위기의 <strong>{r} {s}</strong>는 접대와 모임 모두에 어울리며, 자세한 안내는 {b}가 함께합니다.',
    ])
    para2=random.choice([
        f'{r} {s}의 가장 큰 장점은 접근성과 공간감입니다. 대중교통과 주차 모두 이용하기 편리해 부담 없이 방문할 수 있고, 내부는 여유로운 좌석 배치로 편안함을 더했습니다.',
        f'단체 예약 시 인원과 목적에 맞춰 공간을 안내받을 수 있어, 회식이나 모임을 준비하는 분들의 만족도가 높습니다. 미리 문의하면 원하는 시간대에 맞춰 준비가 가능합니다.',
        f'{r} 지역 특성상 {s} 선택지가 다양하지만, 위치와 분위기, 응대 수준을 함께 고려하면 후회 없는 선택을 할 수 있습니다. {b}는 이 기준으로 정보를 정리합니다.',
        f'처음 방문하는 분도 편하게 이용할 수 있도록 안내가 잘 되어 있으며 재방문율이 높은 편입니다. 궁금한 점은 방문 전 미리 문의하시면 자세히 안내받을 수 있습니다.',
        f'무엇보다 {r} {s}는 분위기와 실속의 균형이 좋습니다. 과하지 않으면서도 필요한 것은 갖춰, 처음 오는 분도 익숙한 분도 만족스럽게 이용할 수 있습니다.',
        f'좋은 자리는 위치만으로 정해지지 않습니다. {r} {s}는 응대와 관리 상태까지 신경 쓴 편이라, 중요한 모임을 맡기기에도 부담이 적습니다.',
        f'{s}를 고를 때 흔히 놓치는 것이 동선과 주변 환경입니다. {r} {s}는 이동이 편하고 주변 인프라도 잘 갖춰져, 약속 전후 시간을 알차게 쓰기 좋습니다.',
        f'예약 단계에서 원하는 조건을 미리 전하면 방문이 훨씬 매끄럽습니다. {r} {s}는 사전 문의에 따라 인원·시간대 준비가 가능해, 계획한 대로 자리를 진행할 수 있습니다.',
        f'{r}에서 {s}를 찾는 분들이 자주 확인하는 포인트는 분위기·가격·접근성 세 가지입니다. 이 글에서는 그 기준으로 필요한 정보를 차례대로 정리했습니다.',
        f'같은 {s}라도 어떤 곳을 고르느냐에 따라 만족도가 크게 달라집니다. {r} {s}의 특징을 미리 알아두면 실제 방문 때 시행착오를 줄일 수 있습니다.',
        f'{r} {s}는 혼자보다는 여럿이 함께할 때 더 빛나는 공간입니다. 좌석 구성과 분위기가 모임에 맞춰져 있어, 대화와 시간을 편하게 즐기기에 알맞습니다.',
    ])

    feat_pool=[
        '넓은 홀과 프라이빗 룸으로 인원에 맞춘 공간 선택이 가능합니다.',
        '선명한 음향과 조명으로 분위기를 한층 끌어올립니다.',
        '대중교통·주차 접근성이 좋아 이동이 편리합니다.',
        '친절하고 세심한 응대로 편안한 이용을 돕습니다.',
        '예약 문의가 간편해 원하는 시간대를 잡기 쉽습니다.',
        '합리적인 가격 구성으로 부담을 줄였습니다.',
        '청결하게 관리되는 쾌적한 실내 환경을 유지합니다.',
        '단체 모임과 회식에 적합한 좌석 배치를 갖췄습니다.',
        f'{r} 중심가에서 가까워 약속 장소로 잡기 좋습니다.',
        '초행길에도 찾기 쉬운 위치에 자리하고 있습니다.',
        '분위기 있는 인테리어로 특별한 자리를 완성합니다.',
        '다양한 목적의 모임에 유연하게 대응합니다.',
        '사전 문의로 원하는 조건을 맞출 수 있어 준비가 수월합니다.',
        '프라이빗한 공간 구성으로 편안한 대화가 가능합니다.',
        '재방문하는 단골이 많아 꾸준히 관리되는 곳입니다.',
        f'{r} 인근 주요 지점에서 이동이 편리한 위치입니다.',
        '목적과 인원에 따라 좌석을 유연하게 배치합니다.',
        '깔끔한 첫인상과 세심한 관리로 신뢰를 줍니다.',
    ]
    feats=''.join(f'<li style="margin:9px 0;line-height:1.85;padding-left:26px;position:relative;"><span style="position:absolute;left:0;color:{c2};font-weight:bold;">✔</span>{x}</li>' for x in random.sample(feat_pool,random.randint(5,8)))

    faq_pool=[
        ('예약은 어떻게 하나요?', f'{p}로 문의 주시면 인원과 시간에 맞춰 빠르게 안내해 드립니다. 방문 전 예약을 권장합니다.'),
        ('주차가 가능한가요?','인근 주차 이용이 가능하며 대중교통 접근성도 좋아 차량·도보 모두 편리합니다.'),
        ('단체 이용도 되나요?','인원에 맞춰 공간을 안내해 드리므로 회식·모임 등 단체 이용에 적합합니다. 사전 문의 시 준비가 원활합니다.'),
        (f'{r} 어디에 있나요?', f'{r} 중심가 인근에 위치해 찾기 쉽습니다. 정확한 위치는 문의 시 안내해 드립니다.'),
        ('처음 방문인데 괜찮을까요?','처음 오시는 분도 편하게 이용하실 수 있도록 안내가 잘 되어 있습니다. 부담 없이 방문하세요.'),
        ('이용 시간은 어떻게 되나요?','이용 가능 시간대는 시기에 따라 다를 수 있어 방문 전 문의로 확인하시는 것을 권장합니다.'),
        ('분위기는 어떤가요?', f'{mood} 분위기로 편안하게 시간을 보내기 좋습니다.'),
        ('예약 없이 방문해도 되나요?','가능하나 원하는 시간대 이용을 위해서는 사전 예약을 추천드립니다.'),
        ('문의는 어디로 하나요?', f'{p}로 연락 주시면 친절하게 안내해 드립니다.'),
        ('인원이 많아도 괜찮나요?','인원 규모를 미리 알려주시면 그에 맞춰 공간을 안내해 드립니다. 대규모 모임도 상담 가능합니다.'),
        (f'{s} 처음인데 뭘 준비해야 하나요?','특별한 준비물은 없습니다. 원하시는 분위기나 목적만 말씀해 주시면 맞춰 안내해 드립니다.'),
        ('대중교통으로 가기 편한가요?', f'{r} 중심가 인근이라 대중교통 접근성이 좋습니다. 도보·차량 모두 이용하기 편리합니다.'),
        ('예약 변경이나 취소도 되나요?','가능합니다. 일정이 바뀌면 미리 연락 주시면 원활하게 조정해 드립니다.'),
        ('어떤 모임에 잘 어울리나요?','회식·지인 모임·접대 등 다양한 자리에 두루 어울립니다. 목적을 말씀해 주시면 추천해 드립니다.'),
        ('주말에도 이용할 수 있나요?','이용 가능 여부는 시기에 따라 다를 수 있어, 방문 전 문의로 확인하시길 권장합니다.'),
    ]
    faqs=''.join(f'<dt style="font-weight:bold;color:{c2};margin-top:12px;">Q. {q}</dt><dd style="margin:6px 0 12px 20px;line-height:1.7;">{a}</dd>' for q,a in random.sample(faq_pool,random.randint(4,6)))
    # ★번호 매긴 H2 FAQ 섹션 + 상단 목차(TOC) — 붙여준 예시 디자인 반영(대표님 지시 2026-09-07).
    _nqa=random.sample(faq_pool,4)
    toc_items=''.join(
        f'<li style="padding:8px 0;border-bottom:1px solid #e0e0e0;">'
        f'<span style="color:{AC};font-weight:700;margin-right:10px;">{i+1:02d}</span>'
        f'<span style="color:#222;">{q}</span></li>' for i,(q,a) in enumerate(_nqa))
    toc_box=(f'<div style="background:#F7F7F5;border:1px solid {AC};border-radius:10px;padding:20px 24px;margin:0 0 36px;">'
        f'<p style="font-size:14px;font-weight:800;color:{AC};letter-spacing:2px;margin:0 0 12px;">목차</p>'
        f'<ul style="list-style:none;padding:0;margin:0;font-size:15px;">{toc_items}</ul></div>')
    numbered_faq=''.join(
        f'<h2 style="font-size:22px;font-weight:800;margin:36px 0 14px;padding-bottom:8px;'
        f'border-bottom:2px solid {AC};color:{AC};">{i+1:02d}. {q}</h2>'
        f'<p style="font-size:17px;line-height:1.9;margin:0 0 16px;color:#222;">{a}</p>' for i,(q,a) in enumerate(_nqa))

    reco_pool=['회식·접대 장소를 찾는 직장인','지인들과 편하게 모일 공간이 필요한 분',f'{r} 인근에서 약속 장소를 정하려는 분','믿을 만한 정보로 실패 없이 고르고 싶은 분','분위기 좋은 자리를 원하는 분','접근성 좋은 위치를 선호하는 분']
    recos=''.join(f'<li style="margin:9px 0;line-height:1.85;padding-left:26px;position:relative;"><span style="position:absolute;left:0;">👉</span>{x}</li>' for x in random.sample(reco_pool,random.randint(3,min(5,len(reco_pool)))))

    # 후기 카드(페르소나 + 별점) — 여러 개를 카드로
    personas=['첫 방문 고객','40대 직장인','단골 손님','회식 담당자','지인 추천 방문','30대 고객','모임 총무',
              '20대 직장인','타지역 방문객','오랜만의 모임','부서 회식 담당','친구 모임 총무']
    rev_pool=[
        f'{mood} 분위기에 응대도 친절해서 편하게 즐겼습니다. {r} 근처에서 이만한 곳 찾기 어려워요.',
        '회식 장소로 예약했는데 공간이 넓고 깔끔해 만족스러웠어요. 미리 문의하니 준비가 잘 돼 있었습니다.',
        '위치가 찾기 쉬워 초행인데도 안 헤맸어요. 분위기가 좋아 모임이 화기애애했습니다. 추천합니다.',
        f'가격도 합리적이고 응대가 세심해 좋은 자리가 됐습니다. {b} 안내대로 하니 편했어요.',
        f'{r}에서 {s} 고민하다 방문했는데 선택 잘했어요. 편하게 대화 나눌 수 있었습니다.',
        '재방문 의사 있습니다. 다음 모임도 여기로 잡으려고요. 전반적으로 만족스러웠어요.',
        f'예약 문의부터 방문까지 흐름이 매끄러웠습니다. {r} 오면 또 들를 생각이에요.',
        '기대보다 공간이 여유로워서 대화하기 편했습니다. 사람 많은 자리였는데도 붐비는 느낌이 없었어요.',
        f'{mood} 인테리어가 사진보다 실제가 더 좋았어요. 중요한 자리였는데 분위기 덕을 봤습니다.',
        '처음 방문이라 긴장했는데 안내가 친절해서 금방 편해졌습니다. 다음엔 지인들과 오려고요.',
        f'접근성이 정말 좋아서 약속 장소로 딱이었어요. {r} 중심가에서 가까운 게 큰 장점입니다.',
        '가성비를 따지는 편인데 여기는 값어치를 했습니다. 무리한 부담 없이 즐기고 왔어요.',
    ]
    _pp=random.sample(personas,3); _rr=random.sample(rev_pool,3)
    reviews=''.join(
        f'<div style="background:#20293a;color:#e8edf4;border-radius:10px;padding:15px 18px;margin:11px 0;">'
        f'<div style="font-weight:bold;margin-bottom:6px;">{pp} <span style="color:#ffc107;letter-spacing:2px;">★★★★★</span></div>'
        f'<div style="line-height:1.75;color:#cbd5e1;font-size:14px;">{rr}</div></div>' for pp,rr in zip(_pp,_rr))
    # 이용 안내/혜택 박스(초록 강조)
    benefit_pool=['투명한 안내 — 방문 전 문의로 편하게 확인','단골 고객을 위한 세심한 응대','편안하고 프라이빗한 공간','합리적이고 정직한 운영','원하는 시간대 맞춤 예약','청결하게 관리되는 쾌적한 환경','인원에 맞춘 유연한 공간 배정','초행도 찾기 쉬운 위치와 동선','사전 문의로 준비되는 매끄러운 방문','목적에 맞춘 맞춤 안내']
    benefits=''.join(f'<li style="margin:8px 0;line-height:1.8;padding-left:22px;position:relative;"><span style="position:absolute;left:0;color:{c1};">◆</span>{x}</li>' for x in random.sample(benefit_pool,random.randint(3,4)))
    # 이용 팁
    tip_pool=['원하시는 분위기나 코스를 미리 말씀해 주시면 맞춤 안내가 가능합니다.','처음이시라면 궁금한 점을 미리 정리해 문의하시면 더 자세히 안내받으실 수 있습니다.','예약은 원하시는 날짜보다 조금 미리 잡으시면 좋은 시간대를 선택하실 수 있습니다.','특별한 날이라면 미리 말씀해 주세요. 분위기에 맞게 준비해 드립니다.','단체 인원은 사전에 알려주시면 공간 배치가 원활합니다.','주말·성수기에는 예약이 빨리 마감될 수 있으니 서둘러 문의하시는 것이 좋습니다.','방문 인원이 바뀌면 미리 알려주시면 자리 조정이 수월합니다.','대중교통 이용 시 인근 지점을 기준으로 오시면 찾기 편합니다.']
    tips=''.join(f'<li style="margin:8px 0;line-height:1.8;padding-left:24px;position:relative;"><span style="position:absolute;left:0;">💡</span>{x}</li>' for x in random.sample(tip_pool,random.randint(3,4)))
    # 핵심 키워드 해시태그
    tagwords=[f'{r}{s}',r,s,b]+rel[:5]
    hashtags=' '.join(f'<span style="display:inline-block;padding:6px 13px;margin:4px 4px;background:#eef2f7;color:{c1};border:1px solid {c2};border-radius:20px;font-size:13px;font-weight:600;">#{re.sub(r"[^가-힣A-Za-z0-9]","",str(x))}</span>' for x in tagwords if str(x).strip())
    # 리치 소제목(아이콘 + 좌측 강조바)
    def SEC(icon,tt): return f'<h2 style="color:{c1};font-size:22px;margin:36px 0 14px;padding:11px 15px;border-left:6px solid {c2};background:#f5f7fb;border-radius:0 6px 6px 0;">{icon} {tt}</h2>'
    price_box=(f'<div style="border:1px solid {c2};background:#f4faf6;border-radius:10px;padding:18px 20px;margin:24px 0;">'
        f'<p style="font-weight:bold;color:{c1};font-size:16px;margin:0 0 10px;">✅ {r} {s} 이용 안내</p>'
        f'<ul style="margin:0;padding:0;list-style:none;font-size:14px;color:#333;">{benefits}</ul></div>')
    # 서비스 비교 테이블 (일반 vs 프리미엄) — 어두운 헤더
    _crows=[('시설 수준','보통','최고급'),('스태프 서비스','기본','맞춤 VIP'),('프리미엄석','보통','완벽 보장'),
            ('이벤트 혜택','없음','상시 제공'),('예약 편의','대기 가능','우선 예약'),('분위기','일반','프라이빗 고급')]
    random.shuffle(_crows); _crows=_crows[:random.randint(4,5)]
    compare_table=(f'<table style="width:100%;border-collapse:collapse;margin:14px 0;font-size:14px;">'
        f'<thead><tr style="background:{c1};color:#fff;"><th style="padding:11px;text-align:left;">항목</th>'
        f'<th style="padding:11px;">일반</th><th style="padding:11px;color:#ffe082;">프리미엄</th></tr></thead><tbody>'
        +''.join(f'<tr style="border-bottom:1px solid #e6ebf2;"><td style="padding:10px 11px;color:#555;">{_a}</td>'
                 f'<td style="padding:10px 11px;text-align:center;color:#999;">{_bc}</td>'
                 f'<td style="padding:10px 11px;text-align:center;font-weight:bold;color:{c2};">{_cc}</td></tr>' for _a,_bc,_cc in _crows)
        +'</tbody></table>')
    # 이용 흐름 STEP (컬러 바) — 첫 단계(예약)는 고정, 이후는 풀에서 랜덤 선택해 매번 다르게.
    _step_first=('전화 예약',f'원하는 날짜와 시간을 {p}로 말씀해 주세요. 당일 예약도 환영합니다.')
    _step_pool=[('방문 및 안내','방문 시 스태프가 친절하게 안내해 드립니다.'),
            ('맞춤 서비스','선호에 맞춰 최상의 경험을 제공합니다.'),
            ('공간 배정','인원과 목적에 맞는 공간으로 안내해 드립니다.'),
            ('편안한 이용','여유로운 분위기에서 시간을 즐기시면 됩니다.'),
            ('세심한 응대','필요한 것이 있으면 언제든 편하게 요청하세요.'),
            ('만족스러운 마무리','다음 방문 시 좋은 혜택과 멤버십 안내를 받으실 수 있습니다.')]
    _steps=[_step_first]+random.sample(_step_pool,3)
    steps_html=''.join(f'<div style="background:{c2};color:#fff;text-align:center;padding:8px;font-weight:bold;border-radius:5px;margin:14px 0 6px;letter-spacing:1px;">STEP {_i+1}</div>'
        f'<p style="font-size:14px;margin:0 0 4px;line-height:1.7;"><b style="color:{c1};">{_t}</b> — {_ds}</p>' for _i,(_t,_ds) in enumerate(_steps))
    # 업종별 소개 섹션 (참고 디자인 반영 — 지역에서 만나는 다양한 업종)
    _cats=[('프리미엄 룸','고급스러운 프라이빗 공간에서 최상의 서비스를 경험할 수 있는 프리미엄 업소'),
           ('노래 엔터테인먼트','최신 음향 시스템과 신나는 분위기에서 노래를 즐길 수 있는 엔터테인먼트 업소'),
           ('퍼블릭 계열','오픈된 분위기에서 다양한 만남과 즐거움을 누릴 수 있는 퍼블릭 스타일 업소'),
           ('바 & 라운지','감성적인 바 문화와 개성 넘치는 분위기를 즐길 수 있는 바 및 라운지 업소'),
           ('보도 & 글로벌','전문 보도 서비스와 글로벌 감성이 어우러진 다채로운 엔터테인먼트 업소'),
           ('프라이빗 모임','조용하고 프라이빗하게 소규모 모임을 즐기기 좋은 공간')]
    random.shuffle(_cats); _cats=_cats[:random.randint(3,5)]
    cats_html=''.join(f'<h3 style="color:{c1};font-size:16px;margin:18px 0 4px;">{_t}</h3>'
        f'<p style="font-size:14px;color:#555;line-height:1.7;margin:0 0 3px;">{_ds}</p>'
        f'<p style="font-size:13px;color:{c2};margin:0 0 6px;">{r} {random.choice([s,b])}</p>' for _t,_ds in _cats)
    # 중간 콘텐츠 블록 — 순서를 매번 섞어 변형 폭 확대(중복 방지)
    blocks=[
        SEC('🏢',f'{r}에서 만나는 다양한 업종')+cats_html,
        SEC('⭐',f'{r} {s} 주요 특징')+f'<ul style="font-size:15px;padding:0;list-style:none;color:#444;">{feats}</ul>',
        SEC('🙋',f'{r} {s} 이런 분께 추천합니다')+f'<ul style="font-size:15px;padding:0;list-style:none;color:#444;">{recos}</ul>',
        SEC('💬',f'{r} {s} 자주 묻는 질문')+f'<dl style="font-size:15px;margin:14px 0;">{faqs}</dl>',
        SEC('🌟',f'{r} {s} 이용 후기')+reviews,
        SEC('💡',f'{r} {s} 이용 팁')+f'<ul style="font-size:15px;padding:0;list-style:none;color:#444;">{tips}</ul>',
    ]
    random.shuffle(blocks)
    # ── 상단: 카테고리 라벨 + 큰 H1 + 언더라인 바 (붙여준 예시 디자인) ──
    _label=random.choice(['총정리','이용 안내','완벽 가이드','한눈에 정리','상세 안내'])
    head=(f'<div style="font-size:12px;font-weight:700;letter-spacing:5px;color:{AC};margin-bottom:12px;">'
          f'{_label} · {r} {s}</div>'
        + f'<h1 style="font-size:32px;font-weight:800;line-height:1.3;margin:0 0 16px;color:#111;">{title}</h1>'
        + f'<div style="width:80px;height:4px;background:{AC};margin:0 0 28px;border-radius:2px;"></div>')
    html=(head
        + IMG(0, f'{r} {s}의 {mood.split()[0]} 공간')
        + toc_box                                    # ★상단 목차 박스
        + SEC('📍',f'{r} {s} 안내')
        + f'<p style="font-size:16px;margin:16px 0;line-height:1.95;">{intro}</p>'
        + f'<p style="font-size:15px;margin:14px 0;line-height:1.95;color:#333;">{para2}</p>'
        + price_box
        + numbered_faq                               # ★번호 매긴 H2 FAQ(01. 02. 03. 04.)
        + SEC('📊',f'{r} {s} 서비스 비교') + compare_table
        + ''.join(blocks)
        + SEC('🧭',f'{r} {s} 이용 흐름') + steps_html
        + SEC('#️⃣','핵심 키워드')
        + f'<div style="margin:12px 0 6px;line-height:2.4;">{hashtags}</div>'
        + f'<div style="margin:38px 0 8px;padding:24px;background:#1f2733;color:#e8edf4;border-radius:12px;text-align:center;">'
          f'<p style="font-size:17px;font-weight:bold;color:#fff;margin:0 0 14px;">🎁 {r} {s} 예약 혜택</p>'
          f'<div style="display:inline-grid;gap:6px;text-align:center;font-size:14px;color:#cbd5e1;margin-bottom:14px;">'
          f'<div>일반 예약</div><div style="color:{c2};font-size:18px;">⚡</div><div>당일 예약</div>'
          f'<div style="color:{c2};font-size:18px;">⚡</div><div style="font-weight:bold;color:#ffe082;">VIP 서비스</div></div>'
          f'<p style="font-size:24px;font-weight:bold;color:#fff;margin:0;background:#0f1621;padding:12px;border-radius:8px;"><a href="{tel_href(rawphone)}" style="color:#fff;text-decoration:none;display:block;">📞 {p}</a></p>'
          f'<p style="font-size:13px;color:#cbd5e1;margin:8px 0 0;">📱 터치하면 바로 연결</p></div>'
        + f'<p style="font-size:12px;color:#999;text-align:right;margin:16px 0 8px;">최신 업데이트 · {_upd} 기준</p>')
    return html, title

# ==================== GPT 본문 생성 (선택) ====================
def generate_post_gpt(keywords, cfg, workroom_id=None):
    """OpenAI로 키워드1 중심의 장문 HTML 본문 생성. 실패 시 템플릿으로 폴백."""
    import requests as _rq
    r=(keywords.get('지역') or '서울').strip(); s=(keywords.get('서비스') or '셔츠룸').strip()
    b=(keywords.get('브랜드') or cfg.get('brand') or '인천홍마니').strip()
    _rawph=pick_phone(cfg); p=format_phone(_rawph)
    # ★제공자 분기(대표님 2026-09-11): nvidia면 build.nvidia.com 무료 엔드포인트(OpenAI 호환 형식). 비용 $0.
    _prov=(cfg.get('llm_provider') or 'openrouter').strip().lower()
    if _prov=='nvidia':
        key=(cfg.get('nvidia_api_key') or '').strip(); model=(cfg.get('nvidia_model') or 'nvidia/nemotron-3-ultra-550b-a55b').strip()
        if not key: raise RuntimeError('nvidia_api_key 없음 — 설정 탭에 NVIDIA 키(nvapi-…) 입력')
    elif _prov=='openrouter':
        key=(cfg.get('openrouter_api_key') or '').strip(); model=(cfg.get('openrouter_model') or 'deepseek/deepseek-v4-flash-0731').strip()
        if not key: raise RuntimeError('openrouter_api_key 없음 — 설정 탭에 OpenRouter 키(sk-or-…) 입력')
    else:
        key=cfg.get('openai_key',''); model=cfg.get('model') or 'gpt-4o-mini'
        if not key: raise RuntimeError('openai_key 없음')
    imgs=pick_images(1,workroom_id=workroom_id)           # 이미지 1개(작업실에 없으면 [] → 이미지 없이)
    c1=c2=random.choice(COLORS)                           # 강조색 하나로 통일(디자인 틀과 동일 색)
    sys_p=("너는 한국어 정보형 랜딩페이지와 지역 안내 글을 작성하는 전문 카피라이터다. "
           "세 개의 키워드 중 키워드1을 문서 전체의 명확한 메인 주제로 삼고, 키워드2와 키워드3은 "
           "메인 주제를 설명하는 보조 문맥으로만 사용한다. 읽기 쉬운 장문 콘텐츠를 쓰되 키워드 도배, "
           "과장·허위·불법·선정적 표현과 검증되지 않은 수치·후기는 만들지 않는다.")
    # ★중복성 개선(대표님 지시 2026-09-11 '발행글 너무 중복'): 매 글마다 '작성 앵글'을 랜덤 지정해
    #  구성·소제목·문체가 글마다 실제로 달라지게 한다(고정 8단 골격 탈피). 도입 문장 시작도 매번 다르게.
    _angle=random.choice([
        "이 글은 '자주 묻는 질문(Q&A) 중심'으로 구성한다. 방문 전 궁금증을 질문-답변 형식으로 풀어가되 소제목도 질문형으로 뽑는다.",
        "이 글은 '처음 방문자를 위한 입문 가이드' 톤으로 쓴다. 준비물·동선·주의점을 단계별로 친절하게 안내한다.",
        "이 글은 '체크리스트·비교 정리' 중심으로 쓴다. 좋은 곳을 고르는 기준을 항목별로 짚어주고 표·목록을 적극 활용한다.",
        "이 글은 '지역 생활정보' 관점으로 쓴다. 해당 지역의 위치·접근성·주변 환경을 먼저 소개하고 자연스럽게 본 주제로 연결한다.",
        "이 글은 '상황별 추천'(회식·모임·접대·기념일 등) 구성으로 쓴다. 목적에 따라 어떤 선택이 어울리는지 시나리오로 안내한다.",
        "이 글은 '핵심 요약 먼저(결론 우선)' 구성으로 쓴다. 맨 앞에 요점을 정리하고 이어서 근거·세부를 풀어낸다.",
    ])
    usr_p=(f"키워드1(메인)='{r}', 키워드2(보조)='{s}', 키워드3(보조)='{b}', 문의전화='{p}'.\n"
           f"첨부 예시처럼 길고 구조적인 정보형 게시글을 작성하라. 반드시 지킬 조건:\n"
           f"1. 문서의 검색 의도와 핵심 주제는 오직 키워드1 '{r}'이다. 키워드2·3은 '{r}'을 설명할 때만 자연스럽게 보조한다.\n"
           f"2. 첫 문단, 주요 소제목 3개 이상, 핵심 정리와 마지막 문단에 '{r}'을 자연스럽게 포함한다. "
           f"동일 문장이나 부자연스러운 반복은 금지한다.\n"
           f"3. 키워드2 '{s}'와 키워드3 '{b}'는 각각 2~4회 정도만 사용하고 메인키워드보다 눈에 띄지 않게 한다.\n"
           f"4. 순수 HTML 조각만 출력한다. 코드블록·마크다운·설명·<html><body> 태그는 금지한다.\n"
           f"5. 1,800~2,800자 분량으로 작성한다. ★작성 앵글: {_angle} "
           f"이 앵글에 맞춰 소제목·구성·문단 순서를 자유롭게 짜라(매 글마다 골격이 달라야 한다). "
           f"단 도입·본문 안내·세부 항목·FAQ·핵심 정리는 형태를 바꿔서라도 포함한다.\n"
           f"6. <h2> 5~7개, 세부 항목은 <h3>과 <p>, 특징은 <ul><li>, FAQ는 <dl><dt><dd>를 사용한다. "
           f"정보 카드 4개는 테두리와 여백을 준 <div>로 만든다. 모바일에서도 읽기 쉬운 인라인 스타일을 사용한다.\n"
           f"7. 색상은 강조색 '{c1}' 하나만 사용한다. 모든 <h2>는 "
           f"style=\"font-size:22px;font-weight:800;margin:36px 0 14px;padding-bottom:8px;border-bottom:2px solid {c1};color:{c1};\" "
           f"로, <h3>는 color:{c1} 로 지정한다. 다른 색은 쓰지 않는다(본문 글자색 제외).\n"
           f"8. 전화번호나 문의 CTA 박스는 본문에 넣지 않는다(문서 맨 끝에 시스템이 따로 붙인다). "
           f"전화번호 {p}도 본문에 쓰지 않는다.\n"
           f"9. 실제로 주어지지 않은 주소·가격·운영시간·후기·보장 표현은 단정하지 않는다. 매번 문장과 항목 순서를 다르게 한다.")
    _url="https://api.openai.com/v1/chat/completions"; _to=60
    _hdr={"Authorization":f"Bearer {key}","Content-Type":"application/json","Accept":"application/json"}
    _body={"model":model,"temperature":0.85,"max_tokens":3800,
           "messages":[{"role":"system","content":sys_p},{"role":"user","content":usr_p}]}
    if _prov=='nvidia':
        _url="https://integrate.api.nvidia.com/v1/chat/completions"; _to=200; _body['max_tokens']=4500   # Ultra 실측 73~136초·output 3800 상한 도달 → 여유
        _ml=model.lower()   # 사고(thinking) 끔 — 우리는 HTML 본문만 필요, 응답 속도·토큰 절약
        if 'deepseek' in _ml: _body['chat_template_kwargs']={'thinking':False}
        elif 'nemotron' in _ml: _body['chat_template_kwargs']={'enable_thinking':False}
        elif 'kimi' in _ml: _body['reasoning_effort']='low'
    elif _prov=='openrouter':
        _url="https://openrouter.ai/api/v1/chat/completions"; _to=120; _body['max_tokens']=4500
        _hdr['HTTP-Referer']='https://google.twseo.kr'; _hdr['X-Title']='chirashi'   # 선택 헤더(순위표용)
        _body['reasoning']={'enabled':False}          # 사고 토큰 끔(HTML 본문만 필요)
        _body['usage']={'include':True}               # 응답 usage에 실제 비용(cost) 포함 → 원장에 실비용 기록
    resp=_rq.post(_url,headers=_hdr,json=_body,timeout=_to)
    if _prov in ('nvidia','openrouter') and resp.status_code==429:
        time.sleep(3); resp=_rq.post(_url,headers=_hdr,json=_body,timeout=_to)   # 분당 한도 — 3초 뒤 1회 재시도
        if resp.status_code==429: raise RuntimeError(f'{_prov.upper()} 분당 한도(429) — 잠시 후 재개')
    if _prov=='openrouter' and resp.status_code==402:   # OpenRouter는 크레딧 바닥을 402로 줌(OpenAI의 429/insufficient_quota와 다름)
        raise RuntimeError('OPENROUTER 잔액 소진(402) — openrouter.ai/settings/credits 충전 필요')
    # ★잔액 소진 구분(대표님 2026-09-11 '이거 맞아?' — 잔액 -$1.44인데 로그는 '레이트리밋'): OpenAI는 크레딧 바닥도
    #   429로 주지만 body error.type이 insufficient_quota. 이를 구분 못해 5분마다 헛요청+오해 로그가 찍혔음.
    if resp.status_code==429:
        try: _et=str(((resp.json() or {}).get('error') or {}).get('type') or '')
        except Exception: _et=''
        if 'insufficient_quota' in _et or 'billing' in _et:
            raise RuntimeError('OpenAI 잔액 소진(insufficient_quota) — platform.openai.com 크레딧 충전 필요')
    resp.raise_for_status()
    payload=resp.json()
    _usage=dict(payload.get('usage') or {})
    if _prov=='nvidia': _usage['cost']=0.0   # 무료 → 원장 실비용 0 (가격 0을 넘기면 `or 0.15` 기본값에 먹혀 $0.0024로 찍히던 버그 수정)
    _record_openai_usage(model,_usage,cfg)
    body=payload['choices'][0]['message']['content'].strip()
    if body.startswith('```'): body=re.sub(r'^```[a-zA-Z]*\n?|```$','',body).strip()
    title,_=build_title(r,s,b,cfg,_rawph)
    # 대표님 지시(2026-09-07): GPT 본문을 '예쁜 디자인 틀'로 감싼다.
    #  단일 강조색 + 카테고리 라벨 + 큰 H1/언더라인 바 + (본문 h2로 만든) 목차 + 다크 CTA + 업데이트 날짜.
    AC=c1                                  # 강조색 하나로 통일
    _upd=_kst_now().strftime('%Y년 %m월')
    # 본문의 <h2> 제목들을 뽑아 상단 목차(TOC) 구성 — 없으면 목차 생략
    _h2s=[re.sub(r'<[^>]+>','',h).strip() for h in re.findall(r'<h2[^>]*>(.*?)</h2>',body,re.S|re.I)]
    _h2s=[h for h in _h2s if h][:5]
    toc_box=''
    if len(_h2s)>=2:
        _items=''.join(
            f'<li style="padding:8px 0;border-bottom:1px solid #e0e0e0;">'
            f'<span style="color:{AC};font-weight:700;margin-right:10px;">{i+1:02d}</span>'
            f'<span style="color:#222;">{h}</span></li>' for i,h in enumerate(_h2s))
        toc_box=(f'<div style="background:#F7F7F5;border:1px solid {AC};border-radius:10px;padding:20px 24px;margin:0 0 32px;">'
            f'<p style="font-size:14px;font-weight:800;color:{AC};letter-spacing:2px;margin:0 0 12px;">목차</p>'
            f'<ul style="list-style:none;padding:0;margin:0;font-size:15px;">{_items}</ul></div>')
    _label=random.choice(['총정리','이용 안내','완벽 가이드','한눈에 정리','상세 안내'])
    _img_block=(f'<div style="text-align:center;margin:0 0 28px;"><img src="{imgs[0]}" alt="{r}" style="max-width:100%;border-radius:8px;" loading="lazy"/></div>' if imgs else '')
    header=(f'<div style="font-size:12px;font-weight:700;letter-spacing:5px;color:{AC};margin-bottom:12px;">{_label} · {r} {s}</div>'
            f'<h1 style="font-size:32px;font-weight:800;line-height:1.3;margin:0 0 16px;color:#111;">{title}</h1>'
            f'<div style="width:80px;height:4px;background:{AC};margin:0 0 28px;border-radius:2px;"></div>'
            f'{_img_block}'
            f'{toc_box}')
    cta=(f'<div style="margin:38px 0 8px;padding:24px;background:#1f2733;color:#e8edf4;border-radius:12px;text-align:center;">'
         f'<p style="font-size:17px;font-weight:bold;color:#fff;margin:0 0 12px;">📞 {r} {s} 문의·예약</p>'
         f'<p style="font-size:24px;font-weight:bold;color:#fff;margin:0;background:#0f1621;padding:12px;border-radius:8px;"><a href="{tel_href(_rawph)}" style="color:#fff;text-decoration:none;display:block;">{p}</a></p>'
         f'<p style="font-size:13px;color:#cbd5e1;margin:8px 0 0;">📱 터치하면 바로 연결</p>'
         f'<p style="font-size:14px;color:#cbd5e1;margin:12px 0 0;">{b}</p></div>'
         f'<p style="font-size:12px;color:#999;text-align:right;margin:16px 0 8px;">최신 업데이트 · {_upd} 기준</p>')
    return header+body+cta, title

_GPT_SKIP_UNTIL=[0.0]   # time.time()까지 GPT 스킵(429 서킷브레이커)
_NOKEY_LOGGED=[0.0]     # '키 없음→템플릿' 안내 마지막 시각(10분 1회)

def _gen_once(keywords, cfg, workroom_id=None):
    # GPT 429(레이트리밋) 서킷브레이커: 429가 나면 5분간 GPT를 건너뛰고 템플릿 직행.
    #  (매 발행마다 GPT 호출→429 대기→폴백 반복이 발행을 느리게 해 타임아웃 유발 — 대표님 지적)
    # 제공자별 키 존재 여부로 게이트(nvidia면 nvidia_api_key). 예전엔 openai_key만 봐서 NVIDIA 전용 설정이 조용히 템플릿으로 빠졌음.
    _pv=(cfg.get('llm_provider') or 'openrouter').strip().lower()
    _llm_key=(cfg.get('nvidia_api_key') if _pv=='nvidia' else (cfg.get('openrouter_api_key') if _pv=='openrouter' else cfg.get('openai_key')))
    if cfg.get('use_gpt') and _llm_key and time.time() >= _GPT_SKIP_UNTIL[0]:
        try:
            return generate_post_gpt(keywords,cfg,workroom_id=workroom_id)
        except Exception as e:
            _msg=str(e)
            # 여러 워커가 같은 순간 429를 받아 같은 줄을 6번 찍던 것 방지: 이미 스킵 중이면 로그 생략.
            _first=(time.time()>=_GPT_SKIP_UNTIL[0])
            if '잔액 소진' in _msg or 'insufficient_quota' in _msg:
                _GPT_SKIP_UNTIL[0]=time.time()+1800   # 30분 스킵 — 충전 전엔 재시도 무의미(헛요청·오해 로그 방지)
                _where='openrouter.ai/settings/credits' if _pv=='openrouter' else 'platform.openai.com'
                if _first: add_log(f'[{_pv.upper()} 잔액소진→30분간 템플릿] 크레딧 부족 — {_where}에서 충전해야 AI 글 재개(그동안 템플릿). 설정 탭에서 엔진/키를 바꿔 저장하면 즉시 재시도')
            elif '분당 한도(429)' in _msg:
                _GPT_SKIP_UNTIL[0]=time.time()+60    # 분당 한도는 금방 풀림 → 60초만 템플릿
                if _first: add_log(f'[{_pv.upper()} 429→60초 템플릿] 분당 요청 한도 — 잠시 후 자동 재개')
            elif '429' in _msg or 'Too Many' in _msg or 'rate limit' in _msg.lower():
                _GPT_SKIP_UNTIL[0]=time.time()+300   # 5분간 GPT 스킵
                if _first: add_log('[GPT 429→5분간 템플릿 사용] OpenAI 레이트리밋 — 발행 속도 유지 위해 잠시 GPT 끔')
            else:
                add_log(f'[GPT 실패→템플릿] {_msg[:80]}')
    elif cfg.get('use_gpt') and not _llm_key and time.time()-_NOKEY_LOGGED[0]>600:
        # 체크는 켜졌는데 선택 엔진의 키가 비면 조용히 템플릿으로 빠져 원인을 알 수 없었음 → 10분에 1번 알림
        _NOKEY_LOGGED[0]=time.time()
        add_log(f'[AI 생성 건너뜀→템플릿] {_pv.upper()} API 키가 비어 있음 — 설정 탭에서 {_pv.upper()} 키를 입력·저장해야 AI 글 생성')
    return generate_rich_html(keywords,cfg,workroom_id=workroom_id)

def generate_article(keywords, cfg, unique=True, workroom_id=None):
    """본문 생성. unique=True 면 제목/본문이 과거와 겹치지 않을 때까지 재생성(상시 다르게).
       workroom_id를 주면 그 작업실 전용 이미지 풀을 사용(없으면 이미지 없이 발행)."""
    if not unique:
        return _gen_once(keywords,cfg,workroom_id=workroom_id)
    html=title=None
    for _ in range(8):
        html,title=_gen_once(keywords,cfg,workroom_id=workroom_id)
        if remember_if_unique(title,html): return html,title
    # 8회 모두 충돌(사실상 불가) → 강제 유니크 토큰 부착
    html=force_unique_html(html); remember_if_unique(title,html,force=True)
    return html,title

# ==================== 텔레그램 알림 (선택) ====================
def send_telegram(cfg, msg):
    tok=cfg.get('telegram_token',''); chat=cfg.get('telegram_chat_id','')
    if not tok or not chat: return False
    try:
        import requests as _rq
        _rq.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                 json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},timeout=10)
        return True
    except Exception: return False

def send_telegram_doc(cfg, filename, data_bytes, caption=''):
    """텔레그램으로 파일(백업 zip/엑셀) 전송."""
    tok=cfg.get('telegram_token',''); chat=cfg.get('telegram_chat_id','')
    if not tok or not chat: return False,'텔레그램 토큰/챗ID 미설정'
    try:
        import requests as _rq
        r=_rq.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                   data={'chat_id':chat,'caption':caption[:1000]},
                   files={'document':(filename,data_bytes)},timeout=40)
        j=r.json()
        return bool(j.get('ok')),('' if j.get('ok') else str(j)[:150])
    except Exception as e: return False,str(e)[:150]

# ==================== 캡차 / 보안 인증 감지 (우회 아님 — 감지해서 제외) ====================
def detect_captcha(d):
    """현재 페이지에 캡차/보안 인증이 '실제로' 있는지 감지. 있으면 종류, 없으면 ''.
       ★대표님 rental-zon 실측: 로그인 회원 글쓰기엔 캡차가 없는데도 페이지 JS의
         'captcha' 문자열만 보고 kcaptcha로 오탐 → 있지도 않은 캡차 처리하다 발행 중단.
         그래서 '보이는 캡차 요소(입력칸/이미지)가 실제로 있을 때만' 캡차로 판정한다."""
    from selenium.webdriver.common.by import By
    try: html=d.page_source or ''
    except Exception: html=''
    low=html.lower()
    # Turnstile/hCaptcha/reCAPTCHA는 위젯 마크업이 명확할 때만
    if 'cf-turnstile' in low or ('turnstile' in low and 'sitekey' in low): return 'turnstile'
    if 'h-captcha' in low or 'hcaptcha' in low: return 'hcaptcha'
    if 'g-recaptcha' in low or 'grecaptcha' in low or 'data-sitekey' in low: return 'recaptcha'
    # kcaptcha(이미지 캡차): '실제 캡차 입력칸 또는 캡차 이미지 요소'가 DOM에 보일 때만 인정.
    #  문자열(kcaptcha/captcha/자동등록방지)만으로는 판정하지 않는다(오탐 방지).
    def _has_visible(css):
        try:
            for el in d.find_elements(By.CSS_SELECTOR,css):
                try:
                    if el.is_displayed(): return True
                except Exception: pass
        except Exception: pass
        return False
    has_cap_input=_has_visible("#captcha_key,input[name='captcha_key'],input[name*='captcha'],"
                               "input[id*='captcha'],input[name='wr_key'],input[name*='secText'],"
                               "input[name*='보안'],#secret_text")
    has_cap_img=_has_visible("img#captcha_img,img#captcha_image,img[src*='captcha'],img[src*='kcaptcha'],"
                             "img[src*='/captcha'],img[alt*='captcha'],img[alt*='보안']")
    if has_cap_input or has_cap_img:
        return 'kcaptcha'
    return ''   # 보이는 캡차 요소 없음 → 캡차 없음(로그인 회원 글쓰기 등)

# CAPTCHA는 자동 해석하거나 우회하지 않는다. 폼을 모두 채운 뒤 사람이
# 입력한 값만 같은 Selenium 세션에 전달하여 등록을 계속한다.
CAPTCHA_LOCK=threading.Lock()
CAPTCHA_TASKS={}

def _captcha_public(task):
    return {k:v for k,v in task.items() if k not in ('event','value','cancelled')}

def _kcaptcha_force_load(d):
    """그누보드 kcaptcha 특화: 초기 #captcha_img는 dot.gif 플레이스홀더라서
       JS가 새로고침 전엔 실이미지가 없다. g5_captcha_url을 읽어 세션을 새로 만들고
       실제 kcaptcha_image.php 를 src에 강제 로드한 뒤 디코딩까지 기다린다.
       실이미지가 뜨면 True, 아니면 False(다른 플랫폼이면 조용히 False).
       세션 정합성: kcaptcha_session.php가 세션에 정답을 심고 kcaptcha_image.php는
       그 정답을 그릴 뿐이므로, 심기 실패 시 stale 이미지를 풀지 않도록 abort한다.
       ★Cafe24 등 그누보드 아님(g5_captcha_url 없음)이면 아무것도 안 하고 즉시 False."""
    import time
    try:
        # Cafe24 등 비그누보드 페이지에서 이 그누보드 전용 로직이 캡차를 건드리지 않게 조기 차단
        if not d.execute_script("return typeof g5_captcha_url!=='undefined';"):
            return False
        # 이미 진짜 이미지가 떠 있으면(dot.gif 아니고 naturalWidth>0) 세션 재생성 불필요
        already=d.execute_script("""
            var i=document.getElementById('captcha_img');
            if(!i) return false;
            var s=i.getAttribute('src')||'';
            return (s.indexOf('dot.gif')===-1 && (i.naturalWidth||0)>5);
        """)
        if already:
            return True
        cap_url=d.execute_script("return (typeof g5_captcha_url!=='undefined')?g5_captcha_url:'';") or ''
        if not cap_url:
            return False
        # 동기 XHR이 서버 지연 시 JS 스레드를 오래 막을 수 있어 스크립트 타임아웃 상한을 건다
        try: d.set_script_timeout(8)
        except Exception: pass
        # 세션에 캡차 정답 심기 → 성공(2xx)해야만 그 정답의 이미지 로드. 실패 시 False 반환.
        ok=d.execute_script("""
            var base=arguments[0], done=false;
            try{
              var xhr=new XMLHttpRequest();
              xhr.open('POST', base+'/kcaptcha_session.php', false); xhr.send();
              done=(xhr.status>=200 && xhr.status<300);
            }catch(e){ done=false; }
            if(done){
              var img=document.getElementById('captcha_img');
              if(img){ img.src = base+'/kcaptcha_image.php?t='+(new Date()).getTime(); }
            }
            return done;
        """, cap_url)
        if not ok:
            return False   # 세션 재생성 실패 → stale 이미지를 풀지 않는다
        # 이미지 디코딩 완료까지 대기(naturalWidth>0)
        for _ in range(20):
            try:
                natw=d.execute_script("var i=document.getElementById('captcha_img');return i?(i.naturalWidth||0):0;") or 0
                if natw>5: return True
            except Exception: pass
            time.sleep(0.3)
        return False
    except Exception:
        return False

def _captcha_image_data(d):
    """그누보드(kcaptcha)/XE·Rhymix/Cafe24 캡차 이미지를 base64로 반환. 못 찾으면 ''."""
    from selenium.webdriver.common.by import By
    import time

    # 0) 그누보드 kcaptcha면 실이미지를 먼저 강제 로드(dot.gif 플레이스홀더 문제 해결)
    _kcaptcha_force_load(d)
    # 0.5) ★캡차 이미지가 실제로 디코딩(naturalWidth>5)될 때까지 '대기만' 한다 — Cafe24 등 비동기 로드 대응.
    #      스샷이 naturalWidth=0(로드 전)에 실패하던 문제 방지(대표님 rental-zon). src 재로드는 안 함
    #      (재로드하면 새 캡차 생성돼 정답과 어긋남). scrollIntoView로 렌더 유도만. 최대 8초.
    try:
        for _ in range(16):
            loaded=d.execute_script("""
                var s=['img#captcha_img','img#captcha_image','img[id*="captcha"]','img[src*="captcha"]'];
                for(var i=0;i<s.length;i++){var im=document.querySelector(s[i]);
                  if(im){ try{im.scrollIntoView({block:'center'});}catch(e){}
                          if((im.naturalWidth||0)>5) return true; } }
                return false;
            """)
            if loaded: break
            time.sleep(0.5)
    except Exception: pass

    # 넓은 폴백(크기추정/data-URI)은 캡차 INPUT이 실제 있을 때만 → 로고·아이콘 오탐 방지
    has_captcha_input=False
    try:
        has_captcha_input=bool(d.find_elements(By.CSS_SELECTOR,
            "input[name*='captcha'],input[id*='captcha'],#captcha_key,input[name='wr_key'],#secret_text"))
    except Exception:
        pass

    def _is_recaptcha(el):
        # reCAPTCHA/hCaptcha 요소는 이미지캡차 솔버(normal)로 보내면 안 됨 → 제외
        try:
            src=(el.get_attribute('src') or '').lower()
            if any(k in src for k in ('recaptcha','hcaptcha','gstatic.com/recaptcha','google.com/recaptcha')):
                return True
        except Exception: pass
        return False

    def _shot(el):
        # 어떤 엘리먼트든(img/canvas/svg/div/bg-image) 렌더된 픽셀을 그대로 캡처
        try:
            if not el or _is_recaptcha(el):
                return ''
            try:
                sz = el.size
                w, h = sz.get('width', 0), sz.get('height', 0)
            except Exception:
                w = h = 0
            # naturalWidth로 실제 디코딩 여부 확인(레이아웃상 크기 0이어도 이미지 픽셀 존재 가능)
            try:
                natw = d.execute_script("return arguments[0].naturalWidth||0;", el) or 0
            except Exception:
                natw = 0
            if w <= 0 and h <= 0 and natw <= 5:
                return ''
            try:
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.25)  # 스샷 전 픽셀 페인트 대기(kcaptcha src 교체 직후 방지)
            except Exception:
                pass
            b64 = el.screenshot_as_base64
            return 'data:image/png;base64,' + b64 if b64 else ''
        except Exception:
            return ''

    # 1) 명시적 CSS 셀렉터: 가장 구체적 → 가장 일반적 (gnuboard/XE/Rhymix/Cafe24/일반)
    SELECTORS = [
        # kcaptcha src가 교체된 진짜 이미지 (dot.gif 플레이스홀더 배제)
        "img#captcha_img[src*='kcaptcha_image.php']",
        "img#captcha_img[src*='captcha.php']",
        "img#captcha_img:not([src*='dot.gif'])",
        "img#captcha_img",                          # gnuboard5 kcaptcha 정식 id
        "img#captcha_image",                        # XE/Rhymix 인라인
        "#captcha img#captcha_img", "fieldset#captcha img", "#captcha img",
        ".captcha img", "fieldset.captcha img",
        # src 특징 기반
        "img[src*='kcaptcha_image.php']", "img[src*='captcha_action=captchaImage']",
        "img[src*='captchaImage']", "img[src*='captcha.php']", "img[src*='kcaptcha']",
        "img[src*='/kcaptcha/']", "img[src*='seccode']", "img[src*='securimage']",
        "img[src*='authimg']", "img[src*='boan']", "img[src*='=captcha']",
        "img[src*='image.php']", "img[src*='vcode']", "img[src*='chkcaptcha']",
        # Cafe24 표준 캡차 (rental-zon 등): /exec/.../captcha, ec-base-captcha, #captchaImg
        "img[src*='/captcha']", "img[src*='Captcha']", "img[src*='security_number']",
        ".ec-base-captcha img", "#captchaImg", "img#captchaImg", ".captchaImg img",
        "img[src*='exec/front'][src*='aptcha']", "span.captcha img", ".security img",
        # id/class/alt/title 기반
        "img[id*='captcha']:not([src*='dot.gif'])", "img[class*='captcha']",
        "img[alt='CAPTCHA']", "img[alt*='captcha']", "img[alt*='보안']",
        "img[title*='자동등록방지']",
        # 래퍼 스코프
        "#captchaArea img", ".captcha-box img", "td.captcha img", ".kcaptcha img",
        ".captcha_wrap img", ".captcha_box img", "#captchaWrap img", ".captchaImage img",
        # 인라인 base64 data-URI (일부 커스텀 스킨)
        "img[src^='data:image']",
        # src에 잡히는 캡차성 이미지(최후 일반)
        "img[src*='captcha']",
    ]

    # 1-pass: is_displayed()가 True인 것 우선
    for sel in SELECTORS:
        try:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if el.is_displayed():
                        r = _shot(el)
                        if r:
                            return r
                except Exception:
                    pass
        except Exception:
            pass

    # 2-pass: is_displayed()가 False로 잘못 잡혀도 크기(width>0,height>0)만 있으면 수용
    for sel in SELECTORS:
        try:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                try:
                    sz = el.size
                    if sz.get('width', 0) > 0 and sz.get('height', 0) > 0:
                        r = _shot(el)
                        if r:
                            return r
                except Exception:
                    pass
        except Exception:
            pass

    # 3) canvas / svg 캡차 (screenshot_as_base64는 img 아닌 엘리먼트도 캡처됨)
    for sel in ["canvas[id*='captcha']", "canvas[class*='captcha']", "#captcha canvas",
                ".captcha canvas", "svg[id*='captcha']", "svg[class*='captcha']",
                "#captcha svg", ".captcha svg"]:
        try:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                r = _shot(el)
                if r:
                    return r
        except Exception:
            pass

    # 4) background-image div/span 캡차: 계산된 스타일에서 captcha 토큰 탐지 후 그 박스를 스샷
    try:
        els = d.execute_script("""
            const out=[];
            for (const el of document.querySelectorAll("div,span,a,i,button,td,[class*='captcha'],[id*='captcha']")) {
                const bg = getComputedStyle(el).backgroundImage || '';
                if (bg && bg !== 'none' && /captcha|kcaptcha|securimage|seccode|boan|vcode/i.test(bg)) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) out.push(el);
                }
            }
            return out;
        """) or []
        for el in els:
            r = _shot(el)
            if r:
                return r
    except Exception:
        pass

    # 5) 폴백: 캡차 INPUT을 찾고, 공통 조상을 4단계까지 거슬러 올라가 가까운 <img>를 캡처
    #    (input#captcha_key, name=captcha_key, name=wr_key, id/name*=captcha)
    for isel in ["#captcha_key", "input[name='captcha_key']", "input[name='wr_key']",
                 "input[name*='captcha']", "input[id*='captcha']", "#secret_text"]:
        try:
            for inp in d.find_elements(By.CSS_SELECTOR, isel):
                node = inp
                for _ in range(4):  # 조상 최대 4단계 상승
                    try:
                        node = node.find_element(By.XPATH, "./..")
                    except Exception:
                        break
                    try:
                        cands = []
                        for im in node.find_elements(By.TAG_NAME, "img"):
                            try:
                                sz = im.size
                                w, h = sz.get('width', 0), sz.get('height', 0)
                            except Exception:
                                w = h = 0
                            try:
                                natw = d.execute_script("return arguments[0].naturalWidth||0;", im) or 0
                            except Exception:
                                natw = 0
                            if (w > 0 and h > 0) or natw > 5:
                                cands.append((w * h if w and h else natw, im))
                        if cands:
                            # 캡차는 작은 이미지 → 가장 작은 후보 우선(스페이서/배너 회피)
                            cands.sort(key=lambda t: t[0])
                            r = _shot(cands[0][1])
                            if r:
                                return r
                    except Exception:
                        pass
        except Exception:
            pass

    # 6) 최종 폴백: 글쓰기 <form> 내부에서 캡차 크기(폭 40~260, 높이 20~120)의 보이는 <img> 첫 매치
    #    캡차 INPUT이 있을 때만 실행(없으면 로고/버튼 이미지 오탐 위험이라 스킵)
    try:
        forms = d.find_elements(By.CSS_SELECTOR,
            "form[name='fwrite'], form#fwrite, form[action*='write_update'], form") if has_captcha_input else []
        for form in forms:
            try:
                for im in form.find_elements(By.TAG_NAME, "img"):
                    try:
                        sz = im.size
                        w, h = sz.get('width', 0), sz.get('height', 0)
                    except Exception:
                        continue
                    if 40 <= w <= 260 and 20 <= h <= 120:  # 캡차 전형 크기(아이콘·배너 배제)
                        r = _shot(im)
                        if r:
                            return r
            except Exception:
                pass
    except Exception:
        pass

    # 7) ★src URL 직접 fetch 폴백(서버측 requests): <img id="captcha_img" src="/exec/front/board/captcha?...">
    #    스샷이 타이밍/렌더로 실패해도, 브라우저 쿠키를 requests에 그대로 실어 그 src를 받아온다.
    #    (in-browser fetch는 async 콜백이 hang하는 경우가 있어 서버측 requests로 안정화 — 대표님 rental-zon)
    try:
        srcs=d.execute_script("""
            var out=[];
            var sels=['img#captcha_img','img#captcha_image','img[id*="captcha"]','img[src*="captcha"]','img[src*="Captcha"]'];
            var seen={};
            sels.forEach(function(s){
                document.querySelectorAll(s).forEach(function(im){
                    var u=im.currentSrc||im.src||im.getAttribute('src')||'';
                    if(u && !seen[u] && !/recaptcha|hcaptcha|gstatic|dot\\.gif/i.test(u)){ seen[u]=1; out.push(u); }
                });
            });
            return out;
        """) or []
        try: add_log(f"[캡차fetch] img src 후보 {len(srcs)}개" + (f" · 예:{srcs[0][:50]}" if srcs else ""))
        except Exception: pass
        if srcs:
            import requests as _rq, base64 as _b64
            from urllib.parse import urljoin as _uj
            sess=_rq.Session()
            try:
                cur=d.current_url or ''
                sess.headers.update({'User-Agent':d.execute_script("return navigator.userAgent")or _HTTP_UA,
                                     'Referer':cur})
                for ck in d.get_cookies():
                    try: sess.cookies.set(ck.get('name'),ck.get('value'),domain=ck.get('domain'))
                    except Exception: pass
            except Exception: cur=''
            for u in srcs[:4]:
                try:
                    au=u if u.startswith('http') else _uj(cur or (d.current_url or ''),u)
                    rr=sess.get(au,timeout=12,verify=False)
                    ct=(rr.headers.get('Content-Type') or '').split(';')[0].lower()
                    try: add_log(f"[캡차fetch] GET {rr.status_code} ct={ct} bytes={len(rr.content or b'')}")
                    except Exception: pass
                    # 이미지 Content-Type이면서 충분히 크면 캡차로 인정(HTML 챌린지 회신 배제)
                    if rr.status_code<400 and rr.content and len(rr.content)>200 and ct.startswith('image'):
                        return 'data:'+(ct or 'image/png')+';base64,'+_b64.b64encode(rr.content).decode()
                except Exception: continue
    except Exception:
        pass

    return ''  # 로그: 여기 도달 시 캡차 미필요(로그인/관리자·비활성)이거나 iframe 내부일 수 있음

def solve_captcha_with_2captcha(d,site,cap_type,cfg,timeout=300):
    """2captcha API를 사용해 CAPTCHA를 자동으로 해결한다."""
    api_key=(cfg.get('twocaptcha_api_key') or '').strip()
    if not api_key or not cfg.get('twocaptcha_enabled'):
        return False,'2captcha 설정 없음','',{}
    
    try:
        from twocaptcha import TwoCaptcha
        from selenium.webdriver.common.by import By
        solver=TwoCaptcha(api_key)
        
        # recaptcha 처리
        if cap_type=='recaptcha':
            # sitekey는 여러 위치에 있을 수 있다: data-sitekey 속성 / g-recaptcha 클래스 /
            # reCAPTCHA iframe src의 ?k= 파라미터 / 페이지 소스 정규식.
            sitekey=None
            for sel in ('[data-sitekey]','.g-recaptcha[data-sitekey]','div.g-recaptcha'):
                try:
                    el=d.find_element(By.CSS_SELECTOR,sel)
                    v=el.get_attribute('data-sitekey')
                    if v: sitekey=v; break
                except Exception: pass
            if not sitekey:
                try:
                    for fr in d.find_elements(By.CSS_SELECTOR,"iframe[src*='recaptcha']"):
                        src=fr.get_attribute('src') or ''
                        mk=re.search(r'[?&]k=([A-Za-z0-9_-]{20,})',src)
                        if mk: sitekey=mk.group(1); break
                except Exception: pass
            if not sitekey:
                try:
                    mk=re.search(r'data-sitekey=["\']([A-Za-z0-9_-]{20,})["\']',d.page_source or '')
                    if mk: sitekey=mk.group(1)
                except Exception: pass
            if not sitekey:
                return False,'sitekey를 찾을 수 없음','',{}
            try:
                result=solver.recaptcha(sitekey=sitekey,url=d.current_url)
                token=result.get('code') or result.get('token') or str(result)
                # reCAPTCHA는 g-recaptcha-response 텍스트영역에 토큰을 넣어야 검증된다.
                try:
                    d.execute_script(
                        "var t=document.getElementById('g-recaptcha-response');"
                        "if(!t){t=document.querySelector('textarea[name=\"g-recaptcha-response\"]');}"
                        "if(t){t.style.display='block';t.value=arguments[0];}", token)
                except Exception: pass
                _record_captcha_usage('recaptcha',True,cfg)
                return True,'recaptcha 해결 완료',token,{'type':'recaptcha','token':token}
            except Exception as e:
                return False,f'recaptcha 해결 실패: {str(e)[:80]}','',{}
        
        # kcaptcha (이미지) 처리
        elif cap_type=='kcaptcha':
            image_data=_captcha_image_data(d)
            if not image_data:
                return False,'captcha 이미지를 찾을 수 없음','',{}
            
            import base64, tempfile
            base64_str=image_data.split(',')[1] if ',' in image_data else image_data
            image_bytes=base64.b64decode(base64_str)
            
            with tempfile.NamedTemporaryFile(suffix='.png',delete=False) as f:
                f.write(image_bytes); temp_path=f.name
            
            try:
                result=solver.normal(temp_path)
                answer=result.get('code') or str(result)
                _record_captcha_usage('kcaptcha',True,cfg)
                return True,'kcaptcha 해결 완료',answer,{'type':'kcaptcha','answer':answer}
            except Exception as e:
                return False,f'kcaptcha 해결 실패: {str(e)[:80]}','',{}
            finally:
                try: os.unlink(temp_path)
                except: pass
        
        # Cloudflare Turnstile (Cafe24 veritas-hub 챌린지 등) 처리
        # — 2captcha가 토큰을 풀고, 그 토큰을 '브라우저 안'에서 페이지 콜백으로 제출한다.
        #   (순수 API로 /validate에 넣으면 토큰 푼 IP≠제출 IP라 403. 브라우저 컨텍스트에서
        #    같은 세션·IP로 제출해야 통과. 발굴/검증 단계라 느려도 무방 — 대표님 지시.)
        elif cap_type=='turnstile':
            try: src=d.page_source or ''
            except Exception: src=''
            try: page_url=d.current_url
            except Exception: page_url=''
            # sitekey: cf-turnstile data-sitekey 또는 0x로 시작하는 위젯키.
            #  ★관리형 챌린지(veritas-hub 등)는 DOM에 data-sitekey가 없고 turnstile iframe src·JS에 숨어있다.
            #   여러 경로로 추출 시도(2026-09-11 대표님 '2captcha 왜 안됨' 진단강화).
            sitekey=None
            mk=re.search(r'data-sitekey=["\']([A-Za-z0-9_\-]{15,})["\']',src)
            if mk: sitekey=mk.group(1)
            if not sitekey:  # turnstile iframe src의 sitekey/k 파라미터
                mk=re.search(r'challenges\.cloudflare\.com/[^"\']*?/([0x][A-Za-z0-9_]{18,})',src) or re.search(r'[?&](?:sitekey|k)=([A-Za-z0-9_\-]{18,})',src)
                if mk: sitekey=mk.group(1)
            if not sitekey:  # JS 내 turnstile.render('#el',{sitekey:'0x...'})
                mk=re.search(r'sitekey["\']?\s*[:=]\s*["\']([A-Za-z0-9_\-]{18,})["\']',src)
                if mk: sitekey=mk.group(1)
            if not sitekey:  # DOM에서 직접(iframe src 조회)
                try:
                    for fr in d.find_elements(By.CSS_SELECTOR,"iframe[src*='challenges.cloudflare.com']"):
                        _s=fr.get_attribute('src') or ''
                        _m=re.search(r'/([0x][A-Za-z0-9_]{18,})',_s) or re.search(r'[?&](?:sitekey|k)=([A-Za-z0-9_\-]{18,})',_s)
                        if _m: sitekey=_m.group(1); break
                except Exception: pass
            if not sitekey:
                mk=re.search(r'(0x[A-Za-z0-9_]{18,})',src)
                if mk: sitekey=mk.group(1)
            if not sitekey:
                add_log(f'[Turnstile진단] sitekey 못찾음 url={page_url[:60]} src길이={len(src)}')
                return False,'turnstile sitekey를 찾을 수 없음','',{}
            add_log(f'[Turnstile진단] sitekey={sitekey[:24]} url={page_url[:50]} — 2captcha 요청')
            try:
                result=solver.turnstile(sitekey=sitekey,url=page_url)
                token=result.get('code') if isinstance(result,dict) else str(result)
            except Exception as e:
                add_log(f'[Turnstile진단] 2captcha 해결실패: {str(e)[:100]}')
                return False,f'turnstile 해결 실패: {str(e)[:80]}','',{}
            if not token:
                return False,'turnstile 토큰 없음','',{}
            # 브라우저 안에서 페이지 콜백으로 토큰 제출(같은 IP/세션 → /validate 통과).
            #  1) 표준 위젯 hidden input(cf-turnstile-response)에 토큰 주입
            #  2) 페이지가 정의한 콜백(javascriptCallback 등) 호출 — Cafe24 challenge가 /validate fetch 수행
            injected=False
            try:
                injected=bool(d.execute_script("""
                    var tok=arguments[0], done=false;
                    // 1) hidden response input들 채우기
                    var names=['cf-turnstile-response','g-recaptcha-response'];
                    names.forEach(function(n){
                        document.querySelectorAll('[name="'+n+'"],#'+n).forEach(function(el){el.value=tok;});
                    });
                    // 2) 알려진 콜백 후보 호출(Cafe24 veritas: javascriptCallback)
                    var cbs=['javascriptCallback','onTurnstileSuccess','turnstileCallback','tsCallback'];
                    for(var i=0;i<cbs.length;i++){
                        try{ if(typeof window[cbs[i]]==='function'){ window[cbs[i]](tok); done=true; break; } }catch(e){}
                    }
                    // 3) turnstile 렌더 콜백(data-callback 지정된 함수명)
                    if(!done){
                        var el=document.querySelector('.cf-turnstile[data-callback]');
                        if(el){ var fn=el.getAttribute('data-callback'); if(fn&&typeof window[fn]==='function'){window[fn](tok);done=true;} }
                    }
                    return done;
                """, token))
            except Exception: pass
            _record_captcha_usage('turnstile',True,cfg)
            if injected:
                return True,'turnstile 해결 완료(콜백 제출)',token,{'type':'turnstile','token':token}
            # 콜백을 못 찾았어도 토큰은 넣었으니 폼 제출 시 검증되게 True로 진행
            return True,'turnstile 토큰 주입(콜백 미발견)',token,{'type':'turnstile','token':token}

        else:
            return False,f'지원하지 않는 captcha 타입: {cap_type}','',{}

    except ImportError:
        return False,'2captcha 라이브러리 미설치','',{}
    except Exception as e:
        return False,f'2captcha 오류: {str(e)[:80]}','',{}

def wait_for_manual_captcha(d,site,cap_type,timeout=600):
    """관리자가 CAPTCHA 값을 입력할 때까지 같은 브라우저 세션을 보존한다."""
    from selenium.webdriver.common.by import By
    tid=uuid.uuid4().hex[:12]; ev=threading.Event(); now=_kst_now()
    task={'id':tid,'site_id':site.get('id'),'site_name':site.get('name') or site.get('site_url',''),
          'captcha_type':cap_type,'image_data':_captcha_image_data(d),'status':'waiting_input',
          'message':'CAPTCHA를 입력하면 같은 작성 화면에서 자동 발행합니다.',
          'created_at':now.strftime('%Y-%m-%d %H:%M:%S'),
          'expires_at':(now+timedelta(seconds=timeout)).strftime('%Y-%m-%d %H:%M:%S'),
          'event':ev,'value':'','cancelled':False}
    with CAPTCHA_LOCK: CAPTCHA_TASKS[tid]=task
    add_log(f'[CAPTCHA 대기] {task["site_name"]} — 관리자 입력 후 자동 발행')
    if not ev.wait(timeout):
        with CAPTCHA_LOCK: task['status']='expired'; task['message']='입력 대기시간 10분 만료'
        return False,'CAPTCHA 수동 입력 대기시간 만료',tid
    with CAPTCHA_LOCK:
        value=str(task.get('value') or '').strip(); cancelled=bool(task.get('cancelled'))
    if cancelled:
        with CAPTCHA_LOCK: task['status']='cancelled'; task['message']='관리자가 취소함'
        return False,'CAPTCHA 수동 입력 취소',tid
    inputs=[]
    for sel in ["input[name='captcha_key']","#captcha_key","input[name='wr_key']","input[name*='captcha']","input[id*='captcha']"]:
        try: inputs.extend([x for x in d.find_elements(By.CSS_SELECTOR,sel) if x.is_displayed()])
        except Exception: pass
        if inputs: break
    if not inputs:
        with CAPTCHA_LOCK: task['status']='failed'; task['message']='CAPTCHA 입력칸을 찾지 못함'
        return False,'CAPTCHA 입력칸을 찾지 못함',tid
    try:
        inputs[0].clear(); inputs[0].send_keys(value)
        with CAPTCHA_LOCK: task['status']='submitting'; task['message']='입력 완료 · 자동 등록 중'
        return True,'',tid
    except Exception as e:
        with CAPTCHA_LOCK: task['status']='failed'; task['message']=str(e)[:120]
        return False,'CAPTCHA 입력 전달 실패: '+str(e)[:100],tid

def finish_captcha_task(tid,ok,message):
    if not tid: return
    with CAPTCHA_LOCK:
        t=CAPTCHA_TASKS.get(tid)
        if t:
            t['status']='done' if ok else 'failed'; t['message']=str(message)[:180]
            t['finished_at']=_kst_now().strftime('%Y-%m-%d %H:%M:%S')

def _page_is_blocked(d):
    """403/보안 차단 페이지 판별. 일반 사이트의 Cloudflare 스크립트 문자열은 차단으로 보지 않는다."""
    try:
        from selenium.webdriver.common.by import By
        html=(d.page_source or '').lower()
        title=(d.title or '').lower()
        body=(d.find_element(By.TAG_NAME,'body').text or '').lower()[:3000]
    except Exception:
        html=''; title=''; body=''
    strong=['403 forbidden','access denied','error 1020','request blocked','접근이 거부','차단되었습니다']
    if any(k in title or k in body for k in strong): return True
    # Cloudflare 도전/차단 화면은 제품명 하나가 아니라 고유 문구 조합으로 확인한다.
    if ('attention required' in title or 'just a moment' in title) and \
       ('cloudflare' in html or 'cf-chl-' in html): return True
    return False

# ==================== 실패 원인 분류 ====================
def classify_fail(msg):
    """발행 실패 메시지를 사람이 읽을 수 있는 원인으로 분류. (코드, 한글, 일시적여부)"""
    ms=str(msg or ''); low=ms.lower()
    if '도배방지' in ms or '너무 빠' in ms:
        return 'flood','도배방지 간격대기',True   # 일시적 → 학습된 간격 뒤 자동 재시도
    if '캡차' in ms or 'captcha' in low or '보안 인증' in ms:
        return 'captcha','캡차/보안인증 감지',False
    if any(k in ms for k in ['타임아웃','시간 초과']) or any(k in low for k in
           ['timeout','timed out','renderer','chrome not reachable','disconnected','connection',
            'session deleted','session not created','net::err','unreachable','max retries','read timed']):
        return 'timeout','타임아웃/브라우저',True   # 일시적 → 자동 재시도
    if any(k in ms for k in ['로그인','아이디','비밀번호']) or any(k in low for k in
           ['login','mb_id','mb_password','password']):
        return 'login','로그인 실패',False
    if any(k in ms for k in ['권한','차단','금지','스팸','도배']) or any(k in low for k in
           ['blocked','forbidden','403','spam','denied','권한이 없']):
        return 'blocked','차단/권한없음',False
    if any(k in ms for k in ['게시판','에디터','셀렉터','확인 불가','글쓰기','페이지 못찾음','입력란 못찾음']) or any(k in low for k in
           ['write.php','bo_table','board_no','no such element','not found','404','wr_subject','wr_content']):
        return 'board','게시판/에디터 못찾음',False
    return 'other','기타',False

# ==================== 데이터 백업 ====================
def build_backup_zip():
    """설정·사이트·이력·예약·키워드·큐를 zip 하나로 묶음(민감정보 마스킹)."""
    import io,zipfile
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for p in [SITES_FILE,HISTORY_FILE,SCHED_FILE,KEYWORDS_FILE,IMAGES_FILE,MEMBERS_FILE,CAND_FILE,QUEUE_FILE]:
            if os.path.exists(p): z.write(str(p),os.path.basename(str(p)))
        # config 는 비번해시·API키 제거하고 저장
        c=dict(load_config()); c.pop('password',None); c['openai_key']=''; c['telegram_token']=''
        z.writestr('config.json',json.dumps(c,ensure_ascii=False,indent=2))
    buf.seek(0); return buf.read()

def cleanup_disk():
    """★디스크 풀(Errno 28) 방지(대표님 서버 2026-09-11): 누적된 크롬 임시프로파일·백업·오래된 파일 정리.
       크롬 user-data-dir(chr_*), app.py.safebak-*, .tmp, 오래된 스크린샷/코어덤프를 삭제한다."""
    import tempfile, glob, shutil
    removed=0
    try:
        # 1) 크롬 임시 프로파일 폴더(chr_*) — selenium이 남긴 것 포함. 크게 쌓임.
        tmp=tempfile.gettempdir()
        for p in glob.glob(os.path.join(tmp,'chr_*'))+glob.glob(os.path.join(tmp,'.com.google.Chrome.*'))+glob.glob(os.path.join(tmp,'.org.chromium.*'))+glob.glob(os.path.join(tmp,'scoped_dir*')):
            try: shutil.rmtree(p,ignore_errors=True); removed+=1
            except Exception: pass
    except Exception: pass
    try:
        # 2) app.py 백업 — 최신 5개만 남김.
        baks=sorted(glob.glob(os.path.join(str(BASE_DIR),'app.py.safebak-*'))+glob.glob(os.path.join(str(BASE_DIR),'app.py.bak*')),reverse=True)
        for p in baks[5:]:
            try: os.remove(p); removed+=1
            except Exception: pass
    except Exception: pass
    try:
        # 3) data/의 옛 .bak·.tmp 정리.
        for p in glob.glob(os.path.join(str(BASE_DIR),'data','*.bak'))+glob.glob(os.path.join(str(BASE_DIR),'data','.*.tmp')):
            try:
                if os.path.getmtime(p) < time.time()-86400: os.remove(p); removed+=1
            except Exception: pass
    except Exception: pass
    if removed:
        try: add_log(f'[디스크정리] 임시·백업 {removed}개 삭제','정리')
        except Exception: pass
    return removed

def do_backup(cfg=None, reason='자동'):
    cfg=cfg or load_config()
    try:
        data=build_backup_zip()
    except Exception as e:
        add_log(f'[백업] zip 생성 실패: {str(e)[:80]}'); return False,str(e)[:120]
    ts=_kst_now().strftime('%Y%m%d_%H%M')
    cap=(f'📦 찌라시 백업 ({reason})\n{_kst_now().strftime("%Y-%m-%d %H:%M")} KST\n'
         f'사이트 {len(load_sites())}개 · 이력 {len(load_json(HISTORY_FILE,[]))}건')
    ok,err=send_telegram_doc(cfg,f'chirashi_backup_{ts}.zip',data,caption=cap)
    add_log(f'[백업] {"전송 성공" if ok else "실패: "+err} ({reason})')
    return ok,err

# ==================== 발행 검증 (게시글 생존 확인) ====================
def check_post_alive(url):
    """게시된 URL 이 아직 살아있는지 HTTP 로 확인. True=생존, False=삭제/없음, None=확인불가."""
    import requests as _rq
    try:
        r=_rq.get(url,timeout=15,verify=False,allow_redirects=True,
                  headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'})
        if r.status_code==429:   # 과도한 요청 — Retry-After 존중하고 이번엔 판정 보류
            ra=r.headers.get('Retry-After','')
            try: time.sleep(min(int(ra),30)) if ra.isdigit() else time.sleep(5)
            except Exception: time.sleep(5)
            return None
        if r.status_code==403: return None   # 차단 — 삭제로 오판하지 않도록 보류
        if r.status_code==404 or r.status_code>=500: return False
        if r.status_code>=400: return False
        body=r.text or ''
        dead=['존재하지 않','삭제된','삭제되었','없는 게시','게시물이 없','원글이 없','잘못된 접근',
              '페이지를 찾을 수 없','not found','더 이상','권한이 없']
        if any(s in body for s in dead): return False
        return True
    except Exception:
        return None

def verify_once(limit=40):
    """이력의 성공 발행글 URL 을 재확인해 생존여부(alive)를 기록. 처리 건수 반환."""
    cfg=load_config()
    with JOB_LOCK:
        h=load_json(HISTORY_FILE,[])
    cand=[r for r in h if r.get('status')=='done' and str(r.get('result_url','')).startswith('http')]
    cand.sort(key=lambda r:(r.get('verified_at') or ''))   # 미검증·오래된 것 우선
    todo=cand[:max(1,limit)]
    results={}
    for r in todo:
        alive=check_post_alive(r['result_url'])
        if alive is not None: results[r['id']]=('yes' if alive else 'no')
        time.sleep(1)
    if results:
        with JOB_LOCK:
            h=load_json(HISTORY_FILE,[]); vat=_kst_now().strftime('%Y-%m-%d %H:%M')
            for rec in h:
                if rec.get('id') in results:
                    rec['alive']=results[rec['id']]; rec['verified_at']=vat
            save_json(HISTORY_FILE,h)
        add_log(f'[검증] {len(results)}건 확인 (생존 {sum(1 for v in results.values() if v=="yes")}·삭제 {sum(1 for v in results.values() if v=="no")})')
    return len(results)

def verify_loop():
    while True:
        try:
            if load_config().get('verify_enabled',True): verify_once(40)
        except Exception as e:
            add_log(f'[검증 오류] {str(e)[:80]}')
        time.sleep(3600)

# ==================== Selenium 드라이버 (회피코드 제거) ====================
_drivers = {}
_drv_lock = threading.Lock()
# ★chromedriver 경로 1회 캐시(대표님 WinError5 2026-09-11): 동시 4워커가 ChromeDriverManager().install()을
#   각자 호출하면 같은 driver.exe 파일을 동시에 만지다 'WinError 5 액세스 거부'. 락으로 1회만 설치·재사용.
_driver_path=[None]; _driver_path_lock=threading.Lock()
def _get_driver_path():
    with _driver_path_lock:
        if not _driver_path[0]:
            from webdriver_manager.chrome import ChromeDriverManager
            _driver_path[0]=ChromeDriverManager().install()
        return _driver_path[0]

def get_driver(remote=False):
    """remote=True면 Bright Data Scraping Browser(원격 크롬)로 연결 — CF+로그인 사이트(타카고 등)용.
       원격은 CF·캡차를 Bright Data가 자동처리. 로컬 크롬과 별도 키로 캐시해 섞이지 않게 한다."""
    tid = threading.current_thread().name
    from selenium import webdriver
    # ★Scraping Browser 원격 크롬(CF+로그인용). 설정된 경우에만.
    if remote:
        cfg=load_config()
        ep=str(cfg.get('sbr_endpoint') or '').strip()
        if cfg.get('sbr_enabled') and ep:
            rkey='__SBR__'+tid
            with _drv_lock:
                if rkey in _drivers:
                    try: _drivers[rkey].current_url; return _drivers[rkey]
                    except:
                        try: _drivers[rkey].quit()
                        except: pass
                        _drivers.pop(rkey,None)
                if not ep.startswith('http'): ep='https://'+ep
                if ':9515' not in ep and not re.search(r':\d+',ep.split('@')[-1]): ep=ep.rstrip('/')+':9515'
                # ★geo 완전 정화(2026-09-09 대표님 'Wrong customer name' 마저 잡기): endpoint에 예전
                #   주입/저장으로 -country-XX가 박혀 있으면 username을 깨 인증실패 → username 부분에서 제거.
                #   (username=스킴 뒤 ~ 첫 ':' 전. 비번·호스트는 건드리지 않음.)
                _mm=re.match(r'(https?://)([^:@/]+)(.*)$',ep)
                if _mm:
                    _user=re.sub(r'-country-[a-z]{2}','',_mm.group(2))   # username에서 -country-XX 제거
                    ep=f'{_mm.group(1)}{_user}{_mm.group(3)}'
                # geo 주입(sbr_country)은 기본 끔. 명시할 때만(권장 안 함) 다시 붙임.
                _cc=str(cfg.get('sbr_country') or '').strip().lower()
                if _cc and '-country-' not in ep:
                    _m2=re.match(r'(https?://)([^:@/]+)(.*)$',ep)
                    if _m2: ep=f'{_m2.group(1)}{_m2.group(2)}-country-{_cc}{_m2.group(3)}'
                opts=webdriver.ChromeOptions(); opts.add_argument('--lang=ko-KR')
                # ★renderer timeout 근본해결(2026-09-08 대표님 '원격크롬 방식 재검토'): pageLoadStrategy='none'.
                #   기본(normal)은 d.get()이 페이지 완전로드까지 대기 → 원격크롬+CF처리+해외지연이 겹쳐
                #   renderer timeout. 'none'이면 get이 로드완료 안기다리고 즉시반환 → 요소를 폴링하면 됨.
                #   (CF는 Bright Data가 백그라운드로 처리하므로 로드완료 신호 없어도 폼은 그려짐)
                opts.page_load_strategy='none'
                # command 응답 타임아웃도 넉넉히(해외 왕복 대비).
                try:
                    from selenium.webdriver.remote.remote_connection import RemoteConnection
                    conn=RemoteConnection(ep,keep_alive=True)
                    try: conn.set_timeout(180)
                    except Exception: pass
                    d=webdriver.Remote(command_executor=conn,options=opts)
                except Exception:
                    d=webdriver.Remote(command_executor=ep,options=opts)
                # none 전략이라 page_load_timeout은 사실상 무의미하나 안전값. implicitly_wait로 요소대기.
                try: d.set_page_load_timeout(120)
                except Exception: pass
                d.implicitly_wait(5)
                _drivers[rkey]=d; return d
        # 원격 요청인데 미설정이면 로컬로 폴백(발행 안 끊김)
    with _drv_lock:
        if tid in _drivers:
            try: _drivers[tid].current_url; return _drivers[tid]
            except: pass
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        opts = webdriver.ChromeOptions()
        # ★환경변수 CHIRASHI_HEADFUL=1이면 크롬 창을 보이게(non-headless) 띄운다(대표님 PC에서 Cafe24
        #   로그인·Turnstile을 눈으로 보며·통과율 높이려고. headless는 CF가 더 잘 막음). 서버는 미설정=headless 유지.
        if os.environ.get('CHIRASHI_HEADFUL','') not in ('1','true','yes'):
            opts.add_argument('--headless=new')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage'); opts.add_argument('--disable-gpu')
        opts.add_argument('--window-size=1920,1080'); opts.add_argument('--log-level=3')
        # 정상 환경 일치(위조 아님): 서버가 한국(Seoul)에 있으므로 로케일/언어를 실제와 맞춤.
        # navigator.webdriver·Canvas·WebGL 등은 건드리지 않음(지문 위조·스텔스 미사용).
        opts.add_argument('--lang=ko-KR')
        opts.add_experimental_option('prefs',{'intl.accept_languages':'ko-KR,ko'})
        opts.page_load_strategy='eager'
        cb=os.environ.get('CHROME_BIN')  # 리눅스 VPS: chromium 경로 지정 가능
        if cb: opts.binary_location=cb
        # ★스레드별 독립 프로파일(WinError5·프로파일잠금 방지): 동시 크롬이 같은 기본 프로파일을 공유하면 충돌.
        try:
            import tempfile
            _udd=os.path.join(tempfile.gettempdir(),f'chr_{re.sub(r"[^A-Za-z0-9]","_",tid)}')
            opts.add_argument('--user-data-dir='+_udd)
        except Exception: pass
        svc=Service(_get_driver_path())   # 캐시된 driver 경로(동시 install 충돌 방지)
        d=webdriver.Chrome(service=svc,options=opts)
        d.set_page_load_timeout(25); d.implicitly_wait(3)
        _drivers[tid]=d; return d

def dismiss_alerts(d):
    """alert 팝업을 닫으면서 텍스트를 캡처(d._last_alerts에 누적) — 도배방지 등 규칙 학습용."""
    texts=[]
    for _ in range(3):
        try:
            a=d.switch_to.alert; texts.append(a.text or ''); a.accept(); time.sleep(0.2)
        except: break
    if texts:
        try: d._last_alerts=(getattr(d,'_last_alerts',[]) or [])[-4:]+texts
        except Exception: pass
    return texts

def _flood_wait_seconds(text):
    """도배방지/재작성 제한 알림 문구에서 대기 시간(초)을 추출. 도배 신호가 없으면 0.
       예: '너무 빠른 시간...', '도배방지...', '10초 후에 다시', '3분 후 작성 가능'."""
    t=str(text or '')
    if not any(k in t for k in ('도배','너무 빠','잠시 후','초 후','초가','초 뒤','분 후','분 뒤',
                                 '다시 등록','다시 작성','다시 쓰','시간이 지나','시간이 경과','제한')):
        return 0
    mm=re.search(r'(\d+)\s*분',t)
    if mm: return min(3600,int(mm.group(1))*60)
    ss=re.search(r'(\d+)\s*초',t)
    if ss: return min(3600,int(ss.group(1)))
    return 60   # 시간 명시 없으면 안전하게 60초

def html_to_plain(content):
    """HTML 모드가 없는 게시판에 넣을 읽기 쉬운 일반 텍스트."""
    text=re.sub(r'(?is)<(script|style).*?>.*?</\1>','',content or '')
    text=re.sub(r'(?i)<br\s*/?>','\n',text)
    text=re.sub(r'(?i)</(?:p|div|h[1-6]|li|tr)>','\n',text)
    text=re.sub(r'(?s)<[^>]+>','',text)
    text=html_lib.unescape(text).replace('\xa0',' ')
    return re.sub(r'\n{3,}','\n\n',text).strip()

def enable_html_mode(d):
    """HTML을 '확실히 렌더하는' 게시판에서만 HTML 모드를 켠다: 실제 HTML 체크박스(그누보드 html)
       또는 KBoard '코드' 탭이 있고 켤 수 있을 때만 True. 그 외(비표준/WYSIWYG 에디터 등 HTML이
       안 먹혀 raw 코드로 노출되는 곳)는 False → 일반 텍스트로 발행한다.
       (대표님 지시: HTML/코드 버튼 없는 곳은 코드 노출 대신 그냥 텍스트)."""
    from selenium.webdriver.common.by import By
    for el in d.find_elements(By.CSS_SELECTOR,"input[type='checkbox']"):
        if not _sel_vis(el): continue
        blob=' '.join([_sel_attr(el,'name'),_sel_attr(el,'id'),_sel_attr(el,'value'),_sel_attr(el,'title')]).lower()
        try:
            eid=_sel_attr(el,'id')
            if eid:
                labels=d.find_elements(By.CSS_SELECTOR,"label[for='"+eid.replace("'","\\'")+"']")
                blob+=' '+' '.join((x.text or '') for x in labels).lower()
            parent=el.find_element(By.XPATH,'..')
            blob+=' '+(parent.text or '').lower()
        except Exception: pass
        if 'html' not in blob: continue
        # ★cocobrony 등: <input name="html" onclick="html_auto_br(this)" value=""> — JS onclick이 걸려있어
        #   execute_script 클릭은 리스너를 안 태워 체크만 되고 서버 html 모드가 안 켜질 수 있다.
        #   → 실제 el.click()으로 onclick(html_auto_br) 발화시키고, 안 되면 JS폴백. 체크 상태 확정.
        try:
            if not el.is_selected():
                try: el.click()                       # 실제 클릭 — onclick 리스너 발화
                except Exception:
                    try: d.execute_script('arguments[0].click()',el)
                    except Exception: pass
            # value가 빈칸이면 그누보드가 html모드로 못 받을 수 있어 강제로 'html1' 세팅.
            try:
                if el.is_selected() and not (el.get_attribute('value') or '').strip():
                    d.execute_script("arguments[0].value='html1';",el)
            except Exception: pass
            if el.is_selected(): return True
        except Exception: continue
    # WordPress/KBoard 클래식 에디터는 HTML 체크박스 대신
    # '비주얼 / 코드' 탭을 제공한다. 코드 탭을 누르면 실제 제출용
    # textarea(kboard_content)가 표시되므로 그곳에 HTML 조각을 넣는다.
    try:
        kboard_textareas=d.find_elements(By.CSS_SELECTOR,"textarea#kboard_content,textarea[name='kboard_content']")
        if kboard_textareas:
            for btn in d.find_elements(By.CSS_SELECTOR,"button,a,input[type='button']"):
                if not _sel_vis(btn): continue
                label=((btn.text or '')+' '+_sel_attr(btn,'value')+' '+_sel_attr(btn,'id')+' '+_sel_attr(btn,'class')).strip().lower()
                if re.search(r'(^|\s)(코드|code|html)(\s|$)',label):
                    d.execute_script('arguments[0].click()',btn); time.sleep(0.2)
                    return True
    except Exception: pass
    # ★HTML을 그대로 렌더하는 리치에디터는 HTML 모드로 발행(예쁜 디자인 유지 — 대표님 지시).
    #   smarteditor2(네이버 SE2, oEditors.SET_IR)·XpressEngine(XE) 에디터는 HTML 콘텐츠를 렌더한다.
    try:
        src=(d.page_source or '').lower()
        # smarteditor2(네이버 SE2): 아래 중 하나라도 있으면 HTML 렌더 에디터로 확정.
        #  ※ 스킨마다 표기가 달라(oEditors=[]·oEditors.getById·plugin/editor/smarteditor2·
        #    class="smarteditor2"·se2_input_wysiwyg 등) 넓게 감지한다.
        #    실제 발행도 oEditors.getById['wr_content'].exec('SET_IR',...)로 HTML을 넣으므로
        #    이 신호가 있으면 HTML이 그대로 렌더된다.
        if any(k in src for k in (
                'smarteditor2','se2_input_wysiwyg','plugin/editor/smarteditor2',
                'oeditors.getbyid','oeditors.geteditorbyidorname','typeof oeditors',
                "oeditors = [", 'oeditors=[', 'class="smarteditor2"')):
            return True
        # XpressEngine 에디터(ckeditor/xpresseditor 등) — editor_sequence가 있는 XE 글쓰기
        if 'editor_sequence' in src and ('xpressengine' in src or '/modules/editor' in src or 'ckeditor' in src):
            return True
    except Exception: pass
    return False

def editor_content_for_page(d,content_html):
    html_mode=enable_html_mode(d)
    return (content_html if html_mode else html_to_plain(content_html)),html_mode

def _brand_email(cfg, site=None):
    """발행 연락처(wr_email)용 이메일을 자동 생성한다. post_email이 설정돼 있으면 그걸 쓰고,
    없으면 브랜드명을 영문 슬러그로 바꿔 <slug><사이트별번호>@gmail.com 형태로 만든다.
    (인증메일이 오지 않는 단순 연락처 칸이라 실제 수신 불가여도 무방하다.)
    사이트마다 뒤 번호가 달라지도록 site id 해시를 붙여 한 주소를 전 사이트에 도배하지 않는다."""
    fixed=(cfg.get('post_email') or '').strip()
    if fixed: return fixed
    slug=re.sub(r'[^a-z0-9]','',(cfg.get('brand') or 'post').strip().lower())
    if not slug or not re.match(r'^[a-z]',slug):  # 한글 등으로 슬러그가 비면 안전한 기본값
        slug='post'+(slug or '')
    slug=slug[:20]
    suffix=''
    if site is not None:
        sid=str(site.get('id') or site.get('site_url') or '')
        if sid:
            import hashlib
            suffix=str(int(hashlib.md5(sid.encode()).hexdigest(),16)%1000)  # 사이트별 0~999 고정 번호(돌려쓰기)
    return f'{slug}{suffix}@gmail.com'

def _post_password(cfg=None):
    """게시글(비회원/비밀글) 비밀번호. ★대표님 지시(2026-09-08): 비밀글로 올라가도 나중에
       열람할 수 있게 '고정' 비번을 쓰고 로그·이력에 저장한다. 예전엔 랜덤(token_hex)이라 잃어버렸음.
       config guest_post_password 있으면 그걸, 없으면 고정 기본값(항상 동일=복구 가능)."""
    cfg=cfg or load_config()
    return (str(cfg.get('guest_post_password') or '').strip() or 'twseo1234')

def fill_required_post_fields(d,site):
    """빨간 별표/required 추가 필드를 의미에 맞는 설정값으로 채운다."""
    from selenium.webdriver.common.by import By
    cfg=load_config(); filled=[]; missing=[]
    brand=(cfg.get('brand') or '게시자').strip()
    # 작성자 이름: 작업실별로 지정한 값(site.writer_name) 우선, 없으면 브랜드. 발행 직전 site에 심어짐.
    writer=(str(site.get('writer_name') or '').strip() or brand)
    try: phone_val=format_phone(pick_phone(cfg))
    except Exception: phone_val=(cfg.get('phone') or '')
    guest_pw=_post_password(cfg)   # ★고정 비번(비밀글 복구용) — 랜덤 금지
    video_url=(cfg.get('video_url') or '').strip()
    landing=(cfg.get('landing_url') or '').strip()
    post_email=_brand_email(cfg, site)  # wr_email 등 이메일 필수항목 자동 채움값(빈값 방지)
    try: page_text=(d.find_element(By.TAG_NAME,'body').text or '').lower()[:6000]
    except Exception: page_text=''
    # ★비밀글 체크박스 해제(대표님 지시 2026-09-11): 기본 체크돼 있으면 글이 비밀글로 올라가 구글 색인0.
    #   그누보드 wr_option[]=secret / '비밀글' 라벨 체크박스가 선택돼 있으면 풀어 공개글로 발행.
    try:
        for cb in d.find_elements(By.CSS_SELECTOR,"input[type='checkbox']"):
            try:
                if not cb.is_selected(): continue
                _blob=(_sel_attr(cb,'name')+' '+_sel_attr(cb,'id')+' '+_sel_attr(cb,'value')).lower()
                _lbl=''
                try:
                    _cid=_sel_attr(cb,'id')
                    if _cid: _lbl=' '.join((x.text or '') for x in d.find_elements(By.CSS_SELECTOR,"label[for='"+_cid.replace("'","\\'")+"']")).lower()
                    _lbl+=' '+(cb.find_element(By.XPATH,'..').text or '').lower()
                except Exception: pass
                if 'secret' in _blob or 'wr_option' in _blob or '비밀' in _lbl or '비밀글' in _blob:
                    try: cb.click()
                    except Exception: d.execute_script('arguments[0].checked=false;',cb)
            except Exception: continue
    except Exception: pass
    # KBoard 비회원 글쓰기는 별표 필수항목이어도 required 속성이 없는
    # 경우가 많다. 알려진 작성자/비밀번호 필드는 선제적으로 채운다.
    for sel,value,label in [
        ("#kboard-input-member-display,input[name='member_display']",writer,'member_display'),
        ("#kboard-input-password,input[name='password']",guest_pw,'password')]:
        try:
            elems=[x for x in d.find_elements(By.CSS_SELECTOR,sel) if _sel_vis(x)]
            if not elems: continue
            el=elems[0]
            if (el.get_attribute('value') or '').strip(): continue
            if value:
                el.clear(); el.send_keys(value); filled.append(label)
            else: missing.append(label)
        except Exception: missing.append(label)
    skip={'wr_subject','subject','title','wr_content','content','captcha_key','captcha'}
    for el in d.find_elements(By.CSS_SELECTOR,"input[required],textarea[required],select[required],.required"):
        if not _sel_vis(el): continue
        typ=(_sel_attr(el,'type') or el.tag_name).lower(); name=_sel_attr(el,'name') or _sel_attr(el,'id')
        if not name or name in skip or typ in ('hidden','submit','button','checkbox','radio','file'): continue
        try:
            if (el.get_attribute('value') or '').strip(): continue
        except Exception: pass
        try: parent=(el.find_element(By.XPATH,'..').text or '').lower()
        except Exception: parent=''
        blob=(name+' '+_sel_attr(el,'id')+' '+_sel_attr(el,'placeholder')+' '+parent).lower()
        tag=el.tag_name.lower()
        # select 필수항목: 값 있는 첫 옵션을 고른다
        if tag=='select':
            try:
                from selenium.webdriver.support.ui import Select
                sel_obj=Select(el); chosen=False
                for opt in sel_obj.options:
                    ov=(opt.get_attribute('value') or '').strip()
                    if ov and ov not in ('0','-1'):
                        sel_obj.select_by_value(ov); filled.append(name); chosen=True; break
                if not chosen and len(sel_obj.options)>1:
                    sel_obj.select_by_index(1); filled.append(name); chosen=True
                if not chosen: missing.append(name)
            except Exception: missing.append(name)
            continue
        value=''
        if re.search(r'(wr_name|이름)',blob): value=writer
        elif typ=='password' or re.search(r'(password|passwd|비밀번호)',blob): value=guest_pw
        elif re.search(r'(email|e-mail|이메일)',blob): value=post_email  # 자동 브랜드메일(빈값 방지)
        elif re.search(r'(tel|phone|mobile|연락처|전화|휴대|핸드폰)',blob): value=(phone_val or post_email)  # 실제 번호(브랜드 넣지 않음)
        elif re.search(r'(youtube|youtu\.be|vimeo|동영상|영상)',blob) or (name in ('wr_link1','link1') and re.search(r'(youtube|유투브|유튜브|vimeo|비메오|동영상)',page_text)): value=video_url or landing or site.get('site_url','')
        elif re.search(r'(link|url|homepage|홈페이지|링크)',blob): value=landing or site.get('site_url','')
        else:
            # 의미를 특정 못한 필수 텍스트(주소 등)는 발행을 막지 말고 채운다. 브랜드 대신 작성자명 사용
            # (대표님 지시: 작성자 설정하면 '인천홍마니' 브랜드가 안 나오게). 이메일 형태면 이메일값.
            value=post_email if ('mail' in blob or '이메일' in blob) else writer
        if value:
            if _robust_fill(d,el,value): filled.append(name)
            else: missing.append(name)
        else: missing.append(name)
    return filled,list(dict.fromkeys(missing))

def reset_driver():
    """현재 워커 스레드의 크롬 드라이버를 종료 → 다음 get_driver() 에서 새로 띄움(자동 재시작).
       ★로컬 드라이버뿐 아니라 Scraping Browser 원격 드라이버(__SBR__ 키)도 함께 정리
       (안 닫으면 원격 세션이 쌓여 Bright Data 비용 누수)."""
    tid=threading.current_thread().name
    with _drv_lock:
        d=_drivers.pop(tid,None)
        rd=_drivers.pop('__SBR__'+tid,None)
    for x in (d,rd):
        if x:
            try: x.quit()
            except: pass

# ==================== Selenium 그누보드 글쓰기 ====================
def _robust_fill(d, el, value):
    """입력 요소에 값을 넣는다. send_keys가 실패(hidden/readonly 등 invalid element state)하면
       JS로 .value를 설정하고 input·change 이벤트를 발생시켜 에디터 연동 스크립트가 반영하게 한다.
       hidden 필드도 확실히 채워진다(많은 그누보드 커스텀 스킨 대응)."""
    try:
        if el.is_displayed() and el.is_enabled():
            el.clear(); el.send_keys(value); return True
    except Exception:
        pass
    try:
        d.execute_script(
            "var e=arguments[0],v=arguments[1];e.value=v;"
            "e.dispatchEvent(new Event('input',{bubbles:true}));"
            "e.dispatchEvent(new Event('change',{bubbles:true}));", el, value)
        return True
    except Exception:
        return False

def _verify_post_by_title(d, bbs, bo, title):
    """제출 후 확인 불가일 때 최후 검증: 게시판 목록을 다시 읽어 방금 올린 제목이
       실제로 등록됐는지 확인한다(느린 서버의 리다이렉트 지연으로 인한 오탐 방지).
       성공하면 해당 글의 뷰 URL을, 실패하면 None을 반환한다. 우회가 아니라 '읽기' 검증."""
    from selenium.webdriver.common.by import By
    def norm(s):
        # 제목 변형(010→OIO, 공백·구분기호 차이)에 견디도록 한글·영숫자만 남겨 비교
        return re.sub(r'[^0-9A-Za-z가-힣]', '', str(s or '')).lower()
    key=norm(title)
    if len(key) < 8:
        return None
    # 목록은 긴 제목을 잘라 보여주므로(행 텍스트가 제목의 접두어) 행⊆제목으로 매칭한다.
    # 또한 브랜드/전화 등 '이 글에만 있는' 특징 조각으로도 본문 전체를 대조한다.
    frag=key[-12:] if len(key)>=12 else key   # 제목 뒤쪽(브랜드/전화)이 가장 변별적
    list_url=f'{bbs}/board.php?bo_table={bo}'
    for _ in range(3):
        try:
            d.set_page_load_timeout(25)
            d.get(list_url); time.sleep(2)
        except Exception:
            pass
        # 1) wr_id 링크를 훑어 '행 텍스트가 제목의 부분(접두어)'인 것을 찾는다
        try:
            anchors=d.find_elements(By.CSS_SELECTOR, "a[href*='wr_id=']")
        except Exception:
            anchors=[]
        for a in anchors:
            try:
                txt=norm(a.text)
                if len(txt) >= 8 and (txt in key or key in txt):
                    href=a.get_attribute('href') or ''
                    if 'wr_id=' in href:
                        return href
            except Exception:
                continue
        # 2) 목록 전체 텍스트에서 변별적 조각으로 확인(스킨이 제목을 잘라 a.text가 짧을 때)
        try:
            page=norm(d.find_element(By.TAG_NAME,'body').text)
            if frag and frag in page:
                # 가능하면 가장 최근(가장 큰) wr_id 를 붙여 뷰 URL을 만든다
                try:
                    ids=[int(x) for x in re.findall(r'wr_id=(\d+)', d.page_source or '')]
                    if ids:
                        return f'{list_url}&wr_id={max(ids)}'
                except Exception:
                    pass
                return list_url
        except Exception:
            pass
        time.sleep(1.5)
    return None

# ==================== browserless 초고속 발행 (requests, 셀레늄 없이) ====================
# 대표님 지시(2026-09-07): 지오알엔디 등 느린 게시판이 셀레늄 발행(100초+) 때문에
#   관리자서버 Cloudflare 524에 걸린다. 대상 게시판들은 Cloudflare가 없으므로(Apache/nginx)
#   requests로 직접 GET폼→캡차풀이→POST write_update.php 하면 ~2~3초에 발행된다.
#   비회원 글쓰기(로그인 불필요) 그누보드가 대상. 실패 시 셀레늄으로 폴백.
_HTTP_UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

def _solve_kcaptcha_bytes(img_bytes, cfg):
    """kcaptcha 이미지 바이트를 2captcha로 풀어 답(숫자)을 반환. 실패 시 ''."""
    api_key=(cfg.get('twocaptcha_api_key') or '').strip()
    if not api_key or not cfg.get('twocaptcha_enabled'): return ''
    import tempfile
    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        return ''
    tp=None
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg',delete=False) as f:
            f.write(img_bytes); tp=f.name
        res=TwoCaptcha(api_key).normal(tp)
        ans=(res.get('code') if isinstance(res,dict) else str(res)) or ''
        # 2captcha는 답을 반환하면(맞든 틀리든) 과금되므로 비용은 여기서 기록한다.
        if ans: _record_captcha_usage('kcaptcha',True,cfg)
        return ans
    except Exception:
        return ''
    finally:
        if tp:
            try: os.unlink(tp)
            except Exception: pass

def gnuboard_post_http(site, title, content_html):
    """requests 기반 초고속 그누보드 발행(비회원 글쓰기). 성공: (True, 글URL).
       불가/폴백 필요: (None, 사유) → 호출측이 셀레늄으로 폴백. 실패: (False, 사유)."""
    import requests as _rq
    try:
        import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception: pass
    cfg=load_config()
    url=site.get('site_url','').rstrip('/')
    m=re.match(r'(https?://[^/]+)',url); base=m.group(1) if m else url
    bbs=base+'/bbs'
    bo=site.get('bo_table','free')
    # 로그인 필요 사이트(mb_id 저장됨)는 requests 로그인까지 필요 → 일단 셀레늄 폴백.
    if str(site.get('mb_id') or '').strip():
        return None,'로그인 사이트 — 셀레늄 폴백'
    s=_rq.Session(); s.headers.update({'User-Agent':_HTTP_UA})
    try:
        r=s.get(f'{bbs}/write.php',params={'bo_table':bo},timeout=15,verify=False)
    except Exception as e:
        return None,f'폼 GET 실패({str(e)[:40]}) — 폴백'
    if r.status_code>=400:
        return None,f'폼 GET {r.status_code} — 폴백'
    html=r.text or ''
    low=html.lower()
    # 로그인 게이트/회원전용이면 폴백(비회원 글쓰기 폼이 아님)
    if ('mb_password' in low and 'login' in low and 'wr_subject' not in low) or ('name="wr_subject"' not in low):
        return None,'비회원 글쓰기 폼 아님 — 폴백'
    # 폼의 write_update 액션 확인
    if 'write_update.php' not in low:
        return None,'write_update 액션 없음 — 폴백'
    # ── 폼 필드 수집 ──
    def _hidden(name,default=''):
        mm=re.search(r'<input[^>]*name=["\']'+re.escape(name)+r'["\'][^>]*value=["\']([^"\']*)["\']',html,re.I)
        if mm: return mm.group(1)
        mm=re.search(r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']'+re.escape(name)+r'["\']',html,re.I)
        return mm.group(1) if mm else default
    data={
        'uid':_hidden('uid'), 'w':_hidden('w',''), 'bo_table':bo, 'wr_id':_hidden('wr_id','0'),
        'sca':'','sfl':'','stx':'','spt':'','sst':'','sod':'','page':'',
        'wr_name':(str(site.get('writer_name') or '').strip() or (cfg.get('brand') or '게시자').strip()),
        'wr_password':_post_password(cfg),   # ★고정 비번(비밀글 복구용, 랜덤 금지 — 대표님 지시)
        'wr_email':_brand_email(cfg,site),
        'wr_homepage':(cfg.get('landing_url') or '').strip(),
        'wr_subject':_strip_non_bmp(title),
        'wr_content':_strip_non_bmp(content_html),
    }
    # w_time(스팸방지 타임스탬프)이 폼에 있으면 그대로 전달
    wt=_hidden('w_time','');
    if wt: data['w_time']=wt
    # html 렌더 필드: 값이 이미 html*면 그대로, 아니면 html1(HTML 렌더)
    hv=_hidden('html','')
    data['html']= hv if re.match(r'^html',hv or '',re.I) else 'html1'
    # 링크 필드(있으면 영상/랜딩)
    if re.search(r'name=["\']wr_link1["\']',html,re.I):
        data['wr_link1']=(cfg.get('video_url') or cfg.get('landing_url') or '').strip()
    # ── 캡차 ──
    capm=re.search(r'g5_captcha_url\s*=\s*["\']([^"\']+)["\']',html)
    needs_cap=bool(capm) or ('captcha_key' in low) or ('kcaptcha' in low)
    cap_base=(capm.group(1) if capm else (base+'/plugin/kcaptcha')) if needs_cap else ''
    def _fetch_captcha_answer():
        """세션에 정답 심기 → 이미지 GET → 2captcha 풀이. (답, 사유). 실패시 ('',사유)."""
        try:
            s.post(cap_base+'/kcaptcha_session.php',timeout=12,verify=False)
            ci=s.get(cap_base+f'/kcaptcha_image.php?t={int(time.time()*1000)}',timeout=12,verify=False)
            if not (ci.status_code<400 and ci.content and len(ci.content)>200):
                return '','캡차 이미지 수신 실패'
            ans=_solve_kcaptcha_bytes(ci.content,cfg)
            return (ans,'') if ans else ('','2captcha 풀이 실패')
        except Exception as e:
            return '',f'캡차 처리 오류({str(e)[:30]})'
    # ── 캡차 오답 시 새 이미지로 재시도(최대 3회) — 한 번 OCR 오답으로 글 날리지 않게(리뷰 지시) ──
    CAP_TRIES=3 if needs_cap else 1
    last_reason='HTTP 발행 확인 불가'
    for attempt in range(CAP_TRIES):
        if needs_cap:
            ans,why=_fetch_captcha_answer()
            if not ans:
                last_reason=why
                if attempt+1<CAP_TRIES: time.sleep(1); continue
                return (None,why+' — 폴백') if '이미지' in why or '오류' in why else (False,why)
            data['captcha_key']=ans
            # uid/w_time이 회전하는 스킨 대비: 캡차 재시도마다 폼을 다시 읽어 최신 토큰 반영
            if attempt>0:
                try:
                    rr=s.get(f'{bbs}/write.php',params={'bo_table':bo},timeout=12,verify=False)
                    h2=rr.text or ''
                    for fld in ('uid','w_time','w','wr_id'):
                        m2=re.search(r'<input[^>]*name=["\']'+fld+r'["\'][^>]*value=["\']([^"\']*)["\']',h2,re.I)
                        if m2: data[fld]=m2.group(1)
                except Exception: pass
        # ── 제출 ──
        try:
            pr=s.post(f'{bbs}/write_update.php',data=data,timeout=20,verify=False,
                      headers={'Referer':f'{bbs}/write.php?bo_table={bo}'},allow_redirects=True)
        except Exception as e:
            return None,f'POST 실패({str(e)[:40]}) — 폴백'
        fin=pr.url or ''; body=pr.text or ''
        # ── 성공 판정 (리뷰 반영: 오탐·중복발행 방지) ──
        # (1) 최종 리다이렉트 URL에 wr_id가 있으면 확실한 성공(board.php 없어도 됨).
        mfin=re.search(r'[?&]wr_id=(\d+)',fin)
        if mfin and 'write_update' not in fin:
            return True,fin
        # (2) write_update가 성공 시 뿌리는 리다이렉트 타깃(location.replace/href, meta refresh)에서만
        #     wr_id를 뽑는다. 페이지 아무 곳의 board.php?wr_id= (목록·최근글 링크)는 다른 글이라 오탐 → 제외.
        mgo=re.search(r'''(?:location\.(?:replace|href)\s*=?\s*|url=)['"]([^'"]*[?&]wr_id=\d+[^'"]*)['"]''',body,re.I)
        if mgo:
            gu=mgo.group(1).replace('&amp;','&')
            if not gu.startswith('http'): gu=urllib.parse.urljoin(f'{bbs}/',gu)
            return True,gu
        # 알림·에러 문구
        alerts=re.findall(r"alert\(['\"]([^'\"]+)['\"]\)",body)
        blob=' '.join(alerts)+' '+re.sub(r'<[^>]+>',' ',body)[:1500]
        # (3) 승인제 게시판: 승인대기/등록완료 문구는 '이미 등록됨'이므로 성공 처리(재시도 중복발행 방지).
        if any(k in blob for k in ['승인 대기','승인대기','관리자 확인','등록되었습니다','등록 완료','작성되었습니다','작성 완료']):
            return True,f'{base}/bbs/board.php?bo_table={bo}'
        cap_miss=any(k in blob for k in ['자동등록방지 숫자가 일치','숫자가 일치하지','보안문자가 일치','자동등록방지 숫자를 다시','입력 글자가 틀'])
        if cap_miss:
            last_reason='캡차 불일치(2captcha 오답)'
            if attempt+1<CAP_TRIES: time.sleep(1); continue   # 새 캡차로 재시도
            # HTTP 캡차 N회 실패 → 셀레늄 폴백(자체 캡차·학습 경로가 뚫을 수 있음). 아직 글 없음=중복 위험 없음.
            return None,last_reason+f' — {CAP_TRIES}회 실패, 셀레늄 폴백'
        if '내용을 입력' in blob: return False,'본문 미입력'
        if '금지단어' in blob: return False,'금지단어 차단'
        if any(k in blob for k in ['권한이 없','로그인','회원만']): return None,'권한/로그인 필요 — 폴백'
        # (4) URL/문구로 판정 불가 — 글이 실제로 올라갔는지 목록에서 확인(중복발행 방지의 핵심).
        #     올라갔으면 True(셀레늄 재발행 안 함), 안 올라갔으면 None(안전 폴백 — 중복 위험 없음).
        landed=_http_verify_title_on_board(s,bbs,bo,title)
        if landed: return True,landed
        return None,'HTTP 발행 확인 불가 — 폴백'
    return None,last_reason+' — 폴백'

def _http_verify_title_on_board(sess, bbs, bo, title):
    """게시판 목록을 requests로 다시 읽어 방금 올린 제목이 실제로 등록됐는지 확인.
       성공하면 그 글 URL, 아니면 ''. (do_post의 중복발행 방지 — 제출성공했는데 확인만 실패한 경우 구제)"""
    def norm(x): return re.sub(r'[^0-9A-Za-z가-힣]','',str(x or '')).lower()
    key=norm(title)
    if len(key)<8: return ''
    try:
        r=sess.get(f'{bbs}/board.php',params={'bo_table':bo},timeout=15,verify=False)
        html=r.text or ''
    except Exception:
        return ''
    # wr_id 링크와 그 앵커 텍스트를 훑어 제목이 포함되는 행 찾기
    for m in re.finditer(r'href=["\']([^"\']*[?&]wr_id=(\d+)[^"\']*)["\'][^>]*>(.*?)</a>',html,re.S|re.I):
        href,wid,anchor=m.group(1),m.group(2),re.sub(r'<[^>]+>','',m.group(3))
        at=norm(anchor)
        if len(at)>=8 and (at in key or key in at):
            u=href.replace('&amp;','&')
            if not u.startswith('http'): u=urllib.parse.urljoin(f'{bbs}/',u)
            return u
    return ''

def gnuboard_post(site, title, content_html, skip_login=False):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    url=site.get('site_url','').rstrip('/')
    # site_url 이 board.php?... 형태일 수 있으므로 도메인 기준 bbs 경로 계산
    m=re.match(r'(https?://[^/]+)',url)
    base=m.group(1) if m else url
    bbs=base+'/bbs'
    bo=site.get('bo_table','m8_qna')
    mid=site.get('mb_id',''); mpw=site.get('mb_pass','')
    d=get_driver()

    # 로그인 (skip_login=True면 가입 직후 세션이 이미 로그인 상태 → 재로그인 건너뜀)
    if mid and not skip_login:
        d.get(f'{bbs}/login.php'); time.sleep(2)
        # 단수 find_element는 칸이 없으면 예외로 전체 발행이 죽는다 → _safe_find로 방어.
        id_ok=False; pw_ok=False
        for el in _safe_find(d,"input[name='mb_id'],input[name='user_id'],input[name='login_id']"):
            try:
                if el.is_displayed(): el.clear(); el.send_keys(mid); id_ok=True; break
            except Exception: pass
        for el in _safe_find(d,"input[name='mb_password'],input[name='user_pw'],input[name='passwd'],input[type='password']"):
            try:
                if el.is_displayed(): el.clear(); el.send_keys(mpw); pw_ok=True; break
            except Exception: pass
        if not (id_ok and pw_ok):
            return False,'로그인 실패 — 로그인 입력칸을 찾지 못함(비표준 로그인폼)'
        # 제출 버튼은 로그인 폼(#login_fs/flogin/action*=login_check) 안의 것만. write.php와 동일하게
        # 헤더 검색폼(fsearchbox)의 검색 버튼이 문서상 먼저라 그냥 submit 셀렉터를 쓰면 잘못 잡힌다.
        login_btn=None
        # 로그인 폼 name이 flogin이 아닌 사이트(foutlogin 등 비표준)도 커버.
        # 로그인 제출 버튼은 action에 login_check가 있는 폼 안의 것을 우선한다.
        for sel in ("form[action*='login_check'] input[type='submit']","form[action*='login_check'] button[type='submit']",
                    "form[action*='login'] input[type='submit']","form[action*='login'] button[type='submit']",
                    "form[name='flogin'] input[type='submit']","form[name='flogin'] button[type='submit']",
                    "form[name*='login'] input[type='submit']","form[name*='login'] button[type='submit']",
                    "#login_fs .btn_submit","form[action*='login_check'] .btn_submit"):
            try:
                for el in d.find_elements(By.CSS_SELECTOR,sel):
                    if el.is_displayed(): login_btn=el; break
            except Exception: pass
            if login_btn: break
        if login_btn:
            try: login_btn.click()
            except Exception:
                try: d.execute_script("var f=document.querySelector(\"form[action*='login_check']\")||document.forms['flogin']||document.querySelector(\"form[name*='login']\");if(f){if(f.requestSubmit)f.requestSubmit();else f.submit();}")
                except Exception: pass
        else:
            # 버튼 아예 못 찾으면 login_check 폼을 직접 제출
            try: d.execute_script("var f=document.querySelector(\"form[action*='login_check']\")||document.forms['flogin']||document.querySelector(\"form[name*='login']\");if(f){if(f.requestSubmit)f.requestSubmit();else f.submit();}")
            except Exception: pass
        time.sleep(2); dismiss_alerts(d)
        # 로그인 실제 성공 확인(로그아웃 링크 등). 실패면 조기 반환해 원인을 명확히.
        try: _b=d.find_element(By.TAG_NAME,'body').text[:2000]
        except Exception: _b=''
        _bsrc=(d.page_source or '').lower()
        if not (('로그아웃' in _b) or ('logout' in _bsrc) or ('mypage' in _bsrc) or ('회원정보' in _b) or ('마이페이지' in _b) or ('bo_table' in _bsrc and 'login' not in d.current_url.lower())):
            return False,'로그인 실패 — 아이디/비번 불일치 또는 승인대기(자동가입 계정 확인 필요)'

    # 글쓰기 페이지
    d.get(f'{bbs}/write.php?bo_table={bo}'); time.sleep(2)

    # 글쓰기가 로그인으로 튕기는 게시판: wr_subject를 찾다가 드라이버가 꼬이기 전에
    # 여기서 깔끔히 중단하고 '로그인 필요'로 반환한다(파이프라인이 자동가입으로 재시도).
    try:
        cur=(d.current_url or '').lower()
    except Exception:
        cur=''
    if ('login.php' in cur or 'login_check' in cur) or (not mid and _page_login_state(d)):
        return False,'로그인이 필요한 게시판입니다 — 비회원 글쓰기 불가'

    # 보안 차단은 즉시 중단한다. CAPTCHA는 내용을 채운 뒤 사람이 입력한다.
    if _page_is_blocked(d):
        return False,'보안 차단 페이지(403 등) — 즉시 중단'
    _cap=detect_captcha(d)

    # 제목 — 일부 스킨은 wr_subject가 hidden 입력(JS 에디터 연동)이라 send_keys가
    # 'invalid element state'로 실패한다. 실패 시 JS로 value를 넣고 input/change 이벤트 발생.
    wait=WebDriverWait(d,8)
    el=wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,"input[name='wr_subject'],input#wr_subject")))
    _robust_fill(d,el,title)
    editor_content,html_mode=editor_content_for_page(d,content_html)

    # 본문 (smarteditor2/iframe/textarea 대응)
    try:
        d.execute_script(f"if(typeof oEditors!=='undefined')oEditors.getById['wr_content'].exec('SET_IR',[arguments[0]])", editor_content)
    except: pass
    try:
        iframe=d.find_element(By.CSS_SELECTOR,"iframe.se2_input_wysiwyg,iframe[id*='editor']")
        d.switch_to.frame(iframe)
        d.execute_script("document.body.innerHTML=arguments[0]",editor_content)
        d.switch_to.default_content()
    except:
        d.switch_to.default_content()
        try:
            ed=d.find_element(By.CSS_SELECTOR,"div[contenteditable='true']")
            d.execute_script("arguments[0].innerHTML=arguments[1]",ed,editor_content)
        except:
            ta=d.find_element(By.CSS_SELECTOR,"textarea[name='wr_content']")
            _robust_fill(d,ta,editor_content)   # hidden textarea면 JS value로 폴백
    # 그누보드는 폼의 html 필드가 HTML모드여야 태그를 렌더한다(아니면 이스케이프→코드 그대로 노출).
    # ★중요: 그누보드5의 html 필드 값은 '1'이 아니라 'html1'(위지윅)·'html2'(HTML) 이다.
    #   기존 값이 이미 'html*'이면 그대로 두고(이미 HTML모드), 비었거나 다른 값일 때만 켠다.
    #   (예전엔 무조건 value='1'로 덮어써서 그누보드가 무효값→일반텍스트로 이스케이프하던 버그 수정.
    #    lamplant youtube 게시판 실측: <input name="html" value="html1"> 를 '1'로 망가뜨리고 있었음)
    if html_mode:
        # 기존 html 필드 값이 이미 'html*'(그누보드 위지윅/HTML 모드)면 건드리지 않는다.
        # 비었거나 다른 값일 때만: 순수 숫자 관례면 '1', 그 외(그누보드)는 'html1'로 켠다.
        _html_flag_js=(
            "var f=document.getElementById('fwrite')||document.forms['fwrite']||"
            "document.querySelector(\"form[action*='write_update']\");"
            "if(f){var h=f.querySelector(\"[name='html']\");"
            "if(h){if(h.type==='checkbox'){h.checked=true;}"
            "else{var v=(h.value||'');if(!(/^html/i).test(v)){h.value=((/^[0-9]*$/).test(v)?'1':'html1');}}}"
            "else{var i=document.createElement('input');i.type='hidden';i.name='html';i.value='html1';f.appendChild(i);}}"
        )
        try: _safe_js(d,_html_flag_js)
        except Exception: pass

    # 본문 HTML에 이미지가 이미 있으면 중복 파일 첨부하지 않는다.
    # 첨부도 작업실 전용 폴더에서만(작업실 이미지 없으면 첨부 안 함).
    if '<img' not in (content_html or '').lower():
        attach_saved_images(d,1,workroom_id=site.get('workroom_id'))
    _,missing=fill_required_post_fields(d,site)
    if missing: return False,'필수항목 설정 필요: '+', '.join(missing[:6])

    captcha_tid=''
    if _cap:
        cfg=load_config()
        # 2captcha 자동 해결 시도
        success,msg,answer,info=solve_captcha_with_2captcha(d,site,_cap,cfg)
        if success:
            from selenium.webdriver.common.by import By
            for sel in ["input[name='captcha_key']","#captcha_key","input[name='wr_key']","input[name*='captcha']","input[id*='captcha']"]:
                try:
                    inp=d.find_element(By.CSS_SELECTOR,sel)
                    if inp and inp.is_displayed():
                        inp.clear(); inp.send_keys(answer); add_log(f'[2captcha] {msg}'); time.sleep(1); break
                except: pass
        else:
            # 2captcha 자동 해결 실패: 무인 자동화라 수동 대기 없이 이 게시판은 건너뛴다.
            add_log(f'[2captcha] 자동 해결 실패: {msg} → 이 게시판 건너뜀(무인)')
            return False,f'캡차 자동 해결 실패({_cap}): {msg}'

    # 등록: native click으로 정상 제출(referer/token 자연스러움) + 짧은 page_load_timeout으로
    #       렌더러 25초 블록 회피 + 상태 접근은 _safe 폴링(제출 후 current_url이 25초 멈추던 문제 해결)
    def _safe(fn,dv=None):
        try: return fn()
        except Exception: return dv
    _safe(lambda: d.execute_script("if(typeof oEditors!=='undefined')try{oEditors.getById['wr_content'].exec('UPDATE_CONTENTS_FIELD',[])}catch(e){}"))
    # ★SE2 동기화 안전망: fwrite_submit이 textarea(#wr_content).value가 비면 '내용을 입력해 주십시오'
    #   알림 후 return false로 제출을 막는다. UPDATE_CONTENTS_FIELD가 iframe→textarea 복사에
    #   실패(에디터 초기화 지연 등)하면 본문이 비어 발행이 조용히 실패(→'등록 확인 불가')한다.
    #   textarea가 비었을(공백만 포함 포함) 때만 본문 HTML을 직접 넣어 이 알림을 방지한다.
    #   (작동 중인 SE2 동기화는 덮어쓰지 않음 — 비었을 때만)
    _safe(lambda: d.execute_script(
        "var ta=document.getElementById('wr_content');"
        "if(ta && !((ta.value||'').trim())){ta.value=arguments[0];"
        "ta.dispatchEvent(new Event('change',{bubbles:true}));}", editor_content))
    # 제출 버튼은 반드시 '글쓰기 폼' 안의 것을 골라야 한다. CSS 셀렉터 그룹은 문서 순서로
    # 첫 매치를 주므로 "input[type=submit]"만 쓰면 헤더 검색폼(fsearchbox)의 검색 버튼이
    # 먼저 잡혀 not-interactable → 제출 실패한다(그누보드 기본 스킨의 대표적 함정).
    def _find_submit_btn():
        # 1) 표준 그누보드5: 글쓰기 폼(#fwrite) 안의 #btn_submit / submit
        for sel in ("#fwrite #btn_submit",
                    "form[name='fwrite'] #btn_submit",
                    "#fwrite input[type='submit']",
                    "#fwrite button[type='submit']",
                    "form[action*='write_update'] input[type='submit']",
                    "form[action*='write_update'] button[type='submit']",
                    "#btn_submit"):
            for el in _safe(lambda: d.find_elements(By.CSS_SELECTOR,sel),[]) or []:
                if _safe(lambda: el.is_displayed(),False):
                    return el
        # 2) 폴백: 검색폼(fsearchbox/search.php)에 속하지 않은 표시된 submit
        for el in _safe(lambda: d.find_elements(By.CSS_SELECTOR,"input[type='submit'],button[type='submit']"),[]) or []:
            if not _safe(lambda: el.is_displayed(),False):
                continue
            in_search=_safe(lambda: d.execute_script(
                "var f=arguments[0].form;return !!(f&&((f.name||'').indexOf('search')>=0||(f.action||'').indexOf('search.php')>=0));",el),False)
            if not in_search:
                return el
        return None
    _safe(lambda: d.set_page_load_timeout(6))
    try:
        btn=_find_submit_btn()
        submitted=False
        if btn:
            submitted=_safe(lambda: (btn.click(),True)[1],False)  # navigation 트리거(6초 상한이라 무한블록 안 됨)
        if not submitted:
            # 클릭이 막히면(not interactable 등) 폼을 직접 제출 — onsubmit(fwrite_submit) 경유로
            # 캡차/금지어 검증까지 정상 수행. requestSubmit이 있으면 그것을(핸들러 실행 보장), 없으면 submit().
            _safe(lambda: d.execute_script("""
                var f=document.getElementById('fwrite')||document.forms['fwrite']
                     ||document.querySelector("form[action*='write_update']");
                if(f){ if(f.requestSubmit){f.requestSubmit();} else { if(typeof fwrite_submit==='function'){if(fwrite_submit(f)===false)return;} f.submit(); } }
            """))
        # 제출 직후 상태 프로브(원인 진단용): 본문 textarea 길이·SE2 상태·알림. 실패 원인 규명에 사용.
        try:
            _tlen=_safe(lambda: d.execute_script("var t=document.getElementById('wr_content');return t?(t.value||'').length:-1"),'?')
            _al=getattr(d,'_last_alerts',[]) or []
            if _al or _tlen in (0,-1,'?'):
                add_log(f"[발행프로브] 본문길이={_tlen} 알림={_al[:2]} {site.get('name') or base[:24]}")
        except Exception: pass
        curl=''; body=''
        deadline=time.time()+14
        while time.time()<deadline:
            _safe(lambda: dismiss_alerts(d))
            curl=_safe(lambda: d.current_url,'') or ''
            # 1) 등록 성공 = 실제 글번호(wr_id>=1)가 있는 뷰로 이동.
            #   ★버그수정(2026-09-08 대표님 제보 "글 안 올라가는데 성공뜸"): 기존엔 'board.php' 문자열만
            #     있어도 성공 처리 → 발행 실패 후 목록(board.php?bo_table=free)이나 wr_id=0으로 튕겨도
            #     '성공'으로 오판, verified_post_url에 wr_id=0 저장(마짱 등 가짜성공). 실제 글번호 필수.
            _m=re.search(r'[?&]wr_id=(\d+)',curl)
            if 'write_update' not in curl and _m and int(_m.group(1))>0:
                finish_captcha_task(captcha_tid,True,curl)
                return True,curl
            # 2) write_update.php에 머물면 에러페이지(캡차불일치·금지단어 등) → 본문 확인
            if 'write_update' in curl:
                body=_safe(lambda: d.find_element(By.TAG_NAME,'body').text[:1500],'') or ''
                if body: break
            time.sleep(0.5)
    finally:
        _safe(lambda: d.set_page_load_timeout(25))
    # 3) URL 로 판정 불가 시 본문 텍스트로 분류
    if not body:
        body=_safe(lambda: d.find_element(By.TAG_NAME,'body').text[:1500],'') or ''
    # 승인제 게시판: 이미 1회 등록되었을 수 있으므로 재시도로 중복 발행되지 않게 성공 처리
    if any(k in body for k in ['승인 대기','승인대기','관리자 확인','등록되었습니다']):
        finish_captcha_task(captcha_tid,True,'등록됨(승인 대기)')
        return True,'등록됨(승인 대기) — 게시판 승인제'
    if any(k in body for k in ['올바른 방법','잘못된 접근','비정상']):
        finish_captcha_task(captcha_tid,False,'제출 거부(referer/token) — 폼 재로드 필요')
        return False,'제출 거부(referer/token 검증 실패)'
    # 캡차 불일치는 명확한 에러문구만으로 판정('자동등록방지'는 write 폼의 캡차 라벨이라 오탐 유발)
    #  ★kcaptcha.js 실제 메시지 '자동등록방지 숫자가 일치하지 않습니다'를 추가(기존 '숫자를 다시'만
    #    잡아 2captcha OCR 오답이 '등록 확인 불가'로 조용히 떨어지던 문제 — 진단 워크플로우 확인).
    if any(k in body for k in ['입력 글자가 틀','횟수가 넘었','자동등록방지 숫자를 다시','자동등록방지 숫자가 일치','숫자가 일치하지','보안문자가 일치']):
        finish_captcha_task(captcha_tid,False,'캡차 불일치')
        return False,'캡차 불일치 — 재시도 필요'
    if any(k in body for k in ['권한이 없','권한 없','로그인이 필요','게시가 금지','차단']):
        return False,'게시 권한 없음/로그인 필요 — 계정·게시판 권한 확인'
    # 3.5) 도배방지/재작성 제한: 알림·본문에서 대기시간(초)을 파싱해 사이트별로 학습하고,
    #      이후 발행 간격을 그 룰에 맞춰 자동 조정한다(under_min_interval이 flood_sec 반영).
    _fa=' '.join(getattr(d,'_last_alerts',[]) or [])
    # ★fwrite_submit이 alert+return false로 막은 '진짜 실패 사유'를 표면화(진단 워크플로우 확인).
    #   지금까진 캡차오답·본문미입력·금지단어가 전부 '등록 확인 불가'로 뭉뚱그려졌다.
    _blob=(_fa+' '+body)
    if any(k in _blob for k in ['자동등록방지 숫자가 일치','숫자가 일치하지','보안문자가 일치하지','자동등록방지 숫자를 다시']):
        finish_captcha_task(captcha_tid,False,'캡차 불일치')
        return False,'캡차 불일치(2captcha 오답) — 재시도 필요'
    if '내용을 입력' in _blob:
        finish_captcha_task(captcha_tid,False,'본문 미입력')
        return False,'본문 미입력 — SE2 에디터 동기화 실패(textarea 비어 제출 차단)'
    if '금지단어' in _blob:
        finish_captcha_task(captcha_tid,False,'금지단어 차단')
        return False,'금지단어 차단 — 게시판 필터에 걸린 단어 포함(제목/본문 조정 필요)'
    if ('글자 이상 쓰' in _blob) or ('글자 이하로 쓰' in _blob) or ('글자 이상' in _blob and '쓰셔야' in _blob):
        finish_captcha_task(captcha_tid,False,'글자수 규칙 위반')
        return False,'본문 글자수 규칙 위반(게시판 최소/최대 길이)'
    _fw=max(_flood_wait_seconds(_fa), _flood_wait_seconds(body))
    if _fw>0:
        try:
            sid=site.get('id')
            if sid and not str(sid).startswith('cand_'):
                _cur=int((_fresh_site(site) or {}).get('flood_sec',0) or 0)
                set_site_flag(sid, flood_sec=max(_cur,_fw), flood_learned_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
            site['flood_sec']=max(int(site.get('flood_sec',0) or 0),_fw)
        except Exception: pass
        finish_captcha_task(captcha_tid,False,f'도배방지 {_fw}초')
        add_log(f'[도배방지 학습] {site.get("name") or base} — {_fw}초 간격 학습(다음부터 자동 대기)')
        return False,f'도배방지 — {_fw}초 후 재시도(간격 학습됨)'
    # 4) 최후 검증: 느린 서버가 제출 후 뷰로 리다이렉트하는 데 시간이 걸려 위 URL/본문
    #    판정을 놓쳤을 수 있다. 게시판 목록을 다시 읽어 방금 올린 제목이 실제로
    #    등록됐는지 확인한다(오탐으로 인한 재시도→중복발행 방지).
    landed=_safe(lambda: _verify_post_by_title(d, bbs, bo, title))
    if landed:
        finish_captcha_task(captcha_tid,True,landed)
        return True,landed
    msg='등록 확인 불가 — 게시판 규칙/에디터 셀렉터/승인대기 확인'
    finish_captcha_task(captcha_tid,False,msg)
    return False,msg

# ==================== Selenium Cafe24 글쓰기 ====================
def _fill_first(d, selectors, value):
    """여러 셀렉터 후보 중 처음 찾은 입력란에 값 입력."""
    from selenium.webdriver.common.by import By
    for sel in selectors:
        try:
            el=d.find_element(By.CSS_SELECTOR,sel); el.clear(); el.send_keys(value); return True
        except Exception: continue
    return False

def _click_first(d, selectors):
    from selenium.webdriver.common.by import By
    for sel in selectors:
        try: d.find_element(By.CSS_SELECTOR,sel).click(); return True
        except Exception: continue
    return False

def cafe24_post(site, title, content_html, skip_login=False):
    """Cafe24 쇼핑몰 게시판 글쓰기. (게시판 구조가 사이트마다 달라 셀렉터 다중 폴백)"""
    from selenium.webdriver.common.by import By
    url=site.get('site_url','').rstrip('/')
    m=re.match(r'(https?://[^/]+)',url); base=m.group(1) if m else url
    bo=str(site.get('bo_table','') or '').strip()
    mid=site.get('mb_id',''); mpw=site.get('mb_pass','')
    # ★Cafe24는 CF+로그인이 잦다. Scraping Browser(원격 크롬)가 설정돼 있으면 그걸로 — CF·캡차·로그인
    #   세션을 Bright Data가 처리(타카고 등). 미설정이면 로컬 크롬으로 폴백(get_driver 내부에서).
    _use_sbr=bool(load_config().get('sbr_enabled'))
    d=get_driver(remote=_use_sbr)
    if _use_sbr: add_log(f'[Cafe24] Scraping Browser(원격 크롬) 사용 — {(site.get("name") or base)[:24]}')
    # ★원격 크롬(Scraping Browser)은 CF를 백그라운드로 푸느라 d.get()이 page_load 완료를 못 받아
    #   renderer timeout(대표님 제보). → 원격일 때 page_load_timeout 짧게(45s) + get 예외무시+window.stop.
    #   CF는 Bright Data가 처리하므로 로드 완료 안 기다리고 폼을 폴링하면 됨.
    def _nav(u, wait_sel=None, wait_sec=30):
        """원격 크롬(pageLoadStrategy=none)은 get이 즉시 반환하므로, wait_sel 요소가
           나타날 때까지 폴링(최대 wait_sec). CF 처리·해외지연 대비. 로컬은 짧게."""
        try: d.get(u)
        except Exception: pass
        if wait_sel:
            _w0=time.time(); _lim=wait_sec if _use_sbr else 8
            while time.time()-_w0<_lim:
                try:
                    if d.find_elements(By.CSS_SELECTOR,wait_sel): break
                except Exception: pass
                time.sleep(0.7)
        else:
            time.sleep(4 if _use_sbr else 1.5)
    if _use_sbr:
        try: d.set_page_load_timeout(45)
        except Exception: pass
    # Cloudflare 'Just a moment' 챌린지 대기(rental-zon 등 CF 뒤 Cafe24 — 실제 크롬이 자동 통과)
    # ★시간최적화(2026-09-08 대표님 지시 '되는 범위서 다 줄여'): 매 루프 page_source(대용량) 읽던 것을
    #   d.title(가벼움)만 먼저 보고, CF 챌린지 제목일 때만 page_source로 확정. 무챌린지(대부분)면
    #   title 한 번 읽고 즉시 반환 → 원격 대용량 페이지에서 page_source 반복읽기(hang 위험) 제거.
    def _wait_cf(sec=15):
        for _ in range(int(sec*2)):
            try:
                t=(d.title or '').lower()
                if not ('just a moment' in t or 'attention required' in t or '잠시' in t):
                    return   # 제목이 CF 챌린지가 아니면 통과(page_source 안 읽음)
                # 제목이 챌린지 의심 → page_source로 확정(이때만 무거운 읽기)
                src=(d.page_source or '').lower()
                if not ('cloudflare' in src or 'cf-chl' in src or 'challenge' in src):
                    return
            except Exception: return
            time.sleep(0.5)
    def _pass_turnstile_if_present():
        """현재 페이지가 Cloudflare Turnstile 챌린지(veritas-hub 등)면 2captcha로 풀고
           브라우저 콜백으로 제출 → 원래 페이지로 복귀 대기. 통과/무챌린지면 True.
           ★시간최적화: URL(가벼움)에 veritas-hub/challenge 있을 때만 page_source(무거움) 읽어 확정.
           대부분은 챌린지 아님 → URL 한 번 읽고 즉시 True(원격 대용량 page_source 반복 제거)."""
        try: cur=(d.current_url or '').lower()
        except Exception: cur=''
        _url_hit=('veritas-hub' in cur or 'challenge' in cur)
        if not _url_hit:
            # URL이 챌린지가 아니면 page_source로 확인(turnstile 위젯이 본문에 임베드된 경우).
            # ★같은 URL에 챌린지가 그려지는 경우(2026-09-11 노드 실측: board/list.html?board_no=6 · title '카페24' · 입력칸 0)
            #   위젯/문구가 JS로 늦게 붙어 한 번 읽으면 놓침 → 최대 4초 폴링, 제목 '카페24'+작은 본문도 챌린지로 간주.
            _found=False
            for _ in range(8):
                try: psrc=(d.page_source or '')
                except Exception: psrc=''
                try: _tt=(d.title or '').strip()
                except Exception: _tt=''
                _pl=psrc.lower()
                if ('turnstile' in _pl or '사람인지' in psrc or '간단한 확인' in psrc
                        or (_tt=='카페24' and len(psrc)<12000)):
                    _found=True; break
                if len(psrc)>20000 and 'cf-' not in _pl: break   # 본문이 큰 정상 페이지 — 챌린지 아님, 더 기다리지 않음
                time.sleep(0.5)
            if not _found:
                return True
        cfg=load_config()
        ok,msg,tok,info=solve_captcha_with_2captcha(d,site,'turnstile',cfg)
        add_log(f'[Turnstile] {msg}')
        if not ok: return False
        for _ in range(40):   # /validate→redirect 복귀 대기(최대 20초)
            try:
                c=(d.current_url or '').lower()
                if 'veritas-hub' not in c and 'challenge' not in c: return True
            except Exception: pass
            time.sleep(0.5)
        return True

    # 로그인 (skip_login=True면 가입 직후 로그인 세션 재사용 → 재로그인 건너뜀)
    if mid and not skip_login:
        # 원격은 로그인폼(member_passwd)이 뜰 때까지 폴링(CF 처리 대기). 로컬은 짧게.
        _nav(base+'/member/login.html', wait_sel="input[name='member_passwd']", wait_sec=40)
        _wait_cf(15); _pass_turnstile_if_present()
        _fill_first(d,["input[name='member_id']","input[name='login_id']","input[name='id']",
                       "#member_id","#loginId","input#id"],mid)
        _fill_first(d,["input[name='member_passwd']","input[name='passwd']","input[name='password']",
                       "input[name='login_password']","#passwd","#loginPasswd","input[type='password']"],mpw)
        # Cafe24 로그인 버튼은 <a data-ez-item="login"> 링크나 fnLogin() JS인 경우가 많다.
        clicked=_click_first(d,["a.btnSubmit","#btnLogin","a.btnLogin",".btnEm","button[type='submit']",
                        "input[type='submit']","a[onclick*='login']","a[data-ez-item='login']",
                        ".ec-base-button a","button.btnSubmit"])
        # ★takago 실측(2026-09-08 브라우저 재현): 로그인버튼 <a data-ez-item=login> 클릭도,
        #   form.requestSubmit()도 제출이 안 됨(로그인 페이지 그대로). Cafe24 정식 로그인 실행함수
        #   window.useLoginKeepingSubmit()를 호출해야 제출됨(호출하니 홈으로 이동=제출 성공 확인).
        #   → 이 함수를 최우선 호출하고, 없으면 fnLogin/폼 requestSubmit 폴백.
        time.sleep(2)
        try:
            if d.find_elements(By.CSS_SELECTOR,"input[name='member_passwd']"):
                d.execute_script("""
                    // 1) Cafe24 정식 로그인 제출 함수(신형 스킨) — 최우선.
                    if(typeof useLoginKeepingSubmit==='function'){ try{ useLoginKeepingSubmit(); return; }catch(e){} }
                    if(typeof fnLogin==='function'){ try{ fnLogin(); return; }catch(e){} }
                    // 2) 폴백: 로그인 폼 직접 제출.
                    var f=document.querySelector("form[action*='Member/login']")||document.querySelector("form[action*='login']")||
                          (document.querySelector("input[name='member_passwd']")||{}).form;
                    if(f){ if(typeof f.requestSubmit==='function')f.requestSubmit(); else f.submit(); }
                """)
        except Exception:
            try:
                from selenium.webdriver.common.keys import Keys
                el=d.find_element(By.CSS_SELECTOR,"input[name='member_passwd']"); el.send_keys(Keys.RETURN)
            except Exception: pass
        # ★로그인 제출 후 Cloudflare Turnstile '사람인지 확인' 챌린지가 뜬다(대표님 개발자도구 실측
        #   2026-09-08: veritas-hub.cafe24.com/challenge + Turnstile 체크박스). 제출→챌린지 이동에
        #   시간차가 있어 한 번만 호출하면 놓친다 → 챌린지가 뜰 때까지·풀릴 때까지 반복 처리.
        _tt0=time.time()
        while time.time()-_tt0<40:
            try: d.execute_script("try{window.stop();}catch(e){}")
            except Exception: pass
            try: cur=(d.current_url or '').lower()
            except Exception: cur=''
            try: psrc=(d.page_source or '').lower()
            except Exception: psrc=''
            on_challenge=('veritas-hub' in cur or 'challenge' in cur or 'turnstile' in psrc or '사람인지' in psrc or '간단한 확인' in psrc)
            if on_challenge:
                _pass_turnstile_if_present()   # 2captcha turnstile 풀어 콜백 제출→복귀
                time.sleep(3); continue
            break   # 챌린지 아님 → 로그인 응답으로 진행
        _wait_cf(10); _pass_turnstile_if_present(); dismiss_alerts(d)
        # 로그인 성공 판정 — ★마이페이지 접근으로 확인(2026-09-08 실측: takago는 봇방어 아님,
        #   대표님이 브라우저로 로그인하면 바로 됨=추가보안창 없음. 워커 실패는 로그인 응답 무한로딩으로
        #   판정을 놓친 것). 로그인폼 유무 판정은 무한로딩·리다이렉트에 취약 → 마이페이지를 직접 열어
        #   로그인폼으로 튕기는지로 확정한다. 로그인됐으면 마이페이지가 열리고, 아니면 로그인폼이 뜬다.
        try:
            if not _use_sbr:   # 원격은 시작부에서 45s로 설정 유지(짧게 덮으면 CF 처리중 타임아웃)
                try: d.set_page_load_timeout(8)
                except Exception: pass
            try: d.get(base+'/myshop/index.html')
            except Exception: pass
            try: d.execute_script("try{window.stop();}catch(e){}")
            except Exception: pass
            _mp0=time.time(); on_login_form=True
            while time.time()-_mp0<(20 if _use_sbr else 12):
                try:
                    on_login_form=bool(d.find_elements(By.CSS_SELECTOR,"input[name='member_passwd']"))
                    if not on_login_form: break
                except Exception: pass
                time.sleep(0.5)
            if not _use_sbr:
                try: d.set_page_load_timeout(25)
                except Exception: pass
            # 마이페이지가 로그인폼으로 안 튕겼으면 로그인 성공.
            _logged_in=not on_login_form
            add_log(f"[Cafe24로그인] {'성공' if _logged_in else '실패'} — {(site.get('name') or base)[:24]}"
                    +('' if _logged_in else ' (아이디/비번 확인 필요)'))
            if not _logged_in:
                # 로그인 실패면 write 진입은 무의미(홈 리다이렉트) — 즉시 명확한 에러 반환.
                return False,'Cafe24 로그인 실패 — 저장된 아이디/비밀번호 확인 필요(재입력 후 재시도)'
        except Exception: pass

    # 글쓰기 페이지 후보 (bo_table 이 숫자면 board_no, 문자면 board 경로)
    # ★rental-zon 등 스킨은 /board/product/write.html?board_no=N 형태 — product 경로도 시도(대표님 실측).
    write_urls=[]; list_urls=[]
    if bo.isdigit():
        write_urls=[base+f'/board/product/write.html?board_no={bo}',
                    base+f'/board/write.html?board_no={bo}', base+f'/board/{bo}/write.html']
        # 목록 경유 폴백용 — write.html 직접접근이 홈으로 리다이렉트되는 스킨(takago 등) 대비.
        list_urls=[base+f'/board/product/list.html?board_no={bo}',
                   base+f'/board/list.html?board_no={bo}']
        # ★타카고 실측(2026-09-11 headful): write.html 직접접근은 404('다시 확인해주세요'). 진짜 글쓰기는
        #   /article/{한글게시판명}/{bo}/ 또는 /board/{한글게시판명}/{bo}/ 목록에서 로그인 후 '글쓰기' 클릭.
        #   게시판명(article_board_name)이 있으면 그 목록을 list_urls 맨 앞에 넣어 클릭경유 우선.
        _abn=str(site.get('article_board_name') or '').strip()
        if _abn:
            list_urls=[base+f'/article/{_abn}/{bo}/', base+f'/board/{_abn}/{bo}/']+list_urls
    elif bo:
        write_urls=[base+f'/board/{bo}/write.html', base+f'/board/product/write.html?board_no=1',
                    base+f'/board/write.html?board_no=1', base+f'/board/write.html?board_no={bo}']
        list_urls=[base+f'/board/{bo}/list.html', base+f'/board/product/list.html?board_no=1']
    else:
        write_urls=[base+'/board/product/write.html?board_no=1',
                    base+'/board/write.html?board_no=1', base+'/board/free/write.html']
        list_urls=[base+'/board/product/list.html?board_no=1']
    # ★대표님 지시(2026-09-08): '메인도메인만 저장 말고 진짜 글 쓸 수 있는 링크까지 저장'.
    #   지난 발행/검증에서 진입 성공한 경로(write_entry_url)가 저장돼 있으면 그걸 최우선으로 쓴다
    #   (매번 추측해 홈으로 튕기던 문제 해결). Cafe24 /article/게시판명/board_no/ SEO-URL(글목록)에는
    #   로그인 시 '글쓰기' 버튼이 있어 세션 유지된 채 진입된다(대표님 발견 /article/상품-qa/6/...).
    _saved_entry=str(site.get('write_entry_url') or '').strip()
    if _saved_entry.startswith('http'):
        if 'write' in _saved_entry.lower():
            write_urls.insert(0,_saved_entry)      # 저장된 게 write.html이면 write 최우선
        else:
            list_urls.insert(0,_saved_entry)       # 저장된 게 목록/글 페이지면 클릭경유 최우선
    # /article/게시판명/board_no/ 형태 글목록도 클릭경유 후보로(로그인 세션에서 글쓰기 버튼 노출).
    _art_name=str(site.get('article_board_name') or '').strip()
    if _art_name and bo.isdigit():
        list_urls.insert(0, base+f'/article/{_art_name}/{bo}/')
    # ★rental-zon 등 Cafe24 스킨의 write.html은 광고/로그위젯 iframe으로 페이지 'load'
    #   이벤트가 늦거나 안 온다. eager로도 d.get()이 page_load_timeout(25초) 블록되다
    #   예외 → write_urls 3개 × 25초 헛돌다 실패했다(rental-zon fail_streak).
    #   실측(2026-09-07 Chrome): subject·textarea[content]·SmartEditor(iframe#content_IFRAME)는
    #   JS로 그려지며 ~8초면 DOM에 나타나고 readyState=complete에도 도달한다.
    #   → get 타임아웃만 짧게(8초) 두고, 로딩을 성급히 끊지 말고 subject를 폴링한다.
    #     (window.stop을 get 직후 걸면 폼 렌더가 중단돼 오히려 실패 — 폴링 소진 후에만 최후로 시도)
    _SUBJ_SEL="input[name='subject'],#subject,input[name='title'],input[name='board_subject']"
    def _form_ready():
        try: return bool(d.find_elements(By.CSS_SELECTOR,_SUBJ_SEL))
        except Exception: return False
    opened=False
    # ★전체 글쓰기 진입에 시간상한(대표님 지시 '되는 범위서 다 줄여' + hang 방지). 원격 90s·로컬 60s.
    #   이 시간 넘으면 후보 순회 중단하고 실패 반환 → 한 단계가 세션지연으로 10분 멈추던 것 방지.
    _entry_deadline=time.time()+(90 if _use_sbr else 60)
    if not _use_sbr:   # 원격(Scraping Browser)은 시작부 45s 유지 — 짧게 덮으면 CF 처리중 타임아웃
        try: d.set_page_load_timeout(8)   # get()이 오래 블록되지 않게 (finally에서 25로 복원)
        except Exception: pass
    def _settle_nav(sec=8):
        """★원격(pageLoadStrategy=none)은 d.get()이 about:blank로 즉시 반환 → current_url을
           너무 일찍 읽으면 about:blank라 오판(리다이렉트 skip 오작동). 실제 네비게이션이
           about:blank를 벗어날 때까지 짧게 폴링. 로컬은 거의 즉시라 짧게."""
        _s0=time.time(); _lim=sec if _use_sbr else 3
        while time.time()-_s0<_lim:
            try:
                c=(d.current_url or '')
                if c and c not in ('about:blank','data:,'): return c
            except Exception: pass
            time.sleep(0.4)
        try: return (d.current_url or '')
        except Exception: return ''
    def _scrape_article_path():
        """로그인 세션 홈에서 게시판 글목록 링크(/article/{명}/{bo}/ 또는 /board/{명}/{bo}/)를 찾아
           list_urls 최우선에 추가하고 article_board_name 저장.
           ★타카고 실측(2026-09-11): 메뉴의 '상품 Q&A → /board/상품-qa/6/'가 진짜 글목록(대표님 확인).
           /article/·/board/ 둘 다 감지. page_source 대신 JS로 링크만 추출(가벼움). 반환: 게시판명 or None."""
        try:
            d.get(base+'/'); _settle_nav()
            _hrefs=d.execute_script(
                "return Array.from(document.querySelectorAll(\"a[href*='/article/'],a[href*='/board/']\")).map(a=>a.getAttribute('href')).slice(0,400);"
            ) or []
        except Exception: _hrefs=[]
        for _h in _hrefs:
            # /article/{명}/{bo}/  또는  /board/{명}/{bo}/  (명은 write/list/read/product 같은 예약어 제외)
            _am=re.search(r'/(?:article|board)/([^/"\']+)/'+re.escape(bo)+r'/', str(_h or ''))
            if _am and _am.group(1).lower() not in ('write','list','read','view','product','free'):
                _an=_am.group(1)
                # 타카고식: /board/{명}/{bo}/ 와 /article/{명}/{bo}/ 둘 다 후보로(어느쪽이든 글쓰기 버튼 노출).
                for _u in (base+f'/board/{_an}/{bo}/', base+f'/article/{_an}/{bo}/'):
                    if _u not in list_urls: list_urls.insert(0,_u)
                try: set_site_flag(site.get('id'),article_board_name=_an); site['article_board_name']=_an
                except Exception: pass
                add_log(f"[Cafe24경로탐지] 홈에서 글목록 발견 — /board/{_an}/{bo}/")
                return _an
        return None
    try:
        # ★board_no 숫자면, 대표님 발견 /article/ 경유가 write.html 직접보다 안정적 →
        #   먼저 홈에서 /article/ 경로를 찾아 list_urls 최우선에 넣는다(저장된 게 없을 때 1회).
        #   (SBR 게이트 제거 2026-09-09: Bright Data 계정정지로 PC 로컬크롬 발행 시에도 이 탐지가 필요.
        #    로컬크롬은 실제 IP라 CF는 넘지만 /article/ 탐지가 안 돌아 '글쓰기 페이지 못찾음' 났음.)
        if bo.isdigit() and not _art_name:
            _art_name=_scrape_article_path() or _art_name
        for wu in write_urls:
            if opened or time.time()>_entry_deadline: break
            try: d.get(wu)
            except Exception: pass          # 로드 미완료(load 이벤트 지연) 타임아웃은 정상 — 폼 폴링으로 판정
            _cur=_settle_nav()              # ★about:blank 벗어날 때까지 대기 후 실제 URL 판정
            # 첫 진입 시 Turnstile/CF 챌린지면 통과 후 복귀
            _wait_cf(12); _pass_turnstile_if_present()
            dismiss_alerts(d)
            # ★진단(2026-09-08): 어느 write_url에서 어디로 갔는지 로그 — 홈 리다이렉트/미렌더 구분용.
            try: _cur=(d.current_url or _cur)
            except Exception: pass
            add_log(f"[Cafe24글쓰기시도] {wu.split('/board/')[-1][:40]} → 현재:{_cur.split('//')[-1][:50]}")
            # ★홈으로 리다이렉트됐으면(경로에 board/write/article 없음) 폼 폴링 낭비 말고 즉시 다음 후보로.
            #   (takago 등은 write.html 직접 get이 홈으로 튕김 — 28초 폴링 소모 방지, article경유에 시간 확보.)
            _cl=_cur.lower()
            if _cur and _cur not in ('about:blank','data:,') and not any(k in _cl for k in ('/board/','write','/article/','board_no','bo_table')):
                continue
            # ★타카고 404 오류페이지 즉시 스킵(2026-09-11 실측): write.html 직접접근이 '다시 한번 확인해주세요'
            #   (사라졌거나 다른 페이지) 오류를 띄운다. URL은 그대로라 위 리다이렉트 체크에 안 걸림 → 제목/본문으로 감지.
            try:
                _t=(d.title or ''); _ps=(d.page_source or '')[:3000]
                if ('다시 한번 확인' in _ps or '사라졌거나 다른 페이지' in _ps or '페이지를 찾을 수 없' in _ps
                        or '주소를 다시 확인' in _ps):
                    add_log(f"[Cafe24글쓰기시도] {wu.split('/board/')[-1][:40]} → 404 오류페이지, 다음 후보로")
                    continue
            except Exception: pass
            # subject 입력칸이 나타날 때까지 폴링(폼은 JS로 그려짐 — 로딩 끊지 않음). 원격은 넉넉히.
            #   단 전체 시간상한(_entry_deadline)을 넘지 않게 캡.
            deadline=min(time.time()+(28 if _use_sbr else 18), _entry_deadline)
            while time.time()<deadline:
                if _form_ready(): opened=True; break
                time.sleep(0.5)
            if not opened:
                # 여기까지 안 뜨면 진짜 무한로딩 의심 — 한 번 stop 후 마지막 확인
                try: d.execute_script("try{window.stop();}catch(e){}")
                except Exception: pass
                time.sleep(1)
                if _form_ready(): opened=True
            if opened: break
        # ★목록/글 경유 폴백 — write.html 직접접근이 홈으로 리다이렉트되는 스킨(takago 등) 대비.
        #   목록의 '글쓰기' 링크 클릭은 세션/리퍼러 유지돼 진입됨. /article/ 경로는 위에서 이미 탐지.
        #   (SBR는 진입 전 _scrape_article_path 실행됨. 로컬인데 아직 안 했으면 여기서 1회.)
        if not opened and not _use_sbr and bo.isdigit() and not _art_name:
            _art_name=_scrape_article_path() or _art_name
        if not opened:
            for lu in list_urls:
                if time.time()>_entry_deadline: break
                try: d.get(lu)
                except Exception: pass
                _lc=_settle_nav()   # ★about:blank 벗어날 때까지 대기(원격 조기판정 방지)
                _wait_cf(12); _pass_turnstile_if_present(); dismiss_alerts(d)
                try: _lc=(d.current_url or _lc)
                except Exception: pass
                # 목록/글 페이지의 '글쓰기' 링크 클릭(직접 get이 아니라 클릭이라 세션/리퍼러 유지).
                #   href[write] 뿐 아니라 Cafe24 스킨의 글쓰기 버튼(board_write·onclick·텍스트)도 폭넓게.
                try:
                    wb=None
                    _links=d.find_elements(By.CSS_SELECTOR,
                        "a[href*='write.html'],a[href*='/write'],a[href*='board_write'],"
                        "a[onclick*='write'],a[class*='write'],a[data-ez-item*='write']")
                    if not _links:   # 텍스트 '글쓰기'/'작성' 앵커·버튼 폴백(href 없는 onclick 버튼 대비)
                        for a in d.find_elements(By.CSS_SELECTOR,"a,button"):
                            try:
                                if a.is_displayed() and any(t in (a.text or '') for t in ('글쓰기','글 쓰기','작성하기','새 글')):
                                    _links=[a]; break
                            except Exception: pass
                    add_log(f"[Cafe24목록경유] 목록:{_lc.split('//')[-1][:44]} write링크 {len(_links)}개")
                    for a in _links:
                        try:
                            if a.is_displayed(): wb=a; break
                        except Exception: pass
                    if not wb: continue
                    d.execute_script("arguments[0].click();",wb)
                except Exception:
                    continue
                _wait_cf(12); _pass_turnstile_if_present(); dismiss_alerts(d)
                deadline=min(time.time()+(28 if _use_sbr else 18), _entry_deadline)
                while time.time()<deadline:
                    if _form_ready(): opened=True; break
                    time.sleep(0.5)
                if opened:
                    add_log(f"[Cafe24글쓰기폼] 목록경유 진입 성공 — {(site.get('name') or base)[:24]}")
                    break
    finally:
        if not _use_sbr:
            try: d.set_page_load_timeout(25)
            except Exception: pass
    if not opened:
        # ★진단(2026-09-08): 폼 못찾을 때 현재 페이지 URL·제목·입력필드명을 덤프 → 실제 셀렉터 파악.
        #   (subject 셀렉터가 이 스킨과 안 맞는지, write.html이 폼을 안 그리는지 구분.)
        try:
            _du=d.execute_script("""
              var q=function(s){return Array.from(document.querySelectorAll(s)).map(function(e){
                  return (e.name||e.id||e.getAttribute('data-name')||'').slice(0,24);}).filter(Boolean);};
              return {url:location.href,title:document.title.slice(0,50),
                      inputs:q('input').slice(0,20),textareas:q('textarea').slice(0,6),
                      iframes:q('iframe').slice(0,6),forms:q('form').slice(0,6)};
            """) or {}
            add_log(f"[Cafe24폼진단] url={str(_du.get('url',''))[:60]} title={_du.get('title','')}")
            add_log(f"[Cafe24폼진단] inputs={_du.get('inputs')} textarea={_du.get('textareas')} iframe={_du.get('iframes')}")
        except Exception as _e:
            add_log(f"[Cafe24폼진단] 덤프실패 {str(_e)[:60]}")
        return False,'Cafe24 글쓰기 페이지 못찾음 — Turnstile/로그인/게시판번호 확인'
    add_log(f"[Cafe24글쓰기폼] 진입 성공 — {(site.get('name') or base)[:24]}")
    # ★대표님 지시(2026-09-08): 진짜 글쓰기 진입 성공한 경로를 사이트에 저장 → 다음부터 최우선 재사용
    #   (매번 추측해 홈으로 튕기던 문제 해결). 현재 write 폼 URL을 write_entry_url로 저장.
    try:
        _entry=(d.current_url or '')
        if _entry.startswith('http') and _entry!=str(site.get('write_entry_url') or ''):
            _flds={'write_entry_url':_entry}
            _am=re.search(r'/article/([^/]+)/(\d+)/',_entry)   # /article/게시판명/board_no/ 면 게시판명도 저장
            if _am: _flds['article_board_name']=_am.group(1)
            set_site_flag(site.get('id'),**_flds); site.update(_flds)
            add_log(f"[Cafe24경로저장] 글쓰기 진입경로 저장 — {_entry.split('//')[-1][:50]}")
    except Exception: pass

    # CF 챌린지가 이미 통과됐으므로, 그래도 남은 진짜 차단(403 등)만 중단
    if _page_is_blocked(d): return False,'보안 차단 페이지(403 등) — 즉시 중단'
    _cap=detect_captcha(d)
    add_log(f"[Cafe24캡차] 감지={_cap or '없음'}")
    if _cap:
        cfg=load_config()
        # 2captcha 자동 해결 시도
        success,msg,answer,info=solve_captcha_with_2captcha(d,site,_cap,cfg)
        if success:
            from selenium.webdriver.common.by import By
            _entered=False
            # Cafe24 캡차 입력칸 폭넓게(캡차 img가 captcha_img면 input은 captcha_key인 경우 多)
            for sel in ["#captcha_key","input[name='captcha_key']","input[name='secText']",
                        "input[name='captcha']","input[name='captchaText']","input[name='wr_key']",
                        "input[name*='captcha']","input[id*='captcha']","input[name*='secText']",
                        "input[name*='security']","input[name*='보안']"]:
                try:
                    for inp in d.find_elements(By.CSS_SELECTOR,sel):
                        if inp.is_displayed():
                            inp.clear(); inp.send_keys(answer); _entered=True; break
                    if _entered: break
                except: pass
            # 셀렉터로 못 넣었으면 JS로 캡차성 input 전부 채움(마지막 안전망)
            if not _entered:
                try:
                    _entered=bool(d.execute_script("""
                        var v=arguments[0], done=false;
                        document.querySelectorAll("input[type='text'],input:not([type])").forEach(function(el){
                            var n=((el.name||'')+' '+(el.id||'')+' '+(el.placeholder||'')).toLowerCase();
                            if(/captcha|보안|자동등록|sectext|security/.test(n) && el.offsetParent!==null){
                                el.value=v; el.dispatchEvent(new Event('input',{bubbles:true})); done=true;
                            }
                        });
                        return done;
                    """, answer))
                except Exception: pass
            add_log(f'[2captcha] {msg} · 캡차입력={"성공" if _entered else "칸못찾음"}')
            if not _entered:
                return False,'캡차 해결됐으나 입력칸을 못 찾음 — Cafe24 캡차 input 확인'
            time.sleep(1)
        else:
            # 2captcha 자동 해결 실패 시 사용자 에러 반환
            add_log(f'[2captcha] 자동 해결 실패: {msg} → Cafe24 자동발행 불가')
            return False,f'캡차 감지({_cap}) — 2captcha 자동 해결 실패: {msg}'

    # 제목
    _fill_first(d,["input[name='subject']","#subject","input[name='title']"],title)
    editor_content,html_mode=editor_content_for_page(d,content_html)

    # 본문 (Cafe24 SmartEditor iframe / CKEditor / textarea)
    filled=False
    try:
        iframe=d.find_element(By.CSS_SELECTOR,"iframe[id*='content'],iframe.cke_wysiwyg_frame,iframe[title*='Rich'],iframe[title*='편집']")
        d.switch_to.frame(iframe)
        d.execute_script("document.body.innerHTML=arguments[0]",editor_content)
        d.switch_to.default_content(); filled=True
    except Exception:
        d.switch_to.default_content()
    if not filled:
        from selenium.webdriver.common.by import By as _By
        for sel in ["textarea[name='content']","textarea#content","textarea[name='contents']",
                    "div[contenteditable='true']","textarea[name='board_content']"]:
            try:
                el=d.find_element(_By.CSS_SELECTOR,sel)
                if sel.startswith('div'):
                    d.execute_script("arguments[0].innerHTML=arguments[1]",el,editor_content)
                else:
                    el.clear(); el.send_keys(editor_content)
                filled=True; break
            except Exception: continue
    if not filled:
        return False,'Cafe24 본문 입력란 못찾음 — 에디터 셀렉터 확인'

    if '<img' not in (content_html or '').lower(): attach_saved_images(d,1)
    _,missing=fill_required_post_fields(d,site)
    if missing: return False,'필수항목 설정 필요: '+', '.join(missing[:6])

    # 등록 — Cafe24 등록 버튼(a.btnSubmit 등)·글쓰기 폼 제출. board.write.php/exec 액션 폼 우선.
    _sub=_click_first(d,["a.btnSubmit","#btnSubmit","button.btnSubmit","a.btnEm.btnStrong",
                           ".ec-base-button a.btnStrong","a[href*='javascript'][class*='Submit']",
                           "input[type='submit']","button[type='submit']","a[onclick*='submit']"])
    if not _sub:
        try:
            d.execute_script("""
                var f=document.querySelector("form[action*='write'],form[action*='board'],form[name='boardWriteForm']")
                     ||(document.querySelector("input[name='subject'],#subject")||{}).form
                     ||document.querySelector('form');
                if(f){ if(typeof f.requestSubmit==='function')f.requestSubmit(); else f.submit(); }
            """)
        except Exception: pass
    # ★제출 후 리다이렉트 대기(대표님 실측 2026-09-11): 실제로는 /article/{명}/{bo}/{글번호}/로 이동해
    #   등록되는데(글 106072 확인), sleep(3)이 짧아 아직 write 페이지일 때 확인해 '확인불가' 오탐 났다.
    #   write.html을 벗어나 글목록/상세(article·read·view)로 갈 때까지 최대 15초 폴링.
    dismiss_alerts(d)
    curl=''
    for _ in range(30):
        try: curl=d.current_url or ''
        except Exception: curl=''
        _cl=curl.lower()
        if curl and 'write' not in _cl and any(k in _cl for k in ['/article/','read.html','view.html','list.html','board_no']):
            break
        time.sleep(0.5)
    dismiss_alerts(d)
    # 알림 문구도 수집(제출 막혔을 때 원인)
    _al=' '.join(getattr(d,'_last_alerts',[]) or [])
    if any(k in curl for k in ['read.html','list.html','article','board_no','view.html']) and 'write.html' not in curl and 'write' not in curl.split('?')[0].split('/')[-1]:
        add_log(f'[Cafe24등록] 성공 → {curl[:60]}')
        return True,(curl or '등록 완료')
    try: body=d.find_element(By.TAG_NAME,'body').text[:1500]
    except Exception: body=''
    blob=_al+' '+body
    if any(k in blob for k in ['등록되었습니다','등록 완료','작성되었습니다','정상적으로 등록']):
        add_log('[Cafe24등록] 성공(문구 확인)')
        return True,'등록됨'
    if any(k in blob for k in ['승인 대기','승인대기','관리자 확인']):
        return True,'등록됨(승인 대기)'
    # ★목록 재조회로 등록 확정(대표님 실측 2026-09-11): takago는 제출 후 URL이 write에 머물고(AJAX 등록)
    #   페이지 이동이 없어 위 판정들이 다 실패했지만, 글은 실제로 등록됨(106072 확인). → 글목록을 다시 열어
    #   방금 쓴 제목이 목록에 있으면 성공으로 확정하고 그 글의 /article/ URL을 결과로 반환.
    if 'write' in (curl.split('?')[0].split('/')[-1] or ''):   # 아직 write 페이지에 머물면
        try:
            _abn=str(site.get('article_board_name') or '').strip()
            _list=(base+f'/board/{_abn}/{bo}/') if (_abn and bo) else (base+f'/board/list.html?board_no={bo}' if bo else '')
            if _list:
                d.get(_list); time.sleep(2); dismiss_alerts(d)
                # 제목 핵심 조각(앞 12자)이 목록에 보이면 등록된 것. 글의 /article/ 상세링크도 함께 회수.
                _needle=re.sub(r'\s+',' ',(title or '')).strip()[:12]
                # ★가짜 성공 제거(2026-09-11 실측): 예전엔 제목이 목록에 없으면 '목록 최상단 글'을 우리 글로 반환해 takago에서
                #   남의 스팸글(106090)을 9번이나 '발행성공'으로 기록했음. 반드시 우리 제목이 목록에 있을 때만 성공.
                _hit=d.execute_script("""
                    var needle=arguments[0]; var norm=function(s){return (s||'').replace(/\\s+/g,' ').trim();};
                    var as=Array.from(document.querySelectorAll("a[href*='/article/']"));
                    for(var i=0;i<as.length;i++){ if(norm(as[i].textContent).indexOf(needle)>=0){ return as[i].href; } }
                    return norm(document.body.innerText).indexOf(needle)>=0 ? 'FOUND' : '';
                """, _needle) if _needle else ''
                if _hit and _hit.startswith('http'):
                    add_log(f'[Cafe24등록] 성공(목록에서 제목 확인) → {_hit[:60]}')
                    return True,_hit
                if _hit=='FOUND':
                    add_log('[Cafe24등록] 성공(목록에 제목 확인)')
                    return True,_list
                add_log(f'[Cafe24등록] 미등록 — 제출 후 목록에 제목 없음({_needle}) · 승인대기/스팸필터/제출실패 의심')
                return False,'Cafe24 등록 확인 불가 — 제출 후 목록에 글 없음(승인대기·스팸필터·제출실패)'
        except Exception as _e:
            add_log(f'[Cafe24등록] 목록재조회 실패 {str(_e)[:50]}')
    # 실패 원인 로그(제출 후 어디에 있는지·알림)
    add_log(f'[Cafe24등록] 확인불가 — url={curl[:40]} 알림={_al[:40]}')
    if any(k in blob for k in ['자동등록방지','보안문자','캡차','captcha','일치하지']):
        return False,'캡차 불일치 — 재시도 필요'
    if any(k in blob for k in ['로그인','권한이 없','권한 없','금지','차단','스팸']):
        return False,'Cafe24 게시 권한 없음/로그인 필요 — 계정·게시판 권한 확인'
    if any(k in blob for k in ['필수','입력해','선택해']):
        return False,f'Cafe24 필수항목 미입력 — {_al[:40] or body[:40]}'
    return False,'Cafe24 등록 확인 불가 — 게시판 설정/에디터 셀렉터 확인'

# ==================== 플랫폼 자동 감지 + 발행 디스패처 ====================
_plat_cache={}
def detect_platform(url, use_cache=True):
    """사이트 URL 로 그누보드/Cafe24/KBoard 자동 판별."""
    import requests as _rq
    m=re.match(r'(https?://[^/]+)',(url or '').rstrip('/')); base=m.group(1) if m else (url or '')
    if not base: return 'gnuboard'
    if use_cache and base in _plat_cache: return _plat_cache[base]
    UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
    def probe(path,needles):
        try:
            r=_rq.get(base+path,timeout=10,verify=False,headers=UA,allow_redirects=True)
            if r.status_code>=400: return False
            t=(r.text or '').lower()
            return any(n in t for n in needles)
        except Exception: return False
    # 등록 URL 자체에서 WordPress KBoard 신호를 먼저 확인한다.
    try:
        rr=_rq.get(url,timeout=10,verify=False,headers=UA,allow_redirects=True)
        page=(rr.text or '').lower()
    except Exception: page=''
    if any(x in page for x in ['kboard-', 'powered by kboard', 'mod=editor', 'kboard_content']):
        res='kboard'
    # 그누보드 신호 우선 확인(가장 흔함)
    elif probe('/bbs/login.php',['mb_password','mb_id']) or probe('/bbs/',['bo_table','wr_id','gnuboard']):
        res='gnuboard'
    # Cafe24 신호
    elif probe('/member/login.html',['member_id','passwd','member_passwd']) or \
         probe('/',['cafe24','xans-','ec-base','/board/write.html','smartdesign']):
        res='cafe24'
    else:
        res='gnuboard'
    _plat_cache[base]=res
    return res

def resolve_platform(site):
    """사이트의 platform 이 auto/미지정이면 감지해서 확정값 반환."""
    plat=(site.get('platform') or '').strip().lower()
    if plat in ('gnuboard','cafe24','kboard'): return plat
    return detect_platform(site.get('site_url',''))

# ==================== 감독형 자가학습 발행 (셀렉터 자동 탐색) ====================
SUBJECT_HINTS=['subject','title','제목','wr_subject','head','tit','bo_subject','headline','sj','제 목']
CONTENT_HINTS=['content','contents','내용','body','wr_content','memo','board_content','desc','editor',
               'text','story','article','ir1','se_','ck','td_content','document_content']
SUBMIT_HINTS=['등록','작성','확인','저장','올리','완료','submit','write','save','send','regist','apply','ok','confirm']

def _sel_vis(el):
    try: return el.is_displayed()
    except Exception: return False
def _sel_attr(el,name):
    try: return (el.get_attribute(name) or '')
    except Exception: return ''
def _css_for(el):
    """요소를 다시 찾을 수 있는 안정적 CSS 셀렉터 생성(id>name>type/class>tag)."""
    i=_sel_attr(el,'id'); n=_sel_attr(el,'name')
    if i: return f"[id='{i}']"
    if n: return f"[name='{n}']"
    try: tag=el.tag_name
    except Exception: return '*'
    typ=_sel_attr(el,'type')
    if tag=='input' and typ: return f"input[type='{typ}']"
    cls=[c for c in (_sel_attr(el,'class') or '').split() if c][:2]
    if cls: return tag+'.'+'.'.join(cls)
    return tag

def _is_specific(sel):
    """id/name/type/class 로 특정되는 셀렉터인지(단순 태그명은 위험)."""
    return bool(sel) and ('[' in sel or '.' in sel)

def discover_subject(d):
    from selenium.webdriver.common.by import By
    inputs=d.find_elements(By.CSS_SELECTOR,"input[type='text'],input[type='search'],input:not([type])")
    first=None
    for el in inputs:
        if not _sel_vis(el): continue
        blob=(_sel_attr(el,'name')+' '+_sel_attr(el,'id')+' '+_sel_attr(el,'placeholder')).lower()
        if any(h in blob for h in SUBJECT_HINTS): return _css_for(el)
        if first is None: first=el
    return _css_for(first) if first is not None else None

def discover_content(d):
    """본문 입력란 탐색 → (mode, selector). mode: iframe|contenteditable|textarea."""
    from selenium.webdriver.common.by import By
    iframes=[f for f in d.find_elements(By.CSS_SELECTOR,'iframe') if _sel_vis(f)]
    for f in iframes:
        blob=(_sel_attr(f,'id')+' '+_sel_attr(f,'class')+' '+_sel_attr(f,'title')).lower()
        if any(h in blob for h in ['editor','wysiwyg','content','se2','cke','편집','rich','smart']):
            return ('iframe',_css_for(f))
    for el in d.find_elements(By.CSS_SELECTOR,"[contenteditable='true']"):
        if _sel_vis(el): return ('contenteditable',_css_for(el))
    tas=[t for t in d.find_elements(By.CSS_SELECTOR,'textarea') if _sel_vis(t)]
    for t in tas:
        blob=(_sel_attr(t,'name')+' '+_sel_attr(t,'id')).lower()
        if any(h in blob for h in CONTENT_HINTS): return ('textarea',_css_for(t))
    if tas: return ('textarea',_css_for(tas[0]))
    if iframes: return ('iframe',_css_for(iframes[0]))
    return (None,None)

def discover_submit(d):
    """등록 버튼 셀렉터 탐색. 특정 불가한 단순 태그면 None(→ form.submit() 사용)."""
    from selenium.webdriver.common.by import By
    cands=d.find_elements(By.CSS_SELECTOR,"input[type='submit'],button,a,input[type='button']")
    generic=None
    for el in cands:   # type=submit 우선
        if _sel_vis(el) and el.tag_name=='input' and _sel_attr(el,'type')=='submit':
            sel=_css_for(el)
            if _is_specific(sel): return sel
            generic=generic or sel
    for el in cands:   # 텍스트/속성 힌트
        if not _sel_vis(el): continue
        blob=((el.text or '')+' '+_sel_attr(el,'value')+' '+_sel_attr(el,'onclick')+' '+_sel_attr(el,'id')+' '+_sel_attr(el,'class')).lower()
        if any(h in blob for h in SUBMIT_HINTS):
            sel=_css_for(el)
            if _is_specific(sel): return sel
            generic=generic or sel
    return None   # 특정 셀렉터 없음 → form.submit() 폴백

def discover_login(d, base):
    """로그인 페이지 후보를 돌며 id/pw/버튼 셀렉터 탐색."""
    from selenium.webdriver.common.by import By
    for path in ['/bbs/login.php','/member/login.html','/login','/member/login','/index.php?mode=login']:
        try: d.get(base+path); time.sleep(1.5)
        except Exception: continue
        pws=[p for p in d.find_elements(By.CSS_SELECTOR,"input[type='password']") if _sel_vis(p)]
        if not pws: continue
        texts=[t for t in d.find_elements(By.CSS_SELECTOR,"input[type='text'],input[type='email'],input:not([type])") if _sel_vis(t)]
        if not texts: continue
        return {'login_url':base+path,'id_sel':_css_for(texts[0]),'pw_sel':_css_for(pws[0]),'login_btn':discover_submit(d)}
    return None

def _confirm_posted(d):
    from selenium.webdriver.common.by import By
    curl=d.current_url or ''
    head=curl.split('?')[0]
    if any(k in curl for k in ['wr_id=','board.php','read.html','list.html','view.html','article','board_no','mod=document','uid=']) and 'write' not in head and 'mod=editor' not in curl:
        return True,curl
    try: body=d.find_element(By.TAG_NAME,'body').text[:1500]
    except Exception: body=''
    if any(k in body for k in ['등록되었습니다','작성되었습니다','등록 완료','승인 대기','승인대기','완료되었']):
        return True,'등록됨'
    if any(k in body for k in ['권한이 없','권한 없','로그인이 필요','로그인 필요','게시가 금지','차단','스팸']):
        return False,'게시 권한 없음/로그인 필요'
    return False,'등록 확인 불가 — 게시판 설정 확인'

def _fill_recipe_fields(d, rec, title, content):
    """현재 write 페이지에 제목/본문 채우고 등록 클릭(네비게이션 없음)."""
    from selenium.webdriver.common.by import By
    d.find_element(By.CSS_SELECTOR,rec['subject_sel']).clear()
    d.find_element(By.CSS_SELECTOR,rec['subject_sel']).send_keys(title)
    editor_content,html_mode=editor_content_for_page(d,content)
    mode=rec.get('content_mode'); csel=rec.get('content_sel')
    # KBoard 코드 탭이 활성화되면 iframe 대신 제출 textarea를 사용한다.
    if html_mode:
        try:
            kta=[x for x in d.find_elements(By.CSS_SELECTOR,"textarea#kboard_content,textarea[name='kboard_content']") if _sel_vis(x)]
            if kta:
                mode='textarea'; csel="#kboard_content" if _sel_attr(kta[0],'id') else "textarea[name='kboard_content']"
        except Exception: pass
    if mode=='iframe':
        fr=d.find_element(By.CSS_SELECTOR,csel); d.switch_to.frame(fr)
        d.execute_script("document.body.innerHTML=arguments[0]",editor_content); d.switch_to.default_content()
    elif mode=='contenteditable':
        el=d.find_element(By.CSS_SELECTOR,csel); d.execute_script("arguments[0].innerHTML=arguments[1]",el,editor_content)
    else:
        el=d.find_element(By.CSS_SELECTOR,csel); el.clear(); el.send_keys(editor_content)
    # 스마트에디터 동기화 시도(있으면)
    try: d.execute_script("if(typeof oEditors!=='undefined')try{oEditors.getById[Object.keys(oEditors.getById)[0]].exec('UPDATE_CONTENTS_FIELD',[])}catch(e){}")
    except Exception: pass
    if '<img' not in (content or '').lower(): attach_saved_images(d,1)
    if rec.get('submit_sel'):
        try: d.find_element(By.CSS_SELECTOR,rec['submit_sel']).click()
        except Exception:
            try: d.find_element(By.CSS_SELECTOR,'form').submit()
            except Exception: pass
    else:
        try: d.find_element(By.CSS_SELECTOR,'form').submit()
        except Exception: pass
    time.sleep(3); dismiss_alerts(d)

def _apply_recipe(d, site, rec, title, content, skip_login=False):
    """저장된 레시피로 발행(로그인→글쓰기→채우기→확인). skip_login=True면 재로그인 생략(가입 직후 세션)."""
    from selenium.webdriver.common.by import By
    mid=site.get('mb_id',''); mpw=site.get('mb_pass','')
    if mid and not skip_login:
        base=re.match(r'(https?://[^/]+)',rec.get('write_url','') or ''); base=base.group(1) if base else ''
        if rec.get('login_url') and rec.get('id_sel') and rec.get('pw_sel'):
            d.get(rec['login_url']); time.sleep(1.5)
            try: e=d.find_element(By.CSS_SELECTOR,rec['id_sel']); e.clear(); e.send_keys(mid)
            except Exception: pass
            try: e=d.find_element(By.CSS_SELECTOR,rec['pw_sel']); e.clear(); e.send_keys(mpw)
            except Exception: pass
            if rec.get('login_btn'):
                try: d.find_element(By.CSS_SELECTOR,rec['login_btn']).click()
                except Exception: pass
            time.sleep(2); dismiss_alerts(d)
        elif base:
            _platform_login(d,base,site)   # 저장된 상세 셀렉터 없으면 플랫폼 로그인
    d.get(rec['write_url']); time.sleep(2); dismiss_alerts(d)
    _,missing=fill_required_post_fields(d,site)
    if missing: return False,'필수항목 설정 필요: '+', '.join(missing[:6])
    _fill_recipe_fields(d, rec, title, content)
    return _confirm_posted(d)

def _page_login_state(d):
    """현재 페이지가 로그인 화면인지(본문 에디터 없음 + 비번칸/로그인안내) 판별."""
    from selenium.webdriver.common.by import By
    try: has_pw=any(_sel_vis(e) for e in d.find_elements(By.CSS_SELECTOR,"input[type='password']"))
    except Exception: has_pw=False
    has_editor=bool(discover_content(d)[1])   # 글쓰기 페이지엔 본문 에디터가 있음
    try: body=d.find_element(By.TAG_NAME,'body').text[:1000]
    except Exception: body=''
    login_notice=('로그인' in body and ('필요' in body or '회원가입' in body or '아이디' in body))
    return (not has_editor) and (has_pw or login_notice)

def _platform_login(d, base, site):
    """플랫폼별 신뢰 셀렉터로 로그인 시도(그누보드/Cafe24). 로그인 URL 반환(실패 None)."""
    from selenium.webdriver.common.by import By
    mid=site.get('mb_id',''); mpw=site.get('mb_pass','')
    if not mid: return None
    plat=resolve_platform(site)
    tries=[('/bbs/login.php',"input[name='mb_id']","input[name='mb_password']")] if plat=='gnuboard' else \
          [('/member/login.html',"input[name='member_id'],input[name='login_id'],input[name='id']","input[name='member_passwd'],input[name='passwd'],input[name='password']")]
    tries.append(('/bbs/login.php',"input[name='mb_id']","input[name='mb_password']"))
    tries.append(('/member/login.html',"input[name='member_id'],input[name='login_id'],input[name='id']","input[name='member_passwd'],input[name='passwd'],input[name='password']"))
    for path,idsel,pwsel in tries:
        try:
            d.get(base+path); time.sleep(1.5)
            ide=d.find_elements(By.CSS_SELECTOR,idsel); pwe=d.find_elements(By.CSS_SELECTOR,pwsel)
            if not ide or not pwe: continue
            ide[0].clear(); ide[0].send_keys(mid); pwe[0].clear(); pwe[0].send_keys(mpw)
            if not _click_first(d,["input[type='submit']","button[type='submit']",".btn_submit","a.btnSubmit","#btnLogin",".btnEm"]):
                try: pwe[0].submit()
                except Exception: pass
            time.sleep(2); dismiss_alerts(d)
            return base+path
        except Exception: continue
    return None

def discover_write_page(d, base, site):
    bo=str(site.get('bo_table','') or '').strip(); plat=resolve_platform(site)
    cands=[]
    # 관리자가 등록한 게시판 화면에서 실제 글쓰기 링크를 먼저 실측한다.
    # 사이트 전체를 탐색하지 않고, 등록 URL 한 화면의 동일 출처 링크만 사용한다.
    try:
        start=(site.get('site_url') or base).strip()
        d.get(start); time.sleep(2); dismiss_alerts(d)
        from selenium.webdriver.common.by import By
        for a in d.find_elements(By.CSS_SELECTOR,'a[href]'):
            href=urllib.parse.urljoin(d.current_url or start,_sel_attr(a,'href'))
            pu=urllib.parse.urlparse(href); pb=urllib.parse.urlparse(base)
            if (pu.scheme,pu.netloc)!=(pb.scheme,pb.netloc): continue
            blob=((a.text or '')+' '+href+' '+_sel_attr(a,'class')+' '+_sel_attr(a,'id')).lower()
            if any(k in blob for k in ['글쓰기','글 쓰기','글작성','글 작성','새글','새 글','등록하기','작성하기',
                    '문의하기','문의작성','write.php','/write.html','/write','mode=write','act=write',
                    'act=dispboardwrite','board_write','bbs_write','/new','/post/new','wr_write']):
                cands.append(href)
    except Exception: pass
    if plat=='cafe24':
        if bo.isdigit(): cands += [f'/board/write.html?board_no={bo}',f'/board/{bo}/write.html']
        elif bo: cands += [f'/board/{bo}/write.html',f'/board/write.html?board_no=1']
        else: cands += ['/board/write.html?board_no=1','/board/free/write.html']
    else:
        cands += [f'/bbs/write.php?bo_table={bo or "free"}']
    cands+=[f'/bbs/write.php?bo_table={bo or "free"}',f'/board/write.html?board_no={bo if bo.isdigit() else "1"}']
    # 추가 플랫폼/경로 커버리지(전환율↑): XE/Rhymix·일반 게시판 write 경로까지 시도
    _b=bo or 'free'
    cands += [
        f'/?mid={_b}&act=dispBoardWrite', f'/index.php?mid={_b}&act=dispBoardWrite',   # XE/Rhymix
        f'/{_b}/write', f'/board/{_b}/write', f'/bbs/{_b}/write', f'/community/{_b}/write',
        f'/board.php?bo_table={_b}&mode=write', f'/bbs/board.php?bo_table={_b}&mode=write',
        '/bbs/write.php?bo_table=notice', '/bbs/write.php?bo_table=qa', '/bbs/write.php?bo_table=qna',
        '/write', '/post/new', '/new',
    ]
    seen=set()
    for path in cands:
        target=path if str(path).startswith(('http://','https://')) else base+path
        if target in seen: continue
        seen.add(target)
        try: d.get(target); time.sleep(2); dismiss_alerts(d)
        except Exception: continue
        if discover_subject(d) and discover_content(d)[1]:
            return d.current_url or target
    # 폴백: 설정된 bo_table이 유령(오류페이지)일 때 — 홈페이지에서 실제 존재하는 게시판
    # (bo_table=xxx 링크)들을 수집해 write.php를 시도, 글쓰기 폼이 있는 게시판을 찾는다.
    # (forwarder.kr처럼 등록된 게시판ID가 실제로 없어 발행이 막히던 문제 해결)
    if plat!='cafe24':
        try:
            from selenium.webdriver.common.by import By
            d.get((site.get('site_url') or base).strip()); time.sleep(2); dismiss_alerts(d)
            found_bo=[]
            for a in d.find_elements(By.CSS_SELECTOR,"a[href*='bo_table=']"):
                href=_sel_attr(a,'href')
                m=re.search(r'bo_table=([A-Za-z0-9_]+)',href)
                if m and m.group(1) not in found_bo: found_bo.append(m.group(1))
            # 홍보/자유 성격 게시판을 앞으로 (free, promotion, hongbo, community, qa 등 우선)
            def _prio(b):
                bl=b.lower()
                for i,k in enumerate(['promotion','hongbo','pr','free','community','club','qa','notice']):
                    if k in bl: return i
                return 99
            found_bo.sort(key=_prio)
            for b in found_bo[:12]:
                if b==bo: continue
                t=base+f'/bbs/write.php?bo_table={b}'
                if t in seen: continue
                seen.add(t)
                try: d.get(t); time.sleep(1.5); dismiss_alerts(d)
                except Exception: continue
                # 로그인/오류 페이지면 스킵
                cu=(d.current_url or '').lower()
                if 'login' in cu or 'err' in (d.title or '').lower() or '오류' in (d.title or ''): continue
                if discover_subject(d) and discover_content(d)[1]:
                    # 찾은 게시판ID를 사이트에 반영(다음부터 이 게시판 사용)
                    try: set_site_flag(site.get('id'), bo_table=b)
                    except Exception: pass
                    if isinstance(site,dict): site['bo_table']=b
                    add_log(f'[게시판 자동교정] {site.get("name") or base} — 유령 게시판({bo}) → 실제 게시판({b})')
                    return d.current_url or t
        except Exception: pass
    # 폴백2: XE/Rhymix(mid=) 게시판 — 홈페이지에서 mid= 링크를 모아 글쓰기 화면을 시도한다.
    try:
        from selenium.webdriver.common.by import By
        d.get((site.get('site_url') or base).strip()); time.sleep(2); dismiss_alerts(d)
        mids=[]
        for a in d.find_elements(By.CSS_SELECTOR,"a[href*='mid=']"):
            mm=re.search(r'mid=([A-Za-z0-9_]+)',_sel_attr(a,'href'))
            if mm and mm.group(1) not in mids: mids.append(mm.group(1))
        for mid_ in mids[:10]:
            for t in (base+f'/?mid={mid_}&act=dispBoardWrite', base+f'/index.php?mid={mid_}&act=dispBoardWrite'):
                if t in seen: continue
                seen.add(t)
                try: d.get(t); time.sleep(1.5); dismiss_alerts(d)
                except Exception: continue
                cu=(d.current_url or '').lower()
                if 'login' in cu or 'err' in (d.title or '').lower() or '오류' in (d.title or ''): continue
                if discover_subject(d) and discover_content(d)[1]:
                    try: set_site_flag(site.get('id'), bo_table=mid_, platform='xe')
                    except Exception: pass
                    if isinstance(site,dict): site['bo_table']=mid_
                    add_log(f'[게시판 자동교정] {site.get("name") or base} — XE 게시판({mid_}) 발견')
                    return d.current_url or t
    except Exception: pass
    return None

def _save_site_analysis(site_id, analysis, rec=None):
    """실측 결과는 성공/실패 모두 저장하고, 확실한 폼만 발행 레시피로 승격한다."""
    if not site_id: return
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id')!=site_id: continue
            s['analysis']=analysis
            if rec:
                s['learned']=rec
                if rec.get('platform'): s['platform']=rec['platform']
        save_sites(sites)

def analyze_site_logic(site):
    """허용된 한 사이트의 등록 URL/로그인/글쓰기 DOM을 측정한다. 절대 제출하지 않는다."""
    from selenium.webdriver.common.by import By
    now=datetime.now().astimezone().isoformat(timespec='seconds')
    result={'measured_at':now,'mode':'read_only_no_submit','ok':False,'platform':'',
            'start_url':site.get('site_url',''),'final_url':'','write_url':'',
            'blocked':False,'captcha':'','form':{},'steps':[]}
    def step(name,ok,detail=''):
        result['steps'].append({'name':name,'ok':bool(ok),'detail':str(detail)[:300]})
    url=(site.get('site_url') or '').strip(); m=re.match(r'(https?://[^/]+)',url)
    base=m.group(1) if m else url
    if not base:
        step('등록 URL',False,'URL 없음'); return result,None
    d=get_driver(); plat=resolve_platform(site); result['platform']=plat
    step('플랫폼',True,plat)
    try:
        d.get(url or base); time.sleep(2); dismiss_alerts(d)
        result['final_url']=d.current_url or ''
        step('등록 화면',True,result['final_url'])
    except Exception as e:
        step('등록 화면',False,str(e)[:180]); return result,None
    if _page_is_blocked(d):
        result['blocked']=True; step('보안 차단',False,'403/보안 차단 감지 — 우회하지 않음'); return result,None
    cap=detect_captcha(d)
    if cap:
        result['captcha']=str(cap); step('CAPTCHA',False,str(cap)+' — 우회하지 않음'); return result,None
    step('초기 화면 보안',True,'차단/CAPTCHA 없음')
    if site.get('mb_id'):
        lu=_platform_login(d,base,site)
        step('로그인',bool(lu),lu or '저장 계정으로 로그인 페이지/필드 확인 실패')
    else:
        step('로그인',True,'계정 미설정 — 공개/비회원 글쓰기만 측정')
    # 설정된 게시판 ID의 표준 쓰기 URL을 먼저 직접 측정하여 로그인/차단 원인을 보존한다.
    bo=str(site.get('bo_table') or '').strip(); direct=''
    if bo:
        direct=(base+f'/board/write.html?board_no={bo}') if plat=='cafe24' and bo.isdigit() else \
               (base+f'/bbs/write.php?bo_table={urllib.parse.quote(bo)}')
    wu=None
    if direct:
        try:
            d.get(direct); time.sleep(2); dismiss_alerts(d)
            result['final_url']=d.current_url or direct
            if _page_is_blocked(d):
                result['write_url']=direct; result['blocked']=True
                step('설정 글쓰기 URL',False,'접근 차단: '+direct+' — 우회하지 않음'); return result,None
            direct_cap=detect_captcha(d)
            if direct_cap:
                result['write_url']=direct; result['captcha']=str(direct_cap)
                step('설정 글쓰기 URL',False,'CAPTCHA 감지: '+str(direct_cap)+' — 우회하지 않음'); return result,None
            if _page_login_state(d) and not site.get('mb_id'):
                result['write_url']=direct
                step('설정 글쓰기 URL',False,'로그인 필요: '+result['final_url']); return result,None
            if discover_subject(d) and discover_content(d)[1]: wu=result['final_url']
        except Exception as e:
            step('설정 글쓰기 URL',False,str(e)[:180])
    if not wu: wu=discover_write_page(d,base,site)
    result['final_url']=d.current_url or result['final_url']
    if not wu:
        detail='로그인 화면으로 이동됨' if _page_login_state(d) else '실제 링크 및 설정 게시판ID 후보에서 폼을 찾지 못함'
        step('글쓰기 폼',False,detail); return result,None
    result['write_url']=wu; result['final_url']=d.current_url or wu
    step('글쓰기 폼',True,wu)
    if _page_is_blocked(d):
        result['blocked']=True; step('글쓰기 보안',False,'403/보안 차단 감지 — 우회하지 않음'); return result,None
    cap=detect_captcha(d)
    if cap:
        result['captcha']=str(cap); step('CAPTCHA',False,str(cap)+' — 우회하지 않음'); return result,None
    subj=discover_subject(d); cmode,csel=discover_content(d); sub=discover_submit(d)
    form_el=None
    try:
        if subj: form_el=d.find_element(By.CSS_SELECTOR,subj).find_element(By.XPATH,'ancestor::form[1]')
    except Exception: pass
    form={'action':'','method':'','subject_sel':subj or '','content_mode':cmode or '',
          'content_sel':csel or '','submit_sel':sub or '','required_fields':[]}
    if form_el is not None:
        form['action']=urllib.parse.urljoin(result['final_url'],_sel_attr(form_el,'action'))
        form['method']=(_sel_attr(form_el,'method') or 'get').lower()
        try:
            for el in form_el.find_elements(By.CSS_SELECTOR,'input[required],textarea[required],select[required]'):
                name=_sel_attr(el,'name') or _sel_attr(el,'id')
                typ=_sel_attr(el,'type') or el.tag_name
                if name: form['required_fields'].append({'name':name,'type':typ})
        except Exception: pass
    result['form']=form
    step('제목 필드',bool(subj),subj or '없음')
    step('본문 필드',bool(csel),f'{cmode} · {csel}' if csel else '없음')
    step('등록 동작',bool(sub or form_el is not None),sub or ('form '+form['method'] if form_el is not None else '없음'))
    if not subj or not csel or form_el is None:
        step('판정',False,'발행 레시피로 저장할 신뢰도 부족'); return result,None
    rec={'platform':plat,'learned_at':now,'learned_mode':'measured_no_submit',
         'write_url':wu,'subject_sel':subj,'content_mode':cmode,
         'content_sel':csel,'submit_sel':sub,'form_action':form['action'],'form_method':form['method']}
    result['ok']=True; step('판정',True,'DOM 실측 완료 · 제출 없이 레시피 저장 가능')
    return result,rec

def discover_and_post(site, title, content, skip_login=False):
    """DOM을 훑어 셀렉터를 스스로 찾아 발행. 성공 시 (ok,msg,레시피) 반환.
       skip_login=True면 가입 직후 로그인 세션 재사용(재로그인 생략)."""
    d=get_driver()
    url=site.get('site_url','').rstrip('/'); m=re.match(r'(https?://[^/]+)',url); base=m.group(1) if m else url
    rec={'platform':resolve_platform(site),'learned_at':datetime.now().strftime('%Y-%m-%d %H:%M')}
    # 로그인: 플랫폼별 신뢰 셀렉터 우선, 실패 시 자동 탐색
    if site.get('mb_id') and not skip_login:
        lu=_platform_login(d,base,site)
        if lu: rec['login_url']=lu
        else:
            login=discover_login(d,base)
            if login:
                rec.update(login)
                from selenium.webdriver.common.by import By
                try: e=d.find_element(By.CSS_SELECTOR,login['id_sel']); e.clear(); e.send_keys(site.get('mb_id',''))
                except Exception: pass
                try: e=d.find_element(By.CSS_SELECTOR,login['pw_sel']); e.clear(); e.send_keys(site.get('mb_pass',''))
                except Exception: pass
                if login.get('login_btn'):
                    try: d.find_element(By.CSS_SELECTOR,login['login_btn']).click()
                    except Exception: pass
                time.sleep(2); dismiss_alerts(d)
    wu=discover_write_page(d,base,site)
    if not wu:
        # 원인 세분화: 로그인 화면으로 튕겼는지 확인
        if _page_login_state(d):
            if not site.get('mb_id'):
                return False,'로그인이 필요한 게시판입니다 — 사이트에 아이디/비밀번호를 입력한 뒤 다시 학습하세요',None
            return False,'로그인 실패로 보입니다 — 아이디/비밀번호가 맞는지 확인하세요(캡차/보안문자 게시판일 수도)',None
        return False,'글쓰기 페이지 못찾음(학습 실패) — 게시판ID(bo_table) 확인',None
    rec['write_url']=wu
    # 보안 차단은 즉시 중단. 캡차는 감지만 해두고 제출 직전에 2captcha로 해결한다.
    if _page_is_blocked(d): return False,'보안 차단 페이지(403 등) — 즉시 중단',None
    _cap=detect_captcha(d)
    subj=discover_subject(d); cmode,csel=discover_content(d); sub=discover_submit(d)
    if not subj or not csel:
        return False,'제목/본문 입력란 못찾음(학습 실패)',None
    rec['subject_sel']=subj; rec['content_mode']=cmode; rec['content_sel']=csel; rec['submit_sel']=sub
    _,missing=fill_required_post_fields(d,site)
    if missing: return False,'필수항목 설정 필요: '+', '.join(missing[:6]),None
    try: _fill_recipe_fields(d, rec, title, content)
    except Exception as e: return False,f'입력 실패(학습): {str(e)[:80]}',None
    # 캡차 감지 시 2captcha로 자동 해결 후 입력 (실패하면 발행 중단)
    if _cap:
        cfg=load_config()
        success,msg,answer,info=solve_captcha_with_2captcha(d,site,_cap,cfg)
        if success:
            from selenium.webdriver.common.by import By
            for sel in ["input[name='captcha_key']","#captcha_key","input[name='wr_key']","input[name*='captcha']","input[id*='captcha']"]:
                try:
                    inp=d.find_element(By.CSS_SELECTOR,sel)
                    if inp and inp.is_displayed():
                        inp.clear(); inp.send_keys(answer); add_log(f'[2captcha] {msg}'); time.sleep(1); break
                except Exception: pass
        else:
            add_log(f'[2captcha] 자동 해결 실패: {msg} → 자동발행 불가')
            return False,f'캡차 감지({_cap}) — 2captcha 자동 해결 실패: {msg}',None
    ok,msg=_confirm_posted(d)
    return ok,msg,(rec if ok else None)

def dryrun_post(site, title, content_html):
    """등록 '직전'까지만 수행 — 실제 글은 올리지 않고 발행 가능 여부를 검증한다.
       로그인 → 글쓰기 페이지 → 캡차확인 → 입력란 탐색 → 값 채우기 까지. 제출 클릭 없음."""
    from selenium.webdriver.common.by import By
    steps=[]
    def st(name,ok,detail=''): steps.append({'name':name,'ok':bool(ok),'detail':str(detail)[:220]})
    url=site.get('site_url','').rstrip('/'); m=re.match(r'(https?://[^/]+)',url)
    base=m.group(1) if m else url
    d=get_driver()
    plat=resolve_platform(site); st('플랫폼 판별',True,plat)

    # 1) 로그인
    mid=site.get('mb_id','')
    if mid:
        lu=_platform_login(d,base,site)
        if lu:
            # 로그인 성공 여부: 로그아웃 링크 존재로 추정
            try: body=d.find_element(By.TAG_NAME,'body').text[:1500]
            except Exception: body=''
            logged=('로그아웃' in body) or ('logout' in (d.page_source or '').lower())
            st('로그인',logged,f'{lu} → '+('세션 확보됨' if logged else '로그인 확인 불가(비번/아이디 확인)'))
        else:
            st('로그인',False,'로그인 페이지를 못 찾음')
    else:
        st('로그인',True,'아이디 미설정 — 비회원 글쓰기로 진행')

    # 2) 글쓰기 페이지
    rec=site.get('learned') or {}
    wu=rec.get('write_url') if rec.get('write_url') else None
    if wu:
        try: d.get(wu); time.sleep(2); dismiss_alerts(d)
        except Exception: wu=None
    if not wu:
        wu=discover_write_page(d,base,site)
    if not wu:
        if _page_login_state(d):
            st('글쓰기 페이지',False,'로그인 화면으로 이동됨 — 계정 필요 또는 로그인 실패')
        else:
            st('글쓰기 페이지',False,'못 찾음 — 게시판ID(bo_table) 확인')
        return False,steps
    st('글쓰기 페이지',True,wu)

    # 3) 차단/캡차
    if _page_is_blocked(d):
        st('보안 차단 확인',False,'403/보안 차단 페이지'); return False,steps
    st('보안 차단 확인',True,'차단 없음')
    cap=detect_captcha(d)
    if cap:
        _cfg=load_config()
        if _cfg.get('twocaptcha_enabled') and (_cfg.get('twocaptcha_api_key') or '').strip():
            st('캡차 확인',True,f'{cap} 감지 — 발행 시 2captcha로 자동 해결 예정')
        else:
            st('캡차 확인',False,f'{cap} 감지 — 자동발행 부적합(2captcha 비활성화)'); return False,steps
    else:
        st('캡차 확인',True,'캡차 없음')

    # 4) 입력란 탐색
    subj=rec.get('subject_sel') or discover_subject(d)
    cmode,csel=(rec.get('content_mode'),rec.get('content_sel')) if rec.get('content_sel') else discover_content(d)
    sub=rec.get('submit_sel') if rec.get('submit_sel') else discover_submit(d)
    st('제목 입력란',bool(subj),subj or '못 찾음')
    st('본문 에디터',bool(csel),f'{cmode} · {csel}' if csel else '못 찾음')
    st('등록 버튼',True,sub or '특정 셀렉터 없음 → form.submit() 사용 예정')
    if not subj or not csel: return False,steps

    # 5) 실제로 값 채워보기 (제출은 하지 않음)
    try:
        e=d.find_element(By.CSS_SELECTOR,subj); e.clear(); e.send_keys(title)
        st('제목 입력 테스트',True,f'{len(title)}자 입력 성공')
    except Exception as ex:
        st('제목 입력 테스트',False,str(ex)[:120]); return False,steps
    try:
        editor_content,html_mode=editor_content_for_page(d,content_html)
        st('본문 형식',True,'HTML 우선' if html_mode else 'HTML 옵션 없음 → 일반 텍스트')
        if html_mode:
            try:
                kta=[x for x in d.find_elements(By.CSS_SELECTOR,"textarea#kboard_content,textarea[name='kboard_content']") if _sel_vis(x)]
                if kta:
                    cmode='textarea'; csel="#kboard_content" if _sel_attr(kta[0],'id') else "textarea[name='kboard_content']"
            except Exception: pass
        if cmode=='iframe':
            fr=d.find_element(By.CSS_SELECTOR,csel); d.switch_to.frame(fr)
            d.execute_script("document.body.innerHTML=arguments[0]",editor_content); d.switch_to.default_content()
        elif cmode=='contenteditable':
            el=d.find_element(By.CSS_SELECTOR,csel); d.execute_script("arguments[0].innerHTML=arguments[1]",el,editor_content)
        else:
            el=d.find_element(By.CSS_SELECTOR,csel); el.clear(); el.send_keys(editor_content[:2000])
        st('본문 입력 테스트',True,f'{len(editor_content)}자 '+('HTML' if html_mode else '일반 텍스트')+' 입력 성공')
    except Exception as ex:
        d.switch_to.default_content()
        st('본문 입력 테스트',False,str(ex)[:120]); return False,steps

    if '<img' in (content_html or '').lower():
        attached,attach_detail=0,'본문 HTML 이미지 1개 사용 · 중복 파일 첨부 생략'
    else:
        attached,attach_detail=attach_saved_images(d,1)
    st('이미지 파일 첨부',True,attach_detail)

    filled_required,missing_required=fill_required_post_fields(d,site)
    st('추가 필수항목',not missing_required,
       ('자동 입력: '+', '.join(filled_required) if filled_required else '추가 입력 없음')+
       ((' · 설정 필요: '+', '.join(missing_required)) if missing_required else ''))
    if missing_required: return False,steps

    # 6) 필수 추가 입력란(비회원 이름/비번 등) 확인
    try:
        reqs=[]
        for el in d.find_elements(By.CSS_SELECTOR,"input[required],input[type='password']"):
            if not _sel_vis(el): continue
            nm=_sel_attr(el,'name') or _sel_attr(el,'id')
            if nm and nm not in (subj or ''): reqs.append(nm)
        st('추가 필수 입력란',True,(', '.join(reqs[:6]) if reqs else '없음')+(' (비회원 글쓰기는 이름/비번 필요할 수 있음)' if reqs else ''))
    except Exception: pass

    st('종합',True,'✅ 등록 직전까지 모두 성공 — 실제 발행 가능 상태 (글은 올리지 않았습니다)')
    return True,steps

def save_learned(site_id, rec):
    """학습된 셀렉터 레시피를 사이트에 영구 저장."""
    if not site_id: return
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id')==site_id:
                s['learned']=rec
                if rec.get('platform'): s['platform']=rec['platform']
        save_sites(sites)

def set_site_flag(site_id, **fields):
    """사이트에 플래그/상태 저장(예: has_captcha)."""
    if not site_id: return
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id')==site_id: s.update(fields)
        save_sites(sites)

def _strip_non_bmp(s):
    """ChromeDriver는 BMP(U+FFFF 이하) 밖 문자를 send_keys로 입력 못 한다
       ('ChromeDriver only supports characters in the BMP'). 제목/본문의 이모지 등
       비BMP 문자를 제거해 발행이 그 에러로 통째로 실패하는 것을 막는다.
       (SEO 가치 없는 장식 이모지라 제거해도 무방 — 대표님 게시글 발행 안정화)."""
    if not s: return s
    return ''.join(ch for ch in str(s) if ord(ch) <= 0xFFFF)

def do_post(site, title, content_html, skip_login=False):
    """발행 라우팅(하이브리드+자가학습): 학습레시피→플랫폼기본→자동학습.
       skip_login=True: 가입 직후 이미 로그인된 세션에서 재로그인 없이 바로 글쓰기(비표준 로그인폼 구제)."""
    # 비BMP(이모지 등) 제거 — ChromeDriver send_keys가 못 다뤄 발행 전체가 실패하던 문제 방지.
    title=_strip_non_bmp(title); content_html=_strip_non_bmp(content_html)
    rec=site.get('learned')
    # 1) 저장된 학습 레시피 우선
    if rec and rec.get('write_url') and rec.get('subject_sel') and rec.get('content_sel'):
        try:
            ok,msg=_apply_recipe(get_driver(),site,rec,title,content_html,skip_login=skip_login)
            if ok: return True,msg
            add_log(f'[학습레시피 실패→폴백] {site.get("name","")}')
        except Exception as e:
            add_log(f'[학습레시피 오류→폴백] {str(e)[:60]}')
    # 2) 플랫폼별 기본 발행기
    plat=resolve_platform(site)
    if plat=='kboard':
        try:
            ok,msg,newrec=discover_and_post(site,title,content_html,skip_login=skip_login)
            if ok and newrec: save_learned(site.get('id'),newrec)
            return ok,msg
        except Exception as e:
            return False,'KBoard 발행 오류: '+str(e)[:120]
    # 2-0) ★browserless 초고속 발행(requests) 우선 시도 — 그누보드 비회원 글쓰기.
    #      성공(True)이면 바로 반환(~2~3초, 524 회피). None이면 셀레늄으로 폴백,
    #      False(캡차오답·본문미입력 등 명확한 실패)면 셀레늄 재시도 대신 그대로 반환.
    #      cafe24·skip_login(가입직후 세션)·학습레시피 케이스는 제외(위/아래에서 처리).
    if plat!='cafe24' and not skip_login and (cfg_http:=load_config()).get('http_publish_enabled',True):
        try:
            hok,hmsg=gnuboard_post_http(site,title,content_html)
        except Exception as e:
            hok,hmsg=None,f'HTTP 예외({str(e)[:40]}) — 폴백'
        if hok is True:
            add_log(f'[browserless] 발행 성공 {site.get("name") or (site.get("site_url","") or "")[:24]}')
            return True,hmsg
        if hok is False:
            return False,hmsg   # 명확한 실패(캡차오답 등) — 셀레늄으로 반복 시도하지 않음
        # hok is None → 셀레늄 폴백으로 진행
    try:
        ok,msg=(cafe24_post if plat=='cafe24' else gnuboard_post)(site,title,content_html,skip_login=skip_login)
    except Exception as e:
        ok,msg=False,str(e)
    if ok: return True,msg
    # 3) 구조적 실패(일시적·로그인·차단 제외)면 자동 학습(디스커버리) 발행 후 레시피 저장
    reason,_,_=classify_fail(msg)
    if reason in ('board','other') or rec is not None:
        try:
            ok2,msg2,newrec=discover_and_post(site,title,content_html,skip_login=skip_login)
            if ok2 and newrec:
                save_learned(site.get('id'),newrec)
                add_log(f'[자동학습 성공] {site.get("name","")} — 셀렉터 저장됨')
                return True,'(자가학습) '+str(msg2)
            if ok2: return True,str(msg2)
            return False,f'{msg} · 학습시도:{msg2}'
        except Exception as e:
            return False,f'{msg} · 학습실패:{str(e)[:50]}'
    return False,msg

# ==================== 큐 & 워커 & 이력 ====================
post_queue=queue.Queue()
wk_active=False
wk_paused=False
wk_stats={'success':0,'fail':0,'queued':0,'total':0,'done':0,'skipped':0,'retry':0}
STATS_LOCK=threading.Lock()
POST_LOCK=threading.Lock()
JOB_LOCK=threading.Lock()   # history.json / queue.json 동시성 보호
# ★사이트별 발행 락(대표님 지시 '속도가 생명'): 여러 워커가 '서로 다른' 사이트는 동시 발행하되,
#   '같은' 사이트에는 동시 발행 안 함(도배방지/간격 우회 방지). site_id별 락을 만들어 관리.
_site_post_locks={}; _site_post_locks_guard=threading.Lock()
def _site_lock(site_id):
    with _site_post_locks_guard:
        lk=_site_post_locks.get(site_id)
        if lk is None: lk=threading.Lock(); _site_post_locks[site_id]=lk
        return lk
# ★전역 동시 크롬 상한(대표님 '속도' + VPS 메모리 보호): 모든 작업실 슬롯×fanout를 통틀어
#   이 수만큼만 크롬 동시 실행. publish_max_chromes로 조절(기본6). 크롬 1개 ~300~500MB.
_chrome_sem=None; _chrome_sem_n=0; _chrome_sem_guard=threading.Lock()
def _global_chrome_sem():
    global _chrome_sem,_chrome_sem_n
    n=max(1,min(12,int(load_config().get('publish_max_chromes',6) or 6)))
    with _chrome_sem_guard:
        if _chrome_sem is None or _chrome_sem_n!=n:
            _chrome_sem=threading.BoundedSemaphore(n); _chrome_sem_n=n
        return _chrome_sem
BULK_LOCK=threading.Lock()
BULK_TASKS={}

# ---- PC/노트북 노드 하트비트(대표님 지시 2026-09-11: 관제실에 기기별 실시간 현황판) ----
#  노드가 claim/report/발굴ingest 호출할 때마다 마지막 활동시각·누적건수를 남긴다.
#  worker-log가 이 값을 내려주면 UI가 'PC / 노트북' 각각 카드로 표시(🟢활성/🔴끊김).
NODE_BEATS={}                     # node_id -> {'last':ts,'action':str,'publish':n,'signup':n,'discover':n,'seen':ts0}
_NODE_LOCK=threading.Lock()
def _node_beat(node_id, action='', publish=0, signup=0, discover=0):
    """노드 활동 1건 기록. action은 최근 동작 요약(한글), 나머지는 누적 카운트 증분."""
    nid=str(node_id or '').strip() or 'pc'
    now=time.time()
    with _NODE_LOCK:
        b=NODE_BEATS.get(nid)
        if not b:
            b={'node_id':nid,'seen':now,'last':now,'action':'','publish':0,'signup':0,'discover':0}
            NODE_BEATS[nid]=b
        b['last']=now
        if action: b['action']=str(action)[:80]
        b['publish']+=int(publish or 0); b['signup']+=int(signup or 0); b['discover']+=int(discover or 0)

# ---- 일시적 실패 자동 재시도(지연 재큐) ----
RETRY_DELAY=300      # 5분 뒤 재시도
RETRY_MAX=2          # 지연 재시도 최대 횟수
_retry_jobs=[]
_retry_lock=threading.Lock()
def schedule_retry(job, delay=None):
    if delay is None: delay=RETRY_DELAY
    with _retry_lock: _retry_jobs.append({'due':time.time()+delay,'job':job})

def retry_loop():
    """지연 재시도 큐를 감시해 시간이 되면 발행 큐로 되돌림."""
    while True:
        try:
            now=time.time(); ready=[]
            with _retry_lock:
                keep=[]
                for it in _retry_jobs:
                    (ready if it['due']<=now else keep).append(it)
                _retry_jobs[:]=keep
            for it in ready:
                post_queue.put(it['job'])
                with STATS_LOCK: wk_stats['queued']=post_queue.qsize()
                if not wk_active: start_workers(load_config().get('workers',2))
        except Exception as e:
            add_log(f'[재시도 루프 오류] {str(e)[:80]}')
        time.sleep(30)

# ---- 발행 이력 원장 (엑셀/결과탭) ----
def history_add(rec):
    with JOB_LOCK:
        h=load_json(HISTORY_FILE,[]); h.append(rec)
        if len(h)>5000: h=h[-5000:]
        save_json(HISTORY_FILE,h)

def history_update(hid,**fields):
    with JOB_LOCK:
        h=load_json(HISTORY_FILE,[])
        for rec in h:
            if rec.get('id')==hid:
                rec.update(fields); rec['updated']=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                break
        save_json(HISTORY_FILE,h)

def _purge_site_history(site):
    """삭제되는 사이트의 미완료/실패 결과 이력(failed·queued·retry·skipped)을 결과탭에서 제거.
       ★'안 되는 사이트' 삭제 시 결과탭에 그 사이트 failed가 계속 남아 '자꾸 실패'처럼 보이던
       것 방지(대표님 지시 2026-09-08). 성공(done)·검증된 이력은 기록보존을 위해 남긴다."""
    sid=site.get('id'); su=str(site.get('site_url') or ''); dom=_domain_of(su)
    if not (sid or dom): return 0
    drop_st={'failed','fail','queued','retry','skipped','error'}
    with JOB_LOCK:
        h=load_json(HISTORY_FILE,[]); before=len(h)
        def _match(r):
            same=(r.get('site_id')==sid) or (dom and _domain_of(str(r.get('site_url') or ''))==dom)
            return same and str(r.get('status','')).lower() in drop_st
        h=[r for r in h if not _match(r)]
        if len(h)!=before: save_json(HISTORY_FILE,h)
        return before-len(h)

# ---- 미완료 작업 영속화 (재시작 복구) ----
def _persist_add(job):
    with JOB_LOCK:
        q=load_json(QUEUE_FILE,[]); q.append(job); save_json(QUEUE_FILE,q)

def _persist_remove(job_id):
    with JOB_LOCK:
        q=[j for j in load_json(QUEUE_FILE,[]) if j.get('job_id')!=job_id]
        save_json(QUEUE_FILE,q)

def recover_queue():
    """재시작 시 유효한 미완료 작업만 복구하고 차단·유령 큐는 스킵 처리."""
    q=load_json(QUEUE_FILE,[])
    n=0; keep=[]; queued_hist=set()
    for job in q:
        hid=job.get('hist_id'); site=job.get('site') or {}
        current=next((s for s in load_sites() if s.get('id')==site.get('id')),None)
        if job.get('site') and job.get('content') and current and is_publishable(current):
            job['site']=current; post_queue.put(job); keep.append(job); n+=1
            if hid: queued_hist.add(hid)
        else:
            why='재시작 시 발행조건 미충족'
            if hid: history_update(hid,status='skipped',message=why)
    save_json(QUEUE_FILE,keep)
    # queue.json에 실제 작업이 없는 queued 이력은 더 이상 진행되지 않는 유령 표시다.
    h=load_json(HISTORY_FILE,[]); changed=False
    for rec in h:
        if rec.get('status')=='queued' and rec.get('id') not in queued_hist:
            rec.update({'status':'skipped','message':'재시작 후 실행 큐 없음'}); changed=True
    if changed: save_json(HISTORY_FILE,h)
    if n:
        with STATS_LOCK:
            wk_stats['total']+=n; wk_stats['queued']=post_queue.qsize()
        add_log(f'[복구] 미완료 작업 {n}건 복구됨 — 워커 시작 시 이어서 발행')
    return n

def site_daily_limit(site,cfg):
    try: return max(0,int(site.get('daily_limit',cfg.get('daily_limit',0)) or 0))
    except Exception: return 0

def site_min_interval(site):
    # 사이트별 값 없으면 config 기본(min_interval_minutes, 없으면 1) 사용 — site_daily_limit과 동일 패턴
    try:
        default=int(load_config().get('min_interval_minutes',1) or 0)
    except Exception:
        default=1
    try: base=max(0,int(site.get('min_interval_minutes',default) or 0))
    except Exception: base=default
    # 학습된 도배방지 간격(flood_sec)이 있으면 그만큼(분 올림)을 최소로 보장 — 사이트 규칙 자동 준수.
    try:
        fs=int(site.get('flood_sec',0) or 0)
        if fs>0: base=max(base,(fs+59)//60)
    except Exception: pass
    return base

def _fresh_site(site):
    return next((s for s in load_sites() if s.get('id')==site.get('id')),site)

def under_daily_limit(site,cfg):
    """사이트당 1일 발행 한도 확인 (0 = 무제한). 도배 방지."""
    site=_fresh_site(site)
    limit=site_daily_limit(site,cfg)
    if limit<=0: return True
    today=datetime.now().strftime('%Y-%m-%d')
    for s in load_sites():
        if s.get('id')==site.get('id'):
            if s.get('posted_date')!=today: return True
            return s.get('posted_today',0)<limit
    return True

def under_min_interval(site):
    """마지막 성공 발행 후 사이트별 최소 간격 준수 여부와 남은 초 반환."""
    site=_fresh_site(site); mins=site_min_interval(site)
    if mins<=0 or not site.get('last_post_at'): return True,0
    try:
        last=datetime.strptime(site['last_post_at'],'%Y-%m-%d %H:%M:%S')
        remain=int(mins*60-(datetime.now()-last).total_seconds())
        return remain<=0,max(0,remain)
    except Exception: return True,0

FAIL_STREAK_DROP=3   # 연속 실패 이 횟수 이상이면 자동 탈락(도배·헛발행 방지)
FAIL_STREAK_LOCK=15  # manual_admin(대표님 등록) 사이트도 이 횟수 이상 연속 실패면 발행 잠금(삭제X, 재허용 가능)

def finalize_post(site,ok,fail_reason=''):
    """상태 갱신 + 성공 시 오늘 발행 카운트 증가 + 연속 실패 카운트(fail_streak) 추적.
       (한 번의 락으로 처리)"""
    today=datetime.now().strftime('%Y-%m-%d')
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id')==site.get('id'):
                s['status']='done' if ok else 'failed'
                if ok:
                    if s.get('posted_date')!=today: s['posted_date']=today; s['posted_today']=0
                    s['posted_today']=s.get('posted_today',0)+1
                    s['last_post_at']=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    s['fail_streak']=0; s.pop('last_fail_reason',None)
                else:
                    s['fail_streak']=int(s.get('fail_streak',0) or 0)+1
                    if fail_reason: s['last_fail_reason']=str(fail_reason)[:120]
                break
        save_sites(sites)
    if not ok:
        reconcile_sites()   # 실패 직후 자동 탈락 조건 재평가(목록 최신화)

def _site_permanent_block(s):
    """이 사이트가 '영구히 발행 불가'인지 판정. 사유 문자열 반환(아니면 '')."""
    # 이메일 인증 필요만으로는 영구차단하지 않는다 — 자동가입이 mail.tm 임시메일로
    # 인증을 실제 시도하기 때문(즉시차단 모순 제거). 진짜 안 되는 사이트는 아래
    # fail_streak(연속 실패) 조건에서 걸러진다.
    tbr=str(s.get('technical_block_reason') or '')
    if any(k in tbr for k in ('폼을 찾지 못','글쓰기 폼','게시판ID')): return '글쓰기 폼 없음: '+tbr[:60]
    lfr=str(s.get('last_fail_reason') or '')
    if any(k in lfr for k in ('보안 차단','403','게시가 금지','권한이 없','권한 없','차단')): return '차단/권한없음: '+lfr[:50]
    if any(k in lfr for k in ('제출 거부','referer','token')): return '제출 거부(referer/token)'
    if int(s.get('fail_streak',0) or 0)>=FAIL_STREAK_DROP:
        return f'연속 실패 {s.get("fail_streak")}회'
    return ''

# 오류/안내 페이지·데모/샘플 사이트 판정 키워드(사이트 이름·게시판명 기준, 네트워크 호출 없음)
ERROR_PAGE_HINTS=('오류안내','오류 안내','에러페이지','에러 페이지','페이지를 찾을 수 없','존재하지 않는',
                  '삭제된 페이지','접근할 수 없','잘못된 접근','not found','error page','forbidden','access denied',
                  # 대표님 지시: 글 없음/삭제/이동 안내가 뜨면 발행 무의미 → 즉시 탈락
                  '글이 존재하지 않','존재하지 않습니다','삭제되었거나 이동','삭제 되었거나','게시물이 존재하지',
                  '게시글이 존재하지','원본글이 존재하지','이미 삭제된','삭제된 게시',
                  # 대표님 지시(2026-09-08): Cafe24 '다시 한번 확인해주세요 / 사라졌거나 변경' 안내 = 즉시 탈락
                  '다시 한번 확인해주세요','다시 한 번 확인','사라졌거나 다른 페이지','주소를 다시 확인','페이지로 변경되었')
DEMO_HOST_HINTS=('demo.','sample.','example.','sandbox.')   # 서브도메인 라벨(데모/샘플)
DEMO_HOST_EXACT=('demo.webtro.kr','demo.sir.kr','webtro.kr','www.webtro.kr','g5.demo.sir.kr')

def _is_error_or_demo_site(s):
    """실제 발행 대상이 아닌 사이트(오류안내 페이지·데모/샘플 사이트) 판정. 사유 반환(아니면 '')."""
    dom=_domain_of(str(s.get('site_url') or '')).lower()
    if dom in DEMO_HOST_EXACT or dom.startswith(DEMO_HOST_HINTS):
        return f'데모/샘플 사이트({dom})'
    raw=(str(s.get('name') or '')+' '+str(s.get('board_name') or '')).lower()
    for k in ERROR_PAGE_HINTS:
        if k.lower() in raw:
            return f'오류/안내 페이지({k})'
    return ''

def reconcile_sites():
    """사이트 목록 상시 최신화: '안 되는' 사이트를 목록에서 자동 삭제한다(대표님 요청 — 안 되는 건 남기지 않음).
       - 삭제 대상: 오류/데모 사이트, 영구차단(_site_permanent_block: 폼없음·차단·연속실패), 기존 rejected.
       - 보호: 실게시 검증 완료(verified_post_url) 사이트, 진행중(가입/검증 중) 사이트는 유지.
       - 삭제 도메인은 영구 탈락 목록에 기록(재발굴 차단). 삭제 시 sites.json 롤링 백업(.autodrop-bak)."""
    now=_kst_now().strftime('%Y-%m-%d %H:%M')
    kept=[]; removed=[]; dropped_doms=[]; locked=0
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            edr=_is_error_or_demo_site(s)
            # ★가짜 검증 무효화(2026-09-08 대표님 제보): gnuboard(board.php) verified_post_url인데
            #   wr_id가 없거나 0이면 실제 글이 안 올라간 것(목록/에러페이지로 튕긴 걸 성공 오판).
            #   → 검증 취소해서 발행 목록에서 빼고 재검증 유도(마짱 wr_id=0 등 가짜성공 제거).
            _vpu=str(s.get('verified_post_url') or '')
            if 'board.php' in _vpu and s.get('write_test_status')=='passed':
                _wm=re.search(r'[?&]wr_id=(\d+)',_vpu)
                if not _wm or int(_wm.group(1))<=0:
                    s['write_test_status']='failed'; s['verified_post_url']=''
                    s['last_fail_reason']='가짜 검증(글번호 없음) 무효화 — 실제 글 미등록. 재검증 필요'
            verified=str(s.get('verified_post_url') or '').startswith(('http://','https://'))
            # ★읽기제한 게시판 재검사(대표님 지시 2026-09-08 '의미없는 발행 안하도록'): 검증됐고
            #   아직 안 본 사이트는 발행글 URL을 비로그인으로 열어 실제 읽히는지 확인. 잇츠키친처럼
            #   포인트/권한 제한이면(글 올라가도 조회차단·SEO0) 검증취소+발행잠금+영구탈락(재발굴 차단).
            #   read_checked 플래그로 사이트당 1회만(reconcile 매번 HTTP 안 하게).
            if verified and not edr and not s.get('read_checked'):
                _rb=_post_read_block_reason(_vpu)
                s['read_checked']=True
                if _rb:
                    s['write_test_status']='failed'; s['verified_post_url']=''; s['permission']=False
                    s['last_fail_reason']=f'읽기제한 게시판 — {_rb}(글 올라가도 조회차단·SEO0)'
                    removed.append(s); dropped_doms.append(_domain_of(s.get('site_url','')))
                    _purge_site_history(s)
                    add_log(f'[읽기제한 삭제] {(s.get("name") or s.get("site_url",""))[:24]} — {_rb}','정리')
                    continue
            # ★대표님이 직접 계정 넣어 추가한 사이트(manual_admin) 처리.
            #   원칙: 일시적 실패(타임아웃·도배방지)로는 삭제/잠금하지 않고 보호(계정·설정 유지).
            #   단 '진짜 안 되는 것'(비일시적 실패 15회+ & 검증URL 없음)은 대표님 지시로 아예 삭제.
            if s.get('registration_source')=='manual_admin' and not edr:
                if s.get('status')=='rejected': s['status']='idle'   # 재시도 가능하게 상태 완화
                _fs=int(s.get('fail_streak',0) or 0)
                _lfr=str(s.get('last_fail_reason') or '')
                # classify_fail 3번째 반환값=일시적 여부. 타임아웃/도배방지면 True(보호).
                try: _temp=classify_fail(_lfr)[2]
                except Exception: _temp=False
                _has_verified=str(s.get('verified_post_url') or '').startswith(('http://','https://'))
                # ★안 되는 사이트 아예 삭제(대표님 지시 2026-09-08): 비일시적 실패 15회+ & 검증 안 됨.
                #   (게시판못찾음·차단·로그인실패 등. 타임아웃 같은 일시적 실패는 아래 잠금으로만.)
                if _fs>=FAIL_STREAK_LOCK and not _temp and not _has_verified:
                    removed.append(s); dropped_doms.append(_domain_of(s.get('site_url','')))
                    _purge_site_history(s)   # 결과탭의 그 사이트 failed/queued 이력 정리(도배 제거)
                    continue
                # 일시적 실패로 15회+면 삭제 대신 잠금만(계정·설정 유지, 원인 해소 후 재허용).
                if _fs>=FAIL_STREAK_LOCK and s.get('permission'):
                    s['permission']=False
                    s['auto_drop_reason']=f'연속 실패 {s.get("fail_streak")}회(일시적) — 발행 잠금(원인 확인 후 재허용)'
                    s['auto_dropped_at']=now; locked+=1
                kept.append(s); continue
            # 검증된 사이트는 오류/데모가 아닌 한 보호(일시 실패로 삭제 안 함)
            if verified and not edr:
                if int(s.get('fail_streak',0) or 0)>=FAIL_STREAK_DROP+2 and s.get('permission'):
                    s['permission']=False; s['auto_drop_reason']=f'검증됨이나 연속 실패 {s.get("fail_streak")}회 — 발행 잠금'
                    s['auto_dropped_at']=now; locked+=1
                kept.append(s); continue
            # 안 되는 사이트(오류/데모·기존 rejected·영구차단) → 목록에서 삭제
            reason=edr or ('기존 탈락' if s.get('status')=='rejected' else _site_permanent_block(s))
            if reason:
                removed.append(s); dropped_doms.append(_domain_of(s.get('site_url','')))
            else:
                kept.append(s)   # 진행중(가입/검증 중) 등은 유지
        if removed or locked:
            if removed:
                try:
                    import shutil; shutil.copy(str(SITES_FILE),str(SITES_FILE)+'.autodrop-bak')
                except Exception: pass
            save_sites(kept)
    if dropped_doms:   # 삭제 도메인은 영구 탈락 목록에 기록(재발굴 방지)
        add_rejected_domains(dropped_doms,'안 되는 사이트 자동삭제')
    if removed: add_log(f'[자동정리] 안 되는 사이트 {len(removed)}개 자동삭제(발행가능·진행중만 유지)','정리')
    return len(removed)
    return changed

def purge_dead_sites(confirm=False):
    """'진짜 되는 사이트(실게시 검증)'만 남기고 안 되는 사이트를 목록에서 삭제한다.
    - 되는 것 판정: is_autopostable (permission + status!=rejected + write_test_status=passed
      + verified_post_url http). 이게 아니면 삭제 대상.
    - confirm=False면 삭제 예정 목록만 반환(dry-run). True면 백업 후 실제 삭제.
    - 안전장치: 되는 게 0개면 전멸 방지로 중단. 삭제 전 원본 백업(복원 가능).
    이 함수는 스케줄러/자동호출에 연결하지 않는다 — 관리자 버튼으로만 실행."""
    with POST_LOCK:
        sites=load_sites()
        keep=[s for s in sites if is_autopostable(s)]
        dead=[s for s in sites if not is_autopostable(s)]
        dead_info=[{'name':s.get('name') or s.get('site_url',''),'url':s.get('site_url',''),
                    'status':s.get('status',''),'reason':('rejected' if s.get('status')=='rejected'
                    else 'failed' if s.get('status')=='failed' else '미검증(실게시 검증 안됨)')} for s in dead]
        if not confirm:
            return {'ok':True,'dry_run':True,'keep':len(keep),'dead':len(dead),'dead_sites':dead_info}
        if not keep:
            return {'ok':False,'error':'되는 사이트가 0개 — 전멸 방지로 삭제 중단'}
        if not dead:
            return {'ok':True,'deleted':0,'keep':len(keep),'message':'삭제할 사이트 없음'}
        # 백업(복원 가능하게 원본 파일 복사)
        try:
            import shutil
            bak=str(SITES_FILE)+'.purge-bak-'+_kst_now().strftime('%Y%m%d-%H%M%S')
            shutil.copy(str(SITES_FILE),bak)
        except Exception: bak=''
        save_sites(keep)
        try: add_rejected_domains([_domain_of(s.get('site_url','')) for s in dead],'사이트 정리 삭제')
        except Exception: pass
        add_log(f'[사이트 정리] 안 되는 {len(dead)}개 삭제 · 되는 {len(keep)}개 유지 (백업: {bak.split("/")[-1] if bak else "실패"})','정리')
    return {'ok':True,'deleted':len(dead),'keep':len(keep),'backup':bak,'dead_sites':dead_info}

def start_workers(n=2):
    global wk_active,wk_paused
    if wk_active: return
    # ★병렬 발행(대표님 지시 '속도가 생명'): 워커 여러 개로 '서로 다른' 사이트 동시 발행.
    #   같은 사이트 동시발행은 _site_lock으로 방지하므로 n=1 강제 제거. 단 크롬 N개=메모리라 상한 6.
    #   (기존 n=1은 과보호였음 — under_min_interval/under_daily_limit이 이미 동일사이트 도배 막음.)
    try: n=max(1,min(6,int(n or 2)))
    except Exception: n=2
    wk_active=True; wk_paused=False; add_log(f'[워커] {n}개 시작 (병렬 발행, 사이트별 락 보호)')
    for i in range(n):
        t=threading.Thread(target=worker_loop,name=f'W-{i+1}',daemon=True); t.start()

def stop_workers():
    global wk_active; wk_active=False; add_log('[워커] 정지')

def pause_workers():
    global wk_paused; wk_paused=True; add_log('[워커] 일시정지')

def resume_workers():
    global wk_paused; wk_paused=False; add_log('[워커] 재개')

def worker_loop():
    while wk_active:
        # 일시정지: 큐에서 꺼내지 않고 대기 (작업 보존)
        if wk_paused: time.sleep(1); continue
        try: job=post_queue.get(timeout=1)
        except: continue
        site=job['site']; title=job['title']; content=job['content']; cfg=load_config()
        job_id=job.get('job_id'); hid=job.get('hist_id')
        name=site.get('name') or site.get('site_url','')[:24]

        # 큐 등록 뒤 사이트가 삭제/미허용/캡차 상태로 바뀌어도 발행되지 않도록 현재 상태 재검증.
        current=next((s for s in load_sites() if s.get('id')==site.get('id')),None)
        if not current or not is_publishable(current):
            why='사이트 삭제됨' if not current else '관리자 허용 또는 실게시 검증 조건 미충족'
            add_log(f'[스킵] {name} {why}')
            if hid: history_update(hid,status='skipped',message=why)
            if job_id: _persist_remove(job_id)
            with STATS_LOCK:
                wk_stats['skipped']+=1; wk_stats['done']+=1; wk_stats['queued']=post_queue.qsize()
            continue
        site=current; job['site']=current

        # ★같은 사이트를 다른 워커가 이미 발행 중이면(락 점유) 이 잡을 큐 뒤로 넘기고 다른 잡 처리.
        #   서로 다른 사이트는 병렬 진행 → 12곳이 워커 수만큼 동시에 발행됨(대표님 '속도가 생명').
        _slk=_site_lock(site.get('id'))
        if not _slk.acquire(blocking=False):
            post_queue.put(job)          # 뒤로 돌려 다른 워커/다음 차례에 처리
            time.sleep(0.3); continue
        try:
            _do_publish_job(job,site,cfg,name,job_id,hid)
        finally:
            _slk.release()

def _do_publish_job(job,site,cfg,name,job_id,hid):
        """한 발행 잡을 실제 처리(한도·간격 확인 → 발행 → 재시도/기록). 사이트별 락 안에서 호출됨."""
        title=job['title']; content=job['content']
        # 1일 한도 확인 (도배 방지)
        if not under_daily_limit(site,cfg):
            add_log(f'[스킵] {name} 일일 발행 한도 도달')
            if hid: history_update(hid,status='skipped',message='일일 발행 한도 도달')
            if job_id: _persist_remove(job_id)
            with STATS_LOCK:
                wk_stats['skipped']+=1; wk_stats['done']+=1; wk_stats['queued']=post_queue.qsize()
            return

        interval_ok,remain=under_min_interval(site)
        if not interval_ok:
            mins=max(1,(remain+59)//60)
            add_log(f'[스킵] {name} 최소 발행 간격 미충족 ({mins}분 남음)')
            if hid: history_update(hid,status='skipped',message=f'사이트 최소 발행 간격 미충족 ({mins}분 남음)')
            if job_id: _persist_remove(job_id)
            with STATS_LOCK:
                wk_stats['skipped']+=1; wk_stats['done']+=1; wk_stats['queued']=post_queue.qsize()
            return

        if hid: history_update(hid,status='posting')
        # 발행 (실패 시 3회 재시도, 지수 백오프 + 드라이버 자동 재시작)
        ok=False; msg=''
        for attempt in range(1,4):
            try:
                ok,msg=do_post(site,title,content)
                if ok: break
                add_log(f'[재시도 {attempt}/3] {name} - {msg}')
                reset_driver()   # 실패 시 드라이버 새로 띄워 세션 꼬임 방지
            except Exception as e:
                msg=str(e); add_log(f'[재시도 {attempt}/3] {name} - {msg[:60]}')
                reset_driver()   # 크롬 죽었을 때 자동 재시작
            if not wk_active: break
            if attempt<3 and not wk_paused: time.sleep(min(5*attempt,15))

        # 실패 원인 분류 + 일시적 실패는 지연 후 자동 재시도
        reason=reason_ko=''; transient=False
        if not ok:
            reason,reason_ko,is_temp=classify_fail(msg)
            requeues=job.get('requeues',0)
            transient=is_temp and requeues<RETRY_MAX and wk_active
            if transient:
                job['requeues']=requeues+1
        if transient:
            with STATS_LOCK:
                wk_stats['retry']+=1; wk_stats['queued']=post_queue.qsize()
            if hid: history_update(hid,status='retry',fail_reason=reason,fail_reason_ko=reason_ko,
                                   message=f'{reason_ko} → {RETRY_DELAY//60}분 후 자동 재시도 ({job["requeues"]}/{RETRY_MAX}): {str(msg)[:150]}')
            schedule_retry(job)   # queue.json 은 그대로 유지(재시작 시 복구)
            add_log(f'[재시도 예약 {job["requeues"]}/{RETRY_MAX}] {name} - {reason_ko}')
            if cfg.get('notify_fail'): send_telegram(cfg,f'🔄 일시적 실패({reason_ko}) 재시도 예약: {name}')
            return
        with STATS_LOCK:
            if ok: wk_stats['success']+=1
            else: wk_stats['fail']+=1
            wk_stats['done']+=1; wk_stats['queued']=post_queue.qsize()
        finalize_post(site,ok,fail_reason=('' if ok else str(msg)))
        # CAPTCHA 값은 관리자 수동 입력만 허용하며 자동 판독/우회하지 않는다.
        if hid: history_update(hid,status='done' if ok else 'failed',
                               result_url=(msg if ok and str(msg).startswith('http') else ''),
                               fail_reason=('' if ok else reason),
                               fail_reason_ko=('' if ok else reason_ko),
                               alive=('yes' if ok and str(msg).startswith('http') else ''),
                               message=str(msg)[:300])
        if job_id: _persist_remove(job_id)
        add_log(f'[{"성공" if ok else "실패:"+reason_ko}] {name}')
        # 텔레그램 알림 (설정에 따라)
        if ok and cfg.get('notify_done'): send_telegram(cfg,f'✅ 발행 성공: {name}\n{title[:60]}')
        if (not ok) and cfg.get('notify_fail'): send_telegram(cfg,f'❌ 발행 실패({reason_ko}): {name}\n{str(msg)[:120]}')

        # rate limit: 포스트 간 지연 (도배 방지)
        delay=int(cfg.get('post_delay',30) or 0)
        if delay>0 and wk_active and not wk_paused: time.sleep(delay)

def is_permitted(site):
    """사이트 관리에서 등록 상태로 전환된 사이트만 발행."""
    return bool(site.get('permission')) and site.get('registration_source') in ('manual_admin','admin_bulk','legacy_admin','candidate_registered','verified_test')

def is_autopostable(site):
    """자동발행 대상 = 허용 + 실제 게시 성공 URL 검증 완료.
       (CAPTCHA는 2captcha로 자동 해결 시도 → 실패 시 수동 대기)"""
    verified_url=str(site.get('verified_post_url') or '')
    # 로그인 필요 사이트인데 로그인 정보(mb_id)가 없으면 발행 제외 — 가입 미완료 사이트가
    # 발행 시도돼 failed 나는 것 방지(대표님 지시 2026-09-06). 비회원 글쓰기(login_required=False)는 영향 없음.
    if site.get('login_required') and not str(site.get('mb_id') or '').strip():
        return False
    # 오류안내/데모 사이트는 검증됐어도 발행 대상에서 즉시 제외(reconcile이 곧 영구 탈락 처리).
    if _is_error_or_demo_site(site):
        return False
    # ★비밀글 강제 게시판 제외(대표님 지시 2026-09-11): 글이 비밀글로만 올라가면 구글이 못읽어 SEO0.
    #   한 번 비밀글로 발행된 게 확인되면 secret_forced 표시 → 더는 발행 안 함(헛발행 방지).
    if site.get('secret_forced'):
        return False
    return (is_permitted(site)
            and site.get('status')!='rejected'
            and site.get('write_test_status')=='passed'
            and verified_url.startswith(('http://','https://')))

def is_assisted_postable(site):
    """보조발행 대상 = 허용 + 실제 게시 성공 URL 검증 완료."""
    verified_url=str(site.get('verified_post_url') or '')
    return (is_permitted(site)
            and site.get('status')!='rejected'
            and site.get('write_test_status')=='passed'
            and verified_url.startswith(('http://','https://')))

def is_publishable(site):
    """서버가 발행해도 되는 사이트. ★cafe24는 노드가 살아있으면 서버 발행 제외(2026-09-11: 워크룸이 enqueue를 안 거치고
       직접 발행해 takago에 계속 서버 발행이 붙었음 — enqueue 필터만으론 부족해 여기서 막는다)."""
    return is_autopostable(site) and not _cafe24_node_only(site)

def _cafe24_node_only(site):
    """Cafe24 사이트는 PC 노드가 살아 있으면 노드(집 IP 로컬크롬)가 발행 — 서버(DC IP)는 Turnstile/CF에 막혀
       실패만 쌓고 사이트를 잠가버렸음(hbbiomall 2026-09-11). 서버 큐에서는 제외, 노드 claim-sites가 가져간다."""
    try: return site.get('platform')=='cafe24' and _pc_node_alive()
    except Exception: return False

def enqueue(sites,title,content,meta=None):
    meta=meta or {}
    allowed=[s for s in sites if is_publishable(s) and not _cafe24_node_only(s)]
    blocked=[s for s in sites if not (is_publishable(s) and not _cafe24_node_only(s))]
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for s in allowed:
        jid=secrets.token_hex(8)
        history_add({'id':jid,'time':now,'updated':now,'site_id':s.get('id'),
                     'site_name':s.get('name') or s.get('site_url',''),'site_url':s.get('site_url',''),
                     'bo_table':s.get('bo_table',''),'title':title,
                     'region':meta.get('region',''),'service':meta.get('service',''),
                     'status':'queued','result_url':'','message':'','attempts':0})
        job={'job_id':jid,'hist_id':jid,'site':s,'title':title,'content':content}
        post_queue.put(job); _persist_add(job)
    for s in blocked:
        if _cafe24_node_only(s): continue   # Cafe24는 노드가 발행 — 서버 스킵은 정상이라 로그 안 남김
        _why=('미허용 도메인' if not is_permitted(s) else '제외')
        add_log(f'[차단:{_why}] 발행 스킵: {s.get("name") or (s.get("site_url","") or "")[:30]}')
    with STATS_LOCK:
        wk_stats['total']+=len(allowed); wk_stats['queued']=post_queue.qsize()
    return len(allowed),len(blocked)

def enqueue_generated(sites, keywords, cfg, meta=None):
    """허용 사이트마다 '각각 다른' 유니크 제목·본문을 새로 생성해 큐 등록.
       → 같은 키워드라도 사이트마다 글이 달라져 중복 발행을 방지."""
    meta=meta or {}
    allowed=[s for s in sites if is_publishable(s) and not _cafe24_node_only(s)]
    blocked=[s for s in sites if not (is_publishable(s) and not _cafe24_node_only(s))]
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for s in allowed:
        html,title=generate_article(keywords,cfg)   # 사이트마다 새로 생성(유니크)
        jid=secrets.token_hex(8)
        history_add({'id':jid,'time':now,'updated':now,'site_id':s.get('id'),
                     'site_name':s.get('name') or s.get('site_url',''),'site_url':s.get('site_url',''),
                     'bo_table':s.get('bo_table',''),'title':title,
                     'region':meta.get('region',''),'service':meta.get('service',''),
                     'workroom_id':meta.get('workroom_id',''),'workroom_name':meta.get('workroom_name',''),
                     'member':meta.get('member',''),
                     'status':'queued','result_url':'','message':'','attempts':0})
        job={'job_id':jid,'hist_id':jid,'site':s,'title':title,'content':html}
        post_queue.put(job); _persist_add(job)
    for s in blocked:
        if _cafe24_node_only(s): continue   # Cafe24는 노드가 발행 — 서버 스킵은 정상이라 로그 안 남김
        _why=('미허용 도메인' if not is_permitted(s) else '제외')
        add_log(f'[차단:{_why}] 발행 스킵: {s.get("name") or (s.get("site_url","") or "")[:30]}')
    with STATS_LOCK:
        wk_stats['total']+=len(allowed); wk_stats['queued']=post_queue.qsize()
    return len(allowed),len(blocked)

# ==================== 예약 발행 스케줄러 ====================
def load_scheds(): return load_json(SCHED_FILE,[])
def save_scheds(s): save_json(SCHED_FILE,s)

def schedule_keyword_key(kw):
    """예약별 키워드 진행률을 재시작 후에도 동일하게 식별한다."""
    return '|'.join(str((kw or {}).get(k,'') or '').strip() for k in ('지역','서비스','브랜드'))

def _kst_now():
    """서버가 UTC여도 한국시간 기준으로 계산."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Seoul'))
    except Exception:
        return datetime.utcfromtimestamp(time.time()+9*3600)

# ==================== 회원(고객) 관리 + 월 정산 ====================
def load_members(): return load_json(MEMBERS_FILE,[])
def save_members(m): save_json(MEMBERS_FILE,m)
def _cur_month(): return _kst_now().strftime('%Y-%m')

def member_fee(m):
    """월 청구액 = 기본료 + (추가 광고수 × 추가단가)."""
    base=int(m.get('plan_fee',30000) or 0)
    addons=int(m.get('addons',0) or 0)
    afee=int(m.get('addon_fee',10000) or 0)
    return base+addons*afee

def member_view(m):
    """회원 1건을 정산정보 포함해 표시용으로 반환."""
    cm=_cur_month(); pays=m.get('payments',{}) or {}
    pm=pays.get(cm,{}) if isinstance(pays,dict) else {}
    fee=member_fee(m)
    return {**m,'fee':fee,'this_month':cm,
            'paid':bool(pm.get('paid')),'paid_at':pm.get('paid_at',''),
            'unpaid_months':[k for k,v in pays.items() if not (v or {}).get('paid')] if isinstance(pays,dict) else []}

# ==================== 도메인 발굴 (구글 검색 → 자동검수 → 승인 대기) ====================
def load_cands():
    """후보 데이터 로드. 더 이상 사용하지 않는 연락처 정보는 영구 제거한다."""
    cands=load_json(CAND_FILE,[])
    changed=False
    for cand in cands:
        if cand.pop('emails',None) is not None: changed=True
    if changed: save_json(CAND_FILE,cands)
    return cands
def save_cands(c): save_json(CAND_FILE,c)
_cand_lock=threading.RLock()   # 재진입 가능(add_candidates_from 안에서 remove_rejected_domain 호출 등 중첩 허용)

# ---- 영구 탈락 도메인(다음 발굴에서 제외) ----
def load_rejected_domains():
    """영구 탈락 도메인 집합. 탈락 후보를 삭제해도 재수집되지 않게 한다."""
    d=load_json(REJECTED_DOMAINS_FILE,{})
    if isinstance(d,list): d={'domains':d}
    return set(x.lower() for x in (d.get('domains') or []))

def add_rejected_domains(domains, reason=''):
    """도메인들을 영구 탈락 목록에 추가(사유·시각 기록)."""
    if isinstance(domains,str): domains=[domains]
    doms=[(_domain_of(x) if x.startswith('http') else x).lower().replace('www.','') for x in domains if x]
    doms=[d for d in doms if d]
    if not doms: return 0
    with _cand_lock:
        raw=load_json(REJECTED_DOMAINS_FILE,{})
        if isinstance(raw,list): raw={'domains':raw,'log':[]}
        cur=set(x.lower() for x in (raw.get('domains') or []))
        log=raw.get('log') or []
        now=_kst_now().strftime('%Y-%m-%d %H:%M')
        added=0
        for d in doms:
            if d not in cur:
                cur.add(d); log.append({'domain':d,'reason':str(reason)[:80],'at':now}); added+=1
        raw['domains']=sorted(cur); raw['log']=log[-2000:]
        save_json(REJECTED_DOMAINS_FILE,raw)
    return added

def remove_rejected_domain(dom):
    """영구 탈락 목록에서 도메인 제거(수동 재활성화 시). 제거되면 True."""
    dm=(_domain_of(dom) if str(dom).startswith('http') else str(dom)).lower().replace('www.','')
    if not dm: return False
    with _cand_lock:
        raw=load_json(REJECTED_DOMAINS_FILE,{})
        if isinstance(raw,list): raw={'domains':raw,'log':[]}
        cur=set(x.lower() for x in (raw.get('domains') or []))
        if dm not in cur: return False
        cur.discard(dm); raw['domains']=sorted(cur)
        save_json(REJECTED_DOMAINS_FILE,raw)
    return True

# 쿼리 조합 — 플랫폼 흔적 × 홍보 의도
GNU_PATTERNS=['inurl:bbs/board.php bo_table=promotion','inurl:bbs/board.php bo_table=hongbo',
              'inurl:bbs/board.php bo_table=ad','inurl:bbs/board.php bo_table=link',
              'inurl:bbs/board.php bo_table=partner','inurl:bbs/board.php bo_table=banner',
              'inurl:bbs/write.php bo_table=promotion','inurl:bbs/board.php "홍보게시판"']
CAFE_PATTERNS=['inurl:/board/ list.html "홍보"','inurl:/board/free/ "홍보"','"cafe24" inurl:/board/ "제휴"']
INTENT=['"홍보게시판"','"자유홍보"','"홍보 환영"','"홍보 가능"','"링크등록"','"제휴문의"','"상호등록"','"업체등록"']
# 제외 도메인(포털·정부·언론·대형)
BLACK_DOMAINS=['naver.com','daum.net','google.','youtube.','facebook.','instagram.','tistory.com',
               'blog.','cafe.','.go.kr','.or.kr','.ac.kr','.mil.kr','wikipedia.','namu.wiki',
               'chosun.com','donga.com','joins.com','hani.co.kr','mk.co.kr','news','gov',
               'coupang.com','11st.co.kr','gmarket.co.kr','auction.co.kr','interpark',
               'twseo.kr','marketingmonster.kr']
# ★기업 소개성 게시판 제외(대표님 지시 2026-09-11 '다트미디어 CEO 인사말 등록은 아니다'): 회사 정체성 페이지
#   (CEO인사말·회사소개·연혁·조직도·오시는길)에 유흥 홍보글 = 즉시 삭제·신고 위험·SEO 0. 스팸 흔적이 있어도 탈락.
#   ※채용공고·공지사항·업체등록(company)·가입인사는 대표님 황금패턴/개방게시판이라 제외하지 않는다.
CORP_BOARD_TITLE=['ceo 인사말','ceo인사말','ceo 메시지','대표 인사말','대표인사말','인사말','회사소개','회사 소개','기업소개',
                  '기업 소개','회사연혁','회사 연혁','연혁','조직도','오시는길','오시는 길','찾아오시는','회사개요','경영이념',
                  'ci소개','ci 소개','greeting','company profile','about us']
CORP_BOARD_TABLE=['ceo','greeting','greetings','aboutus','about_us','history','organization','org_chart','location',
                  'vision','philosophy','ci']
AD_BAN_WORDS=['광고 금지','광고금지','홍보 금지','홍보금지','상업적 게시물','상업적게시물','광고성 글 삭제',
              '광고글 삭제','도배 금지','스팸 금지','영리 목적','상업적 목적 금지','무단 홍보']
# 주차/만료 도메인 (검색엔진엔 남아있지만 실제론 껍데기)
PARKED_WORDS=['resources and information','this domain','domain is for sale','도메인 판매',
              '이 도메인은','buy this domain','parked','sedoparking','afternic','dan.com',
              'hugedomains','도메인이 만료','관련 검색어','sponsored listings','related searches']
# 불법·도박·성인 사이트 (후보 부적합 — 제휴 대상 아님)
ILLEGAL_WORDS=['카지노','바카라','슬롯','토토','먹튀','배팅','베팅','도박','홀덤','파워볼',
               '사설','환전','꽁머니','livecasino','casino','baccarat','betting']
PROMO_WORDS=['홍보게시판','자유홍보','홍보 환영','홍보가능','홍보 가능','제휴문의','제휴 문의','링크등록',
             '업체등록','상호등록','광고게시판','홍보하기','업체홍보','파트너 모집']
# ★업자 홍보 흔적 키워드(대표님 지시 2026-09-11): 게시판 글에 이런 단어가 있으면 이미 유흥·마사지
#   업자들이 홍보 중인 '방치·개방 게시판'일 확률↑ = 우리 글도 잘 올라가고 색인 잘 됨(발굴 우선순위↑).
#   ★부분일치 주의(2026-09-11 실측 버그): '라인'⊂'온라인', '텔레'⊂'텔레비전', '안마'⊂'안마의자', '출장'⊂'출장비'
#   → 일반 쇼핑몰 홈이 biz=7로 잡혀 빡센검수(promo_hint)를 우회했음. 단독 단어 대신 홍보글 특유의 구문으로.
BIZ_PROMO_WORDS=['찌라시','치라시','텔레그램','telegram','텔레 문의','텔레문의','텔레 상담','텔레아이디',
                 '라인 문의','라인문의','라인 상담','라인상담','라인아이디','라인 아이디','line 문의','line상담',
                 '마사지','건마','스웨디시','1인샵','왁싱','노래방','가라오케','하이퍼블릭',
                 '쓰리노','셔츠룸','풀싸롱','유흥','출장마사지','출장안마','오피','키스방','휴게텔']

def _domain_of(url):
    m=re.match(r'https?://([^/]+)',url or '')
    return (m.group(1).lower() if m else '').replace('www.','')

def _excluded_domain_list(cfg=None):
    """설정(excluded_domains)의 사용자 지정 제외 도메인 목록. 한 줄에 하나(#주석 무시)."""
    try:
        cfg=cfg or load_config()
        return [x.strip().lower() for x in str(cfg.get('excluded_domains','') or '').splitlines()
                if x.strip() and not x.lstrip().startswith('#')]
    except Exception:
        return []

def _is_blacklisted(url):
    u=(url or '').lower()
    if any(b in u for b in BLACK_DOMAINS): return True
    # 사용자 지정 제외 도메인(웹빌더 플랫폼 등, isweb.co.kr 같은) — 설정에서 관리.
    return any(b in u for b in _excluded_domain_list())

# 그누보드 홍보/자유 게시판에서 흔한 bo_table 값 (URL 조각으로 직접 검색)
GNU_BO_TABLES=['promotion','promotion1','hongbo','hongbo1','ad','link','partner','banner',
               'free','free1','guest','company','pr','event','notice_pr']
# Brave에 잘 먹히는 '평문 URL조각' — inurl: 대신 실제 경로 문자열을 그대로 검색
# (홍보·제휴·자유게시판 계열 bo_table을 넓게 커버 — 매일 우선 실행되므로 다양할수록 좋다)
# '바로 발행되는 500곳' 목표 — 비회원(로그인 없이) 글쓰기 게시판을 앞쪽에 우선 배치한다.
# write.php로 바로 글쓰기 가능하고 '비회원/누구나/회원가입 없이' 신호가 있는 게시판을 노림.
BRAVE_URL_FRAGMENTS=['bbs/write.php bo_table=promotion 비회원','bbs/write.php bo_table=free 비회원',
                     'bbs/board.php 비회원 글쓰기 홍보','bbs/board.php 회원가입 없이 글쓰기',
                     'bbs/board.php 누구나 글쓰기 홍보게시판','bbs/write.php 비회원 홍보 환영',
                     # 전략1: 광고허용·비회원 글쓰기 확실한 긍정 신호 강화
                     'bbs/board.php 자유홍보 게시판 글쓰기','bbs/board.php 업체등록 무료 홍보',
                     'bbs/board.php 광고 환영 비회원','bbs/board.php 홍보 자유롭게 게시판',
                     'bbs/write.php bo_table=ad 비회원','bbs/write.php bo_table=partner 홍보',
                     'bbs/board.php bo_table=promotion','bbs/board.php bo_table=hongbo',
                     'bbs/board.php bo_table=link','bbs/board.php bo_table=partner',
                     'bbs/board.php bo_table=ad','bbs/board.php bo_table=banner',
                     'bbs/board.php bo_table=pr','bbs/board.php bo_table=company',
                     'bbs/board.php bo_table=guest','bbs/board.php bo_table=event',
                     'bbs/write.php bo_table=promotion','bbs/write.php bo_table=hongbo',
                     'bbs/board.php bo_table=free 홍보','bbs/board.php 홍보게시판 글쓰기',
                     'bbs/board.php 자유게시판 홍보 환영','그누보드 홍보게시판 비회원 글쓰기',
                     # 전략2: '이미 홍보글이 올라와 색인된' 게시판(=글 잘 올라가고 색인 빠른 곳) 역추적.
                     #  검색에 나온다는 것 자체가 색인됨을 뜻하고, 010/문의/후기 홍보글이 있으면 실제 등록 가능.
                     'bbs/board.php 010 문의 홍보게시판','bbs/board.php 노래방 홍보 글쓰기',
                     'bbs/board.php 마사지 홍보 게시판 비회원','bbs/board.php 유흥 홍보 게시판 등록',
                     'bbs/board.php 출장 문의 게시판 글쓰기','bbs/board.php 업소 홍보 게시판 010',
                     'bbs/board.php 후기 이벤트 홍보 등록','bbs/board.php 지역 홍보 게시판 자유',
                     # 전략3: 활발한(=색인 빠른) 게시판 신호 — 최근/오늘 등록·조회 많은 홍보 게시판.
                     'bbs/board.php 오늘 등록 홍보 게시판','bbs/board.php 실시간 홍보 자유게시판',
                     'bbs/board.php 광고 게시판 무료 등록 비회원','bbs/board.php 링크 홍보 게시판 누구나']
# 카페24 게시판 찾기 조각 — 카페24로 만든 사이트(쇼핑몰·회사홈)의 자유/홍보 게시판을 겨냥.
# 경로: /board/free/list.html, /board/write.html?board_no=, /article/... 등. 그누보드와 동등하게 강화.
CAFE24_FRAGMENTS=['board/free/list.html 홍보','board/free/write.html 비회원','board write.html 자유게시판 홍보',
                  'board_no 자유게시판 홍보 글쓰기','cafe24 자유게시판 비회원 글쓰기','cafe24 게시판 홍보 환영',
                  'board 홍보게시판 write.html 등록','cafe24 자유게시판 광고 가능','board/board.html 홍보 비회원',
                  'article write 자유게시판 홍보','cafe24 커뮤니티 게시판 홍보 글쓰기','board/free 광고 환영 비회원',
                  'cafe24 쇼핑몰 자유게시판 글쓰기','cafe24 게시판 홍보 후기 등록','board write.html 비회원 홍보 환영',
                  'cafe24 자유게시판 업체등록 무료','cafe24 홍보게시판 010 문의','cafe24 게시판 광고 게시 가능',
                  'board list.html 자유 홍보 010','cafe24 자유게시판 링크 등록',
                  # ★황금사이트 패턴(2026-09-08 samjinvalve 분석·nimble 검증): 정상도메인+방치 개방게시판.
                  #   실측: 아래 검색어로 samjinvalve·jinaedeul·selenus 등 상위노출 사이트 무더기 적중.
                  'inurl:mod=document 홍보','inurl:mod=document 노래방','mod=document 홍보 게시판',
                  'article 상품-qa 홍보','article 상품문의 홍보 010','article qa 노래방 홍보',
                  'article 상품-사용후기 홍보','article voices-of-customers 홍보',
                  '채용공고 홍보 노래방','채용공고 010 유흥','공지사항 홍보 글쓰기 010',
                  'kboard 자유게시판 홍보','kboard mod=document 홍보',
                  # ★업자 홍보글 역추적(대표님 지시 2026-09-11): 찌라시·텔레·라인 키워드가 이미 올라온
                  #   게시판 = 업자들이 쓰는 개방 게시판 = 우리 글도 잘 붙는다. 그 형제 게시판 무더기 발굴.
                  'bbs/board.php 찌라시 텔레 문의','article 상품-qa 텔레 라인 문의',
                  'board list.html 찌라시 010 문의','bbs/board.php 라인 문의 마사지 010',
                  'article qa 텔레그램 노래방','mod=document 찌라시 유흥 010',
                  'bbs/board.php 텔레 상담 010 마사지','cafe24 게시판 라인 문의 노래방 010']

# ★Cafe24 게시판 '이름' × 업종 검색 (대표님 지시 2026-09-07):
#  이런 사이트들은 URL이 /article/상품-qa/ 처럼 '게시판 이름'을 담는다. 그래서 구글/Brave에
#  '상품 Q&A 노래방'처럼 (게시판명 + 업종)으로 검색하면 그 게시판에 이미 올라온 홍보글이
#  잡혀 → 같은 형제 게시판을 대량 역발굴한다. Cafe24 기본/흔한 게시판명을 폭넓게 넣는다.
# ★실데이터 빈도순(2026-09-11 후보URL 94종 실측, 대표님 지시 '상품 Q&A 외에도 많다'):
#   상품-qa 85 · 상품-사용후기 44 · 자유게시판 40 · 공지사항 18 · qa 15 · review/리뷰 19 · 문의게시판 6 ·
#   포토후기 5 · gallery 4 · 묻고답하기 4 … 잘 나오는 게시판명이 앞에 오도록 정렬하고 빠진 것 보강.
CAFE24_BOARD_NAMES=['상품 Q&A','상품 사용후기','자유게시판','공지사항','Q&A','REVIEW','리뷰',
                    '자유홍보게시판',          # 이름에 '홍보' — 개방 게시판 신호 강함
                    '문의게시판','포토후기','갤러리','묻고답하기','상품후기','상품문의','1:1 문의게시판',
                    '고객센터','구매후기','이용후기','사용후기','이벤트','이벤트게시판',
                    '자주묻는질문','FAQ','제휴문의','제휴도매문의','A/S 게시판','베스트후기',
                    '커뮤니티','질문답변','상품평','자유 게시판','자유 커뮤니티','게시판','NOTICE']
# 업종(홍보 대상) — 이 게시판명들과 곱해 검색. 지역 없이 업종만으로도 형제 게시판 역추적됨.
CAFE24_SVC_KEYWORDS=['노래방','마사지','출장마사지','가라오케','셔츠룸','룸싸롱','하이퍼블릭',
                     '쓰리노','출장안마','스웨디시','풀싸롱','텐프로','건마','안마']

def _cafe24_boardname_queries():
    """Cafe24 게시판명 × 업종 조합 검색어 — /article/게시판명/ 형태 형제 게시판 역발굴.
       ★Brave 실측(2026-09-11): '상품 Q&A + 업종 + 010 문의' 조합이 게시판글 20/20(100%) 적중.
       rapigencare식 /article/상품-qa/6/ 황금게시판 무더기 발굴. 이 조합을 맨 앞에 둔다."""
    qs=[]
    # 0) 100% 적중 조합 최우선: 게시판명 + 업종 + '010 문의'(업자 홍보글 역추적)
    #    ★업종-우선(interleave, 대표님 지시 2026-09-11 '상품 Q&A 외에도 많다'): 예전엔 '상품 Q&A×전업종'이
    #    먼저 다 돌아야 다음 게시판명으로 넘어가 초반 Brave 배치(30개)가 상품 Q&A에 편중됐다.
    #    업종을 바깥 루프로 두면 첫 배치부터 여러 게시판명(후기·자유·공지·리뷰…)을 골고루 훑는다.
    for sv in CAFE24_SVC_KEYWORDS:
        for bn in CAFE24_BOARD_NAMES:
            qs.append(f'{bn} {sv} 010 문의')       # 예: 상품 Q&A 노래방 010 문의 → 상품 사용후기 노래방 010 문의 …
    # 1) 게시판명 × 업종(기본형) + 홍보 역추적
    for bn in CAFE24_BOARD_NAMES:
        for sv in CAFE24_SVC_KEYWORDS:
            qs.append(f'"{bn}" {sv}')            # 예: "상품 Q&A" 노래방
        qs.append(f'"{bn}" 010 홍보')            # 홍보글 있는 게시판 역추적
        qs.append(f'article "{bn}" 010')          # cafe24 article URL + 게시판명
    return qs

# ★무인증(꿀사이트) 최우선 조각 — 실측상 유일하게 전환되는 유형.
#  ① 비회원 글쓰기(가입 자체가 없음=인증 불필요) ② 그누보드 기본가입(이메일인증 기본 OFF)
#  본인인증/실명인증 게시판은 auto_signup에서 조기 제외되므로 여기선 무인증 신호만 강하게 민다.
NO_VERIFY_FRAGMENTS=[
    # 가입 없이 바로 글쓰기 = 인증 원천 불필요 (최고 전환)
    'bbs/write.php 비회원 글쓰기 홍보','bbs/board.php 비회원 글작성 가능','회원가입 없이 글쓰기 게시판',
    'bbs/board.php 비회원도 글쓰기','누구나 글쓰기 게시판 로그인 없이','비회원 글쓰기 홍보게시판 그누보드',
    'bbs/write.php bo_table=free 비회원 글쓰기','bbs/write.php bo_table=promotion 비회원 글쓰기',
    # 가입은 하되 이메일/본인인증 없는 게시판 (그누보드 기본값)
    'bbs/register.php 이메일 인증 없이 가입','그누보드 회원가입 인증없이 바로',
    'bbs/board.php 가입 즉시 글쓰기 홍보','회원가입 바로 승인 글쓰기 게시판',
    'bbs/board.php 자유가입 홍보 글쓰기','간편가입 홍보게시판 글쓰기 그누보드',
    # 실제 홍보글(010)이 이미 올라와 있는 무관리 게시판 역추적 = 무인증·무검열 증거
    'bbs/board.php 010 홍보 글 비회원','bbs/board.php 광고 글 등록 비회원 환영',
]

def _board_finder_queries(provider):
    """플랫폼(그누보드/카페24) 홍보·자유 게시판을 '찾기 위한' 검색어.
       사용자의 지역×업종 목록(=경쟁사 검색)과 무관하게 항상 앞에 실행한다.
       ★무인증(비회원/기본가입) 게시판 조각을 맨 앞에 배치해 쿼리 한도 안에서 최우선 실행."""
    qs=[]
    if provider=='brave':
        # ★0순위(Brave 실측 100% 적중, 대표님 지시 2026-09-11 '타율 좋게'): Cafe24 게시판명×업종×010
        #   = rapigencare식 /article/상품-qa/ 황금게시판. Brave 토큰(비용) 최우선 소비 대상.
        qs.extend(_cafe24_boardname_queries())
        # 1) 무인증 — 실측상 유일 전환 유형(비회원/이메일인증 없는 가입)
        for frag in NO_VERIFY_FRAGMENTS:
            qs.append(frag)
            for i in INTENT[:2]:
                qs.append(f'{frag} {i}')
        # 그누보드: 평문 URL조각 + 홍보의도 (라이브 검증: 실제 홍보게시판 10/10 적중)
        for frag in BRAVE_URL_FRAGMENTS:
            qs.append(frag)
            for i in INTENT[:3]:
                qs.append(f'{frag} {i}')
        for bo in GNU_BO_TABLES:
            qs.append(f'bbs/board.php bo_table={bo} 홍보')
        for i in INTENT:
            qs.append(f'{i} bbs board.php 글쓰기')
            qs.append(f'{i} 그누보드 게시판')
        # 카페24: 게시판 경로 조각 + 홍보/비회원 신호(그누보드와 동등하게 강화 — 카페24 사이트 대량 커버)
        for frag in CAFE24_FRAGMENTS:
            qs.append(frag)
            for i in INTENT[:2]:
                qs.append(f'{frag} {i}')
        # (Cafe24 게시판명×업종 쿼리는 위 0순위에서 이미 최우선 추가됨)
    else:
        # Google: inurl: 연산자가 강력 — 무인증(비회원 글쓰기) 신호를 맨 앞에.
        qs.append('inurl:bbs/write.php 비회원 글쓰기')
        qs.append('inurl:bbs/board.php 회원가입 없이 글쓰기')
        qs.append('inurl:bbs/board.php 비회원 홍보 글쓰기')
        qs.append('inurl:bbs/board.php 누구나 글쓰기 홍보')
        for p in GNU_PATTERNS+CAFE_PATTERNS:
            qs.append(p)
            for i in INTENT[:4]:
                qs.append(f'{p} {i}')
        for i in INTENT:
            qs.append(f'{i} inurl:bbs')
            qs.append(f'{i} inurl:board')
        # ★Cafe24 게시판명 × 업종 (대표님 지시)
        qs.extend(_cafe24_boardname_queries())
    # ★검색어 대폭 확장(대표님 지시 '무제한'): 지역 × 업종 × 게시판신호 조합을 뒤에 붙여 수천 개로.
    #   앞쪽(무인증·플랫폼조각)이 최우선이고, PC 커서 로테이션이 뒤쪽까지 순회하며 신규 발굴 지속.
    qs.extend(_region_service_queries())
    return list(dict.fromkeys(qs))   # 중복 제거(순서 유지)

# 전국 지역(시·구 단위 대표) × 유흥/마사지 업종 — 게시판 홍보글 역발굴용 대량 조합.
_FINDER_REGIONS=['서울','강남','강북','수원','인천','부천','성남','안양','부평','일산','분당','천안','아산',
                 '청주','대전','대구','부산','서면','해운대','광주','울산','창원','전주','제주','평택','안산',
                 '김포','파주','동탄','용인','원주','포항','구미','경주','목포','순천','강릉','춘천','세종']
_FINDER_SVCS=['노래방','가라오케','셔츠룸','룸싸롱','하이퍼블릭','쓰리노','풀싸롱','텐프로','다국적노래방',
              '마사지','출장마사지','출장안마','스웨디시','건마','타이마사지','1인샵','왁싱']
_FINDER_SIGNALS=['bbs/board.php 홍보','게시판 010 홍보','자유게시판 후기','article 홍보','mod=document 홍보']

def _region_service_queries():
    """지역×업종×게시판신호 대량 조합 검색어. (예: '강남 하이퍼블릭 bbs/board.php 홍보')"""
    qs=[]
    for rg in _FINDER_REGIONS:
        for sv in _FINDER_SVCS:
            qs.append(f'{rg}{sv} 홍보 게시판')            # 예: 강남하이퍼블릭 홍보 게시판
            for sig in _FINDER_SIGNALS[:2]:
                qs.append(f'{rg}{sv} {sig}')
    return qs

def build_queries(cfg):
    """플랫폼 흔적 × 홍보 의도 × (선택)업종 키워드 조합 생성.
       provider가 brave면 inurl: 연산자가 안 먹으므로 '평문 URL조각' 쿼리를 쓴다.
       그누보드/카페24 홍보게시판 '찾기' 검색어를 항상 먼저 실행하고, 그 뒤에
       사용자가 저장한 목록(지역×업종 등)을 붙인다."""
    provider=(cfg.get('search_provider') or 'brave').lower()
    extra=[x.strip() for x in (cfg.get('discover_keywords','') or '').splitlines() if x.strip()]
    direct=[x.strip() for x in (cfg.get('discover_direct_queries','') or '').splitlines()
            if x.strip() and not x.lstrip().startswith('#')]
    # 1) 항상 먼저: 플랫폼 홍보게시판 찾기 검색어
    finder=_board_finder_queries(provider)
    # 2) 업종/지역 키워드가 있으면 게시판 조각과 곱해 보강
    if provider=='brave':
        for e in extra:
            for i in INTENT[:3]:
                finder.append(f'{e} {i}')
            finder.append(f'{e} bbs/board.php bo_table=promotion')
            finder.append(f'{e} 홍보게시판 글쓰기')
    else:
        for e in extra:
            for i in INTENT[:5]:
                finder.append(f'{e} {i}')
            finder.append(f'{e} inurl:bbs/board.php')
    # 3) 사용자가 저장한 목록은 뒤에 이어붙인다(보존). finder가 앞이라 쿼리한도 안에서 우선 실행됨.
    return list(dict.fromkeys(finder+direct))

def google_search(cfg, query, start=1, num=10):
    """Google Custom Search JSON API. (공식 API — 결과 직접 스크래핑 안 함)"""
    key=cfg.get('google_api_key',''); cx=cfg.get('google_cx','')
    if not key or not cx: raise RuntimeError('구글 API 키/검색엔진ID(cx) 미설정')
    import requests as _rq
    r=_rq.get('https://www.googleapis.com/customsearch/v1',
              params={'key':key,'cx':cx,'q':query,'start':start,'num':num,'hl':'ko','lr':'lang_ko'},
              timeout=20)
    if r.status_code==429: raise RuntimeError('구글 API 일일 한도 초과(429)')
    if r.status_code>=400: raise RuntimeError(f'구글 API 오류 {r.status_code}: {r.text[:120]}')
    j=r.json()
    return [{'url':it.get('link',''),'title':it.get('title',''),'snippet':it.get('snippet','')}
            for it in (j.get('items') or [])]

def brave_search(cfg, query, start=1, num=10):
    """Brave Search 공식 Web API."""
    key=(cfg.get('brave_api_key') or '').strip()
    if not key: raise RuntimeError('Brave Search API 키 미설정 — 설정 탭에서 입력하세요')
    import requests as _rq
    offset=max(0,(int(start or 1)-1)//max(1,int(num or 10)))
    r=_rq.get('https://api.search.brave.com/res/v1/web/search',
              headers={'Accept':'application/json','X-Subscription-Token':key},
              params={'q':query,'count':min(20,max(1,int(num or 10))),'offset':min(9,offset),
                      'country':'KR','search_lang':'ko','safesearch':'moderate'},timeout=20)
    if r.status_code==429: raise RuntimeError('Brave Search API 사용량/속도 한도 초과(429)')
    if r.status_code in (401,403): raise RuntimeError('Brave Search API 키 또는 구독 상태 확인 필요')
    if r.status_code>=400: raise RuntimeError(f'Brave Search API 오류 {r.status_code}: {r.text[:120]}')
    rows=((r.json().get('web') or {}).get('results') or [])
    _record_brave_usage(cfg)  # 성공 호출 1건 기록(횟수 정확·금액은 설정단가 기준 추정)
    return [{'url':it.get('url',''),'title':it.get('title',''),'snippet':it.get('description','')}
            for it in rows if it.get('url')]

def web_search(cfg, query, start=1, num=10):
    provider=(cfg.get('search_provider') or 'brave').lower()
    return google_search(cfg,query,start,num) if provider=='google' else brave_search(cfg,query,start,num)

def _post_read_block_reason(url):
    """발행된 글 URL을 '비로그인'으로 열어 실제 본문이 읽히는지 확인. 읽기 차단이면 사유 문자열,
       정상 읽힘이면 ''. ★잇츠키친처럼 포인트/권한 제한으로 글읽기가 막히는 게시판 감지용
       (글번호는 나와도 구글이 본문을 못 읽어 SEO 0 = 헛발행). 대표님 지시 2026-09-08."""
    import requests as _rq
    UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
    try:
        r=_rq.get(url,timeout=12,verify=False,headers=UA,allow_redirects=True)
    except Exception:
        return ''   # 조회 자체 실패는 판정 보류(정상으로 두고 발행)
    h=r.text or ''
    # 오류/제한 안내 신호(그누보드 wrest.php 계열 알림 + 제목). 본문이 정상이면 이 문구들이 없다.
    # 0) ★비밀글(대표님 지시 2026-09-08): '비밀글 기능으로 보호된 글' → 구글도 못읽음(SEO0).
    #    비번은 _post_password로 고정 저장되므로 대표님이 열람 가능. 사유에 비번 포함해 로그·이력에 남김.
    if ('비밀글' in h and any(k in h for k in ['보호','열람','비밀번호'])) or '비밀글 기능으로 보호' in h:
        return f'비밀글(비번 {_post_password()})'
    # 1) 포인트/열람권한 제한
    if '포인트' in h and any(k in h for k in ['불가','모자라','부족']) and any(k in h for k in ['글읽기','글 읽기','열람','조회','읽기']):
        return '포인트 부족(글읽기 제한)'
    # 2) 오류안내 페이지로 튕김(제목 기반)
    tm=re.search(r'<title[^>]*>(.*?)</title>',h,re.S|re.I)
    title=re.sub(r'\s+',' ',(tm.group(1) if tm else '')).strip()
    if any(k in title for k in ['오류안내','오류 안내','접근할 수 없','권한','로그인']):
        return f'오류안내({title[:20]})'
    # 3) 로그인/권한 안내 본문
    if any(k in h for k in ['로그인 후 이용','권한이 없','열람 권한','회원만','로그인이 필요']) and len(h)<8000:
        return '로그인/열람권한 필요'
    # 4) 페이지가 사실상 비어있음(정상 글이면 최소 수 KB)
    if len(h)<1500:
        return '본문 없음(빈 페이지)'
    return ''

def _index_block_reason(html, page_url, resp_headers=None):
    """글 상세페이지 HTML을 보고 '구글이 색인 못할 신호'가 있으면 사유 문자열, 없으면 ''.
       ★대표님 지시 2026-09-11 '며칠 지나도 색인 안 되는 글 많다 — 발행 전 미리 판별'.
       실측(52개): canonical 불일치 16·noindex 5·본문안읽힘 5가 헛발행의 주범.
       발행해도 색인 안 되면 SEO 0이므로, 이 신호 있으면 검수에서 애초에 제외한다.
       (판정은 '확실한 것'만: 오탐으로 멀쩡한 게시판을 버리지 않도록 보수적으로.)"""
    if not html: return ''
    h=html
    try:
        # 1) meta robots / X-Robots-Tag 의 noindex — 구글에게 '색인하지 마' 명시. 가장 확실.
        mm=re.search(r'<meta[^>]+name=["\']robots["\'][^>]*>', h, re.I)
        if mm and re.search(r'noindex', mm.group(0), re.I):
            return 'noindex 태그 — 구글 색인 차단(발행해도 SEO0)'
        if resp_headers:
            xr=str(resp_headers.get('X-Robots-Tag','') or resp_headers.get('x-robots-tag','') or '')
            if 'noindex' in xr.lower():
                return 'noindex(헤더) — 구글 색인 차단'
        # 2) canonical 이 '이 글이 아닌 다른 곳(목록/홈)'을 가리키면 우리 글이 원본으로 안 잡힘.
        #    ★보수적 판정: 상세글 URL엔 글번호(wr_id/article번호)가 있는데 canonical엔 그게 없으면 차단.
        cm=re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', h, re.I)
        if cm:
            from urllib.parse import urljoin as _uj, urlsplit as _us
            can=_uj(page_url, cm.group(1))
            pu=_us(page_url)
            pid=re.search(r'wr_id=(\d+)', pu.query) or re.search(r'/(?:article|board)/[^/]+/\d+/(\d+)', pu.path)
            if pid:
                num=pid.group(1)
                if num not in can:
                    return f'canonical 불일치 — 목록/홈을 원본으로 지정({can[:40]}) → 이 글 색인 안 됨'
    except Exception:
        return ''
    return ''

def _cafe24_board_map(html, page_url=''):
    """Cafe24 페이지 HTML에서 게시판 경로 매핑을 추출. 반환: {board_no(str): 경로(str)}.
       예: <a href="/board/product2/list.html?board_no=6"> → {'6':'product2'}.
       /article/SEO-URL 사이트의 실제 write 경로 자동탐지용(경로가 product·product2·free 등 제각각).
       board_no 없는 경로별 링크(/board/free/)는 board_no=1로 간주(Cafe24 기본)."""
    m={}
    if not html: return m
    try:
        # 1) board_no 명시된 링크 — 가장 신뢰. list/write 어느 쪽이든 경로+번호가 드러난다.
        for path,no in re.findall(r'/board/([A-Za-z0-9_]+)/(?:list|write|read|view)\.html\?[^"\'>]*board_no=(\d+)',html,re.I):
            if path.lower() not in ('write','list','read','view'):   # 경로 자리에 잘못 걸린 것 방지
                m.setdefault(no,path)
        # 2) board_no 없는 경로 링크(/board/free/list.html) — board_no=1로 간주(중복이면 위 매핑 우선)
        for path in re.findall(r'/board/([A-Za-z0-9_]+)/(?:list|write)\.html(?![?][^"\'>]*board_no)',html,re.I):
            if path.lower() not in ('write','list','read','view'):
                m.setdefault('1',path)
    except Exception:
        pass
    return m

def screen_candidate(url, cfg=None):
    """HTTP 1~3회로 후보 자동 검수. 반환: 검수 결과 dict."""
    import requests as _rq
    UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
    m=re.match(r'(https?://[^/]+)',url or ''); base=m.group(1) if m else url
    res={'base':base,'domain':_domain_of(url),'platform':'unknown','board_name':'','bo_table':'',
         'write_form':False,'captcha':'','login_required':False,'ad_banned':False,'promo_hint':False,
         'last_post_days':None,'reachable':False,'note':'',
         'parked':False,'illegal':False}
    _cfg_sc=load_config()
    def get(u):
        try: rr=_rq.get(u,timeout=12,verify=False,headers=UA,allow_redirects=True)
        except Exception: rr=None
        # ★CF 챌린지에 막히면(just a moment 등) Web Unlocker로 재시도 — CF 걸린 Cafe24 검수 통과용.
        #   (대표님 지시 2026-09-08: 비회원 게시판부터 Web Unlocker 연결. CF 사이트만 선택적 사용=비용↓)
        try:
            _blocked=(rr is None) or (rr.status_code in (403,503)) or \
                     ('just a moment' in (rr.text or '')[:3000].lower() or 'cf-chl' in (rr.text or '')[:3000].lower() or '_cf_chl' in (rr.text or '')[:3000].lower())
        except Exception: _blocked=True
        if _blocked and unlocker_enabled(_cfg_sc):
            uhtml=unlocker_fetch(u,_cfg_sc,timeout=90)
            if uhtml:
                class _UResp:   # requests.Response 흉내(검수 코드가 .text/.status_code/.url 사용)
                    def __init__(s,t,u): s.text=t; s.status_code=200; s.url=u
                return _UResp(uhtml,u)
        return rr
    # bo_table 추출 — ★비표준 파라미터(bo_id 등)도 인식(대표님 제보 codeb.dhu.ac.kr: bo_id=qna).
    #   글쓰기 URL은 그 사이트가 쓰는 파라미터명 그대로 만들어야 하므로 bo_param에 기록.
    res['bo_param']='bo_table'
    bm=re.search(r'bo_table=([A-Za-z0-9_]+)',url or '')
    if bm:
        res['bo_table']=bm.group(1)
    else:
        bi=re.search(r'\bbo_id=([A-Za-z0-9_]+)',url or '')
        if bi: res['bo_table']=bi.group(1); res['bo_param']='bo_id'
    r=get(url)
    if not r or r.status_code>=400:
        res['note']=f'접속 실패({r.status_code if r else "timeout"})'; return res
    res['reachable']=True
    html=r.text or ''; low=html.lower()
    # ★읽기제한 게시판 사전탈락(대표님 지시 2026-09-08 '의미없는 발행 안하도록'): 잇츠키친처럼
    #   글 조회에 포인트/권한이 필요한 게시판은 발행해도 구글이 본문 못읽어 SEO 0 = 헛발행.
    #   목록에서 기존 글 하나를 열어 '포인트 부족/글읽기 불가'면 후보 탈락(재발굴돼도 여기서 걸림).
    try:
        _wm=re.search(r'(board\.php\?[^"\']*?wr_id=\d+)',html)
        if _wm:
            from urllib.parse import urljoin as _uj
            _plink=_wm.group(1).replace('&amp;','&')   # HTML 엔티티 디코드(안 하면 URL 깨짐)
            _purl=_uj(r.url,_plink)
            _rb=_post_read_block_reason(_purl)
            if _rb:
                res['read_restricted']=_rb
                res['note']=f'읽기제한({_rb}) — 발행해도 조회차단·SEO0'
                return res   # 즉시 탈락(더 볼 것 없음)
            # ★색인 차단 신호(대표님 지시 2026-09-11): 기존 글 하나를 열어 noindex/canonical 검사.
            #   발행해도 구글이 색인 안 할 게시판이면 애초에 탈락(헛발행 방지).
            try:
                _pr=_rq.get(_purl,timeout=12,verify=False,headers=UA,allow_redirects=True)
                _ib=_index_block_reason(_pr.text or '', str(_pr.url), _pr.headers)
                if _ib:
                    res['index_blocked']=_ib
                    res['note']=f'색인차단({_ib[:40]})'
                    return res   # 즉시 탈락
            except Exception: pass
    except Exception: pass
    # 플랫폼
    if 'bo_table' in low or 'gnuboard' in low or '/bbs/' in low: res['platform']='gnuboard'
    elif 'cafe24' in low or 'xans-' in low or '/board/write.html' in low: res['platform']='cafe24'
    # 게시판명 (title/h1)
    tm=re.search(r'<title[^>]*>(.*?)</title>',html,re.S|re.I)
    title=re.sub(r'\s+',' ',re.sub(r'&nbsp;',' ',(tm.group(1) if tm else '')))
    res['board_name']=title[:80]
    # 주차/만료 도메인 판별 (검색엔진엔 남아있지만 실제론 껍데기)
    tl=title.lower()
    # ★기업 소개성 게시판(CEO 인사말·회사소개·연혁·조직도…) 즉시 탈락 — 대표님 지시 2026-09-11.
    #   '가입인사' 같은 개방 게시판은 '인사말' 오탐 방지로 제외.
    try:
        _bt=(res.get('bo_table') or '').lower()
        _corp_t=([w for w in CORP_BOARD_TITLE if w in tl] if '가입' not in tl else [])
        if _corp_t or _bt in CORP_BOARD_TABLE:
            res['corp_board']=(_corp_t[0] if _corp_t else _bt)
    except Exception: pass
    res['parked']=(any(w in low or w in tl for w in PARKED_WORDS) or len(html)<800)
    # 불법·도박 사이트 판별 (제휴 대상 부적합)
    res['illegal']=any(w in low for w in ILLEGAL_WORDS)
    # 광고 금지 / 홍보 허용 흔적 (본문 + URL/게시판ID/타이틀까지 함께 판단)
    res['ad_banned']=any(w in html for w in AD_BAN_WORDS)
    hint_blob=html+' '+title+' '+(url or '')+' '+res['bo_table']
    # ★업자 홍보 키워드 개수(대표님 지시 2026-09-11): 찌라시·텔레·라인·마사지·노래방 등이 게시판에
    #   여러 번 나오면 이미 유흥/마사지 업자들이 쓰는 개방 게시판 = 발행·색인 잘 되는 황금 게시판.
    try: res['biz_promo_count']=sum(html.count(w) for w in BIZ_PROMO_WORDS)
    except Exception: res['biz_promo_count']=0
    res['promo_hint']=(any(w in hint_blob for w in PROMO_WORDS)
                       or res['biz_promo_count']>=2   # 업자 키워드 2회+면 홍보허용 흔적으로 인정
                       or bool(re.search(r'bo_table=(promotion|hongbo|ad|link|partner|banner)',url or '',re.I)))
    # ★황금사이트 신호(대표님 지시 2026-09-08): 게시판 목록에 전화번호(010 등)가 여러 개 있으면
    #   이미 업자들이 활발히 홍보 중 = 발행 성공 확률 높은 방치·개방 게시판(samjinvalve식).
    #   010-xxxx-xxxx / O1O / 공백·점·하이픈 구분 다 잡는다. 8개+면 '황금'으로 강가점.
    try:
        # 홍보글 전화번호는 010/O1O/공일공 + 임의 구분자(기호/공백 무엇이든) + 4자리 + 4자리.
        # 예: 010 6613 4800, O1O+2748+5884, [010_5603◆7569], O1O—56O3=7569
        #  → 숫자/O를 [0-9Oo] 로, 구분자를 비숫자 0~2글자로 넓게.  뒤 8자리(4+4) 핵심.
        _sep=r'[^0-9A-Za-z가-힣]{0,3}'
        _d=r'[0-9OoIl]'   # 0→O, 1→I/l 치환 흔함
        pat=re.compile(_d+'1'+_d+_sep+_d+r'{3,4}'+_sep+_d+r'{4}')
        found=set(pat.findall(html))
        res['promo_phone_count']=len(found)
    except Exception:
        res['promo_phone_count']=0
    # 최근 글(날짜 패턴에서 가장 최근)
    try:
        ds=re.findall(r'(20\d{2})[-.](\d{1,2})[-.](\d{1,2})',html)
        if ds:
            from datetime import date
            latest=max(date(int(y),int(mo),int(d)) for y,mo,d in ds
                       if 1<=int(mo)<=12 and 1<=int(d)<=31)
            res['last_post_days']=max(0,(_kst_now().date()-latest).days)
    except Exception: pass
    # 글쓰기 페이지: 실제 쓰기 링크(write.php)만 신뢰한다.
    # ※ board.php?wr_id= (글 조회) 페이지에도 wr_content가 있지만 그건 '댓글 폼'이라
    #    글쓰기 폼으로 오인하면 안 된다(오판 시 write.php에서 게시판 못찾음으로 실패).
    from urllib.parse import urljoin
    wurls=[]
    url_low=(url or '').lower()
    is_view = 'wr_id=' in url_low  # 글 조회 URL이면 그 페이지의 폼은 댓글일 가능성 → 신뢰 안 함
    if (not is_view) and 'write.php' in url_low and \
       ('wr_subject' in low or 'name="subject"' in low) and ('wr_content' in low or 'name="content"' in low):
        wurls.append(url)
    for href in re.findall(r'href=["\']([^"\']+)["\']',html,re.I):
        hl=href.lower()
        if ('write.php' in hl and ('bo_table=' in hl or 'bo_id=' in hl)) or ('/board/write.html' in hl and 'board_no=' in hl):
            wurls.append(urljoin(r.url,href))
    if res['platform']=='cafe24':
        # ★Cafe24 write URL 자동탐지(2026-09-08 대표님 지시). /article/SEO-URL 황금사이트(jinaedeul 등)는
        #   실제 게시판 경로가 product/product2/free/faq/gallery 등 사이트마다 다르고, board_no도 6·4 등 제각각.
        #   기존엔 board_no=1만 찍어 못 찾았다. → 홈/현재 HTML의 /board/{경로}/list.html?board_no=N 링크를
        #   긁어 board_no→실제경로 매핑을 만들고, 후보 URL(/article/.../N/)의 N에 맞는 write.html을 생성한다.
        #   (CF 걸린 사이트도 이 GET은 get()의 unlocker_fetch 폴백으로 통과되므로 링크를 볼 수 있다)
        bmap=_cafe24_board_map(html, r.url)                 # {board_no: 경로}
        if not bmap:                                        # 현재 페이지에 없으면 홈에서 한 번 더 긁는다
            hr=get(base+'/')
            if hr and (hr.text or ''): bmap=_cafe24_board_map(hr.text, hr.url)
        # 후보 URL이 /article/카테고리/N/ 형태면 그 N이 목표 board_no
        ano=re.search(r'/article/[^/]+/(\d+)/?',url or '')
        art_no=ano.group(1) if ano else None
        added=False
        if art_no and art_no in bmap:                       # 정확 매핑 — 최우선
            wurls.append(base+f'/board/{bmap[art_no]}/write.html?board_no={art_no}'); added=True
        for _no,_path in bmap.items():                      # 나머지 매핑도 후보로(방치·개방 게시판 우선순위는 뒤)
            wurls.append(base+f'/board/{_path}/write.html?board_no={_no}')
        if art_no and not added:                            # 매핑엔 없지만 board_no는 아는 경우 흔한 경로로 시도
            for _p in ('product','free','board'):
                wurls.append(base+f'/board/{_p}/write.html?board_no={art_no}')
        wurls.append(base+'/board/write.html?board_no=1')   # 최후 폴백(기존 동작 유지)
    else:
        bo=res['bo_table'] or 'free'
        bp=res.get('bo_param','bo_table')   # bo_id 사이트는 그 파라미터명 그대로(codeb.dhu 등)
        wurls.append(base+f'/bbs/write.php?{bp}={bo}')
        if bp!='bo_table':   # 혹시 표준도 되는지 함께 시도
            wurls.append(base+f'/bbs/write.php?bo_table={bo}')
    wurls=list(dict.fromkeys(wurls))[:8]
    for wu in wurls:
        wr=get(wu)
        if not wr or wr.status_code>=400: continue
        wh=wr.text or ''; wl=wh.lower()
        has_subject=('wr_subject' in wl or 'name="subject"' in wl or "name='subject'" in wl)
        has_content=('wr_content' in wl or 'name="content"' in wl or "name='content'" in wl)
        if has_subject and has_content:
            res['write_form']=True; res['write_url']=wr.url
        if any(k in wl for k in ['captcha_key','kcaptcha','g-recaptcha','h-captcha','cf-turnstile']) or '자동등록방지' in wh:
            res['captcha']='감지'
        if not res['write_form'] and ('mb_password' in wl or '로그인' in wh):
            res['login_required']=True
        if any(w in wh for w in AD_BAN_WORDS): res['ad_banned']=True
        if res['write_form']: break
    # ★자동가입 사전 판정(2026-09-08 대표님 지시): 로그인 필요 게시판이면 가입폼을 미리 읽어
    #   이메일 인증·본인인증(휴대폰) 필요 여부를 검수 단계에서 판정한다. 필요하면 auto_pipeline이
    #   자동가입을 건너뛰고 '수동가입 대기(manual_signup)'로 분류 → 크롬·2captcha 낭비 0.
    #   (비회원 글쓰기 가능하면 가입 자체가 불필요하므로 login_required일 때만 검사.)
    res['signup_email_verify']=False; res['signup_phone_cert']=False
    if res.get('login_required') and not res.get('write_form'):
        signup_urls=[base+'/bbs/register.php', base+'/member/join.html',
                     base+'/bbs/register_form.php', base+'/shop/member.php?type=join']
        for su in signup_urls:
            sr=get(su)
            if not sr or sr.status_code>=400: continue
            sh=sr.text or ''
            # 본인인증(휴대폰/실명) 신호
            if any(k in sh for k in ['win_hp_cert','nice본인인증','checkplus','휴대폰 본인인증','휴대폰본인인증',
                                     'nice_ok','본인인증','실명인증','SMS 인증','아이핀','ipin']):
                res['signup_phone_cert']=True
            # 이메일 인증 필수 신호(learn_signup의 강제 신호 패턴과 동일 기준 — 오탐 축소)
            if (re.search(r'(?:e-?mail|이메일)\s*(?:주소)?\s*(?:인증|확인)(?:을|를|이|가)?\s*(?:반드시|필수|해야|하셔야|완료해야|하여야)',sh,re.I)
                or re.search(r'(?:인증\s*(?:메일|이메일)|인증\s*링크).{0,40}(?:발송|보냈|전송|클릭|확인)',sh,re.I)):
                res['signup_email_verify']=True
            if re.search(r'wr_subject|mb_id|mb_password',sh,re.I) or res['signup_phone_cert'] or res['signup_email_verify']:
                break   # 가입폼(또는 인증신호) 찾음 → 더 볼 필요 없음
    return res

def score_candidate(c):
    """점수화 — 위에서부터 100개만 보면 되게."""
    s=0
    name=(c.get('board_name','')+' '+c.get('bo_table',''))
    if c.get('promo_hint') or any(w in name for w in ['홍보','제휴','광고','링크','업체']): s+=30
    if c.get('write_form') and not c.get('login_required'): s+=20
    lp=c.get('last_post_days')
    if lp is not None:
        if lp<=7: s+=15
        elif lp<=30: s+=8
        elif lp>365: s-=10
    if not c.get('captcha'): s+=10
    if c.get('platform') in ('gnuboard','cafe24'): s+=5
    # ★황금사이트 가점(대표님 지시): 게시판에 전화번호 홍보글 많으면 발행 성공확률↑.
    #   8개+ = 확실한 활발 게시판(강가점 40), 3개+ = 가능성 있음(20), 1개+ = 약간(8).
    _pc=int(c.get('promo_phone_count',0) or 0)
    if _pc>=8: s+=40
    elif _pc>=3: s+=20
    elif _pc>=1: s+=8
    # ★업자 홍보 키워드 가점(대표님 지시 2026-09-11 '찌라시·텔레·라인·마사지·노래방 있으면 좋다'):
    #   유흥·마사지 업자들이 이미 쓰는 개방 게시판 신호. 많을수록 발행·색인 잘 됨.
    _bp=int(c.get('biz_promo_count',0) or 0)
    if _bp>=8: s+=30
    elif _bp>=3: s+=15
    elif _bp>=2: s+=6
    if c.get('ad_banned'): s-=50
    if c.get('captcha'): s-=30
    if c.get('login_required'): s-=20
    if c.get('parked'): s-=100      # 주차/만료 도메인 = 껍데기
    if c.get('illegal'): s-=100     # 도박·불법 사이트 = 제휴 부적합
    if not c.get('reachable'): s-=100
    return s

def precheck_search_result(url):
    """검색 후보 저장 전 실제 접근 및 HTML title 숫자 개수를 가볍게 확인."""
    import requests as _rq
    try:
        r=_rq.get(url,timeout=12,verify=False,allow_redirects=True,headers={
            'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36'})
        if r.status_code>=400: return {'reachable':False,'title':'','digits':0}
        html=r.text or ''
        tm=re.search(r'<title[^>]*>(.*?)</title>',html,re.S|re.I)
        title=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',tm.group(1) if tm else '')).strip()
        return {'reachable':True,'title':title[:200],'digits':len(re.findall(r'\d',title))}
    except Exception:
        return {'reachable':False,'title':'','digits':0}

# ─── URL 패턴 발굴 (대표님 지시 2026-09-07) ──────────────────────────────
# "검색 키워드 목록"에 bbs/board.php?bo_table=free&wr_id= 같은 URL 조각을 직접 넣으면
#   ① Brave가 그 조각으로 검색해 실제 그 게시판 글 URL들을 물어오고
#   ② 그 글 URL을 '글 목록(=글쓰기 가능한 게시판)' URL로 잘라서 후보로 등록한다.
# 개별 글(wr_id=12345)이 아니라 게시판(bo_table=free)을 후보로 잡아야 screen_candidate가
# 글쓰기 폼을 찾고 auto_pipeline이 발행을 시도할 수 있다.
_POST_ID_KEYS=('wr_id','document_srl','no','idx','uid','id','num','board_no','p','articleno','bbsidx')

def _is_url_pattern_query(q):
    """직접 검색어가 게시판 URL 조각인지 판별.
       예) bbs/board.php?bo_table=free&wr_id=  ·  /board/list.html?board_no=  ·  inurl:board.php"""
    if not q: return False
    s=q.strip().lower().lstrip('#').replace('inurl:','').strip()
    if any(t in s for t in ('bbs/board.php','board.php?','bo_table=','/board/','board_no=',
                            'wr_id=','document_srl=','mid=','act=dispbo')):
        # 사람이 읽는 문장(공백 많고 한글)과 구분: URL스러운 토큰이 있어야 패턴으로 인정
        return ('=' in s) or ('board' in s) or ('bbs/' in s)
    return False

def _board_url_from_result(url):
    """개별 글 URL을 '글 목록(게시판)' URL로 축약.
       wr_id/document_srl 등 글 식별 파라미터를 떼어내 bo_table/board_no만 남긴다.
       그누보드 예) .../bbs/board.php?bo_table=free&wr_id=123&page=2 → .../bbs/board.php?bo_table=free
       XE 예)      .../index.php?mid=free&document_srl=123          → .../index.php?mid=free"""
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        sp=urlsplit(url)
        if not sp.query:
            return url  # 쿼리 없으면 그대로(정적 게시판일 수 있음)
        keep=[]
        for k,v in parse_qsl(sp.query, keep_blank_values=True):
            if k.lower() in _POST_ID_KEYS:   # 글 식별자·페이지네이션 제거
                continue
            if k.lower() in ('page','page_num','sca','sfl','stx','sst','sod','spt','sop'):
                continue
            keep.append((k,v))
        new_q=urlencode(keep)
        return urlunsplit((sp.scheme,sp.netloc,sp.path,new_q,''))
    except Exception:
        return url

def add_candidates_from(items, cfg, source='search'):
    """검색 결과 → 접근·title 숫자·글쓰기 폼까지 확인 → 후보 등록.
       source='urlpattern'이면 각 결과 URL을 게시판(글목록) URL로 축약해서 등록한다."""
    if source=='urlpattern':
        # 글 URL → 게시판 URL 축약 후, 같은 게시판 중복 제거
        seen=set(); reduced=[]
        for it in items:
            u=it.get('url') if isinstance(it,dict) else str(it)
            if not u: continue
            bu=_board_url_from_result(u)
            if bu in seen: continue
            seen.add(bu)
            if isinstance(it,dict):
                it=dict(it); it['url']=bu
            else:
                it={'url':bu}
            reduced.append(it)
        items=reduced
        source='search'   # 이후 로직은 일반 검색과 동일하게 검수·등록
    with _cand_lock:
        cands=load_cands()
        known_dom={c.get('domain') for c in cands}
        site_dom={_domain_of(s.get('site_url','')) for s in load_sites()}
        rejected_dom=load_rejected_domains()   # 영구 탈락: 재수집 안 함
        added=0; blocked_unreachable=0; blocked_write=0; blocked_rejected=0; blocked_errpage=0
        for it in items:
            url=it.get('url') if isinstance(it,dict) else str(it)
            if not url or not url.startswith('http'): continue
            if source!='manual' and _is_blacklisted(url): continue   # 수동은 블랙리스트 무시(대표님 명시 추가)
            dom=_domain_of(url)
            if not dom: continue
            if source=='manual':
                # 대표님이 직접 넣은 URL — 영구탈락/블랙리스트/이전 실패를 무시하고 강제 재활성화.
                _dm=dom.replace('www.','')
                if _dm in rejected_dom:
                    try: remove_rejected_domain(_dm)
                    except Exception: pass
                # 기존에 같은 도메인 후보가 있으면(특히 rejected) 되살려 재검수한다.
                _react=False
                for _c in cands:
                    if _c.get('domain')==dom:
                        _c['status']='ready'; _c['screened']=False; _c.pop('reject_reason',None)
                        _c['url']=url; _react=True
                if _react:
                    added+=1; save_cands(cands); add_log(f'[수동추가] 기존 후보 재활성화: {dom}'); continue
            else:
                if dom in known_dom or dom in site_dom: continue
                if dom.replace('www.','') in rejected_dom:   # 이전에 탈락한 도메인은 건너뜀
                    blocked_rejected+=1; continue
            # 접속 가능 여부만 확인. '제목 숫자 8개' 규칙은 정상 게시판도 대량 오탈락시켜 제거함
            # (대표님 지시 2026-09-06). 주차/스팸은 뒤의 screen_candidate가 parked/illegal로 거른다.
            check=precheck_search_result(url) if source!='manual' else {'reachable':True,'title':'','digits':8}
            if not check.get('reachable'):
                blocked_unreachable+=1; continue
            # 오류안내/에러 페이지 제목이면 후보에서 즉시 제외(뚜뚜월드처럼 홈이 '오류안내 페이지'인 곳).
            _ttl=str(check.get('title') or '')
            if source!='manual' and any(k in _ttl for k in ERROR_PAGE_HINTS):
                blocked_errpage+=1; add_rejected_domains(dom,'오류안내 페이지'); continue
            form_check=screen_candidate(url,cfg) if source!='manual' else {}
            # write_form이 없어도 '게시판 URL이면서 로그인 필요' 후보는 받아둔다 —
            # auto_pipeline이 자동가입(2captcha·mail.tm)으로 뚫은 뒤 발행을 시도한다.
            # (비회원 글쓰기 게시판만 받던 관문을, 로그인 게시판까지 확대)
            url_is_board=bool(re.search(r'(bbs/board\.php|bo_table=|/board/.*list\.html|board_no=)',url.lower()))
            _is_board_login=bool(form_check.get('login_required')) and (
                form_check.get('platform') in ('gnuboard','cafe24','kboard') or url_is_board)
            if source!='manual' and not form_check.get('write_form') and not _is_board_login:
                blocked_write+=1; continue
            if source!='manual':
                form_check['screened']=True
                form_check['score']=score_candidate(form_check)
                if form_check.get('parked'): form_check['status']='rejected'; form_check['reject_reason']='주차/만료 도메인'
                elif form_check.get('illegal'): form_check['status']='rejected'; form_check['reject_reason']='도박·불법 사이트'
                else: form_check['status']='ready'
            known_dom.add(dom)
            rec={'id':secrets.token_hex(6),'url':url,'domain':dom,
                 'title':check.get('title') or (it.get('title','') if isinstance(it,dict) else ''),
                 'title_digit_count':check.get('digits',0),'precheck_reachable':True,
                 'snippet':(it.get('snippet','') if isinstance(it,dict) else ''),
                 'found_at':_kst_now().strftime('%Y-%m-%d %H:%M'),'source':source,
                 'query':(it.get('query','') if isinstance(it,dict) else ''),
                 'status':'new','score':0,'screened':False}
            rec.update(form_check)
            cands.append(rec)
            added+=1
        save_cands(cands)
    # 사전필터 로그는 제외 합계가 클 때(5건 이상)만 남긴다 — 발굴 배치마다 자잘한 제외가
    # 화면을 도배하던 문제(대표님 지시). 소수 제외는 정상이라 굳이 안 남김.
    _blk=blocked_unreachable+blocked_write+blocked_rejected+blocked_errpage
    if source!='manual' and _blk>=5:
        add_log(f'[후보 사전필터] 접속불가 {blocked_unreachable} · 오류페이지 {blocked_errpage} · 글쓰기폼없음 {blocked_write} · 영구탈락 {blocked_rejected} 제외')
    return added

def screen_pending(limit=30):
    """미검수 후보를 검수·점수화. 처리 건수 반환."""
    cfg=load_config()
    with _cand_lock:
        cands=load_cands()
        todo=[c for c in cands if not c.get('screened')][:max(1,limit)]
        ids=[c['id'] for c in todo]
    results={}
    for c in todo:
        try:
            r=screen_candidate(c['url'],cfg)
        except Exception as e:
            r={'note':str(e)[:100],'reachable':False}
        r['screened']=True
        r['score']=score_candidate(r)
        # 자동 탈락 사유
        if not r.get('reachable'): r['status']='ready'; r['reject_reason']='현재 접속 불가 — 후보 유지·재검수 가능'
        elif r.get('read_restricted'): r['status']='rejected'; r['reject_reason']=f'읽기제한 게시판 — {r.get("read_restricted")}(발행해도 조회차단·SEO0)'
        elif r.get('index_blocked'): r['status']='rejected'; r['reject_reason']=f'색인차단 — {r.get("index_blocked")}'
        elif r.get('corp_board'): r['status']='rejected'; r['reject_reason']=f'기업 소개 게시판({r.get("corp_board")}) — 홍보 부적합(삭제·신고 위험)'
        elif r.get('parked'): r['status']='rejected'; r['reject_reason']='주차/만료 도메인 (실제 게시판 아님)'
        elif r.get('illegal'): r['status']='rejected'; r['reject_reason']='도박·불법 사이트 (제휴 부적합)'
        elif r.get('ad_banned'): r['status']='rejected'; r['reject_reason']='광고 금지 명시'
        elif r.get('captcha'): r['status']='ready'; r['reject_reason']='캡차 있음 — 2captcha 자동해결 시도 예정'
        elif not r.get('write_form'):
            # 글쓰기 폼이 없다: 그누보드/카페24면 로그인 후 쓰기 가능성 있어 ready 유지(자동가입 대상).
            # 그 외(디렉토리·전화번호검색·경쟁업체 랜딩 등 게시판 아님)는 자동 탈락시켜 목록을 깨끗이.
            if r.get('platform') in ('gnuboard','cafe24') or r.get('login_required'):
                r['status']='ready'; r['reject_reason']='글쓰기 폼 미확인 — 로그인 필요할 수 있음(자동가입 시도)'
            else:
                r['status']='rejected'; r['reject_reason']='게시판 글쓰기 폼 없음(디렉토리/랜딩 페이지 — 홍보 대상 아님)'
        else: r['status']='ready'
        # ★빡센 검수(대표님 지시 2026-09-08 '홍보글 있는 방치 게시판만 통과'): strict_screen 켜지면
        #   ready로 통과하려던 후보도 '홍보 증거'가 없으면 탈락. 홍보 증거 = ①게시판에 전화번호 홍보글
        #   존재(promo_phone_count≥1: 업자들이 이미 쓰는 개방·방치 게시판) 또는 ②홍보허용 흔적(promo_hint).
        #   + 죽은 게시판(마지막글 180일+) 제외. (수동 추가 source=manual은 대표님 지정이라 예외 통과.)
        if cfg.get('strict_screen',True) and r.get('status')=='ready' and c.get('source')!='manual':
            _pc=int(r.get('promo_phone_count',0) or 0)
            _has_promo=(_pc>=1) or bool(r.get('promo_hint'))
            _lp=r.get('last_post_days')
            _dead=(_lp is not None and _lp>180)
            if not _has_promo:
                r['status']='rejected'; r['reject_reason']='빡센검수 탈락 — 홍보글 흔적 없음(방치·개방 게시판 아님)'
            elif _dead:
                r['status']='rejected'; r['reject_reason']=f'빡센검수 탈락 — 죽은 게시판(최근글 {_lp}일 전)'
        results[c['id']]=r
        time.sleep(1)   # 요청 속도 관리
    rejected_now=[]
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            if c['id'] in results:
                c.update(results[c['id']])
                if results[c['id']].get('status')=='rejected':
                    rejected_now.append(c.get('domain') or _domain_of(c.get('url','')))
        save_cands(cands)
    if rejected_now:   # 검수에서 탈락한 도메인은 영구 목록에 기록(재수집 방지)
        add_rejected_domains(rejected_now,'검수 탈락')
    return len(results)

def discover_once(cfg=None, max_queries=10):
    """구글 검색 1배치 실행 → 후보 등록 → 검수. (검색량 제어)
       게시판찾기(finder) 검색어는 커서와 무관하게 매일 처음부터 우선 실행하고,
       그 뒤에 지역×업종(direct) 목록을 커서로 순환한다. (finder가 첫날만 실행되고
       마는 문제 방지 — 실제 글 올릴 게시판을 매일 새로 찾기 위함)"""
    cfg=cfg or load_config()
    st=load_json(DISCO_FILE,{})
    today=_kst_now().strftime('%Y-%m-%d')
    if st.get('date')!=today:
        # 날짜가 바뀌면 하루 카운터는 리셋하되, 커서(cursor·fcursor)는 이어받아
        # finder/direct를 여러 날에 걸쳐 골고루 순회한다(같은 앞부분만 반복 방지).
        st={'date':today,'queries':0,'found':0,
            'cursor':int(st.get('cursor',0) or 0),'fcursor':int(st.get('fcursor',0) or 0)}
    st.setdefault('fcursor',0)  # 기존 상태 호환(finder 커서 없던 날)
    target=int(cfg.get('discover_daily_target',100) or 100)
    qlimit=int(cfg.get('discover_query_limit',100) or 100)   # 구글 무료 하루 100
    # 발행가능 사이트가 목표(site_goal, 기본 500) 미만이면 일일 쿼리·후보 한도를 풀어
    # 목표를 채울 때까지 계속 발굴한다(대표님 요청). 목표 도달 시 discover_loop이 자동 중단.
    # ※ Brave 쿼리당 과금 — 배치(discover_batch)·주기(discover_interval_sec)로 속도는 유지.
    try:
        _pub=len([s for s in load_sites() if is_publishable(s)])
        _goal=int(cfg.get('site_goal',500) or 500)
        if _pub < _goal:
            if st.get('_goal_mode_logged')!=today:
                add_log(f'[발굴] 발행가능 {_pub}/{_goal} — 목표까지 한도해제 연속 발굴 모드')
                st['_goal_mode_logged']=today
            qlimit=max(qlimit,1000000); target=max(target,1000000)
    except Exception: pass
    provider=(cfg.get('search_provider') or 'brave').lower()
    finder=_board_finder_queries(provider)          # 게시판 찾기(매일 우선)
    direct=[x.strip() for x in (cfg.get('discover_direct_queries','') or '').splitlines()
            if x.strip() and not x.lstrip().startswith('#')]  # 지역×업종(커서 순환)
    finder=list(dict.fromkeys(finder))
    # '바로 되는 500곳' 목표: 쿼리의 대부분(90%)을 게시판찾기(finder)에 쓴다.
    # direct(지역+업종)는 업소 홈페이지만 나와 발행 불가라 최소한만. finder는 fcursor로
    # 여러 날에 걸쳐 순회(날짜 바뀌어도 fcursor 유지)해 122개 검색어를 골고루 소진.
    # finder(게시판 찾기) 비중 — 기본 100%. 업소 직접키워드(강남동노래방 등)는 업체 홈페이지만
    # 나와 발행 불가라 기본적으로 사용 안 함. finder_ratio 설정(0~1)으로 조절 가능.
    try: _fr=float(cfg.get('finder_ratio',1.0) or 1.0)
    except Exception: _fr=1.0
    _fr=max(0.0,min(1.0,_fr))
    finder_budget=int(qlimit*_fr) if finder else 0
    if not finder and not direct:
        queries=build_queries(cfg)
        if not queries: return {'ok':False,'error':'쿼리 없음'}
    added=0; used=0; errs=[]
    def _next_query():
        # finder를 fcursor로 순회하다(예산 소진 전) 그 뒤 direct를 cursor로 순환.
        # 반환: (쿼리, source) — direct 항목이 게시판 URL 조각이면 'urlpattern'으로 처리.
        if finder and st['queries']<finder_budget:
            q=finder[st['fcursor']%len(finder)]; st['fcursor']+=1; return q,'search'
        if direct:
            q=direct[st['cursor']%len(direct)]; st['cursor']+=1
            return q,('urlpattern' if _is_url_pattern_query(q) else 'search')
        if finder:  # direct가 없으면 finder 계속
            q=finder[st['fcursor']%len(finder)]; st['fcursor']+=1; return q,'search'
        return None,None
    for _ in range(max_queries):
        if st['queries']>=qlimit: errs.append('일일 쿼리 한도 도달'); break
        if st['found']>=target: errs.append('일일 후보 목표 달성'); break
        q,qsrc=_next_query()
        if q is None: errs.append('쿼리 없음'); break
        try:
            items=web_search(cfg,q)
            for it in items: it['query']=q
            n=add_candidates_from(items,cfg,source=qsrc); added+=n; st['found']+=n
        except Exception as e:
            errs.append(str(e)[:120]); break
        finally:
            st['queries']+=1; used+=1
        time.sleep(1)
    save_json(DISCO_FILE,st)
    screened=0
    try: screened=screen_pending(20)
    except Exception as e: errs.append('검수 오류 '+str(e)[:80])
    add_log(f'[발굴] 쿼리 {used}회 · 신규 {added}개 · 검수 {screened}건'+(' · '+errs[0] if errs else ''))
    return {'ok':True,'queries':used,'added':added,'screened':screened,
            'today_queries':st['queries'],'today_found':st['found'],'errors':errs}

# ==================== 임시메일(mail.tm) — 이메일 인증 자동 처리 ====================
TEMPMAIL_API='https://api.mail.tm'

def _tempmail_req(path, method='GET', data=None, token=None):
    hdr={'Content-Type':'application/json'}
    if token: hdr['Authorization']='Bearer '+token
    body=json.dumps(data).encode() if data else None
    r=urllib.request.Request(TEMPMAIL_API+path, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e:
        return 0, {'error': str(e)[:80]}

def _1secmail_create():
    """1secmail 임시메일 발급. 반환: (address, pw, token=login|domain) 또는 (None,None,None).
       1secmail은 계정 생성이 불필요 — 임의 주소로 바로 수신 가능. token에 'login|domain'을 담아
       wait 단계에서 재사용한다."""
    try:
        st, doms = _http_json('https://www.1secmail.com/api/v1/?action=getDomainList')
        if not isinstance(doms, list) or not doms: return None, None, None
        domain = random.choice(doms)
        login = f'twseo{secrets.token_hex(4)}'
        addr = f'{login}@{domain}'
        return addr, secrets.token_hex(8), f'{login}|{domain}'
    except Exception:
        return None, None, None

def _http_json(url):
    """GET JSON (1secmail용). 반환 (status, obj)."""
    try:
        req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception:
        return 0, None

def _gmail_dot_variant(local):
    """지메일 아이디에 점(.)을 랜덤 삽입해 유니크 주소를 만든다. 지메일은 점을 무시하고 배달하므로
       모두 같은 받은편지함으로 오지만, 게시판엔 서로 다른 정상 이메일로 보인다('+' 거부 회피).
       예: 'aveydg1' → 'a.veyd.g1'. 글자 사이 위치를 랜덤 선택(최소 1개, 연속점·양끝점 금지)."""
    if len(local)<2: return local
    gaps=list(range(1,len(local)))          # 점을 넣을 수 있는 글자 사이 위치
    random.shuffle(gaps)
    k=random.randint(1,min(3,len(gaps)))    # 1~3개 점 삽입
    picks=sorted(gaps[:k])
    out=[]
    for i,ch in enumerate(local):
        if i in picks: out.append('.')
        out.append(ch)
    return ''.join(out)

def tempmail_create():
    """인증용 이메일 발급. IMAP(지메일 등) 설정 시 도트(.) 변형 유니크 주소로 발급하고
       IMAP으로 수신(플러스주소 거부 게시판도 통과). 미설정 시 mail.tm→1secmail 임시메일.
       반환: (address, password, token). 태그: 'IMAP:<tag>' / 'MT:<t>' / '1S:<login>|<domain>'."""
    # 0) IMAP 실제메일(지메일 등) — 설정돼 있으면 최우선(진짜 도메인이라 수신율↑)
    try:
        _cfg=load_config(); _em=(_cfg.get('imap_email') or '').strip(); _pw=(_cfg.get('imap_password') or '').strip()
        if _em and _pw and '@' in _em:
            _local,_,_dom=_em.partition('@')
            # 게시판이 '+플러스주소'를 거부/무발송하는 경우가 많아(실측: 인증메일 0통) 도트(.) 트릭 사용.
            # 지메일은 아이디 중간 점을 무시하고 배달하지만, 게시판엔 정상 이메일로 보이고
            # 인증메일의 TO 헤더엔 점 포함 원본 주소가 남아 매칭 가능하다.
            _dotted=_gmail_dot_variant(_local)
            _addr=f'{_dotted}@{_dom}'
            add_log(f'[지메일 IMAP] {_em} 로 인증 (도트주소 {_addr} — 대표님 지메일로 실제 수신)')
            return _addr, '', 'IMAP:'+_addr
        else:
            # ★진단: 지메일 설정이 비어 임시메일로 떨어짐 = 인증 게시판 전환율↓의 근본원인.
            add_log('[임시메일] ⚠ IMAP 미설정(지메일 계정/앱비번 비어있음) → mail.tm 임시메일 사용(차단률 높음). 설정탭에서 지메일 저장 필요')
    except Exception: pass
    # 1) mail.tm 시도
    st, doms = _tempmail_req('/domains')
    if st == 200 and isinstance(doms, dict):
        members = doms.get('hydra:member') or []
        if members:
            domain = members[0].get('domain')
            addr = f'twseo{secrets.token_hex(5)}@{domain}'; pw = secrets.token_hex(10)
            st, _ = _tempmail_req('/accounts', 'POST', {'address': addr, 'password': pw})
            if st in (200, 201):
                st, tok = _tempmail_req('/token', 'POST', {'address': addr, 'password': pw})
                token = tok.get('token') if (st == 200 and isinstance(tok, dict)) else None
                if token:
                    return addr, pw, 'MT:' + token
    # 2) 1secmail 폴백
    addr, pw, tk = _1secmail_create()
    if tk:
        return addr, pw, '1S:' + tk
    return None, None, None

def _extract_verify(blob, text):
    """메일 본문에서 인증 링크 또는 6자리 코드 추출. 반환 {'link':..}/{'code':..}/None."""
    for murl in re.findall(r'https?://[^\s"\'<>]+', blob):
        if re.search(r'(email_check|auth|confirm|verify|activate|register|certify|인증)', murl, re.I):
            return {'link': murl}
    mcode = re.search(r'\b(\d{6})\b', text)
    if mcode: return {'code': mcode.group(1)}
    return None

def _imap_msg_text(msg):
    """이메일 메시지에서 text/html 본문을 모두 이어붙여 반환."""
    import email as _email
    parts=[]
    try:
        if msg.is_multipart():
            for p in msg.walk():
                ct=(p.get_content_type() or '')
                if ct in ('text/plain','text/html'):
                    try: parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or 'utf-8','ignore'))
                    except Exception: pass
        else:
            try: parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8','ignore'))
            except Exception: pass
    except Exception: pass
    return ' '.join(parts)

def _imap_wait_verify(addr, timeout=120):
    """지메일 IMAP 받은편지함에서 도트주소(addr, 예: a.veyd.g1@gmail.com)로 온 인증메일을 찾아 링크/코드 추출.
       지메일은 점을 무시하고 배달하므로 받은편지함은 하나지만, 게시판이 보낸 원본 주소(점 포함)는
       To/Delivered-To 헤더에 남아 정확 매칭이 된다. UNSEEN을 훑어 헤더에 addr이 있는 메일을 찾는다."""
    import imaplib, email as _email
    cfg=load_config(); em=(cfg.get('imap_email') or '').strip(); pw=(cfg.get('imap_password') or '').strip()
    host=(cfg.get('imap_host') or 'imap.gmail.com').strip()
    if not em or not pw: return None
    addr_l=str(addr).lower()
    deadline=time.time()+timeout
    while time.time()<deadline:
        try:
            M=imaplib.IMAP4_SSL(host); M.login(em,pw); M.select('INBOX')
            ids=[]
            # 지메일 검색은 점을 정규화하므로 TO 검색으로 정확 매칭이 어렵다 → 최근 UNSEEN 전체를 훑는다.
            try:
                typ,data=M.search(None,'UNSEEN')
                if data and data[0]: ids=data[0].split()
            except Exception: pass
            for mid in reversed(ids[-30:]):
                typ,md=M.fetch(mid,'(RFC822)')
                if not md or not md[0]: continue
                msg=_email.message_from_bytes(md[0][1])
                hdr=(str(msg.get('To',''))+' '+str(msg.get('Delivered-To',''))+' '+str(msg.get('X-Original-To',''))+' '+str(msg.get('Envelope-To',''))).lower()
                # 헤더에 정확한 도트주소가 있어야 이 가입의 인증메일(다른 가입 메일과 혼선 방지)
                if addr_l not in hdr: continue
                body=_imap_msg_text(msg)
                r=_extract_verify(body, body)
                if r:
                    try: M.store(mid,'+FLAGS','\\Seen')
                    except Exception: pass
                    try: M.logout()
                    except Exception: pass
                    return r
            try: M.logout()
            except Exception: pass
        except Exception as e:
            add_log(f'[IMAP 인증오류] {str(e)[:70]}'); return None
        time.sleep(5)
    return None

def tempmail_wait_verify_link(token, timeout=120):
    """받은편지함 폴링 → 인증 링크/6자리 코드. token 태그(IMAP:/MT:/1S:)로 서비스 구분.
       반환: {'link':url} 또는 {'code':'123456'} 또는 None."""
    deadline = time.time() + timeout
    # --- IMAP 실제메일(지메일 등) ---
    if str(token).startswith('IMAP:'):
        return _imap_wait_verify(token[5:], timeout)
    # --- 1secmail ---
    if str(token).startswith('1S:'):
        login, _, domain = token[3:].partition('|')
        while time.time() < deadline:
            st, msgs = _http_json(f'https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}')
            if isinstance(msgs, list):
                for m in msgs:
                    mid = m.get('id')
                    st2, full = _http_json(f'https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={mid}')
                    if not isinstance(full, dict): continue
                    text = (full.get('textBody') or '') + ' '
                    blob = text + ' ' + (full.get('htmlBody') or '') + ' ' + (full.get('body') or '')
                    r = _extract_verify(blob, text)
                    if r: return r
            time.sleep(4)
        return None
    # --- mail.tm (기본; 'MT:' 접두 제거) ---
    mt = token[3:] if str(token).startswith('MT:') else token
    while time.time() < deadline:
        st, msgs = _tempmail_req('/messages', token=mt)
        items = (msgs.get('hydra:member') or []) if isinstance(msgs, dict) else []
        for m in items:
            mid = m.get('id')
            st2, full = _tempmail_req(f'/messages/{mid}', token=mt)
            if st2 != 200 or not isinstance(full, dict): continue
            text = (full.get('text') or '') + ' '
            h = full.get('html'); html = ' '.join(h) if isinstance(h, list) else (h if isinstance(h, str) else '')
            r = _extract_verify(text + ' ' + html, text)
            if r: return r
        time.sleep(4)
    return None

def _cafe24_signup_gate(d, site, cfg, join_url, max_sec=75):
    """cafe24 가입 진입 관문: join.html → veritas-hub Turnstile 챌린지(2captcha로 해결) / agreement.html 약관(전부 동의→다음)
       을 비밀번호 칸이 보일 때까지 반복 처리. ★auto_signup에서 측정 프로필이 캐시(30분)면 이 스레드의 크롬은 챌린지를
       아직 안 넘어 join.html이 다시 챌린지로 튕김(2026-09-11 재현) → 여기서 처리. 반환: 비번 칸 도달 여부."""
    from selenium.webdriver.common.by import By
    t0=time.time(); solved=0
    while time.time()-t0<max_sec:
        try: dismiss_alerts(d)
        except Exception: pass
        try: cu=(d.current_url or '').lower()
        except Exception: cu=''
        try:
            if d.find_elements(By.CSS_SELECTOR,"input[type='password']"): return True
        except Exception: pass
        try: ps=(d.page_source or '')[:20000].lower()
        except Exception: ps=''
        if 'veritas-hub' in cu or 'challenge' in cu or 'cf-turnstile' in ps:
            if solved>=2: break   # 두 번 풀어도 계속 챌린지면 포기(요청 차단 상태)
            _ok,_m,_t,_i=solve_captcha_with_2captcha(d,site,'turnstile',cfg); solved+=1
            add_log(f'[가입 Turnstile] {site.get("name") or site.get("site_url","")} — {_m}')
            for _ in range(15):
                try: c2=(d.current_url or '').lower()
                except Exception: c2=''
                if 'veritas-hub' not in c2 and 'challenge' not in c2: break
                time.sleep(1)
            try:
                c3=(d.current_url or '').lower()
                if 'veritas-hub' in c3 or 'challenge' in c3: d.get(join_url); time.sleep(2)
                else: time.sleep(1.5)
            except Exception: pass
            continue
        if 'agreement' in cu or '/agree' in cu:
            for cb in d.find_elements(By.CSS_SELECTOR,"input[type='checkbox']"):
                try:
                    nm=((cb.get_attribute('name') or '')+' '+(cb.get_attribute('id') or '')).lower()
                    if ('agree' in nm or 'all' in nm) and not cb.is_selected():
                        try: cb.click()
                        except Exception: d.execute_script('arguments[0].checked=true;arguments[0].dispatchEvent(new Event("change",{bubbles:true}));',cb)
                except Exception: continue
            clicked=False
            for sel in ("button[type='submit']","input[type='submit']","a.btnSubmit","a.btn_submit","button.btnSubmit","a[href*='join']","button","a"):
                for el in d.find_elements(By.CSS_SELECTOR,sel):
                    try:
                        if not el.is_displayed(): continue
                        tx=((el.text or '')+' '+(el.get_attribute('value') or '')+' '+(el.get_attribute('alt') or '')).strip()
                        if sel in ("button[type='submit']","input[type='submit']") or re.search(r'(다음|동의하고|동의|회원가입|가입하기|확인|next|agree)',tx,re.I):
                            d.execute_script('arguments[0].click()',el); clicked=True; break
                    except Exception: continue
                if clicked: break
            time.sleep(2.5)
            if not clicked:
                try: d.get(join_url); time.sleep(2)
                except Exception: pass
            continue
        time.sleep(0.7)
    try: return bool(d.find_elements(By.CSS_SELECTOR,"input[type='password']"))
    except Exception: return False

def auto_signup_guarded(site, submit=True, timeout=100):
    """auto_signup을 타임아웃 보호 하에 실행한다. 작업 스레드가 자기 드라이버로 가입을 수행하고,
       timeout을 넘기면 메인이 그 드라이버를 강제 quit해서 hang된 selenium 호출을 예외로 끊는다.
       → 한 사이트가 무한 hang해 전환루프가 영영 완료 안 되던 문제 해결(김정은산부인과 케이스)."""
    import threading as _th
    # ★cafe24는 측정(Turnstile 해결 30~60초)+약관+폼입력+캡차가 100초를 넘겨 '자동가입 타임아웃(100초)'로 죽었음(gmmusic 2026-09-11).
    if (site.get('platform')=='cafe24') and timeout<220: timeout=220
    box={'done':False,'ret':(False,'타임아웃'),'wtid':None}
    def _work():
        box['wtid']=_th.current_thread().name
        try:
            box['ret']=auto_signup(site,submit=submit)
        except Exception as e:
            box['ret']=(False,f'가입 처리 예외: {str(e)[:80]}')
        finally:
            box['done']=True
    t=_th.Thread(target=_work,name=f'SUWORK-{secrets.token_hex(3)}',daemon=True)
    t.start(); t.join(timeout)
    # ★가입 워커(SUWORK) 스레드의 크롬을 항상 정리(성공/타임아웃 무관). 예전엔 타임아웃 때만 정리해
    #   가입 크롬이 누수됐음 — 병렬 가입(signup_parallel)에선 크롬이 빠르게 쌓여 OOM. 반드시 quit.
    wt=box.get('wtid')
    try:
        with _drv_lock:
            dd=_drivers.pop(wt,None) if wt else None
        if dd:
            try: dd.quit()
            except Exception: pass
    except Exception: pass
    if not box['done']:
        add_log(f'[자동가입 타임아웃] {site.get("name") or site.get("site_url","")} — {timeout}초 초과, 드라이버 리셋 후 다음 후보로')
        return False,f'자동가입 타임아웃({timeout}초 초과)'
    return box['ret']

def auto_signup(site, submit=True):
    """로그인 필요 사이트에 앱이 스스로 회원가입한다(2captcha로 캡차 해결).
       submit=False면 제출 직전까지만(폼 입력·캡차해결) 수행하고 실제 가입은 안 함(검증용).
       반환: (ok, msg). 성공 시 site에 mb_id/mb_pass가 저장돼 있다.
       이메일 인증 필요·지원불가 캡차는 실패로 반환(가입 대상 제외)."""
    from selenium.webdriver.common.by import By
    cfg=load_config()
    _snm=site.get('name') or site.get('site_url','')
    _surl=site.get('site_url','') or ''
    add_log(f'[자동가입 시작] {_snm} {_surl}'.rstrip())   # URL 포함 → 워커로그에서 클릭 가능
    # 1) 가입폼 측정(캐시 30분) — 이메일 인증 필요하면 즉시 제외
    try:
        profile=learn_signup_profile(site,force=False)
    except Exception as e:
        return False,f'가입폼 측정 실패: {str(e)[:100]}'
    if not profile:
        return False,'가입폼을 찾지 못함'
    fields=profile.get('fields') or []
    form_url=profile.get('form_url') or profile.get('signup_url') or site.get('signup_url','')
    if not form_url:
        return False,'가입 폼 URL 불명'
    # 2) 자격증명 생성 (사이트 규칙 반영)
    mid,pw=_signup_credentials(site,profile.get('rules'))
    # 이메일 인증이 필요한 게시판이면 임시메일(mail.tm)로 실제 수신 가능한 주소를 쓴다.
    need_email_verify=bool(profile.get('email_verification_required'))
    tm_addr=tm_pw=tm_token=None
    if need_email_verify:
        tm_addr,tm_pw,tm_token=tempmail_create()
        if not tm_token:
            return False,'이메일 인증 필요 — 임시메일 발급 실패'
        email=tm_addr
        add_log(f'[자동가입] {site.get("name") or site.get("site_url","")} 이메일 인증 → 임시메일 {tm_addr}')
    else:
        email_local=re.sub(r'[^a-z0-9]','',mid.lower())[:20] or ('u'+secrets.token_hex(4))
        email=f'{email_local}@gmail.com'
    nick=(cfg.get('brand') or 'user')+secrets.token_hex(2)
    name=cfg.get('brand') or '홍길동'
    vals_by_role={'id':mid,'password':pw,'password_confirm':pw,'email':email,
                  'nickname':nick,'name':name}
    d=get_driver()
    # 그누보드 가입은 register.php(약관) → 동의 → register_form.php(실제 폼) 2단계다.
    # register_form.php를 GET으로 직접 열면 약관 화면이라 필드가 없다 → 약관부터 진행.
    signup_url=profile.get('signup_url') or site.get('signup_url') or form_url
    def _has_pw_field():
        return bool(_safe_find(d,"input[name='mb_password'],#reg_mb_password,input[type='password']"))
    try:
        d.set_page_load_timeout(25); d.get(signup_url); time.sleep(2); dismiss_alerts(d)
    except Exception:
        pass
    if site.get('platform')=='cafe24':
        try:
            _ju=_signup_origin(site)+'/member/join.html'
            if not _cafe24_signup_gate(d,site,cfg,_ju):
                add_log(f'[자동가입] {_snm} — cafe24 챌린지/약관 관문 통과 실패(비번 칸 미도달)')
        except Exception as _e:
            add_log(f'[자동가입] {_snm} — cafe24 관문 오류 {str(_e)[:60]}')
    # 약관 동의 체크(있으면 전부 체크) 후 '동의' 제출 버튼으로 실제 폼 진입
    for cb in _safe_find(d,"input[type='checkbox']"):
        try:
            nm=(cb.get_attribute('name') or '').lower()
            if any(k in nm for k in ('agree','약관','provision','privacy','all')) or not nm:
                if not cb.is_selected(): d.execute_script('arguments[0].click()',cb)
        except Exception: pass
    if not _has_pw_field():
        # 약관 폼(fregister/register.php)을 제출해 register_form.php로 이동.
        # 제출 버튼이 input이 아니라 커스텀 <button class="fw_terms_btn">인 사이트(forwarder.kr 등)도 커버.
        submitted_agree=False
        for sel in ("#fregister input[type='submit']","#fregister button[type='submit']","#fregister button",
                    "form[name='fregister'] input[type='submit']","form[name='fregister'] button[type='submit']","form[name='fregister'] button",
                    "form[action*='register_form'] input[type='submit']","form[action*='register_form'] button",
                    ".fw_terms_btn","button.fw_terms_btn","button[value*='회원가입']","button[value*='회 원 가 입']",
                    "input[type='submit']","button[type='submit']"):
            for el in _safe_find(d,sel):
                try:
                    if el.is_displayed(): d.execute_script('arguments[0].click()',el); submitted_agree=True; break
                except Exception: pass
            if submitted_agree: break
        # 버튼 클릭이 안 먹으면 fregister 폼을 JS로 직접 제출(onsubmit 우회)
        if not submitted_agree:
            _safe_js(d,"var f=document.getElementById('fregister')||document.forms['fregister'];if(f){if(f.requestSubmit)f.requestSubmit();else f.submit();}")
        time.sleep(2); dismiss_alerts(d)
    if not _has_pw_field():
        # 그래도 없으면 register_form.php를 직접 열되 약관값·기업회원 파라미터를 붙여 접근 시도
        base_o=_signup_origin(site)
        for q in ('?agree=1&agree2=1','?company=1&agree=1&agree2=1','?agree=on&agree2=on'):
            try:
                d.get(base_o+'/bbs/register_form.php'+q); time.sleep(2); dismiss_alerts(d)
                if _has_pw_field(): break
            except Exception: pass
    if not _has_pw_field():
        return False,'가입 폼(비밀번호 입력칸)에 도달 실패 — 약관/인증 단계 확인'
    # 2.5) 휴대폰 본인인증(SMS) 폼은 자동가입 불가 — 임시메일·캡차 비용 쓰기 전에 조기 제외.
    # (본인인증을 통과해야 아이디/이름 칸이 활성화되는 폼. win_hp_cert 등 본인인증 버튼 존재.)
    try:
        page=(d.page_source or '').lower()
        cert_signals=('win_hp_cert' in page or 'nice본인인증' in page or 'checkplus' in page
                      or '휴대폰 본인인증' in (d.page_source or '') or '휴대폰본인인증' in (d.page_source or '')
                      or 'kcb' in page and 'cert' in page)
        # 아이디/이름 등 핵심 가입필드가 hidden이면 본인인증 후 노출되는 폼일 확률이 높다
        name_hidden=bool(_safe_find(d,"input[name='mb_name'][type='hidden'],input[name='mb_id'][type='hidden']"))
        if cert_signals and name_hidden:
            return False,'휴대폰 본인인증 필요 — 자동가입 불가(본인인증 게시판 제외)'
    except Exception: pass
    # 3) role별 필드 자동 입력 — 학습된 셀렉터 우선
    filled=[]
    def _fill(role, sel):
        val=vals_by_role.get(role)
        if not val: return False
        for el in _safe_find(d,sel):
            try:
                if el.is_displayed():
                    el.clear(); el.send_keys(val)
                    if role not in filled: filled.append(role)
                    return True
            except Exception: pass
        return False
    for f in fields:
        role=f.get('role') or ''; sel=f.get('selector') or ''
        if not sel or role in ('','captcha'): continue
        _fill(role, sel)
    # 3.5) 그누보드 표준 필드명 폴백 — 학습 셀렉터로 못 채운 role을 표준 name/id로 재시도한다.
    # (그누보드는 회원가입 필드명이 표준화돼 있어, 학습이 어긋나도 대부분 이 폴백으로 구제된다.)
    GNU_STD={
        'id':"#reg_mb_id,input[name='mb_id'],input[name='user_id'],input[name='userid'],input[name='login_id'],input[name='member_id'],input[name='m_id']",
        'password':"#reg_mb_password,input[name='mb_password'],input[name='user_pw'],input[name='passwd'],input[name='password'],input[name='m_password']",
        'password_confirm':"#reg_mb_password_re,input[name='mb_password_re'],input[name='passwd_re'],input[name='password_re'],input[name='re_password'],input[name='password2'],input[name='passwd_confirm']",
        'email':"#reg_mb_email,input[name='mb_email'],input[name='email'],input[name='user_email'],input[type='email']",
        'name':"input[name='mb_name'],input[name='name'],input[name='user_name']",
        'nickname':"#reg_mb_nick,input[name='mb_nick'],input[name='nickname'],input[name='nick']",
    }
    for role,sel in GNU_STD.items():
        if role not in filled:
            _fill(role, sel)
    # 3.6) type 기반 위치추정 최후 폴백 — name/id가 완전 비표준이라 위에서 못 채운 경우,
    # 화면에 보이는 password 입력칸 순서로 비번/비번확인을, 첫 text 칸을 아이디로 채운다.
    if 'password' not in filled:
        try:
            pws=[el for el in _safe_find(d,"input[type='password']") if el.is_displayed()]
            if pws:
                pws[0].clear(); pws[0].send_keys(pw); filled.append('password')
                if len(pws)>=2 and 'password_confirm' not in filled:
                    pws[1].clear(); pws[1].send_keys(pw); filled.append('password_confirm')
        except Exception: pass
    if 'id' not in filled:
        try:
            # 이미 채운 요소를 제외한, 보이는 첫 text 입력칸을 아이디로 추정
            texts=[el for el in _safe_find(d,"input[type='text'],input:not([type])") if el.is_displayed()
                   and not (el.get_attribute('value') or '').strip()]
            if texts:
                texts[0].clear(); texts[0].send_keys(mid); filled.append('id')
        except Exception: pass
    add_log(f'[자동가입 입력] {site.get("name") or site.get("site_url","")} — 입력: {",".join(filled) or "없음"}')
    if 'id' not in filled or 'password' not in filled:
        return False,f'가입 필수필드 입력 실패(입력됨: {",".join(filled) or "없음"})'
    # 4) 캡차 있으면 2captcha로 해결 후 입력 — 오답/해결실패 대비 최대 3회 재시도(실측: 캡차 36회 풀렸으나 가입 0)
    cap=detect_captcha(d)
    if cap:
        ok=False; cmsg=''; answer=''; info={}; _catt=0
        for _catt in range(1,4):
            ok,cmsg,answer,info=solve_captcha_with_2captcha(d,site,cap,cfg)
            if ok: break
            if _catt<3:
                try: _kcaptcha_force_load(d)   # 새 캡차 이미지 강제 로드 후 재시도
                except Exception: pass
                time.sleep(1)
        if not ok:
            return False,f'가입 캡차({cap}) 해결 {_catt}회 실패: {cmsg}'
        if cap in ('kcaptcha',) and answer:
            done=False
            for csel in ["input[name='captcha_key']","#captcha_key","input[name*='captcha']","input[id*='captcha']"]:
                for el in _safe_find(d,csel):
                    try:
                        if el.is_displayed(): el.clear(); el.send_keys(answer); done=True; break
                    except Exception: pass
                if done: break
        add_log(f'[자동가입 캡차] {site.get("name") or site.get("site_url","")} — {cmsg}')
    if not submit:
        return True,f'가입 직전까지 성공(입력: {",".join(filled)}{" +캡차" if cap else ""}) — 실제 가입 안 함'
    # 5) 제출
    submitted=False
    for sel in ("form#fregister input[type='submit']","form[name='fregister'] input[type='submit']",
                "form#fregister button[type='submit']","#register_form input[type='submit']",
                "form[action*='register_form_update'] input[type='submit']",
                "form[action*='register_form_update'] button[type='submit']"):
        for el in _safe_find(d,sel):
            try:
                if el.is_displayed(): d.execute_script('arguments[0].click()',el); submitted=True; break
            except Exception: pass
        if submitted: break
    if not submitted:
        _safe_js(d,"var f=document.getElementById('fregister')||document.forms['fregister']||document.querySelector(\"form[action*='register_form_update']\");if(f){if(f.requestSubmit)f.requestSubmit();else f.submit();}")
    time.sleep(3); dismiss_alerts(d)
    add_log(f'[자동가입] {_snm} 제출 완료 — 가입 결과 확인 중{" (이메일 인증 필요할 수 있음)" if need_email_verify else ""}')
    # 5.5) 대표님 전략: 먼저 '인증 없이 바로 가입됐는지' 확인 → 됐으면 인증 스킵(꿀사이트).
    #      제출 직후 이미 로그인 상태(로그아웃/마이페이지 노출)면 이메일 인증 불필요 → 바로 성공 처리.
    def _quick_logged_in():
        try: _b=d.find_element(By.TAG_NAME,'body').text[:2500]
        except Exception: _b=''
        _s=(d.page_source or '').lower()
        return ('로그아웃' in _b) or ('logout' in _s) or ('mypage' in _s) or ('마이페이지' in _b) or ('회원정보' in _b)
    _already=_quick_logged_in()
    if _already and need_email_verify:
        need_email_verify=False   # 인증 없이 가입 완료됨 → 인증 스킵(꿀사이트)
        add_log(f'[자동가입] {_snm} — 이메일 인증 없이 가입 완료 확인(꿀사이트) → 인증 생략')
    # 이메일 인증: 위에서 성공 안 됐고 인증이 실제로 필요할 때만 대기(안 오면 60초로 단축)
    if need_email_verify and tm_token:
        add_log(f'[자동가입] {site.get("name") or site.get("site_url","")} 인증메일 대기 중...')
        verify=tempmail_wait_verify_link(tm_token, timeout=60)
        if not verify:
            return False,'이메일 인증 실패 — 인증메일이 오지 않음(게시판 미발송/봇차단 가능)'
        if verify.get('link'):
            try: d.get(verify['link']); time.sleep(2); dismiss_alerts(d)
            except Exception: pass
            add_log(f'[자동가입] 인증링크 방문 완료')
        elif verify.get('code'):
            # 코드 입력형: 인증코드 입력칸을 찾아 넣고 제출
            for csel in ("input[name*='auth']","input[name*='cert']","input[name*='code']","input[id*='auth']","input[id*='code']"):
                done=False
                for el in _safe_find(d,csel):
                    try:
                        if el.is_displayed(): el.clear(); el.send_keys(verify['code']); done=True; break
                    except Exception: pass
                if done:
                    _safe_js(d,"var b=document.querySelector(\"input[type='submit'],button[type='submit']\");if(b)b.click();")
                    time.sleep(2); break
            add_log(f'[자동가입] 인증코드 입력 완료')
    # 6) 가입 성공 검증 (다단계) — ① 제출 직후 신호 ② 가입 후 세션 ③ 로그인 재시도 순.
    raw=site.get('site_url',''); m=re.match(r'(https?://[^/]+)',raw); base=m.group(1) if m else raw
    def _logged_in():
        try: body=d.find_element(By.TAG_NAME,'body').text[:2500]
        except Exception: body=''
        src=(d.page_source or '').lower()
        return ('로그아웃' in body) or ('logout' in src) or ('mypage' in src) or ('회원정보수정' in body) or ('회원정보' in body) or ('마이페이지' in body)
    def _mark_success(msg):
        # ★핵심: 가입한 계정을 넘겨받은 site dict에 직접 심는다. auto_pipeline은 임시 dict
        # (id='cand_...')를 넘기는데, set_site_flag는 저장된 사이트만 갱신하므로 이 대입이 없으면
        # tmp['mb_id']가 빈 채로 남아 발행 단계에서 '비회원 발행'→로그인필요 게시판 거부가 된다.
        # 이 두 줄이 '가입은 됐는데 발행 로그인 실패' 반복의 실제 해결.
        site['mb_id']=mid; site['mb_pass']=pw
        # 인증메일 없이 가입된 게시판(=꿀사이트)은 별도 표시해 신뢰 사이트로 남기고 우선한다.
        # (실측: 이메일 인증 경로는 전환율 0. 무인증 게시판만 안정적으로 발행됨 — 대표님 지시.)
        _no_verify=(not need_email_verify)
        site['no_verify']=_no_verify
        set_site_flag(site.get('id'),mb_id=mid,mb_pass=pw,signup_status='complete',
                      login_saved=True,no_verify=_no_verify,
                      signup_updated_at=datetime.now().isoformat(timespec='seconds'))
        _tag=' 🍯무인증' if _no_verify else ' (이메일인증)'
        add_log(f'[자동가입 성공]{_tag} {site.get("name") or site.get("site_url","")} — {mid} ({msg})')
        return True,f'자동가입 성공 — {mid}'

    # ① 제출 직후 페이지의 명시적 신호 검사 (가입완료 or 중복/오류)
    try:
        pt=d.find_element(By.TAG_NAME,'body').text[:3000]
    except Exception: pt=''
    plow=(d.page_source or '').lower()+' '+pt
    # 실패(반려) 신호 — 이게 있으면 가입이 안 된 것. 정확히 사유를 반환.
    if any(k in pt for k in ('이미 등록된','이미 사용중','이미 사용 중','중복된 아이디','사용중인 아이디','이미 가입')):
        return False,'가입 실패 — 아이디/이메일 중복(다음 시도 시 다른 값 사용)'
    if any(k in pt for k in ('비밀번호는','아이디는','필수','올바르지 않','형식이 맞지','다시 입력')) and '가입' not in pt[:80]:
        return False,f'가입 실패 — 입력값 규칙 위반 가능({pt[:60].strip()})'
    if any(k in pt for k in ('본인인증','휴대폰 인증','실명인증','SMS 인증')):
        return False,'가입 실패 — 본인인증 필요(자동가입 불가)'
    if any(k in pt for k in ('승인 후','관리자 승인','승인이 필요','가입 승인')):
        return False,'가입 보류 — 관리자 승인제 게시판(자동발행 불가)'
    # 성공 신호 — 명시적 완료 문구
    if any(k in pt for k in ('가입을 환영','회원가입이 완료','가입이 완료','환영합니다','가입을 축하','register_result')) or 'register_result' in plow:
        return _mark_success('가입완료 페이지 확인')

    # ② 가입 직후 세션이 이미 로그인 상태인지(그누보드는 가입 즉시 로그인되는 경우 많음)
    try:
        d.get(base); time.sleep(1.5)
        if _logged_in(): return _mark_success('가입 직후 세션 로그인됨')
    except Exception: pass

    # ③ 로그인 재시도 — 로그인 URL 후보를 넓게 시도
    for lurl in (f'{base}/bbs/login.php', f'{base}/login.php', f'{base}/member/login.php', f'{base}/bbs/login_check.php'):
        try:
            d.get(lurl); time.sleep(1.5)
            filled_login=False
            for s2 in (("input[name='mb_id'],input[name='user_id'],input[name='login_id'],#login_id",mid),
                       ("input[name='mb_password'],input[name='user_pw'],input[name='passwd'],input[type='password']",pw)):
                for el in _safe_find(d,s2[0]):
                    try:
                        if el.is_displayed(): el.clear(); el.send_keys(s2[1]); filled_login=True; break
                    except Exception: pass
            if not filled_login: continue
            for lsel in ("form[name='flogin'] input[type='submit']","form[action*='login_check'] input[type='submit']",
                         "form[action*='login'] input[type='submit']","form[action*='login'] button[type='submit']",
                         "#login_fs .btn_submit","input[type='submit']","button[type='submit']"):
                for el in _safe_find(d,lsel):
                    try:
                        if el.is_displayed(): el.click(); break
                    except Exception: pass
            time.sleep(2); dismiss_alerts(d)
            if _logged_in(): return _mark_success('로그인 확인')
        except Exception: continue
    return False,'가입 제출했으나 로그인 확인 실패(가입 규칙·중복ID·승인제·비표준 로그인폼 가능)'

def _safe_find(d, sel):
    try: return d.find_elements(__import__('selenium.webdriver.common.by',fromlist=['By']).By.CSS_SELECTOR, sel) or []
    except Exception: return []

def _safe_js(d, js):
    try: return d.execute_script(js)
    except Exception: return None

def _promote_candidate_to_site(cand, result_url, write_url='', bo='', permission=True):
    """실제 발행 성공한 후보를 사이트 목록으로 승격(api_cand_verified 로직 재사용).
       permission=True면 즉시 발행 허용 ON(완전 자동)."""
    domain=(cand.get('domain') or _domain_of(cand.get('url',''))).lower()
    m=re.match(r'(https?://[^/]+)',cand.get('url','')); base=m.group(1) if m else cand.get('url','')
    bo=bo or cand.get('bo_table') or 'free'
    now=_kst_now().strftime('%Y-%m-%d %H:%M')
    with POST_LOCK:
        sites=load_sites(); site=next((s for s in sites if _domain_of(s.get('site_url',''))==domain),None)
        created=site is None
        if created:
            site={'id':secrets.token_hex(6),'site_url':base,'platform':cand.get('platform','gnuboard'),
                  'mb_id':'','mb_pass':'','bo_table':bo,'name':cand.get('board_name') or domain,
                  'daily_limit':0,'min_interval_minutes':1,'status':'idle',
                  'added':_kst_now().strftime('%m/%d %H:%M')}
            sites.append(site)
        site.update({'bo_table':bo,'write_url':write_url,'verified_post_url':result_url,
                     'write_test_status':'passed','verified_at':now,'last_structure_check':now,
                     'registration_source':'verified_test'})
        if permission:
            site.update({'permission':True,'permission_note':'자동 파이프라인: 실게시 검증 완료',
                         'permission_date':_kst_now().strftime('%Y-%m-%d')})
        else:
            site.setdefault('permission',False)
        save_sites(sites)
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            if c.get('domain','').lower()==domain:
                c.update({'status':'approved','verified_at':now,'verified_post_url':result_url,'site_id':site['id']})
        save_cands(cands)
    add_log(f'[자동등록] {domain} · {bo} · '+('발행허용 ON' if permission else '발행잠금'))
    return site

# ★PC 발행노드(대표님 지시 2026-09-09): 서버·PC가 같은 후보를 겹쳐 처리하지 않도록 claim 잠금.
def _claim_active(c):
    """이 후보가 다른 노드(PC)에 잡혀 있고 아직 만료 안 됐으면 True → 서버 파이프라인은 건너뜀."""
    try: return bool(c.get('claimed_by')) and float(c.get('claim_expire',0) or 0) > time.time()
    except Exception: return False

def _pc_node_alive(within=600):
    """PC/노트북 발행노드가 최근 within초 내 하트비트를 보냈으면 True.
       ★cafe24 라우팅(대표님 승인 2026-09-11): 노드 살아있으면 서버는 cafe24를 안 건드리고 PC에 맡긴다
       (서버 DC IP는 Turnstile 못 넘어 탈락만 내고, 그러면 PC가 볼 기회조차 사라지던 구멍).
       노드가 전부 죽어 있으면 False → 서버가 폴백으로 처리(후보 방치 방지)."""
    try:
        now=time.time()
        with _NODE_LOCK:
            if NODE_BEATS:
                return any(float(b.get('last',0) or 0) > now-within for b in NODE_BEATS.values())
        # ★부팅 직후 유예: NODE_BEATS는 메모리라 재시작마다 비워짐 → 노드가 다시 beat하기 전 ~30초를
        #   '노드 죽음'으로 오판해 cafe24를 서버가 집어 탈락내던 누수 방지. 부팅 180초 내 무beat면 살아있다고 간주
        #   (노드는 자동시작이라 곧 beat함). 180초 지나도 침묵이면 진짜 죽은 것 → 폴백.
        return (now-_BOOT_EPOCH) < 180
    except Exception:
        return False

def _llm_for_nodes():
    """PC 노드가 글 생성에 쓸 LLM 설정(대표님 2026-09-11 '비용 절감'): 서버 설정탭이 3대의 단일 기준.
       노드는 로컬 config를 쓰므로 서버에서 NVIDIA를 켜도 노드는 옛 OpenAI 키로 가던 빈틈을 메움.
       Brave 키를 발굴노드에 내려주는 것과 같은 패턴(토큰 인증 응답에만 실림)."""
    c=load_config()
    return {'provider':(c.get('llm_provider') or 'openrouter').strip().lower(),
            'nvidia_api_key':(c.get('nvidia_api_key') or '').strip(),
            'nvidia_model':(c.get('nvidia_model') or '').strip(),
            'openrouter_api_key':(c.get('openrouter_api_key') or '').strip(),
            'openrouter_model':(c.get('openrouter_model') or '').strip()}

def _has_write_path(c):
    """게시판형(글쓰기 가능성) 후보인지 — auto_pipeline_once와 /api/pipeline/claim 공통 판정(DRY)."""
    if c.get('write_form'): return True
    if c.get('platform') in ('gnuboard','cafe24','kboard'): return True
    if c.get('promo_ok') or c.get('board_name'): return True
    u=(c.get('url') or '').lower()
    return bool(re.search(r'(bbs/|board\.php|write\.php|bo_table=|/board/|board_no=|kboard)', u))

def auto_pipeline_once(limit=5):
    """완전 자동 파이프라인: ready 후보 → (필요시)자동가입 → 실제 글1건 발행 → 성공시 자동등록.
       배치당 limit개만 처리(부하·탐지 회피). 반환: 요약 dict."""
    cfg=load_config()
    if not cfg.get('auto_pipeline_enabled'):
        return {'ok':False,'error':'auto_pipeline 비활성화'}
    # 테스트 발행 키워드: 대표님 작업실/스케줄 통합 풀에서 랜덤 추출(없으면 기본값). 하드코딩 제거.
    _tk_pool=collect_all_keywords()
    def _test_kw():
        return pick_keywords(_tk_pool,cfg) if _tk_pool else {'지역':'인천','서비스':'셔츠룸','브랜드':cfg.get('brand','') or '테스트'}
    site_domains={_domain_of(s.get('site_url','')) for s in load_sites()}
    with _cand_lock:
        cands=load_cands()
        # 자동가입 실패로 rejected된 게시판을 하루 지나면 ready로 되살려 새 코드로 재시도한다.
        # (자동가입 로직이 개선돼도 옛 실패가 rejected로 굳어 재시도조차 안 되던 문제 방지.)
        # 무한 반복 방지: 후보당 재시도 3회까지. 불법/주차/영구탈락은 되살리지 않는다.
        revived=0; today_s=_kst_now().strftime('%Y-%m-%d')
        for c in cands:
            rr=str(c.get('reject_reason',''))
            if (c.get('status')=='rejected' and '자동가입 실패' in rr
                    # 인증벽(본인인증·SMS·실명·성인 등)은 되살려도 무의미 → 영구탈락(전략5)
                    and not any(w in rr for w in ['본인인증','실명인증','휴대폰','SMS','문자인증','아이핀','성인인증','19금','인증필요'])
                    and not c.get('illegal') and not c.get('parked')
                    and int(c.get('signup_retry',0) or 0) < 3
                    and str(c.get('signup_retry_date',''))!=today_s):
                c['status']='ready'; c['signup_retry']=int(c.get('signup_retry',0) or 0)+1
                c['signup_retry_date']=today_s; revived+=1
        if revived:
            save_cands(cands); add_log(f'[자동가입 재시도] 이전 실패 게시판 {revived}곳 재시도 대상 복원')
        # ★빡센검수 소급 적용(대표님 지시 2026-09-09 '대기 너무 쌓임'): strict_screen 켜져 있으면,
        #   기존 backlog의 ready 후보 중 '홍보 흔적 없는 것'(방치·개방 게시판 아님)을 대기줄에서 탈락.
        #   (예전 느슨한 검수로 통과된 것들이 가입 안 되며 대기만 쌓이던 문제 — 될 만한 것만 남긴다.)
        if cfg.get('strict_screen',True):
            pruned=0
            for c in cands:
                if c.get('status')!='ready': continue
                if c.get('write_form') and not c.get('login_required'): continue   # 비회원(바로발행)은 유지
                _pc=int(c.get('promo_phone_count',0) or 0)
                if _pc>=1 or c.get('promo_hint'): continue                         # 홍보흔적 있으면 유지
                # ★로그인필요 + 홍보흔적 없음 = 가입해도 될 확률 낮은 사이트 → 대기줄에서 탈락(출처 무관).
                #   (밤새 PC발굴이 manual로 오태깅돼 최근3시간 보호에 걸려 안 지워지던 문제 — 보호 제거.
                #    진짜 수동추가로 지키고 싶은 건 비회원이거나 홍보흔적 있어 위에서 이미 유지됨.)
                c['status']='rejected'; c['reject_reason']='빡센검수(소급) — 홍보글 흔적 없음(가입해도 될 확률 낮음)'
                pruned+=1
            if pruned:
                save_cands(cands); add_log(f'[빡센검수 정리] 홍보흔적 없는 대기 후보 {pruned}곳 탈락 — 될 만한 것만 남김','정리')
    # 대상: 검수완료(ready) + 아직 사이트 미등록 + 자동탈락 아님 + '글쓰기 가능성'이 있는 것.
    # (114/맵 등 전화번호·디렉토리 사이트는 게시판이 아니라 제외. 그 외 게시판형 후보는
    #  글쓰기폼 미확인이라도 일단 자동가입→발행 시도해 되는지 판별한다 — 방치 없이 되거나 탈락)
    # _has_write_path는 모듈 레벨(PC claim과 공통). 오류안내 페이지 후보는 처리 전 즉시 탈락.
    for _c in cands:
        if _c.get('status') in ('ready','new') and any(k in str(_c.get('title') or '') for k in ERROR_PAGE_HINTS):
            try: _cand_set(_c['id'],status='rejected',reject_reason='오류안내 페이지'); _c['status']='rejected'
            except Exception: pass
    # ★이메일 인증·본인인증(휴대폰) 필요 후보는 자동가입 시도 안 하고 '수동가입 대기'로 분류
    #   (대표님 지시 2026-09-08: 자동불가라 크롬·2captcha 낭비. 대표님이 직접 계정 넣으면 발행가능).
    #   검수(screen_candidate)가 미리 판정한 signup_email_verify·signup_phone_cert 사용.
    for _c in cands:
        if _c.get('status') in ('ready','approved') and (_c.get('signup_email_verify') or _c.get('signup_phone_cert')):
            _why='본인인증(휴대폰) 필요' if _c.get('signup_phone_cert') else '이메일 인증 필요'
            try:
                _cand_set(_c['id'],status='manual_signup',reject_reason=f'{_why} — 자동가입 불가, 대표님 수동가입 대기')
                _c['status']='manual_signup'
            except Exception: pass
    # 쿨다운: 최근 20분 내 시도한 후보는 제외 → 한 사이트(예: 김정은)가 실패/hang해도
    # 곧바로 다시 잡혀 루프를 독점하지 않게. 다른 후보에게 순서가 돌아간다.
    _cool=time.time()-1200
    # ★status 'ready'뿐 아니라 'approved'(대표님이 UI에서 승인한 후보)도 전환 대상에 포함.
    #   (approved 후보 14곳이 파이프라인이 ready만 봐서 방치되던 문제 — 대표님 "사이트 안 늚")
    pend=[c for c in cands
          if c.get('screened') and c.get('status') in ('ready','approved')
          and not c.get('parked') and not c.get('illegal') and not c.get('ad_banned')
          and (c.get('domain') or '').lower() not in site_domains
          and c.get('reachable') and _has_write_path(c)
          and not _claim_active(c)   # ★PC 노드가 잡고 있는(만료 전) 후보는 서버가 건드리지 않음
          and not (c.get('platform')=='cafe24' and _pc_node_alive())   # ★cafe24는 PC(로컬크롬)만 — 서버(DC IP)는 Turnstile 못넘어 탈락만 냄. 노드 다 죽으면 폴백.
          and float(c.get('last_pipeline_at',0) or 0) < _cool]
    # (파이프라인 진단 로그 제거 — 처리할 후보 없을 때마다 매 주기 찍혀 화면 도배. 대표님 지시)
    # 비회원 글쓰기 가능(로그인 불필요) 게시판을 먼저 처리한다. 로그인 필요 게시판은
    # 이메일 인증 등으로 자동가입이 막히는 경우가 많아 배치 슬롯을 낭비하기 쉽다.
    def _prio(c):
        direct = c.get('write_form') and not c.get('login_required')  # 바로 발행 가능
        no_cap = not c.get('captcha')                                  # 캡차 없으면 더 빠름
        return (1 if direct else 0, 1 if no_cap else 0, c.get('score',0))
    pend.sort(key=_prio, reverse=True)
    # 수동으로 직접 넣은 URL(source=manual)은 대표님이 지정한 것이므로 무조건 최우선 처리.
    _manual=[c for c in pend if c.get('source')=='manual']
    _rest=[c for c in pend if c.get('source')!='manual']
    # 비회원(로그인 불필요) 우선으로 배치를 채우고, 로그인 필요 게시판은 소수만 처리한다.
    # (로그인 게시판은 이메일 인증 대기로 슬롯을 낭비 → 전환율 저하. 대표님 지시: 비회원 우선.)
    _guest=[c for c in _rest if c.get('write_form') and not c.get('login_required')]
    _login=[c for c in _rest if not (c.get('write_form') and not c.get('login_required'))]
    # 로그인 풀은 '무인증(꿀사이트)' 우선 정렬 — 이메일 인증 필요로 판명된 후보는 맨 뒤로.
    # (실측: 인증 경로 전환율 0. 인증 안 받는 게시판만 안정적으로 가입·발행됨 — 대표님 지시.)
    def _login_prio(c):
        need_verify=bool(c.get('signup_email_verification') or c.get('email_verification_required'))
        no_cap=not c.get('captcha')
        return (0 if need_verify else 1, 1 if no_cap else 0, c.get('score',0))
    _login.sort(key=_login_prio, reverse=True)
    # ★자동가입 전담 병렬(대표님 지시 2026-09-08 '워커 다른 데 쓰기'): 가입 실패 92%가 최대 병목.
    #   가입은 이메일 인증 대기로 느려 순차 처리하면 슬롯 낭비 → 후보들을 병렬 스레드로 동시 처리.
    #   로그인 후보 상한도 크게(병렬이라 슬롯 안 막힘). 동시 크롬은 전역 세마포어로 상한.
    _login_cap=int(cfg.get('signup_parallel',4) or 4)*3   # 병렬이라 더 많이 잡아도 됨
    pend=_manual + _guest[:max(1,limit)] + _login[:max(0,_login_cap)]
    done=0; registered=0; signed=0; results=[]
    _pipe_lock=threading.Lock()
    _sfan=max(1,min(6,int(cfg.get('signup_parallel',4) or 4)))
    _ssem=threading.Semaphore(_sfan)          # 동시 가입/발행테스트 수(이 배치 내)
    _gsem=_global_chrome_sem()                # 전역 크롬 상한(발행 워커와 공유)
    def _proc(c):
      with _ssem, _gsem:
        nonlocal done,registered,signed
        name=c.get('board_name') or c.get('domain') or c.get('url','')[:30]
        # 쿨다운 기록: 이 후보를 방금 시도했음을 남겨, 실패/hang해도 다음 사이클에 곧바로 다시
        # 잡아 루프를 독점하지 않게 한다(김정은처럼 한 사이트가 파이프라인을 막던 문제 해결).
        try: _cand_set(c['id'], last_pipeline_at=time.time())
        except Exception: pass
        # 임시 site dict(발행 함수는 site 형태를 기대) — 후보 정보로 구성
        _curl=c.get('url','')
        m=re.match(r'(https?://[^/]+)',_curl); base=m.group(1) if m else _curl
        tmp={'id':'cand_'+c.get('id',''),'site_url':base,'platform':c.get('platform','gnuboard'),
             'bo_table':c.get('bo_table') or 'free','name':name,'mb_id':'','mb_pass':''}
        # ★Cafe24: 처음 발굴한 URL(글목록 /article|/board/{명}/{no}/)을 진입점으로(대표님 지시 2026-09-11
        #   '처음 발굴한 사이트로 접속→로그인→진짜 글쓰기 링크'). 이게 없으면 write.html 추측→404.
        if c.get('platform')=='cafe24':
            _am=re.search(r'/(?:article|board)/([^/]+)/(\d+)/', _curl)
            if _am and _am.group(1).lower() not in ('write','list','read','view','product','free'):
                tmp['article_board_name']=_am.group(1); tmp['bo_table']=_am.group(2)
                tmp['write_entry_url']=base+f'/board/{_am.group(1)}/{_am.group(2)}/'
            elif _curl:
                tmp['write_entry_url']=_curl   # /article/ 아니어도 발굴 URL을 진입 후보로
        # ★대표님 지시: 이미 등록된 사이트에 저장된 계정(mb_id/mb_pass)이 있으면 그걸 써서 로그인 발행한다.
        #   (계정이 있는데 새로 자동가입하려다 '중복ID'로 실패하던 버그 — sungsim처럼 대표님이 준 계정)
        try:
            _dom0=(c.get('domain') or _domain_of(c.get('url',''))).lower()
            _saved=next((s for s in load_sites()
                         if _domain_of(s.get('site_url',''))==_dom0 and str(s.get('mb_id') or '').strip()),None)
            if _saved:
                tmp['mb_id']=_saved.get('mb_id',''); tmp['mb_pass']=_saved.get('mb_pass','')
                tmp['bo_table']=_saved.get('bo_table') or tmp['bo_table']
                add_log(f'[계정 재사용] {name} — 저장된 계정({tmp["mb_id"]})으로 로그인 발행(자동가입 생략)')
        except Exception: pass
        try:
            _just_signed=False
            kw=_test_kw()
            html,title=generate_article(kw,cfg,unique=True)
            # 대표님 파이프라인 순서:
            #  ① 접속은 검수/발굴 단계에서 이미 확인(reachable) → 여기선 통과한 것만 옴
            #  ② 먼저 '비회원 글쓰기'로 바로 발행 시도 — 로그인 명백히 필요한 게 아니면 가입 없이 시도.
            #  ③ 로그인 필요로 튕기면 그때 자동가입 → 세션 재사용 재발행.
            #  ④ 자동가입은 auto_signup 내부에서 '이메일 없이 먼저, 안 되면 인증' 순서로 처리.
            # 저장된 계정이 이미 있으면 자동가입 건너뛰기 — 그 계정으로 do_post가 로그인 발행한다.
            _has_account = bool(str(tmp.get('mb_id') or '').strip())
            _login_first = (not _has_account) and bool(c.get('login_required')) and not c.get('write_form')
            if _login_first:
                # 게시판 자체가 로그인 필수 + 저장된 계정 없음 → 자동가입으로 계정 생성(성공시 저장·재사용)
                ok_su,msg_su=auto_signup_guarded(tmp,submit=True)
                if ok_su:
                    with _pipe_lock: signed+=1
                    _just_signed=True
                else:
                    _cand_set(c['id'],status='rejected',reject_reason=f'자동가입 실패: {msg_su[:80]}')
                    with _pipe_lock: results.append({'name':name,'stage':'signup','ok':False,'msg':msg_su})
                    add_log(f'[자동가입 실패] {name} — {str(msg_su)[:90]}')
                    return   # (병렬 처리: continue → return)
            # 발행 시도(②: 비회원 우선, 또는 방금 가입한 세션으로)
            ok,msg=do_post(tmp,title,html,skip_login=_just_signed)
            # 검수는 비회원 글쓰기로 봤지만 실제 write.php가 로그인으로 튕기는 게시판이 있다.
            # 이 경우 자동가입 후 1회 재시도(gjsec처럼 login_required 오판된 케이스 구제).
            # ★Cafe24는 '글쓰기 페이지 못찾음'도 대개 로그인 필요 → 자동가입 트리거에 포함(대표님 지시 2026-09-11).
            _need_su=re.search(r'(로그인이 필요|로그인 실패|로그인 화면|권한이 없|권한 없)',str(msg)) or \
                     (tmp.get('platform')=='cafe24' and ('글쓰기 페이지 못찾음' in str(msg) or '게시판번호' in str(msg)))
            if (not ok) and (not tmp.get('mb_id')) and _need_su:
                reset_driver(); time.sleep(1)
                add_log(f'[파이프라인] {name} 로그인필요 → 자동가입 시도')
                ok_su,msg_su=auto_signup_guarded(tmp,submit=True)
                if ok_su:
                    with _pipe_lock: signed+=1
                    add_log(f'[파이프라인] {name} 자동가입 성공 → 세션 재사용 재발행')
                    # reset_driver 하지 않음 — 가입 직후 로그인된 세션을 그대로 써서 발행(비표준 로그인폼 구제)
                    ok,msg=do_post(tmp,title,html,skip_login=True)
                else:
                    add_log(f'[파이프라인] {name} 자동가입 실패: {str(msg_su)[:80]}')
                    msg=f'{msg} · 자동가입 실패: {str(msg_su)[:60]}'
            result_url=msg if (ok and str(msg).startswith(('http://','https://'))) else ''
            if ok and result_url:
                _promote_candidate_to_site(c,result_url,write_url=tmp.get('learned',{}).get('write_url','') if isinstance(tmp.get('learned'),dict) else '',
                                           bo=tmp.get('bo_table'),permission=True)
                # 가입정보가 생겼으면 등록 사이트에 반영
                if tmp.get('mb_id'):
                    set_site_flag(_promoted_site_id(c),mb_id=tmp.get('mb_id'),mb_pass=tmp.get('mb_pass'))
                with _pipe_lock:
                    registered+=1
                    results.append({'name':name,'stage':'post','ok':True,'url':result_url})
                add_log(f'[발행가능 등록] {name} — 검증 통과 → 발행가능 {result_url}'.rstrip())  # URL 포함 → 클릭 가능
            elif ok:
                _cand_set(c['id'],status='rejected',reject_reason='발행됨(결과 URL 확인 불가)')
                with _pipe_lock: results.append({'name':name,'stage':'post','ok':False,'msg':'결과 URL 없음'})
                add_log(f'[탈락] {name} — 발행됐으나 결과 URL 확인 불가')
            else:
                reason,_,is_temp=classify_fail(msg)
                # 일시적 실패라도 무한 재시도로 배치 슬롯을 소모하지 않게 상한(5회)을 둔다.
                attempts=int(c.get('pipeline_attempts',0) or 0)+1
                if is_temp and attempts<5:
                    _cand_set(c['id'],status='ready',reject_reason=f'일시적 실패({attempts}/5): {str(msg)[:60]}',pipeline_attempts=attempts)
                    add_log(f'[발행 재시도] {name} ({attempts}/5) — {str(msg)[:70]}')
                else:
                    _cand_set(c['id'],status='rejected',reject_reason=(str(msg)[:90] if not is_temp else f'재시도 {attempts}회 초과: {str(msg)[:60]}'),pipeline_attempts=attempts)
                    add_log(f'[탈락] {name} — {str(msg)[:80]}')  # 안 되는 곳 탈락 로그
                with _pipe_lock: results.append({'name':name,'stage':'post','ok':False,'msg':str(msg)[:90]})
        except Exception as e:
            with _pipe_lock: results.append({'name':name,'stage':'error','ok':False,'msg':str(e)[:100]})
        finally:
            with _pipe_lock: done+=1
            reset_driver(); time.sleep(1)   # 각 스레드 자기 크롬 정리(병렬이라 긴 대기 불필요)
    # ★후보들을 병렬 스레드로 동시 처리(가입 전담 병렬). 각 스레드=자기 크롬, 전역 세마포어로 상한.
    _pthreads=[]
    for c in pend:
        t=threading.Thread(target=_proc,args=(c,),name=f'SIGNUP-{c.get("id","")[:6]}',daemon=True)
        t.start(); _pthreads.append(t)
    for t in _pthreads: t.join()
    # 3) 등록됐지만 '준비됨/캡차대기/메일대기'에서 멈춘 사이트: 자동가입을 실제로 끝내고
    #    스케줄/작업실 랜덤 키워드로 발행 테스트까지 완료 → 성공 시 발행가능으로 등록.
    prepared=[s for s in load_sites()
              if s.get('signup_status') in ('prepared','captcha_wait','email_wait')
              and not str(s.get('verified_post_url') or '').startswith(('http://','https://'))
              and s.get('status')!='rejected']
    for s in prepared[:max(1,limit)]:
        nm=s.get('name') or (s.get('site_url','') or '')[:30]
        try:
            ok_su,msg_su=auto_signup_guarded(s,submit=True)
            if not ok_su:
                at=int(s.get('signup_complete_attempts',0) or 0)+1
                if at>=3:
                    set_site_flag(s.get('id'),status='rejected',permission=False,
                                  auto_drop_reason=f'가입완료 실패 {at}회: {str(msg_su)[:50]}')
                    add_log(f'[가입완료 실패→탈락] {nm}: {str(msg_su)[:60]}')
                else:
                    set_site_flag(s.get('id'),signup_complete_attempts=at)
                    add_log(f'[가입완료 재시도 {at}/3] {nm}: {str(msg_su)[:60]}')
                continue
            signed+=1
            fresh=next((x for x in load_sites() if x.get('id')==s.get('id')),s)
            kw=_test_kw()
            html,title=generate_article(kw,cfg,unique=True)
            # 가입 직후 로그인된 세션 그대로 발행(재로그인 생략) — 비표준 로그인폼 사이트 구제
            ok,msg=do_post(fresh,title,html,skip_login=True)
            if (not ok) and re.search(r'(로그인|login)',str(msg)):
                # 세션이 안 잡힌 예외 → 정식 로그인으로 1회 폴백
                ok,msg=do_post(fresh,title,html,skip_login=False)
            result_url=msg if (ok and str(msg).startswith(('http://','https://'))) else ''
            if ok and result_url:
                set_site_flag(s.get('id'),write_test_status='passed',verified_post_url=result_url,
                              verified_at=_kst_now().strftime('%Y-%m-%d %H:%M'),permission=True,status='idle')
                registered+=1
                add_log(f'[가입완료+발행테스트 성공] {nm} → 발행가능 등록 (키워드 {kw.get("지역","")}{kw.get("서비스","")})')
            else:
                add_log(f'[발행테스트 실패] {nm}: {str(msg)[:70]}')
        except Exception as e:
            add_log(f'[가입완료 오류] {nm}: {str(e)[:70]}')
        finally:
            done+=1
            reset_driver(); time.sleep(2)
    dropped=reconcile_sites()   # 파이프라인 후 사이트 목록 최신화(안 되는 곳 자동삭제)
    # 공회전(처리·가입·등록·삭제 전부 0)은 로그 생략 — 화면 도배 방지(대표님 지시).
    if done or signed or registered or dropped:
        add_log(f'[자동파이프라인] 처리 {done} · 가입 {signed} · 등록 {registered}'+(f' · 자동삭제 {dropped}' if dropped else ''))
    return {'ok':True,'processed':done,'signed_up':signed,'registered':registered,'dropped':dropped,'results':results}

def _cand_set(cid, **fields):
    rejected_dom=None
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            if c.get('id')==cid:
                c.update(fields)
                # 탈락 처리되면 도메인을 영구 탈락 목록에 기록(다음 발굴에서 제외)
                if fields.get('status')=='rejected':
                    rejected_dom=(c.get('domain') or _domain_of(c.get('url','')))
        save_cands(cands)
    if rejected_dom:
        add_rejected_domains(rejected_dom, fields.get('reject_reason',''))

def _promoted_site_id(cand):
    dom=(cand.get('domain') or _domain_of(cand.get('url',''))).lower()
    for s in load_sites():
        if _domain_of(s.get('site_url',''))==dom: return s.get('id')
    return None

def collect_all_keywords():
    """상시발행용 키워드 통합 — 전역 풀(keywords.json) + 모든 작업실(workrooms.json)
       + 모든 회원(고객, members.json)의 전용 키워드를 합친다.
       반환: [{'지역','서비스','브랜드'}, ...]. 대표님이 어디에 넣든 상시발행이 쓸 수 있게."""
    pool=list(load_keywords() or [])   # 이미 dict 리스트
    try:
        for room in (load_json(WORKROOMS_FILE,[]) or []):
            for line in str(room.get('keyword_csv') or '').splitlines():
                raw=line.strip()
                if not raw or raw.startswith('#'): continue
                p=[x.strip() for x in raw.split(',')]
                if len(p)>=3 and all(p[:3]):
                    pool.append({'지역':p[0],'서비스':p[1],'브랜드':p[2]})
                elif len(p)>=2 and p[0] and p[1]:
                    pool.append({'지역':p[0],'서비스':p[1],'브랜드':(p[2] if len(p)>2 else '')})
                elif p and p[0]:
                    pool.append(_auto_subkeywords(p[0]))   # 메인 1개 → 서브 자동생성
    except Exception:
        pass
    # 회원(고객) 전용 키워드도 상시발행 풀에 합친다 — 회원관리에 넣은 키워드가 24시간 발행에 안 쓰이던 문제 해결.
    # members.json의 keywords는 이미 {'지역','서비스','브랜드'} dict 리스트(회원 저장 시 파싱됨).
    try:
        for m in (load_members() or []):
            for kw in (m.get('keywords') or []):
                if isinstance(kw,dict) and (kw.get('지역') or kw.get('서비스')):
                    pool.append({'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),'브랜드':kw.get('브랜드','')})
    except Exception:
        pass
    return pool

_WR_CURSOR={}   # workroom_id -> 다음 발행할 조합 '순열 위치'(메모리). 재시작 시 0부터.
_WR_ORDER={}    # workroom_id -> 섞은 조합 인덱스 순열(지역 편중 방지). 한 바퀴 소진 시 재셔플.
_WR_RR=[0]      # 작업실 라운드로빈 포인터(모든 작업실을 번갈아 동시 진행)
_WR_PICK_LOCK=threading.Lock()  # ★워크스틸링: 여러 워커가 '다음 조합'을 겹치지 않게 뽑도록 보호(대표님 '하이브리드')

def _pick_next_combo(rooms):
    """★공유풀 워크스틸링(대표님 지시 2026-09-09): 고정 담당 없이, 모든 워커가 이 함수로
       '다음 발행할 (작업실, 조합)'을 잠금 하에 하나씩 꺼낸다 → 놀지 않고 서로 도와 발행.
       - 작업실은 _WR_RR로 라운드로빈(전 작업실 골고루), 조합은 _WR_ORDER 셔플순열로 순회(지역 편중 방지).
       - 반환: (room, kw, cur1based, total) 또는 None(발행할 조합 없음)."""
    if not rooms: return None
    with _WR_PICK_LOCK:
        room=rooms[_WR_RR[0]%len(rooms)]; _WR_RR[0]=(_WR_RR[0]+1)%max(1,len(rooms))
        combos=_workroom_combos(room); rid=room.get('id','')
        if not combos: return None
        order=_WR_ORDER.get(rid)
        if not order or len(order)!=len(combos):
            order=list(range(len(combos))); random.shuffle(order); _WR_ORDER[rid]=order
        cur=int(_WR_CURSOR.get(rid,0) or 0)
        if cur>=len(order):
            cur=0; random.shuffle(order); _WR_ORDER[rid]=order   # 새 바퀴: 다시 섞기
        idx=order[cur]; _WR_CURSOR[rid]=cur+1
        return (room, combos[idx], cur+1, len(combos))

def _workroom_combos(room):
    """작업실 keyword_csv → [{'지역','서비스','브랜드'}, ...] (한 줄=한 조합=한 글).
       ★대표님 지시(2026-09-07): 한 줄에 메인 키워드 1개만 넣으면(콤마 없음)
         '메인만 한 줄' 모드로 보고, 서브2·3은 발행 시 그 구/동에 맞춰 자동 생성한다.
         (콤마로 3개 넣으면 기존처럼 고정 조합)"""
    combos=[]
    for line in str(room.get('keyword_csv') or '').splitlines():
        raw=line.strip()
        if not raw or raw.startswith('#'): continue   # 빈 줄·메모(#) 제외
        p=[x.strip() for x in raw.split(',')]
        if len(p)>=3 and all(p[:3]):
            combos.append({'지역':p[0],'서비스':p[1],'브랜드':p[2]})   # 고정 3개 조합(기존)
        elif len(p)>=2 and p[0]:
            combos.append({'지역':p[0],'서비스':p[1],'브랜드':(p[2] if len(p)>2 else '')})
        elif p and p[0]:
            # 메인 1개만 → 발행 시 서브 자동생성. main으로 표시.
            combos.append({'_main':p[0],'_main_only':True})
    return combos

_WR_SLOTS={}   # slot_index -> Thread (작업실 발행 슬롯 워커 = 동시 크롬)
_MEM_WARN=['']   # VPS 메모리 축소 경고 중복 방지용(마지막 경고 상태)

def _publish_combo_to_site(s, kw, wname, rid, cfg, writer_name=''):
    """한 조합(kw)을 '한 사이트'에 유니크 글로 발행. _publish_one_combo가 사이트마다 병렬 호출.
       각 호출은 자기 스레드의 크롬(get_driver는 스레드명 기반)을 써서 서로 간섭 안 함.
       ★같은 사이트 동시발행은 _site_lock으로 방지(도배·간격 우회 방지)."""
    if not cfg.get('publish_loop_enabled'): return
    fresh=next((x for x in load_sites() if x.get('id')==s.get('id')),None)
    if not fresh or not is_publishable(fresh): return
    _slk=_site_lock(fresh.get('id'))
    if not _slk.acquire(blocking=False): return   # 다른 슬롯/조합이 이 사이트 발행 중 → 스킵
    try:
        if writer_name: fresh['writer_name']=writer_name
        if rid: fresh['workroom_id']=rid
        if not under_daily_limit(fresh,cfg): return
        if not under_min_interval(fresh)[0]: return
        pub_kw=_auto_subkeywords(kw.get('_main','')) if kw.get('_main_only') else _fix_kw_dong(kw)
        try:
            html,title=generate_article(pub_kw,cfg,unique=True,workroom_id=rid)
        except Exception as e:
            add_log(f"[작업실:{wname}] 생성오류 {str(e)[:50]}"); return
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); jid=secrets.token_hex(8)
        history_add({'id':jid,'time':now,'updated':now,'site_id':fresh.get('id'),
            'site_name':fresh.get('name') or fresh.get('site_url',''),'site_url':fresh.get('site_url',''),
            'bo_table':fresh.get('bo_table',''),'title':title,'region':pub_kw.get('지역',''),'service':pub_kw.get('서비스',''),
            'brand':pub_kw.get('브랜드',''),
            'workroom_id':rid,'workroom_name':wname,'status':'posting','result_url':'','message':'','attempts':0})
        ok=False; msg=''
        for attempt in range(1,4):
            try:
                ok,msg=do_post(fresh,title,html)
                if ok: break
                reset_driver()
            except Exception as e:
                msg=str(e); reset_driver()
            if attempt<3: time.sleep(min(5*attempt,15))
        reason=reason_ko=''
        if not ok: reason,reason_ko,_=classify_fail(msg)
        try: finalize_post(fresh,ok,fail_reason=('' if ok else str(msg)))
        except Exception: pass
        # ★비밀글 감지·비번 기록(대표님 지시 2026-09-08): 발행 성공했는데 그 글이 '비밀글'로 보호되면
        #   구글도 못읽음(SEO0). 사용한 고정 비번을 이력·로그에 남겨 대표님이 나중에 열람 가능하게.
        _pw=_post_password(cfg); _secret=False
        if ok and str(msg).startswith('http'):
            try: _secret=str(_post_read_block_reason(msg) or '').startswith('비밀글')
            except Exception: _secret=False
        # ★비밀글이면 '성공'으로 치지 않는다(대표님 지시 2026-09-11 '비밀글 발행 막기'): 구글이 못읽어
        #   헛발행. 그 사이트는 secret_forced 표시 → 다음부터 발행 대상에서 제외.
        if _secret:
            try: set_site_flag(fresh.get('id'),secret_forced=True,secret_at=_kst_now().strftime('%Y-%m-%d %H:%M'))
            except Exception: pass
            ok=False; reason_ko='비밀글(구글 색인불가)'; reason='secret_post'
        history_update(jid,status='done' if ok else 'failed',
            result_url=(msg if ok and str(msg).startswith('http') else ''),
            fail_reason=('' if ok else reason),fail_reason_ko=('' if ok else reason_ko),
            post_password=_pw, is_secret=_secret,      # 이력에 비번·비밀글여부 저장
            alive=('yes' if ok and str(msg).startswith('http') else ''),message=str(msg)[:300])
        with STATS_LOCK:
            if ok: wk_stats['success']+=1
            else: wk_stats['fail']+=1
            wk_stats['done']+=1
        add_log(f"[작업실:{wname}] {'성공' if ok else '실패:'+reason_ko} {fresh.get('name') or (fresh.get('site_url','') or '')[:20]}"
                +(f" 🔒비밀글→발행중단(구글 색인불가)" if _secret else ''))
    finally:
        _slk.release()

def _publish_one_combo(kw, wname, rid, cfg, writer_name=''):
    """한 조합(kw)을 '발행가능 사이트 전체'에 병렬로 동시 발행(대표님 '속도가 생명').
       ★기존엔 사이트를 한 개씩 순차(+사이트마다 30초 대기)라 12곳이면 매우 느렸다.
       → publish_fanout(기본4)개씩 스레드로 동시 발행. 각 스레드=자기 크롬. 같은사이트는 _site_lock."""
    sites=[x for x in load_sites() if is_publishable(x)]
    if not sites: return
    fan=max(1,min(6,int(cfg.get('publish_fanout',4) or 4)))
    _sem=threading.Semaphore(fan)          # 이 조합의 동시 발행 수(작업실 슬롯 내)
    _gsem=_global_chrome_sem()             # 전역 크롬 상한(모든 슬롯 통틀어)
    threads=[]
    def _one(site):
        with _sem, _gsem:                  # 조합 내 상한 + 전역 크롬 상한 둘 다 만족해야 발행
            try: _publish_combo_to_site(site,kw,wname,rid,cfg,writer_name=writer_name)
            except Exception as e: add_log(f"[작업실:{wname}] 발행스레드 오류 {str(e)[:50]}")
            finally:
                # ★이 발행스레드의 크롬 정리(스레드명 기반 캐시라 스레드 죽으면 누수 → 명시 종료).
                try: reset_driver()
                except Exception: pass
    for s in sites:
        if not cfg.get('publish_loop_enabled'): break
        t=threading.Thread(target=_one,args=(s,),name=f'PUB-{s.get("id","")[:6]}-{secrets.token_hex(2)}',daemon=True)
        t.start(); threads.append(t)
    for t in threads: t.join()   # 이 조합의 모든 사이트 발행 완료까지 대기(다음 조합으로)

def workroom_worker(slot):
    """발행 슬롯 워커(독립 크롬 스레드). ★워크스틸링(대표님 '하이브리드' 2026-09-09):
       고정 담당 없이 _pick_next_combo로 공유풀에서 다음 조합을 하나씩 꺼내 발행 → 놀지 않고
       서로 도와 발행한다(작업실 2개라도 워커 6개가 그 2개를 6갈래로 나눠 처리).
       동시 워커 수(=동시 크롬)는 publish_loop가 메모리 여유에 맞춰 spawn하는 need로 상한.
       작업실이 없으면 슬롯0이 통합풀(전역+회원) 랜덤 발행으로 폴백."""
    add_log(f"[작업실워커 시작] 슬롯 {slot+1} (워크스틸링)")
    while True:
        try:
            cfg=load_config()
            if not cfg.get('publish_loop_enabled'):
                time.sleep(20); continue
            rooms=[r for r in (load_json(WORKROOMS_FILE,[]) or []) if _workroom_combos(r)]
            if not rooms:
                if slot==0:   # 작업실 없음 → 슬롯0이 통합풀 폴백
                    pool=collect_all_keywords()
                    if pool: _publish_one_combo(pick_keywords(pool,cfg),'통합풀','',cfg)
                    else: add_log('[상시발행] 키워드 작업실에 조합을 추가하세요 (비어있음)')
                    time.sleep(20)
                else:
                    time.sleep(30)   # 다른 슬롯은 통합풀 중복 발행 방지 위해 대기
                continue
            if not any(is_publishable(s) for s in load_sites()):
                time.sleep(30); continue
            picked=_pick_next_combo(rooms)   # 공유풀에서 다음 (작업실,조합) 하나 꺼냄(겹침 없음)
            if not picked:
                time.sleep(5); continue
            room,kw,cur,total=picked
            _kwlabel=(kw.get('_main') or f"{kw.get('지역','')}{kw.get('서비스','')}")
            add_log(f"[작업실:{room.get('name','')}] 조합 {cur}/{total} ({_kwlabel}) 발행 시작 (슬롯 {slot+1})")
            _publish_one_combo(kw,room.get('name',''),room.get('id',''),cfg,writer_name=str(room.get('writer_name') or '').strip())
        except Exception as e:
            add_log(f"[작업실워커 오류 슬롯{slot+1}] {str(e)[:70]}")
            time.sleep(10)
    _WR_SLOTS.pop(slot,None)
    try: reset_driver()   # 이 슬롯 스레드의 크롬 정리
    except Exception: pass

def publish_loop():
    """작업실 발행 슬롯 매니저: config workroom_workers 개(작업실 수 이하)의 독립 크롬 워커를
       유지해 작업실들을 나눠 맡아 '진짜 동시' 발행한다. 죽은 슬롯은 재스폰, 작업실이 늘면 슬롯 증설.
       발행 실제 로직은 workroom_worker/_publish_one_combo가 담당(post_queue 미사용).
       ※ 동시 크롬 수가 많아 VPS가 버거우면 workroom_workers를 낮추거나 VPS 업그레이드."""
    _last_clean=[0]
    while True:
        try:
            # ★주기적 디스크 정리(Errno 28 방지): 30분마다 크롬 임시프로파일·백업 삭제.
            if time.time()-_last_clean[0] > 1800:
                _last_clean[0]=time.time()
                try: cleanup_disk()
                except Exception: pass
            cfg=load_config()
            if cfg.get('publish_loop_enabled'):
                rooms=[r for r in (load_json(WORKROOMS_FILE,[]) or []) if _workroom_combos(r)]
                cap=max(1,int(cfg.get('workroom_workers',3) or 3))
                # ★워크스틸링(대표님 '하이브리드'): 작업실 수로 제한하지 않고 설정한 워커수(cap)만큼 띄운다.
                #   단 전체 조합 수보다 많을 필요는 없으니 그걸 상한으로(조합 1개뿐인데 6워커는 낭비).
                _totcombos=sum(len(_workroom_combos(r)) for r in rooms)
                need=max(1,min(cap,max(1,_totcombos)))   # 작업실 없으면 1(통합풀 폴백)
                # VPS 보호: 가용 메모리가 부족하면 동시 워커(크롬) 수를 자동 축소한다.
                # 민감도는 설정으로 조절(대표님 요청: 가드 민감도 낮춤). 크롬 1개당 추정치·예비를 낮추면
                # 같은 메모리에서 더 많은 워커를 허용(단 OOM 위험↑). 기본: 예비 350MB, 크롬당 300MB.
                try:
                    _reserve=int(cfg.get('vps_reserve_mb',350) or 350)
                    _per=max(150,int(cfg.get('vps_mb_per_worker',300) or 300))
                    mt=open('/proc/meminfo').read()
                    avail=int(re.search(r'MemAvailable:\s+(\d+)',mt).group(1))//1024   # MB
                    mem_cap=max(1,(avail-_reserve)//_per)
                    if mem_cap<need:
                        if _MEM_WARN[0]!=f'{need}->{mem_cap}':
                            add_log(f'[VPS 경고] 메모리 부족(가용 {avail}MB) — 동시 발행 워커 {need}→{mem_cap}개로 자동 축소. 처리량을 위해 VPS 업그레이드(RAM 증설)를 권장합니다.','정리')
                            _MEM_WARN[0]=f'{need}->{mem_cap}'
                        need=mem_cap
                    else:
                        _MEM_WARN[0]=''
                except Exception:
                    pass   # /proc 없는 환경(로컬 등)은 건너뜀
                for slot in list(_WR_SLOTS.keys()):
                    t=_WR_SLOTS.get(slot)
                    if (not t) or (not t.is_alive()) or slot>=need:
                        _WR_SLOTS.pop(slot,None)
                for slot in range(need):
                    t=_WR_SLOTS.get(slot)
                    if (not t) or (not t.is_alive()):
                        nt=threading.Thread(target=workroom_worker,args=(slot,),name=f'WR{slot+1}',daemon=True)
                        _WR_SLOTS[slot]=nt; nt.start()
        except Exception as e:
            add_log(f'[작업실 매니저 오류] {str(e)[:100]}')
        time.sleep(30)

def discover_loop():
    """24시간 자동 발굴 — 목표치까지 천천히 채우고 남는 시간엔 검수."""
    while True:
        try:
            cfg=load_config()
            provider=(cfg.get('search_provider') or 'brave').lower()
            ready=bool(cfg.get('brave_api_key')) if provider=='brave' else bool(cfg.get('google_api_key') and cfg.get('google_cx'))
            # 발행가능 사이트가 목표(site_goal)에 도달하면 발굴 중단 — Brave 크레딧 절약(전략C).
            # 막혀서 목표 밑으로 떨어지면 자동 재개된다.
            pub_count=len([s for s in load_sites() if is_publishable(s)])
            goal=int(cfg.get('site_goal',500) or 500)
            if cfg.get('discover_enabled') and ready and pub_count < goal:
                discover_once(cfg,max_queries=int(cfg.get('discover_batch',50) or 50))
            elif cfg.get('discover_enabled') and pub_count >= goal:
                # 목표 달성 — 발굴 멈추고 미검수 후보만 정리
                if any(not c.get('screened') for c in load_cands()): screen_pending(10)
            else:
                # 발굴 꺼져 있어도 미검수 후보는 계속 처리
                if any(not c.get('screened') for c in load_cands()): screen_pending(10)
            # 제외 도메인(설정)에 걸린 기존 후보를 자동 탈락 — 나중에 제외목록에 추가해도 이미
            # 발굴된 후보가 안 사라지던 문제(대표님 지시: clickn.co.kr 등 제외했는데 후보에 남음).
            try:
                _pruned=0
                with _cand_lock:
                    _cds=load_cands(); _chg=False
                    for _c in _cds:
                        if _c.get('status')=='rejected': continue
                        if _is_blacklisted(_c.get('url','')):
                            _c['status']='rejected'; _c['reject_reason']='제외 도메인(설정) — 자동 탈락'; _chg=True; _pruned+=1
                    if _chg: save_cands(_cds)
                if _pruned: add_log(f'[제외도메인 정리] {_pruned}개 후보 자동 탈락(설정 제외목록 반영)')
            except Exception as e: add_log(f'[제외도메인 정리 오류] {str(e)[:70]}')
            # 사이트 목록 상시 최신화(막힌 곳 자동 탈락). 후보→가입→발행 '전환'은 별도 pipeline_loop이
            # 독립적으로 돌린다(발굴이 루프를 독차지해 전환이 굶던 문제 해결 — 대표님 지시).
            try: reconcile_sites()
            except Exception as e: add_log(f'[자동정리 오류] {str(e)[:80]}')
        except Exception as e:
            add_log(f'[발굴 루프 오류] {str(e)[:100]}')
        time.sleep(int(load_config().get('discover_interval_sec',600) or 600))   # 10분마다 (크레딧 절약 — 하루에 몰아 안 쓰고 분산. 한도는 discover_once가 지킴)

def pipeline_loop():
    """전환 전용 루프: 발굴과 독립적으로 auto_pipeline_once를 돌려 후보→가입→발행테스트→등록을
       꾸준히 처리한다(발굴이 루프를 독차지해 전환이 굶던 문제 해결). pipeline_interval_sec(기본 120초)."""
    time.sleep(25)   # 부팅 직후 복구/발굴과 겹치지 않게 약간 지연
    while True:
        try:
            cfg=load_config()
            if cfg.get('auto_pipeline_enabled'):
                _t0=time.time()
                r=auto_pipeline_once(limit=int(cfg.get('auto_pipeline_batch',20) or 20))
                _el=int(time.time()-_t0)
                # 공회전(처리·가입·등록 전부 0)은 로그 생략 — 화면 도배 방지(대표님 지시).
                #   뭔가 실제로 처리됐을 때만 완료 로그를 남긴다.
                if isinstance(r,dict) and (r.get('processed',0) or r.get('signed_up',0) or r.get('registered',0)):
                    add_log(f'[전환루프] 1회 완료 ({_el}초) — 처리 {r.get("processed",0)} · 가입 {r.get("signed_up",0)} · 등록 {r.get("registered",0)}')
        except Exception as e:
            add_log(f'[전환루프 오류] {str(e)[:120]}')
        try: time.sleep(int(load_config().get('pipeline_interval_sec',120) or 120))
        except Exception: time.sleep(120)

def member_paid_now(m):
    """이번 달 납부 완료 여부."""
    pays=m.get('payments',{}) or {}
    return bool((pays.get(_cur_month(),{}) or {}).get('paid'))

def member_runnable(m,cfg):
    """이 회원의 스케줄을 지금 돌려도 되는가. (사유, 가능여부)"""
    if m.get('status','active')!='active': return False,'정지 회원'
    if not m.get('sched_enabled'): return False,'스케줄 꺼짐'
    if not (m.get('sched_times') or []): return False,'시간대 미설정'
    if cfg.get('block_unpaid') and not member_paid_now(m): return False,'미납 — 자동 정지'
    return True,''

def member_sites(m):
    """회원에게 배정된 발행 가능 사이트(미배정이면 전체 허용 사이트)."""
    ids=m.get('site_ids') or []
    sites=[s for s in load_sites() if (not ids or s.get('id') in ids)]
    return [s for s in sites if is_publishable(s)]

def member_keywords(m):
    """회원 전용 키워드 풀(없으면 공용 풀)."""
    kw=m.get('keywords') or []
    return kw if kw else load_keywords()

def run_member_job(mid, minute_key):
    """회원 1명의 스케줄 1회 실행 — 지터 대기 후 발행 큐 등록."""
    try:
        cfg=load_config()
        m=next((x for x in load_members() if x.get('id')==mid),None)
        if not m: return
        jitter=int(m.get('jitter',0) or 0)
        if jitter>0:
            time.sleep(random.randint(0,jitter*60))   # 시간 분산: 동시 폭주 방지
        m=next((x for x in load_members() if x.get('id')==mid),None)  # 대기 중 변경 반영
        if not m: return
        ok,why=member_runnable(m,cfg)
        if not ok:
            add_log(f'[회원스케줄:{m.get("name") or m.get("biz")}] 건너뜀 — {why}'); return
        sites=member_sites(m); pool=member_keywords(m)
        nm=m.get('name') or m.get('biz') or mid
        if not sites: add_log(f'[회원스케줄:{nm}] 발행 가능 사이트 없음'); return
        if not pool: add_log(f'[회원스케줄:{nm}] 키워드 없음'); return
        cnt=max(1,int(m.get('per_run',1) or 1))
        total=0
        for _ in range(cnt):
            kw=pick_keywords(pool,cfg)
            total+=enqueue_generated(sites,{'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),
                                            '브랜드':kw.get('브랜드','') or cfg.get('brand','')},cfg,
                                     {'region':kw.get('지역',''),'service':kw.get('서비스',''),'member':nm})[0]
        # 실행 기록
        mem=load_members()
        for x in mem:
            if x.get('id')==mid:
                x['last_run']=_kst_now().strftime('%Y-%m-%d %H:%M')
                x['run_count']=int(x.get('run_count',0) or 0)+1
                x['last_run_min']=minute_key
        save_members(mem)
        add_log(f'[회원스케줄:{nm}] {total}건 큐 등록 (사이트 {len(sites)}개 × {cnt}회)')
        if total and not wk_active: start_workers(cfg.get('workers',2))
        if cfg.get('notify_done'): send_telegram(cfg,f'⏰ {nm} 자동발행 {total}건 등록')
    except Exception as e:
        add_log(f'[회원스케줄 오류] {str(e)[:100]}')

def member_scheduler_loop():
    """jump 방식: 회원마다 설정한 시간대에 서버가 24시간 자동 구동."""
    last_min=None
    while True:
        try:
            now=_kst_now(); hm=now.strftime('%H:%M'); wd=now.weekday()
            minute_key=now.strftime('%Y-%m-%d %H:%M')
            if minute_key!=last_min:
                last_min=minute_key
                cfg=load_config()
                for m in load_members():
                    if hm not in (m.get('sched_times') or []): continue
                    days=m.get('sched_days') or []
                    if days and wd not in days: continue
                    if m.get('last_run_min')==minute_key: continue
                    ok,why=member_runnable(m,cfg)
                    if not ok:
                        add_log(f'[회원스케줄:{m.get("name") or m.get("biz")}] 시간 도달했으나 건너뜀 — {why}')
                        continue
                    # 즉시 선점 기록(중복 실행 방지) 후 백그라운드 실행
                    mem=load_members()
                    for x in mem:
                        if x.get('id')==m.get('id'): x['last_run_min']=minute_key
                    save_members(mem)
                    threading.Thread(target=run_member_job,args=(m.get('id'),minute_key),daemon=True).start()
        except Exception as e:
            add_log(f'[회원스케줄러 오류] {str(e)[:100]}')
        time.sleep(20)

def settle_summary():
    """이번 달 정산 요약(활성 회원 기준)."""
    cm=_cur_month(); mem=load_members()
    active=[m for m in mem if m.get('status','active')=='active']
    billed=sum(member_fee(m) for m in active)
    paid=sum(member_fee(m) for m in active if ((m.get('payments',{}) or {}).get(cm,{}) or {}).get('paid'))
    unpaid_list=[{'id':m.get('id'),'name':m.get('name') or m.get('biz',''),'biz':m.get('biz',''),
                  'fee':member_fee(m)} for m in active
                 if not ((m.get('payments',{}) or {}).get(cm,{}) or {}).get('paid')]
    return {'month':cm,'members':len(mem),'active':len(active),
            'billed':billed,'paid':paid,'unpaid':billed-paid,
            'unpaid_count':len(unpaid_list),'unpaid_list':unpaid_list}

sched_active=True
def scheduler_loop():
    """1분 단위로 스케줄 확인 → 조건 맞으면 발행 큐 등록. days: 0=월..6=일, 빈 리스트=매일."""
    last_min=None
    while sched_active:
        try:
            now=_kst_now(); hm=now.strftime('%H:%M'); wd=now.weekday(); today=now.strftime('%Y-%m-%d')
            minute_key=now.strftime('%Y-%m-%d %H:%M')
            if minute_key!=last_min:
                last_min=minute_key
                cfg=load_config(); changed=False
                # ---- 매일 자동 백업(텔레그램) ----
                bt=(cfg.get('backup_time') or '').strip()
                if bt and hm==bt:
                    st=load_json(DATA_DIR/'backup.state',{})
                    if st.get('date')!=today:
                        save_json(DATA_DIR/'backup.state',{'date':today})
                        try: do_backup(cfg,'자동')
                        except Exception as e: add_log(f'[백업 오류] {str(e)[:80]}')
                scheds=load_scheds()
                for sc in scheds:
                    if not sc.get('enabled',True): continue
                    if hm not in (sc.get('times') or []): continue
                    days=sc.get('days') or []
                    if days and wd not in days: continue
                    if sc.get('last_run_min')==minute_key: continue
                    sc['last_run_min']=minute_key; sc['last_run']=now.strftime('%Y-%m-%d %H:%M'); changed=True
                    # 예약에 키워드를 직접 넣지 않으면 공용 키워드 풀을 사용한다.
                    # 완료 키는 schedules.json에 저장하므로 재시작해도 이미 사용한 키워드를 반복하지 않는다.
                    source_sets=sc.get('keyword_sets') or load_keywords()
                    completed=set(sc.get('completed_keys') or [])
                    remaining=[k for k in source_sets if schedule_keyword_key(k) not in completed]
                    if not remaining:
                        sc['enabled']=False; sc['completed_at']=now.strftime('%Y-%m-%d %H:%M')
                        changed=True
                        add_log(f'[예약 완료:{sc.get("name")}] 모든 키워드 사용 완료 — 스케줄 자동 종료')
                        if cfg.get('notify_done'):
                            send_telegram(cfg,f'🏁 {sc.get("name")} 모든 키워드 완료 · 스케줄 자동 종료')
                        continue
                    cnt=max(1,int(sc.get('count',1) or 1))
                    ksets=remaining[:cnt]
                    sids=sc.get('site_ids') or []
                    sites=[s for s in load_sites() if not sids or s.get('id') in sids]
                    allowed=[s for s in sites if is_permitted(s)]
                    if not allowed:
                        add_log(f'[예약:{sc.get("name")}] 허용 사이트 없음 — 건너뜀'); continue
                    n=0
                    for ks in ksets:
                        kw={'지역':ks.get('지역',''),'서비스':ks.get('서비스',''),'브랜드':ks.get('브랜드','')}
                        # 사이트마다 유니크 본문 생성(중복 방지)
                        added=enqueue_generated(allowed,kw,cfg,{'region':kw['지역'],'service':kw['서비스']})[0]
                        n+=added
                        if added:
                            completed.add(schedule_keyword_key(ks))
                    sc['completed_keys']=sorted(completed); changed=True
                    left=sum(1 for k in source_sets if schedule_keyword_key(k) not in completed)
                    if left==0:
                        sc['enabled']=False; sc['completed_at']=now.strftime('%Y-%m-%d %H:%M')
                        add_log(f'[예약 완료:{sc.get("name")}] 모든 키워드 큐 등록 완료 — 스케줄 자동 종료')
                        if cfg.get('notify_done'):
                            send_telegram(cfg,f'🏁 {sc.get("name")} 모든 키워드 완료 · 스케줄 자동 종료')
                    add_log(f'[예약 실행:{sc.get("name")}] {n}건 큐 등록')
                    if n and not wk_active: start_workers(cfg.get('workers',2))
                if changed: save_scheds(scheds)
        except Exception as e:
            add_log(f'[스케줄러 오류] {str(e)[:80]}')
        time.sleep(20)

# ==================== 텔레그램 폰 제어 (명령 수신) ====================
TG_HELP=('📱 찌라시 봇 명령어\n'
         '/상태 — 워커·큐 현황\n'
         '/오늘 — 오늘 발행 통계\n'
         '/발행 — 키워드 풀에서 랜덤 뽑아 전체 발행(원클릭)\n'
         '/발행 지역,서비스[,브랜드] — 지정 키워드로 즉시 발행\n'
         '/정지 · /재개 — 워커 일시정지/재개\n'
         '/백업 — 지금 백업 파일 전송\n'
         '/검증 — 발행글 생존 확인 실행')

def handle_tg_command(cfg,text):
    t=(text or '').strip(); low=t.lower()
    def reply(m): send_telegram(cfg,m)
    if low in ('/help','/start','/도움말') or t in ('도움말','명령어'):
        reply(TG_HELP); return
    if t.startswith('/상태') or low.startswith('/status'):
        reply(f'📊 상태\n워커: {"ON" if wk_active else "OFF"}{" (일시정지)" if wk_paused else ""}\n'
              f'큐: {post_queue.qsize()} · 재시도대기: {len(_retry_jobs)}\n'
              f'성공 {wk_stats["success"]} · 실패 {wk_stats["fail"]} · 스킵 {wk_stats["skipped"]}'); return
    if t.startswith('/오늘') or low.startswith('/today'):
        today=_kst_now().strftime('%Y-%m-%d'); h=load_json(HISTORY_FILE,[])
        th=[x for x in h if (x.get('time') or '').startswith(today)]
        ok=sum(1 for x in th if x.get('status')=='done'); fl=sum(1 for x in th if x.get('status')=='failed')
        reply(f'📅 오늘({today})\n성공 {ok} · 실패 {fl} · 전체 {len(th)}건'); return
    if t.startswith('/정지') or low.startswith('/pause'):
        pause_workers(); reply('⏸ 워커 일시정지'); return
    if t.startswith('/재개') or low.startswith('/resume'):
        resume_workers()
        if not wk_active: start_workers(cfg.get('workers',2))
        reply('▶️ 워커 재개'); return
    if t.startswith('/백업') or low.startswith('/backup'):
        reply('📦 백업 생성 중...'); okb,err=do_backup(cfg,'텔레그램'); reply('✅ 백업 전송 완료' if okb else '❌ 실패: '+err); return
    if t.startswith('/검증') or low.startswith('/verify'):
        reply('🔎 생존 확인 실행...(백그라운드)')
        threading.Thread(target=verify_once,kwargs={'limit':40},daemon=True).start(); return
    if t.startswith('/발행') or low.startswith('/post'):
        parts_split=t.split(None,1)
        arg=(parts_split[1] if len(parts_split)>1 else '').replace('，',',')
        parts=[x.strip() for x in arg.split(',') if x.strip()]
        sites=[s for s in load_sites() if is_publishable(s)]
        if not sites: reply('⚠️ 발행 가능한 사이트가 없습니다(허용·캡차없음)'); return
        # 인자 없으면 키워드 풀에서 랜덤(폰 원클릭)
        if len(parts)<2:
            pool=load_keywords()
            if not pool: reply('형식: /발행 지역,서비스[,브랜드]\n또는 키워드 풀을 등록하면 /발행 만으로 랜덤 발행'); return
            kw=pick_keywords(pool,cfg)
        else:
            kw={'지역':parts[0],'서비스':parts[1],'브랜드':(parts[2] if len(parts)>2 and parts[2] else cfg.get('brand',''))}
        try:
            n=enqueue_generated(sites,{'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),'브랜드':kw.get('브랜드','')},cfg,{'region':kw.get('지역',''),'service':kw.get('서비스','')})[0]
            if n and not wk_active: start_workers(cfg.get('workers',2))
            reply(f'📤 {n}건 큐 등록 (사이트별 유니크)\n키워드: {kw.get("지역","")} {kw.get("서비스","")}')
        except Exception as e:
            reply('❌ 생성 실패: '+str(e)[:100])
        return
    reply('알 수 없는 명령입니다. /help')

_tg_offset={'v':0}
def telegram_loop():
    """텔레그램 getUpdates 롱폴링. 등록된 chat_id 만 명령 처리."""
    import time as _t
    while True:
        cfg=load_config()
        if not (cfg.get('telegram_control') and cfg.get('telegram_token') and cfg.get('telegram_chat_id')):
            _t.sleep(5); continue
        tok=cfg['telegram_token']; chat=str(cfg['telegram_chat_id'])
        try:
            import requests as _rq
            r=_rq.get(f"https://api.telegram.org/bot{tok}/getUpdates",
                      params={'offset':_tg_offset['v']+1,'timeout':30},timeout=40)
            j=r.json()
            for up in j.get('result',[]):
                _tg_offset['v']=up['update_id']
                msg=up.get('message') or up.get('edited_message') or {}
                frm=str((msg.get('chat') or {}).get('id',''))
                text=(msg.get('text') or '').strip()
                if not text or frm!=chat: continue   # 인증: 등록된 챗만
                try: handle_tg_command(cfg,text)
                except Exception as e: add_log(f'[텔레그램 처리 오류] {str(e)[:80]}')
        except Exception:
            _t.sleep(5)

# ==================== 사이트 헬스체크 (HTTP) ====================
def site_health(site):
    """로그인/글쓰기 페이지가 살아있는지 가벼운 HTTP 점검(셀레니움 없이)."""
    import requests as _rq
    url=site.get('site_url','').rstrip('/')
    m=re.match(r'(https?://[^/]+)',url); base=m.group(1) if m else url
    bo=site.get('bo_table','free')
    out={'reachable':False,'login_form':False,'write_page':False,'note':''}
    try:
        r=_rq.get(base,timeout=12,verify=False,headers={'User-Agent':'Mozilla/5.0'})
        out['reachable']=r.status_code<500
        try:
            lp=_rq.get(base+'/bbs/login.php',timeout=12,verify=False,headers={'User-Agent':'Mozilla/5.0'})
            out['login_form']=("mb_id" in lp.text and "mb_password" in lp.text)
        except Exception: pass
        try:
            wp=_rq.get(base+f'/bbs/write.php?bo_table={bo}',timeout=12,verify=False,headers={'User-Agent':'Mozilla/5.0'})
            out['write_page']=(wp.status_code<500 and ('wr_subject' in wp.text or 'wr_content' in wp.text or '로그인' in wp.text))
        except Exception: pass
    except Exception as e:
        out['note']=str(e)[:80]
    out['ok']=out['reachable'] and (out['login_form'] or out['write_page'])
    return out

# ==================== 통계 집계 ====================
def compute_stats():
    h=load_json(HISTORY_FILE,[])
    total=len(h); ok=sum(1 for x in h if x.get('status')=='done'); fail=sum(1 for x in h if x.get('status')=='failed')
    skip=sum(1 for x in h if x.get('status')=='skipped')
    alive=sum(1 for x in h if x.get('alive')=='yes'); dead=sum(1 for x in h if x.get('alive')=='no')
    alive_rate=round(alive/(alive+dead)*100,1) if (alive+dead) else 0.0
    reasons={}   # 실패 원인 분류 집계
    by_site={}; by_day={}
    for x in h:
        sn=x.get('site_name') or '(미상)'; d=(x.get('time') or '')[:10]; st=x.get('status')
        bs=by_site.setdefault(sn,{'done':0,'failed':0,'other':0,'alive':0,'dead':0})
        bs['done' if st=='done' else 'failed' if st=='failed' else 'other']+=1
        if x.get('alive')=='yes': bs['alive']+=1
        elif x.get('alive')=='no': bs['dead']+=1
        bd=by_day.setdefault(d,{'done':0,'failed':0})
        if st in ('done','failed'): bd[st]+=1
        if st=='failed':
            rk=x.get('fail_reason_ko') or '기타'; reasons[rk]=reasons.get(rk,0)+1
    rate=round(ok/(ok+fail)*100,1) if (ok+fail) else 0.0
    # ★일별 통계(대표님 지시 2026-09-09): 데이터 있는 날만 뽑지 말고 최근 14일 '연속 달력'으로.
    #   빈 날도 0으로 채워 항상 14개 칸이 실제 날짜에 정렬되게(안 그러면 4일치가 14일인 척 늘어남).
    _now=datetime.now().astimezone()
    days=[]
    for i in range(13,-1,-1):
        dd=(_now-timedelta(days=i)).strftime('%Y-%m-%d')
        v=by_day.get(dd,{'done':0,'failed':0})
        days.append((dd,v))
    top_sites=sorted(by_site.items(),key=lambda kv:-(kv[1]['done']+kv[1]['failed']))[:12]
    return {'total':total,'ok':ok,'fail':fail,'skip':skip,'rate':rate,
            'alive':alive,'dead':dead,'alive_rate':alive_rate,
            'reasons':[{'reason':k,'n':v} for k,v in sorted(reasons.items(),key=lambda kv:-kv[1])],
            'by_day':[{'day':d,'done':v['done'],'failed':v['failed']} for d,v in days],
            'by_site':[{'site':s,**v} for s,v in top_sites]}

# ==================== Flask ====================
app=Flask(__name__)

def _get_secret():
    """세션 서명키 — 하드코딩 대신 env 또는 persist 파일에서 로드 (없으면 랜덤 생성)."""
    env=os.environ.get('CHIRASHI_SECRET')
    if env: return env
    kf=DATA_DIR/'secret.key'
    try:
        if kf.exists(): return kf.read_text().strip()
        s=secrets.token_hex(32); kf.write_text(s); return s
    except: return secrets.token_hex(32)
app.secret_key=_get_secret()
# 세션 쿠키 보안: JS 접근 차단·HTTPS 전용·크로스사이트 제한
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=True)

# ---- 크롤/스크래핑/색인/임베드 차단 ----
BLOCK_UA=['bot','crawler','spider','scrapy','ahrefs','semrush','mj12','dotbot','python-requests',
          'httpclient','curl','wget','headless','phantom','slurp','baiduspider','yandex','censys','zgrab']
@app.after_request
def _sec_headers(resp):
    # 검색엔진 색인·아카이브 금지 + 임베드(아이프레임) 차단 + 스니핑·캐시 방지
    resp.headers['X-Robots-Tag']='noindex, nofollow, noarchive, nosnippet, noimageindex'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Content-Security-Policy']="frame-ancestors 'none'"
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['Referrer-Policy']='no-referrer'
    # 캐시 완전 방지 — Cloudflare가 옛 화면(구버전)을 붙잡는 문제 해결.
    # Cloudflare는 Cache-Control만으론 자체 규칙으로 캐시할 수 있어, CDN 전용 헤더도 함께 보낸다.
    resp.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['CDN-Cache-Control']='no-store'
    resp.headers['Cloudflare-CDN-Cache-Control']='no-store'
    resp.headers['Pragma']='no-cache'
    resp.headers['Permissions-Policy']='geolocation=(), camera=(), microphone=()'
    return resp

@app.errorhandler(Exception)
def _backend_error(err):
    """API 예외는 HTML 오류 페이지 대신 일관된 JSON으로 기록·반환."""
    if isinstance(err,HTTPException): return err
    try: add_log(f'[API 오류] {request.method} {request.path} - {type(err).__name__}: {str(err)[:160]}')
    except Exception: pass
    if request.path.startswith('/api/'):
        return jsonify({'ok':False,'error':'서버 처리 중 오류가 발생했습니다'}),500
    return ('Internal Server Error',500)

def auth():
    return session.get('login',False)

# ---- 로그인 브루트포스 방어 (IP당 5분 내 6회 실패 시 잠금) ----
_login_hits=defaultdict(list); _login_lock=threading.Lock()
def _client_ip():
    xff=request.headers.get('X-Forwarded-For','')
    return (xff.split(',')[0].strip() if xff else request.remote_addr) or '?'
def login_allowed(ip):
    now=time.time()
    with _login_lock:
        arr=[t for t in _login_hits[ip] if now-t<300]; _login_hits[ip]=arr
        return len(arr)<6
def login_fail(ip):
    with _login_lock: _login_hits[ip].append(time.time())
def check_pw(pw):
    """평문/해시 모두 지원 + env(CHIRASHI_PASSWORD) 우선."""
    stored=os.environ.get('CHIRASHI_PASSWORD') or load_config().get('password','admin1234')
    if isinstance(stored,str) and (stored.startswith('pbkdf2:') or stored.startswith('scrypt:')):
        try: return check_password_hash(stored,pw)
        except: return False
    return pw==stored

@app.route('/robots.txt')
def robots():
    from flask import Response
    return Response("User-agent: *\nDisallow: /\n",mimetype='text/plain')

@app.before_request
def chk():
    # 토큰 조회/관리: 올바른 ?token= 이면 UA·로그인 통과.
    #  /api/logs·/api/worker-log = 읽기전용 로그. /api/sites·/api/candidates = 사이트/후보 관리.
    #  /api/test/* = 발행 테스트 트리거(등록 사이트에 실제 글1건 발행해 검증).
    _p=request.path
    if _p=='/api/version': return  # 배포 SHA 확인 — 공개(민감정보 없음)
    if _p in ('/api/logs','/api/worker-log','/api/sites','/api/sites/creds','/api/sites/purge-secret','/api/sites/reject','/api/sites/unlock-cafe24','/api/openai/usage','/api/config/clear-key','/api/candidates','/api/candidates/ingest','/api/candidates/revive-cafe24','/api/rejected-domains','/api/discovery/queries','/api/pipeline/claim','/api/pipeline/report','/api/pipeline/claim-sites','/api/pipeline/report-site','/api/unlocker/test','/api/sbr/test') or _p.startswith('/api/test/'):
        tok=(request.args.get('token') or '').strip()
        cfgtok=(load_config().get('log_token') or '').strip()
        if cfgtok and tok==cfgtok:
            return  # 통과
    # ★업로드 이미지(/media/)는 공개 — 외부 게시판이 게시글의 <img>를 로드해야 하므로
    #   로그인·UA차단 없이 접근 가능해야 한다(안 그러면 로그인HTML을 받아 이미지가 깨짐 — 대표님 제보).
    if request.path.startswith('/media/'): return
    # 알려진 크롤러/스크래퍼 User-Agent 즉시 차단(로그인·업데이트 제외)
    if request.path not in ['/robots.txt','/api/admin/update']:
        ua=(request.headers.get('User-Agent','') or '').lower()
        if not ua or any(b in ua for b in BLOCK_UA):
            # ★/api/ 요청은 403도 JSON으로(HTML 반환 시 프런트가 'Unexpected token <'로 파싱실패·배너도배).
            if request.path.startswith('/api/'):
                return jsonify({'ok':False,'error':'forbidden'}),403
            return ('Forbidden',403)
    if request.path=='/robots.txt': return
    if request.path.startswith('/static'): return
    if request.path in ['/login','/logout']: return
    if request.path=='/api/admin/update': return  # 자체 토큰 인증
    if not auth():
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({'ok':False,'error':'로그인이 만료되었습니다'}),401
        return redirect('/login')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        ip=_client_ip()
        if not login_allowed(ip):
            return R('로그인',error='로그인 시도 초과 - 잠시 후 다시 시도하세요')
        if check_pw(request.form.get('pw','')):
            session['login']=True; return redirect('/')
        login_fail(ip)
        return R('로그인',error='비밀번호 틀림')
    return R('로그인',error='')

@app.route('/logout')
def logout():
    session.clear(); return redirect('/login')

@app.route('/')
def index():
    sites=load_sites()
    return R('대시보드',cfg=load_config(),sites=sites,
             publish_sites=[s for s in sites if is_publishable(s)],wk=wk_stats,wk_on=wk_active)

# ★배포 반영 확인용(대표님 '언제 되는데/아직 안뜬다' 대응): 현재 구동중 git SHA + 부팅시각.
#   인증 없이 조회 가능(민감정보 없음). before_request 화이트리스트에 등록됨.
_BOOT_TS=time.strftime('%Y-%m-%d %H:%M:%S')
_BOOT_EPOCH=time.time()   # _pc_node_alive 부팅유예 판정용(재시작 직후 NODE_BEATS 비어있는 ~30초 오판 방지)
@app.route('/api/version')
def api_version():
    sha=''
    try:
        import subprocess
        sha=subprocess.check_output(['git','rev-parse','--short','HEAD'],cwd=BASE_DIR,timeout=3).decode().strip()
    except Exception:
        try:
            with open(os.path.join(BASE_DIR,'.git','refs','heads','master')) as f: sha=f.read().strip()[:7]
        except Exception: sha='?'
    # ★배포 검증용(2026-09-11): 서버엔 .git이 없어 sha가 '?' → 실행 중인 app.py 파일의 md5·크기를 노출.
    #   git push(자동배포) 와 push_update가 겹치면 옛 파일로 재시작될 수 있어, 로컬 md5와 대조해 확정한다.
    _md5='?'; _sz=0
    try:
        import hashlib
        with open(os.path.abspath(__file__),'rb') as _f: _b=_f.read()
        _md5=hashlib.md5(_b).hexdigest()[:12]; _sz=len(_b)
    except Exception: pass
    return jsonify({'ok':True,'sha':sha,'booted':_BOOT_TS,'app_md5':_md5,'app_size':_sz})

# API
@app.route('/api/generate',methods=['POST'])
def api_gen():
    d=request.get_json(silent=True) or {}; kw=d.get('keywords',{}); cfg=load_config()
    html,title=generate_article(kw,cfg)
    return jsonify({'ok':True,'title':title,'content':html})

# ---- 작업실별 이미지 URL 풀 헬퍼 ----
def _sanitize_wid(wid):
    return re.sub(r'[^0-9a-zA-Z_-]','',str(wid or ''))

def _wr_load_image_urls(wid):
    """작업실 image_urls(외부 URL 풀) 로드. wid 없으면 전역(images.json)."""
    if not wid: return load_image_urls()
    room=next((r for r in (load_json(WORKROOMS_FILE,[]) or []) if str(r.get('id'))==str(wid)),None)
    if not room: return []
    return [u.strip() for u in (room.get('image_urls') or []) if isinstance(u,str) and u.strip().startswith('http')]

def _wr_save_image_urls(wid,urls):
    """작업실 image_urls 저장. wid 없으면 전역(images.json)."""
    if not wid: save_image_urls(urls); return True
    with _json_lock(WORKROOMS_FILE):
        rooms=load_json(WORKROOMS_FILE,[]) or []
        room=next((r for r in rooms if str(r.get('id'))==str(wid)),None)
        if not room: return False
        room['image_urls']=urls
        room['updated_at']=datetime.now().strftime('%m/%d %H:%M')
        save_json(WORKROOMS_FILE,rooms)
    return True

def _req_wid():
    """요청에서 workroom_id 추출(JSON body·query·form 순). 없으면 ''(전역)."""
    d=request.get_json(silent=True) or {}
    return _sanitize_wid(d.get('workroom_id') or request.args.get('workroom_id') or request.form.get('workroom_id') or '')

# ---- 이미지 URL 풀 (본문 삽입용) — workroom_id 주면 그 작업실 전용, 없으면 전역 ----
@app.route('/api/images',methods=['GET','POST','DELETE'])
def api_images():
    wid=_req_wid()
    if request.method=='POST':
        d=request.get_json() or {}
        urls=d.get('urls')
        if urls is None:
            urls=[x.strip() for x in (d.get('text','') or '').splitlines() if x.strip()]
        urls=[u for u in urls if u.startswith('http')]
        if d.get('append'):
            urls=_wr_load_image_urls(wid)+urls
        urls=list(dict.fromkeys(urls))   # 중복 제거(순서보존)
        if not _wr_save_image_urls(wid,urls):
            return jsonify({'ok':False,'error':'작업실을 찾을 수 없습니다'}),404
        return jsonify({'ok':True,'count':len(urls)})
    if request.method=='DELETE':
        _wr_save_image_urls(wid,[]); return jsonify({'ok':True})
    return jsonify(_wr_load_image_urls(wid))

@app.route('/media/<path:filename>')
def image_media(filename):
    return send_from_directory(UPLOAD_DIR,filename,conditional=True,max_age=86400)

@app.route('/api/images/upload',methods=['POST'])
def api_images_upload():
    wid=_req_wid()
    dest=_workroom_upload_dir(wid) if wid else UPLOAD_DIR
    files=request.files.getlist('files')
    if not files: return jsonify({'ok':False,'error':'이미지 파일을 선택하세요'}),400
    saved=[]; too_big=False; bad_ext=False
    for f in files[:20]:
        raw=(f.filename or '')
        # ★확장자는 '원본 파일명'에서 뽑는다. secure_filename은 한글 파일명(노래방.jpg 등)에서
        #   확장자를 통째로 날려(→'jpg') 정상 이미지가 거부되던 버그 수정(대표님 제보 2026-09-07).
        ext=Path(raw).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            # MIME 타입으로 폴백(파일명에 확장자가 없거나 한글로 뭉개진 경우)
            mime=(getattr(f,'mimetype','') or '').lower()
            ext={'image/jpeg':'.jpg','image/png':'.png','image/gif':'.gif','image/webp':'.webp'}.get(mime,'')
            if ext not in IMAGE_EXTENSIONS: bad_ext=True; continue
        f.stream.seek(0,2); size=f.stream.tell(); f.stream.seek(0)
        if size<=0 or size>10*1024*1024: too_big=True; continue
        stem=secure_filename(Path(raw).stem)[:60] or 'image'   # 한글이면 비어 'image'로 폴백(정상)
        name=f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_{secrets.token_hex(4)}_{stem}{ext}'
        f.save(dest/name); saved.append(name)
    if not saved:
        if too_big and not bad_ext:
            return jsonify({'ok':False,'error':'파일이 너무 큽니다 (파일당 최대 10MB)'}),400
        return jsonify({'ok':False,'error':'JPG·PNG·GIF·WEBP 이미지만 가능합니다 (파일당 최대 10MB)'}),400
    return jsonify({'ok':True,'count':len(saved),'files':saved})

@app.route('/api/images/file',methods=['DELETE'])
def api_images_file_delete():
    d=request.get_json(silent=True) or {}
    wid=_sanitize_wid(d.get('workroom_id') or '')
    base=_workroom_upload_dir(wid) if wid else UPLOAD_DIR
    name=Path(d.get('name','')).name
    p=base/name
    if not name or p.suffix.lower() not in IMAGE_EXTENSIONS or not p.exists():
        return jsonify({'ok':False,'error':'파일을 찾을 수 없습니다'}),404
    p.unlink(); return jsonify({'ok':True})

@app.route('/api/images/files')
def api_images_files():
    wid=_sanitize_wid(request.args.get('workroom_id') or '')
    return jsonify(uploaded_images(wid) if wid else uploaded_images())

# ---- 도메인 발굴 후보 (승인해야만 사이트 목록에 투입) ----
@app.route('/api/candidates',methods=['GET','DELETE'])
def api_candidates():
    if request.method=='DELETE':
        d=request.get_json() or {}
        if d.get('id'):
            save_cands([c for c in load_cands() if c.get('id')!=d['id']])
        elif d.get('clear')=='rejected':
            save_cands([c for c in load_cands() if c.get('status')!='rejected'])
        else:
            save_cands([])
        return jsonify({'ok':True})
    cands=load_cands()
    # 제거된 메일초안/문의함 기능의 기존 데이터도 함께 정리한다.
    legacy_changed=False
    for c in cands:
        if c.pop('mail_draft',None) is not None: legacy_changed=True
        if c.get('status')=='contacted': c['status']='ready'; legacy_changed=True
    if legacy_changed: save_cands(cands)
    order={'ready':0,'new':1,'approved':2,'rejected':3}
    cands.sort(key=lambda c:(order.get(c.get('status'),9),-int(c.get('score',0) or 0)))
    st=load_json(DISCO_FILE,{})
    summary={'total':len(cands),
             'ready':sum(1 for c in cands if c.get('status')=='ready'),
             'new':sum(1 for c in cands if c.get('status')=='new'),
             'approved':sum(1 for c in cands if c.get('status')=='approved'),
             'rejected':sum(1 for c in cands if c.get('status')=='rejected'),
             'today_queries':st.get('queries',0),'today_found':st.get('found',0),
             'date':st.get('date','')}
    # 후보 표시 상한 상향(대표님 '무제한'): 발굴이 쌓여도 다 보이게. summary는 전체 기준 집계.
    return jsonify({'candidates':cands[:2000],'summary':summary})

@app.route('/api/candidates/discover',methods=['POST'])
def api_cand_discover():
    d=request.get_json() or {}; cfg=load_config()
    provider=(cfg.get('search_provider') or 'brave').lower()
    if provider=='brave' and not cfg.get('brave_api_key'):
        return jsonify({'ok':False,'error':'설정 탭에서 Brave Search API 키를 먼저 입력하세요'})
    if provider=='google' and not (cfg.get('google_api_key') and cfg.get('google_cx')):
        return jsonify({'ok':False,'error':'Google API 키와 검색엔진ID(cx)가 필요합니다'})
    try:
        return jsonify(discover_once(cfg,max_queries=int(d.get('queries',5) or 5)))
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)[:200]})

@app.route('/api/candidates/manual',methods=['POST'])
def api_cand_manual():
    """수동 URL 붙여넣기 → 후보 등록 + 검수 + ★즉시 발행테스트까지(대표님 지시 2026-09-08:
       수동 추가 도메인은 파이프라인 주기 기다리지 말고 바로 검수→가입→발행테스트).
       source='manual'은 auto_pipeline이 최우선 처리하므로, 검수 직후 파이프라인 1회를 바로 돌린다."""
    d=request.get_json() or {}; cfg=load_config()
    urls=[x.strip() for x in (d.get('urls','') or '').splitlines() if x.strip().startswith('http')]
    if not urls: return jsonify({'ok':False,'error':'http로 시작하는 URL을 한 줄에 하나씩 넣으세요'})
    n=add_candidates_from([{'url':u} for u in urls],cfg,source='manual')
    def _screen_then_pipeline():
        try: screen_pending(limit=len(urls))          # 1) 검수(글쓰기폼 확인)
        except Exception as e: add_log(f'[수동추가 검수오류] {str(e)[:80]}')
        try:
            add_log(f'[수동추가] {len(urls)}개 검수 완료 → 즉시 발행테스트 시작(대표님 수동추가는 바로 처리)')
            auto_pipeline_once(limit=max(len(urls),3))  # 2) 가입·발행테스트(manual은 최우선 처리)
        except Exception as e: add_log(f'[수동추가 파이프라인오류] {str(e)[:80]}')
    threading.Thread(target=_screen_then_pipeline,daemon=True).start()
    return jsonify({'ok':True,'added':n,'screening':True,'auto_test':True})

@app.route('/api/candidates/ingest',methods=['POST'])
def api_cand_ingest():
    """★PC 자동 발굴 연동(대표님 지시 2026-09-08): 대표님 PC의 발굴 스크립트가 찾은 URL을
       토큰으로 전송 → 서버가 후보 등록 + 검수 + 발행테스트까지 자동 처리.
       (수동탭 addManual과 달리 토큰 접근 가능 — 로그인 없이 PC가 POST. source='manual'로
        auto_pipeline 최우선 처리.) body: {urls:[...] 또는 "줄바꿈 문자열", note?} """
    d=request.get_json(silent=True) or {}; cfg=load_config()
    node_id=str(d.get('node_id') or '').strip()   # 발굴 노드(선택) — 관제실 기기별 현황용
    raw=d.get('urls','')
    if isinstance(raw,list):
        urls=[str(x).strip() for x in raw if str(x).strip().startswith('http')]
    else:
        urls=[x.strip() for x in str(raw or '').splitlines() if x.strip().startswith('http')]
    urls=list(dict.fromkeys(urls))[:100]   # 중복 제거·1회 100개 상한(부하·탐지 회피)
    if not urls:
        if node_id: _node_beat(node_id, action='발굴 중(신규 URL 없음)')
        return jsonify({'ok':False,'error':'http로 시작하는 URL이 없습니다'})
    # ★PC 발굴은 source='pc'(자동 발굴). 진짜 수동추가(manual)와 구분 — 빡센검수 예외는 manual만
    #   적용해야 함(PC발굴을 manual로 태깅했더니 빡센검수를 전부 우회해 대기가 안 줄던 버그, 2026-09-09).
    n=add_candidates_from([{'url':u} for u in urls],cfg,source='pc')
    def _screen_then_pipeline():
        try: screen_pending(limit=len(urls))
        except Exception as e: add_log(f'[PC발굴 검수오류] {str(e)[:80]}')
        try:
            add_log(f'[PC발굴 연동] {len(urls)}개 수신 → 검수 완료, 발행테스트 시작')
            auto_pipeline_once(limit=max(len(urls),3))
        except Exception as e: add_log(f'[PC발굴 파이프라인오류] {str(e)[:80]}')
    threading.Thread(target=_screen_then_pipeline,daemon=True).start()
    add_log(f'[PC발굴 연동] URL {len(urls)}개 수신(신규 {n}개) — 자동 검수·발행테스트 진행')
    if node_id: _node_beat(node_id, action=f'발굴 {len(urls)}개 수신(신규 {n})', discover=n)
    return jsonify({'ok':True,'received':len(urls),'added':n,'screening':True,'auto_test':True})

@app.route('/api/candidates/revive-cafe24',methods=['POST'])
def api_revive_cafe24():
    """★쌓인 Cafe24 재시도(대표님 지시 2026-09-11): SBR 계정정지·경로문제로 rejected된 Cafe24 후보를
       ready로 되살려 PC/노트북 노드가 로컬크롬으로 재시도하게 한다. 이제 로컬크롬 발행이 되므로.
       ★선별: '재시도하면 될 만한' 사유만 복원. 오류안내·인증벽·불법·주차는 제외(되살려도 안 됨 → 크롬 낭비).
       body: {limit?} — 한 번에 되살릴 최대 수(기본 전체). 반환: 되살린 수."""
    d=request.get_json(silent=True) or {}
    limit=int(d.get('limit',0) or 0)   # 0=전체
    # 되살릴 사유(로컬크롬으로 재시도 가치 있음)
    _revive_hit=['wrong customer','sbr','scraping browser','원격','글쓰기 페이지 못찾음','글쓰기 못찾음',
                 'turnstile','캡차','타임아웃','일시적','확인 불가','확인불가','등록 확인']
    # 되살리면 안 되는 사유(재시도 무의미)
    _skip_hit=['오류안내','본인인증','실명인증','휴대폰','sms','문자인증','아이핀','성인인증','19금','인증필요',
               '포인트','읽기 제한','권한']
    # ★대표님 지시 2026-09-11 '휴대폰 인증벽은 빨리 걸러내 도망': 자동 불가한 본인인증 후보를
    #   manual_signup에 방치하지 말고 영구제외 → 파이프라인이 '뚫리는 것'에만 집중.
    #   (도메인 영구탈락은 관제실 ↺ 버튼으로 되돌릴 수 있어 안전.)
    purge_cert=bool(d.get('purge_cert',True))
    purged=0; purge_doms=[]
    revived=0
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            # (1) 휴대폰 본인인증벽 후보 영구제외 — manual_signup·ready·approved 어디에 있든
            if purge_cert and c.get('signup_phone_cert') and c.get('status')!='rejected':
                c['status']='rejected'
                c['reject_reason']='휴대폰 본인인증 필요 — 자동발행 불가(영구제외, 대표님 지시)'
                c['claimed_by']=''; c['claim_expire']=0
                dom=(c.get('domain') or _domain_of(c.get('url',''))).strip()
                if dom: purge_doms.append(dom)
                purged+=1
                continue
            # (2)(3) 재시도 가치 있는 cafe24 탈락 복원(엔진실패·SBR죽음 등)
            if limit and revived>=limit: continue
            if c.get('platform')!='cafe24' or c.get('status')!='rejected': continue
            if c.get('illegal') or c.get('parked') or c.get('ad_banned'): continue
            rr=str(c.get('reject_reason') or '').lower()
            if any(k in rr for k in _skip_hit): continue          # 안 될 사유 제외
            if rr and not any(k in rr for k in _revive_hit): continue  # 되살릴 사유만(빈 사유는 스킵)
            c['status']='ready'; c['reject_reason']=''
            c['pipeline_attempts']=0; c['signup_retry']=0
            c['claimed_by']=''; c['claim_expire']=0; c['last_pipeline_at']=0
            revived+=1
        if revived or purged: save_cands(cands)
    if purge_doms: add_rejected_domains(purge_doms,'휴대폰 본인인증(자동불가)')
    add_log(f'[Cafe24 정리] 인증벽 {purged}곳 영구제외 · 재시도 {revived}곳 ready 복원(로컬크롬 재발행)','파이프라인')
    return jsonify({'ok':True,'revived':revived,'purged':purged})

@app.route('/api/sites/purge-secret',methods=['POST'])
def api_sites_purge_secret():
    """★비밀글 사이트 일괄 제외(대표님 지시 2026-09-11 '아직도 돈다'): 발행이력에 비밀글(is_secret)로
       찍힌 사이트를 지금 즉시 secret_forced 표시 → 다음 발행부터 제외. (발행마다 하나씩 잡히는 걸
       기다리지 않고 이미 아는 것은 한 번에 정리.) 반환: 표시한 사이트 수."""
    hist=load_json(HISTORY_FILE,[])
    sids={str(h.get('site_id') or '') for h in hist if h.get('is_secret') and h.get('site_id')}
    now=_kst_now().strftime('%Y-%m-%d %H:%M')
    flagged=[]
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if str(s.get('id') or '') in sids and not s.get('secret_forced'):
                s['secret_forced']=True; s['secret_at']=now
                flagged.append((s.get('name') or s.get('site_url') or '')[:30])
        if flagged: save_sites(sites)
    add_log(f'[비밀글 일괄제외] {len(flagged)}곳 발행 중단(구글 색인불가)','정리')
    return jsonify({'ok':True,'flagged':len(flagged),'sites':flagged})

@app.route('/api/sites/reject',methods=['POST'])
def api_sites_reject():
    """★사이트 즉시 제외(대표님 지시 2026-09-11 '다트미디어 CEO 인사말 등록은 아니다'). body {domain, reason}.
       등록 사이트 rejected+발행중단, 같은 도메인 후보 rejected, 도메인 영구탈락(재발굴 차단). 관제실 ↺로 되돌릴 수 있음."""
    d=request.get_json(silent=True) or {}
    raw=str(d.get('domain') or '').strip()
    dom=(_domain_of(raw) if raw.startswith('http') else raw).lower().replace('www.','').strip()
    reason=str(d.get('reason') or '수동 제외')[:120]
    if not dom: return jsonify({'ok':False,'error':'domain 필요'}),400
    now=_kst_now().strftime('%Y-%m-%d %H:%M'); hit=[]
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if _domain_of(s.get('site_url','')).replace('www.','')==dom:
                s['status']='rejected'; s['permission']=False; s['write_test_status']='failed'
                s['verified_post_url']=''; s['last_fail_reason']=reason; s['rejected_at']=now
                hit.append((s.get('name') or s.get('site_url') or '')[:40])
        if hit: save_sites(sites)
    ch=0
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            if (c.get('domain') or _domain_of(c.get('url',''))).lower().replace('www.','')==dom and c.get('status')!='rejected':
                c['status']='rejected'; c['reject_reason']=reason; c['claimed_by']=''; c['claim_expire']=0; ch+=1
        if ch: save_cands(cands)
    add_rejected_domains([dom],reason)
    add_log(f'[수동 제외] {dom} — {reason} (사이트 {len(hit)}·후보 {ch})','정리')
    return jsonify({'ok':True,'domain':dom,'sites':hit,'candidates':ch})

def _cafe24_result_is_new(prev_url, new_url):
    """cafe24 결과 URL이 '새 글'인지 글번호로 판정. /article/<board>/<bo>/<N>/ 의 N이 이전 결과(prev)보다 커야 새 글.
       번호를 못 읽으면(비-cafe24·목록 URL 등) 판단 보류=True. ★takago 가짜 성공(남의 스팸글 106090 반복 보고) 차단용."""
    try:
        m2=re.search(r'/article/[^/]+/\d+/(\d+)/?',str(new_url or ''))
        if not m2: return True
        m1=re.search(r'/article/[^/]+/\d+/(\d+)/?',str(prev_url or ''))
        if not m1: return True
        return int(m2.group(1))>int(m1.group(1))
    except Exception: return True

@app.route('/api/sites/unlock-cafe24',methods=['POST'])
def api_sites_unlock_cafe24():
    """★잠긴 Cafe24 사이트 발행 재개(대표님 지시 2026-09-11 '카페24 왜 안되냐'): Bright Data(SBR) 계정정지로
       'Wrong customer name' 연속 실패→auto_drop된 등록 사이트를 잠금 해제. 노드는 이제 로컬크롬만 쓰므로 될 것.
       fail_streak·auto_drop·pc_claim 리셋 + status idle + permission 복구(검증됐던 것). body: {domain?} — 없으면 전체."""
    d=request.get_json(silent=True) or {}
    raw=str(d.get('domain') or '').strip()
    dom=(_domain_of(raw) if raw.startswith('http') else raw).lower().replace('www.','').strip() if raw else ''
    _sbr_hit=('wrong customer','scraping browser','brightdata','sbr','원격','글쓰기 페이지 못찾음','timeout','타임아웃')
    unlocked=[]
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('platform')!='cafe24': continue
            if dom and _domain_of(s.get('site_url','')).replace('www.','')!=dom: continue
            lr=str(s.get('last_fail_reason') or '').lower()
            # 도메인 지정이면 무조건, 전체면 SBR/엔진 실패로 잠긴 것만(사용자가 진짜 거부한 건 건드리지 않음)
            locked=(s.get('auto_dropped_at') or int(s.get('fail_streak',0) or 0)>0)
            if not dom and not (locked and any(k in lr for k in _sbr_hit)): continue
            if not locked and not dom: continue
            s['fail_streak']=0; s.pop('last_fail_reason',None); s.pop('auto_drop_reason',None); s.pop('auto_dropped_at',None)
            s['pc_claim_by']=''; s['pc_claim_expire']=0; s['pc_last_try']=0
            if s.get('status')=='failed': s['status']='idle'
            if str(s.get('verified_post_url') or '')[:4]=='http': s['permission']=True   # 이미 실게시 검증된 것은 허용 복구
            unlocked.append((s.get('name') or s.get('site_url') or '')[:34])
        if unlocked: save_sites(sites)
    add_log(f'[Cafe24 잠금해제] {len(unlocked)}곳 발행 재개(로컬크롬) — {dom or "전체 SBR실패분"}','파이프라인')
    return jsonify({'ok':True,'unlocked':len(unlocked),'sites':unlocked})

@app.route('/api/pipeline/claim',methods=['POST'])
def api_pipeline_claim():
    """★PC 발행노드(대표님 지시 2026-09-09): PC가 '가입·발행 대기' 후보를 원자적으로 잠그고 받아간다.
       서버 auto_pipeline_once의 pend 선별과 동일 조건(공통 _has_write_path·_claim_active) 사용 →
       서버·PC가 겹쳐 처리하지 않음. body: {node_id, n}. claim TTL 후 자동 회수(PC 죽어도 안전)."""
    d=request.get_json(silent=True) or {}
    node_id=str(d.get('node_id') or '').strip() or 'pc'
    n=max(1,min(20,int(d.get('n',4) or 4)))
    # ★노드 코드 세대 게이트(2026-09-11): 13:47 가입 관문 수정 뒤에도 재시작 안 된 옛 노드가 복원한 cafe24 후보 201곳을
    #   옛 로직으로 전부 다시 태웠음. ver<2(옛 pc_node는 ver 미전송=0)엔 cafe24를 안 주고 일반 후보만 준다.
    try: _ver=int(d.get('ver',0) or 0)
    except Exception: _ver=0
    ttl=max(120,min(1800,int(load_config().get('pc_claim_ttl',600) or 600)))
    now=time.time()
    site_domains={_domain_of(s.get('site_url','')) for s in load_sites()}
    _cool=now-1200
    picked=[]
    with _cand_lock:
        cands=load_cands()
        # 만료된 claim은 먼저 회수(claimed_by 비움) — 죽은 노드가 잡고 있던 것 되살리기.
        for c in cands:
            if c.get('claimed_by') and float(c.get('claim_expire',0) or 0) <= now:
                c['claimed_by']=''; c['claim_expire']=0
        elig=[c for c in cands
              if c.get('screened') and c.get('status') in ('ready','approved')
              and not c.get('parked') and not c.get('illegal') and not c.get('ad_banned')
              and (c.get('domain') or '').lower() not in site_domains
              and c.get('reachable') and _has_write_path(c)
              and not _claim_active(c)
              and not (c.get('signup_email_verify') or c.get('signup_phone_cert'))  # 인증벽은 자동가입 불가 → 제외
              and not (c.get('platform')=='cafe24' and _ver<2)   # 옛 노드(ver<2)엔 cafe24 안 줌(위 주석)
              and float(c.get('last_pipeline_at',0) or 0) < _cool]
        # 비회원(바로발행) 우선 → 그다음 로그인. (서버 파이프라인과 동일한 우선순위 감각)
        def _prio(c):
            is24=c.get('platform')=='cafe24'   # ★PC만 할 수 있는 cafe24를 최우선(서버는 이제 안 건드림)
            direct=c.get('write_form') and not c.get('login_required')
            return (1 if is24 else 0, 1 if direct else 0, 1 if not c.get('captcha') else 0, c.get('score',0))
        elig.sort(key=_prio,reverse=True)
        for c in elig[:n]:
            c['claimed_by']=node_id; c['claim_expire']=now+ttl
            c['last_pipeline_at']=now   # 쿨다운도 찍어 서버가 곧바로 다시 후보로 안 봄
            # PC가 발행에 필요로 하는 필드만 추려 전달(민감정보 최소화).
            picked.append({'id':c.get('id'),'url':c.get('url'),'domain':c.get('domain'),
                'platform':c.get('platform','gnuboard'),'bo_table':c.get('bo_table') or 'free',
                'board_name':c.get('board_name'),'login_required':bool(c.get('login_required')),
                'write_form':bool(c.get('write_form')),'captcha':bool(c.get('captcha'))})
        if picked: save_cands(cands)
    if picked: add_log(f'[PC노드] {node_id} 후보 {len(picked)}곳 claim(가입·발행 위임)','파이프라인')
    _node_beat(node_id, action=(f'후보 {len(picked)}곳 발행 시작' if picked else '대기(후보 없음)'))
    return jsonify({'ok':True,'candidates':picked,'ttl':ttl,'llm':_llm_for_nodes()})

@app.route('/api/pipeline/report',methods=['POST'])
def api_pipeline_report():
    """★PC 발행노드 결과 회신: PC가 가입·발행한 결과를 서버에 반영(사이트등록·이력·상태·claim해제).
       서버 auto_pipeline_once의 성공/실패 처리와 동일 효과. body: {node_id, results:[...]}
       각 result: {cand_id, ok, result_url, mb_id?, mb_pass?, bo_table?, msg?, is_temp?, signed?}"""
    d=request.get_json(silent=True) or {}
    node_id=str(d.get('node_id') or '').strip() or 'pc'
    results=d.get('results') or []
    if not isinstance(results,list): return jsonify({'ok':False,'error':'results 배열 필요'}),400
    applied=0; registered=0
    for r in results:
        cid=str(r.get('cand_id') or '').strip()
        if not cid: continue
        # 이 후보가 이 노드의 claim이 맞는지 확인(엉뚱한/늦은 회신 무시)
        c=None
        with _cand_lock:
            for x in load_cands():
                if x.get('id')==cid: c=x; break
        if not c: continue
        if c.get('claimed_by') and c.get('claimed_by')!=node_id:
            continue   # 다른 노드/서버로 이미 넘어간 claim — 무시
        ok=bool(r.get('ok')); result_url=str(r.get('result_url') or '')
        name=c.get('board_name') or c.get('domain') or (c.get('url','') or '')[:30]
        # ★가짜 성공 차단(2026-09-11): cafe24 결과 글번호가 발굴 때 본 글번호보다 크지 않으면 남의 글(목록 첫 글) → 실패 처리
        if ok and c.get('platform')=='cafe24' and not _cafe24_result_is_new(c.get('url'),result_url):
            ok=False; r=dict(r); r['msg']=f'동일/이전 글 URL 재보고({result_url[-14:]}) — 미등록(가짜 성공 차단)'; r['is_temp']=False
        try:
            if ok and result_url.startswith(('http://','https://')):
                _promote_candidate_to_site(c,result_url,bo=r.get('bo_table') or c.get('bo_table'),permission=True)
                if r.get('mb_id'):
                    sid=_promoted_site_id(c)
                    if sid: set_site_flag(sid,mb_id=r.get('mb_id'),mb_pass=r.get('mb_pass',''))
                registered+=1
                add_log(f'[발행가능 등록] {name} — PC노드 검증 통과 → 발행가능 {result_url}'.rstrip())
            elif ok:
                _cand_set(cid,status='rejected',reject_reason='발행됨(결과 URL 확인 불가)',claimed_by='',claim_expire=0)
                add_log(f'[탈락] {name} — PC노드 발행됐으나 결과 URL 확인 불가')
            else:
                msg=str(r.get('msg') or '')[:90]; is_temp=bool(r.get('is_temp'))
                att=int(c.get('pipeline_attempts',0) or 0)+1
                if is_temp and att<5:
                    _cand_set(cid,status='ready',reject_reason=f'일시적 실패({att}/5): {msg}',pipeline_attempts=att,claimed_by='',claim_expire=0)
                    add_log(f'[발행 재시도] {name} ({att}/5) — {msg}')
                else:
                    _cand_set(cid,status='rejected',reject_reason=msg or '발행 실패',pipeline_attempts=att,claimed_by='',claim_expire=0)
                    add_log(f'[탈락] {name} — {msg}')
            # 성공/등록 케이스도 claim 해제(_promote가 status=approved로 바꾸지만 claim 필드는 남으므로 정리)
            if ok:
                _cand_set(cid,claimed_by='',claim_expire=0)
            applied+=1
        except Exception as e:
            add_log(f'[PC노드 회신오류] {name} {str(e)[:60]}')
    _node_beat(node_id, action=(f'발행 {registered}곳 등록' if registered else f'회신 {applied}건'), publish=registered)
    return jsonify({'ok':True,'applied':applied,'registered':registered})

@app.route('/api/pipeline/claim-sites',methods=['POST'])
def api_pipeline_claim_sites():
    """★PC 발행노드용 '등록 Cafe24 사이트' 위임(대표님 지시 2026-09-09 'PC노드가 등록Cafe24도 발행').
       서버(데이터센터 IP)는 CF가 막는 Cafe24 로그인 사이트를, 집 IP인 PC가 로컬크롬으로 발행하도록 넘긴다.
       조건: platform=cafe24 + 저장 계정 있음 + 아직 발행검증 안 됨(또는 재검증 필요) + 최근 시도 아님.
       비번(mb_pass) 포함해 반환(토큰 인증, PC 신뢰). PC는 로그·화면에 비번 노출 안 함. TTL 후 자동 회수."""
    d=request.get_json(silent=True) or {}
    node_id=str(d.get('node_id') or '').strip() or 'pc'
    n=max(1,min(5,int(d.get('n',2) or 2)))
    _cfg=load_config()
    ttl=max(300,min(3600,int(_cfg.get('pc_site_claim_ttl',1200) or 1200)))
    now=time.time(); picked=[]
    with POST_LOCK:
        sites=load_sites()
        for s in sites:   # 만료 claim 회수
            if s.get('pc_claim_by') and float(s.get('pc_claim_expire',0) or 0)<=now:
                s['pc_claim_by']=''; s['pc_claim_expire']=0
        # ★검증됐지만 SBR로 죽어 잠긴 것도 재위임(2026-09-11): 예전엔 verified_post_url이 http면 영영 제외돼,
        #   Bright Data 'Wrong customer name'으로 auto_drop된 hbbiomall·타카고 등이 로컬크롬 전환 후에도
        #   재발행 대상에서 빠졌음. 검증 전(미검증) OR 잠김(permission=False·auto_dropped_at 있음)이면 위임.
        def _need_pub(s):
            if str(s.get('verified_post_url') or '')[:4]!='http': return True   # 미검증
            if s.get('auto_dropped_at') and not s.get('permission'): return True  # 검증됐으나 실패로 잠김 → 재발행
            # ★정기 발행도 노드가(2026-09-11): 검증·허용된 Cafe24는 서버(CF 차단 IP) 대신 노드가 계속 발행.
            #   서버 큐는 _cafe24_node_only로 제외됨. 사이트별 1일 한도·최소 간격은 서버와 같은 규칙.
            try:
                if is_autopostable(s) and under_daily_limit(s,_cfg) and under_min_interval(s)[0]: return True
            except Exception: pass
            return False
        elig=[s for s in sites
              if (s.get('platform')=='cafe24')
              and str(s.get('mb_id') or '').strip()
              and _need_pub(s)
              and s.get('status')!='rejected'
              and not (s.get('pc_claim_by') and float(s.get('pc_claim_expire',0) or 0)>now)
              and float(s.get('pc_last_try',0) or 0) < now-1800]      # 30분 쿨다운
        for s in elig[:n]:
            s['pc_claim_by']=node_id; s['pc_claim_expire']=now+ttl; s['pc_last_try']=now
            picked.append({'id':s.get('id'),'site_url':s.get('site_url'),'platform':'cafe24',
                'bo_table':s.get('bo_table') or '1','name':s.get('name') or s.get('site_url'),
                'mb_id':s.get('mb_id',''),'mb_pass':s.get('mb_pass',''),   # ★비번 포함(PC 로컬 로그인용)
                'write_entry_url':s.get('write_entry_url',''),'article_board_name':s.get('article_board_name','')})
        if picked: save_sites(sites)
    if picked: add_log(f'[PC노드] {node_id} 등록Cafe24 {len(picked)}곳 위임(로컬크롬 로그인발행)','파이프라인')
    if picked: _node_beat(node_id, action=f'Cafe24 {len(picked)}곳 발행 시작')
    return jsonify({'ok':True,'sites':picked,'ttl':ttl,'llm':_llm_for_nodes()})

@app.route('/api/pipeline/report-site',methods=['POST'])
def api_pipeline_report_site():
    """★PC가 등록 Cafe24 사이트 로컬발행 결과 회신 → write_test_status·verified_post_url 갱신 + claim 해제.
       body: {node_id, results:[{site_id, ok, result_url, msg}]}"""
    d=request.get_json(silent=True) or {}
    node_id=str(d.get('node_id') or '').strip() or 'pc'
    results=d.get('results') or []
    if not isinstance(results,list): return jsonify({'ok':False,'error':'results 배열 필요'}),400
    applied=0; passed=0; now=_kst_now().strftime('%Y-%m-%d %H:%M')
    for r in results:
        sid=str(r.get('site_id') or '').strip()
        if not sid: continue
        ok=bool(r.get('ok')); url=str(r.get('result_url') or '')
        try:
            _site=next((s for s in load_sites() if str(s.get('id') or '')==sid),None)
            _nm=((_site or {}).get('name') or (_site or {}).get('site_url') or sid[:8])
            # ★가짜 성공 서버측 차단(2026-09-11): 옛 코드 노드가 '목록 첫 글'(남의 글)을 성공 URL로 보고하던 것을
            #   글번호로 잡는다 — 이전 verified_post_url과 같거나 더 작은 /article/…/N/ 이면 새 글이 아님.
            if ok and _site and not _cafe24_result_is_new(_site.get('verified_post_url'),url):
                ok=False; r=dict(r); r['msg']=f'동일/이전 글 URL 재보고({url[-14:]}) — 미등록으로 처리(가짜 성공 차단)'
            if ok and url.startswith(('http://','https://')):
                set_site_flag(sid,write_test_status='passed',verified_post_url=url,verified_at=now,
                              registration_source='verified_test',pc_claim_by='',pc_claim_expire=0)
                passed+=1
                # ★정기 발행 회계(2026-09-11): 노드 발행도 서버 발행과 똑같이 이력(결과탭)·오늘 카운트·간격에 반영.
                if _site:
                    _t=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    history_add({'id':secrets.token_hex(8),'time':_t,'updated':_t,'site_id':sid,
                                 'site_name':_nm,'site_url':_site.get('site_url',''),'bo_table':_site.get('bo_table',''),
                                 'title':str(r.get('title') or '')[:120],'region':str(r.get('region') or ''),'service':str(r.get('service') or ''),
                                 'status':'done','result_url':url,'message':url,'attempts':0,'node':node_id,'alive':'yes'})
                    finalize_post(_site,True)
                add_log(f'[Cafe24 발행성공] {str(_nm)[:24]} — PC로컬({node_id}) → {url}'.rstrip())
            else:
                set_site_flag(sid,write_test_status='failed',pc_claim_by='',pc_claim_expire=0)
                if _site: finalize_post(_site,False,str(r.get('msg') or '')[:120])
                add_log(f'[Cafe24 발행실패] {str(_nm)[:24]} — {str(r.get("msg") or "")[:80]}')
            applied+=1
        except Exception as e:
            add_log(f'[PC노드 사이트회신오류] {str(e)[:60]}')
    _node_beat(node_id, action=(f'Cafe24 {passed}곳 발행성공' if passed else f'Cafe24 회신 {applied}건'), publish=passed)
    return jsonify({'ok':True,'applied':applied,'passed':passed})

@app.route('/api/discovery/queries',methods=['GET'])
def api_discovery_queries():
    """★PC 발굴 스크립트가 '서버와 동일한 검색어'로 발굴하도록 쿼리 목록 제공(단일 소스).
       토큰 접근. PC는 이 쿼리로 자기 검색키로 검색→결과 URL을 /api/candidates/ingest로 전송.
       (쿼리 로직이 서버에만 있어 PC스크립트와 드리프트 안 남.)"""
    cfg=load_config()
    provider=(cfg.get('search_provider') or 'brave').lower()
    try: qs=_board_finder_queries(provider)
    except Exception as e:
        return jsonify({'ok':False,'error':f'쿼리 생성 실패: {str(e)[:80]}'})
    # 이미 등록·탈락한 도메인은 PC가 스킵하도록 함께 전달(중복 검색·전송 방지).
    try: known={_domain_of(s.get('site_url','')) for s in load_sites()}
    except Exception: known=set()
    try:
        raw=load_json(REJECTED_DOMAINS_FILE,{})
        rej=set((raw.get('domains') if isinstance(raw,dict) else raw) or [])
    except Exception: rej=set()
    # ★검색어 무제한(대표님 지시 2026-09-08 '400개 이후로 추가 안되냐 무제한으로'): 상한 제거.
    #   중복만 제거해 전량 전달(PC가 커서로 나눠 순회). known/rejected도 상한 대폭 상향(중복발굴 방지).
    qs=list(dict.fromkeys(qs))   # 중복 제거(순서 유지)
    # ★PC도 Brave로 발굴(대표님 지시 2026-09-11: DDG 차단됨). provider=brave면 서버가 가진 Brave 키를
    #   토큰 인증된 이 응답으로만 내려준다(관리자 UI에선 마스킹됨 — 노출면 최소화). PC는 이 키로 brave 검색.
    _bk=''
    if provider=='brave':
        _bk=(cfg.get('brave_api_key') or '').strip()
    return jsonify({'ok':True,'provider':provider,'queries':qs,'query_count':len(qs),
                    'brave_key':_bk,
                    'known_domains':sorted(d for d in known if d),
                    'rejected_domains':sorted(str(d).lower() for d in rej)})

@app.route('/api/rejected-domains',methods=['GET','POST'])
def api_rejected_domains():
    """자동 탈락(영구 제외) 도메인 조회/관리. GET: 개수+목록+사유로그. POST {remove:'도메인'}: 재활성화."""
    if request.method=='POST':
        d=request.get_json(silent=True) or {}; dom=str(d.get('remove') or '').strip()
        if dom:
            ok=remove_rejected_domain(dom)
            return jsonify({'ok':ok})
        return jsonify({'ok':False,'error':'remove 도메인 필요'})
    raw=load_json(REJECTED_DOMAINS_FILE,{})
    if isinstance(raw,list): raw={'domains':raw,'log':[]}
    doms=sorted(x.lower() for x in (raw.get('domains') or []))
    try: logn=max(1,min(2000,int(request.args.get('logn',300))))
    except Exception: logn=300
    log=list(reversed(raw.get('log') or []))[:logn]   # 최근 사유(기본 300, ?logn=2000까지)
    return jsonify({'ok':True,'count':len(doms),'domains':doms[:2000],'log':log})

@app.route('/api/candidates/screen',methods=['POST'])
def api_cand_screen():
    d=request.get_json() or {}
    if d.get('id') or d.get('rescreen'):
        with _cand_lock:
            cands=load_cands()
            for c in cands:
                if d.get('rescreen') and c.get('status')!='approved': c['screened']=False
                elif c.get('id')==d.get('id'): c['screened']=False
            save_cands(cands)
    n=screen_pending(1 if d.get('id') else int(d.get('limit',20) or 20))
    return jsonify({'ok':True,'screened':n})

@app.route('/api/candidates/status',methods=['POST'])
def api_cand_status():
    d=request.get_json() or {}
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            if c.get('id')==d.get('id'):
                if 'status' in d: c['status']=d['status']
                if 'note' in d: c['note']=d['note']
        save_cands(cands)
    return jsonify({'ok':True})

@app.route('/api/candidates/approve/<cid>',methods=['POST'])
def api_cand_approve(cid):
    """후보를 사이트 목록에 직접 등록. 근거 메모는 선택사항."""
    d=request.get_json() or {}
    note=(d.get('permission_note','') or '').strip() or '후보 목록에서 직접 등록'
    c=next((x for x in load_cands() if x.get('id')==cid),None)
    if not c: return jsonify({'ok':False,'error':'후보 없음'})
    with POST_LOCK:
        sites=load_sites()
        if any(_domain_of(s.get('site_url',''))==c.get('domain') for s in sites):
            return jsonify({'ok':False,'error':'이미 등록된 도메인'})
        sites.append({'id':secrets.token_hex(6),'site_url':(c.get('base') or c.get('url','')).rstrip('/'),
                  'platform':c.get('platform','auto') if c.get('platform') in ('gnuboard','cafe24') else 'auto',
                  'mb_id':d.get('mb_id',''),'mb_pass':d.get('mb_pass',''),
                  'bo_table':(d.get('bo_table') or c.get('bo_table') or 'free'),
                  'name':(d.get('name') or c.get('domain','')),
                  'permission':True,'permission_note':note or '자동 허용(대표님 무조건 허용)',
                  'registration_source':'candidate_registered','daily_limit':0,'min_interval_minutes':1,
                  'permission_date':_kst_now().strftime('%Y-%m-%d'),
                  'has_captcha':bool(c.get('captcha')),
                  'write_test_status':'pending','verified_post_url':'',
                      'status':'pending','added':_kst_now().strftime('%m/%d %H:%M')})
        save_sites(sites)
    with _cand_lock:
        cands=load_cands()
        for x in cands:
            if x.get('id')==cid: x['status']='approved'; x['approved_at']=_kst_now().strftime('%Y-%m-%d %H:%M')
        save_cands(cands)
    add_log(f'[후보 등록] {c.get("domain")} → 사이트 목록 등록 (발행잠금·실게시 테스트 통과 시 발행가능)','검수')
    return jsonify({'ok':True})

@app.route('/api/candidates/verified',methods=['POST'])
def api_cand_verified():
    """실제 게시까지 검증된 후보를 즉시 사이트 목록으로 승격하고 변동 규칙을 저장."""
    d=request.get_json(silent=True) or {}
    result_url=(d.get('result_url') or '').strip(); write_url=(d.get('write_url') or '').strip()
    # 제출 버튼이 눌렸다는 사실만으로 성공 처리하지 않는다. 결과 URL이 없거나
    # 게시물 후속 검색에서 발견되지 않으면 후보/사이트를 즉시 탈락시킨다.
    post_found=d.get('post_found',True) is not False
    if not result_url.startswith(('http://','https://')) or not post_found:
        source_url=write_url or (d.get('url') or '').strip()
        domain=urllib.parse.urlsplit(source_url).netloc.lower() if source_url else (d.get('domain') or '').lower()
        now=_kst_now().strftime('%Y-%m-%d %H:%M')
        reason='결과 URL 없음' if not result_url.startswith(('http://','https://')) else '게시물 검색 결과 없음'
        with _cand_lock:
            cands=load_cands()
            for c in cands:
                if (domain and c.get('domain','').lower()==domain) or (d.get('candidate_id') and c.get('id')==d.get('candidate_id')):
                    c.update({'status':'rejected','reject_reason':reason,'write_test_status':'failed',
                              'verified_at':now,'verified_post_url':''})
            save_cands(cands)
        if domain:
            with POST_LOCK:
                sites=load_sites()
                for site in sites:
                    if _domain_of(site.get('site_url',''))==domain:
                        site.update({'status':'rejected','permission':False,'write_test_status':'failed',
                                     'verification_fail_reason':'','verified_post_url':'',
                                     'last_structure_check':now})
                save_sites(sites)
        add_log(f'[실게시 검증 탈락] {domain or "도메인 미확인"} · {reason}')
        return jsonify({'ok':False,'rejected':True,'error':reason,'domain':domain}),409
    rp=urllib.parse.urlsplit(result_url); wp=urllib.parse.urlsplit(write_url or result_url)
    if not rp.netloc or (write_url and rp.netloc.lower()!=wp.netloc.lower()):
        return jsonify({'ok':False,'error':'글쓰기 URL과 결과 URL의 도메인이 다릅니다'}),400
    qs=urllib.parse.parse_qs(rp.query); wqs=urllib.parse.parse_qs(wp.query)
    bo=(d.get('bo_table') or (wqs.get('bo_table') or qs.get('bo_table') or ['free'])[0]).strip()
    domain=rp.netloc.lower(); base=f'{rp.scheme}://{rp.netloc}'
    permission_note=(d.get('permission_note') or '').strip()
    caps=d.get('capabilities') if isinstance(d.get('capabilities'),dict) else {}
    now=_kst_now().strftime('%Y-%m-%d %H:%M')
    with POST_LOCK:
        sites=load_sites(); site=next((s for s in sites if _domain_of(s.get('site_url',''))==domain),None)
        created=site is None
        if created:
            site={'id':secrets.token_hex(6),'site_url':base,'platform':d.get('platform','gnuboard'),
                  'mb_id':'','mb_pass':'','bo_table':bo,'name':d.get('name') or domain,
                  'daily_limit':0,'min_interval_minutes':1,'status':'idle','added':_kst_now().strftime('%m/%d %H:%M')}
            sites.append(site)
        site.update({'bo_table':bo,'write_url':write_url,'verified_post_url':result_url,
                     'write_test_status':'passed','verified_at':now,'capabilities':caps,
                     'last_structure_check':now,'registration_source':'verified_test'})
        # ★자동허용(대표님 지시 2026-09-08 '무조건 허용'): 실게시 검증 통과=발행 허용. 수동 근거 불요.
        site.update({'permission':True,'permission_date':_kst_now().strftime('%Y-%m-%d'),
                     'permission_note':permission_note or '실게시 검증 완료 → 자동 허용'})
        save_sites(sites)
    with _cand_lock:
        cands=load_cands()
        for c in cands:
            if c.get('domain','').lower()==domain:
                c.update({'status':'approved','verified_at':now,'verified_post_url':result_url,
                          'capabilities':caps,'site_id':site['id']})
        save_cands(cands)
    add_log(f'[실게시 검증→자동등록] {domain} · {bo} · 발행 허용(자동)')
    return jsonify({'ok':True,'created':created,'site_id':site['id'],'permission':bool(site.get('permission'))})

@app.route('/api/candidates/export',methods=['GET'])
def api_cand_export():
    from flask import Response
    import io
    cands=load_cands()
    cols=[('domain','도메인'),('url','URL'),('platform','플랫폼'),('board_name','게시판명'),
          ('bo_table','게시판ID'),('score','점수'),('status','상태'),('promo_hint','홍보허용흔적'),
          ('parked','주차도메인'),('illegal','도박불법'),
          ('ad_banned','광고금지'),('captcha','캡차'),('login_required','로그인필요'),
          ('write_form','글쓰기폼'),('last_post_days','최근글(일)'),
          ('reject_reason','탈락사유'),('found_at','발견일')]
    try:
        from openpyxl import Workbook
        wb=Workbook(); ws=wb.active; ws.title='발굴후보'
        ws.append([c[1] for c in cols])
        for r in cands:
            ws.append([', '.join(r.get(c[0])) if isinstance(r.get(c[0]),list) else str(r.get(c[0],'') or '') for c in cols])
        buf=io.BytesIO(); wb.save(buf); buf.seek(0)
        return Response(buf.read(),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':'attachment; filename=candidates.xlsx'})
    except Exception:
        import csv
        sio=io.StringIO(); w=csv.writer(sio); w.writerow([c[1] for c in cols])
        for r in cands: w.writerow([', '.join(r.get(c[0])) if isinstance(r.get(c[0]),list) else str(r.get(c[0],'') or '') for c in cols])
        return Response('﻿'+sio.getvalue(),mimetype='text/csv',
                        headers={'Content-Disposition':'attachment; filename=candidates.csv'})

# ---- 회원(고객) 관리 + 월 정산 ----
@app.route('/api/members',methods=['GET','POST','DELETE'])
def api_members():
    if request.method=='POST':
        d=request.get_json() or {}; mem=load_members()
        mid=d.get('id') or secrets.token_hex(6)
        ex=next((m for m in mem if m.get('id')==mid),None)
        rec=ex or {'id':mid,'payments':{},'join_date':_kst_now().strftime('%Y-%m-%d')}
        for k in ['name','biz','phone','memo','status']:
            if k in d: rec[k]=d[k]
        for k in ['plan_fee','addons','addon_fee','settle_day','jitter','per_run']:
            if k in d:
                try: rec[k]=int(d[k])
                except Exception: rec[k]=0
        # ---- 회원별 스케줄 (jump 방식: 계정당 개별 시간대) ----
        if 'sched_enabled' in d: rec['sched_enabled']=bool(d['sched_enabled'])
        if 'sched_times' in d:
            raw=d['sched_times']
            if isinstance(raw,str): raw=[x.strip() for x in raw.replace('，',',').split(',')]
            rec['sched_times']=sorted({t for t in (raw or []) if re.match(r'^\d{1,2}:\d{2}$',str(t).strip())})
        if 'sched_days' in d:
            try: rec['sched_days']=[int(x) for x in (d['sched_days'] or []) if str(x).isdigit()]
            except Exception: rec['sched_days']=[]
        if 'site_ids' in d: rec['site_ids']=list(d['site_ids'] or [])
        if 'keywords_csv' in d:
            rows=[]
            for line in (d.get('keywords_csv','') or '').splitlines():
                p=[x.strip() for x in line.split(',')]
                if len(p)>=2 and p[0] and p[1]:
                    rows.append({'지역':p[0],'서비스':p[1],'브랜드':(p[2] if len(p)>2 else '')})
            rec['keywords']=rows
        rec.setdefault('status','active'); rec.setdefault('plan_fee',30000)
        rec.setdefault('addon_fee',10000); rec.setdefault('addons',0); rec.setdefault('settle_day',1)
        rec.setdefault('sched_enabled',False); rec.setdefault('sched_times',[])
        rec.setdefault('sched_days',[]); rec.setdefault('site_ids',[])
        rec.setdefault('keywords',[]); rec.setdefault('jitter',5); rec.setdefault('per_run',1)
        if not ex: mem.append(rec)
        save_members(mem); return jsonify({'ok':True,'id':mid})
    if request.method=='DELETE':
        d=request.get_json() or {}
        save_members([m for m in load_members() if m.get('id')!=d.get('id')])
        return jsonify({'ok':True})
    return jsonify({'members':[member_view(m) for m in load_members()],'summary':settle_summary()})

@app.route('/api/members/pay',methods=['POST'])
def api_member_pay():
    """특정 회원의 특정 월 납부 상태 토글/설정."""
    d=request.get_json() or {}; mid=d.get('id'); month=d.get('month') or _cur_month()
    paid=bool(d.get('paid',True)); mem=load_members()
    for m in mem:
        if m.get('id')==mid:
            pays=m.get('payments') or {}
            if not isinstance(pays,dict): pays={}
            if paid:
                pays[month]={'paid':True,'paid_at':_kst_now().strftime('%Y-%m-%d %H:%M'),'amount':member_fee(m)}
            else:
                pays[month]={'paid':False}
            m['payments']=pays; save_members(mem)
            return jsonify({'ok':True})
    return jsonify({'ok':False,'error':'회원 없음'})

@app.route('/api/members/run/<mid>',methods=['POST'])
def api_member_run(mid):
    """회원 스케줄 지금 즉시 1회 실행(테스트)."""
    m=next((x for x in load_members() if x.get('id')==mid),None)
    if not m: return jsonify({'ok':False,'error':'회원 없음'})
    cfg=load_config()
    sites=member_sites(m); pool=member_keywords(m)
    if not sites: return jsonify({'ok':False,'error':'배정된 발행 가능 사이트가 없습니다'})
    if not pool: return jsonify({'ok':False,'error':'회원 전용 키워드도 공용 키워드도 비어있습니다'})
    if cfg.get('block_unpaid') and not member_paid_now(m) and m.get('status','active')=='active':
        return jsonify({'ok':False,'error':'미납 회원 — 설정에서 미납 자동정지를 끄거나 납부 처리 후 실행'})
    nm=m.get('name') or m.get('biz') or mid
    cnt=max(1,int(m.get('per_run',1) or 1)); total=0
    for _ in range(cnt):
        kw=pick_keywords(pool,cfg)
        total+=enqueue_generated(sites,{'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),
                                        '브랜드':kw.get('브랜드','') or cfg.get('brand','')},cfg,
                                 {'region':kw.get('지역',''),'service':kw.get('서비스',''),'member':nm})[0]
    mem=load_members()
    for x in mem:
        if x.get('id')==mid:
            x['last_run']=_kst_now().strftime('%Y-%m-%d %H:%M'); x['run_count']=int(x.get('run_count',0) or 0)+1
    save_members(mem)
    if total and not wk_active: start_workers(cfg.get('workers',2))
    return jsonify({'ok':True,'generated':total,'sites':len(sites),'runs':cnt})

@app.route('/api/members/export',methods=['GET'])
def api_members_export():
    from flask import Response
    import io
    mem=load_members(); cm=_cur_month()
    cols=[('name','이름/담당'),('biz','업소'),('phone','연락처'),('status','상태'),
          ('plan_fee','기본료'),('addons','추가광고'),('addon_fee','추가단가'),('fee','월청구'),
          ('paid','이번달납부'),('join_date','가입일'),('memo','메모')]
    rows=[member_view(m) for m in mem]
    try:
        from openpyxl import Workbook
        wb=Workbook(); ws=wb.active; ws.title=f'회원정산_{cm}'
        ws.append([c[1] for c in cols])
        for r in rows:
            ws.append([('O' if (c[0]=='paid' and r.get('paid')) else ('X' if c[0]=='paid' else str(r.get(c[0],'') or ''))) for c in cols])
        buf=io.BytesIO(); wb.save(buf); buf.seek(0)
        return Response(buf.read(),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':f'attachment; filename=members_{cm}.xlsx'})
    except Exception:
        import csv
        sio=io.StringIO(); w=csv.writer(sio); w.writerow([c[1] for c in cols])
        for r in rows: w.writerow([('O' if (c[0]=='paid' and r.get('paid')) else ('X' if c[0]=='paid' else str(r.get(c[0],'') or ''))) for c in cols])
        return Response('﻿'+sio.getvalue(),mimetype='text/csv',
                        headers={'Content-Disposition':f'attachment; filename=members_{cm}.csv'})

# ---- 키워드 풀 (엑셀/CSV 저장 → 랜덤 치환) ----
@app.route('/api/keywords',methods=['GET','POST','DELETE'])
def api_keywords():
    if request.method=='POST':
        d=request.get_json() or {}
        rows=d.get('rows')
        if rows is None:
            rows=[]
            for line in (d.get('csv','') or '').splitlines():
                line=line.strip()
                if not line: continue
                p=[x.strip() for x in line.split(',')]
                if len(p)>=2 and p[0] and p[1]:
                    rows.append({'지역':p[0],'서비스':p[1],'브랜드':(p[2] if len(p)>2 else '')})
        if d.get('append'): rows=load_keywords()+rows
        save_keywords(rows); return jsonify({'ok':True,'count':len(rows)})
    if request.method=='DELETE':
        save_keywords([]); return jsonify({'ok':True})
    return jsonify(load_keywords())

@app.route('/api/workrooms',methods=['GET','POST','DELETE'])
def api_workrooms():
    """키워드 작업실 CRUD. 키워드 조합과 대상 사이트를 서로 독립 저장한다."""
    rooms=load_json(WORKROOMS_FILE,[])
    if request.method=='POST':
        d=request.get_json(silent=True) or {}; rid=str(d.get('id') or secrets.token_hex(6))
        room=next((x for x in rooms if x.get('id')==rid),None)
        if room is None:
            room={'id':rid,'created_at':_kst_now().strftime('%Y-%m-%d %H:%M')}; rooms.append(room)
        name=(d.get('name') or '').strip() or f'{len(rooms)}번 작업실'
        keyword_csv=str(d.get('keyword_csv') or '')
        rows=[]
        for line in keyword_csv.splitlines():
            raw=line.strip()
            if not raw: continue
            if raw.startswith('#'):        # 메모 줄은 그대로 보존
                rows.append(raw); continue
            p=[x.strip() for x in raw.split(',')]
            if len(p)>=3 and all(p[:3]): rows.append(','.join(p[:3]))   # 고정 3개 조합
            elif len(p)>=2 and p[0] and p[1]: rows.append(','.join(p[:2]))  # 지역,서비스
            elif p and p[0]: rows.append(p[0])   # ★메인 1개만 — 서브는 발행 시 자동생성(대표님 지시)
        new_csv='\n'.join(rows)
        # 키워드 목록이 바뀌면 소진 커서를 맨 위로 리셋(새로 생성/교체 시 처음부터 소진).
        if new_csv!=(room.get('keyword_csv') or ''):
            _WR_CURSOR[rid]=0
        room.update({'name':name,'keyword_csv':new_csv,'site_id':str(d.get('site_id') or ''),
                     'bases':str(d.get('bases') or ''),   # 키워드 종류(재접속 시 복원용)
                     'writer_name':str(d.get('writer_name') or '').strip()[:40],   # 작업실별 작성자 이름
                     'updated_at':_kst_now().strftime('%Y-%m-%d %H:%M')})
        save_json(WORKROOMS_FILE,rooms)
        return jsonify({'ok':True,'id':rid,'name':name,'count':len(rows)})
    if request.method=='DELETE':
        d=request.get_json(silent=True) or {}; rid=str(d.get('id') or '')
        save_json(WORKROOMS_FILE,[x for x in rooms if x.get('id')!=rid])
        return jsonify({'ok':True})
    return jsonify(rooms)

@app.route('/api/regions',methods=['GET'])
def api_regions():
    """전국 시도 → 시군구 → 읍면동 계층. 키워드 일괄 생성 도구에서 사용."""
    data=load_json(REGIONS_FILE,{})
    if not data: return jsonify({'error':'행정구역 데이터 없음'}),503
    # 현재 명칭 보정 및 단층 구조인 세종특별자치시 추가
    if '강원도' in data: data['강원특별자치도']=data.pop('강원도')
    if '전라북도' in data: data['전북특별자치도']=data.pop('전라북도')
    data.setdefault('세종특별자치시',{})['세종시']=[
        '조치원읍','연기면','연동면','부강면','금남면','장군면','연서면','전의면','전동면','소정면',
        '한솔동','새롬동','나성동','도담동','어진동','아름동','종촌동','고운동','보람동','대평동',
        '소담동','반곡동','해밀동','다정동','집현동']
    return jsonify(data)

@app.route('/api/keywords/upload',methods=['POST'])
def api_keywords_upload():
    f=request.files.get('file')
    if not f: return jsonify({'ok':False,'error':'파일 없음'}),400
    rows=[]
    try:
        from openpyxl import load_workbook
        import io
        wb=load_workbook(io.BytesIO(f.read()),read_only=True,data_only=True)
        ws=wb.active
        for i,row in enumerate(ws.iter_rows(values_only=True)):
            vals=[('' if c is None else str(c)).strip() for c in row]
            if not vals or not vals[0]: continue
            if i==0 and ('지역' in vals[0] or '키워드' in vals[0]): continue  # 헤더 스킵
            if len(vals)>=2 and vals[0] and vals[1]:
                rows.append({'지역':vals[0],'서비스':vals[1],'브랜드':(vals[2] if len(vals)>2 else '')})
    except Exception as e:
        return jsonify({'ok':False,'error':f'엑셀 파싱 실패: {str(e)[:80]}'}),400
    if request.form.get('append')=='1': rows=load_keywords()+rows
    save_keywords(rows); return jsonify({'ok':True,'count':len(rows)})

@app.route('/api/generate/random',methods=['POST'])
def api_gen_random():
    d=request.get_json() or {}; cfg=load_config(); pool=load_keywords()
    if not pool: return jsonify({'ok':False,'error':'키워드 풀이 비어있습니다 (엑셀/CSV 등록)'})
    site_ids=d.get('site_ids',[]); count=max(1,int(d.get('count',1) or 1))
    # 발행 모드: site_ids(또는 count>1) 지정 시 랜덤 N개를 허용 사이트 큐에 등록
    if site_ids or count>1:
        sites=[s for s in load_sites() if not site_ids or s.get('id') in site_ids]
        allowed=[s for s in sites if is_permitted(s)]; blocked=len(sites)-len(allowed)
        if not allowed:
            return jsonify({'ok':False,'error':f'홍보 허용된 사이트가 없습니다 (미허용 {blocked}개 제외)'})
        total=0
        for _ in range(count):
            kw=pick_keywords(pool,cfg)
            # 사이트마다 유니크 본문 생성(중복 방지)
            total+=enqueue_generated(allowed,{'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),'브랜드':kw.get('브랜드','')},cfg,{'region':kw.get('지역',''),'service':kw.get('서비스','')})[0]
        if not wk_active: start_workers(cfg.get('workers',2))
        return jsonify({'ok':True,'generated':total,'blocked':blocked,'picks':count,'queued':wk_stats['queued']})
    # 미리보기 모드: 1개 생성해서 결과창에 표시
    kw=pick_keywords(pool,cfg)
    html,title=generate_article({'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),'브랜드':kw.get('브랜드','')},cfg)
    return jsonify({'ok':True,'title':title,'content':html,'picked':kw})

@app.route('/api/backup/now',methods=['POST'])
def api_backup_now():
    ok,err=do_backup(load_config(),'수동')
    return jsonify({'ok':ok,'error':('' if ok else err)})

@app.route('/api/diag',methods=['GET'])
def api_diag():
    """서버 환경 자가진단: 크롬·드라이버·실제 페이지 로드·리소스."""
    import shutil,subprocess,platform
    out={'time':_kst_now().strftime('%Y-%m-%d %H:%M:%S'),'python':sys.version.split()[0],
         'platform':platform.platform()[:60],'steps':[]}
    def step(name,ok,detail=''):
        out['steps'].append({'name':name,'ok':bool(ok),'detail':str(detail)[:200]})
    # 1) 크롬 바이너리
    cb=os.environ.get('CHROME_BIN') or ''
    cands=[cb,'/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser','/snap/bin/chromium']
    found=''
    for p in cands:
        if p and os.path.exists(p): found=p; break
    if not found:
        w=shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
        if w: found=w
    ver=''
    if found:
        try: ver=subprocess.run([found,'--version'],capture_output=True,text=True,timeout=15).stdout.strip()
        except Exception as e: ver='버전확인 실패: '+str(e)[:60]
    step('크롬 설치',bool(found),f'{found} {ver}' if found else '크롬 없음 — 설치 필요')
    out['chrome_bin']=found
    # 2) selenium / webdriver_manager 모듈
    try:
        import selenium; step('selenium 모듈',True,'v'+getattr(selenium,'__version__','?'))
    except Exception as e: step('selenium 모듈',False,str(e)[:80])
    try:
        import webdriver_manager; step('webdriver_manager',True,'설치됨')
    except Exception as e: step('webdriver_manager',False,str(e)[:80])
    # 3) 실제 드라이버 기동 + 페이지 로드
    d=None
    if found:
        try:
            if not os.environ.get('CHROME_BIN'): os.environ['CHROME_BIN']=found
            t0=time.time(); d=get_driver()
            step('크롬 드라이버 기동',True,f'{round(time.time()-t0,1)}초')
            try:
                t1=time.time(); d.get('https://example.com'); time.sleep(1)
                ttl=(d.title or '')[:60]
                step('페이지 로드 테스트',bool(ttl),f'title="{ttl}" ({round(time.time()-t1,1)}초)')
            except Exception as e: step('페이지 로드 테스트',False,str(e)[:150])
        except Exception as e:
            step('크롬 드라이버 기동',False,str(e)[:200])
    else:
        step('크롬 드라이버 기동',False,'크롬 미설치로 건너뜀')
    # 4) 리소스
    try:
        du=shutil.disk_usage('/'); step('디스크',du.free>500*1024*1024,
            f'여유 {round(du.free/1024/1024/1024,1)}GB / 전체 {round(du.total/1024/1024/1024,1)}GB')
    except Exception as e: step('디스크',False,str(e)[:60])
    try:
        mt=open('/proc/meminfo').read()
        tot=int(re.search(r'MemTotal:\s+(\d+)',mt).group(1))//1024
        av=int(re.search(r'MemAvailable:\s+(\d+)',mt).group(1))//1024
        step('메모리',av>200,f'가용 {av}MB / 전체 {tot}MB')
    except Exception as e: step('메모리',False,str(e)[:60])
    # 5) 데이터 상태
    step('허용 사이트',len([s for s in load_sites() if is_publishable(s)])>0,
         f'발행가능 {len([s for s in load_sites() if is_publishable(s)])}개 / 전체 {len(load_sites())}개')
    step('키워드 풀',len(load_keywords())>0,f'{len(load_keywords())}개')
    step('이미지 URL',True,f'{len(load_image_urls())}개'+(' (기본 이미지 사용)' if not load_image_urls() else ''))
    out['ok']=all(s['ok'] for s in out['steps'] if s['name'] in ('크롬 설치','크롬 드라이버 기동','페이지 로드 테스트'))
    return jsonify(out)

@app.route('/api/verify/now',methods=['POST'])
def api_verify_now():
    threading.Thread(target=verify_once,kwargs={'limit':40},daemon=True).start()
    return jsonify({'ok':True,'started':True})

@app.route('/api/oneclick',methods=['POST'])
def api_oneclick():
    """원클릭 실시간 발행: 키워드 풀 랜덤 추출 → 사이트마다 유니크 생성 → 허용·캡차없는 사이트 전체 발행."""
    d=request.get_json() or {}; cfg=load_config(); pool=load_keywords()
    if not pool: return jsonify({'ok':False,'error':'키워드 풀이 비어있습니다 (글 생성 탭에서 등록)'})
    sites=load_sites(); allowed=[s for s in sites if is_publishable(s)]
    if not allowed:
        cap=sum(1 for s in sites if is_permitted(s) and s.get('has_captcha'))
        return jsonify({'ok':False,'error':f'발행 가능한 사이트가 없습니다 (허용·캡차없음 0개'+(f' · 캡차제외 {cap}개' if cap else '')+')'})
    count=max(1,min(50,int(d.get('count',1) or 1)))
    total=0
    for _ in range(count):
        kw=pick_keywords(pool,cfg)
        total+=enqueue_generated(allowed,{'지역':kw.get('지역',''),'서비스':kw.get('서비스',''),'브랜드':kw.get('브랜드','')},cfg,{'region':kw.get('지역',''),'service':kw.get('서비스','')})[0]
    if total and not wk_active: start_workers(cfg.get('workers',2))
    return jsonify({'ok':True,'generated':total,'sites':len(allowed),'picks':count})

@app.route('/api/post',methods=['POST'])
def api_post():
    d=request.get_json(silent=True) or {}; cfg=load_config()
    ids=d.get('site_ids',[]); title=d.get('title',''); content=d.get('content','')
    region=d.get('region',''); service=d.get('service',''); brand=d.get('brand','')
    meta={'region':region,'service':service}
    sites=[s for s in load_sites() if s.get('id') in ids]
    allowed=[s for s in sites if is_permitted(s)]
    # 2개 이상 사이트 + 키워드 있으면 사이트마다 유니크 재생성(중복 방지). 단일 사이트는 검토한 원문 그대로.
    if len(allowed)>1 and region and service:
        q,blocked=enqueue_generated(sites,{'지역':region,'서비스':service,'브랜드':brand or cfg.get('brand','')},cfg,meta)
        note='사이트별 유니크 본문 재생성'
    else:
        if title and content: remember_if_unique(title,content)   # 원문도 중복DB에 기록
        q,blocked=enqueue(sites,title,content,meta); note=''
    if q and not wk_active: start_workers(cfg.get('workers',2))
    return jsonify({'ok':True,'queued':q,'blocked':blocked,'note':note})

@app.route('/api/bulk',methods=['POST'])
def api_bulk():
    d=request.get_json(silent=True) or {}; cfg=load_config()
    keyword_sets=d.get('keyword_sets',[]); site_ids=d.get('site_ids',[])
    workroom_id=str(d.get('workroom_id') or ''); workroom_name=str(d.get('workroom_name') or '직접 입력')[:80]
    sites=[s for s in load_sites() if not site_ids or s.get('id') in site_ids]
    if not sites: return jsonify({'ok':False,'error':'사이트 없음'})
    allowed=[s for s in sites if is_publishable(s)]; blocked=len(sites)-len(allowed)
    if not allowed:
        return jsonify({'ok':False,'error':f'실게시 검증을 통과한 허용 사이트가 없습니다 ({blocked}개 제외)'})
    keyword_sets=[x for x in keyword_sets if isinstance(x,dict) and x.get('지역') and x.get('서비스') and x.get('브랜드')]
    if not keyword_sets: return jsonify({'ok':False,'error':'유효한 키워드 조합이 없습니다'})
    # 즉시 발행은 오늘 남은 한도와 최소 간격을 넘기지 않는다. 0=무제한이어도 한 번의
    # 요청에서 과도한 API 생성이 일어나지 않도록 10개까지만 준비한다.
    capacities=[]
    for s in allowed:
        lim=site_daily_limit(s,cfg)
        today=_kst_now().strftime('%Y-%m-%d')
        used=int(s.get('posted_today',0) or 0) if s.get('posted_date')==today else 0
        cap=max(0,lim-used) if lim>0 else 10
        interval_ok,_=under_min_interval(s)
        if not interval_ok: cap=0
        elif site_min_interval(s)>0: cap=min(cap,1)
        capacities.append(cap)
    accepted=min(len(keyword_sets),min(capacities) if capacities else 0,10)
    if accepted<=0:
        return jsonify({'ok':False,'error':'오늘 한도 또는 최소 발행 간격 때문에 지금 실행 가능한 작업이 없습니다. 다음 가능 시간에 다시 실행하세요.'})
    with BULK_LOCK:
        active=next((x for x in BULK_TASKS.values() if x.get('status') in ('preparing','running')),None)
        if active:
            return jsonify({'ok':False,'error':'이미 목록 생성 작업이 진행 중입니다. 중복 실행하지 않았습니다.','task_id':active.get('id')})
        tid=secrets.token_hex(8)
        task={'id':tid,'status':'preparing','total':accepted,'done':0,'queued':0,
              'workroom_id':workroom_id,'workroom_name':workroom_name,
              'requested':len(keyword_sets),'remaining':len(keyword_sets)-accepted,'error':'','created_at':_kst_now().strftime('%Y-%m-%d %H:%M:%S')}
        BULK_TASKS[tid]=task
    def prepare_bulk():
        global wk_active
        try:
            task['status']='running'
            for ks in keyword_sets[:accepted]:
                kw={'지역':ks.get('지역',''),'서비스':ks.get('서비스',''),'브랜드':ks.get('브랜드','')}
                task['queued']+=enqueue_generated(allowed,kw,cfg,{'region':kw['지역'],'service':kw['서비스'],
                    'workroom_id':workroom_id,'workroom_name':workroom_name})[0]
                task['done']+=1
            if task['queued'] and not wk_active: start_workers(cfg.get('workers',2))
            task['status']='done'; task['finished_at']=_kst_now().strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            task['status']='failed'; task['error']=str(e)[:180]
    threading.Thread(target=prepare_bulk,name='BULK-PREP',daemon=True).start()
    return jsonify({'ok':True,'task_id':tid,'accepted':accepted,'requested':len(keyword_sets),
                    'remaining':len(keyword_sets)-accepted,'blocked':blocked})

@app.route('/api/bulk/status/<tid>',methods=['GET'])
def api_bulk_status(tid):
    with BULK_LOCK: task=BULK_TASKS.get(tid)
    if not task: return jsonify({'ok':False,'error':'작업 상태 없음'}),404
    return jsonify({'ok':True,**task})

@app.route('/api/worker-log',methods=['GET'])
def api_worker_log():
    """작업실별 준비 작업과 실제 워커 발행 이력을 한 화면에 제공한다."""
    rid=str(request.args.get('workroom_id') or '')
    history=list(reversed(load_json(HISTORY_FILE,[])))
    if rid: history=[x for x in history if str(x.get('workroom_id') or '')==rid]
    with BULK_LOCK: tasks=list(BULK_TASKS.values())
    if rid: tasks=[x for x in tasks if str(x.get('workroom_id') or '')==rid]
    tasks.sort(key=lambda x:x.get('created_at',''),reverse=True)
    sites=load_sites(); publishable=[s for s in sites if is_publishable(s)]
    assisted=[s for s in sites if is_assisted_postable(s)]
    captcha=[s.get('name') or s.get('site_url','') for s in sites if s.get('has_captcha')]
    # 모든 작업(발굴/검수/가입/발행/정리)을 분류된 활동 로그로 제공(최근 300개, 최신순)
    activity=list(reversed(load_json(LOG_FILE,[])))[:300]
    for a in activity:
        if 'cat' not in a: a['cat']=_log_category(a.get('msg',''))
    # PC/노트북 노드 현황(관제실 기기별 카드). last 오래되면 UI가 '끊김'으로 표시.
    with _NODE_LOCK:
        nodes=sorted(NODE_BEATS.values(),key=lambda b:b.get('last',0),reverse=True)
        nodes=[dict(b) for b in nodes]
    return jsonify({'ok':True,'tasks':tasks[:100],'history':history[:500],
                    'publishable_count':len(publishable),'assisted_count':len(assisted),'captcha_sites':captcha,
                    'activity':activity,'nodes':nodes,'now':time.time(),
                    'workers':{**wk_stats,'active':wk_active,'paused':wk_paused}})

@app.route('/api/manual-checks',methods=['GET'])
def api_captcha_tasks():
    with CAPTCHA_LOCK:
        tasks=[_captcha_public(x) for x in CAPTCHA_TASKS.values()]
    tasks.sort(key=lambda x:x.get('created_at',''),reverse=True)
    return jsonify({'ok':True,'tasks':tasks[:20]})

@app.route('/api/manual-checks/<tid>/submit',methods=['POST'])
def api_captcha_submit(tid):
    value=str((request.get_json(silent=True) or {}).get('value') or '').strip()
    if not value or len(value)>64:
        return jsonify({'ok':False,'error':'CAPTCHA 값을 입력하세요'}),400
    with CAPTCHA_LOCK:
        task=CAPTCHA_TASKS.get(tid)
        if not task or task.get('status')!='waiting_input':
            return jsonify({'ok':False,'error':'대기 중인 CAPTCHA 작업이 아닙니다'}),409
        task['value']=value; task['status']='input_received'; task['message']='입력값 전달 중'
        task['event'].set()
    return jsonify({'ok':True,'message':'입력 완료 · 자동 발행을 계속합니다'})

@app.route('/api/manual-checks/<tid>/cancel',methods=['POST'])
def api_captcha_cancel(tid):
    with CAPTCHA_LOCK:
        task=CAPTCHA_TASKS.get(tid)
        if not task: return jsonify({'ok':False,'error':'작업 없음'}),404
        task['cancelled']=True; task['event'].set()
    return jsonify({'ok':True})

@app.route('/api/sites',methods=['GET','POST','DELETE'])
def api_sites():
    if request.method=='POST':
        d=request.get_json(silent=True) or {}
        if not str(d.get('site_url','')).strip().startswith(('http://','https://')):
            return jsonify({'ok':False,'error':'올바른 http(s) 사이트 URL이 필요합니다'}),400
        _plat=(d.get('platform','auto') or 'auto').strip().lower()
        if _plat not in ('gnuboard','cafe24'):   # auto/미지정 → 자동 감지
            try: _plat=detect_platform(d.get('site_url','').strip())
            except Exception: _plat='gnuboard'
        site={'id':d.get('id',str(int(time.time()*1000))),'site_url':d.get('site_url','').strip().rstrip('/'),
              'platform':_plat,'mb_id':d.get('mb_id',''),'mb_pass':d.get('mb_pass',''),
              'bo_table':d.get('bo_table','').strip() or 'm8_qna','name':d.get('name',''),
              'permission':bool(d.get('permission',False)),
              'registration_source':'manual_admin',
              'daily_limit':max(0,int(d.get('daily_limit',0) or 0)),
              'min_interval_minutes':max(0,int(d.get('min_interval_minutes',1) or 0)),
              'permission_note':(d.get('permission_note','') or '').strip(),
              'permission_date':(datetime.now().strftime('%Y-%m-%d') if d.get('permission') else ''),
              'status':'idle','added':datetime.now().strftime('%m/%d %H:%M')}
        with POST_LOCK:
            sites=load_sites(); ex=[s for s in sites if s.get('id')==site['id']]
            if ex:
                # 기존 발행 카운트 등은 보존
                merged=sites[sites.index(ex[0])]
                if not site.get('mb_pass'): site['mb_pass']=merged.get('mb_pass','')
                merged.update(site)
            else: sites.append(site)
            save_sites(sites)
        return jsonify({'ok':True})
    elif request.method=='DELETE':
        d=request.get_json(silent=True) or {}
        with POST_LOCK:
            sites=[s for s in load_sites() if s.get('id')!=d.get('id','')]
            save_sites(sites)
        return jsonify({'ok':True})
    # 비밀번호는 브라우저/API로 되돌려 보내지 않는다. 저장 여부만 표시한다.
    public=[]
    for s in load_sites():
        x=dict(s); x['login_saved']=bool(x.pop('mb_pass','') or x.get('mb_pass_enc'))
        x.pop('mb_pass_enc',None); public.append(x)
    return jsonify(public)

@app.route('/api/sites/limits',methods=['POST'])
def api_sites_limits():
    """사이트 목록에서 하루 발행 한도와 최소 간격만 안전하게 즉시 수정한다."""
    d=request.get_json(silent=True) or {}; sid=str(d.get('id','')).strip()
    try:
        daily=max(0,min(10000,int(d.get('daily_limit',0))))
        interval=max(0,min(10080,int(d.get('min_interval_minutes',1))))
    except (TypeError,ValueError):
        return jsonify({'ok':False,'error':'건수와 간격은 0 이상의 숫자로 입력하세요'}),400
    found=False
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id')==sid:
                s['daily_limit']=daily; s['min_interval_minutes']=interval
                s['limits_updated_at']=_kst_now().strftime('%Y-%m-%d %H:%M')
                found=True; break
        if found: save_sites(sites)
    if not found: return jsonify({'ok':False,'error':'사이트 없음'}),404
    return jsonify({'ok':True,'daily_limit':daily,'min_interval_minutes':interval})

@app.route('/api/sites/creds',methods=['POST'])
def api_sites_creds():
    """사이트 로그인 계정(mb_id/mb_pass)만 변경. 토큰 인증(로그인 세션 없이 관리 가능).
       ★비번은 응답·로그에 표기하지 않는다. 계정 변경 시 이전 실패 검증상태를 초기화해 재발행 시도되게 한다."""
    d=request.get_json(silent=True) or {}; sid=str(d.get('id','')).strip()
    mb_id=str(d.get('mb_id','') or '').strip(); mb_pass=str(d.get('mb_pass','') or '')
    if not sid: return jsonify({'ok':False,'error':'id 필요'}),400
    found=False
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id')==sid:
                if mb_id: s['mb_id']=mb_id
                if mb_pass: s['mb_pass']=mb_pass
                # 계정 바뀌었으니 이전 로그인실패로 굳은 상태 초기화 → 새 계정으로 재발행 시도.
                s['write_test_status']=''; s['verified_post_url']=''
                s['pc_claim_by']=''; s['pc_claim_expire']=0; s['pc_last_try']=0
                s['creds_updated_at']=_kst_now().strftime('%Y-%m-%d %H:%M')
                found=True; break
        if found: save_sites(sites)
    if not found: return jsonify({'ok':False,'error':'사이트 없음'}),404
    add_log(f'[계정변경] {sid[:8]} — 로그인 계정 갱신(mb_id={mb_id}) · 재발행 대상 초기화')  # 비번은 로그 미표기
    return jsonify({'ok':True,'mb_id':mb_id})

def _signup_credentials(site, rules=None):
    """사이트별 제약을 반영한 충돌 가능성이 낮은 가입정보를 생성한다.
       ★고정계정(config signup_fixed_id/pw)이 설정돼 있으면 그걸 그대로 사용(대표님 지시 통일)."""
    _cfgc=load_config()
    _fid=str(_cfgc.get('signup_fixed_id') or '').strip()
    _fpw=str(_cfgc.get('signup_fixed_pw') or '').strip()
    if _fid and _fpw:
        return _fid,_fpw
    profile={'id_min':4,'id_max':16,'password_min':10,'require_special':False} if site.get('platform')=='cafe24' \
        else {'id_min':3,'id_max':20,'password_min':10,'require_special':False}
    profile.update(site.get('signup_rules') or {}); profile.update(rules or {}); rules=profile
    min_id=max(3,min(20,int(rules.get('id_min',3) or 3)))
    max_id=max(min_id,min(30,int(rules.get('id_max',20) or 20)))
    prefix=re.sub(r'[^a-z0-9_]','',str(rules.get('id_prefix') or 'twseo').lower()) or 'twseo'
    suffix=datetime.now().strftime('%m%d')+secrets.token_hex(2)
    mid=(prefix+'_'+suffix)[:max_id]
    if len(mid)<min_id: mid=(mid+secrets.token_hex(8))[:min_id]
    plen=max(8,min(64,int(rules.get('password_min',10) or 10)))
    special=str(rules.get('password_specials') or '!@#$%')
    alphabet='abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
    chars=[secrets.choice('abcdefghjkmnpqrstuvwxyz'),secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ'),
           secrets.choice('23456789')]
    # 특수문자를 항상 1개 포함 — 규칙을 명시하지 않지만 특수문자를 요구하는 게시판이 많아
    # '입력값 규칙 위반' 가입실패를 줄인다(대소문자+숫자+특수 모두 보장). (실측 기반 개선)
    if special: chars.append(secrets.choice(special))
    pool=alphabet+(special or '')
    chars += [secrets.choice(pool) for _ in range(max(0,plen-len(chars)))]
    random.SystemRandom().shuffle(chars)
    return mid,''.join(chars)

@app.route('/api/sites/signup-prepare/<sid>',methods=['POST'])
def api_site_signup_prepare(sid):
    d=request.get_json(silent=True) or {}; now=datetime.now().isoformat(timespec='seconds')
    sites=load_sites(); site=next((s for s in sites if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'}),404
    learn_error=''; profile=None
    try: profile=learn_signup_profile(site,force=False)
    except Exception as e: learn_error=str(e)[:180]
    with POST_LOCK:
        current=load_sites(); saved=next((s for s in current if s.get('id')==sid),None)
        if not saved: return jsonify({'ok':False,'error':'사이트 없음'}),404
        if profile:
            for k in ['signup_profile_host','signup_rules','signup_url','signup_profile_version','signup_profile_changed','signup_profile_measured_at','signup_has_captcha','signup_email_verification']:
                saved[k]=site.get(k)
        site=saved
        # 이메일 인증 필요 사이트도 제외하지 않는다 — 자동가입이 mail.tm 임시메일로 인증까지
        # 시도한다(적극 시도). 참고용으로 인증 필요 표시만 남긴다.
        rules=d.get('rules') or site.get('signup_rules') or {}
        mid,pw=_signup_credentials(site,rules)
        raw_url=site.get('site_url',''); parts=urllib.parse.urlsplit(raw_url)
        base=f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else raw_url.rstrip('/')
        default_signup=base+('/member/join.html' if site.get('platform')=='cafe24' else '/bbs/register.php')
        site.update({'mb_id':mid,'mb_pass':pw,'signup_rules':rules,'signup_status':'prepared',
                     'signup_updated_at':now,'signup_url':d.get('signup_url') or site.get('signup_url') or
                         default_signup})
        save_sites(current)
    # 비밀번호는 생성 직후 한 번만 전달한다. 이후 API에서는 마스킹한다.
    return jsonify({'ok':True,'mb_id':mid,'mb_pass':pw,'signup_url':site['signup_url'],
                    'status':'prepared','learned':bool(profile),'learn_error':learn_error,
                    'profile_version':site.get('signup_profile_version',0),'rules':site.get('signup_rules',{}),
                    'captcha':bool(site.get('signup_has_captcha')),
                    'notice':'CAPTCHA와 이메일 인증은 직접 완료하세요'})

@app.route('/api/sites/signup-learn/<sid>',methods=['POST'])
def api_site_signup_learn(sid):
    sites=load_sites(); site=next((s for s in sites if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'}),404
    try: profile=learn_signup_profile(site,force=True)
    except Exception as e: return jsonify({'ok':False,'error':str(e)[:200]}),400
    with POST_LOCK:
        current=load_sites(); saved=next((s for s in current if s.get('id')==sid),None)
        if saved:
            for k in ['signup_profile_host','signup_rules','signup_url','signup_profile_version','signup_profile_changed','signup_profile_measured_at','signup_has_captcha','signup_email_verification']:
                saved[k]=site.get(k)
            # 이메일 인증 필요 사이트도 제외하지 않는다(자동가입이 mail.tm으로 인증 시도).
            save_sites(current)
    return jsonify({'ok':True,'version':profile['version'],'changed':site.get('signup_profile_changed',False),
                    'seen_count':profile['seen_count'],'rules':profile['rules'],'captcha':profile['captcha'],
                    'email_verification_required':bool(profile.get('email_verification_required')),
                    'field_count':len(profile['fields']),'measured_at':profile['measured_at']})

@app.route('/api/sites/signup-status/<sid>',methods=['POST'])
def api_site_signup_status(sid):
    d=request.get_json(silent=True) or {}; status=str(d.get('status','')).strip()
    allowed={'prepared','captcha_wait','email_wait','complete','failed'}
    if status not in allowed: return jsonify({'ok':False,'error':'올바르지 않은 가입 상태'}),400
    with POST_LOCK:
        sites=load_sites(); site=next((s for s in sites if s.get('id')==sid),None)
        if not site: return jsonify({'ok':False,'error':'사이트 없음'}),404
        if status=='complete' and (not site.get('mb_id') or not site.get('mb_pass')):
            return jsonify({'ok':False,'error':'저장된 로그인정보가 없습니다'}),400
        site['signup_status']=status; site['signup_updated_at']=datetime.now().isoformat(timespec='seconds')
        site['login_saved']=bool(site.get('mb_id') and site.get('mb_pass'))
        save_sites(sites)
        host=site.get('signup_profile_host')
        if host:
            profiles=load_signup_profiles(); profile=profiles.get(host)
            if profile:
                key='success_count' if status=='complete' else ('failure_count' if status=='failed' else '')
                if key: profile[key]=int(profile.get(key,0))+1
                profile['last_outcome']=status; profile['last_outcome_at']=site['signup_updated_at']; save_signup_profiles(profiles)
    return jsonify({'ok':True,'status':status,'login_saved':site.get('login_saved',False)})

@app.route('/api/workers/start',methods=['POST'])
def api_wk_start():
    d=request.get_json() or {}; start_workers(d.get('n',load_config().get('workers',2)))
    return jsonify({'ok':True})

@app.route('/api/workers/stop',methods=['POST'])
def api_wk_stop():
    stop_workers(); return jsonify({'ok':True})

@app.route('/api/workers/pause',methods=['POST'])
def api_wk_pause():
    pause_workers(); return jsonify({'ok':True})

@app.route('/api/workers/resume',methods=['POST'])
def api_wk_resume():
    resume_workers(); return jsonify({'ok':True})

@app.route('/api/workers/stats',methods=['GET'])
def api_wk_stats():
    # 500곳 목표 진행률: 실게시 검증된 발행 가능 사이트 수 집계
    try:
        cfg=load_config(); goal=int(cfg.get('site_goal',500) or 500)
        ADMIN=('manual_admin','admin_bulk','legacy_admin','candidate_registered','verified_test')
        sites=load_sites()
        publishable=sum(1 for s in sites if s.get('permission') and s.get('registration_source') in ADMIN
                        and s.get('status')!='rejected' and s.get('write_test_status')=='passed'
                        and str(s.get('verified_post_url') or '').startswith(('http://','https://')))
    except Exception:
        goal=500; publishable=0
    # 실제 발행 주체는 작업실 워커(_WR_SLOTS의 독립 크롬 스레드)다. 살아있는 슬롯 수를 센다.
    try: wr_workers=sum(1 for t in _WR_SLOTS.values() if t and t.is_alive())
    except Exception: wr_workers=0
    return jsonify({**wk_stats,'active':wk_active,'paused':wk_paused,
                    'wr_workers':wr_workers,
                    'site_goal':goal,'site_done':publishable})

@app.route('/api/workers/reset',methods=['POST'])
def api_wk_reset():
    with STATS_LOCK:
        for k in ['success','fail','total','done','skipped','retry']: wk_stats[k]=0
        wk_stats['queued']=post_queue.qsize()
    return jsonify({'ok':True})

# ---- 완전 자동 파이프라인 (발굴→자동가입→실발행→자동등록) ----
PIPELINE_STATE={'running':False,'last_result':None,'started_at':'','finished_at':''}
_pipeline_lock=threading.Lock()

@app.route('/api/pipeline/run',methods=['POST'])
def api_pipeline_run():
    """검수완료 후보를 자동가입→실발행→자동등록까지 즉시 1배치 실행(백그라운드)."""
    d=request.get_json(silent=True) or {}
    limit=int(d.get('limit', load_config().get('auto_pipeline_batch',3)) or 3)
    with _pipeline_lock:
        if PIPELINE_STATE['running']:
            return jsonify({'ok':False,'error':'파이프라인이 이미 실행 중입니다'}),409
        PIPELINE_STATE.update({'running':True,'started_at':_kst_now().strftime('%Y-%m-%d %H:%M:%S'),
                               'finished_at':'','last_result':None})
    def _run():
        try:
            # 수동 실행 시에는 설정 토글과 무관하게 이번 1회는 강제로 돌린다
            cfg=load_config()
            if not cfg.get('auto_pipeline_enabled'):
                cfg2=dict(cfg); cfg2['auto_pipeline_enabled']=True; save_config(cfg2)
                res=auto_pipeline_once(limit=limit); save_config(cfg)  # 원래 설정 복구
            else:
                res=auto_pipeline_once(limit=limit)
        except Exception as e:
            res={'ok':False,'error':str(e)[:200]}
        with _pipeline_lock:
            PIPELINE_STATE.update({'running':False,'last_result':res,
                                   'finished_at':_kst_now().strftime('%Y-%m-%d %H:%M:%S')})
    threading.Thread(target=_run,name='PIPELINE',daemon=True).start()
    return jsonify({'ok':True,'started':True,'limit':limit})

@app.route('/api/pipeline/status',methods=['GET'])
def api_pipeline_status():
    with _pipeline_lock:
        return jsonify(dict(PIPELINE_STATE))

@app.route('/api/sites/reconcile',methods=['POST'])
def api_sites_reconcile():
    """사이트 목록 즉시 최신화: 발행이 막힌 사이트를 자동 탈락시킨다."""
    try:
        n=reconcile_sites()
        return jsonify({'ok':True,'dropped':n})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)[:150]}),500

@app.route('/api/sites/purge',methods=['POST'])
def api_sites_purge():
    """안 되는 사이트를 목록에서 삭제(진짜 되는 것만 남김). body {confirm:true} 없으면
    삭제 예정 목록만 반환(dry-run). 삭제 전 자동 백업 + 전멸 방지 가드."""
    try:
        d=request.get_json(silent=True) or {}
        return jsonify(purge_dead_sites(confirm=bool(d.get('confirm'))))
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)[:150]}),500

@app.route('/api/logs',methods=['GET'])
def api_logs():
    """최근 실행 로그(add_log)를 최신순으로 반환. 파이프라인 진행 표시용.
       진단용으로 IMAP 설정 여부(값 노출 없이 있음/없음)도 함께 반환한다."""
    try: n=max(1,min(200,int(request.args.get('n',60))))
    except Exception: n=60
    logs=load_json(LOG_FILE,[])
    _c=load_config()
    _em=(_c.get('imap_email') or '').strip()
    diag={'imap_email_set':bool(_em),'imap_email_domain':(_em.split('@')[-1] if '@' in _em else ''),
          'imap_password_set':bool((_c.get('imap_password') or '').strip()),
          'imap_host':_c.get('imap_host',''),
          'publish_loop':bool(_c.get('publish_loop_enabled')),'auto_pipeline':bool(_c.get('auto_pipeline_enabled'))}
    return jsonify({'ok':True,'logs':list(reversed(logs))[:n],'diag':diag})

# ---- 발행 이력 (결과 탭) ----
@app.route('/api/history',methods=['GET'])
def api_history():
    # ★작업실별 균형 반환(대표님 지시 2026-09-09): 예전엔 최신 500건만 → 발행 많은 작업실(노래방)이
    #   500건을 독차지해 다른 작업실(마사지)이 안 보였음. 작업실마다 최신 300건씩 모아 반환.
    h=list(reversed(load_json(HISTORY_FILE,[])))   # 최신순
    per={}; out=[]
    for r in h:
        wn=r.get('workroom_name') or '직접 입력'
        c=per.get(wn,0)
        if c<300:   # 작업실당 최신 300건까지
            per[wn]=c+1; out.append(r)
        if len(out)>=1500: break   # 전체 상한(과대응답 방지)
    return jsonify(out)

@app.route('/api/history/clear',methods=['POST'])
def api_history_clear():
    save_json(HISTORY_FILE,[]); return jsonify({'ok':True})

@app.route('/api/history/export',methods=['GET'])
def api_history_export():
    from flask import Response
    import io
    h=load_json(HISTORY_FILE,[])
    cols=[('time','시간'),('workroom_name','작업실'),('site_name','사이트'),('site_url','URL'),('bo_table','게시판'),
          ('member','회원'),('region','지역'),('service','서비스'),('title','제목'),('status','상태'),
          ('fail_reason_ko','실패원인'),('alive','생존'),('verified_at','검증시각'),
          ('result_url','결과URL'),('message','메시지')]
    try:
        from openpyxl import Workbook
        wb=Workbook(); ws=wb.active; ws.title='발행이력'
        ws.append([c[1] for c in cols])
        for rec in h:
            ws.append([str(rec.get(c[0],'') or '') for c in cols])
        for i,c in enumerate(cols,1):
            ws.column_dimensions[chr(64+i)].width=[18,16,20,34,12,14,10,12,40,10,14,8,16,30,40][i-1]
        buf=io.BytesIO(); wb.save(buf); buf.seek(0)
        return Response(buf.read(),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition':'attachment; filename=chirashi_history.xlsx'})
    except Exception:
        # openpyxl 미설치 시 CSV 폴백 (엑셀에서 열림)
        import csv
        sio=io.StringIO(); w=csv.writer(sio); w.writerow([c[1] for c in cols])
        for rec in h: w.writerow([str(rec.get(c[0],'') or '') for c in cols])
        data='﻿'+sio.getvalue()   # BOM: 엑셀 한글 깨짐 방지
        return Response(data,mimetype='text/csv',
                        headers={'Content-Disposition':'attachment; filename=chirashi_history.csv'})

@app.route('/api/config/clear-key',methods=['POST'])
def api_cfg_clear_key():
    """LLM 키를 서버에서 비움. 설정 UI는 '빈 칸=기존 유지'라 키를 지울 방법이 없었음
       (대표님 2026-09-11 OpenAI 키 revoke 후 '서버에 남은 키도 지워줘'). 값은 절대 응답에 안 실음."""
    d=request.get_json(silent=True) or {}
    k=str(d.get('key') or '').strip()
    # Bright Data 3종(SBR endpoint·Unlocker 키·프록시 비번)도 여기서 비움 — 계정정지된 뒤에도 서버 config에 남아
    #   서버가 검증된 Cafe24 사이트 발행 때마다 죽은 SBR로 접속('Wrong customer name')하던 문제(2026-09-11). 비우면 해당 기능 OFF.
    _FLAG={'sbr_endpoint':'sbr_enabled','unlocker_api_key':'unlocker_enabled','proxy_pass':'proxy_enabled'}
    if k not in ('openai_key','openai_admin_key','nvidia_api_key','openrouter_api_key','sbr_endpoint','unlocker_api_key','proxy_pass'):
        return jsonify({'ok':False,'error':'허용되지 않은 키'}),400
    cfg=load_config(); was=bool((cfg.get(k) or '').strip()); cfg[k]=''
    if k in _FLAG: cfg[_FLAG[k]]=False
    save_config(cfg)
    add_log(f'[설정] {k} 서버에서 삭제(비움)')
    return jsonify({'ok':True,'key':k,'was_set':was})

@app.route('/api/config',methods=['GET','POST'])
def api_cfg():
    if request.method=='POST':
        d=request.get_json(silent=True) or {}; cfg=load_config()
        old_search=(cfg.get('discover_keywords',''),cfg.get('discover_direct_queries',''))
        _LLM_KEYS=('llm_provider','openai_key','nvidia_api_key','openrouter_api_key','model','nvidia_model','openrouter_model','use_gpt')
        old_llm=tuple(cfg.get(k) for k in _LLM_KEYS)
        for k in ['brand','phone','phones','openai_key','openai_admin_key','llm_provider','nvidia_api_key','nvidia_model','openrouter_api_key','openrouter_model','openai_monthly_budget_usd',
                  'openai_input_price_per_million','openai_cached_input_price_per_million','openai_output_price_per_million',
                  'model','workers','post_delay','daily_limit',
                  'use_gpt','telegram_token','telegram_chat_id','notify_done','notify_fail','update_token',
                  'telegram_control','backup_time','verify_enabled','mix_keywords','block_unpaid',
                  'google_api_key','google_cx','brave_api_key','search_provider','discover_enabled','discover_daily_target',
                  'discover_query_limit','discover_keywords','discover_direct_queries','excluded_domains','finder_ratio',
                  'workroom_workers','vps_reserve_mb','vps_mb_per_worker','site_goal',
                  'video_url','landing_url','post_email','guest_post_password',
                  'imap_email','imap_password','imap_host',
                  'twocaptcha_api_key','twocaptcha_enabled',
                  'twocaptcha_price_recaptcha_usd','twocaptcha_price_image_usd','brave_price_per_query_usd',
                  'auto_pipeline_enabled','auto_pipeline_batch',
                  'proxy_enabled','proxy_host','proxy_port','proxy_user','proxy_pass','proxy_only_for_cf',
                  'unlocker_enabled','unlocker_api_key','unlocker_zone',
                  'sbr_enabled','sbr_endpoint','sbr_country','signup_fixed_id','signup_fixed_pw']:
            if k in d:
                if k in ('openai_key','openai_admin_key','nvidia_api_key','openrouter_api_key','telegram_token','google_api_key','brave_api_key','guest_post_password','twocaptcha_api_key','imap_password','proxy_pass','unlocker_api_key','sbr_endpoint','signup_fixed_pw') and d[k]=='***설정됨***': continue  # 마스크 값은 무시(기존 유지)
                cfg[k]=d[k]
        if (cfg.get('llm_provider') or '').strip().lower() not in ('openrouter','nvidia'): cfg['llm_provider']='openrouter'   # 빈 select·구버전 'openai' 값 방어(OpenAI 퇴출)
        if d.get('password'): cfg['password']=generate_password_hash(d['password'])  # 해시 저장
        # 완전 자동화: 필수 키(Brave 발굴 + 2captcha)가 채워지면 발굴·파이프라인을 자동 ON.
        # (키를 넣는 행위 = 자동 운영 동의로 간주. 원치 않으면 아래 토글을 수동 OFF 가능)
        if (cfg.get('brave_api_key') or '').strip():
            cfg['discover_enabled']=True
        if (cfg.get('brave_api_key') or '').strip() and (cfg.get('twocaptcha_api_key') or '').strip() and cfg.get('twocaptcha_enabled'):
            cfg['auto_pipeline_enabled']=True
        save_config(cfg)
        # 엔진/키를 바꿔 저장하면 잔액소진·429 차단기(_GPT_SKIP_UNTIL)를 즉시 해제 — 안 그러면 OpenAI 잔액소진 30분 차단이
        # 메모리에 남아 OpenRouter로 바꿔도 만료까지 템플릿만 나감(대표님 2026-09-11 전환 직후 원장 0건 원인).
        if tuple(cfg.get(k) for k in _LLM_KEYS)!=old_llm and _GPT_SKIP_UNTIL[0]>time.time():
            _GPT_SKIP_UNTIL[0]=0.0
            add_log(f"[AI 엔진 변경] {(cfg.get('llm_provider') or 'openrouter').upper()}로 전환 — 이전 차단기 해제, 다음 발행부터 즉시 사용")
        # 검색 조건을 바꾸면 다음 검색부터 새 조건의 첫 줄이 즉시 실행되도록 커서를 초기화한다.
        new_search=(cfg.get('discover_keywords',''),cfg.get('discover_direct_queries',''))
        if new_search!=old_search:
            st=load_json(DISCO_FILE,{}) or {}; st['cursor']=0; save_json(DISCO_FILE,st)
        return jsonify({'ok':True,'search_cursor_reset':new_search!=old_search})
    c=dict(load_config())
    if c.get('openai_key'): c['openai_key']='***설정됨***'   # 키 노출 방지
    if c.get('openai_admin_key'): c['openai_admin_key']='***설정됨***'
    if c.get('nvidia_api_key'): c['nvidia_api_key']='***설정됨***'
    if c.get('openrouter_api_key'): c['openrouter_api_key']='***설정됨***'
    if c.get('telegram_token'): c['telegram_token']='***설정됨***'
    if c.get('google_api_key'): c['google_api_key']='***설정됨***'
    if c.get('brave_api_key'): c['brave_api_key']='***설정됨***'
    if c.get('guest_post_password'): c['guest_post_password']='***설정됨***'
    if c.get('twocaptcha_api_key'): c['twocaptcha_api_key']='***설정됨***'
    if c.get('imap_password'): c['imap_password']='***설정됨***'
    if c.get('proxy_pass'): c['proxy_pass']='***설정됨***'   # 프록시 비번 노출 방지
    if c.get('unlocker_api_key'): c['unlocker_api_key']='***설정됨***'   # Web Unlocker API 키 노출 방지
    if c.get('sbr_endpoint'): c['sbr_endpoint']='***설정됨***'   # Scraping Browser endpoint(비번 포함) 노출 방지
    if c.get('signup_fixed_pw'): c['signup_fixed_pw']='***설정됨***'   # 자동가입 고정비번 노출 방지
    c.pop('password',None)
    return jsonify(c)

@app.route('/api/unlocker/test',methods=['GET','POST'])
def api_unlocker_test():
    """Web Unlocker로 지정 URL(기본 타카고 게시판)을 불러와 CF 통과 여부를 진단. 토큰 접근 가능.
       실제 발행 연결 전, Web Unlocker가 작동하는지+계정 검토 완료됐는지 확인용."""
    cfg=load_config()
    if not unlocker_enabled(cfg):
        return jsonify({'ok':False,'error':'Web Unlocker 미설정(설정에서 활성화+API키 입력 필요)'})
    target=(request.args.get('url') or 'https://takago.store/board/product/list.html?board_no=6').strip()
    # 진단은 raw 응답을 직접 받아 에러 상세를 그대로 노출(로그가 잘려 원인 파악 어려움).
    import requests as _rq
    key=str(cfg.get('unlocker_api_key') or '').strip(); zone=str(cfg.get('unlocker_zone') or 'web_unlocker1').strip()
    try:
        _r=_rq.post('https://api.brightdata.com/request',
            headers={'Content-Type':'application/json','Authorization':f'Bearer {key}'},
            json={'zone':zone,'url':target,'format':'raw'},timeout=90)
    except Exception as e:
        return jsonify({'ok':False,'error':f'요청 예외: {str(e)[:150]}','zone':zone})
    if _r.status_code>=400:
        return jsonify({'ok':False,'status':_r.status_code,'zone':zone,
                        'error':'Web Unlocker 거부','detail':(_r.text or '')[:400]})
    html=_r.text or ''
    low=html.lower()
    # CF 챌린지 잔존 여부(뚫렸으면 'just a moment'·turnstile 없어야 함)
    cf_blocked=any(k in low for k in ['just a moment','cf-chl','challenge-platform','_cf_chl','turnstile','사람인지'])
    has_board=any(k in html for k in ['상품','게시','글쓰기','list','write','제목'])
    return jsonify({'ok':True,'url':target,'bytes':len(html),
                    'cf_blocked':cf_blocked,'looks_like_board':has_board,
                    'verdict':('CF 통과 성공(게시판 로드됨)' if (not cf_blocked and has_board) else
                               'CF 여전히 막힘' if cf_blocked else '응답은 왔으나 게시판 아님(URL 확인)'),
                    'sample':html[:200]})

@app.route('/api/sbr/test',methods=['GET','POST'])
def api_sbr_test():
    """Scraping Browser(원격 크롬)가 이 환경에서 실제로 연결·페이지로드 되는지 실측 진단. 토큰 접근 가능.
       ★타카고 CF+로그인 발행을 재시도하기 전, '이번엔 되는지'를 근거로 판정하기 위한 것.
       (이전 보류 사유=한국VPS↔해외 Bright Data 프록시 왕복지연으로 원격크롬 renderer timeout.)
       각 단계 소요시간(연결/get/제목확보)을 반환해 병목이 연결인지 렌더인지 구분한다."""
    from selenium.webdriver.common.by import By
    cfg=load_config()
    ep=str(cfg.get('sbr_endpoint') or '').strip()
    if not (cfg.get('sbr_enabled') and ep):
        return jsonify({'ok':False,'error':'Scraping Browser 미설정(설정에서 활성화+endpoint 입력 필요)'})
    # ★진단(2026-09-09): username(비번 제외)·geo·호스트를 마스킹해 보여줌 — 'Wrong customer name' 원인 파악.
    #   Bright Data 정상 형식: brd-customer-{ID}-zone-{ZONE}. ID가 없거나 -country- 붙어있으면 문제.
    def _mask_ep(e):
        try:
            e2=e if e.startswith('http') else 'https://'+e
            _m=re.match(r'https?://([^:@/]+)(?::[^@]*)?@([^/]+)',e2)  # user @ host(비번 제거)
            if _m: return {'username':_m.group(1),'host':_m.group(2)}
        except Exception: pass
        return {'username':'(파싱실패)','host':'?'}
    _epinfo=_mask_ep(ep); _epinfo['sbr_country']=str(cfg.get('sbr_country') or '(없음)')
    _epinfo['has_country_flag']=('-country-' in ep)
    target=(request.args.get('url') or 'https://takago.store/board/product/list.html?board_no=6').strip()
    steps={}; t0=time.time()
    d=None
    try:
        d=get_driver(remote=True)                       # 원격 크롬 연결(브라우저 세션 생성)
        steps['connect_sec']=round(time.time()-t0,1)
        # get — pageLoadStrategy='none'이라 즉시 반환. 이후 <title>/요소를 폴링한다.
        tg=time.time()
        try: d.get(target)
        except Exception as e: steps['get_error']=str(e)[:120]
        steps['get_return_sec']=round(time.time()-tg,1)
        # 제목/URL 확보까지 폴링(원격+CF 처리 대비 최대 40초). 여기서 timeout이면 이전과 동일 벽.
        tp=time.time(); title=''; cur=''
        while time.time()-tp<40:
            try:
                title=(d.title or ''); cur=(d.current_url or '')
                if title or (cur and cur!='data:,' and cur!='about:blank'): break
            except Exception: pass
            time.sleep(1)
        steps['page_ready_sec']=round(time.time()-tp,1)
        try: psrc=(d.page_source or '')
        except Exception: psrc=''
        low=psrc.lower()
        cf_blocked=any(k in low for k in ['just a moment','cf-chl','challenge-platform','_cf_chl'])
        has_form=any(k in psrc for k in ['상품','게시','글쓰기','list','write','제목','member'])
        got_page=bool(title) or len(psrc)>2000
        verdict=('연결·페이지로드 성공 — CF+로그인 재시도 가치 있음' if got_page and not cf_blocked else
                 'CF 잔존(원격이 CF 못 넘음)' if cf_blocked else
                 '연결됐으나 페이지 못 받음(renderer timeout 의심 — 이전 벽 그대로)')
        return jsonify({'ok':True,'url':target,'steps':steps,'total_sec':round(time.time()-t0,1),
                        'title':title[:80],'current_url':cur[:120],'bytes':len(psrc),
                        'cf_blocked':cf_blocked,'looks_usable':has_form,'verdict':verdict,'endpoint':_epinfo})
    except Exception as e:
        return jsonify({'ok':False,'error':f'원격 연결/실행 예외: {str(e)[:200]}',
                        'steps':steps,'total_sec':round(time.time()-t0,1),'endpoint':_epinfo,
                        'hint':'연결 자체 실패면 endpoint(비번 포함)·잔액·계정상태 확인'})
    finally:
        # 진단 세션은 남기지 않는다(스레드별 __SBR__ 드라이버가 발행에 재사용되지 않게 정리).
        try:
            if d: reset_driver()
        except Exception: pass

@app.route('/api/openai/usage',methods=['GET'])
def api_openai_usage():
    cfg=load_config(); summary=_local_openai_usage_summary(cfg)
    try:
        actual=_openai_admin_costs(cfg) if summary.get('llm_provider')=='openai' else None   # OpenAI 퇴출 후엔 Costs API 조회 안 함
        if actual is not None:
            total=float(actual.get('total_usd') or 0)
            summary['source']='official_costs_api'; summary['actual_month_cost_usd']=total
            if actual.get('daily'): summary['actual_daily']=actual['daily']  # 공식 API 일별 실측
            budget=float(cfg.get('openai_monthly_budget_usd') or 0)
            summary['remaining_budget_usd']=round(max(0,budget-total),6) if budget>0 else None
            summary['note']='OpenAI 조직 Costs API 실제 비용'
    except Exception as e:
        summary['admin_error']=str(e)[:180]
        summary['note']='관리자 키 조회 실패 · 프로젝트 키 토큰 예상치 표시'
    return jsonify({'ok':True,**summary})

@app.route('/api/twocaptcha/usage',methods=['GET'])
def api_twocaptcha_usage():
    cfg=load_config();
    summary=_twocaptcha_usage_summary(cfg)
    return jsonify({'ok':True,**summary})

@app.route('/api/usage',methods=['GET'])
def api_usage():
    """3개 API(OpenAI·2captcha·Brave) 통합 비용/사용량 — 대시보드 '비용' 탭이 한 번에 불러온다.
    각 항목: month/today 합계, per_call_usd(횟수당), daily(일별)·hourly(시간별) 시계열, unit_price(단가).
    실측 여부(measured)와 추정 여부(estimated)를 명시해 UI가 '추정' 뱃지를 붙일 수 있게 한다."""
    cfg=load_config()

    # --- AI 글 생성: 현재 엔진(OpenRouter=응답 실비용 / NVIDIA=무료) 기준 실측. OpenAI는 2026-09-11 퇴출(화면에서 제거) ---
    oa=_local_openai_usage_summary(cfg); cur=oa.get('current') or {}
    _pv=str(cur.get('provider') or 'openrouter'); _pm=str(cur.get('model') or '')
    oa_measured=bool(cur.get('measured'))
    by_model=[{'model':k,'requests':v.get('requests',0),'cost_usd':round(float(v.get('estimated_cost_usd') or 0),6),
               'current':(k==_pm),'measured':('/' in k)}   # 슬래시 모델명(OpenRouter·NVIDIA)=실비용/무료, gpt-*=토큰 추정
              for k,v in sorted((oa.get('by_model') or {}).items(),key=lambda kv:-float(kv[1].get('estimated_cost_usd') or 0))]
    openai_block={
        'label':f'AI 글 생성 · {_pv.upper()}',
        'model':_pm,
        'month_cost_usd':round(float((cur.get('month') or {}).get('estimated_cost_usd') or 0),6),
        'today_cost_usd':round(float((cur.get('today') or {}).get('estimated_cost_usd') or 0),6),
        'month_requests':int((cur.get('month') or {}).get('requests') or 0),
        'today_requests':int((cur.get('today') or {}).get('requests') or 0),
        'per_call_usd':cur.get('per_call_usd',0.0),
        'daily':cur.get('daily',[]),
        'hourly':cur.get('hourly',[]),
        'unit_price':({'per_post_usd':cur.get('per_call_usd',0.0)} if _pv=='openrouter' else ({'per_post_usd':0.0} if _pv=='nvidia' else {})),
        'measured':oa_measured,
        'estimated':not oa_measured,
        'note':(f'{_pm} — OpenRouter 응답의 실제 청구액(usage.cost) 합계' if _pv=='openrouter'
                else (f'{_pm} — NVIDIA 무료 엔드포인트($0)' if _pv=='nvidia' else '토큰 기반 추정치')),
        'by_model':by_model,
        'ledger_month_usd':round(float(oa['month'].get('estimated_cost_usd') or 0),6),   # 이번 달 전체 원장(퇴출 엔진 포함)
        'ledger_today_usd':round(float(oa['today'].get('estimated_cost_usd') or 0),6),
    }

    # --- 2captcha: 횟수는 정확, 금액은 설정단가×성공횟수 추정. 잔액은 참고용. ---
    cap=_captcha_usage_summary(cfg)
    bal=_twocaptcha_usage_summary(cfg)  # getbalance 스냅샷(잔액·잔액낙폭)
    captcha_block={
        'label':'2captcha',
        'month_cost_usd':cap['month'].get('estimated_cost_usd',0.0),
        'today_cost_usd':cap['today'].get('estimated_cost_usd',0.0),
        'month_requests':cap['month'].get('requests',0),
        'today_requests':cap['today'].get('requests',0),
        'month_success':cap['month'].get('success',0),
        'per_call_usd':cap.get('per_call_usd',0.0),
        'daily':cap.get('daily',[]),
        'hourly':cap.get('hourly',[]),
        'unit_price':cap.get('unit_price',{}),
        'measured':False, 'estimated':True,
        'note':cap.get('note'),
        'balance_usd':(bal.get('balance') if bal.get('ok') else None),         # 참고용 잔액
        'balance_delta_usd':(bal.get('charged_since_last_check_usd') if bal.get('ok') else None),
        'balance_ok':bool(bal.get('ok')),
        'balance_error':(bal.get('error') if not bal.get('ok') else None),
    }

    # --- Brave: 사용량 API 없음 → 호출횟수×설정단가 추정. 단가 미설정이면 비용 0. ---
    br=_brave_usage_summary(cfg)
    brave_price=float(cfg.get('brave_price_per_query_usd') or 0.0)
    brave_block={
        'label':'Brave Search',
        'month_cost_usd':br['month'].get('estimated_cost_usd',0.0),
        'today_cost_usd':br['today'].get('estimated_cost_usd',0.0),
        'month_requests':br['month'].get('requests',0),
        'today_requests':br['today'].get('requests',0),
        'per_call_usd':br.get('per_call_usd',0.0),
        'daily':br.get('daily',[]),
        'hourly':br.get('hourly',[]),
        'unit_price':br.get('unit_price',{}),
        'measured':False, 'estimated':True,
        'price_configured':brave_price>0,
        'note':br.get('note'),
        'active':((cfg.get('search_provider') or 'brave').lower()=='brave'),
    }

    # 합계는 이번 달 실제로 나간 돈 기준(퇴출된 OpenAI 원장분 포함) — 카드 상단은 현재 엔진만, 합계는 전체.
    total_month=round(openai_block['ledger_month_usd']+captcha_block['month_cost_usd']+brave_block['month_cost_usd'],6)
    total_today=round(openai_block['ledger_today_usd']+captcha_block['today_cost_usd']+brave_block['today_cost_usd'],6)
    return jsonify({'ok':True,'openai':openai_block,'twocaptcha':captcha_block,'brave':brave_block,
                    'total_month_usd':total_month,'total_today_usd':total_today,
                    'usdkrw':_usd_krw(cfg),  # USD→KRW 실시간 환율(원화 표시용)
                    'note':'AI 글 생성: OpenRouter는 응답 실측 청구액·NVIDIA는 무료·종료된 모델 행은 토큰 추정. 2captcha·Brave는 설정 단가×횟수 추정(횟수는 정확).'})

# ★발행 테스트는 '전용 워커(TEST1 스레드)'에서 직렬로만 실행 (대표님 지시).
#   - 전용 스레드명 → get_driver가 그 스레드의 크롬 1개를 재사용(테스트마다 새 크롬 안 띄움).
#   - 락으로 동시 1건만 → 크롬 난립으로 본 발행 워커(WR1·WR2)까지 죽던 문제 방지.
_TEST_LOCK=threading.Lock()
_TEST_BUSY=[False]

def _run_write_test(sid):
    """전용 테스트 워커에서 발행 테스트를 끝까지 수행하고 결과를 사이트에 기록.
       do_post가 get_driver()를 부르면 이 스레드(이름 TEST1)의 전용 크롬을 재사용한다."""
    got=_TEST_LOCK.acquire(blocking=False)
    if not got:
        return   # 이미 다른 테스트 진행 중 — 직렬화(동시 테스트 금지)
    _TEST_BUSY[0]=True
    try:
        site=next((s for s in load_sites() if s.get('id')==sid),None)
        if not site: return
        cfg=load_config()
        set_site_flag(sid,status='testing',write_test_started_at=_kst_now().strftime('%Y-%m-%d %H:%M'))
        html,title=generate_article({'지역':'테스트','서비스':'테스트'},cfg)
        try:
            ok,msg=do_post(site,title,html)
        except Exception as e:
            ok,msg=False,f'테스트 예외: {str(e)[:150]}'
        try: finalize_post(site,ok,fail_reason=('' if ok else str(msg)))
        except Exception: pass
        result_url=msg if ok and str(msg).startswith(('http://','https://')) else ''
        now=_kst_now().strftime('%Y-%m-%d %H:%M')
        # ★의미없는 발행 방지(대표님 지시 2026-09-08): 글번호가 나와도 '비로그인으로 실제 읽히는지'
        #   확인. 잇츠키친처럼 포인트/권한 제한으로 글읽기가 막히면 구글도 본문을 못 읽어 SEO 0 →
        #   발행 성공으로 인정하지 않는다(발행처에서 제외). 목록엔 떠도 본문 조회 차단이면 헛발행.
        _read_block=''
        if ok and result_url:
            _read_block=_post_read_block_reason(result_url)
        if ok and result_url and not _read_block:
            set_site_flag(sid,status='done',write_test_status='passed',verified_at=now,
                          verified_post_url=result_url,last_structure_check=now,last_fail_reason='')
            add_log(f'[발행테스트 성공] {site.get("name") or site.get("site_url","")} → {result_url}')
        elif ok and result_url and _read_block:
            set_site_flag(sid,status='failed',write_test_status='failed',permission=False,
                          verified_post_url='',last_fail_reason=f'읽기제한 게시판 — {_read_block}(글 올라가도 조회차단·SEO0)')
            add_log(f'[발행테스트 실패] {site.get("name")}: 읽기제한({_read_block}) — 헛발행이라 제외')
        elif ok:
            set_site_flag(sid,status='failed',write_test_status='failed',
                          verification_fail_reason='결과 URL/게시물 검색 결과 없음',last_fail_reason='결과 URL 없음')
            add_log(f'[발행테스트] {site.get("name")}: 성공응답이나 결과 URL 없음')
        else:
            set_site_flag(sid,status='failed',write_test_status='failed',last_fail_reason=str(msg)[:200])
            add_log(f'[발행테스트 실패] {site.get("name")}: {str(msg)[:80]}')
    except Exception as e:
        try: set_site_flag(sid,status='failed',last_fail_reason=f'테스트 예외: {str(e)[:150]}')
        except Exception: pass
        add_log(f'[발행테스트 오류] {str(e)[:80]}')
    finally:
        # 테스트 전용 크롬만 정리(본 발행 워커 WR1·WR2 크롬은 안 건드림)
        try: reset_driver()
        except Exception: pass
        _TEST_BUSY[0]=False
        _TEST_LOCK.release()

@app.route('/api/test/<sid>',methods=['POST'])
def api_test(sid):
    site=next((s for s in load_sites() if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'})
    if not is_permitted(site):
        return jsonify({'ok':False,'error':'미허용 도메인 — 테스트도 실제 발행이므로 홍보 허용(✔) 설정 후 이용하세요'})
    cfg=load_config()
    if not under_daily_limit(site,cfg):
        return jsonify({'ok':False,'error':'사이트 일일 발행 한도에 도달했습니다'})
    interval_ok,remain=under_min_interval(site)
    if not interval_ok:
        return jsonify({'ok':False,'error':f'사이트 최소 발행 간격 미충족 ({max(1,(remain+59)//60)}분 남음)'})
    # 전용 테스트 워커가 이미 바쁘면 대기 안내(동시 테스트 금지 — 크롬 난립 방지)
    if _TEST_BUSY[0]:
        return jsonify({'ok':True,'async':True,'busy':True,'message':'다른 발행 테스트가 진행 중입니다 — 끝난 뒤 다시 시도하세요(테스트는 1건씩만).'})
    # ★전용 스레드명 TEST1로 실행 → 그 스레드의 크롬 1개 재사용, 본 발행 워커와 격리.
    threading.Thread(target=_run_write_test,args=(sid,),name='TEST1',daemon=True).start()
    return jsonify({'ok':True,'async':True,'message':'전용 테스트 워커에서 발행 테스트 시작 — 1~2분 후 사이트 상태에서 결과 확인(본 발행에 영향 없음)'})

# ---- 사이트 대량등록 (CSV: url,이름,게시판,아이디,비번,허용) ----
@app.route('/api/sites/bulk',methods=['POST'])
def api_sites_bulk():
    d=request.get_json(silent=True) or {}; text=d.get('csv',''); default_perm=bool(d.get('permission',False))
    added=0; new_sites=[]
    for line in (text or '').splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        parts=[x.strip() for x in line.split(',')]
        url=parts[0] if parts else ''
        if not url or not url.startswith('http'): continue
        nm=parts[1] if len(parts)>1 else ''
        bo=parts[2] if len(parts)>2 else 'free'
        mid=parts[3] if len(parts)>3 else ''
        mpw=parts[4] if len(parts)>4 else ''
        perm=default_perm
        if len(parts)>5: perm=parts[5] in ('1','true','y','Y','o','O','허용','true')
        new_sites.append({'id':secrets.token_hex(6),'site_url':url.rstrip('/'),'platform':'auto',
                      'mb_id':mid,'mb_pass':mpw,'bo_table':bo or 'free','name':nm,
                      'permission':perm,'permission_note':'CSV 일괄등록',
                      'registration_source':'admin_bulk','daily_limit':0,'min_interval_minutes':1,
                      'permission_date':(datetime.now().strftime('%Y-%m-%d') if perm else ''),
                      'write_test_status':'pending','verified_post_url':'',
                      'status':'idle','added':datetime.now().strftime('%m/%d %H:%M')})
        added+=1
    if new_sites:
        with POST_LOCK:
            sites=load_sites(); sites.extend(new_sites); save_sites(sites)
    return jsonify({'ok':True,'added':added})

# ---- 허용상태 일괄 토글 ----
@app.route('/api/sites/permission',methods=['POST'])
def api_sites_permission():
    d=request.get_json(silent=True) or {}; ids=set(d.get('ids',[])); val=bool(d.get('permission',False))
    # ★자동허용(대표님 지시): 근거 5자 필수 제거. 켜면 바로 허용, 없으면 기본 문구.
    note=(d.get('permission_note') or '').strip() or '자동 허용'
    n=0
    with POST_LOCK:
        sites=load_sites()
        for s in sites:
            if s.get('id') in ids:
                s['permission']=val
                if val:
                    s['permission_note']=note
                    s['permission_date']=datetime.now().strftime('%Y-%m-%d')
                    s['permission_checked_at']=_kst_now().strftime('%Y-%m-%d %H:%M')
                    s['permission_checked_by']='관리자 직접 확인'
                else:
                    s['permission_revoked_at']=_kst_now().strftime('%Y-%m-%d %H:%M')
                n+=1
        save_sites(sites)
    return jsonify({'ok':True,'changed':n})

# ---- 사이트 헬스체크 ----
@app.route('/api/sites/health/<sid>',methods=['POST'])
def api_site_health(sid):
    site=next((s for s in load_sites() if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'})
    h=site_health(site)
    try: plat=detect_platform(site.get('site_url',''),use_cache=False)   # 점검 시 플랫폼도 재감지
    except Exception: plat=site.get('platform','gnuboard')
    h['platform']=plat
    set_site_flag(sid,health=('ok' if h.get('ok') else 'bad'),
                  health_at=datetime.now().strftime('%m/%d %H:%M'),platform=plat)
    return jsonify({'ok':True,'health':h})

@app.route('/api/sites/detect/<sid>',methods=['POST'])
def api_site_detect(sid):
    site=next((s for s in load_sites() if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'})
    try: plat=detect_platform(site.get('site_url',''),use_cache=False)
    except Exception as e: return jsonify({'ok':False,'error':str(e)[:100]})
    set_site_flag(sid,platform=plat)
    return jsonify({'ok':True,'platform':plat})

@app.route('/api/sites/debug/<sid>',methods=['POST'])
def api_site_debug(sid):
    """심층 디버그: 로그인·글쓰기 페이지에서 실제로 무엇이 보이는지 그대로 덤프."""
    from selenium.webdriver.common.by import By
    site=next((s for s in load_sites() if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'})
    url=site.get('site_url','').rstrip('/'); m=re.match(r'(https?://[^/]+)',url)
    base=m.group(1) if m else url
    out={'base':base,'bo_table':site.get('bo_table',''),'mb_id':site.get('mb_id',''),'phases':[]}
    def snap(label):
        try: body=d.find_element(By.TAG_NAME,'body').text[:600]
        except Exception: body=''
        try:
            fields=[]
            for el in d.find_elements(By.CSS_SELECTOR,'input,textarea,select'):
                if not _sel_vis(el): continue
                fields.append(f"{el.tag_name}[{_sel_attr(el,'type') or ''}] name={_sel_attr(el,'name')} id={_sel_attr(el,'id')}")
            fields=fields[:14]
        except Exception: fields=[]
        out['phases'].append({'label':label,'url':(d.current_url or '')[:200],
                              'title':(d.title or '')[:100],'body':body,'fields':fields})
    try:
        d=get_driver()
        # 1) 로그인 페이지
        d.get(base+'/bbs/login.php'); time.sleep(2); snap('로그인 페이지')
        # 2) 로그인 시도
        mid=site.get('mb_id',''); mpw=site.get('mb_pass','')
        if mid:
            try:
                ide=d.find_elements(By.CSS_SELECTOR,"input[name='mb_id']")
                pwe=d.find_elements(By.CSS_SELECTOR,"input[name='mb_password']")
                out['login_fields_found']={'mb_id':len(ide),'mb_password':len(pwe)}
                if ide and pwe:
                    ide[0].clear(); ide[0].send_keys(mid); pwe[0].clear(); pwe[0].send_keys(mpw)
                    clicked=_click_first(d,["input[type='submit']","button[type='submit']",".btn_submit","#btn_submit"])
                    out['login_submit_clicked']=clicked
                    if not clicked:
                        try: pwe[0].submit()
                        except Exception: pass
                    time.sleep(3); dismiss_alerts(d); snap('로그인 시도 후')
            except Exception as e: out['login_error']=str(e)[:150]
        # 3) 글쓰기 페이지 후보들
        bo=site.get('bo_table','free')
        for path in [f'/bbs/write.php?bo_table={bo}', f'/bbs/board.php?bo_table={bo}']:
            try:
                d.get(base+path); time.sleep(2); dismiss_alerts(d); snap('시도: '+path)
            except Exception as e:
                out['phases'].append({'label':'시도: '+path,'url':'','title':'','body':'ERROR '+str(e)[:100],'fields':[]})
    except Exception as e:
        out['error']=str(e)[:200]
    return jsonify({'ok':True,**out})

@app.route('/api/sites/dryrun/<sid>',methods=['POST'])
def api_site_dryrun(sid):
    """드라이런: 실제 글을 올리지 않고 등록 직전까지 검증."""
    site=next((s for s in load_sites() if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'})
    cfg=load_config()
    kw={'지역':'인천','서비스':'셔츠룸','브랜드':cfg.get('brand','') or '테스트'}
    html,title=generate_article(kw,cfg,unique=False)   # 중복DB 오염 방지
    try:
        ok,steps=dryrun_post(site,title,html)
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)[:200],'steps':[]})
    # 캡차가 감지되면 플래그 저장
    if any((not s['ok']) and '캡차' in s['name'] for s in steps):
        set_site_flag(sid,has_captcha=True,captcha_note='드라이런 감지')
    if ok:
        set_site_flag(sid,dryrun_status='passed',dryrun_at=_kst_now().strftime('%Y-%m-%d %H:%M'),
                      last_structure_check=_kst_now().strftime('%Y-%m-%d %H:%M'))
    return jsonify({'ok':ok,'steps':steps,'title':title})

@app.route('/api/sites/learn/<sid>',methods=['POST'])
def api_site_learn(sid):
    """비제출 실측학습: 등록된 사이트 한 곳의 DOM/폼을 측정하고 근거와 레시피를 저장."""
    site=next((s for s in load_sites() if s.get('id')==sid),None)
    if not site: return jsonify({'ok':False,'error':'사이트 없음'})
    if not is_permitted(site):
        return jsonify({'ok':False,'error':'미허용 사이트 — 관리자가 직접 등록하고 홍보 허용한 사이트만 실측 가능'})
    try:
        analysis,rec=analyze_site_logic(site)
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)[:150]})
    _save_site_analysis(sid,analysis,rec)
    if analysis.get('captcha'):
        set_site_flag(sid,has_captcha=True,captcha_note=('실측: '+analysis['captcha'])[:80])
    elif analysis.get('ok'):
        set_site_flag(sid,has_captcha=False,captcha_note='',status='idle',technical_block_reason='')
    else:
        reason='글쓰기 폼을 찾지 못함'
        for step in analysis.get('steps',[]):
            if step.get('name')=='글쓰기 폼' and step.get('detail'): reason=str(step['detail'])[:160]
        set_site_flag(sid,status='failed',write_test_status='failed',verified_post_url='',
                      technical_block_reason=reason,last_structure_check=_kst_now().strftime('%Y-%m-%d %H:%M'))
    learned=None if not rec else {k:rec.get(k) for k in ['platform','write_url','subject_sel','content_mode','content_sel','submit_sel','form_action','form_method','learned_mode']}
    return jsonify({'ok':bool(analysis.get('ok')),'message':('실측 레시피 저장 완료' if rec else '실측 완료 — 발행 폼을 확정하지 못함'),
                    'captcha':bool(analysis.get('captcha')),'blocked':bool(analysis.get('blocked')),
                    'analysis':analysis,'learned':learned})

# ---- 예약 스케줄 CRUD ----
@app.route('/api/schedules/karaoke-oneclick',methods=['POST'])
def api_sched_karaoke():
    """노래방 스케줄 원클릭: discover_direct_queries(지역+업종 조합)를 발행용 키워드
    풀로 변환해 넣고(브랜드 빈값), 검증 완료 사이트 전체에 매일 발행 스케줄을 등록한다."""
    cfg=load_config()
    d=request.get_json(silent=True) or {}
    SERVICES=['하이퍼블릭','기모노룸','레깅스룸','가라오케','노래주점','룸살롱','룸싸롱','풀싸롱',
              '쓰리노','셔츠룸','다국적','퍼블릭','노래방']
    def _split(q):
        q=q.strip()
        for sv in sorted(SERVICES,key=len,reverse=True):
            if q.endswith(sv): return q[:-len(sv)].strip(), sv
        return '', ''
    seen=set(); pool=[]
    for line in (cfg.get('discover_direct_queries','') or '').splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        region,svc=_split(line)
        if not region or not svc: continue
        key=(region,svc)
        if key in seen: continue
        seen.add(key)
        pool.append({'지역':region,'서비스':svc,'브랜드':''})  # 브랜드 없이(지역+서비스만)
    if not pool:
        return jsonify({'ok':False,'error':'discover_direct_queries에서 지역+업종 조합을 찾지 못했습니다'})
    save_keywords(pool)
    # 검증 완료(실게시 검증 + 허용) 사이트 대상 스케줄 등록
    sites=load_sites()
    verified=[s for s in sites if is_permitted(s) and s.get('status')!='rejected'
              and s.get('write_test_status')=='passed'
              and re.match(r'^https?://', str(s.get('verified_post_url') or ''))]
    if not verified:
        return jsonify({'ok':True,'keywords':len(pool),'schedule':None,
                        'warning':'키워드는 저장했으나 검증 완료 사이트가 없어 스케줄은 만들지 않았습니다'})
    times=d.get('times') or ['10:00']   # 기본 매일 오전 10시(사장님이 UI에서 변경 가능)
    scheds=load_scheds()
    sc={'id':'karaoke_auto','name':'노래방 자동발행(검증사이트)',
        'keyword_sets':[],   # 빈 리스트 = 공용 키워드 풀(방금 저장한 것) 사용
        'site_ids':[s.get('id') for s in verified],
        'times':times,'days':[],'enabled':True,
        'count':max(1,int(d.get('count',1) or 1)),'last_run':'','completed_keys':[],'completed_at':''}
    ex=[x for x in scheds if x.get('id')=='karaoke_auto']
    if ex: scheds[scheds.index(ex[0])].update(sc)
    else: scheds.append(sc)
    save_scheds(scheds)
    add_log(f'[노래방 스케줄] 키워드 {len(pool)}개 로드 · 검증사이트 {len(verified)}곳 · 매일 {",".join(times)} 발행 예약')
    return jsonify({'ok':True,'keywords':len(pool),'sites':len(verified),
                    'site_names':[(s.get('name') or s.get('site_url',''))[:20] for s in verified],
                    'times':times,'count':sc['count']})

@app.route('/api/schedules',methods=['GET','POST','DELETE'])
def api_scheds():
    if request.method=='POST':
        d=request.get_json(silent=True) or {}; scheds=load_scheds()
        sc={'id':d.get('id') or secrets.token_hex(6),'name':d.get('name','예약'),
            'keyword_sets':d.get('keyword_sets',[]),'site_ids':d.get('site_ids',[]),
            'times':d.get('times',[]),'days':d.get('days',[]),'enabled':bool(d.get('enabled',True)),
            'count':max(1,int(d.get('count',1) or 1)),'last_run':'','completed_keys':[],'completed_at':''}
        ex=[x for x in scheds if x.get('id')==sc['id']]
        if ex: scheds[scheds.index(ex[0])].update(sc)
        else: scheds.append(sc)
        save_scheds(scheds); return jsonify({'ok':True,'id':sc['id']})
    if request.method=='DELETE':
        d=request.get_json(silent=True) or {}; scheds=[x for x in load_scheds() if x.get('id')!=d.get('id')]
        save_scheds(scheds); return jsonify({'ok':True})
    return jsonify(load_scheds())

@app.route('/api/schedules/toggle',methods=['POST'])
def api_sched_toggle():
    d=request.get_json(silent=True) or {}; scheds=load_scheds()
    for x in scheds:
        if x.get('id')==d.get('id'): x['enabled']=not x.get('enabled',True)
    save_scheds(scheds); return jsonify({'ok':True})

# ---- 통계 ----
@app.route('/api/stats',methods=['GET'])
def api_stats():
    return jsonify(compute_stats())

# ---- 텔레그램 테스트 발송 ----
@app.route('/api/telegram/test',methods=['POST'])
def api_tg_test():
    ok=send_telegram(load_config(),'🔔 찌라시 마스터 텔레그램 연결 테스트')
    return jsonify({'ok':ok,'error':None if ok else '토큰/챗ID 확인'})

# ---- 원격 자가 업데이트 (로그인 불필요, 토큰 인증) ----
_uploads={}; _upload_lock=threading.Lock()
def _apply_update(newcode):
    import py_compile,base64 as _b64
    if b'def main' not in newcode or len(newcode)<1000:
        return {'ok':False,'error':'app.py 형식 이상'},400
    target=os.path.join(BASE_DIR,'app.py'); cur=b''
    try:
        with open(target,'rb') as f: cur=f.read()
        with open(os.path.join(BASE_DIR,'app.py.bak'),'wb') as f: f.write(cur)
    except Exception: pass
    with open(target,'wb') as f: f.write(newcode)
    try: py_compile.compile(target,doraise=True)
    except Exception as e:
        if cur:
            with open(target,'wb') as f: f.write(cur)
        return {'ok':False,'error':f'문법오류 롤백: {str(e)[:120]}'},400
    def _restart(): time.sleep(1.0); os._exit(0)
    threading.Thread(target=_restart,daemon=True).start()
    return {'ok':True,'bytes':len(newcode),'restart':'1초 후 재기동'},200

@app.route('/api/admin/update',methods=['POST'])
def api_admin_update():
    d=request.get_json(silent=True) or {}
    tok=d.get('token') or request.headers.get('X-Update-Token','')
    want=os.environ.get('CHIRASHI_UPDATE_TOKEN') or load_config().get('update_token','')
    if not want:
        return jsonify({'ok':False,'error':'원격 업데이트 비활성화됨'}),503
    if not secrets.compare_digest(str(tok),str(want)):
        return jsonify({'ok':False,'error':'토큰 불일치'}),403
    import base64 as _b64
    # 청크 업로드 (WAF 우회: 작은 조각으로 나눠 전송)
    if 'chunk' in d or 'finalize' in d:
        uid=d.get('upload_id','')
        if not uid: return jsonify({'ok':False,'error':'upload_id 필요'}),400
        with _upload_lock:
            buf=_uploads.setdefault(uid,{})
            if 'chunk' in d: buf[int(d.get('seq',0))]=d['chunk']
            if d.get('finalize'):
                total=int(d.get('total',len(buf)))
                if len(buf)<total:
                    return jsonify({'ok':False,'error':f'조각 부족 {len(buf)}/{total}'}),400
                b64=''.join(buf[i] for i in sorted(buf)); _uploads.pop(uid,None)
                try: newcode=_b64.b64decode(b64)
                except Exception as e: return jsonify({'ok':False,'error':'b64 디코드 실패'}),400
                res,code=_apply_update(newcode); return jsonify(res),code
        return jsonify({'ok':True,'received':len(buf)})
    # 단건 업로드
    b64=d.get('app_b64','')
    if not b64: return jsonify({'ok':False,'error':'app_b64 없음'}),400
    try:
        newcode=_b64.b64decode(b64)
        res,code=_apply_update(newcode); return jsonify(res),code
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)[:150]}),500

# ==================== HTML ====================
HTML=r'''<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>찌라시 마스터 v6</title>
<style>:root{--bg:#060913;--c:#0d1117;--b:#1a1f2e;--t:#c9d1d9;--d:#6b7280;--p:#3b82f6;--g:#22c55e;--r:#ef4444;--y:#f59e0b;--v:#8b5cf6}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--t);min-height:100vh;font-size:13px}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--b)}
header{background:var(--c);border-bottom:1px solid var(--b);padding:10px 16px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{font-size:15px;font-weight:700}.logo s{color:var(--p);text-decoration:none}
.stats{display:flex;gap:12px;font-size:11px;color:var(--d)}.stats b{color:var(--t)}
.tabs{display:flex;gap:2px;padding:10px 14px 0;flex-wrap:wrap}
.tab{padding:7px 14px;border:1px solid transparent;border-radius:8px 8px 0 0;cursor:pointer;font-size:11px;color:var(--d);background:transparent}
.tab.on{background:var(--c);border-color:var(--b);border-bottom-color:var(--c);color:var(--t);font-weight:600}
.wrap{max-width:1200px;margin:0 auto;padding:10px 14px}
.panel{display:none;padding:10px 0}.panel.on{display:block}
/* ★발행현황 탭 가독성 개선(대표님 '글씨·간격 빽빽함' 2026-09-09): 이 탭에서만 여백·글자 키움. 다른 탭 무영향. */
#p-wlog .card{padding:16px}
#p-wlog .note{font-size:13px;line-height:1.6;padding:12px 14px}
#p-wlog h3{font-size:15px}
#p-wlog table td,#p-wlog table th{padding:7px 9px;font-size:12.5px}
#p-wlog table th{font-size:11.5px}
/* ★설정 탭 압축(대표님 2026-09-11 '자리 차지 많다·눈에 잘 들어오게'): 라벨-입력 가로 배치, 여백 축소, 고급 항목 접기, 3열 자동 그리드 */
#p-set .card{padding:10px 12px;margin-bottom:0}
#p-set .card h3{font-size:11px;margin-bottom:7px;color:var(--t);letter-spacing:.5px;text-transform:none}
#p-set .grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:8px;align-items:start}
#p-set .f{display:grid;grid-template-columns:118px minmax(0,1fr);gap:4px 8px;align-items:center;margin-bottom:5px}
#p-set .f>small{color:var(--d);font-size:10.5px;line-height:1.25}
#p-set .f>small a{color:var(--r);margin-left:4px;text-decoration:none}
#p-set .lb{display:block;color:var(--d);font-size:10.5px;margin-bottom:2px}
#p-set input,#p-set select,#p-set textarea{padding:5px 8px;font-size:12px}
#p-set .chk{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--t);margin:4px 0}
#p-set .chk input{width:auto}
#p-set .help{font-size:10px;color:var(--d);line-height:1.45;margin:3px 0 5px}
#p-set .r2{display:grid;grid-template-columns:1fr 1fr;gap:6px}
#p-set .r3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px}
#p-set details.adv{border:1px solid var(--b);border-radius:7px;padding:6px 9px;margin-top:6px}
#p-set details.adv>summary{cursor:pointer;font-size:11px;color:var(--p);font-weight:700;list-style:none}
#p-set details.adv>summary::-webkit-details-marker{display:none}
#p-set details.adv>summary::before{content:'▸ ';color:var(--d)}
#p-set details.adv[open]>summary::before{content:'▾ '}
@media (max-width:640px){ #p-set input,#p-set select,#p-set textarea{font-size:16px} #p-set .r3{grid-template-columns:1fr 1fr} }   /* 폰: iOS 확대 방지 16px 유지. 주의: 여는 중괄호 바로 뒤에 #을 붙이면 Jinja 주석 토큰이 되므로 반드시 띄움 */
.card{background:var(--c);border:1px solid var(--b);border-radius:10px;padding:14px;margin-bottom:10px}
.card h3{font-size:10px;color:var(--d);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
.row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
input,select,textarea{padding:8px 10px;border:1px solid var(--b);border-radius:6px;background:var(--bg);color:var(--t);font-size:12px;outline:none;font-family:inherit;width:100%}
input:focus,textarea:focus{border-color:var(--p)}
textarea{resize:vertical;line-height:1.5}
.btn{padding:7px 14px;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-weight:600;color:#fff;transition:.2s}
.btn-p{background:var(--p)}.btn-g{background:#166534}.btn-r{background:#991b1b}.btn-y{background:#92400e}.btn-v{background:#5b21b6}.btn-d{background:var(--b);color:var(--d)}
.btn:disabled{opacity:.4}.btn-xs{padding:3px 7px;font-size:10px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;padding:5px 8px;color:var(--d);font-weight:500;border-bottom:2px solid var(--b);font-size:10px}
td{padding:5px 8px;border-bottom:1px solid #111827}
.st{display:inline-block;padding:2px 6px;border-radius:10px;font-size:9px;font-weight:600}
.st-i{background:#1e293b;color:var(--d)}.st-ok{background:#052e16;color:var(--g)}.st-f{background:#450a0a;color:var(--r)}.st-y{background:#422006;color:var(--y)}
.toast{position:fixed;top:12px;right:12px;z-index:999;padding:8px 14px;border-radius:6px;font-size:11px;font-weight:500;animation:in .3s}
@keyframes in{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
.toast-ok{background:#052e16;color:var(--g)}.toast-er{background:#450a0a;color:var(--r)}
input[type=checkbox]{accent-color:var(--p)}
/* ★모바일 UI 개선(대표님 지시 2026-09-09 '한 화면에 안 들어옴'): 좁은 화면에서 헤더 세로쌓기·글자축소·여백정리. */
@media (max-width:640px){
  header{flex-direction:column;align-items:flex-start;gap:6px;padding:8px 12px}
  .logo{font-size:14px;white-space:nowrap}
  .stats{gap:8px 10px;flex-wrap:wrap;font-size:10px;width:100%}
  .stats > span, .stats > div{white-space:nowrap}
  .tabs{padding:8px 8px 0;gap:3px;overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch}
  .tab{padding:6px 10px;font-size:11px;white-space:nowrap;flex:0 0 auto}
  .wrap{padding:8px 10px}
  .card{padding:11px}
  input,select,textarea{font-size:16px}   /* iOS 확대 방지 위해 16px */
  /* 발행이력 표: 모바일에선 시간·상태·제목이 한 줄에 들어오게 폰트·여백 축소 */
  #p-wlog table td,#p-wlog table th{padding:5px 6px;font-size:12px}
}
.twocap-shell{background:linear-gradient(180deg,#0f1729 0%,#0b1322 100%);border:1px solid var(--b);border-radius:12px;padding:10px 10px 8px}
.twocap-header{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--d);margin-bottom:8px}
.twocap-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 7px;border-radius:999px;font-size:10px;font-weight:700}
.twocap-chip.ok{background:rgba(34,197,94,.12);color:var(--g);border:1px solid rgba(34,197,94,.35)}
.twocap-chip.warn{background:rgba(245,158,11,.12);color:var(--y);border:1px solid rgba(245,158,11,.35)}
.twocap-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.twocap-metric{background:rgba(255,255,255,.02);border:1px solid rgba(148,163,184,.15);border-radius:8px;padding:8px 7px;text-align:center;min-height:64px}
.twocap-metric .label{font-size:9px;color:var(--d);margin-bottom:5px;letter-spacing:.02em}
.twocap-metric .value{font-size:18px;font-weight:700;line-height:1.2}
.twocap-metric .value.green{color:var(--g)}
.twocap-metric .value.blue{color:var(--p)}
.twocap-metric .value.amber{color:var(--y)}
.twocap-footer{display:flex;justify-content:space-between;gap:10px;margin-top:9px;padding-top:8px;border-top:1px solid rgba(148,163,184,.12);font-size:9px;color:var(--d)}
.prog{background:var(--b);border-radius:6px;height:14px;overflow:hidden}
.prog>div{height:100%;width:0;background:var(--g);transition:width .3s}
.note{background:#0b1a2e;border:1px solid #1a3a5e;border-radius:8px;padding:10px 12px;font-size:11px;color:#9db8d8;line-height:1.6;margin-bottom:10px}
</style></head><body>'''

LOGIN_HTML=r'''<div style="display:flex;align-items:center;justify-content:center;min-height:100vh">
<div style="background:var(--c);border:1px solid var(--b);border-radius:14px;padding:40px;width:340px;text-align:center">
<h2 style="margin-bottom:4px;font-size:18px">찌라시 마스터 v6 <span style="font-size:10px;color:var(--d);font-weight:400">build 0905-작업분류·정리</span></h2>
<p style="color:var(--d);font-size:12px;margin-bottom:24px">정직한 자동 발행 (회피코드 없음)</p>
<form method="POST">
{% if error %}<p style="color:var(--r);font-size:11px;margin-bottom:8px">{{ error }}</p>{% endif %}
<input type="password" name="pw" placeholder="비밀번호" autofocus style="margin-bottom:12px">
<button type="submit" class="btn btn-p" style="width:100%">로그인</button>
</form></div></div>'''

DASH_HTML=r'''<header><div class="logo">찌라시 <s>마스터 v6</s></div>
<div class="stats" id="live"><span>큐:<b id="q">0</b></span><span>성공:<b id="ok" style="color:var(--g)">0</b></span><span>실패:<b id="fl" style="color:var(--r)">0</b></span><span>스킵:<b id="sk" style="color:var(--y)">0</b></span><span>발행워커:<b id="ws" style="color:var(--d)">-</b></span><span style="margin-left:10px;padding-left:10px;border-left:1px solid var(--b)">🎯 발행가능 <b id="siteGoal" style="color:var(--p)">-</b></span></div>
<a href="/logout" class="btn-xs" style="background:var(--b);color:var(--d);text-decoration:none">로그아웃</a></header>

<div class="tabs"><button id="tab-gen" class="tab" onclick="T('gen')" style="display:none">글 생성</button><button class="tab on" onclick="T('kw')">키워드</button><button class="tab" onclick="T('wlog')">발행 현황</button><button class="tab" onclick="T('images')">이미지 저장</button><button class="tab" onclick="T('sites')">사이트 (<span id="siteTabCount">{{sites|length}}</span>)</button><button class="tab" onclick="T('disco')">발굴</button><button id="tab-mem" class="tab" onclick="T('mem')" style="display:none">회원·정산</button><button class="tab" onclick="T('stats')">통계</button><button class="tab" onclick="T('cost')">API 비용</button><button class="tab" onclick="T('set')">설정</button></div>
<div class="wrap"><div id="toasts"></div>
<div id="pvOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:500;padding:20px" onclick="if(event.target===this)closePreview()">
<div style="max-width:820px;margin:0 auto;background:#fff;color:#222;border-radius:10px;max-height:90vh;overflow:auto">
<div style="position:sticky;top:0;background:#0d1117;color:#fff;padding:10px 14px;display:flex;align-items:center;gap:10px"><b style="flex:1;font-size:13px">🔍 발행 미리보기 (실제 게시판에 보일 모양)</b><span id="pvTitle" style="font-size:11px;color:#9aa"></span><button class="btn btn-r btn-xs" onclick="closePreview()">닫기</button></div>
<iframe id="pvFrame" style="width:100%;height:70vh;border:0;background:#fff"></iframe></div></div>

<div id="p-gen" class="panel">
<div class="note">✔ 현재 적용 규칙 — <b style="color:var(--p)">메인 키워드1 + 같은 지역 키워드2·3</b>으로 AI 장문 HTML을 생성합니다. 이미지는 저장소에서 1개만 사용하고 ALT에는 키워드1을 넣습니다. 지역 순서는 인천→경기→서울→충남→충북→세종→전북→전남→경상→경북→강원→제주이며, 모든 키워드를 사용하면 예약이 자동 종료됩니다. <b style="color:var(--g)">허용 동의가 기록된 사이트만 발행</b>됩니다.</div>

<div class="card" id="progCard" style="display:none"><h3>발행 진행률</h3>
<div class="prog"><div id="progBar"></div></div>
<div style="font-size:10px;color:var(--d);margin-top:5px" id="progText">0 / 0</div></div>


<div class="card"><h3>생성 결과</h3>
<div class="row"><input type="text" id="gTitle" placeholder="제목" style="font-weight:600"></div>
<textarea id="gContent" rows="10" placeholder="리치HTML 본문..."></textarea>
<div class="row" style="margin-top:6px"><span style="color:var(--d);font-size:10px" id="gLen">0자</span><span style="flex:1"></span>
<button class="btn btn-d" onclick="previewPost()">미리보기</button>
<button class="btn btn-g" onclick="postSel()">선택 사이트 발행</button>
<button class="btn btn-y" onclick="postAll()">전체 사이트 발행</button></div></div></div>

<div id="p-kw" class="panel on">
<div class="note">🗂 <b>작업실 하나 = 업종 하나</b>. 키워드 종류만 넣고 <b style="color:var(--p)">생성</b>만 누르면 24시간 자동 발행됩니다. 관리할 건 <b style="color:var(--p)">키워드</b>와 <b style="color:var(--p)">이미지</b>뿐.</div>
<div class="card" style="border-color:#334155"><h3>🗂 키워드 작업실 — 업종별로 추가 (예: 노래방 · 마사지 · 셔츠룸)</h3>
<div class="row">
<select id="wrSelect" onchange="showWorkroom()" style="min-width:180px"><option value="">작업실 선택</option></select>
<input id="wrName" placeholder="예: 노래방" style="max-width:200px">
<button class="btn btn-p" onclick="newWorkroom()">+ 작업실 추가</button>
<button class="btn btn-g btn-xs" onclick="saveWorkroom()">이름 저장</button>
<button class="btn btn-r btn-xs" onclick="deleteWorkroom()">삭제</button>
<span id="wrSaved" style="color:var(--d);font-size:10px"></span></div>
<div class="row" style="margin-top:6px"><span style="color:var(--p);font-size:11px;font-weight:600">✍ 작성자 이름</span><input id="wrWriter" placeholder="이 작업실 글쓴이 (비우면 브랜드 사용)" style="max-width:260px"><span style="color:var(--d);font-size:10px">게시판 작성자(wr_name)로 사용 · 작업실마다 다르게</span></div>

<div style="margin-top:14px;font-weight:700;color:var(--p);font-size:12px">1) 지역 범위</div>
<div class="row" style="margin-top:4px">
<select id="wrProvince" style="width:auto" onchange="fillGuSel('wrProvince','wrGuSel')"><option value="">전국</option></select>
<select id="wrGuSel" style="width:auto" title="특정 시·군·구만 선택(비우면 시·도 전체)"><option value="">시·군·구 전체</option></select>
<label><input type="checkbox" id="wrCity" checked style="width:auto"> 시·도</label>
<label><input type="checkbox" id="wrGu" checked style="width:auto"> 시·구·군</label>
<label><input type="checkbox" id="wrDong" checked style="width:auto"> 읍·면·동</label>
<label title="전국 주요 지하철역명 추가 (예: 강남역 노래방)"><input type="checkbox" id="wrStation" style="width:auto"> 🚇 지하철역</label>
<select id="wrJoin" style="width:auto"><option value="">붙여쓰기</option><option value=" ">띄어쓰기</option></select></div>

<div style="margin-top:14px;font-weight:700;color:var(--p);font-size:12px">2) 키워드 종류 <span style="font-weight:400;color:var(--d)">(한 줄에 하나, 3개 이상)</span></div>
<textarea id="wrBases" rows="3" placeholder="출장마사지&#10;마사지&#10;홍보게시판" style="margin-top:4px"></textarea>
<div style="color:var(--d);font-size:10px;margin-top:4px">지역마다 위 키워드 중 서로 다른 3개를 랜덤 조합해 글 1건을 만듭니다. 첫 번째가 메인 키워드.</div>

<div style="margin-top:14px;font-weight:700;color:var(--p);font-size:12px">3) 생성 → 자동저장 → 발행</div>
<div class="row" style="margin-top:4px">
<button class="btn btn-v" style="font-size:14px;padding:9px 18px" onclick="applyWorkroomRegional(false)">➕ 생성 + 저장 (발행 시작)</button>
<button class="btn btn-y btn-xs" onclick="applyWorkroomRegional(true)">🔄 전체 교체</button>
<button class="btn btn-d btn-xs" onclick="previewWorkroomRegional()">개수 확인</button>
<span id="wrRegionCount" style="color:var(--d);font-size:11px">0개</span></div>
<div style="color:var(--d);font-size:10px;margin-top:6px">발행 대상: <b style="color:var(--g)">전체 발행가능 사이트 자동</b> · 목록을 다 쓰면 맨 위부터 무한 반복(제목·본문은 매번 새로 생성).</div>

<details style="margin-top:10px"><summary style="cursor:pointer;color:var(--d);font-size:11px">저장된 조합 보기 · 직접 편집</summary>
<div style="color:var(--g);font-size:10px;margin:6px 0 4px;line-height:1.6">💡 <b>메인 키워드 1개만 한 줄씩</b> 넣어도 됩니다(예: <code>교동노래방</code>). 그러면 서브2·3은 <b>그 구/동에 맞춰 매 발행마다 자동 랜덤</b> — 같은 조합 반복 없이 쭉쭉 진행됩니다.<br>콤마로 <code>지역,서비스,브랜드</code> 3개를 넣으면 기존처럼 고정 조합으로 씁니다.</div>
<textarea id="wrKeywords" rows="5" placeholder="교동노래방&#10;강남하이퍼블릭&#10;부평다국적노래방&#10;(또는) 서울,셔츠룸,강남홍마니" style="margin-top:6px"></textarea>
<button class="btn btn-g btn-xs" style="margin-top:4px" onclick="saveWorkroom()">직접 편집분 저장</button></details>
<select id="wrSite" style="display:none"><option value="">전체</option></select>
</div>

<div class="card" style="display:none"><h3>키워드 조합 대량 입력 (CSV: 키워드1,키워드2,키워드3)</h3>
<textarea id="kwlist" rows="4" placeholder="인천셔츠룸,인천노래방,인천가라오케&#10;서울셔츠룸,서울노래방,서울가라오케"></textarea>
<div class="row" style="margin-top:6px"><button class="btn btn-p" id="bulkRunBtn" onclick="genFromList()">목록 생성 작업 시작</button>
<span style="color:var(--d);font-size:10px" id="kwCount">0줄</span>
<span style="color:var(--p);font-size:10px" id="bulkState"></span>
<span style="flex:1"></span>
<select id="kwSiteFilter" style="width:auto"><option value="">전체 실게시 검증 사이트</option>{% for s in publish_sites %}<option value="{{s.id}}">{{s.name or s.site_url[:20]}}</option>{% endfor %}</select></div></div>

<div class="card" style="display:none"><h3>키워드 풀 — 지역순서 적용 및 사용 완료 추적</h3>
<div style="font-size:10px;color:var(--d);margin-bottom:6px">한 줄에 <b style="color:var(--p)">키워드1,키워드2,키워드3</b>을 입력합니다. 2·3은 키워드1과 같은 지역이어야 하며, 예약은 지정 지역순서대로 진행하고 전부 사용하면 자동 종료됩니다.</div>
<textarea id="poolCsv" rows="5" placeholder="키워드1,키워드2,키워드3 — 한 줄에 한 조합&#10;인천셔츠룸,인천노래방,인천가라오케&#10;부천셔츠룸,부천노래방,부천가라오케&#10;서울셔츠룸,서울노래방,서울가라오케"></textarea>
<div class="row" style="margin-top:6px">
<button class="btn btn-p" onclick="savePool()">풀 저장(덮어쓰기)</button>
<button class="btn btn-v" onclick="savePool(true)">풀에 추가</button>
<label class="btn btn-d" style="cursor:pointer">엑셀(.xlsx) 업로드<input type="file" id="poolXlsx" accept=".xlsx" style="display:none" onchange="uploadXlsx()"></label>
<span style="flex:1"></span>
<span style="color:var(--d);font-size:10px" id="poolCount">0개</span>
<button class="btn btn-r btn-xs" onclick="if(confirm('키워드 풀 전체 삭제?'))clearPool()">비우기</button></div>
<div class="row" style="margin-top:6px">
<select id="poolSiteFilter" style="width:auto"><option value="">전체 실게시 검증 사이트</option>{% for s in publish_sites %}<option value="{{s.id}}">{{s.name or s.site_url[:20]}}</option>{% endfor %}</select>
<input type="number" id="poolN" value="1" min="1" max="50" style="width:70px" title="랜덤 뽑을 개수">
<button class="btn btn-g" onclick="genRandom()">랜덤 생성+발행</button>
<span style="color:var(--d);font-size:10px">풀에서 N개 랜덤 추출 → 허용 사이트 발행</span></div></div>
</div>

<div id="p-images" class="panel">
<div class="card" style="border:1px solid var(--p)"><h3>이미지 저장 대상 작업실</h3>
<div style="font-size:10px;color:var(--d);margin-bottom:6px">작업실을 고르면 <b style="color:var(--p)">그 작업실 전용 이미지</b>를 저장·조회합니다. 발행 시 각 작업실 글에는 <b>그 작업실 이미지만</b> 사용됩니다. <b style="color:var(--y)">이미지를 넣지 않은 작업실은 이미지 없이 발행</b>됩니다.</div>
<div class="row"><select id="imgWrSelect" style="flex:1" onchange="onImgWrChange()"><option value="">전체(공통) 이미지</option></select></div></div>
<div class="card"><h3>이미지 파일 저장 <span id="imgWrLabel" style="color:var(--p);font-size:12px"></span></h3>
<div style="font-size:10px;color:var(--d);margin-bottom:8px">JPG·PNG·GIF·WEBP, 파일당 최대 10MB. 저장된 이미지는 파일 첨부란이 있는 게시판에 최대 2개까지 자동으로 들어갑니다.</div>
<div class="row"><input type="file" id="imgFiles" accept="image/jpeg,image/png,image/gif,image/webp" multiple style="flex:1"><button class="btn btn-p" onclick="uploadImages()">선택 이미지 저장</button></div>
<div id="imgGallery" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:12px"></div></div>
<div class="card"><h3>외부 이미지 URL 풀 (본문 삽입 — 한 줄에 하나) <span id="imgWrLabel2" style="color:var(--p);font-size:12px"></span></h3>
<div style="font-size:10px;color:var(--d);margin-bottom:6px">여기에 넣은 이미지 주소에서 <b style="color:var(--p)">매번 랜덤으로</b> 골라 본문에 삽입합니다(alt=지역 자동). <b style="color:var(--y)">작업실 선택 시 비워두면 그 작업실 글엔 이미지가 안 들어갑니다.</b></div>
<textarea id="imgUrls" rows="5" placeholder="https://내사이트.kr/img/room1.jpg&#10;https://내사이트.kr/img/room2.jpg"></textarea>
<div class="row" style="margin-top:6px"><button class="btn btn-p" onclick="saveImages(false)">저장(덮어쓰기)</button><button class="btn btn-v" onclick="saveImages(true)">추가</button><span style="flex:1"></span><span style="color:var(--d);font-size:10px" id="imgCount">0개</span><button class="btn btn-r btn-xs" onclick="if(confirm('이미지 URL 전체 삭제?'))clearImages()">비우기</button></div></div></div>

<div id="p-sites" class="panel">
<div class="card"><h3>사이트 추가</h3>
<div class="row"><input type="text" id="sUrl" placeholder="https://사이트.com (또는 board.php URL)" style="flex:1"><select id="sPlat" style="width:110px"><option value="auto">자동감지</option><option value="gnuboard">그누보드</option><option value="cafe24">Cafe24</option></select></div>
<div class="row"><input type="text" id="sName" placeholder="이름" style="flex:1"><input type="text" id="sBo" placeholder="게시판ID (bo_table) 예:free" style="width:170px"></div>
<!-- ★하루한도/최소간격 입력칸 제거(대표님 지시 2026-09-09 '고정이라 없앰'): 항상 무제한(0)·1분 고정. addSite에서 하드코딩 전송. -->
<div class="row"><input type="text" id="sId" placeholder="아이디" style="width:130px"><input type="password" id="sPw" placeholder="비밀번호" style="width:130px"><button class="btn btn-p" id="addBtn" onclick="addSite()">추가</button><button class="btn btn-d btn-xs" id="editCancel" style="display:none" onclick="cancelEdit()">취소</button></div>
<!-- ★홍보허용 UI 제거(대표님 지시 '무조건 허용'): 자동 허용. 기존 JS 호환 위해 hidden 유지(항상 체크). -->
<div class="row" style="background:#0b1a2e;border:1px solid #166534;border-radius:6px;padding:8px;color:var(--g);font-size:11px">✔ 자동 허용 — 발행 테스트를 통과하면 바로 발행됩니다(별도 허용 절차 없음)</div>
<input type="checkbox" id="sPerm" checked hidden><input type="hidden" id="sPermNote" value="자동 허용">
<div style="font-size:10px;color:var(--d)">게시판ID(bo_table)는 글이 올라갈 게시판 식별자입니다. 예) 자유게시판 free, 홍보게시판 promotion · <b style="color:var(--y)">홍보 허용을 체크한 사이트만 발행/테스트됩니다.</b></div></div>
<div class="card"><h3>CSV 대량 등록 (한 줄에 하나: URL,이름,게시판,아이디,비번,허용여부)</h3>
<textarea id="bulkCsv" rows="4" placeholder="https://a.kr,에이,free,id1,pw1,1&#10;https://b.kr,비,promotion,id2,pw2,0"></textarea>
<div class="row" style="margin-top:6px"><label style="display:flex;align-items:center;gap:6px;color:var(--g);font-size:11px"><input type="checkbox" id="bulkPerm" style="width:auto">허용여부 미기재 시 기본 허용</label><span style="flex:1"></span><button class="btn btn-p" onclick="bulkAdd()">대량 등록</button></div>
<div style="font-size:10px;color:var(--d)">허용여부: 1/0 (마지막 칸). 미기재면 위 체크박스 기본값. 등록 후에도 목록에서 선택→허용 일괄 변경 가능.</div></div>
<div class="card"><h3>사이트 목록 (실시간 갱신)</h3>
<div class="row" style="margin-bottom:6px"><button class="btn btn-d btn-xs" onclick="healthAll()">선택 상태점검</button><span style="flex:1"></span><span style="color:var(--d);font-size:10px">발행테스트 통과 시 자동 허용 · 체크박스로 선택 후 상태점검</span></div>
<div style="max-height:400px;overflow-y:auto" id="siteList"></div></div></div>

<!-- ★결과 탭(p-res) 제거 — 발행 현황(p-wlog) 탭에 병합됨(대표님 지시 2026-09-09). -->

<div id="p-wlog" class="panel">
<div class="note">작업실에서 시작한 글 생성 준비와 실제 워커 발행 상태를 작업실별로 확인합니다. 준비 완료 뒤에는 큐→발행 중→성공/실패→결과 URL 순서로 기록됩니다.</div>
<div class="card"><h3>워커 실행로그</h3>
<div class="row"><select id="wlogRoom" style="width:auto" onchange="renderWorkerLog()"><option value="">전체 작업실</option></select><button class="btn btn-d" onclick="renderWorkerLog()">새로고침</button><span style="flex:1"></span><span id="wlogWorker" style="color:var(--d);font-size:10px"></span></div>
<div id="wlogBlock" style="margin:8px 0"></div>
<div id="captchaTasks" style="margin:8px 0"></div>
<div id="wlogTasks" style="margin:8px 0"></div></div>
<div class="card" style="margin:10px 0"><div class="row" style="align-items:center"><h3 style="margin:0">🛰️ 관제실 — 실시간 작업 현황</h3><span style="flex:1"></span>
<span id="actCounts" style="font-size:11px;color:var(--d)"></span></div>
<!-- ★관제실(대표님 지시 2026-09-09): 세로 배치 — 위=워커 세계(발행), 아래=러너 세계(PC발굴 포함).
     좌우 2단은 러너가 잘 안 보인다 하여 상하로. PC 발굴은 '발굴' 구획에 실시간으로 흐름. -->
<div id="ctrlGrid" style="display:grid;grid-template-rows:auto auto;gap:10px;margin-top:8px">
  <!-- 워커 세계(위) -->
  <div style="border:1px solid #166534;border-radius:8px;overflow:hidden">
    <div style="background:#0d2a17;color:var(--g);padding:8px 12px;font-weight:700;font-size:13px">🖥️ 워커 세계 <span style="color:var(--d);font-weight:400">· 실제 글 올리는 발행</span> <span id="cnt발행" style="float:right;color:var(--d)"></span></div>
    <div style="max-height:280px;overflow-y:auto" id="col발행"></div>
  </div>
  <!-- ★기기별 실시간 현황(대표님 지시 2026-09-11): PC/노트북 각각 마지막활동·발행·상태 카드 -->
  <div id="nodeStrip" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px"></div>
  <!-- 러너 세계(아래, PC발굴 포함) — 4구획을 좌우로 나눠 한눈에 -->
  <div style="border:1px solid #4c1d95;border-radius:8px;overflow:hidden">
    <div style="background:#1a0f2e;color:var(--v);padding:8px 12px;font-weight:700;font-size:13px">🏃 러너 세계 <span style="color:var(--d);font-weight:400">· 발굴(PC 연동)·검수·가입·정리</span></div>
    <!-- minmax(0,1fr): 1fr만 쓰면 nowrap 로그 한 줄의 min-content가 열 최소폭이 돼 그리드가 화면 밖으로 넘침(대표님 2026-09-11 '오른쪽 짤림') -->
    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <div style="border-top:1px solid #33425f;border-right:1px solid #33425f;min-width:0"><div style="padding:6px 11px;font-size:12px;color:var(--p)">🔍 발굴(PC) <span id="cnt발굴" style="float:right;color:var(--d)"></span></div><div style="max-height:190px;overflow-y:auto" id="col발굴"></div></div>
      <div style="border-top:1px solid #33425f;min-width:0"><div style="padding:6px 11px;font-size:12px;color:var(--v)">📋 검수 <span id="cnt검수" style="float:right;color:var(--d)"></span></div><div style="max-height:190px;overflow-y:auto" id="col검수"></div></div>
      <div style="border-top:1px solid #33425f;border-right:1px solid #33425f;min-width:0"><div style="padding:6px 11px;font-size:12px;color:var(--y)">👤 가입 <span id="cnt가입" style="float:right;color:var(--d)"></span></div><div style="max-height:190px;overflow-y:auto" id="col가입"></div></div>
      <div style="border-top:1px solid #33425f;min-width:0"><div style="padding:6px 11px;font-size:12px;color:var(--r)">🧹 정리 <span id="cnt정리" style="float:right;color:var(--d)"></span></div><div style="max-height:190px;overflow-y:auto" id="col정리"></div></div>
    </div>
  </div>
</div>
<!-- ★결과탭 병합(대표님 지시 2026-09-09): 관제실 아래에 발행이력 표를 함께(제목=링크·발행링크 클릭). -->
<div class="card" style="margin-top:10px"><div class="row" style="align-items:center"><h3 style="margin:0">📮 발행 이력</h3><span id="histCount" style="color:var(--d);font-size:11px"></span><span style="flex:1"></span>
<select id="histDate" style="width:auto" onchange="renderHistory(true)" title="날짜별 보기"><option value="">전체 날짜</option></select>
<button class="btn btn-d btn-xs" onclick="renderHistory(true)">새로고침</button>
<button class="btn btn-g btn-xs" onclick="window.open('/api/history/export','_blank')">엑셀 내보내기</button>
<button class="btn btn-r btn-xs" onclick="if(confirm('이력 전체 삭제?'))api('/history/clear','POST').then(()=>{toast('이력 삭제됨');renderHistory(true)})">이력 비우기</button></div>
<div style="max-height:520px;overflow-y:auto;margin-top:6px" id="histList"></div></div>
</div></div>

<div id="p-disco" class="panel">
<div class="note">🔎 Brave 검색 → 접속 성공 → 오류/데모/웹빌더·영구탈락 제외 → 게시판 글쓰기 폼 확인까지 통과한 곳만 후보로 수집합니다. 그다음 자동가입·발행테스트로 <b style="color:var(--g)">실제 되는 곳만 자동 등록</b>, 안 되는 곳은 자동 탈락됩니다. (수동 URL은 최우선 처리)</div>
<div id="dcSummary" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px"></div>
<div class="card"><h3>후보 수집 <span style="font-size:11px;color:var(--g);font-weight:400">🟢 완전자동 작동 중 — 발굴·검수·가입·발행이 24시간 자동으로 돕니다</span></h3>
<div class="row">
<button class="btn btn-g" onclick="location='/api/candidates/export'">엑셀 내보내기</button>
<span style="flex:1"></span>
<button class="btn btn-r btn-xs" onclick="if(confirm('탈락 후보만 삭제할까요?'))clearRejected()">탈락 정리</button></div>
<div style="font-size:10px;color:var(--d);margin:8px 0 4px">특정 사이트를 직접 넣고 싶을 때만 아래에 URL을 붙여넣으세요. 나머지는 자동입니다.</div>
<textarea id="dcUrls" rows="3" placeholder="URL 직접 추가 (한 줄에 하나)&#10;https://example.kr/bbs/board.php?bo_table=promotion"></textarea>
<div class="row" style="margin-top:6px"><button class="btn btn-v" onclick="addManual()">URL 추가 + 검수</button></div></div>
<div class="card"><h3>후보 목록 (점수순 — 위에서부터 검토)</h3>
<div class="row" style="margin-bottom:6px"><select id="dcFilter" style="width:auto" onchange="renderCands()"><option value="">전체</option><option value="ready">검수완료</option><option value="approved">사이트 등록됨</option><option value="rejected">제외</option><option value="new">미검수</option></select><span style="flex:1"></span><span style="color:var(--d);font-size:10px" id="dcCount">0개</span></div>
<div style="max-height:520px;overflow-y:auto" id="dcList"></div></div></div>

<div id="p-mem" class="panel">
<div class="note">👥 회원·정산 + ⏰ 계정당 개별 자동발행 — 회원마다 <b style="color:var(--p)">원하는 시간대</b>를 지정하면 서버가 24시간 그 시간에 자동 실행합니다(PC 불필요). 회원별 전용 키워드·담당 사이트를 따로 배정할 수 있고, 시간분산으로 동시 폭주를 막습니다. 월 청구 = 기본료 + (추가 광고수 × 추가단가).</div>
<div id="memSummary" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px"></div>
<div class="card"><h3>회원 추가 / 수정</h3>
<div class="row"><input type="text" id="mName" placeholder="담당자/이름" style="flex:1"><input type="text" id="mBiz" placeholder="업소명" style="flex:1"><input type="text" id="mPhone" placeholder="연락처" style="width:150px"></div>
<div class="row"><span style="color:var(--d);font-size:11px">기본료(월)</span><input type="number" id="mFee" value="30000" style="width:110px">
<span style="color:var(--d);font-size:11px">추가광고수</span><input type="number" id="mAddons" value="0" min="0" style="width:80px">
<span style="color:var(--d);font-size:11px">추가단가</span><input type="number" id="mAddonFee" value="10000" style="width:100px">
<span style="color:var(--d);font-size:11px">정산일</span><input type="number" id="mDay" value="1" min="1" max="31" style="width:70px">
<select id="mStatus" style="width:auto"><option value="active">활성</option><option value="paused">정지</option></select></div>
<div class="row"><input type="text" id="mMemo" placeholder="메모 (선택)" style="flex:1"></div>
<div style="border-top:1px solid var(--line);margin:10px 0;padding-top:12px">
<div style="font-size:11px;color:var(--p2);font-weight:700;margin-bottom:8px">⏰ 이 회원의 자동발행 스케줄 (계정당 개별 관리)</div>
<label style="display:flex;align-items:center;gap:6px;color:var(--g);font-size:12px;margin-bottom:8px"><input type="checkbox" id="mSchedOn" style="width:auto">자동발행 켜기 — 서버가 24시간 이 시간에 자동 실행</label>
<div class="row"><input type="text" id="mTimes" placeholder="점프 시간대 HH:MM, 쉼표로 여러개 (예: 09:00,14:00,20:00)" style="flex:1"></div>
<div class="row"><span style="color:var(--d);font-size:11px">요일(미선택=매일):</span>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="0" style="width:auto">월</label>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="1" style="width:auto">화</label>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="2" style="width:auto">수</label>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="3" style="width:auto">목</label>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="4" style="width:auto">금</label>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="5" style="width:auto">토</label>
<label style="font-size:11px"><input type="checkbox" class="mDay" value="6" style="width:auto">일</label>
<span style="flex:1"></span>
<span style="color:var(--d);font-size:11px">1회당 발행수</span><input type="number" id="mPerRun" value="1" min="1" max="20" style="width:70px">
<span style="color:var(--d);font-size:11px">시간분산(분)</span><input type="number" id="mJitter" value="5" min="0" max="60" style="width:70px" title="설정 시각에서 0~N분 랜덤 지연 — 동시 폭주 방지"></div>
<div class="row"><textarea id="mKw" rows="3" placeholder="이 회원 전용 키워드 (지역,서비스,브랜드 — 한 줄에 하나)&#10;비우면 공용 키워드 풀 사용&#10;인천,셔츠룸,인천홍마니"></textarea></div>
<div class="row"><span style="color:var(--d);font-size:11px">담당 사이트(미선택=전체 허용 사이트):</span><div id="mSiteBox" style="display:flex;gap:8px;flex-wrap:wrap;font-size:11px"></div></div>
</div>
<div class="row"><button class="btn btn-p" id="memBtn" onclick="addMember()">회원 추가</button><button class="btn btn-d btn-xs" id="memCancel" style="display:none" onclick="cancelMember()">취소</button></div></div>
<div class="card"><h3>회원 목록 (이번 달 정산)</h3>
<div class="row" style="margin-bottom:6px"><button class="btn btn-g" onclick="location='/api/members/export'">엑셀 내보내기</button><button class="btn btn-d" onclick="renderMembers()">새로고침</button><span style="flex:1"></span><span style="color:var(--d);font-size:10px" id="memCount">0명</span></div>
<div style="max-height:460px;overflow-y:auto" id="memList"></div></div></div>

<div id="p-stats" class="panel">
<div class="card"><h3>발행 통계</h3>
<div class="row" style="margin-bottom:8px"><button class="btn btn-d btn-xs" onclick="renderStats()">새로고침</button><button class="btn btn-v btn-xs" onclick="api('/verify/now','POST').then(()=>toast('생존 확인 시작 (1~2분 후 갱신)'))">지금 생존확인</button></div>
<div id="statTop" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px"></div>
<div id="statReasons"></div>
<div id="statSurvival"></div>
<div style="font-size:11px;color:var(--d);margin:6px 0">최근 14일 (초록=성공, 빨강=실패)</div>
<div id="statDays"></div>
<div style="font-size:11px;color:var(--d);margin:14px 0 6px">사이트별 (상위 12)</div>
<div id="statSites"></div></div></div>

<div id="p-cost" class="panel">
<div class="card"><div class="row" style="align-items:center"><h3 style="margin:0">API 실시간 비용 · 사용량</h3><span style="flex:1"></span><span id="costAutoInfo" style="font-size:10px;color:var(--d);margin-right:8px">30초마다 자동 새로고침</span><button class="btn btn-d btn-xs" onclick="loadUsageDashboard()">새로고침</button></div>
<div style="font-size:11px;color:var(--d);margin-top:6px">이번 달 합계 <b id="costTotalMonth" style="color:var(--p)">-</b> · 오늘 <b id="costTotalToday" style="color:var(--g)">-</b> <span style="color:var(--y)">· AI 글 생성: OpenRouter는 응답의 <b>실측</b> 청구액 · NVIDIA는 무료($0) · 종료된 모델 행은 토큰 추정 | 2captcha·Brave는 설정 단가×횟수 <b>추정</b>(횟수는 정확).</span></div>
</div>
<div id="costCards" style="display:grid;grid-template-columns:1fr;gap:12px;margin-top:10px"></div>
</div>

<div id="p-set" class="panel">
<!-- ★설정 탭 압축 재구성(대표님 2026-09-11 '자리 차지 많다·깔끔하게·눈에 잘 들어오게' + 'OpenAI 화면에서 제거'):
     라벨-입력 가로 배치(.f), 3열 자동 그리드, 긴 설명은 title 툴팁/한 줄로, 고급(IMAP·Bright Data·지역생성·단가)은 접힘(details.adv).
     모든 input id는 saveCfg/loadCfgUI와 1:1 — 바꾸지 말 것. OpenAI 키·모델·예산·단가 입력은 삭제됨(엔진: OpenRouter/NVIDIA). -->
<div class="grid3">

<div class="card"><h3>🏷 기본 정보</h3>
<div class="f"><small>브랜드명</small><input id="cBrand" value="{{cfg.brand}}"></div>
<div class="f"><small>대표 전화</small><input id="cPhone" value="{{cfg.phone}}"></div>
<div class="f"><small>전화 목록<br><span style="font-size:9.5px">한 줄 하나 · 제목에 랜덤</span></small><textarea id="cPhones" rows="2" placeholder="01082755736&#10;01021636400" title="여러 개면 그 중 하나를 랜덤 선택. 제목의 번호는 [010]↔8275↔5736 처럼 기호가 매번 바뀜. 비우면 대표 전화 사용"></textarea></div>
<div class="f"><small>동영상 URL</small><input id="cVideoUrl" placeholder="https://www.youtube.com/watch?v=..." title="영상란이 필수인 게시판에 자동 입력"></div>
<div class="f"><small>랜딩 URL</small><input id="cLandingUrl" placeholder="https://내사이트.kr/" title="링크란이 필수인 게시판에 자동 입력"></div>
<div class="f"><small>게시용 이메일</small><input id="cPostEmail" type="email" placeholder="name@example.com" title="이메일이 필수인 게시판에만 사용"></div>
<div class="f"><small>비회원 글 비번</small><input id="cGuestPw" type="password" placeholder="변경시만 입력"></div>
<details class="adv"><summary>📧 IMAP 이메일 인증 (자동가입용 · 지메일 앱 비밀번호)</summary>
<div class="help">가입 인증메일을 IMAP으로 자동 읽음. 각 가입은 <b>내주소+랜덤@gmail.com</b>으로 유니크 발급. 지메일은 2단계인증 후 <b>앱 비밀번호</b>(일반 비번 아님)·IMAP 사용 켜기. 비우면 임시메일.</div>
<div class="f"><small>이메일</small><input id="cImapEmail" type="email" placeholder="mymail@gmail.com"></div>
<div class="f"><small>앱 비밀번호</small><input id="cImapPass" type="password" placeholder="16자리 (변경시만)"></div>
<div class="f"><small>IMAP 호스트</small><input id="cImapHost" placeholder="imap.gmail.com"></div></details>
</div>

<div class="card"><h3>⚙️ 발행 제어</h3>
<div class="r3">
<div><small class="lb">워커 수 (권장 4)</small><input type="number" id="cWorkers" value="{{cfg.workers}}" min="1" max="6" title="서로 다른 사이트 동시 발행 수 · 최대 6"></div>
<div><small class="lb">글 간격(초)</small><input type="number" id="cDelay" value="{{cfg.post_delay}}" min="0" title="도배 방지 — 포스트 간 지연"></div>
<div><small class="lb">사이트당 1일 한도</small><input type="number" id="cDaily" value="{{cfg.daily_limit}}" min="0" title="0 = 무제한"></div>
</div>
<div class="f" style="margin-top:6px"><small>관제실 비밀번호</small><input type="password" id="cPw" placeholder="변경시만 입력"></div>
<label class="chk"><input type="checkbox" id="cMixKw">지역 연동 키워드 혼합 <span class="help" style="margin:0">(같은 지역의 서비스·브랜드만 조합)</span></label>
<label class="chk"><input type="checkbox" id="cVerify">발행글 생존 자동 검증 <span class="help" style="margin:0">(1시간마다 URL 재확인)</span></label>
<label class="chk"><input type="checkbox" id="cBlockUnpaid">미납 회원 자동 정지</label>
<div class="help">중복 방지는 상시 작동 — 제목·본문은 과거와 겹치지 않게 매번 새로 생성, 같은 키워드라도 사이트마다 다름.</div>
<div class="row" style="margin:6px 0 0;gap:5px">
<button class="btn btn-g btn-xs" onclick="api('/workers/start','POST',{n:parseInt(document.getElementById('cWorkers').value)||2}).then(()=>toast('워커 시작'))">워커 시작</button>
<button class="btn btn-y btn-xs" onclick="api('/workers/pause','POST').then(()=>toast('일시정지'))">일시정지</button>
<button class="btn btn-v btn-xs" onclick="api('/workers/resume','POST').then(()=>toast('재개'))">재개</button>
<button class="btn btn-r btn-xs" onclick="api('/workers/stop','POST').then(()=>toast('워커 정지'))">정지</button>
<button class="btn btn-d btn-xs" onclick="if(confirm('통계 초기화?'))api('/workers/reset','POST').then(()=>toast('초기화됨'))">통계 초기화</button></div>
<div class="help" style="margin-top:4px">일시정지는 큐를 지우지 않고 멈춤(재개 시 이어서). 패널을 껐다 켜도 미완료 작업은 자동 복구.</div>
</div>

<div class="card"><h3>🧠 AI 본문 생성</h3>
<label class="chk" style="color:var(--g)"><input type="checkbox" id="cUseGpt">AI로 본문 생성 (끄면 템플릿만)</label>
<div class="f"><small>엔진</small><select id="cLlmProvider"><option value="openrouter">OpenRouter · 저가 (deepseek 등)</option><option value="nvidia">NVIDIA · 무료 (build.nvidia.com)</option></select></div>
<div class="f"><small>OpenRouter 키 <a href="#" onclick="clearKey('openrouter_api_key');return false" title="서버에서 키 삭제">🗑</a></small><input type="password" id="cOpenrouterKey" placeholder="sk-or-v1-... (변경시만)"></div>
<div class="f"><small>OpenRouter 모델</small><input id="cOpenrouterModel" placeholder="deepseek/deepseek-v4-flash-0731"></div>
<div class="f"><small>NVIDIA 키 <a href="#" onclick="clearKey('nvidia_api_key');return false" title="서버에서 키 삭제">🗑</a></small><input type="password" id="cNvidiaKey" placeholder="nvapi-... (변경시만)"></div>
<div class="f"><small>NVIDIA 모델</small><input id="cNvidiaModel" placeholder="nvidia/nemotron-3-ultra-550b-a55b"></div>
<div class="row" style="margin:4px 0 0;gap:5px"><button class="btn btn-g btn-xs" onclick="window.open('https://openrouter.ai','_blank')">OpenRouter 사이트 ↗</button><button class="btn btn-d btn-xs" onclick="window.open('https://openrouter.ai/settings/credits','_blank')">크레딧 충전 ↗</button><button class="btn btn-d btn-xs" onclick="loadOpenAIUsage()">사용량 새로고침</button></div>
<div id="openaiUsage" style="margin-top:7px;padding:7px 9px;background:#0b1322;border:1px solid var(--b);border-radius:7px;font-size:10.5px;color:var(--d);line-height:1.55">사용량 불러오는 중...</div>
<div class="help">키워드1을 메인 주제로 1,800~2,800자 장문, 키워드2·3은 같은 지역의 보조 키워드. 분당 한도(429)에 걸리면 60초 템플릿 후 자동 재개. 🗑 = 서버에서 키 삭제.</div>
</div>

<div class="card"><h3>🔎 도메인 발굴 (Brave Search)</h3>
<div class="f"><small>Brave API 키 <a href="https://api-dashboard.search.brave.com/" target="_blank" rel="noopener" title="Brave API 대시보드" style="color:var(--p)">↗</a></small><input type="password" id="cBraveKey" placeholder="변경시만 입력"></div>
<div class="r2"><div><small class="lb">하루 후보 목표</small><input type="number" id="cDTarget" value="100" min="10" max="1000"></div><div><small class="lb">하루 쿼리 한도</small><input type="number" id="cDQuery" value="100" min="1" max="10000" title="Brave 플랜 한도 안에서"></div></div>
<label class="chk" style="color:var(--g)"><input type="checkbox" id="cDiscoOn">24시간 자동 발굴 (1분마다)</label>
<div class="f" style="grid-template-columns:1fr"><small>검색어 목록 — 한 줄 하나, 그대로 Brave 검색 · <b>게시판 URL 조각</b>(예 <code>bbs/board.php?bo_table=free&amp;wr_id=</code>)도 가능 · #은 메모</small>
<textarea id="cDDirect" rows="5" placeholder="bbs/board.php?bo_table=free&amp;wr_id=&#10;&quot;홍보게시판&quot; 마사지&#10;# 메모(검색 안 함)"></textarea></div>
<div class="f" style="grid-template-columns:1fr"><small>🚫 제외 도메인 — 웹빌더 등 · 한 줄 하나, 이 문자열이 포함되면 즉시 제외</small><textarea id="cExcludedDomains" rows="2" placeholder="isweb.co.kr&#10;imweb.me&#10;modoo.at"></textarea></div>
<div class="row" style="margin:2px 0 0;font-size:11px;color:var(--r)">🗂 자동 탈락 <b id="rejCount">-</b>개 <span class="help" style="margin:0">(시스템이 자동 수집·제외)</span><button class="btn btn-d btn-xs" type="button" onclick="showRejected()">목록</button></div>
<div id="rejList" style="display:none;margin-top:6px;max-height:220px;overflow:auto;background:#0d1420;border:1px solid var(--b);border-radius:8px;padding:8px;font-size:11px;color:var(--d)"></div>
<details class="adv"><summary>전국 시·구·동 + 키워드 일괄 생성</summary>
<div class="help">서울+키워드 · 강남+키워드 · 호암직동+키워드 식으로 단계별 짧은 지역명 조합. 중복은 자동 제거.</div>
<div class="row" style="gap:4px"><select id="rgProvince" style="width:auto" onchange="fillGuSel('rgProvince','rgGuSel')"><option value="">전국</option></select><select id="rgGuSel" style="width:auto" title="특정 시·군·구만 (비우면 시·도 전체)"><option value="">시·군·구 전체</option></select>
<label class="chk" style="margin:0"><input type="checkbox" id="rgCity" checked>시·도</label><label class="chk" style="margin:0"><input type="checkbox" id="rgGu" checked>시·구·군</label><label class="chk" style="margin:0"><input type="checkbox" id="rgDong" checked>읍·면·동</label>
<select id="rgJoin" style="width:auto"><option value="">붙여쓰기</option><option value=" ">띄어쓰기</option></select></div>
<textarea id="rgKeywords" rows="2" placeholder="출장마사지&#10;홍보게시판"></textarea>
<div class="row" style="margin-top:5px;gap:4px"><button class="btn btn-d btn-xs" onclick="previewRegionalKeywords()">개수 확인</button><button class="btn btn-v btn-xs" onclick="applyRegionalKeywords(false)">목록에 추가</button><button class="btn btn-y btn-xs" onclick="applyRegionalKeywords(true)">목록 교체</button><span id="rgCount" style="font-size:10px;color:var(--g)"></span></div></details>
<details class="adv"><summary>API 단가 (비용 탭 추정용 — 2captcha·Brave는 건당 실과금을 안 알려줌)</summary>
<div class="r3" style="margin-top:5px"><div><small class="lb">Brave $/쿼리</small><input type="number" id="cBravePrice" min="0" step="0.001" value="0.005"></div><div><small class="lb">reCAPTCHA $/건</small><input type="number" id="cCapRePrice" min="0" step="0.0001" value="0.003"></div><div><small class="lb">이미지캡차 $/건</small><input type="number" id="cCapImgPrice" min="0" step="0.0001" value="0.0005"></div></div></details>
</div>

<div class="card"><h3>🤖 CAPTCHA 자동 해결 (2captcha)</h3>
<label class="chk" style="color:var(--g)"><input type="checkbox" id="cTwocaptchaEn">자동 해결 켜기 (reCAPTCHA · hCaptcha · Turnstile · kCaptcha)</label>
<div class="f"><small>2captcha 키</small><input type="password" id="cTwocaptchaKey" placeholder="변경시만 입력"></div>
<div id="twocaptchaUsage" style="margin-top:4px;padding:7px 9px;background:#0b1322;border:1px solid var(--b);border-radius:7px;font-size:10.5px;color:var(--d)">2captcha 상태 확인 중...</div>
<div class="help"><a href="https://2captcha.com/" target="_blank" rel="noopener" style="color:var(--p)">2captcha 계정 ↗</a> · 자동 해결 실패 시 수동 입력으로 폴백.</div>
</div>

<div class="card"><h3>📣 텔레그램 · 백업</h3>
<div class="f"><small>봇 토큰</small><input type="password" id="cTgTok" placeholder="변경시만 입력"></div>
<div class="f"><small>챗 ID</small><input id="cTgChat" placeholder="123456789"></div>
<div class="row" style="gap:10px;margin:2px 0"><label class="chk" style="margin:0"><input type="checkbox" id="cNotifyDone">성공알림</label><label class="chk" style="margin:0"><input type="checkbox" id="cNotifyFail">실패알림</label><label class="chk" style="margin:0"><input type="checkbox" id="cTgControl">폰 제어</label><button class="btn btn-d btn-xs" onclick="api('/telegram/test','POST').then(r=>toast(r&&r.ok?'전송됨':'실패: '+(r&&r.error||''),r&&r.ok?'ok':'er'))">테스트 전송</button></div>
<div class="f"><small>자동 백업 시각</small><div class="row" style="margin:0;gap:5px;flex-wrap:nowrap"><input id="cBackupTime" placeholder="04:00 (KST · 비우면 끔)" style="flex:1;min-width:0"><button class="btn btn-g btn-xs" onclick="api('/backup/now','POST').then(r=>toast(r&&r.ok?'📦 백업 전송됨':'실패: '+(r&&r.error||'토큰 확인'),r&&r.ok?'ok':'er'))">지금 백업</button></div></div>
<div class="help">폰 제어: 봇에 /상태 /오늘 /발행 /정지 /재개 /백업 /검증 (등록된 챗ID만). 백업은 zip(설정·사이트·이력·예약·키워드)을 텔레그램으로 전송.</div>
</div>

<div class="card"><h3>🔑 자동가입 고정 계정</h3>
<div class="r2"><div><small class="lb">고정 아이디</small><input type="text" id="cSignupId" placeholder="예: ghdakseovy"></div><div><small class="lb">고정 비밀번호</small><input type="password" id="cSignupPw" placeholder="변경시만 입력"></div></div>
<div class="help">비우면 랜덤 계정. 게시판마다 아이디 규칙(글자수)이 달라 가끔 안 맞을 수 있음.</div>
</div>

<div class="card" style="grid-column:1/-1"><details class="adv" style="border:none;padding:0;margin:0"><summary>🌐 Bright Data 고급 — 프록시 · Web Unlocker · Scraping Browser (Cloudflare 걸린 Cafe24용, 평소엔 안 건드림)</summary>
<div class="grid3" style="margin-top:8px">
<div><label class="chk" style="color:var(--g)"><input type="checkbox" id="cProxyEn">프록시 사용 (Residential)</label>
<div class="r2"><div><small class="lb">Host</small><input type="text" id="cProxyHost" placeholder="brd.superproxy.io"></div><div><small class="lb">Port</small><input type="text" id="cProxyPort" placeholder="22225"></div></div>
<div class="r2" style="margin-top:4px"><div><small class="lb">Username</small><input type="text" id="cProxyUser" placeholder="brd-customer-...-zone-..."></div><div><small class="lb">Password</small><input type="password" id="cProxyPass" placeholder="변경시만"></div></div>
<label class="chk"><input type="checkbox" id="cProxyCfOnly">CF 걸린 사이트에만 사용 (비용 절약)</label></div>
<div><label class="chk" style="color:var(--g)"><input type="checkbox" id="cUnlockerEn">Web Unlocker (CF·캡차 자동해결 · 약 $1.5/1000건)</label>
<div class="f"><small>API 키</small><input type="password" id="cUnlockerKey" placeholder="변경시만"></div>
<div class="f"><small>존 이름</small><input type="text" id="cUnlockerZone" placeholder="web_unlocker1"></div></div>
<div><label class="chk" style="color:var(--g)"><input type="checkbox" id="cSbrEn">Scraping Browser (로그인형 Cafe24 · GB 과금)</label>
<div class="f" style="grid-template-columns:1fr"><small>Endpoint</small><input type="password" id="cSbrEp" placeholder="brd-customer-...-zone-scraping_browser:PASS@brd.superproxy.io:9515 (변경시만)"></div>
<div class="help"><a href="https://brightdata.com/" target="_blank" rel="noopener" style="color:var(--p)">Bright Data ↗</a> · 키·비번은 화면·API에 노출 안 됨 · 실패 시 직접연결로 폴백.</div></div>
</div></details></div>

</div>
<div style="display:flex;gap:8px;align-items:center;margin:4px 0 10px;flex-wrap:wrap">
<button class="btn btn-p" onclick="saveCfg()">💾 설정 저장</button>
<span class="help" style="margin:0">모든 카드의 값이 한 번에 저장됩니다.</span><span style="flex:1"></span>
<button class="btn btn-v btn-xs" onclick="runDiag()">🩺 서버 자가진단 (최대 60초)</button></div>
<div id="diagOut"></div></div>

</div><!-- wrap -->

<script>
const $=id=>document.getElementById(id);
// ★탭 전환 방어(대표님 제보 '빈페이지 뜸' 2026-09-09): 탭버튼/패널이 없거나 렌더 1개가 던져도
//   페이지 전체가 하얗게 비지 않도록 null가드 + try/catch. 패널은 무조건 먼저 보이게 한 뒤 렌더 호출.
function T(n){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));const _tb=document.querySelector(`[onclick="T('${n}')"]`);if(_tb)_tb.classList.add('on');const _pn=$('p-'+n);if(_pn)_pn.classList.add('on');try{if(n==='wlog'){renderWorkerLog();renderHistory();renderCaptchaTasks()}if(n==='stats')renderStats();if(n==='cost'){loadUsageDashboard();startUsageAuto()}else{stopUsageAuto()}if(n==='set'){loadCfgUI();loadRegionTool()}if(n==='gen'){loadPool();loadImages();loadRegionTool()}if(n==='kw'){loadWorkrooms();loadRegionTool()}if(n==='mem'){renderMembers();if(!document.querySelector('.mSite'))fillSiteBox([])}if(n==='disco')renderCands()}catch(e){console.error('탭 렌더 오류',n,e)}}
function toast(m,c='ok'){const d=$('toasts');const e=document.createElement('div');e.className='toast toast-'+c;e.textContent=m;d.appendChild(e);setTimeout(()=>e.remove(),2500)}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function api(p,m,b){try{const o={method:m,headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);const r=await fetch('/api'+p,o);
  // ★세션만료(401)만 로그인으로. 403(차단·일시)은 로그인으로 못고치고 페이지만 하얗게 되므로 조용히 무시.
  //   (대표님 제보 '빈페이지' 2026-09-09: 배포 재시작 중 일시 403이 로그인 리다이렉트→빈화면 유발했음.)
  if(r.status===401){if(!window._reloginAt||Date.now()-window._reloginAt>5000){window._reloginAt=Date.now();location='/login'}return null}
  if(r.status===403){return null}
  // ★JSON이 아닌 응답(재시작 중 HTML 등)은 조용히 무시 — 폴링(2초)마다 에러 토스트 뜨던 문제 해결.
  const ct=r.headers.get('content-type')||'';if(ct.indexOf('application/json')<0){return null}
  return await r.json()}catch(e){return null}}
// (kv/gen/genBulk 죽은 JS 제거됨 — k1/k2/k3 입력칸이 없어 호출 불가였음)
function parseList(){return $('kwlist').value.split('\n').map(l=>l.trim()).filter(Boolean).map(l=>{const p=l.split(',');return{지역:(p[0]||'').trim(),서비스:(p[1]||'').trim(),브랜드:(p[2]||'').trim()}}).filter(k=>k.지역&&k.서비스&&k.브랜드)}
let _bulkPolling=false;
async function genFromList(){const sets=parseList();if(!sets.length){toast('키워드 없음','er');return}const sid=$('kwSiteFilter').value;const wid=$('kwlist').dataset.workroomId||'',wn=$('kwlist').dataset.workroomName||'직접 입력';if(!confirm('['+wn+'] '+sets.length+'개 키워드 조합의 생성 작업을 시작할까요?\n사이트 한도와 최소 간격에 따라 지금 가능한 수만 실행됩니다.'))return;const b=$('bulkRunBtn');b.disabled=true;b.textContent='작업 준비 중...';$('bulkState').textContent='서버에 작업을 전달하는 중';toast('['+wn+'] 목록 작업 준비를 시작했습니다');const r=await api('/bulk','POST',{keyword_sets:sets,site_ids:sid?[sid]:[],workroom_id:wid,workroom_name:wn});if(!r||!r.ok){b.disabled=false;b.textContent='목록 생성 작업 시작';$('bulkState').textContent='';if(r)toast(r.error||'실패','er');return}const rem=r.remaining?(' · 나머지 '+r.remaining+'개는 한도/간격상 미실행'):'';$('bulkState').textContent='['+wn+'] 준비 '+r.accepted+'/'+r.requested+rem;toast('즉시 실행 가능 '+r.accepted+'개 준비 시작'+rem,'ok');pollBulkTask(r.task_id)}
async function pollBulkTask(id){if(_bulkPolling)return;_bulkPolling=true;const b=$('bulkRunBtn');for(let i=0;i<600;i++){const r=await api('/bulk/status/'+id,'GET');if(!r||!r.ok)break;$('bulkState').textContent='글 생성 '+r.done+'/'+r.total+' · 큐 '+r.queued+(r.remaining?' · 대기 필요 '+r.remaining:'');if(r.status==='done'){toast('준비 완료 · '+r.queued+'건이 발행 큐에 등록됨','ok');break}if(r.status==='failed'){toast('준비 실패: '+(r.error||''),'er');break}await new Promise(x=>setTimeout(x,1500))}_bulkPolling=false;b.disabled=false;b.textContent='목록 생성 작업 시작'}
function getSiteIds(){return Array.from(document.querySelectorAll('.cb:checked')).map(c=>c.dataset.id)}
function getAllSiteIds(){return Array.from(document.querySelectorAll('#siteList tr[data-id]')).map(r=>r.dataset.id)}
async function postSel(){const t=$('gTitle').value.trim();const c=$('gContent').value.trim();if(!t||!c){toast('제목/본문 입력','er');return}const ids=getSiteIds();if(!ids.length){toast('사이트 선택','er');return}const r=await api('/post','POST',{site_ids:ids,title:t,content:c,region:$('k1').value.trim(),service:$('k2').value.trim(),brand:$('k3').value.trim()});if(r&&r.ok)toast(r.queued+'개 큐 등록'+(r.blocked?` · 미허용 ${r.blocked}개 제외`:''),r.queued?'ok':'er')}
async function postAll(){const t=$('gTitle').value.trim();const c=$('gContent').value.trim();if(!t||!c){toast('제목/본문 입력','er');return}const ids=getAllSiteIds();if(!ids.length){toast('사이트 없음','er');return}const r=await api('/post','POST',{site_ids:ids,title:t,content:c,region:$('k1').value.trim(),service:$('k2').value.trim(),brand:$('k3').value.trim()});if(r&&r.ok)toast(r.queued+'개 큐 등록'+(r.blocked?` · 미허용 ${r.blocked}개 제외`:''),r.queued?'ok':'er')}
// ---- 발행 이력 렌더 (2열 컴팩트) ----
// ★2열용 컴팩트 행(대표님 지시 2026-09-09 '한눈에'): 좁은 2열에서 키워드가 세로로 쪼개지던 것 해결.
//   열을 시간·상태·제목(링크)만으로 간결하게, 모두 nowrap+말줄임 → 한 줄씩 깔끔.
function histRowC(h){const stc=h.status==='done'?'ok':(h.status==='failed'?'f':(h.status==='retry'?'y':'i'));
  const STMAP={done:'완료',posting:'발행중',failed:'실패',retry:'재시도',queued:'대기',skipped:'건너뜀'};
  const stt=STMAP[h.status]||h.status||'';
  const ttl=esc(h.title||'(제목없음)');
  const cell=h.result_url?`<a href="${esc(h.result_url)}" target="_blank" rel="noopener" title="${esc(h.result_url)}" style="color:var(--p);text-decoration:none">🔗 ${ttl}</a>`:ttl;
  const av=h.alive==='no'?' <span style="color:var(--r)">✕삭제</span>':'';
  return `<tr><td style="color:var(--d);white-space:nowrap;font-size:11px">${esc((h.time||'').slice(5,16))}</td><td style="white-space:nowrap"><span class="st st-${stc}">${esc(stt)}</span>${av}</td><td style="max-width:0;width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${ttl}">${cell}</td></tr>`}
function _histTable(rows){return '<table style="table-layout:fixed;width:100%"><thead><tr><th style="width:78px">시간</th><th style="width:66px">상태</th><th>제목(클릭시 글로 이동)</th></tr></thead><tbody>'+rows.map(histRowC).join('')+'</tbody></table>';}
async function renderHistory(force){const h=await api('/history','GET');if(!Array.isArray(h))return;$('histCount').textContent=h.length+'건';
if(!h.length){$('histList').innerHTML='<p style="color:var(--d);padding:30px;text-align:center">아직 발행 이력이 없습니다</p>';return}
// ★날짜별 보기(대표님 지시 2026-09-10): 날짜 드롭다운 채우고, 선택 날짜만 필터. force일 때만 옵션 갱신.
const dsel=$('histDate');
const dates=[...new Set(h.map(x=>String(x.time||'').slice(0,10)).filter(Boolean))].sort().reverse();
if(dsel && (force || !dsel.dataset.filled)){const keep=dsel.value;dsel.innerHTML='<option value="">전체 날짜</option>'+dates.map(d=>'<option value="'+d+'">'+d+'</option>').join('');if(dates.includes(keep))dsel.value=keep;dsel.dataset.filled='1'}
const pick=dsel?dsel.value:'';
const hf=pick?h.filter(x=>String(x.time||'').slice(0,10)===pick):h;
// ★스크롤 튐 방지(대표님 '내리면 다시 올라감'): 갱신 전 각 섹션 스크롤 위치 저장→갱신 후 복원.
const scroll={};document.querySelectorAll('#histList [data-wn]').forEach(el=>{scroll[el.getAttribute('data-wn')]=el.scrollTop});
// 작업실별 그룹핑(순서: 이력에 먼저 등장한 작업실 순)
const groups={},order=[];
hf.forEach(x=>{const wn=x.workroom_name||'직접 입력';if(!(wn in groups)){groups[wn]=[];order.push(wn)}groups[wn].push(x)});
// 반응형: 넓으면 다열, 좁으면(모바일) 1열.
let cells='';
for(const wn of order){const rows=groups[wn];
  cells+=`<div style="border:1px solid #33425f;border-radius:8px;overflow:hidden"><div style="font-weight:700;color:var(--p);padding:6px 10px;background:#0f1830;border-bottom:1px solid #33425f">📂 ${esc(wn)} <span style="color:var(--d);font-weight:400;font-size:11px">${rows.length}건</span></div><div data-wn="${esc(wn)}" style="max-height:360px;overflow:auto">`+_histTable(rows)+'</div></div>';
}
$('histList').innerHTML=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px">${cells}</div>`;
// 스크롤 위치 복원(자동 갱신 시 보던 위치 유지)
document.querySelectorAll('#histList [data-wn]').forEach(el=>{const s=scroll[el.getAttribute('data-wn')];if(s)el.scrollTop=s})}
async function submitCaptcha(id){const el=$('cap_'+id);const value=(el&&el.value||'').trim();if(!value){toast('CAPTCHA 값을 입력하세요','er');return}const r=await api('/manual-checks/'+id+'/submit','POST',{value});if(r&&r.ok){toast('입력 완료 · 자동 발행을 계속합니다');renderCaptchaTasks()}else toast((r&&r.error)||'전달 실패','er')}
async function cancelCaptcha(id){const r=await api('/manual-checks/'+id+'/cancel','POST',{});if(r&&r.ok){toast('CAPTCHA 작업 취소됨');renderCaptchaTasks()}}
async function renderCaptchaTasks(){const box=$('captchaTasks');if(!box)return;const r=await api('/manual-checks','GET');if(!r||!r.ok)return;const waiting=(r.tasks||[]).filter(t=>['waiting_input','input_received','submitting'].includes(t.status));if(!waiting.length){box.innerHTML='';return}box.innerHTML=waiting.map(t=>'<div class="card" style="border-color:#a16207;background:#171205"><h3 style="color:var(--y)">🧩 '+esc(t.site_name)+' · CAPTCHA 입력 후 자동 발행</h3><div class="row">'+(t.image_data?'<img src="'+t.image_data+'" alt="CAPTCHA" style="max-width:240px;max-height:90px;background:#fff;border-radius:4px;padding:4px">':'<span style="color:var(--y)">이미지 캡처 실패 — 사이트 화면에서 CAPTCHA를 확인하세요.</span>')+'<input id="cap_'+esc(t.id)+'" autocomplete="off" placeholder="보이는 문자를 직접 입력" style="width:220px" '+(t.status!=='waiting_input'?'disabled':'')+'><button class="btn btn-g" onclick="submitCaptcha(\''+esc(t.id)+'\')" '+(t.status!=='waiting_input'?'disabled':'')+'>입력 후 자동 발행</button><button class="btn btn-r btn-xs" onclick="cancelCaptcha(\''+esc(t.id)+'\')">취소</button></div><div style="color:var(--d);font-size:10px;margin-top:6px">'+esc(t.message||'')+' · 만료 '+esc(t.expires_at||'')+'</div></div>').join('')}
async function renderWorkerLog(){const roomSel=$('wlogRoom');if(!roomSel.dataset.loaded){const rooms=await api('/workrooms','GET');if(Array.isArray(rooms)){roomSel.innerHTML='<option value="">전체 작업실</option>'+rooms.map(r=>'<option value="'+esc(r.id)+'">'+esc(r.name)+'</option>').join('');roomSel.dataset.loaded='1'}}const rid=roomSel.value;const r=await api('/worker-log'+(rid?'?workroom_id='+encodeURIComponent(rid):''),'GET');if(!r||!r.ok)return;const w=r.workers||{};$('wlogWorker').textContent='워커 '+(w.active?(w.paused?'일시정지':'실행 중'):'정지')+' · 큐 '+(w.queued||0)+' · 성공 '+(w.success||0)+' · 실패 '+(w.fail||0)+' · 스킵 '+(w.skipped||0);$('wlogBlock').innerHTML=r.publishable_count?'<div class="note" style="border-color:#166534;color:var(--g)">발행 가능 검증 사이트 '+r.publishable_count+'곳</div>':'<div class="note" style="border-color:#991b1b;color:var(--r)">⛔ 현재 발행 가능 사이트 0곳'+((r.captcha_sites||[]).length?' · CAPTCHA 감지: '+esc(r.captcha_sites.join(', ')):'')+' — CAPTCHA를 우회하지 않으며 사람이 처리하고 실게시 재검증하기 전까지 자동 발행하지 않습니다.</div>';const sm={preparing:'준비',running:'글 생성 중',done:'준비 완료',failed:'준비 실패'};$('wlogTasks').innerHTML=(r.tasks||[]).length?'<table><thead><tr><th>작업실</th><th>시작</th><th>준비 진행</th><th>큐 등록</th><th>대기 필요</th><th>상태</th></tr></thead><tbody>'+r.tasks.map(t=>'<tr><td><b>'+esc(t.workroom_name||'직접 입력')+'</b></td><td>'+esc(t.created_at||'')+'</td><td>'+esc(t.done||0)+'/'+esc(t.total||0)+'</td><td>'+esc(t.queued||0)+'</td><td>'+esc(t.remaining||0)+'</td><td><span class="st st-'+(t.status==='done'?'ok':t.status==='failed'?'f':'y')+'">'+esc(sm[t.status]||t.status||'')+'</span> '+esc(t.error||'')+'</td></tr>').join('')+'</tbody></table>':'<p style="color:var(--d);padding:12px">선택한 작업실의 준비 작업이 없습니다.</p>';
// ★작업실별 발행이력 표(wlogList) 제거(대표님 지시 2026-09-09): '결과' 탭과 중복 → 관제실만 유지.
//   전체 발행이력·제목링크·발행링크는 '결과' 탭(renderHistory)에서 확인.
window._actLog=r.activity||[];window._nodes=r.nodes||[];window._nodeNow=r.now||0;renderActivity();renderNodeStrip()}
function linkifyLog(msg){
  // esc로 XSS 방지 후, 텍스트 내 http(s) URL을 클릭 가능한 링크로 변환
  var e=esc(msg||'');
  return e.replace(/(https?:\/\/[^\s"'<>]+)/g, function(u){
    return '<a href="'+u+'" target="_blank" rel="noopener" style="color:var(--p);word-break:break-all">'+u+'</a>';
  });
}
// ★관제실 렌더(대표님 지시): 워커(발행)+러너(발굴/검수/가입/정리) 각 구획에 동시에 쭉 흐르게.
//  파이프라인·기타 카테고리는 러너 성격이라 검수 구획에 합류.
function renderActivity(){
  const logs=(window._actLog||[]);
  if(!$('col발행'))return;   // 관제 그리드 없으면(구버전) 스킵
  const buckets={'발행':[],'발굴':[],'검수':[],'가입':[],'정리':[]};
  logs.forEach(x=>{let c=x.cat||'기타';
    if(c==='파이프라인'||c==='기타')c='검수';
    if(buckets[c])buckets[c].push(x);});
  const time=x=>esc((x.time||'').slice(-8));   // HH:MM:SS
  function fill(cat){const el=$('col'+cat);if(!el)return;const arr=buckets[cat]||[];
    const cn=$('cnt'+cat);if(cn)cn.textContent=arr.length?arr.length+'건':'';
    el.innerHTML=arr.length?arr.map(x=>'<div title="'+esc(x.msg||'')+'" style="padding:4px 11px;border-bottom:1px solid #1c2740;font-size:12.5px;line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"><span style="color:var(--d)">'+time(x)+'</span> '+linkifyLog(x.msg||'')+'</div>').join(''):'<div style="padding:16px;text-align:center;color:var(--d);font-size:12px">대기 중…</div>';}
  ['발행','발굴','검수','가입','정리'].forEach(fill);
  const cc=$('actCounts');if(cc)cc.textContent='발행 '+buckets['발행'].length+' · 발굴 '+buckets['발굴'].length+' · 검수 '+buckets['검수'].length+' · 가입 '+buckets['가입'].length+' · 정리 '+buckets['정리'].length;
}
// ★기기별 실시간 현황판(대표님 지시 2026-09-11): PC/노트북 각각 마지막활동·발행·상태 카드.
function _nodeLabel(id){id=(id||'').toLowerCase();
  if(id.indexOf('kang')>=0)return '🖥️ 내 PC';
  if(id.indexOf('desktop')>=0||id.indexOf('note')>=0||id.indexOf('laptop')>=0)return '💻 노트북';
  return '🖥️ '+id;}
function _ago(sec){sec=Math.max(0,Math.round(sec));
  if(sec<60)return sec+'초 전';
  if(sec<3600)return Math.floor(sec/60)+'분 '+(sec%60)+'초 전';
  return Math.floor(sec/3600)+'시간 전';}
function renderNodeStrip(){const box=$('nodeStrip');if(!box)return;
  const nodes=window._nodes||[];const now=window._nodeNow||(Date.now()/1000);
  if(!nodes.length){box.innerHTML='<div style="grid-column:1/3;padding:10px;text-align:center;color:var(--d);font-size:12px;border:1px dashed #33425f;border-radius:8px">아직 연결된 발행노드가 없습니다 — PC/노트북에서 pc_node.py 실행 시 여기에 표시됩니다</div>';return}
  box.innerHTML=nodes.map(n=>{
    // ★3단계(대표님 스샷 2026-09-11 '노트북 끊김 1분43초'): 노드는 claim·회신 때만 beat라 4곳 동시발행 배치(2~4분)
    //   동안 beat가 없음 → 90초 기준이면 정상 작업을 '끊김'으로 오표시. 2분 내 활성 / 7분 내 작업중 / 그 이후 끊김.
    const age=now-(n.last||0);const state=age<120?'live':(age<420?'busy':'dead');
    const dot=state==='live'?'<span style="color:var(--g)">🟢 활성</span>':(state==='busy'?'<span style="color:var(--y)">🟡 작업 중('+_ago(age)+')</span>':'<span style="color:var(--r)">🔴 끊김('+_ago(age)+')</span>');
    const brd=state==='dead'?'#7f1d1d':(state==='busy'?'#713f12':'#166534');const bg=state==='dead'?'#2a0d0d':(state==='busy'?'#2a1a0a':'#0d2a17');
    return '<div style="border:1px solid '+brd+';border-radius:8px;background:'+bg+';padding:8px 11px">'
      +'<div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:13px">'+esc(_nodeLabel(n.node_id))+'</b><span style="font-size:11px">'+dot+'</span></div>'
      +'<div style="font-size:11.5px;color:var(--t);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="'+esc(n.action||'')+'">'+esc(n.action||'대기 중')+'</div>'
      +'<div style="font-size:10.5px;color:var(--d);margin-top:4px">마지막 '+_ago(age)+' · 발행 '+(n.publish||0)+'건 · 발굴 '+(n.discover||0)+'건</div>'
      +'</div>';}).join('');
}
let _editId=null;
async function runDiag(){$('diagOut').innerHTML='<p style="color:var(--d);padding:14px">🩺 진단 중... 크롬을 실제로 띄워보는 중이라 최대 60초 걸립니다.</p>';const r=await api('/diag','GET');if(!r){$('diagOut').innerHTML='<p style="color:var(--r)">진단 실패</p>';return}
const rows=(r.steps||[]).map(s=>`<tr><td>${s.ok?'<span style="color:var(--g)">✅</span>':'<span style="color:var(--r)">❌</span>'}</td><td><b>${esc(s.name)}</b></td><td style="color:var(--d)">${esc(s.detail)}</td></tr>`).join('');
const hdr=r.ok?'<span style="color:var(--g)">✅ 발행 가능 — 크롬 정상 작동</span>':'<span style="color:var(--r)">❌ 발행 불가 — 아래 ❌ 항목 확인</span>';
$('diagOut').innerHTML=`<div style="margin-bottom:8px;font-weight:700">${hdr}</div><table>${rows}</table><div style="font-size:10px;color:var(--d);margin-top:8px">${esc(r.platform||'')} · Python ${esc(r.python||'')}</div>`}
// ---- 도메인 발굴 ----
let _cands=[];
async function renderCands(){const r=await api('/candidates','GET');if(!r||!r.candidates)return;_cands=r.candidates;const s=r.summary||{};
$('dcSummary').innerHTML=tile('전체',s.total||0,'var(--t)')+tile('검수완료',s.ready||0,'var(--g)')+tile('사이트 등록',s.approved||0,'var(--p)')+tile('제외',s.rejected||0,'var(--r)')+tile('오늘 쿼리',(s.today_queries||0)+'/100','var(--v)');
const f=$('dcFilter').value;const list=f?_cands.filter(c=>c.status===f):_cands;
$('dcCount').textContent=list.length+'개';
if(!list.length){$('dcList').innerHTML='<p style="color:var(--d);padding:30px;text-align:center">후보가 없습니다. Brave 키워드 검색을 실행하거나 URL을 직접 추가하세요.</p>';return}
$('dcList').innerHTML='<table><thead><tr><th>점수</th><th>도메인/게시판</th><th>판정</th><th>상태</th><th>동작</th></tr></thead><tbody>'+list.map(c=>{
const sc=c.score||0;const scc=sc>=50?'var(--g)':(sc>=20?'var(--y)':'var(--r)');
const flags=[];
if(c.promo_hint)flags.push('<span class="st st-ok">홍보허용흔적</span>');
if(c.parked)flags.push('<span class="st st-f">주차도메인</span>');
if(c.illegal)flags.push('<span class="st st-f">도박·불법</span>');
if(c.ad_banned)flags.push('<span class="st st-f">광고금지</span>');
if(c.captcha)flags.push('<span class="st st-y">🧩캡차</span>');
if(c.login_required)flags.push('<span class="st st-i">로그인필요</span>');
if(c.write_form)flags.push('<span class="st st-ok">글쓰기폼</span>');
if(c.last_post_days!=null)flags.push('<span class="st st-i">최근글 '+c.last_post_days+'일</span>');
const stmap={ready:'<span class="st st-ok">검수완료</span>',new:'<span class="st st-i">미검수</span>',approved:'<span class="st st-ok">사이트등록</span>',rejected:'<span class="st st-f">제외</span>'};
// 완전자동: 수동 버튼 대신 자동 처리 상태만 표시(되는 곳 자동등록·안 되는 곳 자동탈락).
const acts=(c.status==='approved')?'<span style="color:var(--g);font-size:10px">✓ 자동 등록됨</span>'
 :(c.status==='rejected')?'<span style="color:var(--r);font-size:10px">자동 탈락(재발굴 제외)</span>'
 :(c.status==='ready')?'<span style="color:var(--p);font-size:10px">🤖 자동 가입·발행 대기</span>'
 :'<span style="color:var(--d);font-size:10px">검수 중…</span>';
return '<tr><td style="color:'+scc+';font-weight:700;font-size:15px">'+sc+'</td>'+
'<td><a href="'+esc(c.url)+'" target="_blank" style="color:var(--p)"><b>'+esc(c.domain||'')+'</b></a><br><span style="color:var(--d);font-size:10px">'+esc((c.board_name||c.title||'').slice(0,44))+'</span></td>'+
'<td style="max-width:230px">'+flags.join(' ')+(c.reject_reason?'<br><span style="color:var(--r);font-size:10px">'+esc(c.reject_reason)+'</span>':'')+'</td>'+
'<td>'+(stmap[c.status]||'')+'</td><td style="white-space:nowrap">'+acts+'</td></tr>'}).join('')+'</tbody></table>'}
async function discoverNow(){toast('🔎 Brave 키워드 검색 중...(약 10초)');const r=await api('/candidates/discover','POST',{queries:5});if(r&&r.ok){toast('신규 '+r.added+'개 · 검수 '+r.screened+'건 (오늘 검색 '+r.today_queries+'회)');renderCands()}else toast((r&&r.error)||'실패','er')}
let _regionData=null;
async function loadRegionTool(){if(!_regionData){const r=await api('/regions','GET');if(!r||r.error){toast((r&&r.error)||'행정구역 로드 실패','er');return null}_regionData=r}['rgProvince','wrProvince'].forEach(id=>{const s=$(id);if(!s||s.dataset.loaded)return;Object.keys(_regionData).forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o)});s.dataset.loaded='1'});return _regionData}
// ★지역범위 구단위 선택(대표님 지시 2026-09-09): 시·도 고르면 그 안의 시·군·구 목록을 채운다.
//   특정 구 선택 시 그 구(와 하위 동)만 생성. 비우면 시·도 전체(기존 동작).
async function fillGuSel(provId,guId){const data=await loadRegionTool();const gs=$(guId);if(!gs)return;const prov=$(provId).value;gs.innerHTML='<option value="">시·군·구 전체</option>';if(!prov||!data||!data[prov])return;Object.keys(data[prov]).forEach(dist=>{const o=document.createElement('option');o.value=dist;o.textContent=dist;gs.appendChild(o)})}
function shortProvince(x){return x.replace(/특별자치시$|특별자치도$|특별시$|광역시$|자치도$|도$/,'')}
function shortDistrict(x){const last=x.trim().split(/\s+/).pop();return last.replace(/시$|군$|구$/,'')}
// 읍·면·동 축약: 사람들이 실제로 검색하는 형태로(청라동→청라, 역삼1동→역삼).
// 단 남는 글자가 1자면(명동→명) 어색하므로 원형 유지. 지하철'역'은 여기서 다루지 않음.
function shortDong(x){const s=(x||'').trim().replace(/\d+가$/,'').replace(/\d*(동|읍|면)$/,'');return s.length>=2?s:(x||'').trim()}
// 대한민국 지하철역(업소 밀집 주요역 중심, 시도 키는 regions_full.json과 일치). '역' 포함 표기.
const _stationsByProvince={
"서울특별시":["서울역","시청역","종각역","종로3가역","종로5가역","동대문역","청량리역","제기동역","신설동역","동묘앞역","회기역","을지로입구역","을지로3가역","을지로4가역","동대문역사문화공원역","신당역","왕십리역","한양대역","뚝섬역","성수역","건대입구역","구의역","강변역","잠실나루역","잠실역","잠실새내역","종합운동장역","삼성역","선릉역","역삼역","강남역","교대역","서초역","방배역","사당역","낙성대역","서울대입구역","봉천역","신림역","신대방역","구로디지털단지역","대림역","신도림역","문래역","영등포구청역","당산역","합정역","홍대입구역","신촌역","이대역","아현역","충정로역","구파발역","연신내역","불광역","홍제역","독립문역","경복궁역","안국역","충무로역","동대입구역","약수역","금호역","옥수역","압구정역","신사역","고속터미널역","남부터미널역","양재역","매봉역","도곡역","대치역","학여울역","일원역","수서역","가락시장역","오금역","노원역","창동역","쌍문역","수유역","미아역","미아사거리역","길음역","성신여대입구역","한성대입구역","혜화역","명동역","회현역","숙대입구역","삼각지역","신용산역","이촌역","동작역","이수역","김포공항역","발산역","화곡역","까치산역","목동역","오목교역","여의도역","여의나루역","마포역","공덕역","서대문역","광화문역","군자역","아차산역","광나루역","천호역","강동역","고덕역","상일동역","둔촌동역","올림픽공원역","방이역","마천역","응암역","디지털미디어시티역","월드컵경기장역","망원역","상수역","대흥역","효창공원앞역","녹사평역","이태원역","한강진역","보문역","안암역","고려대역","월곡역","석계역","태릉입구역","화랑대역","봉화산역","도봉산역","수락산역","마들역","중계역","하계역","공릉역","먹골역","중화역","상봉역","면목역","사가정역","용마산역","중곡역","어린이대공원역","청담역","강남구청역","학동역","논현역","반포역","내방역","남성역","숭실대입구역","상도역","장승배기역","보라매역","신풍역","남구로역","가산디지털단지역","온수역","암사역","석촌역","송파역","문정역","장지역","신논현역","언주역","선정릉역","삼성중앙역","봉은사역","노량진역","흑석역","신반포역","중앙보훈병원역","서울숲역","한티역","개포동역"],
"경기도":["판교역","정자역","미금역","수지구청역","광교역","광교중앙역","야탑역","서현역","수내역","오리역","죽전역","기흥역","영통역","수원역","수원시청역","매교역","성남역","모란역","가천대역","복정역","산성역","단대오거리역","신흥역","부천역","중동역","송내역","상동역","부평역","산본역","범계역","평촌역","인덕원역","안양역","금정역","군포역","의왕역","고양시청역","화정역","대곡역","백석역","마두역","정발산역","주엽역","대화역","의정부역","회룡역","망월사역","안산역","중앙역","고잔역","초지역","상록수역","한대앞역","광명사거리역","철산역","광명역","별내역","다산역","동탄역"],
"인천광역시":["인천역","동인천역","주안역","제물포역","도원역","부평역","동암역","간석역","송내역","계양역","작전역","경인교대입구역","부평구청역","부평시장역","인천시청역","예술회관역","인천터미널역","문학경기장역","송도역","캠퍼스타운역","테크노파크역","센트럴파크역","국제업무지구역","지식정보단지역","인하대역","숭의역","신포역","원인재역","연수역"],
"부산광역시":["부산역","서면역","남포역","자갈치역","중앙역","부산진역","동래역","연산역","교대역","부산대역","온천장역","명륜역","범어사역","노포역","사상역","하단역","괴정역","대티역","서대신역","동대신역","토성역","범내골역","범일역","좌천역","수정역","초량역","해운대역","장산역","중동역","벡스코역","센텀시티역","민락역","광안역","금련산역","남천역","경성대부경대역","대연역","못골역","지게골역","문현역","전포역","국제금융센터부산은행역","화명역","덕천역","구포역","수영역","망미역"],
"대구광역시":["대구역","중앙로역","반월당역","동대구역","신천역","동구청역","아양교역","해안역","방촌역","용계역","율하역","안심역","성당못역","대명역","안지랑역","현충로역","영대병원역","교대역","명덕역","남산역","서문시장역","청라언덕역","반고개역","내당역","두류역","감삼역","죽전역","용산역","이곡역","성서산업단지역","계명대역","강창역","칠곡경대병원역","팔거역","동천역","화명역","수성구청역","범어역","만촌역","담티역","연호역","고산역"],
"대전광역시":["대전역","중앙로역","중구청역","서대전네거리역","오룡역","용문역","탄방역","시청역","정부청사역","갈마역","월평역","갑천역","유성온천역","구암역","현충원역","월드컵경기장역","노은역","지족역","반석역","판암역","신흥역","대동역"],
"광주광역시":["광주송정역","송정공원역","도산역","공항역","김대중컨벤션센터역","상무역","운천역","돌고개역","농성역","화정역","쌍촌역","금남로4가역","금남로5가역","문화전당역","남광주역","학동증심사입구역","소태역","녹동역","평동역"]
};
let _workrooms=[];
// ★작업실 조합 수 계산(대표님 '새 작업실 안 들어옴' 2026-09-09): 조합 0이면 발행풀에 안 잡힘 → 셀렉터에 표시.
function _wrComboCount(x){return String(x.keyword_csv||'').split(/\r?\n/).map(s=>s.trim()).filter(s=>s&&!s.startsWith('#')).length}
async function loadWorkrooms(){const r=await api('/workrooms','GET');if(!Array.isArray(r))return;_workrooms=r;const s=$('wrSelect');const keep=s.value;s.innerHTML='<option value="">작업실 선택</option>'+r.map(x=>{const n=_wrComboCount(x);const tag=n?(' ('+n+'조합)'):' (⚠비어있음)';return '<option value="'+esc(x.id)+'">'+esc(x.name)+tag+'</option>'}).join('');if(r.some(x=>x.id===keep))s.value=keep;else if(r.length)s.value=r[0].id;showWorkroom();if(typeof loadImgWorkrooms==='function')loadImgWorkrooms()}
function showWorkroom(){const r=_workrooms.find(x=>x.id===$('wrSelect').value);$('wrName').value=r?r.name:'';$('wrKeywords').value=r?r.keyword_csv:'';if($('wrBases'))$('wrBases').value=(r&&r.bases)?r.bases:'';if($('wrWriter'))$('wrWriter').value=(r&&r.writer_name)?r.writer_name:'';$('wrSite').value=r?r.site_id:'';
  // ★조합 0개면 발행풀에 안 잡힌다는 걸 명확히 경고(대표님 '새 작업실 안 들어옴').
  const el=$('wrSaved');if(!el){return}
  if(r){const n=_wrComboCount(r);el.innerHTML=n?('저장 '+(r.updated_at||'')+' · '+n+'조합'):'<span style="color:var(--r)">⚠ 조합 0개 — 아래 키워드 넣고 \'생성+저장\'을 눌러야 발행됩니다</span>'}
  else el.textContent=''}
async function newWorkroom(){const name=(prompt('새 작업실 이름','작업실'+(_workrooms.length+1))||'').trim();if(!name)return;const r=await api('/workrooms','POST',{name:name,keyword_csv:'',site_id:'',bases:''});if(r&&r.ok){await loadWorkrooms();$('wrSelect').value=r.id;showWorkroom();toast(name+' 추가됨','ok')}}
async function saveWorkroom(){const id=$('wrSelect').value;if(!id){toast('먼저 작업실을 추가하세요','er');return}const r=await api('/workrooms','POST',{id:id,name:$('wrName').value.trim(),keyword_csv:$('wrKeywords').value,site_id:$('wrSite').value,bases:($('wrBases')?$('wrBases').value:''),writer_name:($('wrWriter')?$('wrWriter').value:'')});if(r&&r.ok){toast('작업실 저장 완료 · '+r.count+'개 조합','ok');await loadWorkrooms();$('wrSelect').value=id;showWorkroom()}else toast((r&&r.error)||'저장 실패','er')}
async function deleteWorkroom(){const id=$('wrSelect').value;if(!id)return;if(!confirm('선택한 작업실과 키워드 목록을 삭제할까요?'))return;await api('/workrooms','DELETE',{id:id});await loadWorkrooms();toast('작업실 삭제됨')}
function workroomProvinceRank(p){const x=shortProvince(p);if(x==='인천')return 0;if(x==='경기')return 1;if(x==='서울')return 2;if(x==='충청남'||x==='충남'||x==='대전')return 3;if(x==='충청북'||x==='충북')return 4;if(x==='세종')return 5;if(x==='전북'||x==='전라북')return 6;if(x==='전남'||x==='전라남'||x==='광주')return 7;if(['부산','대구','울산','경남','경상남'].includes(x))return 8;if(x==='경북'||x==='경상북')return 9;if(x==='강원')return 10;if(x==='제주')return 11;return 99}
function randomThree(items){const a=[...new Set(items)];for(let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]]}return a.slice(0,3)}
async function makeWorkroomRegional(){const data=await loadRegionTool();if(!data)return[];const bases=[...new Set($('wrBases').value.split(/\r?\n/).map(x=>x.trim()).filter(x=>x&&!x.startsWith('#')))];if(bases.length<3){toast('서로 다른 키워드를 한 줄에 하나씩 최소 3개 입력하세요','er');return[]}const only=$('wrProvince').value,onlyGu=($('wrGuSel')&&$('wrGuSel').value)||'',join=$('wrJoin').value,regions=[];Object.entries(data).sort((a,b)=>workroomProvinceRank(a[0])-workroomProvinceRank(b[0])).forEach(([province,districts])=>{if(only&&province!==only)return;if($('wrCity').checked&&!onlyGu)regions.push(shortProvince(province));Object.entries(districts||{}).forEach(([district,dongs])=>{if(onlyGu&&district!==onlyGu)return;if($('wrGu').checked)regions.push(shortDistrict(district));if($('wrDong').checked)(dongs||[]).forEach(d=>regions.push(shortDong(d)))})});if(!onlyGu&&$('wrStation')&&$('wrStation').checked){Object.entries(_stationsByProvince).forEach(([prov,sts])=>{if(only&&prov!==only)return;(sts||[]).forEach(st=>regions.push(st))})}const uniqReg=[...new Set(regions.filter(Boolean))];const out=[];uniqReg.forEach(region=>{const picked=randomThree(bases);out.push(picked.map(base=>region+join+base).join(','))});return out}
async function previewWorkroomRegional(){const rows=await makeWorkroomRegional();$('wrRegionCount').textContent=rows.length.toLocaleString()+'개 생성 예정'}
async function applyWorkroomRegional(replace){const id=$('wrSelect').value;if(!id){toast('먼저 작업실을 추가/선택하세요','er');return}const rows=await makeWorkroomRegional();if(!rows.length)return;if(rows.length>50000){toast('5만 개를 초과합니다. 지역 범위를 줄여주세요','er');return}const current=replace?[]:$('wrKeywords').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);const merged=[...new Set(current.concat(rows))];$('wrKeywords').value=merged.join('\n');$('wrRegionCount').textContent='저장 중...';const r=await api('/workrooms','POST',{id:id,name:$('wrName').value.trim(),keyword_csv:merged.join('\n'),site_id:$('wrSite').value,bases:($('wrBases')?$('wrBases').value:''),writer_name:($('wrWriter')?$('wrWriter').value:'')});if(r&&r.ok){$('wrRegionCount').textContent=rows.length.toLocaleString()+'개 생성 · 자동저장됨 총 '+merged.length.toLocaleString()+'개';toast('생성+작업실 자동저장 완료 · '+merged.length.toLocaleString()+'개 조합','ok');await loadWorkrooms();$('wrSelect').value=id;showWorkroom()}else{$('wrRegionCount').textContent=rows.length.toLocaleString()+'개 생성(저장 실패)';toast((r&&r.error)||'자동저장 실패 — 작업실 저장 버튼을 눌러주세요','er')}}
function copyWorkroomToBulk(){const rows=$('wrKeywords').value.trim();if(!rows){toast('작업실 키워드가 없습니다','er');return}const room=_workrooms.find(x=>x.id===$('wrSelect').value);$('kwlist').value=rows;$('kwlist').dataset.workroomId=room?room.id:'';$('kwlist').dataset.workroomName=room?room.name:'직접 입력';$('kwSiteFilter').value=$('wrSite').value;$('kwCount').textContent=(room?'['+room.name+'] ':'')+rows.split(/\r?\n/).filter(Boolean).length+'줄';toast((room?'['+room.name+'] ':'')+'발행 목록에 적용됨','ok');$('kwlist').scrollIntoView({behavior:'smooth',block:'center'})}
async function makeRegionalKeywords(){const data=await loadRegionTool();if(!data)return[];const bases=$('rgKeywords').value.split(/\r?\n/).map(x=>x.trim()).filter(x=>x&&!x.startsWith('#'));if(!bases.length){toast('조합할 키워드를 한 줄에 하나씩 입력하세요','er');return[]}const only=$('rgProvince').value;const onlyGu=($('rgGuSel')&&$('rgGuSel').value)||'';const join=$('rgJoin').value;const regions=[];Object.entries(data).forEach(([province,districts])=>{if(only&&province!==only)return;if($('rgCity').checked&&!onlyGu)regions.push(shortProvince(province));Object.entries(districts||{}).forEach(([district,dongs])=>{if(onlyGu&&district!==onlyGu)return;if($('rgGu').checked)regions.push(shortDistrict(district));if($('rgDong').checked)(dongs||[]).forEach(d=>regions.push(d))})});const out=[];const seen=new Set();regions.forEach(region=>bases.forEach(base=>{const q=region+join+base;if(!seen.has(q)){seen.add(q);out.push(q)}}));return out}
async function previewRegionalKeywords(){const rows=await makeRegionalKeywords();$('rgCount').textContent=rows.length.toLocaleString()+'개 생성 예정'}
async function applyRegionalKeywords(replace){const rows=await makeRegionalKeywords();if(!rows.length)return;if(rows.length>50000){toast('5만 개를 초과합니다. 지역 또는 단계를 줄여주세요','er');return}const current=replace?[]:$('cDDirect').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);const merged=[...new Set(current.concat(rows))];$('cDDirect').value=merged.join('\n');$('rgCount').textContent=rows.length.toLocaleString()+'개 생성 · 전체 '+merged.length.toLocaleString()+'개';toast('목록에 반영됨 · 설정 저장을 눌러주세요','ok')}
async function screenNow(){toast('검수 중...(최대 1분)');const r=await api('/candidates/screen','POST',{limit:20});if(r&&r.ok){toast(r.screened+'건 검수 완료');renderCands()}}
async function setupKaraokeSchedule(){
  const btn=document.getElementById('karaokeSchedBtn');
  const st=document.getElementById('karStatus');
  const time=(document.getElementById('karTime')||{}).value||'10:00';
  const count=Math.max(1,parseInt((document.getElementById('karCount')||{}).value||'1',10)||1);
  if(btn){btn.disabled=true;btn.textContent='🎤 설정 중...';}
  if(st)st.textContent='';
  try{
    const r=await api('/schedules/karaoke-oneclick','POST',{times:[time],count:count});
    if(r&&r.ok){
      if(r.schedule===null||r.warning){
        toast(r.warning||'키워드만 저장됨','er');
        if(st)st.textContent='키워드 '+(r.keywords||0)+'개 저장 · '+(r.warning||'');
      }else{
        toast('🎤 노래방 스케줄 켜짐 · 키워드 '+r.keywords+'개 · '+r.sites+'곳 매일 '+(r.times||[]).join(',')+' 발행');
        if(st)st.textContent='✅ 켜짐: 키워드 '+r.keywords+'개 · '+r.sites+'곳('+(r.site_names||[]).join(', ')+') 매일 '+(r.times||[]).join(',')+' 사이트당 '+r.count+'건';
      }
    }else{ toast((r&&r.error)||'설정 실패','er'); if(st)st.textContent=(r&&r.error)||'실패'; }
  }catch(e){ toast(e.message,'er'); if(st)st.textContent=e.message; }
  if(btn){btn.disabled=false;btn.textContent='🎤 노래방 스케줄 켜기 (검증사이트 자동발행)';}
}
async function runAllInOne(){
  // 올인원: Brave 발굴(게시판찾기) → 이어서 완전자동 파이프라인(가입→발행→등록)을 한 번에.
  const btn=document.getElementById('allInOneBtn');
  const logBox=document.getElementById('pipeLog');
  if(logBox){logBox.innerHTML='';logBox.style.display='block';}
  const pLog=(line,cls)=>{ if(!logBox)return; const color=cls==='ok'?'var(--g)':cls==='er'?'var(--r)':cls==='hi'?'var(--p)':'var(--d)';
    logBox.innerHTML+=`<div style="color:${color}">${line}</div>`; logBox.scrollTop=logBox.scrollHeight; };
  if(btn){btn.disabled=true;btn.textContent='⚡ 발굴 중...';}
  try{
    // 1단계: 발굴(게시판찾기 우선). 여러 번 돌려 후보를 넉넉히 쌓는다.
    pLog('▶ 1단계: Brave 게시판 발굴 시작','hi');
    toast('⚡ 올인원: 게시판 발굴 중...');
    let totalAdded=0;
    for(let round=1; round<=3; round++){
      const r=await api('/candidates/discover','POST',{queries:5});
      if(r&&r.ok){ totalAdded+=(r.added||0);
        pLog(`· 발굴 ${round}회차: 신규 ${r.added||0}개 · 검수 ${r.screened||0}건 (오늘 검색 ${r.today_queries||0}회)`, (r.added>0?'ok':''));
        if((r.today_queries||0)>=100){ pLog('· 오늘 검색 한도(100) 도달 — 발굴 중단','er'); break; }
      } else { pLog('· 발굴 오류: '+((r&&r.error)||''),'er'); break; }
    }
    pLog(`▶ 1단계 완료 — 신규 후보 ${totalAdded}개`, (totalAdded>0?'ok':'er'));
    if(typeof renderCands==='function') renderCands();
  }catch(e){ pLog('✖ 발굴 실패: '+e.message,'er'); }
  if(btn){btn.textContent='⚡ 발행 중...';}
  pLog('▶ 2단계: 완전 자동 파이프라인 (가입→발행→등록)','hi');
  // 2단계: 기존 파이프라인 실행부를 그대로 재사용(로그·상태폴링 포함)
  try{ await pipelineRun(); }
  catch(e){ pLog('✖ 파이프라인 실패: '+e.message,'er'); }
  if(btn){btn.disabled=false;btn.textContent='⚡ 올인원 실행 (발굴→가입→발행→등록)';}
  pLog('■ 올인원 종료','hi');
}
async function pipelineRun(){
  // 이 브라우저는 네이티브 confirm/prompt가 막혀 있으므로 사용하지 않는다.
  // 배치 개수는 입력칸(pipeBatch)에서 읽고, 실행 확인은 버튼 클릭 자체로 갈음.
  const el=document.getElementById('pipeBatch');
  const n=Math.max(1,parseInt((el&&el.value)||'1',10)||1);
  const btn=document.getElementById('pipeRunBtn');
  if(btn){btn.disabled=true;btn.textContent='🤖 실행 중...';}
  const restore=()=>{if(btn){btn.disabled=false;btn.textContent='🤖 완전 자동 파이프라인 실행 (가입→실발행→등록)';}};
  // 실행 로그 영역 준비
  const logBox=document.getElementById('pipeLog');
  const startTs=Date.now();
  function pLog(line,cls){ if(!logBox)return; logBox.style.display='block';
    const color=cls==='ok'?'var(--g)':cls==='er'?'var(--r)':cls==='hi'?'var(--p)':'var(--d)';
    logBox.innerHTML+=`<div style="color:${color}">${line}</div>`; logBox.scrollTop=logBox.scrollHeight; }
  if(logBox){logBox.innerHTML='';logBox.style.display='block';}
  pLog('▶ 파이프라인 시작 — 후보 '+n+'건 처리','hi');
  $('pipeStatus').textContent='시작 — 후보 '+n+'건 처리 중...';
  toast('🤖 파이프라인 시작 ('+n+'건)');
  let seen=0;  // 이미 표시한 로그 개수(중복 방지)
  async function pumpLogs(){
    try{
      const lr=await api('/logs?n=40','GET');
      if(lr&&lr.ok&&Array.isArray(lr.logs)){
        // 오래된→최신 순으로 뒤집고, 파이프라인/발행 관련만
        const rel=lr.logs.slice().reverse().filter(e=>/자동|파이프라인|발행|가입|등록|캡차|2captcha|탈락|스킵|성공|실패|재시도/.test(e.msg||''));
        for(let k=seen;k<rel.length;k++){ const e=rel[k];
          const cls=/성공|완료|등록|해결/.test(e.msg)?'ok':/실패|오류|탈락|거부|불가/.test(e.msg)?'er':'';
          pLog('· '+(e.time||'')+' '+(e.msg||''),cls); }
        seen=Math.max(seen,rel.length);
      }
    }catch(_){}
  }
  try{
    const r=await api('/pipeline/run','POST',{limit:n});
    if(!r||!r.ok){pLog('✖ 실행 실패: '+((r&&r.error)||''),'er');toast((r&&r.error)||'실행 실패','er');$('pipeStatus').textContent=(r&&r.error)||'실행 실패';return}
    $('pipeStatus').textContent='실행 중...';
    for(let i=0;i<120;i++){
      await new Promise(s=>setTimeout(s,2500));
      await pumpLogs();
      const st=await api('/pipeline/status','GET');
      if(st&&!st.running&&st.finished_at&&(new Date(st.finished_at.replace(' ','T')).getTime()>=startTs-3000||st.last_result)){
        await pumpLogs();
        const R=st.last_result||{};
        // 사이트별 결과 상세
        pLog('──────────','');
        (R.results||[]).forEach(x=>{
          if(x.ok) pLog('✅ 등록: '+(x.name||'').slice(0,40)+' → '+(x.url||''),'ok');
          else pLog('❌ '+(x.stage||'')+' 실패: '+(x.name||'').slice(0,30)+' — '+(x.msg||''),'er');
        });
        pLog('■ 완료 — 처리 '+(R.processed||0)+' · 자동가입 '+(R.signed_up||0)+' · 등록 '+(R.registered||0)+(R.dropped?' · 자동탈락 '+R.dropped:''),'hi');
        $('pipeStatus').textContent='완료: 처리 '+(R.processed||0)+' · 가입 '+(R.signed_up||0)+' · 등록 '+(R.registered||0);
        toast('파이프라인 완료 — 등록 '+(R.registered||0)+'개 / 처리 '+(R.processed||0)+'개', (R.registered>0?'ok':''));
        renderCands(); if(typeof renderSites==='function')renderSites();
        return;
      }
    }
    pLog('⏱ 시간 초과 — 백그라운드에서 계속됩니다','er');
    $('pipeStatus').textContent='시간 초과(백그라운드 계속)';
  }finally{ restore(); }
}
async function rescreenAll(){toast('전체 재검수 중...(최대 2분)');const r=await api('/candidates/screen','POST',{rescreen:true,limit:40});if(r&&r.ok){toast(r.screened+'건 재검수 완료');renderCands()}}
async function addManual(){const u=$('dcUrls').value;if(!u.trim()){toast('URL 입력','er');return}const r=await api('/candidates/manual','POST',{urls:u});if(r&&r.ok){toast(r.added+'개 추가 · 검수→발행테스트 바로 시작(1~2분 후 결과탭 확인)','ok');$('dcUrls').value='';setTimeout(renderCands,3000);renderCands()}else toast((r&&r.error)||'실패','er')}
async function setCand(id,st){await api('/candidates/status','POST',{id:id,status:st});renderCands()}
async function clearRejected(){await api('/candidates','DELETE',{clear:'rejected'});renderCands()}
async function approveCand(id){const c=_cands.find(x=>x.id===id);if(!c)return;
if(c.ad_banned&&!confirm('⚠️ 이 사이트는 "광고 금지" 문구가 감지되었습니다. 그래도 사이트 목록에 등록할까요?'))return;
const bo=prompt('게시판ID(bo_table)',c.bo_table||'free')||'free';
const mid=prompt('로그인 아이디 (없으면 비워두세요)','')||'';
const mpw=mid?(prompt('비밀번호','')||''):'';
const r=await api('/candidates/approve/'+id,'POST',{bo_table:bo,mb_id:mid,mb_pass:mpw});
if(r&&r.ok){toast('✅ 사이트 목록에 등록됨');renderCands();renderSites()}else toast((r&&r.error)||'실패','er')}
// ---- 회원·정산 ----
function won(n){return (n||0).toLocaleString()+'원'}
let _memEdit=null;
async function renderMembers(){const r=await api('/members','GET');if(!r||!r.members)return;const s=r.summary||{};
$('memSummary').innerHTML=tile('회원',s.members||0,'var(--t)')+tile('활성',s.active||0,'var(--g)')+tile('이번달 청구',won(s.billed),'var(--p)')+tile('납부완료',won(s.paid),'var(--g)')+tile('미납',won(s.unpaid),'var(--r)')+tile('미납 회원',s.unpaid_count||0,'var(--y)');
$('memCount').textContent=(r.members.length)+'명';
if(!r.members.length){$('memList').innerHTML='<p style="color:var(--d);padding:30px;text-align:center">등록된 회원이 없습니다</p>';return}
const DW=['월','화','수','목','금','토','일'];
$('memList').innerHTML='<table><thead><tr><th>이름/업소</th><th>상태</th><th>월청구</th><th>이번달</th><th>스케줄</th><th>최근실행</th><th>동작</th></tr></thead><tbody>'+r.members.map(m=>{
const st=m.status==='active'?'<span class="st st-ok">활성</span>':'<span class="st st-i">정지</span>';
const pay=m.paid?'<button class="btn btn-g btn-xs" onclick="togglePay(\''+esc(m.id)+'\',false)" title="'+esc(m.paid_at||'')+'">✔ 납부</button>':'<button class="btn btn-r btn-xs" onclick="togglePay(\''+esc(m.id)+'\',true)">미납</button>';
const addon=m.addons?(' <span style="color:var(--d);font-size:9px">+광고'+m.addons+'</span>'):'';
const times=(m.sched_times||[]);
const days=(m.sched_days&&m.sched_days.length)?m.sched_days.map(d=>DW[d]).join(''):'매일';
const sched=m.sched_enabled&&times.length
  ? '<span class="st st-ok">ON</span> <span style="color:var(--p);font-size:10px">'+esc(times.join(', '))+'</span><br><span style="color:var(--d);font-size:9px">'+days+' · '+(m.per_run||1)+'건/회 · ±'+(m.jitter==null?5:m.jitter)+'분'+((m.keywords||[]).length?' · 전용키워드'+m.keywords.length:'')+((m.site_ids||[]).length?' · 사이트'+m.site_ids.length:'')+'</span>'
  : '<span class="st st-i">OFF</span>';
return '<tr><td><b>'+esc(m.name||'')+'</b><br><span style="color:var(--d);font-size:10px">'+esc(m.biz||'')+' '+esc(m.phone||'')+'</span></td><td>'+st+'</td><td style="color:var(--p)">'+won(m.fee)+addon+'</td><td>'+pay+'</td><td>'+sched+'</td><td style="color:var(--d);font-size:10px">'+esc(m.last_run||'-')+(m.run_count?'<br>총 '+m.run_count+'회':'')+'</td><td><button class="btn btn-g btn-xs" onclick="runMember(\''+esc(m.id)+'\')" title="지금 1회 실행">실행</button> <button class="btn btn-p btn-xs" onclick=\'editMember('+JSON.stringify(m)+')\'>편집</button> <button class="btn btn-r btn-xs" onclick="delMember(\''+esc(m.id)+'\')">삭제</button></td></tr>'}).join('')+'</tbody></table>'}
function mDays(){return Array.from(document.querySelectorAll('.mDay:checked')).map(c=>parseInt(c.value))}
function mSiteIds(){return Array.from(document.querySelectorAll('.mSite:checked')).map(c=>c.dataset.id)}
async function fillSiteBox(sel){const sites=await api('/sites','GET');if(!Array.isArray(sites))return;const set=new Set(sel||[]);
$('mSiteBox').innerHTML=sites.map(s=>`<label style="display:flex;align-items:center;gap:3px"><input type="checkbox" class="mSite" data-id="${esc(s.id)}" style="width:auto" ${set.has(s.id)?'checked':''}>${esc(s.name||(s.site_url||'').slice(0,18))}${s.has_captcha?'🧩':''}</label>`).join('')}
async function addMember(){const d={name:$('mName').value.trim(),biz:$('mBiz').value.trim(),phone:$('mPhone').value.trim(),plan_fee:parseInt($('mFee').value)||0,addons:parseInt($('mAddons').value)||0,addon_fee:parseInt($('mAddonFee').value)||0,settle_day:parseInt($('mDay').value)||1,status:$('mStatus').value,memo:$('mMemo').value.trim(),
sched_enabled:$('mSchedOn').checked,sched_times:$('mTimes').value,sched_days:mDays(),site_ids:mSiteIds(),keywords_csv:$('mKw').value,jitter:parseInt($('mJitter').value)||0,per_run:parseInt($('mPerRun').value)||1};
if(!d.name&&!d.biz){toast('이름 또는 업소명 입력','er');return}if(_memEdit)d.id=_memEdit;const r=await api('/members','POST',d);if(r&&r.ok){toast(_memEdit?'수정됨':'회원 추가됨');cancelMember();renderMembers()}}
function editMember(m){_memEdit=m.id;$('mName').value=m.name||'';$('mBiz').value=m.biz||'';$('mPhone').value=m.phone||'';$('mFee').value=m.plan_fee||0;$('mAddons').value=m.addons||0;$('mAddonFee').value=m.addon_fee||0;$('mDay').value=m.settle_day||1;$('mStatus').value=m.status||'active';$('mMemo').value=m.memo||'';
$('mSchedOn').checked=!!m.sched_enabled;$('mTimes').value=(m.sched_times||[]).join(', ');$('mPerRun').value=m.per_run||1;$('mJitter').value=(m.jitter==null?5:m.jitter);
document.querySelectorAll('.mDay').forEach(c=>c.checked=(m.sched_days||[]).includes(parseInt(c.value)));
$('mKw').value=(m.keywords||[]).map(k=>[k.지역||'',k.서비스||'',k.브랜드||''].join(',')).join('\n');
fillSiteBox(m.site_ids||[]);
$('memBtn').textContent='수정 저장';$('memBtn').classList.add('btn-y');$('memCancel').style.display='';$('mName').scrollIntoView({behavior:'smooth',block:'center'})}
function cancelMember(){_memEdit=null;['mName','mBiz','mPhone','mMemo','mTimes','mKw'].forEach(i=>$(i).value='');$('mFee').value=30000;$('mAddons').value=0;$('mAddonFee').value=10000;$('mDay').value=1;$('mStatus').value='active';$('mSchedOn').checked=false;$('mPerRun').value=1;$('mJitter').value=5;document.querySelectorAll('.mDay').forEach(c=>c.checked=false);fillSiteBox([]);$('memBtn').textContent='회원 추가';$('memBtn').classList.remove('btn-y');$('memCancel').style.display='none'}
async function runMember(id){if(!confirm('이 회원의 스케줄을 지금 1회 실행합니다.\n배정 사이트에 실제로 발행됩니다. 진행할까요?'))return;toast('⏰ 실행중...');const r=await api('/members/run/'+id,'POST');if(r&&r.ok)toast('✅ '+r.generated+'건 큐 등록 (사이트 '+r.sites+'개)');else toast((r&&r.error)||'실패','er');renderMembers()}
async function delMember(id){if(!confirm('회원을 삭제할까요? (정산 기록도 삭제)'))return;await api('/members','DELETE',{id});renderMembers()}
async function togglePay(id,paid){await api('/members/pay','POST',{id:id,paid:paid});renderMembers()}
async function addSite(){const d={site_url:$('sUrl').value.trim(),platform:$('sPlat').value,name:$('sName').value.trim(),bo_table:$('sBo').value.trim(),mb_id:$('sId').value.trim(),mb_pass:$('sPw').value,permission:$('sPerm').checked,permission_note:$('sPermNote').value.trim(),daily_limit:0,min_interval_minutes:1};if(!d.site_url){toast('URL 입력','er');return}if(_editId)d.id=_editId;const r=await api('/sites','POST',d);if(r&&r.ok){toast(_editId?'수정됨':(d.permission?'추가됨 (홍보 허용)':'추가됨 (미검증 — 발행 제외)'));cancelEdit();renderSites()}}
async function editSite(id){const sites=await api('/sites','GET');if(!Array.isArray(sites))return;const s=sites.find(x=>x.id===id);if(!s){toast('사이트 없음','er');return}
$('sUrl').value=s.site_url||'';$('sPlat').value=s.platform||'auto';$('sName').value=s.name||'';$('sBo').value=s.bo_table||'';$('sId').value=s.mb_id||'';$('sPw').value='';$('sPw').placeholder=s.login_saved?'저장됨 · 변경시에만 입력':'비밀번호';$('sPerm').checked=true;$('sPermNote').value=s.permission_note||'자동 허용';
_editId=id;$('addBtn').textContent='수정 저장';$('addBtn').classList.add('btn-y');$('editCancel').style.display='';
$('sUrl').scrollIntoView({behavior:'smooth',block:'center'});toast('편집 모드 — 값을 고치고 "수정 저장"')}
function cancelEdit(){_editId=null;['sUrl','sName','sBo','sId','sPw'].forEach(i=>$(i).value='');$('sPerm').checked=true;$('sPlat').value='auto';$('addBtn').textContent='추가';$('addBtn').classList.remove('btn-y');$('editCancel').style.display='none'}
async function prepareSignup(id){
if(!confirm('사이트 조건에 맞는 가입용 아이디·비밀번호를 생성해 암호화 저장합니다.\nCAPTCHA와 이메일 인증, 최종 가입은 직접 진행합니다. 계속할까요?'))return;
const r=await api('/sites/signup-prepare/'+id,'POST',{});if(!r||!r.ok){toast((r&&r.error)||'생성 실패','er');return}
const rules=r.rules||{};const v='아이디: '+r.mb_id+'\n비밀번호: '+r.mb_pass+'\n가입주소: '+r.signup_url+'\n\n학습 규칙: ID '+(rules.id_min||'?')+'~'+(rules.id_max||'?')+'자 / 비밀번호 '+(rules.password_min||'?')+'자 이상'+(r.captcha?' / CAPTCHA 있음':'');
prompt('가입정보가 생성·암호화 저장되었습니다. 지금 복사하세요(비밀번호는 다시 표시되지 않습니다).',v);
window.open(r.signup_url,'_blank','noopener');renderSites();}
async function learnSignup(id){toast('🧠 가입 폼을 제출 없이 측정·학습 중...');const r=await api('/sites/signup-learn/'+id,'POST',{});if(r&&r.ok){const x=r.rules||{};toast('🧠 학습 v'+r.version+' · 필드 '+r.field_count+'개 · ID '+x.id_min+'~'+x.id_max+' · PW '+x.password_min+'+'+(r.changed?' · 폼 변경 감지':''),'ok');renderSites()}else toast('가입 폼 학습 실패: '+(r&&r.error||''),'er')}
async function signupDone(id){if(!confirm('CAPTCHA와 이메일 인증까지 끝나 실제 회원가입이 완료됐습니까?'))return;const r=await api('/sites/signup-status/'+id,'POST',{status:'complete'});if(r&&r.ok){toast('✅ 가입 완료 · 자동 로그인정보 저장됨');renderSites()}else toast((r&&r.error)||'처리 실패','er')}
async function signupAll(id,status){if(status==='rejected'){toast('⛔ 이메일 인증 필요 사이트 — 가입 대상 제외','er');return}if(status==='complete'){toast('✅ 회원가입 및 로그인정보 저장 완료');return}if(['prepared','captcha_wait','email_wait'].includes(status)){await signupDone(id);return}await prepareSignup(id)}
// (oneClick 죽은 JS 제거됨 — ocN 입력칸이 없어 호출 불가였음)
function previewPost(){const c=$('gContent').value.trim();if(!c){toast('먼저 글을 생성하세요','er');return}$('pvTitle').textContent=$('gTitle').value||'';const doc='<!DOCTYPE html><html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><style>body{font-family:-apple-system,sans-serif;max-width:760px;margin:0 auto;padding:16px;color:#222;line-height:1.7}img{max-width:100%}</style></head><body>'+c+'</body></html>';$('pvFrame').srcdoc=doc;$('pvOverlay').style.display='block'}
function closePreview(){$('pvOverlay').style.display='none';$('pvFrame').srcdoc=''}
async function delSite(id){if(!confirm('삭제?'))return;await api('/sites','DELETE',{id});renderSites()}
async function testSite(id){toast('Selenium 테스트 중...');const r=await api('/test/'+id,'POST');if(r&&r.ok)toast('✅ 테스트 성공!'+(r.platform?' ['+(r.platform==='cafe24'?'Cafe24':'그누보드')+']':'')+' '+(r.message||''));else toast('실패: '+(r?.error||r?.message||''),'er')}
async function saveCfg(){const d={brand:$('cBrand').value.trim(),phone:$('cPhone').value.trim(),phones:$('cPhones').value,video_url:$('cVideoUrl').value.trim(),landing_url:$('cLandingUrl').value.trim(),post_email:$('cPostEmail').value.trim(),workers:parseInt($('cWorkers').value)||2,post_delay:parseInt($('cDelay').value)||0,daily_limit:parseInt($('cDaily').value)||0,use_gpt:$('cUseGpt').checked,llm_provider:($('cLlmProvider')?$('cLlmProvider').value:'openrouter'),nvidia_model:($('cNvidiaModel')?$('cNvidiaModel').value.trim():''),openrouter_model:($('cOpenrouterModel')?$('cOpenrouterModel').value.trim():''),telegram_chat_id:$('cTgChat').value.trim(),notify_done:$('cNotifyDone').checked,notify_fail:$('cNotifyFail').checked,backup_time:$('cBackupTime').value.trim(),telegram_control:$('cTgControl').checked,verify_enabled:$('cVerify').checked,mix_keywords:$('cMixKw').checked,block_unpaid:$('cBlockUnpaid').checked,search_provider:'brave',discover_enabled:$('cDiscoOn').checked,discover_daily_target:parseInt($('cDTarget').value)||100,discover_query_limit:parseInt($('cDQuery').value)||100,discover_keywords:'',discover_direct_queries:$('cDDirect').value,excluded_domains:($('cExcludedDomains')?$('cExcludedDomains').value:''),imap_email:($('cImapEmail')?$('cImapEmail').value.trim():''),imap_password:($('cImapPass')&&$('cImapPass').value?$('cImapPass').value:'***설정됨***'),imap_host:($('cImapHost')&&$('cImapHost').value.trim()?$('cImapHost').value.trim():'imap.gmail.com'),twocaptcha_enabled:$('cTwocaptchaEn').checked,brave_price_per_query_usd:parseFloat($('cBravePrice').value)||0,twocaptcha_price_recaptcha_usd:parseFloat($('cCapRePrice').value)||0,twocaptcha_price_image_usd:parseFloat($('cCapImgPrice').value)||0};
const bk=$('cBraveKey').value.trim();if(bk)d.brave_api_key=bk;
// 프록시(Bright Data): 비번은 입력했을 때만 전송(빈칸이면 마스크값으로 기존 유지).
d.proxy_enabled=$('cProxyEn').checked;d.proxy_host=$('cProxyHost').value.trim();d.proxy_port=$('cProxyPort').value.trim();d.proxy_user=$('cProxyUser').value.trim();d.proxy_only_for_cf=$('cProxyCfOnly').checked;
const ppw=$('cProxyPass').value.trim();d.proxy_pass=(ppw?ppw:'***설정됨***');
// Web Unlocker
d.unlocker_enabled=$('cUnlockerEn').checked;d.unlocker_zone=$('cUnlockerZone').value.trim()||'web_unlocker1';
const uk=$('cUnlockerKey').value.trim();d.unlocker_api_key=(uk?uk:'***설정됨***');
// Scraping Browser
d.sbr_enabled=$('cSbrEn').checked;
const sep=$('cSbrEp').value.trim();d.sbr_endpoint=(sep?sep:'***설정됨***');
// 자동가입 고정계정
d.signup_fixed_id=$('cSignupId').value.trim();
const spw=$('cSignupPw').value.trim();d.signup_fixed_pw=(spw?spw:'***설정됨***');
const pw=$('cPw').value.trim();if(pw)d.password=pw;const gp=$('cGuestPw').value.trim();if(gp)d.guest_post_password=gp;const nk=($('cNvidiaKey')?$('cNvidiaKey').value.trim():'');if(nk)d.nvidia_api_key=nk;const ork=($('cOpenrouterKey')?$('cOpenrouterKey').value.trim():'');if(ork)d.openrouter_api_key=ork;const tg=$('cTgTok').value.trim();if(tg)d.telegram_token=tg;const tc=$('cTwocaptchaKey').value.trim();if(tc)d.twocaptcha_api_key=tc;const r=await api('/config','POST',d);if(r&&r.ok){toast('저장 완료');$('cPw').value='';$('cGuestPw').value='';if($('cNvidiaKey'))$('cNvidiaKey').value='';if($('cOpenrouterKey'))$('cOpenrouterKey').value='';$('cTgTok').value='';$('cTwocaptchaKey').value='';$('cProxyPass').value='';$('cUnlockerKey').value='';$('cSbrEp').value='';$('cSignupPw').value='';loadOpenAIUsage()}}
async function loadCfgUI(){const c=await api('/config','GET');if(!c)return;$('cVideoUrl').value=c.video_url||'';$('cLandingUrl').value=c.landing_url||'';$('cPostEmail').value=c.post_email||'';$('cGuestPw').placeholder=(c.guest_post_password==='***설정됨***')?'설정됨 · 변경시에만 입력':'변경시에만 입력';$('cUseGpt').checked=!!c.use_gpt;$('cNotifyDone').checked=!!c.notify_done;$('cNotifyFail').checked=!!c.notify_fail;$('cTgControl').checked=!!c.telegram_control;$('cVerify').checked=(c.verify_enabled!==false);$('cMixKw').checked=(c.mix_keywords!==false);$('cBlockUnpaid').checked=(c.block_unpaid!==false);$('cDiscoOn').checked=!!c.discover_enabled;if(c.discover_daily_target)$('cDTarget').value=c.discover_daily_target;if(c.discover_query_limit)$('cDQuery').value=c.discover_query_limit;if(typeof c.discover_direct_queries==='string')$('cDDirect').value=c.discover_direct_queries;if($('cExcludedDomains')&&typeof c.excluded_domains==='string')$('cExcludedDomains').value=c.excluded_domains;if($('cImapEmail'))$('cImapEmail').value=c.imap_email||'';if($('cImapHost'))$('cImapHost').value=c.imap_host||'imap.gmail.com';if($('cImapPass'))$('cImapPass').placeholder=(c.imap_password==='***설정됨***')?'설정됨 · 변경시만 입력':'앱 비밀번호 16자리 (변경시만)';$('cBraveKey').placeholder=(c.brave_api_key==='***설정됨***')?'설정됨 · 변경시에만 입력':'Brave API 키 입력';
if($('cProxyEn')){$('cProxyEn').checked=!!c.proxy_enabled;$('cProxyHost').value=c.proxy_host||'';$('cProxyPort').value=c.proxy_port||'';$('cProxyUser').value=c.proxy_user||'';$('cProxyCfOnly').checked=(c.proxy_only_for_cf!==false);$('cProxyPass').placeholder=(c.proxy_pass==='***설정됨***')?'설정됨 · 변경시만 입력':'변경시만 입력';}
if($('cUnlockerEn')){$('cUnlockerEn').checked=!!c.unlocker_enabled;$('cUnlockerZone').value=c.unlocker_zone||'web_unlocker1';$('cUnlockerKey').placeholder=(c.unlocker_api_key==='***설정됨***')?'설정됨 · 변경시만 입력':'변경시만 입력';}
if($('cSbrEn')){$('cSbrEn').checked=!!c.sbr_enabled;$('cSbrEp').placeholder=(c.sbr_endpoint==='***설정됨***')?'설정됨 · 변경시만 입력':'변경시만 입력';}
if($('cSignupId')){$('cSignupId').value=c.signup_fixed_id||'';$('cSignupPw').placeholder=(c.signup_fixed_pw==='***설정됨***')?'설정됨 · 변경시만 입력':'변경시만 입력';}
if(c.backup_time)$('cBackupTime').value=c.backup_time;if($('cLlmProvider'))$('cLlmProvider').value=(c.llm_provider==='nvidia')?'nvidia':'openrouter';if($('cNvidiaModel'))$('cNvidiaModel').value=c.nvidia_model||'';if($('cNvidiaKey'))$('cNvidiaKey').placeholder=(c.nvidia_api_key==='***설정됨***')?'설정됨 · 변경시만 입력':'nvapi-... (변경시만)';if($('cOpenrouterModel'))$('cOpenrouterModel').value=c.openrouter_model||'';if($('cOpenrouterKey'))$('cOpenrouterKey').placeholder=(c.openrouter_api_key==='***설정됨***')?'설정됨 · 변경시만 입력':'sk-or-v1-... (변경시만)';if(c.telegram_chat_id)$('cTgChat').value=c.telegram_chat_id;if(typeof c.phones==='string')$('cPhones').value=c.phones;$('cTgTok').placeholder=(c.telegram_token==='***설정됨***')?'설정됨 · 변경시만 입력':'변경시만 입력';$('cTwocaptchaEn').checked=!!c.twocaptcha_enabled;$('cTwocaptchaKey').placeholder=(c.twocaptcha_api_key==='***설정됨***')?'설정됨 · 변경시만 입력':'변경시만 입력';if(c.brave_price_per_query_usd!=null)$('cBravePrice').value=c.brave_price_per_query_usd;if(c.twocaptcha_price_recaptcha_usd!=null)$('cCapRePrice').value=c.twocaptcha_price_recaptcha_usd;if(c.twocaptcha_price_image_usd!=null)$('cCapImgPrice').value=c.twocaptcha_price_image_usd;loadOpenAIUsage();api('/rejected-domains','GET').then(r=>{if(r&&r.ok&&$('rejCount'))$('rejCount').textContent=r.count})}
async function showRejected(){const box=$('rejList');if(!box)return;if(box.style.display!=='none'){box.style.display='none';return}box.style.display='block';box.innerHTML='불러오는 중…';const r=await api('/rejected-domains','GET');if(!r||!r.ok){box.innerHTML='조회 실패';return}if($('rejCount'))$('rejCount').textContent=r.count;const logmap={};(r.log||[]).forEach(x=>{if(!logmap[x.domain])logmap[x.domain]=x.reason||''});box.innerHTML='<div style="color:var(--r);margin-bottom:6px">총 '+r.count+'개 · 발굴 자동 제외됨 (재활성화하려면 옆 ↺ 클릭)</div>'+(r.domains||[]).map(d=>'<div style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;border-bottom:1px solid #17202e"><span><b style="color:var(--t)">'+esc(d)+'</b> <span style="color:var(--d)">'+esc((logmap[d]||'').slice(0,30))+'</span></span><span style="cursor:pointer;color:var(--g)" title="재활성화(제외 해제)" onclick="unrejectDomain(\''+esc(d)+'\')">↺</span></div>').join('')}
async function clearKey(k){if(!confirm(k+' 를 서버에서 지울까요? (그 엔진은 키를 다시 넣기 전까지 못 씁니다)'))return;const r=await api('/config/clear-key','POST',{key:k});if(r&&r.ok){toast((r.was_set?'삭제됨':'이미 비어 있음')+' · '+k,'ok');loadCfgUI()}else toast('실패','er')}
async function unrejectDomain(dom){if(!confirm(dom+' 을(를) 자동 탈락에서 해제할까요? (다시 발굴 대상이 됩니다)'))return;const r=await api('/rejected-domains','POST',{remove:dom});if(r&&r.ok){toast('해제됨 · '+dom,'ok');showRejected();showRejected()}else toast('실패','er')}
async function loadOpenAIUsage(){
  const r=await api('/openai/usage','GET');
  const c=await api('/twocaptcha/usage','GET');
  if(r&&r.ok){
    // 현재 엔진(모델)만 실측 표시 — OpenAI 예산/토큰 추정 표기는 2026-09-11 제거
    const cur=r.current||{},cm=cur.month||{},ct=cur.today||{};
    const eng='<b style="color:var(--t)">엔진 '+esc((r.llm_provider||'openrouter').toUpperCase())+'</b> · '+esc(r.llm_model||'')+' · 키 '+(r.llm_key_set?'<span style="color:var(--g)">있음</span>':'<span style="color:var(--r)">없음 — 위에 키 입력 후 저장</span>')+(r.use_gpt?'':' · <span style="color:var(--y)">AI 생성 체크 꺼짐(템플릿만)</span>');
    $('openaiUsage').innerHTML=eng+'<br><b style="color:var(--p)">이번 달 $'+Number(cm.estimated_cost_usd||0).toFixed(4)+'</b> ('+(cm.requests||0)+'편) · 오늘 $'+Number(ct.estimated_cost_usd||0).toFixed(4)+' ('+(ct.requests||0)+'편) · 편당 $'+Number(cur.per_call_usd||0).toFixed(5)+' <span style="color:var(--g)">'+(cur.measured?'실측':'추정')+'</span>';
  } else {
    $('openaiUsage').innerHTML='<span style="color:var(--y)">AI 사용량을 불러오지 못했습니다.</span>';
  }
  if(c&&c.ok){
    const bal=(typeof c.balance==='number')?c.balance:0;const rem=(typeof c.remaining_usd==='number')?c.remaining_usd:bal;const delta=(typeof c.charged_since_last_check_usd==='number')?c.charged_since_last_check_usd:0;
    $('twocaptchaUsage').innerHTML = `
      <div class="twocap-shell">
        <div class="twocap-header">
          <span>2captcha</span>
          <span class="twocap-chip ok">정상 연결</span>
        </div>
        <div class="twocap-metrics">
          <div class="twocap-metric"><div class="label">잔액</div><div class="value green">$${Number(bal).toFixed(4)}</div></div>
          <div class="twocap-metric"><div class="label">남은 금액</div><div class="value blue">$${Number(rem).toFixed(4)}</div></div>
          <div class="twocap-metric"><div class="label">실시간 차감</div><div class="value amber">$${Number(delta).toFixed(4)}</div></div>
        </div>
        <div class="twocap-footer">
          <span>업데이트 ${esc(c.updated_at||'최근')}</span>
          <span>${esc(c.error||'2captcha 정상 연결')}</span>
        </div>
      </div>
    `;
  } else {
    $('twocaptchaUsage').innerHTML = `
      <div class="twocap-shell">
        <div class="twocap-header">
          <span>2captcha</span>
          <span class="twocap-chip warn">미연결</span>
        </div>
        <div class="twocap-metrics">
          <div class="twocap-metric"><div class="label">잔액</div><div class="value green">$0.0000</div></div>
          <div class="twocap-metric"><div class="label">남은 금액</div><div class="value blue">$0.0000</div></div>
          <div class="twocap-metric"><div class="label">실시간 차감</div><div class="value amber">$0.0000</div></div>
        </div>
        <div class="twocap-footer">
          <span>상태</span>
          <span>${esc((c&&c.error)||'API 키/활성화 상태를 확인하세요')}</span>
        </div>
      </div>
    `;
  }
}
// ---- API 비용 대시보드 (3개 API 통합) ----
let _usdkrwRate=0;  // USD→KRW 환율(loadUsageDashboard에서 갱신)
function _usd(v){const n=Number(v||0);const d='$'+(n<0.01&&n>0?n.toFixed(5):n.toFixed(4));return d}
function _krw(v){const n=Number(v||0);const r=_usdkrwRate||1350;const w=n*r;  // 환율 미확보 시에도 기본 1350으로 원화 표시(안전망)
  // 작은 금액도 원단위까지, 큰 금액은 천단위 콤마
  return '₩'+(w<10?w.toFixed(1):Math.round(w).toLocaleString());}
function _money(v){const k=_krw(v);return _usd(v)+(k?` <span style="font-size:.82em;color:var(--d)">(${k})</span>`:'');}
function _bars(series,key){ // 미니 막대(일별/시간별). series=[{cost,count,...}]
  if(!series||!series.length)return '<div style="font-size:9px;color:var(--d)">데이터 없음</div>';
  const mx=Math.max(1,...series.map(x=>Number(x[key]||0)));
  return '<div style="display:flex;align-items:flex-end;gap:2px;height:44px">'+series.map(x=>{
    const v=Number(x[key]||0);const h=Math.max(1,Math.round(v/mx*40));
    const lb=x.date?x.date.slice(5):(x.hour!=null?(x.hour+'시'):'');
    return `<div style="flex:1;min-width:3px" title="${lb}: ${key==='cost'?_usd(v):v}"><div style="height:${h}px;background:var(--p);border-radius:2px;opacity:${v?0.85:0.25}"></div></div>`;
  }).join('')+'</div>'}
function _costCard(b,extraNote){
  const est=b.estimated?'<span class="twocap-chip warn" style="margin-left:6px">추정</span>':'<span class="twocap-chip ok" style="margin-left:6px">실측</span>';
  const up=b.unit_price||{};
  const upStr=Object.keys(up).length?Object.entries(up).map(([k,v])=>`${esc(k)}: $${Number(v).toFixed(k.includes('million')?4:5)}`).join(' · '):'단가 정보 없음';
  return `<div class="twocap-shell">
    <div class="twocap-header"><span style="font-weight:700;color:var(--t)">${esc(b.label)}${est}</span><span style="font-size:9px;color:var(--d)">${esc(b.note||'')}</span></div>
    <div class="twocap-metrics" style="grid-template-columns:repeat(4,1fr)">
      <div class="twocap-metric"><div class="label">이번 달</div><div class="value green">${_usd(b.month_cost_usd)}</div><div style="font-size:8px;color:var(--d)">${_krw(b.month_cost_usd)||''} · ${b.month_requests||0}회</div></div>
      <div class="twocap-metric"><div class="label">오늘</div><div class="value blue">${_usd(b.today_cost_usd)}</div><div style="font-size:8px;color:var(--d)">${_krw(b.today_cost_usd)||''} · ${b.today_requests||0}회</div></div>
      <div class="twocap-metric"><div class="label">횟수당(평균)</div><div class="value amber">${_usd(b.per_call_usd)}</div><div style="font-size:8px;color:var(--d)">${_krw(b.per_call_usd)||''}</div></div>
      <div class="twocap-metric"><div class="label">단가</div><div class="value" style="font-size:11px;line-height:1.35;color:var(--t)">${esc(upStr)}</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:9px">
      <div><div style="font-size:9px;color:var(--d);margin-bottom:3px">최근 14일 (일별 비용)</div>${_bars(b.daily,'cost')}</div>
      <div><div style="font-size:9px;color:var(--d);margin-bottom:3px">오늘 24시간 (시간별 비용)</div>${_bars(b.hourly,'cost')}</div>
    </div>
    ${(b.by_model&&b.by_model.length)?'<table style="margin-top:8px;font-size:10.5px"><thead><tr><th>모델 (이번 달)</th><th style="text-align:right">횟수</th><th style="text-align:right">비용</th><th>기준</th></tr></thead><tbody>'+b.by_model.map(x=>`<tr style="${x.current?'':'color:var(--d)'}"><td>${esc(x.model)}${x.current?' <span class="twocap-chip ok">현재</span>':''}</td><td style="text-align:right">${Number(x.requests||0).toLocaleString()}회</td><td style="text-align:right">${_usd(x.cost_usd)}</td><td>${x.measured?'실측':'추정'}${x.current?'':' · 종료'}</td></tr>`).join('')+'</tbody></table>':''}
    ${extraNote?`<div class="twocap-footer"><span>${extraNote}</span></div>`:''}
  </div>`}
let _usageTimer=null;
function startUsageAuto(){
  stopUsageAuto();
  _usageTimer=setInterval(()=>{
    // 비용 탭이 실제 표시 중이고, 브라우저 탭이 활성일 때만 갱신(불필요한 서버 호출 방지)
    if(document.hidden)return;
    const p=$('p-cost'); if(!p||!p.classList.contains('on')){stopUsageAuto();return}
    loadUsageDashboard(true);
  },30000);
}
function stopUsageAuto(){ if(_usageTimer){clearInterval(_usageTimer);_usageTimer=null} }
async function loadUsageDashboard(silent){
  const box=$('costCards'); if(box&&!silent)box.innerHTML='<div style="color:var(--d);font-size:11px">불러오는 중…</div>';
  const u=await api('/usage','GET');
  if(!u||!u.ok){if(box&&!silent)box.innerHTML='<div style="color:var(--y);font-size:11px">비용 정보를 불러오지 못했습니다.</div>';return}
  _usdkrwRate=(u.usdkrw&&u.usdkrw.rate)||0;  // 환율 갱신(원화 병기용)
  $('costTotalMonth').innerHTML=_money(u.total_month_usd);
  $('costTotalToday').innerHTML=_money(u.total_today_usd);
  const rateInfo=u.usdkrw?('환율 ₩'+Math.round(u.usdkrw.rate).toLocaleString()+'/$'+(u.usdkrw.source==='live'?' 실시간':u.usdkrw.source==='cache'?' 캐시':' 기본값')):'';
  const ai=$('costAutoInfo'); if(ai){const t=new Date();ai.textContent='30초마다 자동 새로고침 · 갱신 '+String(t.getHours()).padStart(2,'0')+':'+String(t.getMinutes()).padStart(2,'0')+':'+String(t.getSeconds()).padStart(2,'0')+(rateInfo?' · '+rateInfo:'')}
  const o=u.openai||{},c=u.twocaptcha||{},b=u.brave||{};
  const oNote=(o.ledger_month_usd!=null&&Math.abs((o.ledger_month_usd||0)-(o.month_cost_usd||0))>0.0001)?('이번 달 AI 원장 합계 '+_usd(o.ledger_month_usd)+' — 위 숫자는 현재 엔진만, 합계는 종료된 모델(표 참고) 포함'):'';
  let cNote='';
  if(c.balance_ok&&c.balance_usd!=null)cNote='참고용 잔액 '+_usd(c.balance_usd)+(c.balance_delta_usd?(' · 최근 차감 '+_usd(c.balance_delta_usd)):'');
  else if(c.balance_error)cNote='잔액조회: '+esc(c.balance_error);
  let bNote=b.price_configured?'':'⚠ 쿼리당 단가 미설정 — 설정 탭에서 입력하면 추정비용이 계산됩니다';
  if(!b.active)bNote=(bNote?bNote+' · ':'')+'현재 검색 공급자가 Brave가 아님';
  box.innerHTML=_costCard(o,oNote)+_costCard(c,cNote)+_costCard(b,bNote);
}
async function loadPool(){const p=await api('/keywords','GET');if(!Array.isArray(p))return;$('poolCount').textContent=p.length+'개';$('poolCsv').value=p.map(k=>[k.지역||'',k.서비스||'',k.브랜드||''].join(',')).join('\n')}
async function savePool(append){const csv=$('poolCsv').value;const r=await api('/keywords','POST',{csv:csv,append:!!append});if(r&&r.ok){toast('풀 저장: '+r.count+'개');loadPool()}else if(r)toast('실패','er')}
async function clearPool(){const r=await api('/keywords','DELETE');if(r&&r.ok){toast('풀 비움');loadPool()}}
function imgWid(){const s=$('imgWrSelect');return s?s.value:''}
function _wq(path){const w=imgWid();return w?(path+(path.includes('?')?'&':'?')+'workroom_id='+encodeURIComponent(w)):path}
async function loadImgWorkrooms(){const s=$('imgWrSelect');if(!s)return;let list=[];try{const r=await api('/workrooms','GET');if(Array.isArray(r))list=r}catch(e){}const keep=s.value;s.innerHTML='<option value="">전체(공통) 이미지</option>'+list.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+'</option>').join('');if(list.some(x=>x.id===keep))s.value=keep;onImgWrChange(true)}
function onImgWrChange(skipToast){const w=imgWid();const nm=w?(($('imgWrSelect').selectedOptions[0]||{}).textContent||''):'';const lbl=w?('· '+nm):'· 전체(공통)';if($('imgWrLabel'))$('imgWrLabel').textContent=lbl;if($('imgWrLabel2'))$('imgWrLabel2').textContent=lbl;loadImages();loadImageFiles();if(!skipToast&&w)toast(nm+' 작업실 이미지','ok')}
async function loadImages(){const p=await api(_wq('/images'),'GET');if(!Array.isArray(p))return;$('imgCount').textContent=p.length+'개';$('imgUrls').value=p.join('\n')}
async function saveImages(append){const text=$('imgUrls').value;const r=await api('/images','POST',{text:text,append:!!append,workroom_id:imgWid()});if(r&&r.ok){toast('이미지 URL: '+r.count+'개 저장');loadImages()}else if(r)toast((r.error)||'실패','er')}
async function clearImages(){const r=await api('/images','DELETE',{workroom_id:imgWid()});if(r&&r.ok){toast('이미지 URL 비움');loadImages()}}
async function loadImageFiles(){const rows=await api(_wq('/images/files'),'GET');if(!Array.isArray(rows))return;const g=$('imgGallery');g.innerHTML=rows.length?rows.map(x=>`<div style="background:#0b1322;border:1px solid var(--line);border-radius:8px;padding:7px"><img src="${x.url}" alt="${esc(x.name)}" style="width:100%;height:105px;object-fit:cover;border-radius:5px"><div style="font-size:9px;color:var(--d);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:5px 0" title="${esc(x.name)}">${esc(x.name)}</div><div class="row"><span style="font-size:9px;color:var(--d)">${Math.ceil(x.size/1024)}KB</span><span style="flex:1"></span><button class="btn btn-r btn-xs" onclick="deleteImageFile('${esc(x.name)}')">삭제</button></div></div>`).join(''):'<div style="color:var(--d);font-size:11px">저장된 이미지가 없습니다.</div>'}
async function uploadImages(){const f=$('imgFiles').files;if(!f.length)return toast('이미지를 선택하세요','er');const fd=new FormData();[...f].forEach(x=>fd.append('files',x));const w=imgWid();if(w)fd.append('workroom_id',w);try{const r=await(await fetch('/api/images/upload',{method:'POST',body:fd})).json();if(r.ok){toast(r.count+'개 이미지 저장됨','ok');$('imgFiles').value='';loadImageFiles()}else toast(r.error||'업로드 실패','er')}catch(e){toast(e.message,'er')}}
async function deleteImageFile(name){if(!confirm('이 이미지를 삭제할까요?'))return;const r=await api('/images/file','DELETE',{name,workroom_id:imgWid()});if(r&&r.ok){toast('이미지 삭제됨','ok');loadImageFiles()}else toast(r&&r.error||'삭제 실패','er')}
async function uploadXlsx(){const f=$('poolXlsx').files[0];if(!f)return;const fd=new FormData();fd.append('file',f);try{const r=await(await fetch('/api/keywords/upload',{method:'POST',body:fd})).json();if(r&&r.ok){toast('엑셀 업로드: '+r.count+'개');loadPool()}else toast(r&&r.error||'업로드 실패','er')}catch(e){toast(e.message,'er')}$('poolXlsx').value=''}
async function genRandom(){const sid=$('poolSiteFilter').value;const n=parseInt($('poolN').value)||1;const r=await api('/generate/random','POST',{site_ids:sid?[sid]:[],count:n});if(r&&r.ok){if(r.generated!=null)toast(r.generated+'건 큐 등록 (랜덤 '+r.picks+'개)'+(r.blocked?` · 미허용 ${r.blocked} 제외`:''));else{$('gTitle').value=r.title;$('gContent').value=r.content;$('gLen').textContent=(r.content||'').length.toLocaleString()+'자';toast('랜덤 미리보기 생성')}loadOpenAIUsage()}else if(r)toast(r.error||'실패','er')}
// ---- 사이트 대량/허용/헬스 ----
async function bulkAdd(){const csv=$('bulkCsv').value.trim();if(!csv){toast('CSV 입력','er');return}const r=await api('/sites/bulk','POST',{csv:csv,permission:$('bulkPerm').checked});if(r&&r.ok){toast(r.added+'개 등록');$('bulkCsv').value='';renderSites()}}
// ★자동허용(대표님 지시): 켤 때 근거 입력 없이 바로 허용. 끄기는 특정 사이트 수동 잠금용으로 유지.
async function toggleSitePermission(id,on){const r=await api('/sites/permission','POST',{ids:[id],permission:on,permission_note:on?'자동 허용':''});if(r&&r.ok){toast(on?'✅ 발행 허용':'발행 잠금됨');renderSites()}else{toast((r&&r.error)||'변경 실패','er');renderSites()}}
async function healthAll(){const ids=getSiteIds();if(!ids.length){toast('사이트 선택','er');return}toast(ids.length+'개 점검중...');for(const id of ids){await api('/sites/health/'+id,'POST')}renderSites();toast('점검 완료')}
// (예약 스케줄 UI 제거됨 — 회원별 스케줄러가 대체. 죽은 JS 정리)
// ---- 통계 ----
function tile(label,val,color){return `<div class="card" style="flex:1;min-width:110px;text-align:center;margin:0"><div style="font-size:22px;font-weight:700;color:${color}">${val}</div><div style="font-size:10px;color:var(--d)">${label}</div></div>`}
async function renderStats(){const s=await api('/stats','GET');if(!s)return;
$('statTop').innerHTML=tile('전체',s.total,'var(--t)')+tile('성공',s.ok,'var(--g)')+tile('실패',s.fail,'var(--r)')+tile('스킵',s.skip,'var(--y)')+tile('성공률',s.rate+'%','var(--p)')+tile('생존율',(s.alive_rate||0)+'%','var(--v)');
const rz=(s.reasons||[]);
$('statReasons').innerHTML=rz.length?('<div style="font-size:11px;color:var(--d);margin:10px 0 4px">실패 원인 분류</div><div style="display:flex;gap:6px;flex-wrap:wrap">'+rz.map(r=>`<span class="st st-f">${esc(r.reason)} ${r.n}</span>`).join('')+'</div>'):'';
$('statSurvival').innerHTML=(s.alive+s.dead)?`<div style="font-size:11px;color:var(--d);margin:12px 0 4px">발행글 생존 (검증됨 ${s.alive+s.dead}건)</div><span class="st st-ok">생존 ${s.alive}</span> <span class="st st-f">삭제 ${s.dead}</span>`:'';
// ★일별 통계 막대(대표님 지시 2026-09-09): 최근 14일 연속 달력. 날짜별 총건수를 막대 위에 표시,
//   데이터 없는 날도 옅은 바닥선으로 구분. 성공(초록)+실패(빨강) 누적막대.
const mx=Math.max(1,...s.by_day.map(d=>d.done+d.failed));const CH=140;
$('statDays').innerHTML='<div style="display:flex;align-items:flex-end;gap:3px;height:'+(CH+18)+'px">'+s.by_day.map(d=>{const tot=d.done+d.failed;const h=tot?Math.max(4,Math.round(tot/mx*CH)):0;const go=tot?Math.round(d.done/tot*h):0;return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%" title="${d.day}: 성공 ${d.done} · 실패 ${d.failed} (총 ${tot})"><div style="font-size:9px;color:${tot?'var(--t)':'#33425f'};margin-bottom:2px;font-weight:${tot?600:400}">${tot||''}</div>${tot?`<div style="width:100%;height:${h}px;display:flex;flex-direction:column-reverse;border-radius:3px 3px 0 0;overflow:hidden;background:var(--b)"><div style="height:${h-go}px;background:var(--r)"></div><div style="height:${go}px;background:var(--g)"></div></div>`:'<div style="width:100%;height:2px;background:#1c2740;border-radius:2px"></div>'}<div style="font-size:8px;color:var(--d);margin-top:3px;white-space:nowrap">${(d.day||'').slice(5)}</div></div>`}).join('')+'</div>';
$('statSites').innerHTML='<table><thead><tr><th>사이트</th><th>성공</th><th>실패</th><th>생존</th><th>삭제</th></tr></thead><tbody>'+s.by_site.map(x=>`<tr><td>${esc(x.site)}</td><td style="color:var(--g)">${x.done}</td><td style="color:var(--r)">${x.failed}</td><td style="color:var(--v)">${x.alive||0}</td><td style="color:var(--d)">${x.dead||0}</td></tr>`).join('')+'</tbody></table>'}

// ---- 사이트 목록 실시간 렌더 (새로고침 없이) ----
function siteRow(s){const st=s.status==='done'?'ok':s.status==='failed'?'f':'i';const nm=esc(s.name||(s.site_url||'').slice(0,20));const today=(s.posted_today||0);
const adminSource=['manual_admin','admin_bulk','legacy_admin','candidate_registered','verified_test'].includes(s.registration_source);const permitted=!!(s.permission&&adminSource);const perm='<label title="'+esc(s.permission_note||'허용 근거 미입력')+'" style="display:flex;align-items:center;gap:5px;white-space:nowrap;color:'+(permitted?'var(--g)':'var(--r)')+'"><input type="checkbox" style="width:auto" '+(permitted?'checked':'')+' onchange="toggleSitePermission(\''+esc(s.id)+'\',this.checked)">'+(permitted?'허용 동의됨':'발행 불가')+'</label>';
const lb=(s.permission&&adminSource)?'':'border-left:3px solid var(--r)';
const hdot=s.health?('<span title="상태점검: '+esc(s.health)+' ('+esc(s.health_at||'')+')" style="color:'+(s.health==='ok'?'var(--g)':'var(--r)')+'">●</span> '):'';
const pl=(s.platform||'auto');const plname=pl==='cafe24'?'Cafe24':(pl==='gnuboard'?'그누보드':'자동');const plcolor=pl==='cafe24'?'var(--v)':(pl==='gnuboard'?'var(--p)':'var(--d)');
const learned=s.learned&&s.learned.write_url;const lbadge=learned?' <span title="자가학습 셀렉터 저장됨" style="color:var(--g)">🎓</span>':'';
const cbadge=s.has_captcha?' <span title="캡차 감지 — 자동발행 제외('+esc(s.captcha_note||'')+')" style="color:var(--y)">🧩캡차</span>':'';
const pltag='<span style="font-size:9px;color:'+plcolor+'" title="발행 방식">'+plname+'</span>'+lbadge+cbadge;
const ss=s.signup_status||'';const signup=ss==='complete'?'<span class="st st-ok">가입완료·로그인저장</span>':(ss==='rejected'?'<span class="st st-f" title="'+esc(s.signup_reject_reason||'')+'">가입 제외 · 이메일인증</span>':(ss==='prepared'?'<span class="st st-y">가입정보만 준비됨</span>':(ss?'<span class="st st-y">가입 '+esc(ss)+'</span>':'')));const pv=s.signup_profile_version?'<span class="st st-i" title="최근측정 '+esc(s.signup_profile_measured_at||'')+'">가입학습 v'+s.signup_profile_version+(s.signup_profile_changed?' 변경':'')+'</span>':'';
const allLabel=ss==='rejected'?'가입 제외':(ss==='complete'?'✓ 로그인 저장됨':(['prepared','captcha_wait','email_wait'].includes(ss)?'실제 가입완료 확인':'올인원 가입'));const allClass=(ss==='rejected'||ss==='complete')?'btn-d':(['prepared','captcha_wait','email_wait'].includes(ss)?'btn-g':'btn-v');
const menu=`<details style="display:inline-block;position:relative"><summary class="btn btn-d btn-xs" style="list-style:none;cursor:pointer">관리 ▾</summary><div style="position:absolute;right:0;z-index:20;background:#101a2c;border:1px solid #33425f;border-radius:8px;padding:7px;min-width:125px;display:grid;gap:5px;box-shadow:0 8px 24px #0008"><button class="btn btn-p btn-xs" onclick="editSite('${esc(s.id)}')">편집</button><button class="btn ${allClass} btn-xs" onclick="signupAll('${esc(s.id)}','${esc(ss)}')">${allLabel}</button><button class="btn btn-r btn-xs" onclick="delSite('${esc(s.id)}')">삭제</button></div></details>`;
return `<tr data-id="${esc(s.id)}" style="${lb}"><td><input type="checkbox" class="cb" data-id="${esc(s.id)}"></td><td>${hdot}<b>${nm}</b><br>${signup} ${pv}</td><td>${perm}</td><td style="min-width:240px;max-width:340px"><a href="${esc(s.site_url||'#')}" target="_blank" rel="noopener" title="${esc(s.site_url||'')}" style="display:block;color:var(--p);word-break:break-all;line-height:1.45">${esc(s.site_url||'-')}</a><a href="${esc(s.site_url||'#')}" target="_blank" rel="noopener" class="btn btn-p btn-xs" style="display:inline-block;margin-top:5px">🔗 링크 열기</a></td><td style="color:var(--p)">${esc(s.bo_table||'')}<br>${pltag}</td><td style="color:var(--d);white-space:nowrap"><div>오늘 ${today}건</div><div style="margin-top:3px;font-size:10px;color:var(--g)">무제한 · 1분 간격(고정)</div></td><td><span class="st st-${st}" title="${esc(s.technical_block_reason||s.verification_fail_reason||'')}">${esc(s.status||'idle')}</span>${s.technical_block_reason?'<br><span style="color:var(--r);font-size:9px">'+esc(s.technical_block_reason)+'</span>':''}</td><td style="white-space:nowrap">${menu}</td></tr>`}
async function healthSite(id){toast('점검중...');const r=await api('/sites/health/'+id,'POST');if(r&&r.ok){const h=r.health;const pn=h.platform==='cafe24'?'Cafe24':'그누보드';toast((h.ok?'✅ 정상':'⚠️ 확인필요')+` [${pn}] 접속:${h.reachable?'O':'X'} 로그인폼:${h.login_form?'O':'X'} 글쓰기:${h.write_page?'O':'X'}`,h.ok?'ok':'er');renderSites()}else toast('실패','er')}
async function dryRun(id){toast('🧪 드라이런 실행중... (글은 올리지 않습니다, 최대 60초)');const r=await api('/sites/dryrun/'+id,'POST');if(!r){toast('실패','er');return}
const rows=(r.steps||[]).map(s=>`<tr><td>${s.ok?'<span style="color:var(--g)">✅</span>':'<span style="color:var(--r)">❌</span>'}</td><td><b>${esc(s.name)}</b></td><td style="color:var(--d)">${esc(s.detail)}</td></tr>`).join('');
const hdr=r.ok?'<span style="color:var(--g)">✅ 발행 가능 — 등록 직전까지 모두 통과</span>':'<span style="color:var(--r)">❌ 발행 불가 — 아래 ❌ 지점에서 막힘</span>';
$('pvTitle').textContent='드라이런 결과';
$('pvFrame').srcdoc='<!DOCTYPE html><html lang=ko><head><meta charset=utf-8><style>body{font-family:-apple-system,sans-serif;padding:18px;color:#222}table{width:100%;border-collapse:collapse;font-size:13px}td{padding:8px 6px;border-bottom:1px solid #eee;vertical-align:top}h3{margin-bottom:12px;font-size:16px}</style></head><body><h3>'+hdr+'</h3><table>'+rows+'</table><p style="color:#888;font-size:12px;margin-top:14px">※ 실제 게시글은 등록되지 않았습니다. 제목/본문을 채워보기만 하고 중단했습니다.</p></body></html>';
$('pvOverlay').style.display='block';renderSites()}
async function detectSite(id){toast('플랫폼 감지중...');const r=await api('/sites/detect/'+id,'POST');if(r&&r.ok){toast('감지됨: '+(r.platform==='cafe24'?'Cafe24':'그누보드'));renderSites()}else toast('실패: '+(r&&r.error||''),'er')}
async function learnSite(id){if(!confirm('실측 학습을 실행합니다.\n등록된 사이트 한 곳의 글쓰기 DOM과 폼을 확인해 저장하며 글은 제출하지 않습니다. 진행할까요?'))return;toast('🎓 비제출 실측 학습 중...(최대 60초)');const r=await api('/sites/learn/'+id,'POST');if(r&&r.ok&&r.learned){toast('🎓 실측 성공! 비제출 레시피 저장됨 ('+(r.learned.content_mode||'')+')');renderSites()}else if(r&&r.captcha){toast('🧩 CAPTCHA 감지 — 우회하지 않고 제외 상태를 저장했습니다','er');renderSites()}else if(r&&r.blocked){toast('⛔ 보안 차단 감지 — 우회하지 않고 측정 결과를 저장했습니다','er');renderSites()}else toast('실측 완료: '+((r&&(r.message||r.error))||'폼 미확정'),'er')}
async function renderSites(){const sites=await api('/sites','GET');if(!Array.isArray(sites))return;
// 탭 카운트는 '실시간 발행가능' 수로 설정(아래 pubs 계산 후)
// 발행용 드롭다운: 실제 게시 성공 URL까지 검증된 허용 사이트만 표시
const sources=['manual_admin','admin_bulk','legacy_admin','candidate_registered','verified_test'];
const publishable=sites.filter(s=>!!s.permission&&sources.includes(s.registration_source)&&s.status!=='rejected'&&s.write_test_status==='passed'&&/^https?:\/\//.test(s.verified_post_url||''));
[['kwSiteFilter','전체 실게시 검증 사이트'],['wrSite','전체 실게시 검증 사이트'],['poolSiteFilter','전체 실게시 검증 사이트']].forEach(([id,label])=>{const sel=$(id);if(!sel)return;const cur=sel.value;sel.innerHTML='<option value="">'+label+' ('+publishable.length+'곳)</option>'+publishable.map(s=>`<option value="${esc(s.id)}">${esc(s.name||(s.site_url||'').slice(0,20))}</option>`).join('');if(publishable.some(s=>s.id===cur))sel.value=cur});
// 체크 상태 보존
const checked=new Set(getSiteIds());
if(!sites.length){$('siteList').innerHTML='<p style="color:var(--d);padding:30px;text-align:center">등록된 사이트가 없습니다</p>';return}
// '실제 발행되는 것만' 필터: 기본은 발행 실패(rejected/failed) 사이트를 숨긴다.
// (사장님 요청 — 목록엔 되는 것/시도 예정만 보이게. 전체보기 토글로 숨긴 것도 확인 가능)
// 3분류: 발행가능(실제 돌아감) / 진행중(뚫는 중) / 탈락(안 됨)
const srcOK=s=>['manual_admin','admin_bulk','legacy_admin','candidate_registered','verified_test'].indexOf(s.registration_source)>=0;
const isPub=s=>!!s.permission&&srcOK(s)&&s.status!=='rejected'&&s.write_test_status==='passed'&&/^https?:\/\//.test(s.verified_post_url||'');
const isDead=s=>(s.status==='rejected'||s.status==='failed'||(s.permission===false&&srcOK(s)&&s.write_test_status==='failed'));
const pubs=sites.filter(isPub);
const prog=sites.filter(s=>!isPub(s)&&!isDead(s));
$('siteTabCount').textContent=pubs.length;   // 탭 카운트=실시간 발행가능 수(대표님 요청)
const showProg=window._siteShowProg||false;
let shown=pubs.slice();
if(showProg)shown=shown.concat(prog);   // '안 되는' 사이트는 서버가 자동삭제 — 이 영역은 발행가능/진행중만
const toggle=`<div class="row" style="margin-bottom:6px;font-size:11px;color:var(--d);flex-wrap:wrap;gap:8px"><span style="color:var(--g)">🟢 발행가능 ${pubs.length}곳</span>${prog.length?`<label style="display:flex;align-items:center;gap:4px"><input type="checkbox" style="width:auto" ${showProg?'checked':''} onclick="window._siteShowProg=this.checked;renderSites()">진행중 ${prog.length}곳 보기</label>`:''}<span style="flex:1"></span></div>`;
if(!shown.length){$('siteList').innerHTML=toggle+'<p style="color:var(--d);padding:30px;text-align:center">발행가능 사이트가 아직 없습니다'+(prog.length?' (진행중 '+prog.length+'곳 — 위에서 보기)':'')+'</p>';return}
$('siteList').innerHTML=toggle+'<table><thead><tr><th><input type="checkbox" id="allCb" onclick="document.querySelectorAll(\'.cb\').forEach(c=>c.checked=this.checked)"></th><th>이름</th><th>허용</th><th>URL</th><th>게시판</th><th>오늘</th><th>상태</th><th>동작</th></tr></thead><tbody>'+shown.map(siteRow).join('')+'</tbody></table>';
checked.forEach(id=>{const c=document.querySelector(`.cb[data-id="${id}"]`);if(c)c.checked=true})}

async function purgeDeadSites(){
  const dry=await api('/sites/purge','POST',{});
  if(!dry){return}
  if(!dry.ok){toast(dry.error||'정리 실패','er');return}
  const del=dry.dead_sites||[];const keep=dry.keep||0;
  if(!del.length){toast('삭제할 안 되는 사이트가 없습니다','ok');return}
  const names=del.map(x=>'· '+(x.name||x.url||'')+' ('+(x.reason||'')+')').join('\n');
  if(!confirm('안 되는 사이트 '+del.length+'곳을 삭제합니다.\n(발행 가능 '+keep+'곳은 유지)\n\n'+names+'\n\n삭제하시겠습니까?'))return;
  const res=await api('/sites/purge','POST',{confirm:true});
  if(res&&res.ok){toast(('삭제 '+(res.deleted||del.length)+'곳 완료 · 유지 '+(res.keep||keep)+'곳'),'ok');renderSites()}
  else{toast((res&&res.error)||'삭제 실패','er')}
}

// ---- 통계/진행률 폴링 ----
async function poll(){const r=await api('/workers/stats','GET');if(!r)return;
$('q').textContent=r.queued||0;$('ok').textContent=r.success||0;$('fl').textContent=r.fail||0;$('sk').textContent=r.skipped||0;
if(r.site_goal){const sg=$('siteGoal');if(sg){const done=r.site_done||0,goal=r.site_goal;const pct=Math.round(done/goal*100);sg.textContent=done+'/'+goal+' ('+pct+'%)';sg.style.color=done>=goal?'var(--g)':'var(--p)'}}
const wn=(r.wr_workers==null?null:r.wr_workers);if(wn!=null){$('ws').textContent=wn>0?(wn+'개 발행중'):'정지';$('ws').style.color=wn>0?'var(--g)':'var(--d)';}else{const wstate=r.paused?'PAUSE':(r.active?'ON':'OFF');$('ws').textContent=wstate;$('ws').style.color=r.paused?'var(--y)':(r.active?'var(--g)':'var(--d)');}
const total=r.total||0,done=r.done||0;
if(total>0){$('progCard').style.display='block';const pct=Math.round(done/total*100);$('progBar').style.width=pct+'%';$('progText').textContent=`${done} / ${total} (${pct}%)`+(r.skipped?` · 스킵 ${r.skipped}`:'')}else{$('progCard').style.display='none'}
// ★결과탭 병합: 발행현황(p-wlog) 탭이 열려 있으면 관제실·발행이력·캡차 모두 갱신.
if($('p-wlog')&&$('p-wlog').classList.contains('on')){renderWorkerLog();renderHistory();renderCaptchaTasks()}}

// ★초기화 방어(대표님 제보 '빈페이지' 2026-09-09): 요소 하나가 null이라도 나머지 초기화·폴링이 죽지 않도록
//   각 단계를 개별 try/catch로. 또 현재 .on 패널이 없으면(구조 꼬임) 무조건 키워드탭으로 복구해 흰화면 방지.
(function boot(){
  try{const g=$('gContent');if(g)g.addEventListener('input',function(){$('gLen').textContent=this.value.length.toLocaleString()+'자'})}catch(e){}
  try{const k=$('kwlist');if(k)k.addEventListener('input',function(){$('kwCount').textContent=parseList().length+'줄'})}catch(e){}
  // 활성 패널이 하나도 없으면 기본 탭 강제 활성화(빈화면 최후 방어).
  try{if(!document.querySelector('.panel.on'))T('kw')}catch(e){console.error('기본탭 복구 실패',e)}
  [renderSites,poll,loadPool,loadImages,loadImageFiles,loadWorkrooms,loadImgWorkrooms,loadRegionTool].forEach(fn=>{try{fn()}catch(e){console.error('init',fn.name,e)}});
})();
setInterval(()=>{try{poll()}catch(e){}},2000);
setInterval(()=>{try{renderSites()}catch(e){}},4000);
</script>
</body></html>'''

def R(title,**kw):
    if title=='로그인': return render_template_string(HTML+LOGIN_HTML+'\n</body></html>',title=title,**kw)
    return render_template_string(HTML+DASH_HTML,title=title,**kw)

# ==================== Main ====================
def main():
    # 정상 환경 일치: 서버 타임존을 한국(Asia/Seoul)으로 (위장이 아니라 실제 운영지역과 맞춤)
    try:
        os.environ['TZ']='Asia/Seoul'; time.tzset()
    except Exception: pass
    cfg=load_config()
    # 500곳 목표 자동 세팅 — 서버 config.json에 남아있는 옛 값(하루100 등)을 500 세팅으로
    # 한 번만 강제 적용한다. (마커 goal500_applied로 1회만 — 이후 사용자가 바꾼 값은 존중)
    try:
        if cfg.get('goal500_applied')!='v2':   # 버전 올려 새 세팅(배치50·1분주기)을 재적용
            cfg['discover_daily_target']=500
            cfg['discover_query_limit']=500
            cfg['discover_batch']=50
            cfg['auto_pipeline_batch']=10
            cfg['site_goal']=500
            cfg['discover_enabled']=True
            cfg['auto_pipeline_enabled']=True
            cfg['goal500_applied']='v2'
            save_config(cfg)
            # 발굴 커서를 리셋해 게시판찾기부터 다시 발굴한다(업소 홈페이지 대신 실제 게시판).
            try: save_json(DISCO_FILE,{'date':_kst_now().strftime('%Y-%m-%d'),'queries':0,'found':0,'cursor':0,'fcursor':0})
            except Exception: pass
            print('🎯 500곳 목표 세팅 v2 적용 — 발굴 하루500·1분주기·배치50·파이프라인10·자동ON·발굴리셋')
    except Exception as e: print('500 세팅 적용 실패:',e)
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8888'))
    print(f'\n찌라시 마스터 v6 - 정직 발행 모드\nhttp://{host}:{port}\n브랜드: {cfg.get("brand","설정필요")}')
    if os.environ.get('CHIRASHI_PASSWORD') is None and cfg.get('password','admin1234')=='admin1234':
        print('⚠️  기본 비밀번호(admin1234) 사용 중 — 공개 서버라면 반드시 변경하세요!')
    # 재시작 복구: 미완료 작업 큐 복원 → 복구된 작업이 있으면 워커를 띄워 이어서 발행한다.
    # (큐가 비어 있으면 워커를 띄우지 않아 불필요한 자동발행이 없다. 이후 스케줄/원클릭이
    #  큐에 작업을 넣으면 각 호출부가 알아서 start_workers를 부른다.)
    try:
        n=recover_queue()
        if n:
            print(f'↻ 미완료 작업 {n}건 복구됨 → 워커 자동 시작해 이어서 발행')
            try:
                if not wk_active: start_workers(cfg.get('workers',2))
            except Exception as e: print('워커 자동시작 실패:',e)
    except Exception as e: print('복구 건너뜀:',e)
    # ★시작 시 디스크 정리(Errno 28 방지) — 크롬 임시프로파일·백업 누적 삭제.
    try:
        _rm=cleanup_disk()
        if _rm: print(f'🧹 디스크 정리 {_rm}개 (임시·백업)')
    except Exception as e: print('디스크정리 건너뜀:',e)
    # 시작 시 사이트 목록 최신화: 발행 막힌 사이트 자동 탈락
    try:
        dn=reconcile_sites()
        if dn: print(f'🧹 사이트 자동정리 {dn}건 (발행 불가 → 탈락)')
    except Exception as e: print('자동정리 건너뜀:',e)
    # 예약 발행 스케줄러 상시 가동
    try:
        threading.Thread(target=scheduler_loop,name='SCHED',daemon=True).start()
        print('⏰ 스케줄러 시작 (KST 기준)')
    except Exception as e: print('스케줄러 시작 실패:',e)
    # 지연 재시도 루프 / 텔레그램 명령 수신 / 발행글 생존 검증
    for fn,nm in [(retry_loop,'RETRY'),(telegram_loop,'TG'),(verify_loop,'VERIFY'),(member_scheduler_loop,'MSCHED'),(discover_loop,'DISCO'),(pipeline_loop,'PIPELINE'),(publish_loop,'PUBLISH')]:
        try: threading.Thread(target=fn,name=nm,daemon=True).start()
        except Exception as e: print(f'{nm} 시작 실패:',e)
    print('🔁 재시도·📱텔레그램·🔎검증 스레드 시작')
    # 헤드리스 서버(VPS)에서는 브라우저 자동 오픈 안 함
    if host in ('127.0.0.1','localhost') and os.environ.get('OPEN_BROWSER','1')!='0':
        try:
            import webbrowser; webbrowser.open(f'http://{host}:{port}')
        except: pass
    app.run(host=host,port=port,debug=False,threaded=True)

if __name__=='__main__':
    main()
