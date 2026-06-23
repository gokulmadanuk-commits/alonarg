# Alonarg setup - creates the venv (outside OneDrive) and installs dependencies.
$ErrorActionPreference = "Stop"
$venv = "$env:LOCALAPPDATA\Alonarg\venv"
Write-Host "Creating venv at $venv ..."
python -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r "$PSScriptRoot\requirements.txt"
Write-Host ""
Write-Host "Setup complete. Run Alonarg with:  .\run.ps1"
