#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
찌라시 마스터 — PC 자동 발굴 연동 스크립트
=========================================
대표님 PC(또는 노트북)에서 이 파일을 실행하면:
  1) 서버에서 '발굴 검색어'와 '이미 등록/탈락한 도메인'을 받아오고
  2) PC의 검색 API 키로 그 검색어들을 검색해 게시판 URL을 모으고
  3) 이미 아는 도메인은 걸러낸 뒤
  4) 새 URL을 서버(/api/candidates/ingest)로 전송한다.
서버는 받은 URL을 자동으로 검수 → (필요시)가입 → 발행테스트까지 진행한다.

즉 PC는 '발굴(검색)'만 맡고, 판정·발행은 서버가 한다. Brave 검색 쿼터를 서버와
분리해 서버의 402(쿼터초과)를 피하는 것이 목적.

── 준비 (한 번만) ──────────────────────────────────
  • Python 3.9+ 설치
  • pip install requests
  • 아래 CONFIG의 SERVER_TOKEN(찌라시 설정탭의 로그 토큰), 검색 키를 채운다.

── 실행 ────────────────────────────────────────────
  한 번만 발굴:      python pc_discovery.py --once
  계속 발굴(권장):   python pc_discovery.py          (기본 20분 간격 반복)
  간격 바꾸기:       python pc_discovery.py --interval 600   (초)
  한 회 검색어 수:   python pc_discovery.py --max-queries 40

보안: 토큰·API키는 이 파일에 직접 넣지 말고 환경변수로 주는 걸 권장.
  Windows(PowerShell):  $env:CHIRASHI_TOKEN="..."; $env:BRAVE_KEY="..."; python pc_discovery.py
