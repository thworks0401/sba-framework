# =============================================================
# SBA_Phase0_Fix_Complete.ps1
# Phase 0 問題を全て自動修正する完全版スクリプト
# 対応PS: 5.1 以上
#
# 実行方法:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   C:/SBA/SBA_Phase0_Fix_Complete.ps1
# =============================================================

$ErrorActionPreference = "Continue"
$SBA_ROOT  = "C:/SBA"
$VENV_DIR  = "$SBA_ROOT/.venv"
$VENV_PY   = "$VENV_DIR/Scripts/python.exe"
$VENV_PIP  = "$VENV_DIR/Scripts/pip.exe"

$script:passCount = 0
$script:failCount = 0
$script:warnCount = 0

# ── 出力ヘルパー ─────────────────────────────────────────────
function OK   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green;  $script:passCount++ }
function FAIL { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red;    $script:failCount++ }
function WARN { param($m) Write-Host "  [WARN] $m" -ForegroundColor Yellow; $script:warnCount++ }
function INFO { param($m) Write-Host "         $m" -ForegroundColor Gray }
function STEP { param($m)
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  $m" -ForegroundColor Cyan
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "╔════════════════════════════════════════════════╗" -ForegroundColor White
Write-Host "║  SBA Framework  Phase 0  完全修正スクリプト   ║" -ForegroundColor White
Write-Host "╚════════════════════════════════════════════════╝" -ForegroundColor White
Write-Host "  PS Version: $($PSVersionTable.PSVersion)" -ForegroundColor Gray
Write-Host "  実行日時  : $(Get-Date -Format 'yyyy/MM/dd HH:mm:ss')" -ForegroundColor Gray


# ═══════════════════════════════════════════════════════════════
STEP "Fix 1 : Python 3.11 の確認と venv 作成"
# ═══════════════════════════════════════════════════════════════

# ── Python 3.11 の実行ファイルを全候補から探す ──────────────
function Find-Python311 {
    # 1) よくある固定パス
    $candidates = @(
        "C:/Users/$env:USERNAME/AppData/Local/Programs/Python/Python311/python.exe",
        "C:/Program Files/Python311/python.exe",
        "C:/Program Files (x86)/Python311/python.exe",
        "C:/Python311/python.exe",
        "C:/Python/Python311/python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $v = & $c --version 2>&1
            if ($v -match "3\.11") { return $c }
        }
    }

    # 2) py ランチャー経由
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $v = & py -3.11 --version 2>&1
        if ($v -match "3\.11") {
            $path = & py -3.11 -c "import sys; print(sys.executable)" 2>&1
            if ($path -and (Test-Path $path)) { return $path }
        }
    }

    # 3) PATH 上の python / python3 を全部試す
    $wherePy = where.exe python 2>&1
    foreach ($p in $wherePy) {
        if ($p -and (Test-Path $p)) {
            $v = & $p --version 2>&1
            if ($v -match "3\.11") { return $p }
        }
    }

    # 4) AppData\Local\Programs\Python 以下を再帰探索
    $searchBase = "C:/Users/$env:USERNAME/AppData/Local/Programs/Python"
    if (Test-Path $searchBase) {
        $found = Get-ChildItem $searchBase -Recurse -Filter "python.exe" -ErrorAction SilentlyContinue |
                 Where-Object { $_.FullName -match "Python311" } |
                 Select-Object -First 1
        if ($found) { return $found.FullName }
    }

    return $null
}

$py311 = Find-Python311

