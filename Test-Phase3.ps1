Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

<#
.SYNOPSIS
SBA Framework Phase 3 仕様一致テスト
.DESCRIPTION
Phase 3（推論エンジン + VRAM制御）の成果物が、
設計書どおりのファイル構成・主要実装要素・必須条件を満たしているかを静的検査する。
Windows 11 / PowerShell 5.1+ 前提。
出力は UTF-8 の JSON / TXT レポート。
#>

param(
    [string]$ProjectRoot = "C:/SBA",
    [switch]$VerboseReport
)

$OutputDir = Join-Path $ProjectRoot "output"
$SrcRoot = Join-Path $ProjectRoot "src/sba"
$InferenceRoot = Join-Path $SrcRoot "inference"
$UtilsRoot = Join-Path $SrcRoot "utils"
$SourcesRoot = Join-Path $SrcRoot "sources"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$JsonReportPath = Join-Path $OutputDir "phase3_spec_test_$Timestamp.json"
$TextReportPath = Join-Path $OutputDir "phase3_spec_test_$Timestamp.txt"

$Results = New-Object System.Collections.Generic.List[object]

function Add-TestResult {
    param(
        [string]$Category,
        [string]$Name,
        [bool]$Passed,
        [string]$Detail,
        [string]$FilePath = ""
    )

    $Results.Add([PSCustomObject]@{
        category = $Category
        name     = $Name
        passed   = $Passed
        detail   = $Detail
        file     = $FilePath
    })
}

function Test-FileExists {
    param(
        [string]$Category,
        [string]$Name,
        [string]$PathToCheck
    )

    if (Test-Path $PathToCheck) {
        Add-TestResult -Category $Category -Name $Name -Passed $true -Detail "ファイル存在OK" -FilePath $PathToCheck
        return $true
    }

    Add-TestResult -Category $Category -Name $Name -Passed $false -Detail "ファイルが存在しない" -FilePath $PathToCheck
    return $false
}

