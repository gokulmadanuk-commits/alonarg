# Registers the Alonarg engine to run headless at logon and keep running
# (including when the screen is locked). Ollama installs its own autostart service.
#   Install:  .\install-autostart.ps1
#   Remove:   .\install-autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$taskName = "Alonarg Engine"

if ($Remove) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Removed scheduled task '$taskName'."
  return
}

$repo = $PSScriptRoot
$pyw  = "$env:LOCALAPPDATA\Alonarg\venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Error "venv not found - run .\setup.ps1 first."; exit 1 }

$action  = New-ScheduledTaskAction -Execute $pyw `
  -Argument "-m uvicorn alonarg.server:app --host 127.0.0.1 --port 8765" `
  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Force `
  -Description "Runs the Alonarg engine (API + transcription + local-LLM summary) at logon." | Out-Null

Start-ScheduledTask -TaskName $taskName

Write-Host "Installed '$taskName'. The engine starts at logon and keeps running while locked."
Write-Host "Dashboard: http://localhost:8765"
Write-Host ""
Write-Host "Tip: set these system-wide so the background task uses them (then re-log in):"
Write-Host '  setx ALONARG_TOKEN "your-long-secret"      # if exposing to your phone via tunnel'
Write-Host '  setx ALONARG_MODEL "small"                 # whisper model'
Write-Host '  setx OLLAMA_MODEL "llama3.2:3b"            # summary model'
Write-Host ""
Write-Host "Note: keep the laptop from sleeping for always-on processing (Settings > Power)."
