# SBA Phase 0 External Dependency Check
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\phase0_external_dependency_check.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\phase0_external_dependency_check.ps1 -CheckEmbeddingModel

[CmdletBinding()]
param(
    [switch]$CheckEmbeddingModel
)

$ErrorActionPreference = "Continue"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $RepoRoot ".env"
$ConfigFile = Join-Path $RepoRoot "sba_config.yaml"

$script:PassCount = 0
$script:WarnCount = 0
$script:FailCount = 0

function Write-Ok($Message) {
    Write-Host "[OK]   $Message" -ForegroundColor Green
    $script:PassCount++
}

function Write-Warn($Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
    $script:WarnCount++
}

function Write-Fail($Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    $script:FailCount++
}

function Write-Info($Message) {
    Write-Host "       $Message" -ForegroundColor DarkGray
}

function Test-CommandExists([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-PythonImport([string]$ModuleName) {
    $code = @'
import importlib.util, sys
name = sys.argv[1]
print("ok" if importlib.util.find_spec(name) else "missing")
'@
    $tempFile = Join-Path $env:TEMP ("sba_import_check_" + [guid]::NewGuid().ToString() + ".py")
    Set-Content -LiteralPath $tempFile -Value $code -Encoding UTF8
    try {
        $result = (& $VenvPython $tempFile $ModuleName 2>$null | Out-String).Trim()
    } finally {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
    return $result -eq "ok"
}

function Invoke-PythonCheck([string]$Code) {
    $tempFile = Join-Path $env:TEMP ("sba_runtime_check_" + [guid]::NewGuid().ToString() + ".py")
    Set-Content -LiteralPath $tempFile -Value $Code -Encoding UTF8
    try {
        return ((& $VenvPython $tempFile 2>&1) | Out-String).Trim()
    } finally {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
}

function Test-ApiKeyPresent([string]$KeyName) {
    if (-not (Test-Path $EnvFile)) {
        return $false
    }

    $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$KeyName\s*=" } | Select-Object -First 1
    if (-not $line) {
        return $false
    }

    $value = ($line -split "=", 2)[1].Trim()
    return -not [string]::IsNullOrWhiteSpace($value)
}

Write-Host ""
Write-Host "SBA Phase 0 External Dependency Check" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot" -ForegroundColor DarkCyan
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkCyan
Write-Host ""

Write-Host "== Basic Files ==" -ForegroundColor Cyan
if (Test-Path $VenvPython) {
    $pyVersion = & $VenvPython --version 2>&1
    if ($pyVersion -match "3\.11") {
        Write-Ok "Python virtualenv detected: $pyVersion"
    } else {
        Write-Warn "Virtualenv exists but Python 3.11 was not detected: $pyVersion"
    }
} else {
    Write-Fail ".venv Python not found: $VenvPython"
}

if (Test-Path $ConfigFile) {
    Write-Ok "Config file found: sba_config.yaml"
} else {
    Write-Fail "Config file missing: sba_config.yaml"
}

if (Test-Path $EnvFile) {
    Write-Ok "Environment file found: .env"
} else {
    Write-Warn "Environment file missing: .env"
}

Write-Host ""
Write-Host "== Python Packages ==" -ForegroundColor Cyan
$moduleChecks = @(
    @{ Name = "qdrant_client"; Label = "qdrant-client" },
    @{ Name = "kuzu"; Label = "kuzu" },
    @{ Name = "sentence_transformers"; Label = "sentence-transformers" },
    @{ Name = "playwright"; Label = "playwright" },
    @{ Name = "pydantic"; Label = "pydantic" },
    @{ Name = "typer"; Label = "typer" },
    @{ Name = "yaml"; Label = "pyyaml" }
)

foreach ($check in $moduleChecks) {
    if (Test-PythonImport $check.Name) {
        Write-Ok "Python module available: $($check.Label)"
    } else {
        Write-Fail "Python module missing: $($check.Label)"
    }
}

Write-Host ""
Write-Host "== External Commands ==" -ForegroundColor Cyan
if (Test-CommandExists "ollama") {
    Write-Ok "ollama command found"

    $ollamaList = ollama list 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "ollama list executed successfully"
        if ($ollamaList -match "phi4") {
            Write-Ok "Ollama model present: phi4"
        } else {
            Write-Warn "Ollama model missing: phi4"
        }
        if ($ollamaList -match "qwen2\.5-coder:7b") {
            Write-Ok "Ollama model present: qwen2.5-coder:7b"
        } else {
            Write-Warn "Ollama model missing: qwen2.5-coder:7b"
        }
    } else {
        Write-Warn "ollama list failed. Ollama service may not be running."
    }
} else {
    Write-Fail "ollama command not found"
}

if (Test-CommandExists "nssm") {
    Write-Ok "nssm command found"
} else {
    Write-Warn "nssm command not found"
}

Write-Host ""
Write-Host "== API Key Presence ==" -ForegroundColor Cyan
if (Test-ApiKeyPresent "GOOGLE_API_KEY" -or (Test-ApiKeyPresent "GEMINI_API_KEY")) {
    Write-Ok "Gemini API key entry is present"
} else {
    Write-Warn "Gemini API key entry is missing"
}

if (Test-ApiKeyPresent "GITHUB_TOKEN") {
    Write-Ok "GitHub token entry is present"
} else {
    Write-Warn "GitHub token entry is missing"
}

Write-Host ""
Write-Host "== Runtime Checks ==" -ForegroundColor Cyan
if (Test-Path $VenvPython) {
    $qdrantCheck = @'
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
import tempfile
tmp = tempfile.mkdtemp()
client = QdrantClient(path=tmp)
try:
    client.create_collection("check", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    client.upsert("check", [PointStruct(id=1, vector=[0.1, 0.2, 0.3, 0.4], payload={"ok": True})])
    hits = client.query_points(collection_name="check", query=[0.1, 0.2, 0.3, 0.4], limit=1).points
    print("ok" if len(hits) == 1 else "fail")
finally:
    client.close()
'@
    $qdrantResult = Invoke-PythonCheck $qdrantCheck
    if ($qdrantResult -eq "ok") {
        Write-Ok "Qdrant local read/write check passed"
    } else {
        Write-Fail "Qdrant local check failed: $qdrantResult"
    }

    $kuzuCheck = @'
import os
import tempfile
import kuzu
with tempfile.TemporaryDirectory() as tmp:
    db = kuzu.Database(os.path.join(tmp, "graph"))
    conn = kuzu.Connection(db)
    conn.execute("CREATE NODE TABLE Item(id STRING, name STRING, PRIMARY KEY(id))")
    conn.execute("CREATE (:Item {id: '1', name: 'ok'})")
    result = conn.execute("MATCH (i:Item) RETURN i.name")
    row = result.get_next()
    print("ok" if row[0] == "ok" else "fail")
'@
    $kuzuResult = Invoke-PythonCheck $kuzuCheck
    if ($kuzuResult -eq "ok") {
        Write-Ok "Kuzu create/query check passed"
    } else {
        Write-Fail "Kuzu runtime check failed: $kuzuResult"
    }

    $playwrightCheck = @'
from pathlib import Path
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        executable = p.chromium.executable_path
        print("ok" if executable and Path(executable).exists() else "missing")
except Exception as exc:
    print(f"fail:{exc}")
'@
    $playwrightResult = Invoke-PythonCheck $playwrightCheck
    if ($playwrightResult -eq "ok") {
        Write-Ok "Playwright Chromium runtime detected"
    } elseif ($playwrightResult -eq "missing") {
        Write-Warn "Playwright is installed but Chromium binary is missing"
    } else {
        Write-Warn "Playwright runtime check returned: $playwrightResult"
    }

    if ($CheckEmbeddingModel) {
        Write-Info "Embedding model check can take time on first run."
        $embeddingCheck = @'
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("BAAI/bge-m3", device="cpu")
vec = model.encode(["SBA dependency check"], normalize_embeddings=True, show_progress_bar=False)
print("ok" if len(vec[0]) == 1024 else "fail")
'@
        $embeddingResult = Invoke-PythonCheck $embeddingCheck
        if ($embeddingResult -eq "ok") {
            Write-Ok "BAAI/bge-m3 load and encode check passed"
        } else {
            Write-Fail "BAAI/bge-m3 load check failed: $embeddingResult"
        }
    } else {
        Write-Info "Skipped heavy embedding model check. Re-run with -CheckEmbeddingModel to verify bge-m3."
    }
}

Write-Host ""
Write-Host "== Summary ==" -ForegroundColor Cyan
Write-Host "PASS : $script:PassCount"
Write-Host "WARN : $script:WarnCount"
Write-Host "FAIL : $script:FailCount"
Write-Host ""

if ($script:FailCount -gt 0) {
    exit 1
}

exit 0
