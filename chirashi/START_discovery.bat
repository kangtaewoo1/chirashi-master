@echo off
cd /d "%~dp0"
title chirashi discovery
echo ================================
echo   chirashi DISCOVERY
echo   (Ctrl+C or close window to stop)
echo ================================
py pc_discovery.py
echo.
echo [discovery stopped] press any key to close.
pause >nul
