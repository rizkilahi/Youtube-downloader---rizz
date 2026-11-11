@echo off
title YouTube Downloader Pro
color 0B
cd /d "%~dp0"

echo ========================================================
echo         YouTube Downloader Pro (Web UI Launcher)
echo ========================================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo [ERROR] Python was not found in your system PATH.
    echo Please install Python 3.9+ from https://www.python.org/
    pause
    exit /b 1
)

echo [INFO] Checking Python dependencies...
python -m pip install -r requirements.txt --quiet

echo.
echo [INFO] Starting web server & opening browser...
echo.

python app.py

pause
