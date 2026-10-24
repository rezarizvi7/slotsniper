@echo off
rem Double-click me to start slotsniper on Windows.
title slotsniper
cd /d "%~dp0"

set PY=python
%PY% --version >nul 2>&1 || set PY=py
%PY% --version >nul 2>&1 || (
  echo.
  echo  Python isn't installed yet.
  echo  1. Download it from https://www.python.org/downloads/
  echo  2. When installing, TICK the box "Add python.exe to PATH"
  echo  3. Then double-click start.bat again.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting things up. This takes a minute or two...
  %PY% -m venv .venv || (echo Could not create the Python environment. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --disable-pip-version-check -r requirements.txt || (echo Installing libraries failed. Check your internet connection. & pause & exit /b 1)

if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist .env copy .env.example .env >nul

rem The example config watches the built-in demo laundry site, so start it too.
findstr /c:"localhost:8765" config.yaml >nul && start "slotsniper demo portal" /min python mock_portal\server.py

echo.
echo  slotsniper is starting. Your browser will open the dashboard.
echo  Keep this window open while it runs. Close it to stop.
echo.
python -m slotsniper dashboard
pause
