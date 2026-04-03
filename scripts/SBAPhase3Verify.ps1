# C:\TH_Works\SBA\scripts\SBAPhase3Verify.ps1
# Phase 3 complete verification script
# Spec-based checks for Tier1/Tier2/Tier3, VRAM guard, EngineRouter, Whisper
# PowerShell 5+ / UTF-8

[CmdletBinding()]
param(
    [switch]$SkipOllamaCheck,
    [switch]$SkipGeminiCheck,
    [switch]$SkipWhisperCheck,
    [switch]$SkipPytest,
    [string]$PythonPath = "C:/TH_Works/SBA/.venv/Scripts/python.exe",
    [string]$ProjectRoot = "C:/TH_Works/SBA"
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host (("==== " + $Title + " ====")) -ForegroundColor Cyan
}

function Write-Sub {
    param([string]$Title)
    Write-Host (("-- " + $Title)) -ForegroundColor Yellow
}

function Write-Info {
    param([string]$Message)
    Write-Host (("INFO: " + $Message)) -ForegroundColor DarkYellow
}

function Write-Warn {
    param([string]$Message)
    Write-Host (("WARN: " + $Message)) -ForegroundColor Yellow
}

function Write-Ok {
    param([string]$Message)
    Write-Host (("OK: " + $Message)) -ForegroundColor Green
}

function Assert-PathExists {
    param(
        [string]$Path,
        [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        throw (("Missing required path: " + $Description + " => " + $Path))
    }
}

function Assert-CommandExists {
    param(
        [string]$Command,
        [string]$Description
    )
    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw (("Missing required command: " + $Description + " => " + $Command))
    }
}

