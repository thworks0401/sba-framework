# Test-Phase1.ps1
# SBA Framework Phase 1 テスト
# 対象: Brain 管理基盤（BrainPackage / BlankTemplate / BrainManager / CLI）

[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\TH_Works\SBA",
    [string]$PythonExe = "python",
    [string]$OutputDir = "C:\TH_Works\SBA\test_results\phase1",
    [string]$Phase0ResultJson = "C:\TH_Works\SBA\test_results\phase0\phase0_test_result.json",
    [switch]$RunCliSmoke,
    [switch]$RunSandboxWriteTest,
    [switch]$UseVenvPython
)

$ErrorActionPreference = "Stop"

function New-TestResult {
    param(
        [string]$Id,
        [string]$Name,
        [string]$Status,
        [string]$Details,
        [string]$Evidence = ""
    )
    [PSCustomObject]@{
        id       = $Id
        name     = $Name
        status   = $Status
        details  = $Details
        evidence = $Evidence
    }
}

function Add-Result {
    param([object]$Result)
    $script:Results.Add($Result) | Out-Null
    $color = switch ($Result.status) {
        "PASS" { "Green" }
        "FAIL" { "Red" }
        "WARN" { "Yellow" }
        "SKIP" { "DarkYellow" }
        default { "White" }
    }
    Write-Host ("[{0}] {1} - {2}" -f $Result.status, $Result.id, $Result.name) -ForegroundColor $color
    if ($Result.details) {
        Write-Host ("       {0}" -f $Result.details)
    }
}

function Ensure-OutputDir {
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }
}

function Get-PythonPath {
    $venvPy = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if ($UseVenvPython -or (Test-Path $venvPy)) {
        if (Test-Path $venvPy) { return $venvPy }
    }
    return $PythonExe
}

function Test-Phase0Dependency {
    if (-not (Test-Path $Phase0ResultJson)) {
        Add-Result (New-TestResult -Id "P1-00" -Name "Phase 0 結果依存確認" -Status "FAIL" -Details "Phase 0 結果JSONが見つからない: $Phase0ResultJson" -Evidence $Phase0ResultJson)
        return
    }

    try {
        $data = Get-Content $Phase0ResultJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $failCount = @($data | Where-Object { $_.status -eq "FAIL" }).Count
        if ($failCount -eq 0) {
            Add-Result (New-TestResult -Id "P1-00" -Name "Phase 0 結果依存確認" -Status "PASS" -Details "Phase 0 に FAIL がないため Phase 1 テスト前提を満たす" -Evidence $Phase0ResultJson)
        } else {
            Add-Result (New-TestResult -Id "P1-00" -Name "Phase 0 結果依存確認" -Status "FAIL" -Details "Phase 0 に FAIL が $failCount 件ある。先に Phase 0 修正が必要" -Evidence $Phase0ResultJson)
        }
    } catch {
        Add-Result (New-TestResult -Id "P1-00" -Name "Phase 0 結果依存確認" -Status "FAIL" -Details "Phase 0 JSON の読取に失敗: $($_.Exception.Message)" -Evidence $Phase0ResultJson)
    }
}

function Test-Phase1Files {
    $required = @(
        "src\sba\brain\brain_package.py",
        "src\sba\brain\blank_template.py",
        "src\sba\brain\brain_manager.py",
        "src\sba\cli\brain_cmds.py",
        "src\sba\__main__.py",
        "tests\test_brain_package.py",
        "tests\test_brain_manager.py",
        "tests\test_blank_template.py",
        "tests\test_cli_integration.py",
        "tests\test_phase1_integration.py",
        "tests\test_phase_1_final.py"
    )

    $missing = @()
    foreach ($rel in $required) {
        if (-not (Test-Path (Join-Path $ProjectRoot $rel))) {
            $missing += $rel
        }
    }

    if ($missing.Count -eq 0) {
        Add-Result (New-TestResult -Id "P1-01" -Name "Phase 1 実装ファイル確認" -Status "PASS" -Details "Brain 管理基盤の主要コードとテストファイルを確認")
    } else {
        Add-Result (New-TestResult -Id "P1-01" -Name "Phase 1 実装ファイル確認" -Status "FAIL" -Details ("不足ファイル: " + ($missing -join ", ")))
    }
}