function Get-FileTextUtf8 {
    param(
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8
}

function Test-ContainsPattern {
    param(
        [string]$Category,
        [string]$Name,
        [string]$FilePath,
        [string[]]$Patterns,
        [string]$Mode = "All"
    )

    $Text = Get-FileTextUtf8 -Path $FilePath
    if ($null -eq $Text) {
        Add-TestResult -Category $Category -Name $Name -Passed $false -Detail "対象ファイルを読めない" -FilePath $FilePath
        return
    }

    $Matched = @()
    foreach ($Pattern in $Patterns) {
        if ($Text -match $Pattern) {
            $Matched += $Pattern
        }
    }

    $Passed = $false
    switch ($Mode) {
        "All" { $Passed = ($Matched.Count -eq $Patterns.Count) }
        "Any" { $Passed = ($Matched.Count -ge 1) }
        default { throw "Mode は All / Any のみ対応" }
    }

    $Detail = if ($Passed) {
        "パターン一致OK: $($Matched -join ', ')"
    }
    else {
        "不足パターンあり。検出: $($Matched -join ', ') / 必要: $($Patterns -join ', ')"
    }

    Add-TestResult -Category $Category -Name $Name -Passed $Passed -Detail $Detail -FilePath $FilePath
}

function Test-NotContainsPattern {
    param(
        [string]$Category,
        [string]$Name,
        [string]$FilePath,
        [string[]]$Patterns
    )

    $Text = Get-FileTextUtf8 -Path $FilePath
    if ($null -eq $Text) {
        Add-TestResult -Category $Category -Name $Name -Passed $false -Detail "対象ファイルを読めない" -FilePath $FilePath
        return
    }

    $Found = @()
    foreach ($Pattern in $Patterns) {
        if ($Text -match $Pattern) {
            $Found += $Pattern
        }
    }

    $Passed = ($Found.Count -eq 0)
    $Detail = if ($Passed) {
        "禁止パターン未検出"
    }
    else {
        "禁止パターンを検出: $($Found -join ', ')"
    }

    Add-TestResult -Category $Category -Name $Name -Passed $Passed -Detail $Detail -FilePath $FilePath
}

Write-Host "=== SBA Phase 3 仕様一致テスト開始 ==="
Write-Host "ProjectRoot: $ProjectRoot"

# ------------------------------------------------------------
# 1. 必須ファイル存在チェック
# ------------------------------------------------------------
$Tier1Path   = Join-Path $InferenceRoot "tier1.py"
$Tier2Path   = Join-Path $InferenceRoot "tier2.py"
$Tier3Path   = Join-Path $InferenceRoot "tier3.py"
$RouterPath  = Join-Path $InferenceRoot "engine_router.py"
$VramPath    = Join-Path $UtilsRoot "vram_guard.py"
$WhisperPath = Join-Path $SourcesRoot "whisper_transcriber.py"

Test-FileExists -Category "files" -Name "tier1.py exists" -PathToCheck $Tier1Path | Out-Null
Test-FileExists -Category "files" -Name "tier2.py exists" -PathToCheck $Tier2Path | Out-Null
Test-FileExists -Category "files" -Name "tier3.py exists" -PathToCheck $Tier3Path | Out-Null
Test-FileExists -Category "files" -Name "engine_router.py exists" -PathToCheck $RouterPath | Out-Null
Test-FileExists -Category "files" -Name "vram_guard.py exists" -PathToCheck $VramPath | Out-Null
Test-FileExists -Category "files" -Name "whisper_transcriber.py exists" -PathToCheck $WhisperPath | Out-Null

# ------------------------------------------------------------
# 2. EngineRouter 仕様チェック
# ------------------------------------------------------------
Test-ContainsPattern -Category "engine_router" -Name "EngineRouter class exists" -FilePath $RouterPath -Patterns @(
    "class\s+EngineRouter"
)

Test-ContainsPattern -Category "engine_router" -Name "route method exists" -FilePath $RouterPath -Patterns @(
    "def\s+route",
    "async\s+def\s+route"
) -Mode "Any"

Test-ContainsPattern -Category "engine_router" -Name "Tier3 rule: code + tech brain" -FilePath $RouterPath -Patterns @(
    "code",
    "tech",
    "tier3|qwen"
) -Mode "All"

Test-ContainsPattern -Category "engine_router" -Name "Tier2 rule: token > 8000" -FilePath $RouterPath -Patterns @(
    "8000",
    "tier2|gemini"
) -Mode "All"

Test-ContainsPattern -Category "engine_router" -Name "Tier2 rule: tier1 wait > 10 sec" -FilePath $RouterPath -Patterns @(
    "10",
    "wait",
    "tier2|gemini"
) -Mode "All"

Test-ContainsPattern -Category "engine_router" -Name "Tier1 default fallback" -FilePath $RouterPath -Patterns @(
    "tier1|phi"
) -Mode "Any"

# ------------------------------------------------------------
# 3. Tier1 仕様チェック
# ------------------------------------------------------------
Test-ContainsPattern -Category "tier1" -Name "Tier1 uses Ollama" -FilePath $Tier1Path -Patterns @(
    "ollama"
) -Mode "Any"

Test-ContainsPattern -Category "tier1" -Name "Tier1 wait time measurement" -FilePath $Tier1Path -Patterns @(
    "wait",
    "time|perf_counter|monotonic"
) -Mode "All"

Test-ContainsPattern -Category "tier1" -Name "Tier1 queue or serialization" -FilePath $Tier1Path -Patterns @(
    "asyncio\.Queue|Queue|Semaphore|Lock"
) -Mode "Any"

# ------------------------------------------------------------
# 4. Tier2 仕様チェック
# ------------------------------------------------------------
Test-ContainsPattern -Category "tier2" -Name "Tier2 uses Gemini" -FilePath $Tier2Path -Patterns @(
    "gemini|google\.generativeai|generativeai"
) -Mode "Any"

Test-ContainsPattern -Category "tier2" -Name "Tier2 checks API remaining quota" -FilePath $Tier2Path -Patterns @(
    "api_usage|remaining|quota|limit|100"
) -Mode "Any"

Test-ContainsPattern -Category "tier2" -Name "Tier2 fallback logic exists" -FilePath $Tier2Path -Patterns @(
    "fallback|tier1"
) -Mode "Any"

# ------------------------------------------------------------
# 5. Tier3 仕様チェック
# ------------------------------------------------------------
Test-ContainsPattern -Category "tier3" -Name "Tier3 uses Qwen coder" -FilePath $Tier3Path -Patterns @(
    "qwen",
    "coder"
) -Mode "All"

Test-ContainsPattern -Category "tier3" -Name "Tier3 code generation intent" -FilePath $Tier3Path -Patterns @(
    "code|implement|refactor|test"
) -Mode "Any"

Test-ContainsPattern -Category "tier3" -Name "Tier3 links with VRAM guard" -FilePath $Tier3Path -Patterns @(
    "vram",
    "guard|lock"
) -Mode "Any"

# ------------------------------------------------------------
# 6. VRAM Guard 仕様チェック
# ------------------------------------------------------------
Test-ContainsPattern -Category "vram_guard" -Name "Global lock exists" -FilePath $VramPath -Patterns @(
    "threading\.Lock|Lock\(\)"
) -Mode "Any"

Test-ContainsPattern -Category "vram_guard" -Name "Tracks current running models" -FilePath $VramPath -Patterns @(
    "current.*model|running.*model|active.*model|loaded.*model"
) -Mode "Any"

Test-ContainsPattern -Category "vram_guard" -Name "Tier1 + Tier3 conflict handled" -FilePath $VramPath -Patterns @(
    "tier1|phi",
    "tier3|qwen"
) -Mode "All"

Test-ContainsPattern -Category "vram_guard" -Name "Tier1 + Whisper conflict handled" -FilePath $VramPath -Patterns @(
    "tier1|phi",
    "whisper"
) -Mode "All"

Test-ContainsPattern -Category "vram_guard" -Name "Unload Ollama before Whisper" -FilePath $VramPath -Patterns @(
    "unload",
    "ollama",
    "whisper"
) -Mode "All"

Test-ContainsPattern -Category "vram_guard" -Name "Reload Ollama after Whisper" -FilePath $VramPath -Patterns @(
    "reload|load",
    "ollama",
    "whisper"
) -Mode "All"

# ------------------------------------------------------------
# 7. Whisper 仕様チェック
# ------------------------------------------------------------
Test-ContainsPattern -Category "whisper" -Name "Whisper implementation exists" -FilePath $WhisperPath -Patterns @(
    "Whisper|Transcriber|faster_whisper|faster-whisper"
) -Mode "Any"

Test-ContainsPattern -Category "whisper" -Name "Uses medium model recommendation" -FilePath $WhisperPath -Patterns @(
    "medium"
) -Mode "Any"

Test-ContainsPattern -Category "whisper" -Name "Whisper coordinates with VRAM guard" -FilePath $WhisperPath -Patterns @(
    "vram",
    "guard|lock|unload|reload"
) -Mode "Any"

# ------------------------------------------------------------
# 8. 禁止・注意チェック
# ------------------------------------------------------------
Test-NotContainsPattern -Category "anti_pattern" -Name "No direct Tier1+Tier3 parallel hint in router" -FilePath $RouterPath -Patterns @(
    "gather\(.+tier1.+tier3",
    "ThreadPool.+tier1.+tier3"
)

# ------------------------------------------------------------
# 9. 集計
# ------------------------------------------------------------
$PassedCount = ($Results | Where-Object { $_.passed }).Count
$FailedCount = ($Results | Where-Object { -not $_.passed }).Count
$TotalCount = $Results.Count
$SuccessRate = if ($TotalCount -gt 0) {
    [Math]::Round(($PassedCount / $TotalCount) * 100, 2)
}
else {
    0
}

$Summary = [PSCustomObject]@{
    generated_at   = (Get-Date).ToString("s")
    project_root   = $ProjectRoot
    total_tests    = $TotalCount
    passed_tests   = $PassedCount
    failed_tests   = $FailedCount
    success_rate   = $SuccessRate
    overall_status = if ($FailedCount -eq 0) { "PASS" } elseif ($PassedCount -gt 0) { "PARTIAL" } else { "FAIL" }
    results        = $Results
}

$Summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $JsonReportPath -Encoding UTF8

$TextLines = New-Object System.Collections.Generic.List[string]
$TextLines.Add("SBA Phase 3 仕様一致テスト")
$TextLines.Add("GeneratedAt: $((Get-Date).ToString("s"))")
$TextLines.Add("ProjectRoot : $ProjectRoot")
$TextLines.Add("Overall     : $($Summary.overall_status)")
$TextLines.Add("Passed      : $PassedCount / $TotalCount")
$TextLines.Add("Failed      : $FailedCount")
$TextLines.Add("SuccessRate : $SuccessRate %")
$TextLines.Add("")

foreach ($Item in $Results) {
    $Status = if ($Item.passed) { "[PASS]" } else { "[FAIL]" }
    $TextLines.Add("$Status [$($Item.category)] $($Item.name)")
    $TextLines.Add("  Detail: $($Item.detail)")
    if ($Item.file) {
        $TextLines.Add("  File  : $($Item.file)")
    }
    $TextLines.Add("")
}

$TextLines | Set-Content -LiteralPath $TextReportPath -Encoding UTF8

Write-Host ""
Write-Host "=== テスト完了 ==="
Write-Host "Overall     : $($Summary.overall_status)"
Write-Host "Passed      : $PassedCount / $TotalCount"
Write-Host "Failed      : $FailedCount"
Write-Host "SuccessRate : $SuccessRate %"
Write-Host ""
Write-Host "JSON Report : $JsonReportPath"
Write-Host "Text Report : $TextReportPath"

if ($VerboseReport) {
    Write-Host ""
    Get-Content -LiteralPath $TextReportPath -Encoding UTF8
}