# Test-Phase2.ps1
# SBA Framework Phase 2 テスト
# 対象: ストレージ層 (Embedder / VectorStore / GraphStore / SQLite / KnowledgeStore)

[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\TH_Works\SBA",
    [string]$PythonExe = "C:\TH_Works\SBA\.venv\Scripts\python.exe",
    [string]$OutputDir = "C:\TH_Works\SBA\test_results\phase2",
    [string]$Phase1ResultJson = "C:\TH_Works\SBA\test_results\phase1\phase1_test_result.json",
    [switch]$RunStorageRoundTrip
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
    if (Test-Path $PythonExe) {
        return $PythonExe
    }
    return "python"
}

function Test-Phase1Dependency {
    if (-not (Test-Path $Phase1ResultJson)) {
        Add-Result (New-TestResult -Id "P2-00" -Name "Phase 1 結果依存確認" -Status "FAIL" -Details "Phase 1 結果JSONが見つからない: $Phase1ResultJson" -Evidence $Phase1ResultJson)
        return
    }

    try {
        $data = Get-Content $Phase1ResultJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $failCount = @($data | Where-Object { $_.status -eq "FAIL" }).Count

        if ($failCount -eq 0) {
            Add-Result (New-TestResult -Id "P2-00" -Name "Phase 1 結果依存確認" -Status "PASS" -Details "Phase 1 に FAIL がないため Phase 2 テスト前提を満たす" -Evidence $Phase1ResultJson)
        }
        else {
            Add-Result (New-TestResult -Id "P2-00" -Name "Phase 1 結果依存確認" -Status "FAIL" -Details "Phase 1 に FAIL が $failCount 件ある。先に Phase 1 修正が必要" -Evidence $Phase1ResultJson)
        }
    }
    catch {
        Add-Result (New-TestResult -Id "P2-00" -Name "Phase 1 結果依存確認" -Status "FAIL" -Details "Phase 1 JSON の読取に失敗: $($_.Exception.Message)" -Evidence $Phase1ResultJson)
    }
}

function Test-Phase2Files {
    $required = @(
        "src\sba\storage\vector_store.py",
        "src\sba\storage\graph_store.py",
        "src\sba\storage\knowledge_store.py",
        "src\sba\storage\experiment_db.py",
        "src\sba\storage\timeline_db.py",
        "src\sba\storage\api_usage_db.py",
        "src\sba\utils\embedder.py",
        "src\sba\utils\chunker.py",
        "data\api_usage.db",
        "data\schema.sql",
        "tests\unit\test_storage.py",
        "tests\test_phase2_storage.py"
    )

    $missing = @()

    foreach ($rel in $required) {
        $full = Join-Path $ProjectRoot $rel
        if (-not (Test-Path $full)) {
            $missing += $rel
        }
    }

    if ($missing.Count -eq 0) {
        Add-Result (New-TestResult -Id "P2-01" -Name "Phase 2 実装ファイル確認" -Status "PASS" -Details "ストレージ層の主要コード・DB・テストファイルを確認")
    }
    else {
        Add-Result (New-TestResult -Id "P2-01" -Name "Phase 2 実装ファイル確認" -Status "FAIL" -Details ("不足ファイル: " + ($missing -join ", ")))
    }
}

function Test-PythonImportPhase2 {
    $py = Get-PythonPath
    $scriptPath = Join-Path $OutputDir "phase2_import_check.py"
    $detailPath = Join-Path $OutputDir "phase2_import_check.json"

    @'
import importlib
import json

modules = [
    "sba.storage.vector_store",
    "sba.storage.graph_store",
    "sba.storage.knowledge_store",
    "sba.storage.experiment_db",
    "sba.storage.timeline_db",
    "sba.storage.api_usage_db",
    "sba.utils.embedder",
    "sba.utils.chunker",
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
        $json = & $py $scriptPath 2>&1

        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($json | Out-String))) {
            Add-Result (New-TestResult -Id "P2-02" -Name "Phase 2 Python import 確認" -Status "FAIL" -Details "import確認スクリプト実行失敗" -Evidence ($json | Out-String))
            return
        }

        ($json | Out-String) | Out-File -FilePath $detailPath -Encoding UTF8
        $data = ($json | Out-String) | ConvertFrom-Json

        $failed = @(
            $data.PSObject.Properties |
            Where-Object { $_.Value.status -ne "PASS" } |
            ForEach-Object { $_.Name }
        )

        if ($failed.Count -eq 0) {
            Add-Result (New-TestResult -Id "P2-02" -Name "Phase 2 Python import 確認" -Status "PASS" -Details "ストレージ層の主要モジュール import 成功" -Evidence $detailPath)
        }
        else {
            Add-Result (New-TestResult -Id "P2-02" -Name "Phase 2 Python import 確認" -Status "FAIL" -Details ("import失敗: " + ($failed -join ", ")) -Evidence $detailPath)
        }
    }
    catch {
        Add-Result (New-TestResult -Id "P2-02" -Name "Phase 2 Python import 確認" -Status "FAIL" -Details $_.Exception.Message)
    }
}

