<#
Restarts the Claude Buddy STT worker on the desktop after updating the code.
Double-click restart-worker.cmd (next to this file) to run it.

    1. git pull (fast-forward only; stops if there are local changes)
    2. stops and starts the "Claude Buddy STT worker" logon task
    3. waits until the worker answers on /health (the voice model takes ~1 min)
#>
param([int]$Port = 8766)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$TaskName = "Claude Buddy STT worker"
$TokenFile = Join-Path $env:USERPROFILE ".config\claude-buddy\token"
$Log = Join-Path $env:LOCALAPPDATA "ClaudeBuddy\worker.log"

Write-Host "==> git pull" -ForegroundColor Cyan
& git -C $Repo pull --ff-only 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "git pull failed (local changes?). Fix it in $Repo and run this again." }

Write-Host "==> Restarting '$TaskName'" -ForegroundColor Cyan
Stop-ScheduledTask -TaskName $TaskName
# A worker that restarted itself after a GPU error runs outside the task.
Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%buddy_hub.worker%'" |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# Give the old process time to exit and free the port.
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName $TaskName

Write-Host "==> Waiting for the worker (loading the models, up to ~2 min)" -ForegroundColor Cyan
$token = (Get-Content $TokenFile -Raw).Trim()
for ($i = 0; $i -lt 90; $i++) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -Headers @{ "X-Buddy-Token" = $token } -TimeoutSec 2
        Write-Host ("worker up: model {0}, voice {1}, gpu {2}" -f $h.model, $h.tts, ($h.gpu | ConvertTo-Json -Compress)) -ForegroundColor Green
        exit 0
    } catch { Start-Sleep -Seconds 2 }
}
throw "Worker did not answer on port $Port. Log: $Log"
