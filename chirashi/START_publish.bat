@echo off
cd /d "%~dp0"
title chirashi publish node
echo ================================
echo   chirashi PUBLISH node
echo   (Ctrl+C or close window to stop)
echo ================================
py pc_node.py
echo.
echo [node stopped] press any key to close.
pause >nul
