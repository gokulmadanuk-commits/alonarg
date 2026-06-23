# Alonarg phone bridge - exposes your PC engine to the phone PWA over a free HTTPS tunnel.
#   Usage:  .\connect.ps1
# After it prints the URL + token, enter them in the PWA Settings (https://alonarg.vercel.app).
$ErrorActionPreference = "Stop"
$venvPy = "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Error "venv not found - run .\setup.ps1 first."; exit 1 }
$port = 8765

# 1) Shared secret so only you can reach the engine.
if ([string]::IsNullOrEmpty($env:ALONARG_TOKEN)) {
  $env:ALONARG_TOKEN = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
  Write-Host "Generated a session token. To reuse it, run: setx ALONARG_TOKEN `"$($env:ALONARG_TOKEN)`""
}
$env:ALONARG_CORS_ORIGINS = "*"
Write-Host "TOKEN (paste into the PWA): $($env:ALONARG_TOKEN)" -ForegroundColor Cyan

# 2) Make sure the engine is listening on 127.0.0.1:8765.
$listening = $false
try { $listening = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue } catch {}
if (-not $listening) {
  Write-Host "Starting Alonarg engine on port $port ..."
  Start-Process -FilePath $venvPy `
    -ArgumentList @("-m","uvicorn","alonarg.server:app","--host","127.0.0.1","--port","$port") `
    -WorkingDirectory $PSScriptRoot -WindowStyle Minimized
  Start-Sleep -Seconds 3
} else {
  Write-Host "A server is already listening on $port. Ensure it was started with the SAME ALONARG_TOKEN."
}

# 3) Ensure cloudflared is installed.
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  Write-Host "cloudflared not found - installing via winget ..."
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
}

# 4) Open the HTTPS tunnel to the engine (prints an https://*.trycloudflare.com URL).
Write-Host ""
Write-Host "Opening tunnel. Copy the https://*.trycloudflare.com URL below into the PWA Settings" -ForegroundColor Green
Write-Host "(Backend URL) together with the TOKEN above, then Recordings -> Sync now." -ForegroundColor Green
Write-Host "PWA: https://alonarg.vercel.app" -ForegroundColor Green
Write-Host ""
cloudflared tunnel --url "http://localhost:$port"
