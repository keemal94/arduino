@echo off
rem Runs the bridge in this window (Ctrl+C stops it). Arguments are passed on, e.g.:
rem   start-bridge.cmd --port COM5      start-bridge.cmd --list      start-bridge.cmd --dry-run
setlocal
set "BRIDGE=%~dp0usage_lcd_bridge.py"
where py >nul 2>nul
if errorlevel 1 goto nopy
py -3 "%BRIDGE%" %*
goto done
:nopy
python "%BRIDGE%" %*
:done
if not errorlevel 1 exit /b 0
echo.
echo The bridge has stopped (see the messages above).
echo Log file: "%LOCALAPPDATA%\usage-lcd\bridge.log"
pause
exit /b 1
