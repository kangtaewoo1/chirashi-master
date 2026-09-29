@echo off
REM ============================================================
REM  chirashi publish-node watchdog (unattended office laptops)
REM  - starts the node if none is running (never starts a duplicate)
REM  - restarts the node if it dies
REM  - if the node hangs (heartbeat stale >= STALE sec), kills it and restarts
REM  - autostart at boot: put a shortcut to this file in  shell:startup
REM  - ASCII ONLY on purpose: cmd.exe mis-reads batch files that mix chcp 65001
REM    with non-ASCII bytes (line offsets shift -> random fragments executed).
REM  - no parenthesized if-blocks: powershell one-liners contain ')' which would
REM    close a cmd block early. goto-style only.
REM  - liveness is checked by COMMAND LINE (pc_node.py) + image name python*
REM    because the interpreter may be python.exe or python3.13.exe (Store build).
REM  - epoch via [DateTimeOffset]::UtcNow: Windows PowerShell 5.1 Get-Date -UFormat %s is local-time based
REM    (+9h in KST) while the node writes a UTC epoch -> false hang detection every 30 s.
REM  - SKIP_PULL=1 : start with local files, do not download (use right after
REM    a git push while raw/master CDN is still stale for a few minutes)
REM ============================================================
cd /d "%~dp0"
title chirashi node watchdog %PC_NODE_ID%

set RAW=https://raw.githubusercontent.com/kangtaewoo1/chirashi-master/master/chirashi
set HB=.node_heartbeat
set STALE=300
set PYTHONIOENCODING=utf-8
REM (PYTHONIOENCODING keeps node log printing safe; never use PYTHONUTF8=1 - it breaks cp949 tasklist decoding)

:START
echo ================================
echo  [%date% %time%] watchdog start/restart cycle  node_id=%PC_NODE_ID%
echo ================================

REM --- pull latest code (skipped when SKIP_PULL=1, or offline -> keep local) ---
if "%SKIP_PULL%"=="1" goto NOPULL
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; try{Invoke-WebRequest -Uri '%RAW%/app.py' -OutFile 'app.py'; Invoke-WebRequest -Uri '%RAW%/pc_node.py' -OutFile 'pc_node.py'; Invoke-WebRequest -Uri '%RAW%/pc_discovery.py' -OutFile 'pc_discovery.py'; Write-Host 'update ok'}catch{Write-Host ('update skipped - ' + $_.Exception.Message)}" 2>nul
goto PULLDONE
:NOPULL
echo update skipped - SKIP_PULL=1 - using local files
set SKIP_PULL=
:PULLDONE

REM --- kill leftover chromedriver only (NOT chrome.exe: keep the owner's Chrome alive) ---
taskkill /F /IM chromedriver.exe >nul 2>&1

REM --- low disk: purge temp chrome profiles right away (disk full = node dies with 'No space left') ---
powershell -NoProfile -Command "$f=(Get-PSDrive C).Free/1GB; if($f -lt 1){Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Temp') -Directory -Force -EA SilentlyContinue | Where-Object {($_.Name -like 'chr_*' -or $_.Name -like 'scoped_dir*') -and $_.LastWriteTime -lt (Get-Date).AddMinutes(-1)} | Remove-Item -Recurse -Force -EA SilentlyContinue; Write-Host ('disk low - chrome profiles purged, free=' + [math]::Round((Get-PSDrive C).Free/1GB,2) + 'GB')}" 2>nul

REM --- guard: if a node is already running, do not start another one ---
call :COUNTNODE
if %NODES% GTR 0 goto ALREADY

REM --- init heartbeat, then start the node in its own minimized window (output -> pc_node.log) ---
powershell -NoProfile -Command "[IO.File]::WriteAllText('%HB%',[string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" 2>nul
REM NODE_WORKERS env (default 2): raise to 3-4 only when the PC has >= 1.5 GB free RAM per extra worker
if "%NODE_WORKERS%"=="" set NODE_WORKERS=2
echo starting node - workers %NODE_WORKERS%
start "chirashi-node" /min cmd /c "py pc_node.py --workers %NODE_WORKERS% >> pc_node.log 2>&1"
goto WATCH

:ALREADY
echo node already running - %NODES% process - watching only
goto WATCH

REM --- watch loop: every 30 s (ping = works without a console stdin, unlike timeout) ---
:WATCH
ping -n 31 127.0.0.1 >nul
call :COUNTNODE
if %NODES% EQU 0 goto MISSING
for /f %%H in ('powershell -NoProfile -Command "$now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); try{$hb=[int](Get-Content '%HB%' -Raw)}catch{$hb=$now}; ($now-$hb)"') do set AGE=%%H
if not defined AGE set AGE=0
if %AGE% GEQ %STALE% goto HANG
set AGE=
goto WATCH

:MISSING
echo [%time%] node process missing - restart
goto START

:HANG
echo [%time%] heartbeat stale %AGE%s - hang - force restart
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|py)' -and $_.CommandLine -like '*pc_node.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }" 2>nul
set AGE=
goto START

REM --- subroutine: NODES = number of python* processes running pc_node.py ---
:COUNTNODE
set NODES=0
for /f %%N in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -like '*pc_node.py*' } | Measure-Object).Count"') do set NODES=%%N
if not defined NODES set NODES=0
goto :EOF
