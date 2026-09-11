#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
찌라시 마스터 — PC 발행노드 (가입·발행 오프로드)
================================================
대표님 PC(강력·상시)에서 이 파일을 실행하면:
  1) 서버에서 '가입·발행 대기'(ready) 후보를 원자적으로 claim(잠금)해 받아오고
  2) PC의 크롬으로 자동가입 + 실제 글1건 발행테스트를 하고(app.py 엔진 그대로 재사용)
  3) 결과를 서버(/api/pipeline/report)로 회신 → 서버가 사이트등록·이력·상태를 반영한다.
서버의 backlog를 PC 메모리로 병렬 소진하는 것이 목적. PC를 꺼도 서버는 그대로 동작한다
(claim은 TTL 후 서버가 자동 회수).

★코드 복제 없음: 발행/가입 엔진은 같은 폴더의 app.py를 import해서 그대로 쓴다.
  (app.py는 import해도 서버가 안 뜬다 — 모든 루프는 main() 안, __main__ 가드로 보호됨.)
  GPT·2captcha 등 API 키는 서버가 내려주지 않고, PC 로컬 data/config.json 의 키를 쓴다.

── 준비 (한 번만) ──────────────────────────────────
  • 이 파일을 app.py 와 같은 폴더(chirashi/)에서 실행
  • pip install -r requirements.txt  (selenium 등 — 서버와 동일)
  • 크롬 설치되어 있어야 함(app.py get_driver 가 자동 드라이버 사용)
  • PC 로컬 data/config.json 에 GPT 키 등이 서버와 동일하게 있어야 발행 생성 성공

── 실행 ────────────────────────────────────────────
  기본(권장, 계속):   python pc_node.py
  한 배치만:          python pc_node.py --once
  동시 발행수 지정:    python pc_node.py --workers 6
  claim 간격(초):      python pc_node.py --idle 30

보안: 토큰은 환경변수 권장.  Windows(PowerShell):
  $env:CHIRASHI_TOKEN="..."; python pc_node.py
