@echo off
REM ============================================================
REM  chirashi 발행노드 자동재시작 watchdog (사무실 노트북 5대 무인운영)
REM  - 노드가 죽으면 자동 재시작
REM  - 노드가 살아있는데 멈추면(heartbeat 5분 정체) 강제 kill 후 재시작
REM  - 부팅 시 자동실행하려면: 이 파일 바로가기를  shell:startup  폴더에 넣기
REM  사용: 이 파일만 더블클릭(START_NODE.bat 대신). 창을 닫으면 감시도 멈춤.
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
title chirashi 노드 watchdog (%PC_NODE_ID%)

set RAW=https://raw.githubusercontent.com/kangtaewoo1/chirashi-master/master/chirashi
set HB=.node_heartbeat
set STALE=300
REM STALE=heartbeat가 이 초(300=5분) 이상 안 바뀌면 행으로 보고 재시작

:START
echo ================================
echo  [%date% %time%] 노드 시작/재시작  node_id=%PC_NODE_ID%
echo ================================

REM 최신 코드 받기(오프라인이면 로컬 사용)
powershell -Command "try{Invoke-WebRequest -Uri '%RAW%/app.py' -OutFile 'app.py'; Invoke-WebRequest -Uri '%RAW%/pc_node.py' -OutFile 'pc_node.py'; Invoke-WebRequest -Uri '%RAW%/pc_discovery.py' -OutFile 'pc_discovery.py'; Write-Host 'update ok'}catch{Write-Host 'update skipped (offline?) - using local'}" 2>nul

REM 이전 잔여 크롬/드라이버 정리(재시작 시 좀비 방지)
taskkill /F /IM chromedriver.exe >nul 2>&1
taskkill /F /IM chrome.exe >nul 2>&1

REM heartbeat 초기화
powershell -Command "[IO.File]::WriteAllText('%HB%',[string][int][double]::Parse((Get-Date -UFormat %%s)))" 2>nul

REM 노드를 백그라운드로 시작하고 PID 확보
echo 노드 프로세스 시작(워커 2)...
start "chirashi-node" /min py pc_node.py --workers 2

REM ── 감시 루프: 30초마다 heartbeat 확인 ──
:WATCH
timeout /t 30 /nobreak >nul

REM 노드 프로세스(py/python)가 살아있나?
tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul
if errorlevel 1 (
  echo [%time%] 노드 프로세스 없음 — 재시작
  goto START
)

REM heartbeat 정체(행) 확인
for /f %%H in ('powershell -Command "$now=[int][double]::Parse((Get-Date -UFormat %%s)); try{$hb=[int](Get-Content '%HB%' -Raw)}catch{$hb=$now}; ($now-$hb)"') do set AGE=%%H
if not defined AGE set AGE=0
if %AGE% GEQ %STALE% (
  echo [%time%] heartbeat %AGE%초 정체 ^(행 상태^) — 강제 재시작
  taskkill /F /IM python.exe >nul 2>&1
  taskkill /F /IM py.exe >nul 2>&1
  goto START
)
set AGE=
goto WATCH