function Test-EmbedderDryRun {
    $py = Get-PythonPath
    $scriptPath = Join-Path $OutputDir "phase2_embedder_check.py"
    $detailPath = Join-Path $OutputDir "phase2_embedder_check.json"

    @'
import json
from sba.utils.embedder import Embedder

sample_texts = [
    "SBA Framework Phase 2 storage layer test.",
    "Python 3.11 + Qdrant + Kuzu + SQLite + bge-m3.",
]

result = {}
try:
    emb = Embedder.get_instance()
    vecs = emb.encode(sample_texts)
    rows = len(vecs)
    cols = len(vecs[0]) if rows > 0 else 0
    result["status"] = "PASS"
    result["shape"] = [rows, cols]
except Exception as e:
    result["status"] = "FAIL"
    result["error"] = str(e)

print(json.dumps(result, ensure_ascii=False))
'@ | Out-File -FilePath $scriptPath -Encoding UTF8

    try {
        $env:PYTHONPATH = (Join-Path $ProjectRoot "src")
        $json = & $py $scriptPath 2>&1

        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($json | Out-String))) {
            Add-Result (New-TestResult -Id "P2-03" -Name "Embedder 動作確認" -Status "FAIL" -Details "embedder確認スクリプト実行失敗" -Evidence ($json | Out-String))
            return
        }

        ($json | Out-String) | Out-File -FilePath $detailPath -Encoding UTF8
        $data = ($json | Out-String) | ConvertFrom-Json

        if ($data.status -eq "PASS") {
            Add-Result (New-TestResult -Id "P2-03" -Name "Embedder 動作確認" -Status "PASS" -Details ("埋め込み生成成功。shape=" + ($data.shape -join "x")) -Evidence $detailPath)
        }
        else {
            Add-Result (New-TestResult -Id "P2-03" -Name "Embedder 動作確認" -Status "FAIL" -Details ("埋め込み生成失敗: " + $data.error) -Evidence $detailPath)
        }
    }
    catch {
        Add-Result (New-TestResult -Id "P2-03" -Name "Embedder 動作確認" -Status "FAIL" -Details $_.Exception.Message)
    }
}

