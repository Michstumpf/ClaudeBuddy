@echo off
rem Double-click: git pull + restart the Claude Buddy STT worker.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-worker.ps1"
echo.
pause