function Test-BrainBankStructure {
    $brainBank = Join-Path $ProjectRoot "brain_bank"
    if (-not (Test-Path -LiteralPath $brainBank)) {
        Add-Result (New-TestResult -Id "P1-02" -Name "brain_bank 構造確認" -Status "FAIL" -Details "brain_bank ディレクトリが見つからない" -Evidence $brainBank)
        return
    }

    $templateCandidates = @()
    foreach ($candidate in @(
        (Join-Path $brainBank "_blank_template"),
        (Join-Path $brainBank "blank_template")
    )) {
        if (Test-Path -LiteralPath $candidate) {
            $templateCandidates += $candidate
        }
    }

    $activePath = Join-Path $brainBank "[active]"
    $hasActive = Test-Path -LiteralPath $activePath

    $brainDirs = Get-ChildItem -LiteralPath $brainBank -Directory -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notin @("_blank_template", "blank_template", "[active]")
    }

    $issues = @()
    if ($templateCandidates.Count -eq 0) { $issues += "blank template 不在" }
    if (-not $hasActive) { $issues += "[active] 不在" }
    if ($brainDirs.Count -eq 0) { $issues += "保存済み Brain が0件" }

    if ($issues.Count -eq 0) {
        Add-Result (New-TestResult -Id "P1-02" -Name "brain_bank 構造確認" -Status "PASS" -Details ("template=" + ($templateCandidates -join ", ") + " / active=$activePath / saved brains=$($brainDirs.Count)") -Evidence $brainBank)
    } else {
        Add-Result (New-TestResult -Id "P1-02" -Name "brain_bank 構造確認" -Status "WARN" -Details ($issues -join " / ") -Evidence $brainBank)
    }
}

function Test-BrainPackageMetadata {
    $brainBank = Join-Path $ProjectRoot "brain_bank"
    $brains = Get-ChildItem -LiteralPath $brainBank -Directory -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notin @("_blank_template", "blank_template", "[active]")
    }

    if ($brains.Count -eq 0) {
        Add-Result (New-TestResult -Id "P1-03" -Name "Brain Package メタデータ確認" -Status "WARN" -Details "検査対象 Brain がないためスキップ相当" -Evidence $brainBank)
        return
    }

    $invalid = @()
    foreach ($brain in $brains) {
        $required = @(
            "metadata.json",
            "self_eval.json",
            "subskill_manifest.json",
            "experiment_log.db",
            "learning_timeline.db"
        )
        foreach ($item in $required) {
            if (-not (Test-Path -LiteralPath (Join-Path $brain.FullName $item))) {
                $invalid += "$($brain.Name): missing $item"
            }
        }
    }

    if ($invalid.Count -eq 0) {
        Add-Result (New-TestResult -Id "P1-03" -Name "Brain Package メタデータ確認" -Status "PASS" -Details "保存済み Brain の基本構成を確認" -Evidence "$($brains.Count) brains")
    } else {
        Add-Result (New-TestResult -Id "P1-03" -Name "Brain Package メタデータ確認" -Status "WARN" -Details ($invalid -join " | "))
    }
}

function Test-PythonImportPhase1 {
    $py = Get-PythonPath
    $scriptPath = Join-Path $OutputDir "phase1_import_check.py"

    @'
import importlib
import json
modules = [
    "sba",
    "sba.brain.brain_package",
    "sba.brain.blank_template",
    "sba.brain.brain_manager",
    "sba.cli.brain_cmds",
]
result = {}
for name in modules:
    try:
        importlib.import_module(name)
        result[name] = {"status": "PASS"}
    except Exception as e:
        result[name] = {"status": "FAIL", "error": str(e)}
print(json.dumps(result, ensure_ascii=False))
'@ | Out-File -FilePath $scriptPath -Encoding UTF8

    try {
        $env:PYTHONPATH = (Join-Path $ProjectRoot "src")
        $json = & $py $scriptPath 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($json)) {
            Add-Result (New-TestResult -Id "P1-04" -Name "Phase 1 Python import 確認" -Status "FAIL" -Details "import確認スクリプト実行失敗")
            return
        }

        $data = $json | ConvertFrom-Json
        $failed = @($data.PSObject.Properties | Where-Object { $_.Value.status -ne "PASS" } | ForEach-Object { $_.Name })
        $detailPath = Join-Path $OutputDir "phase1_import_check.json"
        $json | Out-File -FilePath $detailPath -Encoding UTF8

        if ($failed.Count -eq 0) {
            Add-Result (New-TestResult -Id "P1-04" -Name "Phase 1 Python import 確認" -Status "PASS" -Details "Phase 1 の主要モジュール import 成功" -Evidence $detailPath)
        } else {
            Add-Result (New-TestResult -Id "P1-04" -Name "Phase 1 Python import 確認" -Status "FAIL" -Details ("import失敗: " + ($failed -join ", ")) -Evidence $detailPath)
        }
    } catch {
        Add-Result (New-TestResult -Id "P1-04" -Name "Phase 1 Python import 確認" -Status "FAIL" -Details $_.Exception.Message)
    }
}

