@echo off
cd /d "%~dp0"
echo ================================
echo  chirashi node launcher
echo  updating latest code from GitHub...
echo ================================
set RAW=https://raw.githubusercontent.com/kangtaewoo1/chirashi-master/master/chirashi
powershell -Command "try{Invoke-WebRequest -Uri '%RAW%/app.py' -OutFile 'app.py'; Invoke-WebRequest -Uri '%RAW%/pc_node.py' -OutFile 'pc_node.py'; Invoke-WebRequest -Uri '%RAW%/pc_discovery.py' -OutFile 'pc_discovery.py'; Write-Host 'update ok'}catch{Write-Host 'update skipped (offline?) - using local files'}"
echo.
echo Starting PUBLISH node and DISCOVERY in two windows...
start "chirashi publish" cmd /k cd /d "%~dp0" ^& py pc_node.py
start "chirashi discovery" cmd /k cd /d "%~dp0" ^& py pc_discovery.py
echo Two windows opened. You can close this window.
timeout /t 3 >nul
