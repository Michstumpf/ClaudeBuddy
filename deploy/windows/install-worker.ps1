<#
.SYNOPSIS
  Installs the Claude Buddy GPU speech-to-text worker on the Windows desktop.

.DESCRIPTION
  Run from the repo root in an *Administrator* PowerShell (needed for the
  firewall rule):

      powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1

  It is safe to run again (e.g. after a git pull). Steps:
    1. creates .venv with Python 3.12 and installs hub\requirements-gpu.txt
    2. checks the token copied from the hub
    3. checks that the GPU works (loads the model once; first run downloads ~1.6 GB)
    4. allows TCP 8766 inbound from the Tailscale range only
    5. registers a logon task that starts the worker hidden (pythonw), logging
       to %LOCALAPPDATA%\ClaudeBuddy\worker.log

  The GPU voice (XTTS-v2: PyTorch with CUDA + coqui-tts, ~3 GB of downloads
  plus ~1.8 GB of model) is installed too; pass -NoVoice to skip it. The XTTS
  model license is non-commercial (personal use only).
#>
param(
    [int]$Port = 8766,
    [string]$Model = "large-v3-turbo",
    [switch]$NoVoice
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Venv = Join-Path $Repo ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$PythonW = Join-Path $Venv "Scripts\pythonw.exe"
$TokenFile = Join-Path $env:USERPROFILE ".config\claude-buddy\token"
$LogDir = Join-Path $env:LOCALAPPDATA "ClaudeBuddy"
$TaskName = "Claude Buddy STT worker"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # Windows PowerShell turns any stderr line of a native command (pip
    # warnings, Python logging) into a terminating error under "Stop", so run
    # it with "Continue" and judge it by its exit code instead.
    $ErrorActionPreference = "Continue"
    & $Command
    if ($LASTEXITCODE) { throw "$What failed (exit code $LASTEXITCODE, see above)" }
}

function Find-Python312 {
    # A shell opened before `winget install` has a stale PATH, and winget's
    # per-user install may skip the `py` launcher, so also look where it installs.
    $candidates = New-Object System.Collections.Generic.List[string]
    $ErrorActionPreference = "Continue"  # see Invoke-Native
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $out = & py -3.12 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $candidates.Add(($out | Select-Object -Last 1).Trim()) }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $out = & python -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $candidates.Add(($out | Select-Object -Last 1).Trim()) }
    }
    $candidates.Add("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    $candidates.Add("$env:ProgramFiles\Python312\python.exe")
    foreach ($exe in $candidates) {
        if (-not $exe -or -not (Test-Path $exe)) { continue }
        $minor = & $exe -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$minor".Trim() -eq "312") { return $exe }
    }
    throw "Python 3.12 not found. Install it with: winget install Python.Python.3.12 (then open a new PowerShell)"
}