function Test-CliEntrypoint {
    $mainPy = Join-Path $ProjectRoot "src\sba\__main__.py"
    if (-not (Test-Path -LiteralPath $mainPy)) {
        Add-Result (New-TestResult -Id "P1-05" -Name "CLI エントリポイント確認" -Status "FAIL" -Details "src\sba\__main__.py が見つからない" -Evidence $mainPy)
        return
    }

    try {
        $content = Get-Content $mainPy -Raw -Encoding UTF8
        $checks = @(
            @{ label = "Typer app"; ok = ($content -match "Typer") },
            @{ label = "brain command reference"; ok = ($content -match "brain") }
        )
        $failed = @($checks | Where-Object { -not $_.ok } | ForEach-Object { $_.label })
        if ($failed.Count -eq 0) {
            Add-Result (New-TestResult -Id "P1-05" -Name "CLI エントリポイント確認" -Status "PASS" -Details "__main__.py に Typer / brain 系エントリの痕跡を確認" -Evidence $mainPy)
        } else {
            Add-Result (New-TestResult -Id "P1-05" -Name "CLI エントリポイント確認" -Status "WARN" -Details ("確認不足: " + ($failed -join ", ")) -Evidence $mainPy)
        }
    } catch {
        Add-Result (New-TestResult -Id "P1-05" -Name "CLI エントリポイント確認" -Status "FAIL" -Details $_.Exception.Message -Evidence $mainPy)
    }
}

function Test-CliSmoke {
    if (-not $RunCliSmoke) {
        Add-Result (New-TestResult -Id "P1-06" -Name "CLI スモークテスト" -Status "SKIP" -Details "-RunCliSmoke 未指定のためスキップ")
        return
    }

    $py = Get-PythonPath
    try {
        $env:PYTHONPATH = (Join-Path $ProjectRoot "src")
        $output = & $py -m sba --help 2>&1
        $text = ($output | Out-String)
        $detailPath = Join-Path $OutputDir "phase1_cli_help.txt"
        $text | Out-File -FilePath $detailPath -Encoding UTF8

        if ($LASTEXITCODE -eq 0 -and $text -match "brain") {
            Add-Result (New-TestResult -Id "P1-06" -Name "CLI スモークテスト" -Status "PASS" -Details "python -m sba --help 実行成功。brain サブコマンド表記を確認" -Evidence $detailPath)
        } else {
            Add-Result (New-TestResult -Id "P1-06" -Name "CLI スモークテスト" -Status "WARN" -Details "--help は返ったが期待文言不足、または終了コード異常" -Evidence $detailPath)
        }
    } catch {
        Add-Result (New-TestResult -Id "P1-06" -Name "CLI スモークテスト" -Status "FAIL" -Details $_.Exception.Message)
    }
}

function Test-PytestPhase1Presence {
    $pytestIni = Join-Path $ProjectRoot "pytest.ini"
    if (-not (Test-Path -LiteralPath $pytestIni)) {
        Add-Result (New-TestResult -Id "P1-07" -Name "pytest 構成確認" -Status "FAIL" -Details "pytest.ini が見つからない" -Evidence $pytestIni)
        return
    }

    $phase1Tests = @(
        "tests\test_phase_1_final.py",
        "tests\test_phase1_integration.py",
        "tests\test_cli_integration.py",
        "tests\test_brain_manager.py"
    )

    $missing = @()
    foreach ($rel in $phase1Tests) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $rel))) { $missing += $rel }
    }

    if ($missing.Count -eq 0) {
        Add-Result (New-TestResult -Id "P1-07" -Name "pytest 構成確認" -Status "PASS" -Details "Phase 1 関連 pytest ファイル群を確認" -Evidence $pytestIni)
    } else {
        Add-Result (New-TestResult -Id "P1-07" -Name "pytest 構成確認" -Status "WARN" -Details ("不足テスト: " + ($missing -join ", ")) -Evidence $pytestIni)
    }
}