"""
import os, sys, time, argparse, urllib.parse, re, socket

try:
    import requests
except ImportError:
    print("requests 모듈이 없습니다. 먼저:  pip install requests"); sys.exit(1)

# Windows 콘솔에서 한글·특수문자(—, ⚡)가 깨지거나 크래시하지 않게 stdout을 UTF-8로(파이썬 3.7+).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
# https 검증 생략 경고(InsecureRequestWarning) 억제 — 콘솔 깔끔하게.
try:
    requests.packages.urllib3.disable_warnings()
except Exception:
    pass

# ─────────────────────── CONFIG ───────────────────────
# 토큰을 여기 기본값으로 넣어 어떻게 실행하든(배치 없이 py 직접 실행 포함) 바로 동작하게 함.
# (로그조회용 토큰. 서버 설정탭 '로그 토큰'과 동일. 대표님 시스템이라 내장.)
SERVER      = os.environ.get("CHIRASHI_SERVER") or "https://google.twseo.kr"
# 발행노드(pc_node.py)와 동일 규칙 → 같은 기기는 관제실에서 한 카드로 묶임.
NODE_ID     = os.environ.get("PC_NODE_ID") or ("pc-" + socket.gethostname().lower()[:20])
SERVER_TOKEN= os.environ.get("CHIRASHI_TOKEN") or "cae3aaa53d6f3576a1c1f6a258f79129"
# 검색 제공자: 'ddg'(무료·키불필요, 기본) / 'brave' / 'google'. PC는 대표님 실제 IP라
#  DuckDuckGo 무료 검색이 서버보다 훨씬 덜 차단됨 → 키 없이 발굴 가능(대표님 지시 '키없이 무료').
SEARCH_PROVIDER = os.environ.get("PC_SEARCH_PROVIDER", "ddg")
BRAVE_KEY   = os.environ.get("BRAVE_KEY", "")        # brave 사용 시(선택)
_BRAVE_402  = False   # ★Brave 크레딧 소진 감지 시 True → 그 실행 남은 쿼리를 DDG로 전환(2026-09-12)
GOOGLE_KEY  = os.environ.get("GOOGLE_KEY", "")       # google 사용 시(선택)
GOOGLE_CX   = os.environ.get("GOOGLE_CX", "")        # google 사용 시(검색엔진 ID)
# ──────────────────────────────────────────────────────

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) chirashi-pc-discovery/1.0"}

# 게시판/글 URL로 보이는 것만 후보로 (사람이 읽는 링크·홈은 제외).
#  그누보드/Cafe24 board·KBoard 신호. /article/ 는 Cafe24 SEO-URL(끝에 숫자 ID) 형태만
#  인정(news.naver.com/article/… 같은 일반 기사 오탐 방지).
BOARD_HINTS = ("bbs/board.php", "board.php?", "bo_table=", "/board/", "board_no=",
               "wr_id=", "mod=document", "kboard", "write.php")
# 뉴스·블로그 등 게시판 아님이 명확한 도메인은 아예 제외(전송 노이즈 감소)
SKIP_HOSTS = ("naver.com", "daum.net", "tistory.com", "blog.", "news.", "youtube.com",
              "facebook.com", "instagram.com", "wikipedia.org", "google.")


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Windows 콘솔(cp949)이 못 그리는 문자(—, ⚡ 등)는 안전하게 치환해 출력(크래시 방지).
        enc = (sys.stdout.encoding or "utf-8")
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)


def server_get(path, **params):
    params["token"] = SERVER_TOKEN
    r = requests.get(SERVER + path, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def server_post(path, payload):
    url = SERVER + path + "?token=" + urllib.parse.quote(SERVER_TOKEN)
    r = requests.post(url, json=payload, headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()


def brave_search(query, count=10):
    """Brave Web Search API. 결과 URL 리스트 반환."""
    try:
        r = requests.get("https://api.search.brave.com/res/v1/web/search",
                         params={"q": query, "count": count, "country": "KR",
                                 "search_lang": "ko"},
                         headers={"Accept": "application/json",
                                  "X-Subscription-Token": BRAVE_KEY},
                         timeout=25)
        if r.status_code == 402:
            global _BRAVE_402
            _BRAVE_402 = True   # ★크레딧 소진 신호 — run_once가 이번 실행 남은 쿼리를 DDG로 전환(2026-09-12 대표님 실측)
            log("  Brave 402 — 크레딧 소진 → 무료 발굴(DDG)로 전환"); return []
        if r.status_code >= 400:
            log(f"  Brave {r.status_code}: {r.text[:80]}"); return []
        data = r.json()
        return [it.get("url", "") for it in data.get("web", {}).get("results", []) if it.get("url")]
    except Exception as e:
        log(f"  Brave 예외: {str(e)[:80]}"); return []


def google_search(query, num=10):
    """Google Custom Search JSON API. 결과 URL 리스트 반환."""
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params={"key": GOOGLE_KEY, "cx": GOOGLE_CX, "q": query,
                                 "num": min(10, num), "gl": "kr", "hl": "ko"},
                         headers=UA, timeout=25)
        if r.status_code >= 400:
            log(f"  Google {r.status_code}: {r.text[:80]}"); return []
        return [it.get("link", "") for it in r.json().get("items", []) if it.get("link")]
    except Exception as e:
        log(f"  Google 예외: {str(e)[:80]}"); return []


# DDG가 짧은 시간 다량 요청에 403을 낸다. 브라우저처럼 보이는 UA를 돌려쓰고, 403이면
#  백오프 후 lite 엔드포인트로 폴백한다.
_DDG_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

def _ddg_parse(html):
    out = []
    # html 엔드포인트: class="result__a", lite 엔드포인트: 그냥 결과 링크(uddg 래핑)
    for u in re.findall(r'href="([^"]*uddg=[^"]+)"', html):
        m = re.search(r'uddg=([^&]+)', u)
        if m: out.append(urllib.parse.unquote(m.group(1)))
    if not out:
        for u in re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', html):
            m = re.search(r'uddg=([^&]+)', u)
            out.append(urllib.parse.unquote(m.group(1)) if m else u)
    return out

# DDG가 IP를 완전 차단(Max retries exceeded)하면 이번 실행 내내 DDG를 건너뛴다(느린 재시도 낭비 방지).
_ddg_blocked = [False]

def ddg_search(query, _retry=0):
    """DuckDuckGo HTML 검색 — API 키 불필요(무료). 403 시 백오프+lite 폴백.
       연결 자체 실패(Max retries=IP차단)면 즉시 포기+세션차단 표시(Naver로 폴백하도록)."""
    if _ddg_blocked[0]:
        return []
    ua = _DDG_UAS[_retry % len(_DDG_UAS)]
    hdr = {"User-Agent": ua, "Accept-Language": "ko-KR,ko;q=0.9",
           "Referer": "https://duckduckgo.com/", "Accept": "text/html"}
    endpoint = "https://lite.duckduckgo.com/lite/" if _retry >= 1 else "https://html.duckduckgo.com/html/"
    try:
        r = requests.post(endpoint, data={"q": query, "kl": "kr-kr"},
                          headers=hdr, timeout=12, verify=False)
        if r.status_code == 403 and _retry < 1:
            time.sleep(3)                       # 403은 한 번만 백오프(다른 UA·lite로 재시도)
            return ddg_search(query, _retry + 1)
        if r.status_code >= 400:
            return []
        return _ddg_parse(r.text)
    except Exception as e:
        # 연결 실패(ConnectionError/Max retries) = IP 차단 → 이번 실행 DDG 포기(네이버 폴백 없음).
        _ddg_blocked[0] = True
        log(f"  DDG 연결차단 → 이번 실행 발굴 건너뜀 ({str(e)[:50]})")
        return []


_NAVER_SKIP = ("naver.com", "naver.net", "pstatic.net", "nid.naver", "shopping.naver",
               "dict.naver", "map.naver", "blog.naver", "cafe.naver", "search.naver")

def naver_search(query):
    """네이버 웹문서 검색 — API 키 불필요(무료). ★노드는 대표님 실제 IP라 네이버가 안 막힘(서버 DC IP는 막힘).
       ★2026-09-18 개선(대표님 '저장이미지'와 별개, 발굴 0건 규명): where=web(웹문서 탭)이 통합검색보다 6배
       많은 외부 URL 반환(로컬 실측 6→36). 여러 페이지 + &amp; 디코드 + 전체URL dedup + 게시판형 우선.
       실측: finder 쿼리 6개로 게시판형 URL 83개 수확(개선 전 통합검색은 0~소수)."""
    hdr = {"User-Agent": _DDG_UAS[0], "Accept-Language": "ko-KR,ko;q=0.9"}
    def _is_board(u):
        return bool(re.search(r'(bbs/(board|write)\.php|/board/|/article/|mod=document|document_srl|bo_table=)', u))
    seen = set(); board = []; other = []
    for _st in (1, 11, 21):
        try:
            r = requests.get("https://search.naver.com/search.naver",
                             params={"query": query, "where": "web", "start": _st},
                             headers=hdr, timeout=15, verify=False)
            if r.status_code >= 400:
                continue
            for u in re.findall(r'href="(https?://[^"]+)"', r.text):
                u = u.replace("&amp;", "&")
                if any(s in u.lower() for s in _NAVER_SKIP):
                    continue
                if u in seen:
                    continue
                seen.add(u)
                (board if _is_board(u) else other).append(u)
        except Exception as e:
            log(f"  Naver 예외: {str(e)[:60]}")
        time.sleep(0.4)
    return board + other


# ★구글만 발굴(대표님 지시 2026-09-11 '네이버까지 하는데 구글로만 타율↑'):
#  네이버 폴백 제거 — 네이버 전용 게시판은 구글에 안 잡혀 발행해도 타율이 낮다.
#  무료 경로는 DDG만 사용(DDG 결과는 구글 색인과 잘 일치). google/brave는 명시 모드 그대로.
#  naver_search 함수는 보존(수동/추후용)하되 자동 발굴에선 호출하지 않는다.
def do_search(query, prov="ddg"):
    if prov == "google" and GOOGLE_KEY:
        return google_search(query)
    if prov == "brave" and BRAVE_KEY:
        return brave_search(query)
    # 무료 발굴: DDG 먼저(가끔 됨), 막히거나 빈 결과면 네이버 웹문서로 폴백.
    # ★네이버 재활성화(2026-09-18 대표님 '발굴 0건'): DDG는 서버·이 IP에서 봇차단(연결차단/결과0)이 잦고,
    #   Brave는 402(크레딧 소진)라 무료 경로가 사실상 죽어 신규 후보 0건이었음. 노드는 대표님 실제 IP라
    #   네이버가 안 막히고, 개선된 where=web가 실제 게시판을 대량 반환(로컬 실측 6쿼리 83개). DDG만 쓰던
    #   옛 정책(네이버 폴백 제거)은 DDG가 살아있을 때 얘기 — 지금은 네이버가 유일한 무료 산출원.
    r = ddg_search(query)
    if r:
        return r
    return naver_search(query)


def domain_of(url):
    try:
        net = urllib.parse.urlsplit(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def looks_like_board(url):
    u = (url or "").lower()
    if any(h in u for h in SKIP_HOSTS):
        return False
    if any(h in u for h in BOARD_HINTS):
        return True
    # Cafe24 SEO-URL: /article/{게시판명}/{숫자}/  (끝 숫자 ID가 있어야 게시판 글)
    if re.search(r'/article/[^/]+/\d+/?', u):
        return True
    return False


# ============================================================================
# ★crt.sh 신규 게시판 발굴(2026-09-18 대표님 '신규 그누보드/카페24 도메인 추적'):
#   검색엔진 크레딧 없이 무료로 신규 도메인 대량수집. crt.sh(인증서 투명성 로그)에서 한국 TLD 도메인을
#   훑고, 각 도메인에 그누보드/카페24 게시판이 실제 있는지 노드(집IP)가 확인 → 있는 것만 서버 ingest.
#   실측(2026-09-18): .or.kr(협회·비영리) 그누보드 수율 ~13%(3582도메인), cafe24 쇼핑몰은 ~0%(게시판 없음),
#   .ac.kr(대학)도 0%(자체CMS). → .or.kr>.co.kr>.kr 우선, .ac.kr·cafe24쇼핑몰 제외.
#   crt.sh는 TLD당 1회 조회가 무거움(~1MB)이라 여러 발굴루프에 1회만(_CRTSH_EVERY), TLD는 커서로 순환.
_CRTSH_TLDS = [".or.kr", ".co.kr", ".kr"]   # 게시판 수율 순(대학 .ac.kr 제외, .go.kr은 crt.sh 404+정부기관이라 제외)
_CRTSH_CURSOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pc_discovery_crtsh")
_CRTSH_SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pc_discovery_crtsh_seen")

def _crtsh_read_cursor():
    try: return int(open(_CRTSH_CURSOR_FILE, encoding="utf-8").read().strip() or "0")
    except Exception: return 0

def _crtsh_write_cursor(v):
    try: open(_CRTSH_CURSOR_FILE, "w", encoding="utf-8").write(str(int(v)))
    except Exception: pass

def _crtsh_load_seen():
    """이미 crt.sh 판정한 도메인(중복 재확인 방지). 최대 5만개 유지."""
    try:
        return set(x.strip() for x in open(_CRTSH_SEEN_FILE, encoding="utf-8").read().splitlines() if x.strip())
    except Exception:
        return set()

def _crtsh_save_seen(seen):
    try:
        s = list(seen)[-50000:]   # 상한
        open(_CRTSH_SEEN_FILE, "w", encoding="utf-8").write("\n".join(s))
    except Exception: pass

def _crtsh_domains(tld):
    """crt.sh에서 해당 TLD 도메인 목록(고유·대표도메인만). 실패 시 []."""
    try:
        r = requests.get(f"https://crt.sh/?q=%25{tld}&output=json", headers=UA, timeout=60, verify=False)
        if r.status_code != 200 or not (r.text or "").strip():
            log(f"  crt.sh {tld} HTTP {r.status_code}"); return []
        import json as _json
        data = _json.loads(r.text)
        doms = set()
        for e in data:
            for d in str(e.get("name_value", "")).split("\n"):
                d = d.strip().lower()
                # 대표 도메인만: 와일드카드·모바일미러(m.) 제외, 서브도메인 깊이 제한
                if d.endswith(tld) and not d.startswith("*") and not d.startswith("m.") and d.count(".") <= 3:
                    doms.add(d)
        return list(doms)
    except Exception as e:
        log(f"  crt.sh {tld} 예외: {str(e)[:80]}"); return []

_ERR_WORDS = ("오류안내", "존재하지 않", "없는 게시판", "페이지를 찾을 수 없", "잘못된 접근", "비정상적인", "권한이 없")
_BO_TRY = ("free", "qa", "notice", "community", "board", "bbs", "sboard", "gallery")

def _is_real_board(body):
    """게시판 페이지 HTML이 '실제 존재하는 게시판'인지 판정(오류페이지·홈리다이렉트 제외).
       ★실측 판별기준(2026-09-18): 실제게시판=wr_id링크·bo_table링크 다수+write.php 버튼,
       오류/홈페이지=전부 0. '오류안내' 등 단어 있으면 제외."""
    if any(w in body for w in _ERR_WORDS):
        return False
    wr = len(re.findall(r'wr_id=\d+', body))
    bo = len(re.findall(r'bo_table=\w+', body))
    has_write = "write.php" in body
    # 실제 게시판: 글이 있거나(wr_id≥1), 게시판 네비(bo_table≥3)+글쓰기버튼이 있어야 함(빈 게시판도 통과).
    return (wr >= 1) or (bo >= 3 and has_write)

# ★독점 가능 게시판 판정(2026-09-18 대표님 '내가 독점할 수 있는 사이트 = 키워드 안 겹쳐 상위노출'):
#   실측 — 경쟁없는 방치 게시판에 발행하면 그 글이 그 게시판 검색 최상단(일원역하퍼 점령 공식). 지표:
#   유흥경쟁글 흔적(하이퍼블릭·010·출장 등)=0이면 우리가 유일 / 글 적음·오래방치=방치 → 독점점수.
_COMPETE_WORDS = ('하이퍼블릭','풀싸롱','셔츠룸','쓰리노','텐프로','쩜오','유흥','가라오케','키스방','안마',
                  '출장마사지','출장안마','레깅스룸','미러룸','란제리','호빠','노래빠','O1O','o1o')
def _monopoly_score(body):
    """게시판 HTML로 '독점 가능 점수'(높을수록 경쟁없는 방치판=상위노출 유리). 0~6."""
    wr = len(re.findall(r'wr_id=\d+', body))
    compete = sum(body.count(k) for k in _COMPETE_WORDS)   # 유흥 경쟁글 흔적
    empty = ('게시물이 없' in body or '등록된 글이 없' in body or wr == 0)
    # 최근글 연도(방치 판정): 본문에 20xx 연도 최대값
    yrs = [int(y) for y in re.findall(r'20(\d{2})', body)]
    recent_yr = max(yrs) if yrs else 0   # 두자리(24=2024)
    score = 0
    if compete == 0: score += 3          # 유흥 경쟁 흔적 0 = 우리가 유일(핵심)
    elif compete <= 3: score += 1
    else: score -= 2                     # 이미 업자글 도배 = 경쟁판(감점)
    if empty or wr <= 3: score += 2      # 빈/방치 게시판
    if 0 < recent_yr <= 24: score += 1   # 최근글 2024년 이하(1년+ 방치)
    return max(0, score)

def _has_board(dom):
    """도메인에 '실제 존재하는' 그누보드 게시판이 있는지 확인. 있으면 게시판 URL 반환, 없으면 ''.
       ★cafe24 판정 제거(수율0)·오판정 정밀화(2026-09-18 실측): 단순 'bo_table 문자열 존재'로 판정하면
       오류안내·홈리다이렉트 페이지도 게시판으로 오판(gnrehab·openweb) → 서버 검수에서 다 탈락(신규0).
       실제 게시판 구조(wr_id 글링크/write.php/게시판네비)를 확인하고, bo_table을 free 외 여러개 시도."""
    for scheme in ("https", "http"):
        base = f"{scheme}://{dom}"
        # 1) 그누보드 사이트인지 빠른 확인(홈에 gnuboard generator·bbs 링크) — 아니면 이 도메인 스킵.
        try:
            rh = requests.get(base, headers=UA, timeout=7, verify=False, allow_redirects=True)
            home = (rh.text or "")[:30000]
            if rh.status_code >= 400:
                continue
            if not ("/bbs/board.php" in home or "gnuboard" in home.lower() or "bo_table=" in home):
                continue   # 그누보드 흔적 없음 → 다음 scheme/도메인
        except Exception:
            continue
        # 2) 실제 게시판 찾기: 흔한 bo_table 여러 개 시도, 실제 게시판 구조인 첫 것 반환.
        #    ★독점점수 함께 반환(2026-09-18): 경쟁없는 방치 게시판일수록 높은 점수 → 서버가 우선 발행.
        for bo in _BO_TRY:
            try:
                r = requests.get(f"{base}/bbs/board.php?bo_table={bo}", headers=UA, timeout=7, verify=False, allow_redirects=True)
                body = (r.text or "")[:30000]
                if r.status_code < 400 and _is_real_board(body):
                    return f"{base}/bbs/board.php?bo_table={bo}", _monopoly_score(body)
            except Exception:
                pass
        return "", 0   # 그누보드 사이트지만 열린 게시판 못 찾음
    return "", 0

def crtsh_discover(skip_domains, max_check=60):
    """crt.sh 1개 TLD를 훑어 신규 도메인 중 게시판 있는 것만 서버로 ingest.
       skip_domains: 서버가 아는 도메인(중복 제외). max_check: 이번 회 게시판 확인할 신규 도메인 수(부하 관리)."""
    tld = _CRTSH_TLDS[_crtsh_read_cursor() % len(_CRTSH_TLDS)]
    _crtsh_write_cursor(_crtsh_read_cursor() + 1)
    log(f"[crt.sh 발굴] {tld} 도메인 수집 중…")
    doms = _crtsh_domains(tld)
    if not doms:
        return
    seen = _crtsh_load_seen()
    # 아직 판정 안 한 신규 도메인만(서버 known + 로컬 seen 제외)
    fresh = [d for d in doms if d not in skip_domains and d not in seen and d.replace("www.", "") not in skip_domains]
    log(f"[crt.sh 발굴] {tld} 총 {len(doms)}개 · 미판정 {len(fresh)}개 → 이번 회 {min(len(fresh), max_check)}개 게시판 확인")
    found = []   # (url, monopoly_score)
    checked = 0
    for d in fresh[:max_check]:
        seen.add(d)
        bu, score = _has_board(d)
        if bu:
            found.append((bu, score))
            log(f"  ✅게시판 발견(독점점수 {score}): {bu}")
        checked += 1
        time.sleep(0.5)   # 노드 IP 부하·차단 방지
    _crtsh_save_seen(seen)
    # ★독점점수 높은 순 정렬(2026-09-18 대표님 '독점 가능 사이트 우선'): 경쟁없는 방치판을 먼저 전송·발행.
    found.sort(key=lambda x: x[1], reverse=True)
    board_urls = [u for u, sc in found]
    if found:
        _hi = sum(1 for u, sc in found if sc >= 4)
        log(f"[crt.sh 발굴] 게시판 {len(found)}개(독점가능 고득점 {_hi}개) — 점수순 전송")
        # ingest가 검수·발행테스트를 백그라운드로 돌리지만 응답이 늦을 수 있어 넉넉한 타임아웃.
        # 100개씩 나눠 전송(서버 상한). 타임아웃 나도 서버는 URL을 받았을 수 있으니 다음 회 seen으로 중복 방지됨.
        for _j in range(0, len(board_urls), 100):
            _chunk = board_urls[_j:_j + 100]
            try:
                _url = SERVER + "/api/candidates/ingest?token=" + urllib.parse.quote(SERVER_TOKEN)
                r = requests.post(_url, json={"urls": _chunk, "node_id": NODE_ID}, headers=UA, timeout=120)
                res = r.json() if r.status_code < 400 else {}
                log(f"[crt.sh 발굴] 게시판 {len(_chunk)}개 → 서버 전송(신규 {res.get('added') if res.get('ok') else '?'})")
            except Exception as e:
                log(f"[crt.sh 발굴] 전송 예외(서버는 수신했을 수 있음): {str(e)[:70]}")
    else:
        log(f"[crt.sh 발굴] {tld} 이번 회 게시판 발견 0 (확인 {checked}개)")

# ★검색어 로테이션 커서(대표님 지시): 매 실행마다 400개 검색어의 '다음 묶음'을 돌려
#  같은 검색어 반복으로 신규가 적던 문제 해결. 커서를 로컬 파일에 저장해 이어간다.
_CURSOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pc_discovery_cursor")

def _read_cursor():
    try:
        return int(open(_CURSOR_FILE, encoding="utf-8").read().strip() or "0")
    except Exception:
        return 0

def _write_cursor(v):
    try:
        open(_CURSOR_FILE, "w", encoding="utf-8").write(str(int(v)))
    except Exception:
        pass


def run_once(max_queries):
    global BRAVE_KEY
    if not SERVER_TOKEN:
        log("SERVER_TOKEN이 비어 있습니다. 환경변수 CHIRASHI_TOKEN 또는 파일 CONFIG를 채우세요.")
        return
    log("서버에서 검색어·아는 도메인 받는 중…")
    try:
        info = server_get("/api/discovery/queries")
    except Exception as e:
        log(f"서버 쿼리 조회 실패: {str(e)[:100]}"); return
    if not info.get("ok"):
        log(f"서버 응답 오류: {info.get('error')}"); return
    # ★PC도 Brave로 발굴(대표님 지시 2026-09-11 DDG차단): 서버가 내려준 provider·brave_key를 우선 사용.
    #   서버가 brave 키를 주면 그 키로 Brave 검색(로컬 env BRAVE_KEY보다 우선). DDG는 안 씀.
    srv_prov = (info.get("provider") or "").lower()
    srv_bk = (info.get("brave_key") or "").strip()
    if srv_bk: BRAVE_KEY = srv_bk
    prov = srv_prov or SEARCH_PROVIDER
    if prov == "brave" and not BRAVE_KEY: prov = "ddg"     # 키 없으면 최후에만 ddg
    if prov == "google" and not (GOOGLE_KEY and GOOGLE_CX): prov = "ddg"
    log(f"검색 방식: {prov}{'(서버 Brave키)' if (prov=='brave' and srv_bk) else ('(무료·키불필요)' if prov=='ddg' else '')}")
    all_q = info.get("queries", [])
    # ★로테이션: 커서 위치부터 max_queries개를 잘라 쓰고, 커서를 그만큼 전진(끝이면 처음으로).
    total = len(all_q)
    if total == 0:
        log("서버에서 받은 검색어가 없습니다."); return
    cur = _read_cursor() % total
    if cur + max_queries <= total:
        queries = all_q[cur:cur + max_queries]
    else:   # 끝을 넘으면 앞으로 감아서 채움
        queries = all_q[cur:] + all_q[:(cur + max_queries) - total]
    _write_cursor((cur + max_queries) % total)
    skip = set(info.get("known_domains", [])) | set(info.get("rejected_domains", []))
    log(f"검색어 {len(queries)}개(전체 {total}개 중 {cur+1}~{cur+len(queries)}) · 스킵 {len(skip)}개 · {prov}")

    found = {}     # domain → url (도메인당 1개만; 서버가 게시판 축약)
    global _BRAVE_402; _BRAVE_402=False
    for i, q in enumerate(queries, 1):
        # ★Brave 크레딧 소진(402) 감지되면 즉시 DDG로 전환해 이번 실행을 살린다(대표님 실측 2026-09-12).
        if prov == "brave" and _BRAVE_402:
            prov = "ddg"; log("  → Brave 크레딧 소진 감지: 이번 실행 DDG(무료)로 전환")
        urls = do_search(q, prov)
        new_here = 0
        for u in urls:
            if not looks_like_board(u):
                continue
            d = domain_of(u)
            if not d or d in skip or d in found:
                continue
            found[d] = u; new_here += 1
        if new_here:
            log(f"  [{i}/{len(queries)}] +{new_here}  «{q[:36]}»")
        # ★DDG 봇차단(403) 방지: 요청 간격을 넉넉히(prov=ddg면 2.5s, 키검색은 1.1s).
        #   짧게 연속 요청하면 DDG가 403을 낸다(대표님 화면 실측).
        time.sleep(2.5 if prov == "ddg" else 1.1)

    urls = list(found.values())
    if not urls:
        log("새 게시판 URL 없음(이미 다 알고 있거나 검색결과 없음).")
        try: server_post("/api/candidates/ingest", {"urls": [], "node_id": NODE_ID})  # 관제실 하트비트만
        except Exception: pass
        return
    log(f"새 URL {len(urls)}개 → 서버로 전송…")
    # 서버 ingest는 1회 100개 상한 → 100개씩 나눠 보낸다
    sent = 0
    for j in range(0, len(urls), 100):
        chunk = urls[j:j + 100]
        try:
            res = server_post("/api/candidates/ingest", {"urls": chunk, "node_id": NODE_ID})
            if res.get("ok"):
                sent += res.get("received", len(chunk))
                log(f"  전송 {res.get('received')}개(신규 {res.get('added')}개) — 서버가 검수·발행테스트 시작")
            else:
                log(f"  전송 실패: {res.get('error')}")
        except Exception as e:
            log(f"  전송 예외: {str(e)[:100]}")
    log(f"완료 — 총 {sent}개 서버로 넘김.")


def _server_skip_domains():
    """서버가 아는 도메인(known+rejected) — crt.sh 신규판정 중복 제외용."""
    try:
        info = server_get("/api/discovery/queries")
        return set(info.get("known_domains", [])) | set(info.get("rejected_domains", []))
    except Exception:
        return set()

def main():
    ap = argparse.ArgumentParser(description="찌라시 PC 자동 발굴 연동")
    ap.add_argument("--once", action="store_true", help="한 번만 실행하고 종료")
    ap.add_argument("--interval", type=int, default=300, help="반복 간격(초, 기본 300=5분)")
    ap.add_argument("--max-queries", type=int, default=30, help="한 회당 검색어 수(기본 30)")
    ap.add_argument("--crtsh-every", type=int, default=4, help="crt.sh 발굴을 몇 발굴루프마다 1회(기본 4=약20분마다)")
    a = ap.parse_args()
    log(f"찌라시 PC 발굴 시작 — 서버 {SERVER}")
    if a.once:
        run_once(a.max_queries)
        try: crtsh_discover(_server_skip_domains())   # --once면 crt.sh도 1회
        except Exception as e: log(f"crt.sh 발굴 예외: {str(e)[:100]}")
        return
    _loop = 0
    while True:
        try:
            run_once(a.max_queries)
        except KeyboardInterrupt:
            log("중단됨."); break
        except Exception as e:
            log(f"루프 예외(계속): {str(e)[:120]}")
        # ★crt.sh 신규발굴: 무거우니 _crtsh_every 루프마다 1회(검색엔진 크레딧 없이 무료 신규공급).
        _loop += 1
        if a.crtsh_every > 0 and _loop % a.crtsh_every == 0:
            try:
                crtsh_discover(_server_skip_domains())
            except Exception as e:
                log(f"crt.sh 발굴 예외(계속): {str(e)[:100]}")
        log(f"다음 발굴까지 {a.interval}초 대기…  (Ctrl+C로 종료)")
        try:
            time.sleep(a.interval)
        except KeyboardInterrupt:
            log("중단됨."); break


if __name__ == "__main__":
    main()