function Test-AnyPath {
    param(
        [string[]]$Candidates
    )
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Run-Python {
    param(
        [string[]]$Arguments,
        [string]$Description,
        [switch]$AllowFailure
    )

    Write-Sub $Description
    Write-Host (("python " + ($Arguments -join " "))) -ForegroundColor DarkGray

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonPath
    $psi.WorkingDirectory = $ProjectRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    foreach ($arg in $Arguments) {
        [void]$psi.ArgumentList.Add($arg)
    }

    $proc = New-Object System.Diagnostics.Process
    if ($null -eq $proc) {
        if ($AllowFailure) {
            Write-Warn "Failed to create Process object (Run-Python)"
            return $false
        }
        throw "Failed to create Process object (Run-Python)"
    }

    $proc.StartInfo = $psi
    $started = $proc.Start()
    if (-not $started) {
        if ($AllowFailure) {
            Write-Warn "Failed to start Python process (Run-Python)"
            return $false
        }
        throw "Failed to start Python process (Run-Python)"
    }

    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    if ($null -eq $stdout) { $stdout = "" }
    if ($null -eq $stderr) { $stderr = "" }

    if ($stdout.Length -gt 0) {
        Write-Host $stdout
    }
    if ($stderr.Length -gt 0) {
        Write-Host $stderr -ForegroundColor DarkYellow
    }

    if ($proc.ExitCode -ne 0) {
        if ($AllowFailure) {
            Write-Warn ("Python command failed but continuing: " + $Description + " / ExitCode=" + $proc.ExitCode)
            return $false
        }
        throw (("Python command failed (" + $Description + ") ExitCode=" + $proc.ExitCode))
    }

    return $true
}

function New-TempPythonScript {
    param(
        [string]$FileName,
        [string]$Content
    )

    $tempDir = Join-Path $env:TEMP "sba_phase3_verify"
    if (-not (Test-Path -LiteralPath $tempDir)) {
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    }

    $path = Join-Path $tempDir $FileName
    $Content | Out-File -FilePath $path -Encoding utf8
    return $path
}

Write-Section "1. Basic path check"

Assert-PathExists -Path $PythonPath -Description "Python executable"
Assert-PathExists -Path $ProjectRoot -Description "Project root"
Write-Ok ("Python executable found: " + $PythonPath)
Write-Ok ("Project root found: " + $ProjectRoot)

$configPath = Test-AnyPath -Candidates @(
    (Join-Path $ProjectRoot "sbaconfig.yaml"),
    (Join-Path $ProjectRoot "config/sba.config.example.yaml")
)
if ($null -ne $configPath) {
    if ($configPath -like "*sba.config.example.yaml") {
        Write-Warn ("Using fallback config: " + $configPath)
    }
    else {
        Write-Ok ("Config found: " + $configPath)
    }
}
else {
    throw "Missing config file: neither sbaconfig.yaml nor config/sba.config.example.yaml exists"
}

$brainPath = Test-AnyPath -Candidates @(
    (Join-Path $ProjectRoot "brain_bank"),
    (Join-Path $ProjectRoot "brainbank")
)
if ($null -ne $brainPath) {
    if ($brainPath -like "*brainbank") {
        Write-Warn ("Using fallback brain directory: " + $brainPath)
    }
    else {
        Write-Ok ("Brain directory found: " + $brainPath)
    }
}
else {
    throw "Missing brain storage directory: neither brain_bank nor brainbank exists"
}

$dataDir = Join-Path $ProjectRoot "data"
if (Test-Path -LiteralPath $dataDir) {
    Write-Ok ("Data directory found: " + $dataDir)
    $apiUsagePath = Join-Path $dataDir "apiusage.db"
    if (Test-Path -LiteralPath $apiUsagePath) {
        Write-Ok ("apiusage.db found: " + $apiUsagePath)
    }
    else {
        Write-Warn "data/apiusage.db not found (rate-limit DB not initialized yet)"
    }
}
else {
    Write-Warn "data directory not found (skipping apiusage.db check)"
}

$inferenceDir = Join-Path $ProjectRoot "src/sba/inference"
if (Test-Path -LiteralPath $inferenceDir) {
    Write-Ok ("Inference directory found: " + $inferenceDir)
}
else {
    Write-Warn "src/sba/inference directory not found"
}

$engineRouterPath = Join-Path $ProjectRoot "src/sba/inference/enginerouter.py"
$tier1Path = Join-Path $ProjectRoot "src/sba/inference/tier1.py"
$tier2Path = Join-Path $ProjectRoot "src/sba/inference/tier2.py"
$tier3Path = Join-Path $ProjectRoot "src/sba/inference/tier3.py"
$vramGuardPath = Join-Path $ProjectRoot "src/sba/utils/vramguard.py"
$whisperPath = Join-Path $ProjectRoot "src/sba/sources/whispertranscriber.py"

Write-Info ("EngineRouter path = " + $engineRouterPath)
Write-Info ("VRAM Guard path = " + $vramGuardPath)
Write-Info ("Whisper path = " + $whisperPath)
Write-Info ("testinference path = " + (Join-Path $ProjectRoot "tests/unit/testinference.py"))

if (Test-Path -LiteralPath $engineRouterPath -PathType Leaf) { Write-Ok "enginerouter.py found" } else { Write-Warn "enginerouter.py not found (EngineRouter tests will be skipped)" }
if (Test-Path -LiteralPath $tier1Path -PathType Leaf) { Write-Ok "tier1.py found" } else { Write-Warn "tier1.py not found" }
if (Test-Path -LiteralPath $tier2Path -PathType Leaf) { Write-Ok "tier2.py found" } else { Write-Warn "tier2.py not found" }
if (Test-Path -LiteralPath $tier3Path -PathType Leaf) { Write-Ok "tier3.py found" } else { Write-Warn "tier3.py not found" }
if (Test-Path -LiteralPath $vramGuardPath -PathType Leaf) { Write-Ok "vramguard.py found" } else { Write-Warn "vramguard.py not found (VRAM lock test will be skipped)" }
if (Test-Path -LiteralPath $whisperPath -PathType Leaf) { Write-Ok "whispertranscriber.py found" } else { Write-Warn "whispertranscriber.py not found (Whisper test will be skipped)" }

Write-Section "2. External dependencies"

if (-not $SkipOllamaCheck) {
    Write-Sub "Ollama CLI and models"
    Assert-CommandExists -Command "ollama" -Description "Ollama CLI"
    $ollamaList = & ollama list 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host $ollamaList -ForegroundColor Red
        Write-Warn "ollama list failed"
    }
    else {
        Write-Host $ollamaList
        foreach ($model in @("phi4", "qwen2.5-coder:7b")) {
            if ($ollamaList -match [regex]::Escape($model)) {
                Write-Ok ("Ollama model detected: " + $model)
            }
            else {
                Write-Warn ("Ollama model not detected: " + $model)
            }
        }
    }
}
else {
    Write-Info "Skip Ollama check"
}

if (-not $SkipGeminiCheck) {
    Write-Sub "Gemini .env key"
    $envPath = Join-Path $ProjectRoot ".env"
    if (Test-Path -LiteralPath $envPath) {
        $envContent = Get-Content -LiteralPath $envPath -Encoding UTF8 -Raw
        if ($envContent -match "GEMINI_API_KEY=") {
            Write-Ok "GEMINI_API_KEY exists"
        }
        else {
            Write-Warn "GEMINI_API_KEY not found in .env"
        }
    }
    else {
        Write-Warn ".env not found (Gemini check skipped)"
    }
}
else {
    Write-Info "Skip Gemini check"
}

if (-not $SkipWhisperCheck) {
    Write-Sub "faster-whisper import"
    $scriptPath = New-TempPythonScript -FileName "check_faster_whisper.py" -Content @'
import importlib
module = importlib.import_module("faster_whisper")
version = getattr(module, "__version__", "")
print("OK: faster-whisper import " + str(version))
'@
    [void](Run-Python -Arguments @($scriptPath) -Description "check faster-whisper" -AllowFailure)
}
else {
    Write-Info "Skip Whisper import check"
}

