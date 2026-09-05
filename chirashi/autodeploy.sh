#!/bin/bash
# chirashi 자동배포 — GitHub 최신 app.py를 받아 검증 후 확실히 재시작한다.
# crontab에서 주기 실행되므로, push만 하면 콘솔 없이 자동 반영된다.
# 다운로드는 GitHub API(raw 미디어타입)를 쓴다 — raw.githubusercontent.com CDN 캐시(수분)를
# 우회해 push 직후 즉시 최신을 받는다.
set -u
DIR=/opt/chirashi
API=https://api.github.com/repos/kangtaewoo1/chirashi-master/contents/chirashi/app.py?ref=master
LOG=/tmp/chirashi-autodeploy.log
PORT=8888
cd "$DIR" || exit 0

# 앱 재시작 — systemd(chirashi.service, Restart=always)가 프로세스를 관리하므로
# systemd에게 재시작을 맡긴다. (pkill/nohup은 systemd가 되살려 충돌하므로 쓰지 않는다)
restart_app() {
  systemctl restart chirashi
  sleep 3
}

# 1) 최신 파일 받기(GitHub API, raw 미디어타입 — CDN 캐시 없음). 실패하면 종료
if ! curl -fsSL -H "Accept: application/vnd.github.raw" "$API" -o app.py.remote 2>/dev/null; then exit 0; fi
# 받은 게 빈 파일이거나 에러 JSON이면 반영하지 않음(안전장치)
if [ ! -s app.py.remote ]; then rm -f app.py.remote; exit 0; fi
if head -c 1 app.py.remote | grep -q '{'; then
  echo "$(date) API가 JSON 반환(레이트리밋/오류 추정) — 반영 안 함" >>"$LOG"; rm -f app.py.remote; exit 0
fi
# 2) 파일이 같으면(변경 없음) 아무것도 안 함
if [ -f app.py ] && cmp -s app.py app.py.remote; then rm -f app.py.remote; exit 0; fi
# 3) 문법 검사 실패하면 반영 안 함(현재 버전 유지)
if ! venv/bin/python -m py_compile app.py.remote 2>>"$LOG"; then
  echo "$(date) py_compile 실패 — 반영 안 함" >>"$LOG"; rm -f app.py.remote; exit 0
fi
# 4) 백업 후 교체
cp -a app.py "app.py.safebak-$(date +%Y%m%d-%H%M%S)" 2>/dev/null
mv app.py.remote app.py
ls -1t app.py.safebak-* 2>/dev/null | tail -n +16 | xargs -r rm -f
# 5) 확실히 재시작
restart_app
# 6) 정상 응답 확인, 실패 시 직전 백업으로 자동 복구
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/login" 2>/dev/null)
if [ "$CODE" = "000" ] || [ -z "$CODE" ]; then
  echo "$(date) 신버전 무응답($CODE) — 백업 복구" >>"$LOG"
  BK=$(ls -1t app.py.safebak-* 2>/dev/null | head -1)
  if [ -n "$BK" ]; then cp -a "$BK" app.py; restart_app; fi
else
  echo "$(date) HTTP $CODE 반영 성공" >>"$LOG"
fi
