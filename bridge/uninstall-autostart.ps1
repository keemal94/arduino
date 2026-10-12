# Removes the autostart shortcut and stops the running bridge.
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage LCD.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
    Write-Host "Autostart removed: $shortcutPath"
} else {
    Write-Host 'Autostart was not installed.'
}
& (Join-Path $PSScriptRoot 'stop-bridge.ps1')
