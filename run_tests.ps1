# ============================================================
# SBA Framework: pull → pytest 実行スクリプト
# 使い方: .\run_tests.ps1
#         .\run_tests.ps1 -TestPath "tests/unit"  (絞り込み)
#         .\run_tests.ps1 -NoPull                 (pull スキップ)
# ============================================================

param(
    [string]$TestPath = "tests",
    [switch]$NoPull
)

# ----- 設定 -----
$ProjectRoot = "C:\TH_Works\SBA"
$VenvActivate = "$ProjectRoot\.venv\Scripts\Activate.ps1"
$LogDir       = "$ProjectRoot\logs"
$LogFile      = "$LogDir\test_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# ----- ログディレクトリ作成 -----
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# ----- 関数: 色付きログ出力 -----
function Write-Step {
    param([string]$Msg, [string]$Color = "Cyan")
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] $Msg" -ForegroundColor $Color
    "[$ts] $Msg" | Out-File -FilePath $LogFile -Append -Encoding UTF8
}

function Write-OK   { param([string]$Msg) Write-Step "OK  $Msg" "Green"  }
function Write-FAIL { param([string]$Msg) Write-Step "ERR $Msg" "Red"    }
function Write-INFO { param([string]$Msg) Write-Step "    $Msg" "Yellow" }

# ----- 開始 -----
Write-Step "===== SBA テスト実行開始 =====" "White"
Write-INFO "プロジェクト: $ProjectRoot"
Write-INFO "テスト対象:   $TestPath"

# -----【1】プロジェクトディレクトリへ移動 -----
Write-Step "[1/4] ディレクトリ移動"
Set-Location $ProjectRoot
if ($LASTEXITCODE -ne 0 -and -not (Test-Path $ProjectRoot)) {
    Write-FAIL "プロジェクトディレクトリが見つかりません: $ProjectRoot"
    exit 1
}
Write-OK "PWD: $(Get-Location)"

# -----【2】git pull -----
if (-not $NoPull) {
    Write-Step "[2/4] git pull (origin main)"
    $pullResult = git pull origin main 2>&1
    $pullResult | Out-File -FilePath $LogFile -Append -Encoding UTF8

    if ($LASTEXITCODE -ne 0) {
        Write-FAIL "git pull 失敗"
        $pullResult | ForEach-Object { Write-Host $_ -ForegroundColor Red }
        exit 1
    }
    $pullResult | ForEach-Object { Write-INFO $_ }
    Write-OK "git pull 完了"
} else {
    Write-INFO "[2/4] git pull スキップ (-NoPull フラグ)"
}

# -----【3】venv アクティベート -----
Write-Step "[3/4] venv アクティベート"
if (-not (Test-Path $VenvActivate)) {
    Write-FAIL "venv が見つかりません: $VenvActivate"
    Write-INFO "先に: python -m venv .venv && .venv\Scripts\Activate.ps1 && pip install -e '.[dev]'"
    exit 1
}
& $VenvActivate
Write-OK "venv アクティベート完了"

# -----【4】pytest 実行 -----
Write-Step "[4/4] pytest 実行: $TestPath"
Write-INFO "ログ出力先: $LogFile"

$pytestArgs = @(
    $TestPath,
    "-v",                     # 詳細出力
    "--tb=short",             # エラー時の短いトレースバック
    "--no-header",            # ヘッダー省略
    "--color=yes"             # 色付き出力
)

# pytest 実行（標準出力をコンソールとファイルに同時出力）
$pytestOutput = & python -m pytest @pytestArgs 2>&1
$exitCode = $LASTEXITCODE

# コンソール出力 + ログ記録
$pytestOutput | ForEach-Object {
    Write-Host $_
    $_ | Out-File -FilePath $LogFile -Append -Encoding UTF8
}

# ----- 結果サマリー -----
Write-Step "===== 結果 =====" "White"
if ($exitCode -eq 0) {
    Write-OK "全テスト PASSED"
} else {
    Write-FAIL "FAILED テストあり (exit code: $exitCode)"
}
Write-INFO "ログ保存: $LogFile"

exit $exitCode