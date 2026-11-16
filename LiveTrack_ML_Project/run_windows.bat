@echo off
setlocal
cd /d "%~dp0"
title LiveTrack ML - Setup and Start

echo ============================================
echo          LiveTrack ML - Windows Setup
echo ============================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher was not found.
  echo Please install Python from https://www.python.org/downloads/
  echo During installation, tick "Add Python to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  py -m venv .venv
  if errorlevel 1 goto failed
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto failed

echo.
echo [2/3] Installing required Python packages...
python -m pip install --upgrade pip
if errorlevel 1 goto failed
pip install -r requirements.txt
if errorlevel 1 goto failed

set ADMIN_PASSWORD=ChangeMe123!
set SECRET_KEY=local-demo-key-change-before-deployment

echo.
echo [3/3] Starting LiveTrack ML...
echo Keep this black window open while using the app.
echo Open this address in Chrome: http://127.0.0.1:5000
echo Press CTRL+C here to stop the app.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
python app.py

echo.
echo The app has stopped.
pause
exit /b 0

:failed
echo.
echo Setup did not finish. Take a screenshot of this window and send it to ChatGPT.
pause
exit /b 1
