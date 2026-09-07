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
import os, sys, time, argparse, urllib.parse, re

try:
    import requests
except ImportError:
    print("requests 모듈이 없습니다. 먼저:  pip install requests"); sys.exit(1)

# ─────────────────────── CONFIG ───────────────────────
SERVER      = os.environ.get("CHIRASHI_SERVER", "https://google.twseo.kr")
SERVER_TOKEN= os.environ.get("CHIRASHI_TOKEN", "")   # 찌라시 설정탭 '로그 토큰'
# 검색 제공자: 'brave' 또는 'google'. PC에서 쓸 키를 넣는다(서버와 별도 쿼터).
SEARCH_PROVIDER = os.environ.get("PC_SEARCH_PROVIDER", "brave")
BRAVE_KEY   = os.environ.get("BRAVE_KEY", "")        # brave 사용 시
GOOGLE_KEY  = os.environ.get("GOOGLE_KEY", "")       # google 사용 시
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
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


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


def do_search(query):
    if SEARCH_PROVIDER == "google":
        return google_search(query)
    return brave_search(query)


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


def run_once(max_queries):
    if not SERVER_TOKEN:
        log("SERVER_TOKEN이 비어 있습니다. 환경변수 CHIRASHI_TOKEN 또는 파일 CONFIG를 채우세요.")
        return
    key_ok = (SEARCH_PROVIDER == "brave" and BRAVE_KEY) or \
             (SEARCH_PROVIDER == "google" and GOOGLE_KEY and GOOGLE_CX)
    if not key_ok:
        log(f"검색 키가 없습니다({SEARCH_PROVIDER}). BRAVE_KEY 또는 GOOGLE_KEY/GOOGLE_CX를 채우세요.")
        return
    log("서버에서 검색어·아는 도메인 받는 중…")
    try:
        info = server_get("/api/discovery/queries")
    except Exception as e:
        log(f"서버 쿼리 조회 실패: {str(e)[:100]}"); return
    if not info.get("ok"):
        log(f"서버 응답 오류: {info.get('error')}"); return
    queries = info.get("queries", [])[:max_queries]
    skip = set(info.get("known_domains", [])) | set(info.get("rejected_domains", []))
    log(f"검색어 {len(queries)}개 · 스킵 도메인 {len(skip)}개 · 제공자 {SEARCH_PROVIDER}")

    found = {}     # domain → url (도메인당 1개만; 서버가 게시판 축약)
    for i, q in enumerate(queries, 1):
        urls = do_search(q)
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
        time.sleep(1.1)   # 검색 API 예의상 간격(레이트리밋 방지)

    urls = list(found.values())
    if not urls:
        log("새 게시판 URL 없음(이미 다 알고 있거나 검색결과 없음).")
        return
    log(f"새 URL {len(urls)}개 → 서버로 전송…")
    # 서버 ingest는 1회 100개 상한 → 100개씩 나눠 보낸다
    sent = 0
    for j in range(0, len(urls), 100):
        chunk = urls[j:j + 100]
        try:
            res = server_post("/api/candidates/ingest", {"urls": chunk})
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
    ap.add_argument("--interval", type=int, default=1200, help="반복 간격(초, 기본 1200=20분)")
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
