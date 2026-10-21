@echo off
setlocal
echo Starting Antigravity CDP Studio...
python main.py %*
if errorlevel 1 (
    echo.
    echo Application exited with error. Press any key to exit.
    pause
)
endlocal
