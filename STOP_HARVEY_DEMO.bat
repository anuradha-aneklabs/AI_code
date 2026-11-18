@echo off
title Stop Harvey Local Supervisor
cd /d "%~dp0"

echo ======================================================================
echo    Harvey AI Sales Agent — Stopping Local Supervisor
echo ======================================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\stop_windows_demo_supervisor.py
) else (
    echo [WARNING] Python virtual environment not found. Attempting manual process cleanup...
    for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5555 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
    echo Normal power settings may need manual reset if Python is unavailable.
)

echo.
echo Operation complete.
timeout /t 3 >nul