"""
import os, sys, time, argparse, threading

# Windows 콘솔 cp949 크래시 방지(이모지·— 등) — app.py import 전에.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import requests
except ImportError:
    print("requests 모듈이 없습니다. 먼저:  pip install requests"); sys.exit(1)
try:
    requests.packages.urllib3.disable_warnings()
except Exception:
    pass

# ─────────────────────── CONFIG ───────────────────────
SERVER       = os.environ.get("CHIRASHI_SERVER") or "https://google.twseo.kr"
SERVER_TOKEN = os.environ.get("CHIRASHI_TOKEN") or "cae3aaa53d6f3576a1c1f6a258f79129"
import socket
NODE_ID      = os.environ.get("PC_NODE_ID") or ("pc-" + socket.gethostname().lower()[:20])
UA           = {"User-Agent": "chirashi-pc-node/1.0"}
# ──────────────────────────────────────────────────────


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


# ── app.py 엔진 import (서버 로직 그대로 재사용, 서버는 안 뜸) ──
log("app.py 엔진 로딩 중...")
try:
    import app  # 같은 폴더의 app.py
except Exception as e:
    log(f"app.py import 실패: {e}")
    log("이 파일은 반드시 app.py와 같은 폴더(chirashi/)에서 실행하세요.")
    sys.exit(1)


# ★서버가 내려주는 글 생성 LLM 설정(대표님 2026-09-11 '비용 절감'): 서버 설정탭(제공자·NVIDIA 키·모델)이
#   노드 로컬 config보다 우선. claim 응답의 llm 블록을 보관해 두고 발행 직전 cfg에 덮어쓴다.
#   (노드가 로컬 config만 보면 서버에서 NVIDIA를 켜도 노드는 옛 OpenAI 키로 가던 빈틈.)
_SERVER_LLM = {}

def _apply_llm(cfg):
    """서버 LLM 설정을 cfg에 반영. 서버가 안 주면(옛 서버) 로컬 값 유지."""
    try:
        s = _SERVER_LLM
        if s.get("provider"): cfg["llm_provider"] = s["provider"]
        if s.get("nvidia_api_key"): cfg["nvidia_api_key"] = s["nvidia_api_key"]
        if s.get("nvidia_model"): cfg["nvidia_model"] = s["nvidia_model"]
        if s.get("openrouter_api_key"): cfg["openrouter_api_key"] = s["openrouter_api_key"]
        if s.get("openrouter_model"): cfg["openrouter_model"] = s["openrouter_model"]
    except Exception:
        pass
    return cfg

def _claim(n):
    """서버에서 후보 n개를 claim(잠금)해 받아온다. 반환: 후보 리스트(빈 리스트면 대기)."""
    try:
        r = requests.post(f"{SERVER}/api/pipeline/claim?token={SERVER_TOKEN}",
                          json={"node_id": NODE_ID, "n": n}, headers=UA, timeout=30, verify=False)
        if r.status_code != 200:
            log(f"claim 실패 HTTP {r.status_code}"); return []
        d = r.json()
        if isinstance(d.get("llm"), dict): _SERVER_LLM.update(d["llm"])   # 서버 LLM 설정 보관
        return d.get("candidates") or []
    except Exception as e:
        log(f"claim 오류: {e}"); return []


def _report(results):
    """가입·발행 결과를 서버에 회신 → 서버가 사이트등록·이력·claim해제 반영.
       ★502/타임아웃(서버 재시작·게이트웨이 순간오류)에도 결과가 유실되지 않게 재시도(최대 5회, 지수백오프)."""
    if not results:
        return
    for attempt in range(1, 6):
        try:
            r = requests.post(f"{SERVER}/api/pipeline/report?token={SERVER_TOKEN}",
                              json={"node_id": NODE_ID, "results": results}, headers=UA, timeout=60, verify=False)
            if r.status_code == 200:
                d = r.json()
                log(f"회신 완료: 반영 {d.get('applied',0)} · 발행가능 등록 {d.get('registered',0)}")
                return
            log(f"report 실패 HTTP {r.status_code} (시도 {attempt}/5)")
        except Exception as e:
            log(f"report 오류: {e} (시도 {attempt}/5)")
        if attempt < 5:
            time.sleep(min(5 * attempt, 20))   # 5·10·15·20초 백오프
    log("report 최종 실패 — 결과 유실(다음 배치에서 서버가 만료 회수 후 재처리)")


def _claim_sites(n):
    """★등록 Cafe24 사이트(집 IP 필요)를 서버에서 claim. 반환: 사이트 리스트(비번 포함)."""
    try:
        r = requests.post(f"{SERVER}/api/pipeline/claim-sites?token={SERVER_TOKEN}",
                          json={"node_id": NODE_ID, "n": n}, headers=UA, timeout=30, verify=False)
        if r.status_code != 200:
            return []
        d = r.json() or {}
        if isinstance(d.get("llm"), dict): _SERVER_LLM.update(d["llm"])   # 서버 LLM 설정 보관
        return d.get("sites") or []
    except Exception:
        return []


def _report_sites(results):
    """등록 Cafe24 사이트 로컬발행 결과 회신(재시도 포함)."""
    if not results: return
    for attempt in range(1, 4):
        try:
            r = requests.post(f"{SERVER}/api/pipeline/report-site?token={SERVER_TOKEN}",
                              json={"node_id": NODE_ID, "results": results}, headers=UA, timeout=60, verify=False)
            if r.status_code == 200:
                d = r.json(); log(f"Cafe24 회신: 반영 {d.get('applied',0)} · 발행성공 {d.get('passed',0)}"); return
        except Exception:
            pass
        if attempt < 3: time.sleep(5 * attempt)


def _process_site(s, cfg):
    """등록 Cafe24 사이트 1개를 로컬크롬으로 로그인·발행(app.py cafe24 엔진 경유). 반환: report용 dict.
       ★비번은 site dict 안에서만 쓰고 로그엔 남기지 않는다."""
    _apply_llm(cfg)   # 서버 설정탭의 LLM(제공자·키·모델)을 로컬 config보다 우선 적용
    sid = s.get("id"); base = str(s.get("site_url") or "").rstrip("/")
    name = s.get("name") or base
    site = {"id": sid or "pcsite", "site_url": base, "platform": "cafe24",
            "bo_table": s.get("bo_table") or "1", "name": name,
            "mb_id": s.get("mb_id", ""), "mb_pass": s.get("mb_pass", ""),
            "write_entry_url": s.get("write_entry_url", ""), "article_board_name": s.get("article_board_name", "")}
    res = {"site_id": sid, "ok": False, "result_url": "", "msg": ""}
    try:
        pool = app.collect_all_keywords()
        kw = app.pick_keywords(pool, cfg) if pool else {"지역": "인천", "서비스": "노래방", "브랜드": cfg.get("brand", "") or "테스트"}
        html, title = app.generate_article(kw, cfg, unique=True)
        ok, msg = app.do_post(site, title, html, skip_login=False)   # 저장 계정으로 로그인 발행
        if ok and str(msg).startswith(("http://", "https://")):
            res.update({"ok": True, "result_url": msg}); log(f"[Cafe24 발행성공] {name} → {msg}")
        else:
            res["msg"] = str(msg)[:120]; log(f"[Cafe24 발행실패] {name} — {str(msg)[:80]}")
    except Exception as e:
        res["msg"] = f"예외:{str(e)[:80]}"; log(f"[Cafe24 예외] {name} — {str(e)[:70]}")
    finally:
        try: app.reset_driver()
        except Exception: pass
    return res


def _process_one(cand, cfg, pool):
    """후보 1개를 PC 크롬으로 가입·발행테스트(app.py 엔진). auto_pipeline_once의 _proc 로직과 동일.
       반환: report용 result dict."""
    _apply_llm(cfg)   # 서버 설정탭의 LLM(제공자·키·모델)을 로컬 config보다 우선 적용
    cid = cand.get("id")
    name = cand.get("board_name") or cand.get("domain") or (cand.get("url", "") or "")[:30]
    # 발행 엔진이 기대하는 site dict를 후보로 구성(_proc와 동일)
    import re as _re
    _curl = cand.get("url", "")
    m = _re.match(r"(https?://[^/]+)", _curl); base = m.group(1) if m else _curl
    tmp = {"id": "cand_" + str(cid), "site_url": base, "platform": cand.get("platform", "gnuboard"),
           "bo_table": cand.get("bo_table") or "free", "name": name, "mb_id": "", "mb_pass": ""}
    # ★Cafe24: 처음 발굴한 글목록 URL을 진입점으로(대표님 지시 2026-09-11). write.html 추측→404 방지.
    if cand.get("platform") == "cafe24":
        _am = _re.search(r"/(?:article|board)/([^/]+)/(\d+)/", _curl)
        if _am and _am.group(1).lower() not in ("write", "list", "read", "view", "product", "free"):
            tmp["article_board_name"] = _am.group(1); tmp["bo_table"] = _am.group(2)
            tmp["write_entry_url"] = base + f"/board/{_am.group(1)}/{_am.group(2)}/"
        elif _curl:
            tmp["write_entry_url"] = _curl
    res = {"cand_id": cid, "ok": False, "result_url": "", "mb_id": "", "mb_pass": "",
           "bo_table": tmp["bo_table"], "msg": "", "is_temp": False}
    try:
        kw = app.pick_keywords(pool, cfg) if pool else {"지역": "인천", "서비스": "셔츠룸", "브랜드": cfg.get("brand", "") or "테스트"}
        html, title = app.generate_article(kw, cfg, unique=True)
        _just_signed = False
        _login_first = bool(cand.get("login_required")) and not cand.get("write_form")
        if _login_first:
            ok_su, msg_su = app.auto_signup_guarded(tmp, submit=True)
            if ok_su:
                _just_signed = True
                res["mb_id"] = tmp.get("mb_id", ""); res["mb_pass"] = tmp.get("mb_pass", "")
            else:
                res["msg"] = f"자동가입 실패: {str(msg_su)[:80]}"
                log(f"[가입실패] {name} — {str(msg_su)[:70]}")
                return res
        ok, msg = app.do_post(tmp, title, html, skip_login=_just_signed)
        # write가 로그인으로 튕기면 가입 후 1회 재시도. ★Cafe24는 '글쓰기 페이지 못찾음'도 대개 로그인 필요이므로
        #   그 사유도 자동가입 트리거에 포함(대표님 지시 2026-09-11 '각 사이트 자동가입').
        _need_signup = _re.search(r"(로그인이 필요|로그인 실패|로그인 화면|권한이 없|권한 없)", str(msg)) or \
                       (tmp.get("platform")=="cafe24" and ("글쓰기 페이지 못찾음" in str(msg) or "게시판번호" in str(msg)))
        if (not ok) and (not tmp.get("mb_id")) and _need_signup:
            app.reset_driver(); time.sleep(1)
            ok_su, msg_su = app.auto_signup_guarded(tmp, submit=True)
            if ok_su:
                res["mb_id"] = tmp.get("mb_id", ""); res["mb_pass"] = tmp.get("mb_pass", "")
                ok, msg = app.do_post(tmp, title, html, skip_login=True)
            else:
                msg = f"{msg} · 자동가입 실패: {str(msg_su)[:60]}"
        result_url = msg if (ok and str(msg).startswith(("http://", "https://"))) else ""
        if ok and result_url:
            res.update({"ok": True, "result_url": result_url, "bo_table": tmp.get("bo_table")})
            log(f"[발행성공] {name} → {result_url}")
        elif ok:
            res["msg"] = "결과 URL 없음"; log(f"[URL없음] {name}")
        else:
            reason, _, is_temp = app.classify_fail(msg)
            res["msg"] = str(msg)[:90]; res["is_temp"] = bool(is_temp)
            log(f"[발행실패] {name} — {str(msg)[:70]}")
    except Exception as e:
        res["msg"] = f"처리 예외: {str(e)[:80]}"; res["is_temp"] = True
        log(f"[예외] {name} — {str(e)[:70]}")
    finally:
        try: app.reset_driver()
        except Exception: pass
    return res


def _safe_workers(requested):
    """동시 크롬 수 결정. --workers 지정 시 그대로(1~8). 미지정이면 기본 4에서 시작하되,
       가용 메모리가 넉넉하면 최대 6까지. ★순간 저메모리로 1까지 떨어지지 않게 하한 4(대표님 강력PC·상시)."""
    if requested:
        return max(1, min(8, int(requested)))
    try:
        import psutil
        avail_mb = psutil.virtual_memory().available // (1024 * 1024)
        # 여유 많으면 6, 보통이면 4. 하한 4(강력PC 전제라 순간 저메모리에 과도축소 방지).
        return 6 if avail_mb > 4000 else 4
    except Exception:
        return 4


def _force_local_chrome():
    """Bright Data(Scraping Browser·Web Unlocker·프록시)를 노드 로컬 config에서 영구 OFF.
       ★근본원인(2026-09-11 대표님 '카페24 왜 안되냐/오류 계속뜸'): do_post·cafe24_post가 내부에서
       app.load_config()를 다시 읽어 sbr_enabled를 보는데, 노드 로컬 config에 SBR endpoint가 남아 있으면
       webdriver.Remote(SBR)로 접속→계정 정지된 Bright Data가 'Wrong customer name'으로 거부→발행 0.
       Bright Data는 계정정지로 폐기했고 PC 로컬크롬이 실제 IP로 CF를 넘으므로, 노드에선 항상 로컬크롬만 쓴다.
       save_config로 박아 넣어야 이후 어디서 load_config()가 불려도 OFF가 유지된다."""
    try:
        c = app.load_config(); changed = False
        for k in ("sbr_enabled", "unlocker_enabled", "proxy_enabled"):
            if c.get(k):
                c[k] = False; changed = True
        if changed:
            app.save_config(c)
            log("⚠ Bright Data(SBR·Unlocker·프록시) 로컬 강제 OFF — 로컬크롬만 사용(Wrong customer name 방지)")
    except Exception as e:
        log(f"로컬크롬 강제 설정 실패(무시): {str(e)[:60]}")

def run(once=False, workers=None, idle=30):
    _force_local_chrome()
    cfg = app.load_config()
    n = _safe_workers(workers)
    log(f"PC 발행노드 시작 — node_id={NODE_ID} · 서버={SERVER} · 동시 {n}개")
    while True:
        try:
            cfg = app.load_config()
            pool = app.collect_all_keywords()
            # ① 등록 Cafe24 사이트(집 IP 필요) 먼저 처리 — 서버가 CF 못 넘는 것들. 순차(로그인·CF라 무겁게).
            sites = _claim_sites(min(2, n))
            if sites:
                log(f"등록 Cafe24 {len(sites)}곳 로컬발행(로그인)")
                sres = [_process_site(s, cfg) for s in sites]
                _report_sites(sres)
            # ② 미등록 후보 가입·발행테스트(동시 n).
            cands = _claim(n)
            if not cands and not sites:
                if once:
                    log("claim할 후보 없음 — 종료(--once)"); break
                log(f"대기 후보 없음 — {idle}초 후 재시도"); time.sleep(idle); continue
            if cands:
                log(f"{len(cands)}곳 claim — 가입·발행 시작(동시 {n})")
                results = []; lock = threading.Lock(); threads = []
                def _w(c):
                    r = _process_one(c, cfg, pool)
                    with lock: results.append(r)
                for c in cands:
                    t = threading.Thread(target=_w, args=(c,), name=f"NODE-{str(c.get('id',''))[:6]}", daemon=True)
                    t.start(); threads.append(t)
                for t in threads:
                    t.join()
                _report(results)
            if once:
                log("배치 1회 완료 — 종료(--once)"); break
        except KeyboardInterrupt:
            log("중단(Ctrl+C) — 종료"); break
        except Exception as e:
            log(f"루프 오류: {e} — 15초 후 계속"); time.sleep(15)


def cafe24_test(url, mb_id="", mb_pass=""):
    """★Cafe24 로컬크롬 발행 테스트(대표님 지시 2026-09-09 'PC 로컬크롬로 Cafe24 뚫기').
       Bright Data 없이 PC 실제 IP·실제 크롬으로 CF 통과 + (계정 주면)로그인 + 실제 글1건 발행 검증.
       계정(--id/--pw)을 주면 로그인 발행, 안 주면 비회원/CF만 확인. (서버 claim 안 기다리고 직접 URL 지정.)"""
    import re as _re
    m = _re.match(r"(https?://[^/]+)", url); base = m.group(1) if m else url
    cfg = app.load_config()
    if cfg.get("sbr_enabled"):
        log("⚠ 로컬 config에 sbr_enabled=True — 로컬크롬 테스트 위해 이번 실행만 강제 OFF")
        cfg["sbr_enabled"] = False
    # bo_table 추출. /article/{명}/{no}/ 또는 /board/{명}/{no}/ SEO-URL, ?board_no=·bo_table= 지원.
    #  ★2026-09-11 버그수정: /board/상품-qa/6/ 이 매칭 안 돼 board_no=1로 잘못 파싱됐음(대표님 실측).
    bo = ""; art_name = ""
    am = _re.search(r"/(?:article|board)/([^/]+)/(\d+)/", url)
    if am and am.group(1).lower() not in ("write", "list", "read", "view", "product", "free"):
        art_name = am.group(1); bo = am.group(2)
    else:
        bm = _re.search(r"[?&]board_no=(\d+)", url) or _re.search(r"bo_table=([A-Za-z0-9_]+)", url)
        if bm: bo = bm.group(1)
    site = {"id": "cafe24test", "site_url": base, "platform": "cafe24",
            "bo_table": bo or "1", "name": base, "mb_id": mb_id, "mb_pass": mb_pass}
    # ★대표님이 지정한 '진짜 글쓰기 진입 링크'를 최우선 진입점으로(엔진 write_entry_url/article_board_name 재사용).
    #   /article/ 뿐 아니라 /board/{명}/{no}/ 도 진입링크로(대표님 지정 URL이 /board/상품-qa/6/).
    if url != base and ("/article/" in url or ("/board/" in url and art_name)):
        site["write_entry_url"] = url
        if art_name: site["article_board_name"] = art_name
    _login = bool(mb_id)
    log(f"Cafe24 로컬크롬 발행 테스트 — {base} (bo={site['bo_table']}) · {'계정 로그인' if _login else '비회원(계정없음)'}")
    log("크롬 띄우는 중... CF 통과 시도(실제 IP라 데이터센터보다 유리). 최대 1~2분.")
    try:
        kw = {"지역": "인천", "서비스": "노래방", "브랜드": cfg.get("brand", "") or "테스트"}
        html, title = app.generate_article(kw, cfg, unique=True)
        ok, msg = app.cafe24_post(site, title, html, skip_login=not _login)  # 계정 주면 로그인 발행
        if ok and str(msg).startswith(("http://", "https://")):
            log(f"✅ 발행 성공! → {msg}")
            log("→ PC 로컬크롬이 이 Cafe24의 CF를 통과했습니다. Bright Data 불필요.")
        elif ok:
            log(f"△ 발행됐으나 결과 URL 불명: {str(msg)[:100]}")
        else:
            log(f"✗ 실패: {str(msg)[:140]}")
            low = str(msg).lower()
            if "just a moment" in low or "cloudflare" in low or "챌린지" in msg:
                log("→ CF를 아직 못 넘음. 크롬이 챌린지에 걸림(재시도/수동확인 필요).")
            elif "로그인" in msg or "권한" in msg:
                log("→ CF는 넘었으나 로그인 필요. 이 게시판은 계정이 있어야 발행 가능.")
    except Exception as e:
        log(f"✗ 예외: {str(e)[:140]}")
    finally:
        try: app.reset_driver()
        except Exception: pass
    # ★엔진 내부 진단로그 출력(대표님 'Cafe24 글쓰기 못찾음 더 파기'): app.py add_log는 로컬 logs.json에만
    #   쌓여 콘솔에 안 보인다 → 방금 시도의 Cafe24 관련 진단([Cafe24글쓰기시도]·[Cafe24폼진단] 등)을 뽑아 출력.
    try:
        import json as _j
        lg = _j.load(open(app.LOG_FILE, encoding="utf-8"))
        rel = [l for l in lg if any(k in l.get("msg","") for k in
               ["Cafe24", "글쓰기", "폼진단", "Turnstile", "로그인"])][-12:]
        if rel:
            log("──── 엔진 내부 진단(최근) ────")
            for l in rel: log(f"  {l.get('time','')} {l.get('msg','')[:150]}")
            log("────────────────────────────")
    except Exception as e:
        log(f"(진단로그 읽기 실패: {str(e)[:50]})")


def cafe24_inspect(url):
    """★Cafe24 사이트 구조 분석(대표님 지시 2026-09-09 '해당 사이트 로직에 맞게').
       로컬크롬으로 CF통과 후, 로그인폼·글쓰기 진입·게시판 구조의 실제 DOM을 뽑아
       cafe24_post 로직을 이 사이트에 맞출 근거를 만든다. 계정 불필요(구조만 관찰)."""
    from selenium.webdriver.common.by import By
    import re as _re, json as _json
    m = _re.match(r"(https?://[^/]+)", url); base = m.group(1) if m else url
    cfg = app.load_config()
    if cfg.get("sbr_enabled"): cfg["sbr_enabled"] = False
    d = app.get_driver(remote=False)
    out = {}
    def _grab(u, label):
        log(f"[{label}] 접속: {u}")
        try: d.get(u)
        except Exception: pass
        # CF 통과 대기
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                t = (d.title or "").lower()
                if not ("just a moment" in t or "attention" in t or "잠시" in t): break
            except Exception: pass
            time.sleep(0.7)
        info = {}
        try: info["title"] = d.title
        except Exception: info["title"] = "?"
        try: info["url"] = d.current_url
        except Exception: info["url"] = "?"
        # 로그인 관련 필드/버튼
        try:
            info["inputs"] = d.execute_script(
                "return Array.from(document.querySelectorAll('input')).slice(0,40).map(function(i){return (i.name||i.id||i.type||'?')+'['+i.type+']'});")
        except Exception as e: info["inputs"] = "js오류:"+str(e)[:40]
        # 글쓰기/로그인 버튼·링크
        try:
            info["buttons"] = d.execute_script(
                "return Array.from(document.querySelectorAll('a,button')).map(function(b){var t=(b.textContent||'').trim().slice(0,14);var h=b.getAttribute('href')||b.getAttribute('onclick')||'';return t?(t+(h?(' → '+String(h).slice(0,40)):'')):null}).filter(Boolean).slice(0,40);")
        except Exception as e: info["buttons"] = "js오류:"+str(e)[:40]
        # /article/ 링크(글쓰기 진입 후보)
        try:
            info["article_links"] = d.execute_script(
                "return Array.from(document.querySelectorAll(\"a[href*='/article/'],a[href*='write'],a[href*='login'],a[href*='member']\")).map(function(a){return a.getAttribute('href')}).slice(0,30);")
        except Exception as e: info["article_links"] = "js오류:"+str(e)[:40]
        return info
    try:
        out["home"] = _grab(base, "홈")
        out["login"] = _grab(base + "/member/login.html", "로그인페이지")
        if url != base:
            out["target"] = _grab(url, "지정링크(글목록)")
        # 결과를 파일로 저장(대표님이 보여줄 수 있게)
        p = "cafe24_inspect.json"
        open(p, "w", encoding="utf-8").write(_json.dumps(out, ensure_ascii=False, indent=2))
        log("─" * 40)
        log(f"구조 분석 완료 → {p} 저장됨. 아래 요약:")
        for k, v in out.items():
            log(f"[{k}] title={str(v.get('title'))[:40]} url={str(v.get('url'))[:60]}")
            log(f"   inputs={v.get('inputs')}")
            log(f"   buttons(일부)={str(v.get('buttons'))[:200]}")
            log(f"   links={str(v.get('article_links'))[:200]}")
        log("─" * 40)
        log("이 출력을 캡처해 보내주시면 Claude가 이 사이트 로직에 맞게 코드를 맞춥니다.")
    except Exception as e:
        log(f"✗ 분석 예외: {str(e)[:140]}")
    finally:
        try: app.reset_driver()
        except Exception: pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="한 배치만 처리하고 종료")
    ap.add_argument("--workers", type=int, default=0, help="동시 발행 크롬 수(미지정=메모리 자동)")
    ap.add_argument("--idle", type=int, default=30, help="claim 없을 때 대기 초")
    ap.add_argument("--cafe24-test", dest="cafe24_test", default="", help="지정 Cafe24 URL에 로컬크롬 발행 테스트")
    ap.add_argument("--cafe24-inspect", dest="cafe24_inspect", default="", help="지정 Cafe24 사이트 구조 분석(로그인폼·글쓰기 진입 DOM 추출)")
    ap.add_argument("--id", dest="mb_id", default="", help="(선택) Cafe24 로그인 아이디 — 주면 로그인 발행 테스트")
    ap.add_argument("--pw", dest="mb_pass", default="", help="(선택) Cafe24 로그인 비번 — 명령줄 노출 주의, 테스트 후 창 닫기 권장")
    a = ap.parse_args()
    if a.cafe24_inspect:
        cafe24_inspect(a.cafe24_inspect)
    elif a.cafe24_test:
        cafe24_test(a.cafe24_test, mb_id=a.mb_id, mb_pass=a.mb_pass)
    else:
        run(once=a.once, workers=a.workers, idle=a.idle)
