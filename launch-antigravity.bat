@echo off
setlocal
echo ====================================================
echo Starting Google Antigravity with Remote Debugging
echo Debug Port: 9222
echo ====================================================

set "EXE_PATH=%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe"

if not exist "%EXE_PATH%" (
    set "EXE_PATH=C:\Program Files\Antigravity\Antigravity.exe"
)

if not exist "%EXE_PATH%" (
    echo [ERROR] Antigravity.exe not found at standard locations.
    echo Please ensure Google Antigravity is installed.
    pause
    exit /b 1
)

start "" "%EXE_PATH%" --remote-debugging-port=9222
echo Antigravity launched on port 9222.
endlocal
