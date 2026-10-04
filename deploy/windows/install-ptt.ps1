<#
.SYNOPSIS
  Installs Claude Buddy dictation (tap or hold F9, speak) on a Windows notebook.

.DESCRIPTION
  The audio goes to the hub on the Ubuntu machine over Tailscale, which
  transcribes it (on the desktop's GPU when it's on) and the text is typed
  into the focused window here. Voice intents work too ("manda para a HIPAA:
  roda os testes"). Needs Tailscale on this notebook, logged into the same
  personal account, and the hub listening on the tailnet ([hub] listen /
  allowed_networks in the hub's config.toml).

  Run from the repo root in a normal (not admin) PowerShell:

      powershell -ExecutionPolicy Bypass -File deploy\windows\install-ptt.ps1

  Safe to run again. Log: %LOCALAPPDATA%\ClaudeBuddy\ptt.log
#>
param(
    [string]$Hub = "http://dell:8765",
    [string]$Key = "f9"
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Venv = Join-Path $Repo ".venv-ptt"
$Python = Join-Path $Venv "Scripts\python.exe"
$PythonW = Join-Path $Venv "Scripts\pythonw.exe"
$TokenFile = Join-Path $env:USERPROFILE ".config\claude-buddy\token"
$LogDir = Join-Path $env:LOCALAPPDATA "ClaudeBuddy"
$TaskName = "Claude Buddy dictation"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # Windows PowerShell turns any stderr line of a native command into a
    # terminating error under "Stop": judge native commands by exit code.
    $ErrorActionPreference = "Continue"
    & $Command
    if ($LASTEXITCODE) { throw "$What failed (exit code $LASTEXITCODE, see above)" }
}

function Find-Python312 {
    $ErrorActionPreference = "Continue"
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $out = & py -3.12 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $candidates.Add(($out | Select-Object -Last 1).Trim()) }
    }
    $candidates.Add("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    $candidates.Add("$env:ProgramFiles\Python312\python.exe")
    foreach ($exe in $candidates) { if ($exe -and (Test-Path $exe)) { return $exe } }
    throw "Python 3.12 not found. Install it with: winget install Python.Python.3.12 (then open a new PowerShell)"
}

Step "Python venv"
if (-not (Test-Path $Python)) {
    $base = Find-Python312
    Invoke-Native "creating the venv" { & $base -m venv $Venv }
}
Invoke-Native "pip install" { & $Python -m pip install --upgrade pip pynput sounddevice --quiet }

Step "Token"
$token = if (Test-Path $TokenFile) { (Get-Content $TokenFile -Raw) } else { "" }
if (-not $token -or -not $token.Trim()) {
    New-Item -ItemType Directory -Force (Split-Path $TokenFile) | Out-Null
    do {
        $token = (Read-Host "Paste the hub token (on Ubuntu: cat ~/.config/claude-buddy/token; right-click to paste)").Trim()
    } until ($token)
    Set-Content -Path $TokenFile -Value $token -NoNewline -Encoding ascii
}
$token = $token.Trim()

Step "Hub at $Hub (over Tailscale)"
try {
    Invoke-RestMethod "$Hub/api/state" -Headers @{ "X-Buddy-Token" = $token } -TimeoutSec 5 | Out-Null
    Write-Host "hub reachable" -ForegroundColor Green
} catch {
    Write-Host "hub NOT reachable: is Tailscale connected here, and is the hub listening on the tailnet?" -ForegroundColor Yellow
}

Step "Logon task: $TaskName"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$script = Join-Path $Repo "ptt\buddy_ptt.py"
$launcher = "import os, runpy; os.environ.update(BUDDY_HUB=r'$Hub', BUDDY_PTT_KEY=r'$Key', BUDDY_PTT_LOG=r'$LogDir\ptt.log'); runpy.run_path(r'$script', run_name='__main__')"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "-c `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Tap or hold $Key and speak: Claude Buddy dictation through the hub." -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Write-Host "`nDone. Tap or hold $($Key.ToUpper()) and speak; one beep = listening, two = sent. Log: $LogDir\ptt.log" -ForegroundColor Green
