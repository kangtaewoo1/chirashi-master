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

python "%~dp0pc_discovery.py" %*

echo.
echo (discovery process ended)
pause
