@echo off
chcp 65001 >nul
title 찌라시 PC 자동 발굴
cd /d "%~dp0"

REM ============================================================
REM  찌라시 PC 자동 발굴 연동 — 더블클릭으로 시작
REM  PC가 게시판을 검색(무료 DuckDuckGo)해서 서버로 보내면,
REM  서버가 자동으로 검수 + 발행테스트까지 진행합니다.
REM  ※ API 키 불필요 — 그냥 실행하면 됩니다.
REM ============================================================

REM --- 서버 토큰(로그 토큰) ---
set "CHIRASHI_TOKEN=cae3aaa53d6f3576a1c1f6a258f79129"
set "CHIRASHI_SERVER=https://google.twseo.kr"

REM --- 검색 방식: ddg = 무료(키 불필요). 서버 Brave 쿼터와 안 겹침. ---
set "PC_SEARCH_PROVIDER=ddg"

echo.
echo   ============================================
echo    찌라시 PC 발굴 시작 (20분마다 자동 반복)
echo    - 서버: %CHIRASHI_SERVER%
echo    - 검색: 무료 DuckDuckGo (키 불필요)
echo    - 종료: 이 창을 닫거나 Ctrl+C
echo   ============================================
echo.

python "%~dp0pc_discovery.py" %*

echo.
echo   발굴 프로세스가 종료되었습니다.
pause
