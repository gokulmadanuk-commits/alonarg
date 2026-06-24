# Makes the Alonarg engine start automatically at every login (no admin needed).
# It drops a small launcher in your Startup folder that runs the engine hidden.
#   Install:  .\install-autostart.ps1
#   Remove:   .\install-autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$vbs = Join-Path $startup "Alonarg.vbs"

if ($Remove) {
  Remove-Item $vbs -Force -ErrorAction SilentlyContinue
  Write-Host "Removed Alonarg auto-start."
  return
}

$pyw    = "$env:LOCALAPPDATA\Alonarg\venv\Scripts\pythonw.exe"
$engine = Join-Path $PSScriptRoot "run_engine.py"
if (-not (Test-Path $pyw)) { Write-Error "venv not found - run .\setup.ps1 first."; exit 1 }

# Write the hidden launcher into the Startup folder ("" => a literal quote in VBScript).
$line = 'CreateObject("WScript.Shell").Run """' + $pyw + '"" ""' + $engine + '""", 0, False'
Set-Content -Path $vbs -Value @("' Alonarg engine auto-start (runs hidden at login).", $line) -Encoding ASCII

# Start it now too.
Start-Process -FilePath $pyw -ArgumentList "`"$engine`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden

Write-Host "Alonarg will now start automatically each time you log in."
Write-Host "Dashboard: http://localhost:8765"
Write-Host "Tip: keep the laptop from sleeping for always-on processing (Settings > Power)."
