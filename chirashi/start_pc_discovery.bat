@echo off
chcp 65001 >nul
title chirashi PC discovery
cd /d "%~dp0"

REM chirashi PC auto discovery - double-click to start.
REM No API key needed (free DuckDuckGo search). Token is built in.

set "CHIRASHI_TOKEN=cae3aaa53d6f3576a1c1f6a258f79129"
set "CHIRASHI_SERVER=https://google.twseo.kr"
set "PC_SEARCH_PROVIDER=ddg"
set "PYTHONIOENCODING=utf-8"

REM Find pc_discovery.py: same folder first, then common project locations.
set "PY="
if exist "%~dp0pc_discovery.py" set "PY=%~dp0pc_discovery.py"
if not defined PY if exist "%USERPROFILE%\Desktop\pc_discovery.py" set "PY=%USERPROFILE%\Desktop\pc_discovery.py"
if not defined PY if exist "%USERPROFILE%\Desktop\google-twseo-kr-windows-20260831-220257\chirashi\pc_discovery.py" set "PY=%USERPROFILE%\Desktop\google-twseo-kr-windows-20260831-220257\chirashi\pc_discovery.py"

if not defined PY (
  echo.
  echo   [ERROR] pc_discovery.py not found.
  echo   Put start_pc_discovery.bat and pc_discovery.py in the SAME folder.
  echo.
  pause
  exit /b 1
)

python "%PY%" %*

echo.
echo (discovery process ended)
pause
