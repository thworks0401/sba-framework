[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\TH_Works\SBA",
    [string]$PythonExe = "python",
    [string]$OutputDir = "C:\TH_Works\SBA\test_results\phase0",
    [switch]$SkipModelLoad,
    [switch]$SkipApiCheck
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

function Test-CommandExists {
    param([string]$CommandName)
    try {
        $null = Get-Command $CommandName -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-EnvValue {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
        $value = [Environment]::GetEnvironmentVariable($name, "User")
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
        $value = [Environment]::GetEnvironmentVariable($name, "Machine")
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
    return $null
}

function Ensure-OutputDir {
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }
}

function Test-ProjectRoot {
    if (Test-Path $ProjectRoot) {
        Add-Result (New-TestResult -Id "P0-ROOT" -Name "プロジェクトルート存在確認" -Status "PASS" -Details "ProjectRoot が存在する: $ProjectRoot" -Evidence $ProjectRoot)
    } else {
        Add-Result (New-TestResult -Id "P0-ROOT" -Name "プロジェクトルート存在確認" -Status "FAIL" -Details "ProjectRoot が存在しない: $ProjectRoot" -Evidence $ProjectRoot)
    }
}

function Test-Python311 {
    if (-not (Test-CommandExists $PythonExe)) {
        Add-Result (New-TestResult -Id "P0-01" -Name "Python コマンド存在確認" -Status "FAIL" -Details "Python コマンドが見つからない: $PythonExe")
        return
    }

    $version = & $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
        Add-Result (New-TestResult -Id "P0-01" -Name "Python バージョン確認" -Status "FAIL" -Details "Python バージョン取得に失敗")
        return
    }

    if ($version -like "3.11.*") {
        Add-Result (New-TestResult -Id "P0-01" -Name "Python 3.11 確認" -Status "PASS" -Details "Python $version を確認" -Evidence $version)
    } else {
        Add-Result (New-TestResult -Id "P0-01" -Name "Python 3.11 確認" -Status "FAIL" -Details "要求は Python 3.11 固定。検出: $version" -Evidence $version)
    }
}

function Test-Venv {
    $venvPath = Join-Path $ProjectRoot ".venv"
    $venvPy = Join-Path $venvPath "Scripts\python.exe"
    if ((Test-Path $venvPath) -and (Test-Path $venvPy)) {
        $version = & $venvPy -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($version -like "3.11.*") {
            Add-Result (New-TestResult -Id "P0-02" -Name "venv 環境確認" -Status "PASS" -Details ".venv と python.exe を確認。バージョン: $version" -Evidence $venvPy)
        } else {
            Add-Result (New-TestResult -Id "P0-02" -Name "venv 環境確認" -Status "WARN" -Details ".venv はあるが Python 3.11 ではない可能性。検出: $version" -Evidence $venvPy)
        }
    } else {
        Add-Result (New-TestResult -Id "P0-02" -Name "venv 環境確認" -Status "FAIL" -Details ".venv または .venv\Scripts\python.exe が見つからない" -Evidence $venvPath)
    }
}

function Test-RequirementsFiles {
    $req = Join-Path $ProjectRoot "requirements.txt"
    $reqDev = Join-Path $ProjectRoot "requirements-dev.txt"
    $pyproject = Join-Path $ProjectRoot "pyproject.toml"

    $missing = @()
    foreach ($path in @($req, $reqDev, $pyproject)) {
        if (-not (Test-Path $path)) { $missing += $path }
    }

    if ($missing.Count -eq 0) {
        Add-Result (New-TestResult -Id "P0-03" -Name "依存定義ファイル確認" -Status "PASS" -Details "requirements.txt / requirements-dev.txt / pyproject.toml を確認")
    } else {
        Add-Result (New-TestResult -Id "P0-03" -Name "依存定義ファイル確認" -Status "FAIL" -Details ("不足ファイル: " + ($missing -join ", ")))
    }
}

function Test-DirectoryStructure {
    $required = @(
        "brain_bank",
        "domains",
        "exports",
        "data",
        "logs",
        "src\sba",
        "tests"
    )

    $missing = @()
    foreach ($rel in $required) {
        $full = Join-Path $ProjectRoot $rel
        if (-not (Test-Path $full)) { $missing += $rel }
    }

    if ($missing.Count -eq 0) {
        Add-Result (New-TestResult -Id "P0-09" -Name "基本ディレクトリ構造確認" -Status "PASS" -Details "主要ディレクトリ一式を確認")
    } else {
        Add-Result (New-TestResult -Id "P0-09" -Name "基本ディレクトリ構造確認" -Status "FAIL" -Details ("不足: " + ($missing -join ", ")))
    }
}

function Test-ConfigFiles {
    $yaml1 = Join-Path $ProjectRoot "sba_config.yaml"
    $yaml2 = Join-Path $ProjectRoot "config\sba_config.yaml"
    $env1 = Join-Path $ProjectRoot ".env"
    $env2 = Join-Path $ProjectRoot "config\.env"

    $yaml = if (Test-Path $yaml1) { $yaml1 } elseif (Test-Path $yaml2) { $yaml2 } else { $null }
    $envf = if (Test-Path $env1) { $env1 } elseif (Test-Path $env2) { $env2 } else { $null }

    if ($yaml) {
        $details = "sba_config.yaml を確認: $yaml"
        if ($envf) {
            $details += " / .env も確認: $envf"
            Add-Result (New-TestResult -Id "P0-10" -Name "設定ファイル確認" -Status "PASS" -Details $details -Evidence "$yaml | $envf")
        } else {
            Add-Result (New-TestResult -Id "P0-10" -Name "設定ファイル確認" -Status "WARN" -Details "$details / .env は未確認" -Evidence $yaml)
        }
    } else {
        Add-Result (New-TestResult -Id "P0-10" -Name "設定ファイル確認" -Status "FAIL" -Details "sba_config.yaml が見つからない")
    }
}

function Test-Phase0VerifyScriptExists {
    $scriptPath = Join-Path $ProjectRoot "SBA_Phase0_Verify.ps1"
    if (Test-Path $scriptPath) {
        Add-Result (New-TestResult -Id "P0-15" -Name "既存 Phase0 検証スクリプト確認" -Status "PASS" -Details "既存の SBA_Phase0_Verify.ps1 を確認" -Evidence $scriptPath)
    } else {
        Add-Result (New-TestResult -Id "P0-15" -Name "既存 Phase0 検証スクリプト確認" -Status "WARN" -Details "SBA_Phase0_Verify.ps1 が見つからない。今回作成の Test-Phase0.ps1 を利用")
    }
}

function Write-Reports {
    $jsonPath = Join-Path $OutputDir "phase0_test_result.json"
    $mdPath = Join-Path $OutputDir "phase0_test_result.md"

    $Results | ConvertTo-Json -Depth 5 | Out-File -FilePath $jsonPath -Encoding UTF8

    $pass = @($Results | Where-Object status -eq "PASS").Count
    $fail = @($Results | Where-Object status -eq "FAIL").Count
    $warn = @($Results | Where-Object status -eq "WARN").Count
    $skip = @($Results | Where-Object status -eq "SKIP").Count

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# Phase 0 Test Report")
    $lines.Add("")
    $lines.Add("- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add("- ProjectRoot: $ProjectRoot")
    $lines.Add("- OutputDir: $OutputDir")
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
    Write-Host "Phase 0 Test Summary" -ForegroundColor Cyan
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

Write-Host "SBA Framework Phase 0 Test Started" -ForegroundColor Cyan
Write-Host "ProjectRoot: $ProjectRoot" -ForegroundColor Cyan
Write-Host "OutputDir  : $OutputDir" -ForegroundColor Cyan
Write-Host ""

Test-ProjectRoot
Test-Python311
Test-Venv
Test-RequirementsFiles
Test-DirectoryStructure
Test-ConfigFiles
Test-Phase0VerifyScriptExists
Write-Reports
