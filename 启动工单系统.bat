@echo off
rem Double-click to start the local workorder server (no admin rights needed).
rem Keep the minimized "WorkOrder Server" window open; close it to stop.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3 not found in PATH.
  pause
  exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
  echo Port 8080 already in use - server is running. Opening browser only.
  start "" "http://127.0.0.1:8080/?f=2F"
  exit /b 0
)

echo Starting WorkOrder server...
start "WorkOrder Server" /min python server.py
timeout /t 2 >nul
start "" "http://127.0.0.1:8080/?f=2F"
echo.
echo Employee page : http://127.0.0.1:8080/?f=2F
echo Admin page    : http://127.0.0.1:8080/admin
echo Phone / QR    : http://192.168.12.22:8080/
echo Default admin : admin / 123456
echo.
echo Close the minimized "WorkOrder Server" window to stop the server.
timeout /t 5 >nul
