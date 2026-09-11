@echo off
REM chirashi cafe24 inspect - local Chrome dumps login/write DOM of failing sites
REM Run in the chirashi folder (same as pc_node.py). ASCII-only on purpose (cp949 safe).
cd /d "%~dp0"
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
