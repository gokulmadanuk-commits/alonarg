# Alonarg launcher - starts the server, tray icon, and global hotkey.
$ErrorActionPreference = "Stop"
$py = "$env:LOCALAPPDATA\Alonarg\venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) { $py = "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { Write-Error "venv not found. Run .\setup.ps1 first."; exit 1 }
Set-Location $PSScriptRoot
& "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m alonarg
