@echo off
rem Starts the bridge now and at every logon. Arguments go to the bridge, e.g.: install-autostart.cmd --port COM5
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-autostart.ps1" %*
set RC=%ERRORLEVEL%
pause
exit /b %RC%
