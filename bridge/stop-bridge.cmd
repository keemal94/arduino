@echo off
rem Stops the bridge so the Arduino IDE can use the COM port. Autostart stays installed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-bridge.ps1"
set RC=%ERRORLEVEL%
pause
exit /b %RC%
