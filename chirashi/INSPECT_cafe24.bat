@echo off
REM chirashi cafe24 inspect - local Chrome dumps login/write DOM of failing sites
REM Works from any folder: finds the chirashi folder (pc_node.py) automatically. ASCII-only (cp949 safe).
set CH=%~dp0
if exist "%CH%pc_node.py" goto found
set CH=%USERPROFILE%\Desktop\google-twseo-kr-windows-20260831-220257\chirashi\
if exist "%CH%pc_node.py" goto found
set CH=%USERPROFILE%\Desktop\chirashi\
if exist "%CH%pc_node.py" goto found
set CH=%USERPROFILE%\chirashi\
if exist "%CH%pc_node.py" goto found
echo.
echo ERROR: pc_node.py not found. Put this .bat inside the chirashi folder
echo        (the folder that has START_ALL.bat and pc_node.py) and run it again.
echo        This .bat is at: %~dp0
pause
exit /b 1
:found
cd /d "%CH%"
echo Using chirashi folder: %CD%
set CHIRASHI_HEADFUL=1
echo.
echo [1/3] inspecting sjmania.co.kr
py pc_node.py --cafe24-inspect "https://sjmania.co.kr/article/%%EC%%83%%81%%ED%%92%%88-%%EC%%82%%AC%%EC%%9A%%A9%%ED%%9B%%84%%EA%%B8%%B0/4/25855/"
if exist cafe24_inspect.json move /y cafe24_inspect.json cafe24_inspect_1.json >nul
echo.
echo [2/3] inspecting www.montanaski.co.kr
py pc_node.py --cafe24-inspect "https://www.montanaski.co.kr/article/%%EC%%83%%81%%ED%%92%%88-qa/6/10432/"
if exist cafe24_inspect.json move /y cafe24_inspect.json cafe24_inspect_2.json >nul
echo.
echo [3/3] inspecting lsaderm.com
py pc_node.py --cafe24-inspect "https://lsaderm.com/article/qa/6/13946/"
if exist cafe24_inspect.json move /y cafe24_inspect.json cafe24_inspect_3.json >nul
echo.
echo DONE. Results: cafe24_inspect_1.json / _2.json / _3.json  (plus console output above)
echo Screenshot this window or send the json files to Claude.
pause
