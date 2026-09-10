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
            log("  Brave 402 — 쿼터 초과(잠시 후/내일 재시도)"); return []
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
        # 연결 실패(ConnectionError/Max retries) = IP 차단 → 이번 실행 DDG 포기, Naver로.
        _ddg_blocked[0] = True
        log(f"  DDG 연결차단 → 이번 실행은 Naver로 전환 ({str(e)[:50]})")
        return []


_NAVER_SKIP = ("naver.com", "naver.net", "pstatic.net", "nid.naver", "shopping.naver",
               "dict.naver", "map.naver", "blog.naver", "cafe.naver", "search.naver")

def naver_search(query):
    """네이버 통합검색 — API 키 불필요(무료). DDG 차단 시 폴백. 네이버 내부링크는 제외.
       실측(2026-09-08): '010 홍보 비회원' 등에서 실제 그누보드 게시판 다수 반환."""
    hdr = {"User-Agent": _DDG_UAS[0], "Accept-Language": "ko-KR,ko;q=0.9"}
    try:
        r = requests.get("https://search.naver.com/search.naver",
                         params={"query": query}, headers=hdr, timeout=15, verify=False)
        if r.status_code >= 400:
            log(f"  Naver {r.status_code}"); return []
        out = []
        for u in re.findall(r'href="(https?://[^"]+)"', r.text):
            u = u.replace("&amp;", "&")
            if any(s in u.lower() for s in _NAVER_SKIP):
                continue
            out.append(u)
        return out
    except Exception as e:
        log(f"  Naver 예외: {str(e)[:60]}"); return []


# ★검색엔진 자동분산(대표님 지시): DDG가 IP차단(Max retries)돼도 Naver로 발굴 지속.
#  ddg 모드면 [ddg → 실패 시 naver] 순으로 시도. 결과 있으면 즉시 반환.
def do_search(query, prov="ddg"):
    if prov == "google" and GOOGLE_KEY:
        return google_search(query)
    if prov == "brave" and BRAVE_KEY:
        return brave_search(query)
    # 무료 분산: DDG 먼저, 비거나 실패하면 Naver 폴백
    res = ddg_search(query)
    if res:
        return res
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
    if not SERVER_TOKEN:
        log("SERVER_TOKEN이 비어 있습니다. 환경변수 CHIRASHI_TOKEN 또는 파일 CONFIG를 채우세요.")
        return
    # ddg(무료)는 키 불필요. brave/google을 명시했는데 키가 없으면 자동으로 ddg로 폴백.
    prov = SEARCH_PROVIDER
    if prov == "brave" and not BRAVE_KEY: prov = "ddg"
    if prov == "google" and not (GOOGLE_KEY and GOOGLE_CX): prov = "ddg"
    log(f"검색 방식: {prov}{'(무료·키불필요)' if prov=='ddg' else ''}")
    log("서버에서 검색어·아는 도메인 받는 중…")
    try:
        info = server_get("/api/discovery/queries")
    except Exception as e:
        log(f"서버 쿼리 조회 실패: {str(e)[:100]}"); return
    if not info.get("ok"):
        log(f"서버 응답 오류: {info.get('error')}"); return
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
    for i, q in enumerate(queries, 1):
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


def main():
    ap = argparse.ArgumentParser(description="찌라시 PC 자동 발굴 연동")
    ap.add_argument("--once", action="store_true", help="한 번만 실행하고 종료")
    ap.add_argument("--interval", type=int, default=300, help="반복 간격(초, 기본 300=5분)")
    ap.add_argument("--max-queries", type=int, default=30, help="한 회당 검색어 수(기본 30)")
    a = ap.parse_args()
    log(f"찌라시 PC 발굴 시작 — 서버 {SERVER}")
    if a.once:
        run_once(a.max_queries); return
    while True:
        try:
            run_once(a.max_queries)
        except KeyboardInterrupt:
            log("중단됨."); break
        except Exception as e:
            log(f"루프 예외(계속): {str(e)[:120]}")
        log(f"다음 발굴까지 {a.interval}초 대기…  (Ctrl+C로 종료)")
        try:
            time.sleep(a.interval)
        except KeyboardInterrupt:
            log("중단됨."); break


if __name__ == "__main__":
    main()