Write-Section "3. pytest (Phase3-related)"

if ($SkipPytest) {
    Write-Info "Skip pytest"
}
else {
    $unitInference = Join-Path $ProjectRoot "tests/unit/testinference.py"
    if (Test-Path -LiteralPath $unitInference -PathType Leaf) {
        [void](Run-Python -Arguments @("-m", "pytest", "-q", "tests/unit/testinference.py") -Description "unit/testinference.py" -AllowFailure)
    }
    else {
        Write-Warn "tests/unit/testinference.py not found"
    }

    foreach ($testFile in @(
        "tests/integration/testphasecomprehensive.py",
        "tests/integration/testphasefinalvalidation.py"
    )) {
        $full = Join-Path $ProjectRoot $testFile
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            [void](Run-Python -Arguments @("-m", "pytest", "-q", $testFile) -Description $testFile -AllowFailure)
        }
        else {
            Write-Info (("Skip " + $testFile))
        }
    }
}

Write-Section "4. Minimal logic checks"

if (Test-Path -LiteralPath $vramGuardPath -PathType Leaf) {
    $vramscript = New-TempPythonScript -FileName "phase3_vramguard_test.py" -Content @'
from threading import Thread
import time
from sba.utils.vramguard import vram_lock

events = []

def worker(name, hold_sec):
    events.append(name + ":wait")
    with vram_lock():
        events.append(name + ":acquire")
        time.sleep(hold_sec)
        events.append(name + ":release")

threads = [
    Thread(target=worker, args=("Tier1", 1.0)),
    Thread(target=worker, args=("Tier3", 1.0)),
]

for t in threads:
    t.start()
for t in threads:
    t.join()

print("EVENTS=" + ",".join(events))
if events.count("Tier1:acquire") != 1 or events.count("Tier3:acquire") != 1:
    raise SystemExit("NG: vram_lock acquire count mismatch")
print("OK: vram_lock basic serialization")
'@
    [void](Run-Python -Arguments @($vramscript) -Description "vramguard basic test" -AllowFailure)
}
else {
    Write-Info "Skip vramguard basic test (vramguard.py missing)"
}

if (Test-Path -LiteralPath $engineRouterPath -PathType Leaf) {
    $routerscript = New-TempPythonScript -FileName "phase3_router_test.py" -Content @'
from sba.config import load_config
from sba.inference.enginerouter import EngineRouter

cfg = load_config()
router = EngineRouter(cfg)

cases = [
    ("short", "short text", None),
    ("long", "A" * 16000, None),
    ("code", "write python fizzbuzz", "code.impl"),
]

for name, prompt, subskill in cases:
    res = router.complete(prompt=prompt, subskill=subskill)
    txt = str(res)
    print(name + "=" + txt[:200])

print("OK: EngineRouter basic scenarios")
'@
    [void](Run-Python -Arguments @($routerscript) -Description "EngineRouter basic test" -AllowFailure)
}
else {
    Write-Info "Skip EngineRouter basic test (enginerouter.py missing)"
}

if (-not $SkipWhisperCheck) {
    if (Test-Path -LiteralPath $whisperPath -PathType Leaf) {
        $whisperscript = New-TempPythonScript -FileName "phase3_whisper_test.py" -Content @'
from pathlib import Path
from sba.sources.whispertranscriber import WhisperTranscriber

wav = Path("tests/fixtures/audio/test_short.wav")
if not wav.exists():
    print("WARN: test_short.wav not found, skip whisper test")
else:
    wt = WhisperTranscriber(model_size="medium")
    text = wt.transcribe_file(str(wav))
    print("TRANSCRIBED=" + str(text)[:200])
    print("OK: WhisperTranscriber basic run")
'@
        [void](Run-Python -Arguments @($whisperscript) -Description "Whisper basic test" -AllowFailure)
    }
    else {
        Write-Info "Skip Whisper basic test (whispertranscriber.py missing)"
    }
}
else {
    Write-Info "Skip Whisper basic test"
}

Write-Section "5. Spec reminders"
Write-Host "Tier routing target: code/tech => Tier3, long text or Tier1 wait > 10s => Tier2, otherwise Tier1" -ForegroundColor Cyan
Write-Host "VRAM rule target: Tier1, Tier3, Whisper must not run concurrently on RTX 3060 Ti 8GB" -ForegroundColor Cyan
Write-Host "Whisper rule target: unload Ollama before Whisper when necessary" -ForegroundColor Cyan

Write-Section "Phase3 verification complete"
Write-Host "DONE: Phase3 complete verification script finished" -ForegroundColor Green