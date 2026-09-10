# One-command demo stack: API tunnel -> agent -> backend -> session check -> console -> mobile tunnel.
# Runs in its own terminal so recording does not depend on any other session.
param([switch]$SkipClient)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Load-DotEnv {
    foreach ($line in (Get-Content .env)) {
        if ($line -match '^(LUMAFIELD_[A-Z_]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2])
        }
    }
}

# 0) Demo ports must be free (a previous backend/client may still be running).
$busy = Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $details = $busy | ForEach-Object {
        $owner = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        "port $($_.LocalPort) used by '$owner' (PID $($_.OwningProcess))"
    }
    throw "Demo ports are in use: $($details -join '; '). Stop these processes first."
}

# 1) Public tunnel first: the agent tools need its URL before provisioning.
$tunnelLog = Join-Path $env:TEMP "lumafield-tunnel.log"
$cloudflared = Start-Process -FilePath "$root\.tools\cloudflared.exe" `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate") `
    -RedirectStandardError $tunnelLog -PassThru -WindowStyle Hidden
$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep -Milliseconds 500
    $hit = Select-String -Path $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($hit) { $url = $hit.Matches[0].Value }
}
if (-not $url) { throw "Tunnel did not start; see $tunnelLog" }
Write-Host "API tunnel: $url" -ForegroundColor Cyan

# 2) Persist the URL (UTF-8 without BOM; the first .env line is a comment).
$content = (Get-Content .env -Raw) -replace '(?m)^LUMAFIELD_PUBLIC_API_URL=.*', "LUMAFIELD_PUBLIC_API_URL=$url"
[IO.File]::WriteAllText("$root\.env", $content)

# 3) Create or re-point the agent (updates LUMAFIELD_AGENT_ID in .env).
Load-DotEnv
& .venv\Scripts\python.exe scripts\provision_agent.py
if ($LASTEXITCODE -ne 0) { throw "Agent provisioning failed." }

# 4) Backend (reload .env so the agent id is present for token minting).
Load-DotEnv
$backendOut = Join-Path $env:TEMP "lumafield-backend-out.log"
$backendErr = Join-Path $env:TEMP "lumafield-backend-err.log"
$backend = Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000") `
    -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -PassThru -WindowStyle Hidden
$ok = $false
for ($i = 0; $i -lt 30 -and -not $ok; $i++) {
    Start-Sleep -Milliseconds 500
    try { $ok = (Invoke-RestMethod "http://127.0.0.1:8000/health").status -eq "ok" } catch {}
}
if (-not $ok) { throw "Backend did not start; see $backendErr" }
Write-Host "Backend: http://127.0.0.1:8000" -ForegroundColor Cyan

# 5) Application gate. Voice is requested only when the operator starts the agent.
$gateBody = @{ request_id = "run-demo-gate-$([guid]::NewGuid().ToString('N'))" } | ConvertTo-Json -Compress
$session = Invoke-RestMethod -Method Post -Uri "$url/v1/sessions" `
    -Headers @{ "X-Demo-Access" = $env:LUMAFIELD_DEMO_ACCESS_TOKEN } `
    -ContentType "application/json" -Body $gateBody
if ($session.session_token.Length -lt 32) { throw "Invalid application session." }
Write-Host ("Test session: {0} · agent {1} · application session OK" -f $session.session_id, $session.agent_id) -ForegroundColor Green

# 6) Console (Vite proxies /v1 to the backend).
if (-not $SkipClient) {
    if (-not (Test-Path "$root\client\node_modules")) {
        Push-Location "$root\client"; npm install; Pop-Location
    }
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run dev" `
        -WorkingDirectory "$root\client" -WindowStyle Minimized | Out-Null
    $clientOk = $false
    for ($i = 0; $i -lt 30 -and -not $clientOk; $i++) {
        Start-Sleep -Milliseconds 500
        try { $clientOk = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5173").StatusCode -eq 200 } catch {}
    }
    if (-not $clientOk) { throw "Console did not start at http://127.0.0.1:5173." }

    $mobileTunnelLog = Join-Path $env:TEMP "lumafield-mobile-tunnel.log"
    $mobileTunnel = Start-Process -FilePath "$root\.tools\cloudflared.exe" `
        -ArgumentList @("tunnel", "--url", "http://127.0.0.1:5173", "--no-autoupdate") `
        -RedirectStandardError $mobileTunnelLog -PassThru -WindowStyle Hidden
    $mobileUrl = $null
    for ($i = 0; $i -lt 40 -and -not $mobileUrl; $i++) {
        Start-Sleep -Milliseconds 500
        $hit = Select-String -Path $mobileTunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($hit) { $mobileUrl = $hit.Matches[0].Value }
    }
    if (-not $mobileUrl) { throw "Mobile tunnel did not start; see $mobileTunnelLog" }

    Write-Host "Desktop console: http://127.0.0.1:5173" -ForegroundColor Cyan
    Write-Host "Mobile console: $mobileUrl" -ForegroundColor Green
}

Write-Host ""
$processIds = @($backend.Id, $cloudflared.Id)
if ($mobileTunnel) { $processIds += $mobileTunnel.Id }
Write-Host "To stop: Stop-Process -Id $($processIds -join ', ') (and close the npm window)." -ForegroundColor Yellow
