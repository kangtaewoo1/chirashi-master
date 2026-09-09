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


def _claim(n):
    """서버에서 후보 n개를 claim(잠금)해 받아온다. 반환: 후보 리스트(빈 리스트면 대기)."""
    try:
        r = requests.post(f"{SERVER}/api/pipeline/claim?token={SERVER_TOKEN}",
                          json={"node_id": NODE_ID, "n": n}, headers=UA, timeout=30, verify=False)
        if r.status_code != 200:
            log(f"claim 실패 HTTP {r.status_code}"); return []
        d = r.json()
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


def _process_one(cand, cfg, pool):
    """후보 1개를 PC 크롬으로 가입·발행테스트(app.py 엔진). auto_pipeline_once의 _proc 로직과 동일.
       반환: report용 result dict."""
    cid = cand.get("id")
    name = cand.get("board_name") or cand.get("domain") or (cand.get("url", "") or "")[:30]
    # 발행 엔진이 기대하는 site dict를 후보로 구성(_proc와 동일)
    import re as _re
    m = _re.match(r"(https?://[^/]+)", cand.get("url", "")); base = m.group(1) if m else cand.get("url", "")
    tmp = {"id": "cand_" + str(cid), "site_url": base, "platform": cand.get("platform", "gnuboard"),
           "bo_table": cand.get("bo_table") or "free", "name": name, "mb_id": "", "mb_pass": ""}
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
        # 검수는 비회원으로 봤지만 write가 로그인으로 튕기면 가입 후 1회 재시도(_proc와 동일)
        if (not ok) and (not tmp.get("mb_id")) and _re.search(r"(로그인이 필요|로그인 실패|로그인 화면|권한이 없|권한 없)", str(msg)):
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


def run(once=False, workers=None, idle=30):
    cfg = app.load_config()
    n = _safe_workers(workers)
    log(f"PC 발행노드 시작 — node_id={NODE_ID} · 서버={SERVER} · 동시 {n}개")
    while True:
        try:
            cfg = app.load_config()
            pool = app.collect_all_keywords()
            cands = _claim(n)
            if not cands:
                if once:
                    log("claim할 후보 없음 — 종료(--once)"); break
                log(f"대기 후보 없음 — {idle}초 후 재시도"); time.sleep(idle); continue
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


def cafe24_test(url):
    """★Cafe24 로컬크롬 발행 테스트(대표님 지시 2026-09-09 'PC 로컬크롬로 Cafe24 뚫기').
       Bright Data 없이 PC 실제 IP·실제 크롬으로 CF 통과 + 실제 글1건 발행을 시도해 되는지 즉시 검증.
       (서버 claim 안 기다리고 직접 URL 지정.)"""
    import re as _re
    m = _re.match(r"(https?://[^/]+)", url); base = m.group(1) if m else url
    cfg = app.load_config()
    if cfg.get("sbr_enabled"):
        log("⚠ 로컬 config에 sbr_enabled=True — 로컬크롬 테스트 위해 이번 실행만 강제 OFF")
        cfg["sbr_enabled"] = False
    # bo_table 추출(있으면). /article/ SEO-URL·board_no 등은 엔진이 자동 처리.
    bo = ""
    bm = _re.search(r"[?&]board_no=(\d+)", url) or _re.search(r"bo_table=([A-Za-z0-9_]+)", url)
    if bm: bo = bm.group(1)
    site = {"id": "cafe24test", "site_url": base, "platform": "cafe24",
            "bo_table": bo or "1", "name": base, "mb_id": "", "mb_pass": ""}
    log(f"Cafe24 로컬크롬 발행 테스트 — {base} (bo={site['bo_table']})")
    log("크롬 띄우는 중... CF 통과 시도(실제 IP라 데이터센터보다 유리). 최대 1~2분.")
    try:
        kw = {"지역": "인천", "서비스": "노래방", "브랜드": cfg.get("brand", "") or "테스트"}
        html, title = app.generate_article(kw, cfg, unique=True)
        ok, msg = app.cafe24_post(site, title, html, skip_login=True)  # 계정 없으면 비회원/CF만 검증
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="한 배치만 처리하고 종료")
    ap.add_argument("--workers", type=int, default=0, help="동시 발행 크롬 수(미지정=메모리 자동)")
    ap.add_argument("--idle", type=int, default=30, help="claim 없을 때 대기 초")
    ap.add_argument("--cafe24-test", dest="cafe24_test", default="", help="지정 Cafe24 URL에 로컬크롬 발행 테스트")
    a = ap.parse_args()
    if a.cafe24_test:
        cafe24_test(a.cafe24_test)
    else:
        run(once=a.once, workers=a.workers, idle=a.idle)