function Test-SandboxWriteFlow {
    if (-not $RunSandboxWriteTest) {
        Add-Result (New-TestResult -Id "P1-08" -Name "Hot-Swap 疑似書込テスト" -Status "SKIP" -Details "-RunSandboxWriteTest 未指定のためスキップ")
        return
    }

    $sandboxRoot = Join-Path $OutputDir "sandbox"
    $sourceTemplate = $null
    foreach ($candidate in @(
        (Join-Path $ProjectRoot "brain_bank\blank_template"),
        (Join-Path $ProjectRoot "brain_bank\_blank_template")
    )) {
        if (Test-Path -LiteralPath $candidate) { $sourceTemplate = $candidate; break }
    }

    if (-not $sourceTemplate) {
        Add-Result (New-TestResult -Id "P1-08" -Name "Hot-Swap 疑似書込テスト" -Status "FAIL" -Details "テンプレート候補が見つからない")
        return
    }

    try {
        if (Test-Path $sandboxRoot) { Remove-Item $sandboxRoot -Recurse -Force }
        New-Item -ItemType Directory -Path $sandboxRoot -Force | Out-Null
        $clone1 = Join-Path $sandboxRoot "BrainA"
        $clone2 = Join-Path $sandboxRoot "BrainB"
        Copy-Item -LiteralPath $sourceTemplate -Destination $clone1 -Recurse -Force
        Copy-Item -LiteralPath $sourceTemplate -Destination $clone2 -Recurse -Force

        $meta1 = Join-Path $clone1 "metadata.json"
        if (Test-Path -LiteralPath $meta1) {
            $raw = Get-Content $meta1 -Raw -Encoding UTF8
            $raw = $raw -replace 'blank', 'sandbox-brain-a'
            $raw | Out-File -FilePath $meta1 -Encoding UTF8
        }

        $ok = (Test-Path -LiteralPath (Join-Path $clone1 "metadata.json")) -and (Test-Path -LiteralPath (Join-Path $clone2 "metadata.json"))
        if ($ok) {
            Add-Result (New-TestResult -Id "P1-08" -Name "Hot-Swap 疑似書込テスト" -Status "PASS" -Details "template 複製2件と metadata 更新の疑似テストに成功" -Evidence $sandboxRoot)
        } else {
            Add-Result (New-TestResult -Id "P1-08" -Name "Hot-Swap 疑似書込テスト" -Status "WARN" -Details "複製は作成したが一部ファイルが不足" -Evidence $sandboxRoot)
        }
    } catch {
        Add-Result (New-TestResult -Id "P1-08" -Name "Hot-Swap 疑似書込テスト" -Status "FAIL" -Details $_.Exception.Message -Evidence $sandboxRoot)
    }
}

function Write-Reports {
    $jsonPath = Join-Path $OutputDir "phase1_test_result.json"
    $mdPath = Join-Path $OutputDir "phase1_test_result.md"

    $Results | ConvertTo-Json -Depth 6 | Out-File -FilePath $jsonPath -Encoding UTF8

    $pass = @($Results | Where-Object status -eq "PASS").Count
    $fail = @($Results | Where-Object status -eq "FAIL").Count
    $warn = @($Results | Where-Object status -eq "WARN").Count
    $skip = @($Results | Where-Object status -eq "SKIP").Count

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# Phase 1 Test Report")
    $lines.Add("")
    $lines.Add("- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add("- ProjectRoot: $ProjectRoot")
    $lines.Add("- OutputDir: $OutputDir")
    $lines.Add("- Phase0ResultJson: $Phase0ResultJson")
    $lines.Add("")
    $lines.Add("## Summary")
    $lines.Add("")
    $lines.Add("- PASS: $pass")
    $lines.Add("- FAIL: $fail")
    $lines.Add("- WARN: $warn")
    $lines.Add("- SKIP: $skip")
    $lines.Add("")
    $lines.Add("## Details")
    $lines.Add("")
    foreach ($r in $Results) {
        $lines.Add("### [$($r.status)] $($r.id) $($r.name)")
        $lines.Add("")
        $lines.Add("- Details: $($r.details)")
        if ($r.evidence) {
            $lines.Add("- Evidence: $($r.evidence)")
        }
        $lines.Add("")
    }
    $lines | Out-File -FilePath $mdPath -Encoding UTF8

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "Phase 1 Test Summary" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "PASS: $pass" -ForegroundColor Green
    Write-Host "FAIL: $fail" -ForegroundColor Red
    Write-Host "WARN: $warn" -ForegroundColor Yellow
    Write-Host "SKIP: $skip" -ForegroundColor DarkYellow
    Write-Host "JSON: $jsonPath" -ForegroundColor Cyan
    Write-Host "MD  : $mdPath" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

$Results = New-Object System.Collections.Generic.List[object]
Ensure-OutputDir

Write-Host "SBA Framework Phase 1 Test Started" -ForegroundColor Cyan
Write-Host "ProjectRoot     : $ProjectRoot" -ForegroundColor Cyan
Write-Host "OutputDir       : $OutputDir" -ForegroundColor Cyan
Write-Host "Phase0ResultJson: $Phase0ResultJson" -ForegroundColor Cyan
Write-Host ""

Test-Phase0Dependency
Test-Phase1Files
Test-BrainBankStructure
Test-BrainPackageMetadata
Test-PythonImportPhase1
Test-CliEntrypoint
Test-CliSmoke
Test-PytestPhase1Presence
Test-SandboxWriteFlow
Write-Reports