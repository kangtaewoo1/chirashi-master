#!/bin/bash
# chirashi 자동배포 — GitHub 최신 app.py를 받아 검증 후 확실히 재시작한다.
# crontab에서 5분마다 실행되므로, push만 하면 콘솔 없이 자동 반영된다.
set -u
DIR=/opt/chirashi
RAW=https://raw.githubusercontent.com/kangtaewoo1/chirashi-master/master/chirashi/app.py
LOG=/tmp/chirashi-autodeploy.log
PORT=8888
cd "$DIR" || exit 0

# 앱을 확실히 죽이고(옛 프로세스 잔존 방지) 새로 띄우는 공용 함수
restart_app() {
  pkill -f "venv/bin/python app.py" 2>/dev/null
  for i in 1 2 3 4 5; do
    pgrep -f "venv/bin/python app.py" >/dev/null || break
    sleep 1
  done
  pkill -9 -f "venv/bin/python app.py" 2>/dev/null   # 그래도 살아있으면 강제 종료
  sleep 1
  nohup venv/bin/python app.py > /tmp/chirashi.log 2>&1 &
  sleep 3
}

# 1) 최신 파일 받기(실패하면 종료)
if ! curl -fsSL "$RAW" -o app.py.remote 2>/dev/null; then exit 0; fi
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
