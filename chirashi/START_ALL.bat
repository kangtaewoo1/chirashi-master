@echo off
cd /d "%~dp0"
echo Starting PUBLISH node and DISCOVERY in two windows...
start "chirashi publish" cmd /k cd /d "%~dp0" ^& py pc_node.py
start "chirashi discovery" cmd /k cd /d "%~dp0" ^& py pc_discovery.py
echo Two windows opened. You can close this window.
timeout /t 3 >nul