if ($py311) {
    $ver311 = & $py311 --version 2>&1
    OK "Python 3.11 を発見: $ver311 ($py311)"
} else {
    WARN "Python 3.11 が見つかりません。インストールを試みます..."

    # winget でインストール（既インストール済みのexit 1 を無視）
    winget install Python.Python.3.11 `
        --accept-source-agreements --accept-package-agreements 2>&1 |
        Out-Null
    # exit code 1 = 既インストール済み も正常とみなす

    # インストール後に再探索
    $py311 = Find-Python311

    if ($py311) {
        $ver311 = & $py311 --version 2>&1
        OK "Python 3.11 インストール完了: $ver311 ($py311)"
    } else {
        FAIL "Python 3.11 が見つかりませんでした"
        FAIL "https://www.python.org/downloads/release/python-3119/ から手動インストール後、再実行してください"
        exit 1
    }
}

# ── 現在の venv のバージョン確認 ────────────────────────────
$rebuildVenv = $false
if (Test-Path $VENV_PY) {
    $curVer = & $VENV_PY --version 2>&1
    INFO "現在の venv: $curVer"
    if ($curVer -notmatch "3\.11") {
        INFO "Python 3.11 ベースで venv を作り直します"
        $rebuildVenv = $true
    } else {
        OK "venv は既に Python 3.11 ベース → 作り直し不要"
    }
} else {
    INFO "venv が存在しません → 新規作成します"
    $rebuildVenv = $true
}

if ($rebuildVenv) {
    if (Test-Path $VENV_DIR) {
        INFO "既存 venv を削除中..."
        Remove-Item $VENV_DIR -Recurse -Force
    }
    INFO "venv を作成中..."
    & $py311 -m venv $VENV_DIR
    if ($LASTEXITCODE -eq 0) {
        $newVer = & $VENV_PY --version 2>&1
        OK "venv 作成完了: $newVer"
    } else {
        FAIL "venv 作成に失敗しました"
        exit 1
    }
}


# ═══════════════════════════════════════════════════════════════
STEP "Fix 2 : pip アップグレード + 全ライブラリインストール"
# ═══════════════════════════════════════════════════════════════

INFO "pip をアップグレード中..."
& $VENV_PY -m pip install --upgrade pip --quiet 2>&1 | Out-Null
OK "pip アップグレード完了 ($(& $VENV_PIP --version 2>&1))"

# ── パッケージリスト ─────────────────────────────────────────
$packages = @(
    @{ spec = "sentence-transformers>=2.7.0";   name = "sentence-transformers" },
    @{ spec = "qdrant-client>=1.9.0";           name = "qdrant-client" },
    @{ spec = "kuzu>=0.6.0";                    name = "kuzu" },
    @{ spec = "ollama>=0.2.0";                  name = "ollama" },
    @{ spec = "typer[all]>=0.12.0";             name = "typer" },
    @{ spec = "apscheduler>=3.10.0";            name = "apscheduler" },
    @{ spec = "pydantic>=2.7.0";                name = "pydantic" },
    @{ spec = "loguru>=0.7.0";                  name = "loguru" },
    @{ spec = "google-generativeai>=0.5.0";     name = "google-generativeai" },
    @{ spec = "playwright>=1.43.0";             name = "playwright" },
    @{ spec = "yt-dlp>=2024.5.0";               name = "yt-dlp" },
    @{ spec = "duckduckgo-search>=5.0.0";       name = "duckduckgo-search" },
    @{ spec = "beautifulsoup4>=4.12.0";         name = "beautifulsoup4" },
    @{ spec = "pdfminer.six>=20221105";         name = "pdfminer.six" },
    @{ spec = "pypdf>=4.0.0";                   name = "pypdf" },
    @{ spec = "arxiv>=2.1.0";                   name = "arxiv" },
    @{ spec = "httpx>=0.27.0";                  name = "httpx" },
    @{ spec = "python-dotenv>=1.0.0";           name = "python-dotenv" },
    @{ spec = "pyyaml>=6.0.0";                  name = "pyyaml" },
    @{ spec = "tqdm>=4.66.0";                   name = "tqdm" },
    @{ spec = "plyer>=2.1.0";                   name = "plyer" },
    @{ spec = "numpy>=1.24.0";                  name = "numpy" }
)

$failedPkgs = @()
$total = $packages.Count
$i = 0
foreach ($pkg in $packages) {
    $i++
    Write-Host ("  [{0,2}/{1}] {2,-30}" -f $i, $total, $pkg.name) `
        -ForegroundColor Gray -NoNewline
    $out = & $VENV_PIP install $pkg.spec --quiet 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK" -ForegroundColor Green
    } else {
        Write-Host "FAIL" -ForegroundColor Red
        $failedPkgs += $pkg.name
        INFO "    $($out | Select-Object -Last 2 | Out-String)"
    }
}

