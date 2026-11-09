@echo off
REM ============================================================
REM  chirashi office-PC node installer (run once, double-click)
REM  - downloads the latest code into C:\chirashi_node
REM  - installs Python packages
REM  - asks for the server token (never stored in the repository)
REM  - sets PC_NODE_ID / NODE_WORKERS and autostarts WATCHDOG_NODE.bat at boot
REM  ASCII ONLY on purpose (cmd.exe misreads mixed-encoding batch files).
REM ============================================================
setlocal
set DEST=C:\chirashi_node
set ZIP=%TEMP%\chirashi_master.zip
set REPO=https://github.com/kangtaewoo1/chirashi-master/archive/refs/heads/master.zip

echo.
echo [1/6] Checking Python...
where py >nul 2>&1
if errorlevel 1 goto NOPY
py -3 --version
goto PYOK
:NOPY
echo  Python launcher "py" not found.
echo  Install Python 3.13 from https://www.python.org and tick "Add python.exe to PATH", then run this again.
pause
exit /b 1
:PYOK

echo.
echo [2/6] Checking Chrome...
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" goto CHOK
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" goto CHOK
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" goto CHOK
echo  Google Chrome not found. Install Chrome, then run this again.
pause
exit /b 1
:CHOK
echo  Chrome found.

echo.
echo [3/6] Downloading the latest code...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%REPO%' -OutFile '%ZIP%'"
if errorlevel 1 goto DLFAIL
if exist "%TEMP%\chirashi_unzip" rmdir /s /q "%TEMP%\chirashi_unzip"
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%TEMP%\chirashi_unzip' -Force"
if not exist "%DEST%" mkdir "%DEST%"
REM copy code only; keep an existing local data folder
robocopy "%TEMP%\chirashi_unzip\chirashi-master-master\chirashi" "%DEST%" /E /XD data __pycache__ backups /NFL /NDL /NJH /NJS >nul
if not exist "%DEST%\data" mkdir "%DEST%\data"
if not exist "%DEST%\pc_node.py" goto DLFAIL
echo  Code installed to %DEST%
goto DLOK
:DLFAIL
echo  Download failed. Check the internet connection and run this again.
pause
exit /b 1
:DLOK

echo.
echo [4/6] Installing Python packages (a few minutes)...
py -3 -m pip install --upgrade pip >nul
py -3 -m pip install -r "%DEST%\requirements.txt" onnxruntime
if errorlevel 1 echo  Some packages failed - the node may still run. Check the messages above.

echo.
echo [5/6] Node settings
set /p NODE_NAME=Node name (example: office1):
if "%NODE_NAME%"=="" set NODE_NAME=office1
set /p NODE_TOKEN=Server token (ask Claude / the boss; it is not stored in the code):
if "%NODE_TOKEN%"=="" goto NOTOKEN
setx PC_NODE_ID "pc-%NODE_NAME%" >nul
setx CHIRASHI_TOKEN "%NODE_TOKEN%" >nul
setx NODE_WORKERS "3" >nul
setx PC_NODE_MODE "full" >nul
echo  Saved: PC_NODE_ID=pc-%NODE_NAME%, NODE_WORKERS=3
goto TOKOK
:NOTOKEN
echo  No token entered. Run this installer again with the token.
pause
exit /b 1
:TOKOK

echo.
echo [6/6] Autostart at boot
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\chirashi_node_watchdog.lnk'); $s.TargetPath='%DEST%\WATCHDOG_NODE.bat'; $s.WorkingDirectory='%DEST%'; $s.WindowStyle=7; $s.Save()"
echo  Startup shortcut created.

echo.
echo ============================================================
echo  Done. Please also set: Settings ^> System ^> Power ^> Sleep = Never.
echo  Starting the node now (minimized window "chirashi node watchdog").
echo ============================================================
set PC_NODE_ID=pc-%NODE_NAME%
set CHIRASHI_TOKEN=%NODE_TOKEN%
set NODE_WORKERS=3
set PC_NODE_MODE=full
start "chirashi node watchdog" /min cmd /c "cd /d %DEST% && WATCHDOG_NODE.bat"
pause
endlocal
