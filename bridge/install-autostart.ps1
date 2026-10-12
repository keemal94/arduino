# Runs the bridge hidden (pythonw.exe) at every logon via a shortcut in the Startup
# folder, and starts it now. Arguments go to the bridge, e.g. --port COM5.
# Safe to run again (e.g. after moving this folder): it replaces the old setup.
$bridgeDir = $PSScriptRoot
$script = Join-Path $bridgeDir 'usage_lcd_bridge.py'
$requirements = Join-Path $bridgeDir 'requirements.txt'

function Fail($message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# Find the Python that has pyserial: the py launcher's Python 3, else python on PATH.
if (Get-Command py -ErrorAction SilentlyContinue) { $python = @('py', '-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $python = @('python') }
else { Fail 'Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.' }
$pythonArgs = @($python | Select-Object -Skip 1)
$exe = & $python[0] @pythonArgs -c 'import sys, serial; print(sys.executable)' 2>$null | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or -not $exe) {
    & $python[0] @pythonArgs -c 'import sys' 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail 'Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.'
    }
    Fail "pyserial is not installed. Run this command, then run this script again:`n  $($python -join ' ') -m pip install -r `"$requirements`""
}
$pythonw = Join-Path (Split-Path $exe.Trim() -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    Fail "pythonw.exe was not found next to $exe. Reinstall Python from https://www.python.org/downloads/."
}

$extra = @($args | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } })
$arguments = (@("`"$script`"") + $extra) -join ' '

& (Join-Path $PSScriptRoot 'stop-bridge.ps1')  # a running bridge would keep its old settings

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage LCD.lnk'
try {
    $shortcut = (New-Object -ComObject WScript.Shell -ErrorAction Stop).CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $bridgeDir
    $shortcut.Description = 'Shows Claude usage on the Arduino LCD'
    $shortcut.Save()
} catch {
    Fail "Could not create the shortcut ${shortcutPath}: $_"
}
Write-Host "Autostart installed: $shortcutPath"
Write-Host "  runs: `"$pythonw`" $arguments"

$process = Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $bridgeDir `
    -WindowStyle Hidden -PassThru
$null = $process.Handle  # keeps the exit code readable
Start-Sleep -Seconds 2
if ($process.HasExited) {
    Fail "The bridge stopped right away (exit code $($process.ExitCode)). Run start-bridge.cmd to see why."
}
Write-Host 'The bridge is running in the background.'
Write-Host "Log file: $env:LOCALAPPDATA\usage-lcd\bridge.log"
Write-Host 'Before uploading a sketch with the Arduino IDE, run stop-bridge.cmd to free the COM port.'