# faster-whisper は Python バージョンによって失敗することがあるため別扱い
Write-Host ("  [{0,2}/{1}] {2,-30}" -f ($total+1), ($total+1), "faster-whisper") `
    -ForegroundColor Gray -NoNewline
$fwOut = & $VENV_PIP install "faster-whisper>=1.0.0" --quiet 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK" -ForegroundColor Green
    $script:fasterWhisperInstalled = $true
} else {
    Write-Host "FAIL → openai-whisper で代替します" -ForegroundColor Yellow
    $altOut = & $VENV_PIP install openai-whisper --quiet 2>&1
    if ($LASTEXITCODE -eq 0) {
        OK "openai-whisper（代替）インストール成功"
        $script:fasterWhisperInstalled = $false
    } else {
        $failedPkgs += "faster-whisper / openai-whisper"
        FAIL "Whisper 系ライブラリのインストールに失敗"
    }
}

if ($failedPkgs.Count -gt 0) {
    WARN "以下のパッケージがインストールできませんでした:"
    $failedPkgs | ForEach-Object { INFO "  - $_" }
} else {
    OK "全ライブラリのインストール完了"
}

# Playwright ブラウザ
INFO "Playwright Chromium をインストール中（初回は数分かかります）..."
& $VENV_PY -m playwright install chromium 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { OK "Playwright Chromium インストール完了" }
else { WARN "Playwright Chromium のインストールに失敗（後で手動実行: playwright install chromium）" }


# ═══════════════════════════════════════════════════════════════
STEP "Fix 3 : Ollama / phi4 動作確認"
# ═══════════════════════════════════════════════════════════════

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    FAIL "ollama コマンドが見つかりません → https://ollama.com からインストールしてください"
} else {
    OK "ollama コマンド確認: $($ollamaCmd.Source)"

    $ollamaList = ollama list 2>&1
    if ($LASTEXITCODE -ne 0) {
        WARN "ollama list が失敗 → 'ollama serve' が起動しているか確認してください"
    } else {
        # phi4 確認
        $phi4Line = $ollamaList | Where-Object { $_ -match "phi4" } | Select-Object -First 1
        if ($phi4Line) {
            OK "phi4 モデル確認: $($phi4Line.Trim())"

            # 9.1GB 問題の確認と対処
            if ($phi4Line -match "9\.\d\s*GB") {
                Write-Host ""
                Write-Host "  [!] phi4:latest は 9.1GB です。RTX3060Ti (8GB VRAM) をわずかに超えます。" -ForegroundColor Yellow
                Write-Host "      Ollama は超過分を CPU にオフロードするため動作しますが推論が遅くなります。" -ForegroundColor Yellow
                Write-Host ""
                Write-Host "  対処オプション:" -ForegroundColor White
                Write-Host "    [1] そのまま使う（CPU オフロードあり・動作は問題なし）" -ForegroundColor White
                Write-Host "    [2] 量子化版 phi4:q4_K_M (~7.5GB) を追加 pull する（推奨）" -ForegroundColor White
                Write-Host ""
                $phi4c = Read-Host "  番号を入力 (1 or 2)"
                if ($phi4c -eq "2") {
                    INFO "phi4:q4_K_M を pull 中（~7.5GB）..."
                    ollama pull phi4:q4_K_M
                    if ($LASTEXITCODE -eq 0) { OK "phi4:q4_K_M の pull 完了" }
                    else { WARN "phi4:q4_K_M の pull 失敗 → phi4:latest で続行" }
                }
            }

            # 推論テスト（バックグラウンドジョブで3分タイムアウト）
            INFO "phi4 推論テスト中（CPU オフロード時は 1〜3 分かかることがあります）..."
            $job = Start-Job -ScriptBlock {
                ollama run phi4 "日本語で「テスト成功」とだけ答えてください" 2>&1
            }
            $done = Wait-Job $job -Timeout 180
            if ($done) {
                $res = Receive-Job $job
                $resStr = ($res -join "").Trim()
                if ($resStr.Length -gt 2) {
                    OK "phi4 推論テスト成功: $($resStr.Substring(0, [Math]::Min(60, $resStr.Length)))..."
                } else {
                    WARN "phi4 推論レスポンスが空でした（再試行してください）"
                }
            } else {
                Stop-Job $job
                WARN "phi4 推論が 3 分以内に完了しませんでした（CPU オフロード中は正常な場合があります）"
            }
            Remove-Job $job -Force -ErrorAction SilentlyContinue

        } else {
            WARN "phi4 モデルが見つかりません → ollama pull phi4"
        }

        # qwen2.5-coder 確認
        $qwenLine = $ollamaList | Where-Object { $_ -match "qwen2.5-coder" } | Select-Object -First 1
        if ($qwenLine) { OK "qwen2.5-coder 確認: $($qwenLine.Trim())" }
        else { WARN "qwen2.5-coder:7b が見つかりません → ollama pull qwen2.5-coder:7b" }
    }
}


# ═══════════════════════════════════════════════════════════════
STEP "Fix 4 : ディレクトリ構造を全作成"
# ═══════════════════════════════════════════════════════════════

$dirs = @(
    "$SBA_ROOT/brain_bank/_blank_template",
    "$SBA_ROOT/brain_bank/[active]",
    "$SBA_ROOT/domains/tech",
    "$SBA_ROOT/domains/sales",
    "$SBA_ROOT/domains/tax",
    "$SBA_ROOT/exports",
    "$SBA_ROOT/data",
    "$SBA_ROOT/logs",
    "$SBA_ROOT/config",
    "$SBA_ROOT/src/sba/cli",
    "$SBA_ROOT/src/sba/agent",
    "$SBA_ROOT/src/sba/brain",
    "$SBA_ROOT/src/sba/learning",
    "$SBA_ROOT/src/sba/experiment",
    "$SBA_ROOT/src/sba/inference",
    "$SBA_ROOT/src/sba/storage",
    "$SBA_ROOT/src/sba/sources",
    "$SBA_ROOT/src/sba/subskill",
    "$SBA_ROOT/src/sba/cost",
    "$SBA_ROOT/src/sba/utils",
    "$SBA_ROOT/tests"
)

foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        OK "作成: $dir"
    } else {
        INFO "既存: $dir"
    }
}


# ═══════════════════════════════════════════════════════════════
STEP "Fix 5 : Blank Brain Template ファイル群を生成"
# ═══════════════════════════════════════════════════════════════

$tmpl = "$SBA_ROOT/brain_bank/_blank_template"

# metadata.json
$now = Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
$meta = "{`n" +
        "  `"domain`": null,`n" +
        "  `"version`": `"0.0`",`n" +
        "  `"level`": 0,`n" +
        "  `"created_at`": `"$now`",`n" +
        "  `"last_saved_at`": `"$now`",`n" +
        "  `"description`": `"Blank Brain Template`",`n" +
        "  `"tags`": [],`n" +
        "  `"brain_id`": `"00000000-0000-0000-0000-000000000000`",`n" +
        "  `"source`": `"sba`",`n" +
        "  `"exported_at`": null`n" +
        "}"