Step "Python venv"
if (-not (Test-Path $Python)) {
    $base = Find-Python312
    Write-Host "using $base"
    Invoke-Native "creating the venv" { & $base -m venv $Venv }
}
Invoke-Native "upgrading pip" { & $Python -m pip install --upgrade pip --quiet }
Invoke-Native "pip install" { & $Python -m pip install -r (Join-Path $Repo "hub\requirements-gpu.txt") --quiet }
if (-not $NoVoice) {
    Step "GPU voice: PyTorch (CUDA 12.6) + coqui-tts (several minutes, ~3 GB)"
    # One command, with the CUDA index as an extra source, so pip can never
    # swap in the CPU-only torch from PyPI while resolving coqui-tts.
    # coqui-tts 0.27 asks for transformers>=4.57 with no upper bound, but XTTS
    # imports transformers.pytorch_utils.isin_mps_friendly, gone in transformers 5.
    # Whisper (ctranslate2) then uses torch's bundled cuDNN instead of pip's
    # (see add_cuda_dll_dirs): two cuDNN copies in one process crash. torch 2.7
    # bundles a cuDNN 9 newer than the one ctranslate2 was built with, which is
    # fine (cuDNN is backward compatible within a major version, not forward).
    Invoke-Native "pip install (voice)" { & $Python -m pip install "torch==2.7.1" "torchaudio==2.7.1" `
        "coqui-tts>=0.27,<0.28" "transformers>=4.57,<5" `
        --extra-index-url https://download.pytorch.org/whl/cu126 --quiet }
}

Step "Token"
function Read-TokenFile {
    if (-not (Test-Path $TokenFile)) { return "" }
    $raw = Get-Content $TokenFile -Raw
    if ($null -eq $raw) { return "" }  # empty file
    return $raw.Trim()
}
if (-not (Read-TokenFile)) {
    New-Item -ItemType Directory -Force (Split-Path $TokenFile) | Out-Null
    do {
        # Read-Host does not take Ctrl+V in every console; right-click pastes.
        $token = (Read-Host "Paste the hub token (on Ubuntu: cat ~/.config/claude-buddy/token; right-click to paste)").Trim()
        if (-not $token) { Write-Host "empty, try again" -ForegroundColor Yellow }
    } until ($token)
    Set-Content -Path $TokenFile -Value $token -NoNewline -Encoding ascii
}
Write-Host ("token: {0} ({1} chars)" -f $TokenFile, (Read-TokenFile).Length)

Step "GPU check (loads $Model$(if (-not $NoVoice) { ' and XTTS-v2' }) once; the first run downloads them)"
$HubDir = Join-Path $Repo "hub"
$check = @"
import sys
sys.path.insert(0, r'$HubDir')
from buddy_hub.worker import add_cuda_dll_dirs, gpu_status
add_cuda_dll_dirs()
from buddy_hub.stt import Transcriber
print('gpu:', gpu_status())
Transcriber(model='$Model', device='cuda', compute_type='float16')._load()
print('model loaded on cuda: OK')
"@
if (-not $NoVoice) {
    $check += @"

from buddy_hub.worker import XttsSpeaker
import time
voice = XttsSpeaker()
voice.load()
started = time.time()
wav = voice.synthesize('Ol\u00e1! Eu sou o Buddy, e agora falo pela placa de v\u00eddeo.')
print('voice on cuda: OK (%d KB in %.1fs)' % (len(wav) // 1024, time.time() - started))
"@
}
$checkFile = Join-Path $env:TEMP "claude-buddy-gpu-check.py"
Set-Content -Path $checkFile -Value $check -Encoding ascii
Push-Location (Join-Path $Repo "hub")
try { Invoke-Native "GPU check" { & $Python $checkFile } }
finally { Pop-Location; Remove-Item $checkFile -ErrorAction SilentlyContinue }

Step "Firewall: TCP $Port from Tailscale (100.64.0.0/10) only"
$rule = "Claude Buddy STT (Tailscale)"
Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any | Out-Null

Step "Logon task: $TaskName"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$action = New-ScheduledTaskAction -Execute $PythonW `
    -Argument "-m buddy_hub.worker --host 0.0.0.0 --port $Port --log-file `"$LogDir\worker.log`"" `
    -WorkingDirectory (Join-Path $Repo "hub")
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Whisper on the GPU for the Claude Buddy hub (Ubuntu)." -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

Step "Waiting for the worker"
$token = Read-TokenFile
for ($i = 0; $i -lt 60; $i++) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -Headers @{ "X-Buddy-Token" = $token } -TimeoutSec 2
        Write-Host ("worker up: model {0}, gpu {1}" -f $h.model, ($h.gpu | ConvertTo-Json -Compress)) -ForegroundColor Green
        Write-Host "`nOn Ubuntu, point the hub here (Tailscale name of this PC: $env:COMPUTERNAME):"
        Write-Host "  BUDDY_STT_REMOTE=http://$($env:COMPUTERNAME.ToLower()):$Port"
        exit 0
    } catch { Start-Sleep -Seconds 2 }
}
throw "Worker did not answer on port $Port. Log: $LogDir\worker.log"
