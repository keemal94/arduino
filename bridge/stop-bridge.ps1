# Stops every running bridge (frees the COM port, e.g. before uploading a sketch).
# Autostart stays installed; start it again with install-autostart.cmd or start-bridge.cmd.
$bridges = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'py%'" |
    Where-Object { $_.CommandLine -like '*usage_lcd_bridge.py*' -and $_.ProcessId -ne $PID })
foreach ($p in $bridges) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($bridges.Count -gt 0) {
    Wait-Process -Id $bridges.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
    Write-Host 'The bridge was stopped; its COM port is free now.'
    Write-Host 'To start it again: run install-autostart.cmd (or log off and on again).'
} else {
    Write-Host 'The bridge was not running.'
}