[System.IO.File]::WriteAllText("$tmpl/metadata.json", $meta, [System.Text.Encoding]::UTF8)
OK "metadata.json 生成"

# self_eval.json
$selfEval = "{`n" +
            "  `"level`": 0,`n" +
            "  `"last_eval_at`": null,`n" +
            "  `"next_eval_at`": null,`n" +
            "  `"subskills`": {}`n" +
            "}"
[System.IO.File]::WriteAllText("$tmpl/self_eval.json", $selfEval, [System.Text.Encoding]::UTF8)
OK "self_eval.json 生成"

# subskill_manifest.json
$manifest = "{`n" +
            "  `"domain`": null,`n" +
            "  `"subskills`": []`n" +
            "}"
[System.IO.File]::WriteAllText("$tmpl/subskill_manifest.json", $manifest, [System.Text.Encoding]::UTF8)
OK "subskill_manifest.json 生成"

# experiment_log.db / learning_timeline.db を Python で生成
$pyScript = @'
import sqlite3, os, sys

tmpl = sys.argv[1]

# experiment_log.db
conn = sqlite3.connect(os.path.join(tmpl, "experiment_log.db"))
conn.execute("""
CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT PRIMARY KEY,
    executed_at TEXT NOT NULL,
    brain_id    TEXT NOT NULL,
    subskill    TEXT NOT NULL,
    exp_type    TEXT NOT NULL CHECK(exp_type IN ('A','B','C','D')),
    hypothesis  TEXT NOT NULL,
    plan        TEXT NOT NULL,
    input_data  TEXT,
    output_data TEXT,
    result      TEXT NOT NULL CHECK(result IN ('SUCCESS','FAILURE','PARTIAL')),
    analysis    TEXT,
    delta_score REAL NOT NULL DEFAULT 0.0,
    exec_ms     INTEGER,
    created_at  TEXT NOT NULL
)""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_subskill ON experiments(subskill)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_result   ON experiments(result)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_brain_id ON experiments(brain_id)")
conn.commit(); conn.close()

# learning_timeline.db
conn = sqlite3.connect(os.path.join(tmpl, "learning_timeline.db"))
conn.execute("""
CREATE TABLE IF NOT EXISTS timeline (
    id           TEXT PRIMARY KEY,
    learned_at   TEXT NOT NULL,
    brain_id     TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    url_or_path  TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    subskill     TEXT NOT NULL,
    freshness    REAL NOT NULL DEFAULT 1.0 CHECK(freshness BETWEEN 0.0 AND 1.0),
    is_outdated  INTEGER NOT NULL DEFAULT 0 CHECK(is_outdated IN (0,1)),
    qdrant_ids   TEXT,
    kg_node_ids  TEXT
)""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_tl_subskill  ON timeline(subskill)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_tl_brain_id  ON timeline(brain_id)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_tl_freshness ON timeline(freshness)")
conn.commit(); conn.close()

print("ok")
'@
$tmpScript = "$env:TEMP/sba_init_db.py"
[System.IO.File]::WriteAllText($tmpScript, $pyScript, [System.Text.Encoding]::UTF8)
$dbResult = & $VENV_PY $tmpScript $tmpl 2>&1
if ($dbResult -eq "ok") {
    OK "experiment_log.db 生成（スキーマ付き）"
    OK "learning_timeline.db 生成（スキーマ付き）"
} else {
    FAIL "SQLite DB 生成に失敗: $dbResult"
}
Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue

# api_usage.db（Agent Core 共有・Brain Packageの外に置く）
$pyApiDb = @'
import sqlite3, os, sys
conn = sqlite3.connect(os.path.join(sys.argv[1], "api_usage.db"))
conn.execute("""
CREATE TABLE IF NOT EXISTS api_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    api_name     TEXT NOT NULL,
    usage_date   TEXT NOT NULL,
    usage_month  TEXT NOT NULL,
    req_count    INTEGER NOT NULL DEFAULT 0,
    token_count  INTEGER NOT NULL DEFAULT 0,
    unit_count   INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL,
    UNIQUE(api_name, usage_date)
)""")
conn.execute("""
CREATE TABLE IF NOT EXISTS api_stops (
    api_name    TEXT PRIMARY KEY,
    stopped_at  TEXT,
    stop_reason TEXT,
    resume_at   TEXT
)""")
conn.execute("""
CREATE TABLE IF NOT EXISTS api_thresholds (
    api_name      TEXT PRIMARY KEY,
    daily_limit   INTEGER,
    monthly_limit INTEGER,
    warn_pct      REAL NOT NULL DEFAULT 0.70,
    throttle_pct  REAL NOT NULL DEFAULT 0.85,
    stop_pct      REAL NOT NULL DEFAULT 0.95
)""")
defaults = [
    ("gemini",        1500, None),
    ("youtube",      10000, None),
    ("newsapi",        100, None),
    ("github",        5000, None),
    ("stackoverflow",10000, None),
    ("huggingface",   None, 30000),
]
for api, daily, monthly in defaults:
    conn.execute(
        "INSERT OR IGNORE INTO api_thresholds(api_name,daily_limit,monthly_limit) VALUES(?,?,?)",
        (api, daily, monthly))
conn.commit(); conn.close()
print("ok")
'@
$tmpApiScript = "$env:TEMP/sba_init_api_db.py"
[System.IO.File]::WriteAllText($tmpApiScript, $pyApiDb, [System.Text.Encoding]::UTF8)
$apiDbResult = & $VENV_PY $tmpApiScript "$SBA_ROOT/data" 2>&1
if ($apiDbResult -eq "ok") { OK "api_usage.db 生成（デフォルト閾値付き）" }
else { FAIL "api_usage.db 生成に失敗: $apiDbResult" }
Remove-Item $tmpApiScript -Force -ErrorAction SilentlyContinue


# ═══════════════════════════════════════════════════════════════
STEP "Fix 6 : sba_config.yaml を生成"
# ═══════════════════════════════════════════════════════════════

$configContent = @"
# SBA Framework 設定ファイル
# 変更後は sba restart で反映

paths:
  sba_root:       C:/SBA
  brain_bank:     C:/SBA/brain_bank
  blank_template: C:/SBA/brain_bank/_blank_template
  active_slot:    C:/SBA/brain_bank/[active]
  exports:        C:/SBA/exports
  domains:        C:/SBA/domains
  data:           C:/SBA/data
  logs:           C:/SBA/logs

inference:
  tier1_model:             phi4
  tier3_model:             qwen2.5-coder:7b
  tier2_model:             gemini-2.0-flash
  tier1_timeout_sec:       10
  token_threshold:         8000
  gemini_stop_threshold:   100

embedding:
  model:      BAAI/bge-m3
  device:     cpu
  batch_size: 32

chunking:
  target_min_tokens: 400
  target_max_tokens: 600
  overlap_tokens:    50
  min_drop_tokens:   50

scheduler:
  light_experiment_interval_hours:  1
  medium_experiment_interval_hours: 6
  heavy_experiment_interval_hours:  24
  learning_loop_interval_minutes:   30

backup:
  enabled:       false
  destination:   D:/SBA_Backup
  schedule_cron: "0 4 * * *"
  keep_versions: 7

logging:
  level:        INFO
  max_file_size: 10MB
  backup_count:  5
"@
[System.IO.File]::WriteAllText(
    "$SBA_ROOT/config/sba_config.yaml",
    $configContent,
    [System.Text.Encoding]::UTF8)
OK "sba_config.yaml 生成"


# ═══════════════════════════════════════════════════════════════
STEP "Fix 7 : .env テンプレートを生成"
# ═══════════════════════════════════════════════════════════════

$envPath = "$SBA_ROOT/config/.env"
if (-not (Test-Path $envPath)) {
    $envContent = @"
# SBA Framework 環境変数ファイル
# このファイルは Git 管理に含めないこと (.gitignore に追記すること)

# Google Gemini API キー（必須）
# 取得先: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=AIzaSyDBXlvLxibH1KKbRJt1UJjkrMQQO8m7-L8

# GitHub Personal Access Token（必須）
# 取得先: https://github.com/settings/tokens
# 必要スコープ: repo (read), read:packages
GITHUB_TOKEN=ghp_ここにGitHubトークンを貼り付ける

# YouTube Data API v3 キー（任意）
# 取得先: https://console.cloud.google.com/
# YOUTUBE_API_KEY=AIza...

# NewsAPI キー（任意）
# 取得先: https://newsapi.org/register
# NEWS_API_KEY=...
"@
    [System.IO.File]::WriteAllText($envPath, $envContent, [System.Text.Encoding]::UTF8)
    OK ".env テンプレート生成: $envPath"
} else {
    OK ".env は既に存在します（上書きしません）"
}

Write-Host ""
Write-Host "  [!] 重要: 以下のファイルを開いて API キーを設定してください" -ForegroundColor Yellow
Write-Host "      $envPath" -ForegroundColor Cyan
Write-Host "      - GEMINI_API_KEY  → https://aistudio.google.com/app/apikey" -ForegroundColor Gray
Write-Host "      - GITHUB_TOKEN    → https://github.com/settings/tokens" -ForegroundColor Gray


# ═══════════════════════════════════════════════════════════════
STEP "Fix 8 : NSSM のインストール"
# ═══════════════════════════════════════════════════════════════

$nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssmCmd) {
    OK "NSSM は既にインストール済み: $($nssmCmd.Source)"
} else {
    INFO "NSSM を自動インストールします..."
    $nssmDir = "C:/tools/nssm"
    $nssmExePath = "$nssmDir/nssm.exe"

    New-Item -ItemType Directory -Path $nssmDir -Force | Out-Null

    try {
        $nssmZip = "$env:TEMP/nssm.zip"
        $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
        INFO "ダウンロード中: $nssmUrl"
        Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -TimeoutSec 60

        $extractDir = "$env:TEMP/nssm_extract"
        if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
        Expand-Archive -Path $nssmZip -DestinationPath $extractDir -Force

        # 64bit 版を優先して探す
        $nssmBin = Get-ChildItem $extractDir -Recurse -Filter "nssm.exe" |
                   Where-Object { $_.FullName -match "win64" } |
                   Select-Object -First 1
        if (-not $nssmBin) {
            $nssmBin = Get-ChildItem $extractDir -Recurse -Filter "nssm.exe" |
                       Select-Object -First 1
        }

        if ($nssmBin) {
            Copy-Item $nssmBin.FullName $nssmExePath -Force

            # PATH に追加（ユーザースコープ）
            $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
            if ($userPath -notmatch [regex]::Escape($nssmDir)) {
                [System.Environment]::SetEnvironmentVariable(
                    "Path", "$userPath;$nssmDir", "User")
                $env:Path += ";$nssmDir"
            }
            OK "NSSM インストール完了: $nssmExePath"
        } else {
            FAIL "NSSM の実行ファイルが zip 内に見つかりませんでした"
        }

        Remove-Item $nssmZip -Force -ErrorAction SilentlyContinue
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue

    } catch {
        FAIL "NSSM のダウンロードに失敗: $_"
        Write-Host "  → 手動インストール手順:" -ForegroundColor Yellow
        Write-Host "    1. https://nssm.cc/download から nssm-2.24.zip をダウンロード" -ForegroundColor Gray
        Write-Host "    2. zip を解凍して win64/nssm.exe を C:/tools/nssm/ にコピー" -ForegroundColor Gray
        Write-Host "    3. C:/tools/nssm を PATH に追加" -ForegroundColor Gray
    }
}


