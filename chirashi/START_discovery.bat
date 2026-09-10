@echo off
chcp 65001 >nul
REM ==== 찌라시 발굴 실행 (더블클릭) ====
REM 이 bat 파일이 있는 폴더(chirashi)에서 pc_discovery.py 실행. 사이트 발굴 담당.
cd /d "%~dp0"
title chirashi 발굴
echo ================================
echo   찌라시 발굴 시작
echo   (닫으려면 이 창에서 Ctrl+C 또는 창 닫기)
echo ================================
py pc_discovery.py
echo.
echo [발굴 종료됨] 아무 키나 누르면 창이 닫힙니다.
pause >nul