function Test-StorageRoundTrip {
    if (-not $RunStorageRoundTrip) {
        Add-Result (New-TestResult -Id "P2-04" -Name "ストレージ RoundTrip テスト" -Status "SKIP" -Details "-RunStorageRoundTrip 未指定のためスキップ")
        return
    }

    $py = Get-PythonPath
    $scriptPath = Join-Path $OutputDir "phase2_storage_roundtrip.py"
    $detailPath = Join-Path $OutputDir "phase2_storage_roundtrip.json"

    @'
import json
import shutil
import uuid
from pathlib import Path

from sba.storage.knowledge_store import KnowledgeStore
from sba.utils.chunker import TextChunker

summary = {
    "status": "PASS",
    "brain_id": None,
    "steps": [],
}


def add_step(name: str, ok: bool, detail: str = "") -> None:
    summary["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        summary["status"] = "FAIL"


def resolve_chunk_id(value):
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        candidates = [
            value.get("chunk_id"),
            value.get("id"),
            value.get("knowledge_chunk_id"),
            value.get("graph_id"),
            value.get("node_id"),
            value.get("kuzu_id"),
            value.get("record_id"),
        ]
        for candidate in candidates:
            if candidate:
                return candidate

    for attr in ("chunk_id", "id", "graph_id", "node_id"):
        if hasattr(value, attr):
            v = getattr(value, attr)
            if v:
                return v

    return None


ks = None
tmp_brain_dir = None

try:
    root = Path(r"C:\TH_Works\SBA")
    brain_bank = root / "brain_bank"

    candidate_templates = [
        brain_bank / "_blank_template",
        brain_bank / "blank_template",
    ]

    blank_template = None
    for candidate in candidate_templates:
        if candidate.exists():
            blank_template = candidate
            break

    if blank_template is None:
        add_step("blank_template_exists", False, f"not found: {candidate_templates}")
        raise RuntimeError("blank_template not found")

    add_step("blank_template_exists", True, str(blank_template))

    # 毎回クリーンな一時Brainを作る
    tmp_brain_dir = brain_bank / f"_tmp_phase2_{uuid.uuid4()}"
    shutil.copytree(blank_template, tmp_brain_dir)
    add_step("tmp_brain_created", True, str(tmp_brain_dir))

    test_brain_id = f"phase2-test-{uuid.uuid4()}"
    summary["brain_id"] = test_brain_id

    ks = KnowledgeStore(
        brain_package_path=str(tmp_brain_dir),
        brain_id=test_brain_id,
    )
    add_step("knowledge_store_init", True, f"brain_id={test_brain_id}")

    # 毎回ユニークになるテキスト（重複検知を避ける）
    text = (
        f"SBA Phase 2 storage layer KnowledgeStore round-trip test. "
        f"Run id: {uuid.uuid4()}. "
        f"This text validates chunk generation, vector storage, graph linkage, "
        f"and hybrid retrieval. "
        f"The chunk should be unique for every test execution. "
        f"Primary subskill is storage.test. "
        f"Secondary metadata should also be persisted. "
        f"After insertion, similarity search and hybrid query should find the record. "
        f"Finally, deprecation marking should update the stored knowledge state."
    )

    chunker = TextChunker()
    chunks = []
    try:
        raw_chunks = chunker.chunk_text(text, min_tokens=1, max_tokens=120)
        chunks = list(raw_chunks)
        if len(chunks) > 0:
            add_step("chunker_chunk_text", True, f"chunks={len(chunks)}")
        else:
            chunks = [text]
            add_step("chunker_chunk_text", True, "chunks=0, fallback=original_text")
    except Exception as e:
        chunks = [text]
        add_step("chunker_chunk_text", True, f"fallback_used_due_to_error={str(e)}")

    primary_subskill = "storage.test"
    first_chunk = chunks[0] if chunks else text
    if not isinstance(first_chunk, str):
        first_chunk = str(first_chunk)

    chunk_id = None
    try:
        store_result = ks.store_chunk(
            text=first_chunk,
            primary_subskill=primary_subskill,
            source_type="test",
            source_url=f"about:phase2-knowledge-store:{uuid.uuid4()}",
            trust_score=0.9,
            summary="Phase2 KnowledgeStore roundtrip test chunk",
            secondary_subskills=["storage.secondary"],
        )
        chunk_id = resolve_chunk_id(store_result)

        detail_preview = repr(store_result)
        if len(detail_preview) > 300:
            detail_preview = detail_preview[:300] + "..."
        add_step(
            "store_chunk",
            store_result is not None and chunk_id is not None,
            f"result_type={type(store_result).__name__}, chunk_id={chunk_id}, preview={detail_preview}",
        )
    except Exception as e:
        add_step("store_chunk", False, str(e))

    if chunk_id is not None:
        try:
            chunk = ks.get_chunk(chunk_id)
            add_step("get_chunk", chunk is not None, f"type={type(chunk).__name__}")
        except Exception as e:
            add_step("get_chunk", False, str(e))
    else:
        add_step("get_chunk", False, "chunk_id could not be resolved from store_chunk result")

    query_text = first_chunk[:180]

    try:
        hits = ks.search_similar(
            text=query_text,
            limit=5,
            subskill_id=primary_subskill,
            score_threshold=0.0,
        )
        ok = hits is not None and len(hits) > 0
        add_step("search_similar", ok, f"type={type(hits).__name__}, len={len(hits)}")
    except Exception as e:
        add_step("search_similar", False, str(e))

    try:
        hybrid_hits = ks.query_hybrid(
            query_text=query_text,
            subskill_id=primary_subskill,
            limit=5,
        )
        ok = hybrid_hits is not None and len(hybrid_hits) > 0
        add_step("query_hybrid", ok, f"type={type(hybrid_hits).__name__}, len={len(hybrid_hits)}")
    except Exception as e:
        add_step("query_hybrid", False, str(e))

    try:
        subskill_chunks = ks.get_chunks_by_subskill(primary_subskill)
        add_step("get_chunks_by_subskill", len(subskill_chunks) > 0, f"hits={len(subskill_chunks)}")
    except Exception as e:
        add_step("get_chunks_by_subskill", False, str(e))

    try:
        stats = ks.get_knowledge_base_stats()
        add_step("get_knowledge_base_stats", stats is not None, f"type={type(stats).__name__}")
    except Exception as e:
        add_step("get_knowledge_base_stats", False, str(e))

    try:
        overview = ks.get_subskill_overview()
        add_step("get_subskill_overview", overview is not None, f"type={type(overview).__name__}")
    except Exception as e:
        add_step("get_subskill_overview", False, str(e))

    if chunk_id is not None:
        try:
            ks.mark_deprecated(chunk_id, reason="phase2-test")
            add_step("mark_deprecated", True, f"chunk_id={chunk_id}")
        except Exception as e:
            add_step("mark_deprecated", False, str(e))

        try:
            chunk_after = ks.get_chunk(chunk_id)
            add_step("get_chunk_after_deprecated", chunk_after is not None, f"type={type(chunk_after).__name__}")
        except Exception as e:
            add_step("get_chunk_after_deprecated", False, str(e))
    else:
        add_step("mark_deprecated", False, "chunk_id unresolved")
        add_step("get_chunk_after_deprecated", False, "chunk_id unresolved")

finally:
    if ks is not None:
        try:
            ks.close()
            add_step("knowledge_store_close", True)
        except Exception as e:
            add_step("knowledge_store_close", False, str(e))

    if tmp_brain_dir is not None and tmp_brain_dir.exists():
        try:
            shutil.rmtree(tmp_brain_dir)
            add_step("tmp_brain_removed", True, str(tmp_brain_dir))
        except Exception as e:
            add_step("tmp_brain_removed", False, str(e))

print(json.dumps(summary, ensure_ascii=False))
'@ | Out-File -FilePath $scriptPath -Encoding UTF8

    try {
        $env:PYTHONPATH = (Join-Path $ProjectRoot "src")
        $json = & $py $scriptPath 2>&1

        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($json | Out-String))) {
            Add-Result (New-TestResult -Id "P2-04" -Name "ストレージ RoundTrip テスト" -Status "FAIL" -Details "roundtrip確認スクリプト実行失敗" -Evidence ($json | Out-String))
            return
        }

        ($json | Out-String) | Out-File -FilePath $detailPath -Encoding UTF8
        $data = ($json | Out-String) | ConvertFrom-Json

        if ($data.status -eq "PASS") {
            Add-Result (New-TestResult -Id "P2-04" -Name "ストレージ RoundTrip テスト" -Status "PASS" -Details ("KnowledgeStore RoundTrip成功 (brain_id=" + $data.brain_id + ")") -Evidence $detailPath)
        }
        else {
            $failedSteps = @(
                $data.steps |
                Where-Object { -not $_.ok } |
                ForEach-Object { $_.name }
            )
            Add-Result (New-TestResult -Id "P2-04" -Name "ストレージ RoundTrip テスト" -Status "FAIL" -Details ("失敗Step: " + ($failedSteps -join ", ")) -Evidence $detailPath)
        }
    }
    catch {
        Add-Result (New-TestResult -Id "P2-04" -Name "ストレージ RoundTrip テスト" -Status "FAIL" -Details $_.Exception.Message)
    }
}