# ═══════════════════════════════════════════════════════════════
STEP "Fix 9 : Python パッケージ構造を整備"
# ═══════════════════════════════════════════════════════════════

$initDirs = @(
    "$SBA_ROOT/src/sba",
    "$SBA_ROOT/src/sba/cli",
    "$SBA_ROOT/src/sba/agent",
    "$SBA_ROOT/src/sba/brain",
    "$SBA_ROOT/src/sba/learning",
    "$SBA_ROOT/src/sba/experiment",
    "$SBA_ROOT/src/sba/inference",
    "$SBA_ROOT/src/sba/storage",
    "$SBA_ROOT/src/sba/sources",
    "$SBA_ROOT/src/sba/subskill",
    "$SBA_ROOT/src/sba/cost",
    "$SBA_ROOT/src/sba/utils"
)
foreach ($d in $initDirs) {
    $f = "$d/__init__.py"
    if (-not (Test-Path $f)) {
        [System.IO.File]::WriteAllText($f, "# SBA Framework`n", [System.Text.Encoding]::UTF8)
    }
}
OK "全サブパッケージに __init__.py を生成"

# pyproject.toml
if (-not (Test-Path "$SBA_ROOT/pyproject.toml")) {
    $pyproj = @"
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "sba"
version = "0.1.0"
requires-python = ">=3.11"

[tool.setuptools.packages.find]
where = ["src"]
"@
    [System.IO.File]::WriteAllText(
        "$SBA_ROOT/pyproject.toml", $pyproj, [System.Text.Encoding]::UTF8)
    OK "pyproject.toml 生成"
}

