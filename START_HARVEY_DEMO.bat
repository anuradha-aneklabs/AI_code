@echo off
setlocal
title Harvey AI Sales Agent - Public Demo Supervisor
cd /d "%~dp0"

echo ======================================================================
echo    Harvey AI Sales Agent - Public Demo Supervisor
echo ======================================================================
echo.

if not exist ".venv\Scripts\python.exe" goto missing_python

echo [INFO] Starting Harvey Supervisor (http://127.0.0.1:5555)...
echo [INFO] Cloudflare Quick Tunnel: ENABLED
echo [INFO] Keep this window open.
echo.
".venv\Scripts\python.exe" scripts\run_windows_demo_supervisor.py

echo.
echo [INFO] Supervisor stopped. Check data\demo_supervisor.log
pause
exit /b

:missing_python
echo [ERROR] Python virtual environment not found (.venv\Scripts\python.exe).
pause
exit /b 1