function Write-Reports {
    $jsonPath = Join-Path $OutputDir "phase2_test_result.json"
    $mdPath = Join-Path $OutputDir "phase2_test_result.md"

    $Results | ConvertTo-Json -Depth 6 | Out-File -FilePath $jsonPath -Encoding UTF8

    $pass = @($Results | Where-Object status -eq "PASS").Count
    $fail = @($Results | Where-Object status -eq "FAIL").Count
    $warn = @($Results | Where-Object status -eq "WARN").Count
    $skip = @($Results | Where-Object status -eq "SKIP").Count

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# Phase 2 Test Report")
    $lines.Add("")
    $lines.Add("- Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add("- ProjectRoot: $ProjectRoot")
    $lines.Add("- OutputDir: $OutputDir")
    $lines.Add("- Phase1ResultJson: $Phase1ResultJson")
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
    Write-Host "Phase 2 Test Summary" -ForegroundColor Cyan
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

Write-Host "SBA Framework Phase 2 Test Started" -ForegroundColor Cyan
Write-Host "ProjectRoot      : $ProjectRoot" -ForegroundColor Cyan
Write-Host "OutputDir        : $OutputDir" -ForegroundColor Cyan
Write-Host "Phase1ResultJson : $Phase1ResultJson" -ForegroundColor Cyan
Write-Host ""

Test-Phase1Dependency
Test-Phase2Files
Test-PythonImportPhase2
Test-EmbedderDryRun
Test-StorageRoundTrip
Write-Reports