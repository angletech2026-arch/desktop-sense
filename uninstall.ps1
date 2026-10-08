<#
.SYNOPSIS
  Removes desktop-sense hooks, autostart and PATH entry. Add -Purge to also delete
  all recorded data (screenshots, timeline, analyses), config.json and the .venv.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -Purge
#>
param([switch]$Purge)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Ds = Join-Path $Root 'ds.py'
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$BinDir = Join-Path $Root 'bin'

if (Test-Path $VenvPy) {
    & $VenvPy $Ds stop
    & $VenvPy $Ds autostart off
    & $VenvPy $Ds setup --remove
} else {
    Write-Host "No .venv found, so the daemon, autostart entry and AI-tool hooks could not be removed automatically." -ForegroundColor Yellow
    Write-Host "Remove them by hand: the 'desktop-sense' value under HKCU\Software\Microsoft\Windows\CurrentVersion\Run," -ForegroundColor Yellow
    Write-Host "and the desktop-sense hook entries in ~/.claude/settings.json, ~/.codex/hooks.json, ~/.gemini/settings.json." -ForegroundColor Yellow
}

$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$parts = @($raw -split ';' | Where-Object { $_ -ne '' -and $_ -ne $BinDir })
$key.SetValue('Path', ($parts -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
$key.Close()
Write-Host "Removed $BinDir from PATH."

if ($Purge) {
    foreach ($p in @('data', '.venv', 'config.json')) {
        $full = Join-Path $Root $p
        if (Test-Path $full) { Remove-Item -Recurse -Force $full -Confirm:$false }
    }
    Write-Host "Deleted recorded data, config.json and .venv."
} else {
    Write-Host "Your recorded data is still in $Root\data (run with -Purge to delete it)."
}
Write-Host "desktop-sense is uninstalled. You can delete this folder now." -ForegroundColor Green
