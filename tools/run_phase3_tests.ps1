param(
    [string]$ProjectRoot = "C:\TH_Works\SBA",
    [switch]$InstallDevRequirements,
    [switch]$AllTests,
    [switch]$NoCapture,
    [switch]$StopOnFail
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param(
        [string]$Message,
        [string]$LogFile
    )

    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Assert-PathExists {
    param(
        [string]$PathToCheck,
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $PathToCheck)) {
        throw "$Label が見つからない: $PathToCheck"
    }
}

function Get-TestTargets {
    param(
        [string]$TestsDirectory,
        [string]$LogFile
    )

    $candidates = @(
        "test_engine_router.py",
        "test_enginerouter.py",
        "test_vram_guard.py",
        "test_vramguard.py",
        "test_whisper_transcriber.py",
        "test_whispertranscriber.py",
        "test_tier1.py",
        "test_tier2.py",
        "test_tier3.py",
        "test_phase3.py",
        "test_phase_comprehensive.py",
        "test_phase_final_validation.py",
        "testinference.py",
        "testphasecomprehensive.py",
        "testphasefinalvalidation.py"
    )

    $found = New-Object System.Collections.Generic.List[string]

    foreach ($name in $candidates) {
        $path = Join-Path $TestsDirectory $name
        if (Test-Path -LiteralPath $path) {
            $found.Add($path)
        }
    }

    if ($found.Count -eq 0) {
        Write-Log -Message "Phase3候補ファイル名で見つからないため、tests配下を内容ベースで探索する" -LogFile $LogFile

        $pattern = "engine_router|enginerouter|vram_guard|vramguard|whisper_transcriber|whispertranscriber|tier1|tier2|tier3|Phase 3|Phase3|inference"
        $dynamicMatches = Get-ChildItem -Path $TestsDirectory -File -Filter "*.py" -Recurse | Where-Object {
            try {
                (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8) -match $pattern
            }
            catch {
                $false
            }
        } | Select-Object -ExpandProperty FullName

        foreach ($match in $dynamicMatches) {
            $found.Add($match)
        }
    }

    return $found | Select-Object -Unique
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$VenvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
$PytestIni = Join-Path $ProjectRoot "pytest.ini"
$RequirementsDev = Join-Path $ProjectRoot "requirements-dev.txt"
$TestsDir = Join-Path $ProjectRoot "tests"
$OutputRoot = Join-Path $ProjectRoot "output\phase3_tests"

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $OutputRoot "phase3_test_run_$timestamp.log"
$ResultFile = Join-Path $OutputRoot "phase3_test_result_$timestamp.txt"
$JunitFile = Join-Path $OutputRoot "phase3_test_junit_$timestamp.xml"

Write-Log -Message "=== SBA Phase 3 テスト実行開始 ===" -LogFile $LogFile
Write-Log -Message "ProjectRoot = $ProjectRoot" -LogFile $LogFile

Assert-PathExists -PathToCheck $VenvActivate -Label ".venv Activate script"
Assert-PathExists -PathToCheck $TestsDir -Label "tests ディレクトリ"

if (Test-Path -LiteralPath $PytestIni) {
    Write-Log -Message "pytest.ini を検出: $PytestIni" -LogFile $LogFile
}
else {
    Write-Log -Message "pytest.ini は未検出。pytest のデフォルト設定で続行" -LogFile $LogFile
}

Write-Log -Message "仮想環境を有効化" -LogFile $LogFile
. $VenvActivate

$pythonVersion = & python --version 2>&1
Write-Log -Message "Python version = $pythonVersion" -LogFile $LogFile

if ($InstallDevRequirements) {
    if (Test-Path -LiteralPath $RequirementsDev) {
        Write-Log -Message "requirements-dev.txt をインストール" -LogFile $LogFile
        & python -m pip install -r $RequirementsDev 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Host
    }
    else {
        Write-Log -Message "requirements-dev.txt が見つからないためスキップ" -LogFile $LogFile
    }
}

Write-Log -Message "pytest の利用可否を確認" -LogFile $LogFile
& python -m pytest --version 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Host

$targets = Get-TestTargets -TestsDirectory $TestsDir -LogFile $LogFile

if ($AllTests) {
    Write-Log -Message "AllTests 指定あり。tests 全体を実行する" -LogFile $LogFile
    $targets = @($TestsDir)
}
elseif (-not $targets -or $targets.Count -eq 0) {
    Write-Log -Message "Phase3関連の絞り込み対象が見つからないため、tests 全体を実行する" -LogFile $LogFile
    $targets = @($TestsDir)
}

Write-Log -Message "実行対象一覧:" -LogFile $LogFile
foreach ($target in $targets) {
    Write-Log -Message " - $target" -LogFile $LogFile
}

$pytestArgs = New-Object System.Collections.Generic.List[string]
$pytestArgs.Add("-m")
$pytestArgs.Add("pytest")

foreach ($target in $targets) {
    $pytestArgs.Add($target)
}

$pytestArgs.Add("-v")
$pytestArgs.Add("--tb=short")
$pytestArgs.Add("--junitxml=$JunitFile")

if ($NoCapture) {
    $pytestArgs.Add("-s")
}

if ($StopOnFail) {
    $pytestArgs.Add("-x")
}

Write-Log -Message ("pytest 実行コマンド: python " + ($pytestArgs -join " ")) -LogFile $LogFile

$allOutput = & python @pytestArgs 2>&1
$exitCode = $LASTEXITCODE

$allOutput | Tee-Object -FilePath $LogFile -Append | Out-Host

$summaryLines = New-Object System.Collections.Generic.List[string]
$summaryLines.Add("SBA Phase 3 Test Result")
$summaryLines.Add("GeneratedAt : $((Get-Date).ToString('s'))")
$summaryLines.Add("ProjectRoot : $ProjectRoot")
$summaryLines.Add("Python      : $pythonVersion")
$summaryLines.Add("ExitCode    : $exitCode")
$summaryLines.Add("JunitXml    : $JunitFile")
$summaryLines.Add("")
$summaryLines.Add("Targets:")
foreach ($target in $targets) {
    $summaryLines.Add(" - $target")
}
$summaryLines.Add("")
$summaryLines.Add("LastOutput:")
$allOutput | Select-Object -Last 30 | ForEach-Object {
    $summaryLines.Add($_.ToString())
}

$summaryLines | Set-Content -LiteralPath $ResultFile -Encoding UTF8

if ($exitCode -eq 0) {
    Write-Log -Message "Phase 3 テスト成功" -LogFile $LogFile
    Write-Log -Message "ResultFile = $ResultFile" -LogFile $LogFile
    Write-Log -Message "JunitFile  = $JunitFile" -LogFile $LogFile
    exit 0
}
else {
    Write-Log -Message "Phase 3 テスト失敗。ExitCode = $exitCode" -LogFile $LogFile
    Write-Log -Message "ResultFile = $ResultFile" -LogFile $LogFile
    Write-Log -Message "JunitFile  = $JunitFile" -LogFile $LogFile
    exit $exitCode
}
