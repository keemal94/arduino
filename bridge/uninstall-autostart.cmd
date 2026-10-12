@echo off
rem Removes the autostart shortcut and stops the bridge.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-autostart.ps1"
set RC=%ERRORLEVEL%
pause
exit /b %RC%
