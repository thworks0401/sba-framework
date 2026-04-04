# Test-Phase2.ps1
# Phase 2 検証スクリプト: ストレージ層 (Qdrant + Kuzu + SQLite + KnowledgeStore)

param(
    [string]$ProjectRoot = "C:\TH_Works\SBA",
    [switch]$RunPytest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$OutputDir        = "$ProjectRoot\test_results\phase2"
$Phase1ResultJson = "$ProjectRoot\test_results\phase1\phase1_test_result.json"
$PythonExe        = "$ProjectRoot\.venv\Scripts\python.exe"

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$results   = [System.Collections.Generic.List[PSObject]]::new()
$passCount = 0; $failCount = 0; $warnCount = 0; $skipCount = 0

function Add-Result {
    param([string]$Id, [string]$Name, [string]$Status, [string]$Details)
    $results.Add([PSCustomObject]@{
        id      = $Id
        name    = $Name
        status  = $Status
        details = $Details
    })
    switch ($Status) {
        "PASS" { $script:passCount++; Write-Host "[PASS] $Id - $Name`n       $Details" -ForegroundColor Green }
        "FAIL" { $script:failCount++; Write-Host "[FAIL] $Id - $Name`n       $Details" -ForegroundColor Red }
        "WARN" { $script:warnCount++; Write-Host "[WARN] $Id - $Name`n       $Details" -ForegroundColor Yellow }
        "SKIP" { $script:skipCount++; Write-Host "[SKIP] $Id - $Name`n       $Details" -ForegroundColor DarkGray }
    }
}

Write-Host "SBA Framework Phase 2 Test Started" -ForegroundColor Cyan
Write-Host "ProjectRoot     : $ProjectRoot"
Write-Host "OutputDir       : $OutputDir"
Write-Host "Phase1ResultJson: $Phase1ResultJson`n"

# ------------------------------------------------------------------
# P2-00: Phase 1 結果依存確認
# ------------------------------------------------------------------
if (Test-Path $Phase1ResultJson) {
    $p1      = Get-Content $Phase1ResultJson -Raw | ConvertFrom-Json
    # @() で強制配列化 → .Count が必ず使える
    $hasFail = @($p1 | Where-Object { $_.status -eq "FAIL" })
    if ($hasFail.Count -gt 0) {
        Add-Result "P2-00" "Phase 1 結果依存確認" "FAIL" "Phase 1 に FAIL あり: Phase 2 前提未満"
    } else {
        Add-Result "P2-00" "Phase 1 結果依存確認" "PASS" "Phase 1 に FAIL がないため Phase 2 テスト前提を満たす"
    }
} else {
    Add-Result "P2-00" "Phase 1 結果依存確認" "FAIL" "Phase 1 結果 JSON が見つからない: $Phase1ResultJson"
}

# ------------------------------------------------------------------
# P2-01: ストレージ実装ファイル確認
# ------------------------------------------------------------------
$storageFiles = @(
    "src\sba\storage\knowledge_store.py",
    "src\sba\storage\vector_store.py",
    "src\sba\storage\graph_store.py",
    "src\sba\storage\timeline_db.py",
    "src\sba\storage\api_usage_db.py"
)
# @() で強制配列化
$missingStorage = @($storageFiles | Where-Object { -not (Test-Path "$ProjectRoot\$_") })
if ($missingStorage.Count -eq 0) {
    Add-Result "P2-01" "ストレージ実装ファイル確認" "PASS" "全ストレージファイル確認済み"
} else {
    Add-Result "P2-01" "ストレージ実装ファイル確認" "FAIL" "不足: $($missingStorage -join ', ')"
}

# ------------------------------------------------------------------
# P2-02: ストレージテストファイル確認
# ------------------------------------------------------------------
$testFiles = @(
    "tests\unit\test_storage.py",
    "tests\unit\test_vector_store.py",
    "tests\unit\test_graph_store.py",
    "tests\unit\test_timeline_db.py",
    "tests\unit\test_knowledge_store_advanced.py"
)
# @() で強制配列化
$missingTests = @($testFiles | Where-Object { -not (Test-Path "$ProjectRoot\$_") })
if ($missingTests.Count -eq 0) {
    Add-Result "P2-02" "ストレージテストファイル確認" "PASS" "全テストファイル確認済み"
} else {
    Add-Result "P2-02" "ストレージテストファイル確認" "FAIL" "不足: $($missingTests -join ', ')"
}

# ------------------------------------------------------------------
# P2-03: ストレージ import 確認
# ------------------------------------------------------------------
$importScript = @"
import sys
sys.path.insert(0, r'$ProjectRoot\src')
try:
    from sba.storage.knowledge_store import KnowledgeStore, KnowledgeStoreError
    from sba.storage.vector_store import QdrantVectorStore
    from sba.storage.graph_store import KuzuGraphStore
    from sba.storage.timeline_db import TimelineRepository
    from sba.storage.api_usage_db import APIUsageRepository
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
"@
$importResult = & $PythonExe -c $importScript 2>&1
if ($importResult -match "^OK") {
    Add-Result "P2-03" "ストレージ import 確認" "PASS" "全ストレージモジュール import 成功"
} else {
    Add-Result "P2-03" "ストレージ import 確認" "FAIL" "import エラー: $importResult"
}

# ------------------------------------------------------------------
# P2-04: KnowledgeStore 疑似書込テスト
# ------------------------------------------------------------------
$smokeScript = @"
import sys, tempfile, numpy as np
sys.path.insert(0, r'$ProjectRoot\src')

from sba.storage.knowledge_store import KnowledgeStore
from sba.storage.vector_store import QdrantVectorStore

class _Fake:
    DEDUP_THRESHOLD = 0.92
    def _v(self, t):
        v = np.zeros(1024, dtype=np.float32); v[sum(ord(c) for c in t) % 1024] = 1.0; return v
    def encode(self, texts, **kw): return np.vstack([self._v(t) for t in texts])
    def encode_single(self, text): return self._v(text)

with tempfile.TemporaryDirectory() as tmp:
    fake = _Fake()
    original = QdrantVectorStore._get_embedder
    QdrantVectorStore._get_embedder = lambda self: fake

    ks = KnowledgeStore(tmp, 'smoke-brain')
    ks.ensure_subskill_node('design', '設計')

    r = ks.store_chunk('Python is versatile.', 'design', 'Web', trust_score=0.9)
    assert not r['duplicate_detected'], f'Unexpected duplicate: {r}'
    assert r['chunk_id'], 'chunk_id empty'

    dup = ks.store_chunk('Python is versatile.', 'design', 'Web', trust_score=0.9)
    assert dup['duplicate_detected'], 'Duplicate not detected'

    results = ks.query_hybrid('Python versatile', limit=3)
    assert results, 'query_hybrid returned empty'

    ks.mark_deprecated(r['chunk_id'], 'test')
    chunk = ks.get_chunk(r['chunk_id'])
    assert chunk and bool(chunk['is_deprecated']), 'mark_deprecated failed'

    QdrantVectorStore._get_embedder = original
    print('OK')
"@
$smokeResult = & $PythonExe -c $smokeScript 2>&1
if ($smokeResult -match "^OK") {
    Add-Result "P2-04" "KnowledgeStore 疑似書込テスト" "PASS" "store / duplicate / query_hybrid / mark_deprecated 正常"
} else {
    Add-Result "P2-04" "KnowledgeStore 疑似書込テスト" "FAIL" "エラー: $smokeResult"
}

# ------------------------------------------------------------------
# P2-05: Active Brain ストレージ構造確認
# ------------------------------------------------------------------
$activeBrainPath = "C:\TH_Works\SBA\brain_bank\[active]"
$vectorIndexOk   = Test-Path -LiteralPath "$activeBrainPath\vector_index"
$kgOk            = Test-Path -LiteralPath "$activeBrainPath\knowledge_graph"
if ($vectorIndexOk -and $kgOk) {
    Add-Result "P2-05" "Active Brain ストレージ構造確認" "PASS" "vector_index / knowledge_graph ディレクトリ確認"
} else {
    $missing = @()
    if (-not $vectorIndexOk) { $missing += "vector_index" }
    if (-not $kgOk)          { $missing += "knowledge_graph" }
    Add-Result "P2-05" "Active Brain ストレージ構造確認" "WARN" "不足: $($missing -join ', ')"
}

# ------------------------------------------------------------------
# P2-06: pytest ストレージテスト実行（オプション）
# ------------------------------------------------------------------
if ($RunPytest) {
    Push-Location $ProjectRoot
    $pytestOut = & $PythonExe -m pytest `
        "tests/unit/test_storage.py" `
        "tests/unit/test_vector_store.py" `
        "tests/unit/test_graph_store.py" `
        "tests/unit/test_timeline_db.py" `
        "tests/unit/test_knowledge_store_advanced.py" `
        "-v" "--tb=short" "-q" 2>&1 | Out-String
    Pop-Location

    if ($pytestOut -match " failed") {
        $failLine = ($pytestOut -split "`n" | Select-String "failed|error" | Select-Object -Last 1).ToString().Trim()
        Add-Result "P2-06" "pytest ストレージテスト" "FAIL" $failLine
    } elseif ($pytestOut -match " passed") {
        $passLine = ($pytestOut -split "`n" | Select-String "passed" | Select-Object -Last 1).ToString().Trim()
        Add-Result "P2-06" "pytest ストレージテスト" "PASS" $passLine
    } else {
        Add-Result "P2-06" "pytest ストレージテスト" "WARN" "出力不明確"
    }
} else {
    Add-Result "P2-06" "pytest ストレージテスト" "SKIP" "-RunPytest フラグなし"
}

# ------------------------------------------------------------------
# サマリ出力
# ------------------------------------------------------------------
Write-Host "`n========================================"
Write-Host "Phase 2 Test Summary"
Write-Host "========================================"
Write-Host "PASS: $passCount"
Write-Host "FAIL: $failCount"
Write-Host "WARN: $warnCount"
Write-Host "SKIP: $skipCount"

$jsonPath = "$OutputDir\phase2_test_result.json"
$mdPath   = "$OutputDir\phase2_test_result.md"

$results | ConvertTo-Json -Depth 4 | Out-File -FilePath $jsonPath -Encoding UTF8

$md = "# Phase 2 Test Result`n`n| ID | Name | Status | Details |`n|---|---|---|---|`n"
foreach ($r in $results) {
    $md += "| $($r.id) | $($r.name) | $($r.status) | $($r.details) |`n"
}
$md | Out-File -FilePath $mdPath -Encoding UTF8

Write-Host "JSON: $jsonPath"
Write-Host "MD  : $mdPath"
Write-Host "========================================"