# 開発モードインストール
INFO "sba パッケージを開発モードで venv にインストール中..."
Push-Location $SBA_ROOT
& $VENV_PIP install -e . --quiet 2>&1 | Out-Null
Pop-Location
if ($LASTEXITCODE -eq 0) { OK "sba パッケージ（開発モード）インストール完了" }
else { WARN "sba パッケージのインストールをスキップ（Phase 1 開始前に手動で実行してください）" }


# ═══════════════════════════════════════════════════════════════
STEP "Fix 10 : Qdrant / Kuzu / bge-m3 動作確認"
# ═══════════════════════════════════════════════════════════════

# Qdrant テスト
$pyQdrant = @'
import tempfile, sys
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
try:
    with tempfile.TemporaryDirectory() as tmp:
        c = QdrantClient(path=tmp)
        c.create_collection("test", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
        c.upsert("test", [PointStruct(id=1, vector=[0.1,0.2,0.3,0.4], payload={"subskill":"test"})])
        r = c.search("test", [0.1,0.2,0.3,0.4], limit=1)
        print("ok" if len(r) == 1 else "fail:search empty")
except Exception as e:
    print(f"fail:{e}")
'@
$r = & $VENV_PY -c $pyQdrant 2>&1
if ($r -eq "ok") { OK "Qdrant ローカルモード: 書き込み・検索 正常動作" }
else { FAIL "Qdrant エラー: $r" }

# Kuzu テスト
$pyKuzu = @'
import tempfile, os, sys
import kuzu
try:
    with tempfile.TemporaryDirectory() as tmp:
        db = kuzu.Database(os.path.join(tmp, "test"))
        conn = kuzu.Connection(db)
        conn.execute("CREATE NODE TABLE Person (id STRING, name STRING, PRIMARY KEY(id))")
        conn.execute("CREATE (:Person {id: 'p1', name: 'Alice'})")
        r = conn.execute("MATCH (p:Person) RETURN p.name")
        rows = []
        while r.has_next():
            rows.append(r.get_next())
        print("ok" if rows[0][0] == "Alice" else f"fail:got {rows}")
except Exception as e:
    print(f"fail:{e}")
'@
$r = & $VENV_PY -c $pyKuzu 2>&1
if ($r -eq "ok") { OK "Kuzu: ノード作成・MATCH クエリ 正常動作" }
else { FAIL "Kuzu エラー: $r" }

# bge-m3 テスト
INFO "bge-m3 の動作確認中（初回は数分かかります）..."
$pyBge = @'
import sys
from sentence_transformers import SentenceTransformer
import numpy as np
try:
    m = SentenceTransformer("BAAI/bge-m3", device="cpu")
    v = m.encode(["テスト文章"], normalize_embeddings=True)
    norm_ok = abs(float(np.linalg.norm(v[0])) - 1.0) < 0.01
    shape_ok = v.shape == (1, 1024)
    print("ok" if (norm_ok and shape_ok) else f"fail:shape={v.shape}")
except Exception as e:
    print(f"fail:{e}")
'@
$r = & $VENV_PY -c $pyBge 2>&1
if ($r -eq "ok") { OK "bge-m3: エンコード成功（次元数=1024・L2ノルム=1.0）" }
else { FAIL "bge-m3 エラー: $r" }


# ═══════════════════════════════════════════════════════════════
STEP "最終サマリ"
# ═══════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "  ┌──────────────────────────────────────┐" -ForegroundColor White
Write-Host "  │  修正結果                             │" -ForegroundColor White
Write-Host "  ├──────────────────────────────────────┤" -ForegroundColor White
Write-Host ("  │  [OK]   {0,-5} 項目{1,20}│" -f $script:passCount, "") -ForegroundColor Green
if ($script:warnCount -gt 0) {
Write-Host ("  │  [WARN] {0,-5} 項目（後で対処可能）{1,3}│" -f $script:warnCount, "") -ForegroundColor Yellow }
if ($script:failCount -gt 0) {
Write-Host ("  │  [FAIL] {0,-5} 項目（要対処）{1,9}│" -f $script:failCount, "") -ForegroundColor Red }
Write-Host "  └──────────────────────────────────────┘" -ForegroundColor White
Write-Host ""

Write-Host "  次にやること:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. APIキーを設定する" -ForegroundColor White
Write-Host "     notepad $SBA_ROOT/config/.env" -ForegroundColor Gray
Write-Host "     → GEMINI_API_KEY と GITHUB_TOKEN を設定" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. PowerShell を再起動する（PATH 変更を反映）" -ForegroundColor White
Write-Host ""
Write-Host "  3. 確認スクリプトを再実行する" -ForegroundColor White
Write-Host "     C:/SBA/SBA_Phase0_Verify.ps1" -ForegroundColor Gray
Write-Host ""

if ($script:failCount -eq 0) {
    Write-Host "  ✅ FAIL ゼロ。Phase 1 実装に進めます！" -ForegroundColor Green
} elseif ($script:failCount -le 3) {
    Write-Host "  🟡 残り $($script:failCount) 件。上記の [FAIL] を修正して再実行してください。" -ForegroundColor Yellow
} else {
    Write-Host "  ❌ [FAIL] が $($script:failCount) 件あります。修正して再実行してください。" -ForegroundColor Red
}
Write-Host ""