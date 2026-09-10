@echo off
chcp 65001 >nul
REM ==== 발행노드 + 발굴 둘 다 실행 (더블클릭 한 번에) ====
REM 창 2개를 띄워 각각 발행노드/발굴을 돌린다. 이 bat 위치(chirashi)를 기준.
cd /d "%~dp0"
echo 발행노드 + 발굴 창 2개를 띄웁니다...
start "chirashi 발행노드" cmd /k "cd /d "%~dp0" && py pc_node.py"
start "chirashi 발굴" cmd /k "cd /d "%~dp0" && py pc_discovery.py"
echo 두 창이 떴습니다. 이 창은 닫아도 됩니다.
timeout /t 3 >nul
