"""
pytest 共通設定とフィクスチャ

外部パッケージが未インストールでもテストが動くよう、
sys.modules でスタブを差し込んでからSBAモジュールをインポートする。

【修正履歴】
  2026-04-03:
    - event_loop fixture のスコープを session → function に変更
      （pytest-asyncio v0.21+ では session スコープは DeprecationWarning）
    - pytest_plugins タプルを削除（pytest.ini の asyncio_mode=auto で十分）
    - mock_tier2.get_remaining_quota の戻り値に daily_used=0 を明示追加
      （engine_router.py が daily_used * 0.001 を計算するため None 禁止）
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


# ======================================================================
# 外部依存モジュールのスタブ化
# ======================================================================

def _stub(name: str, **attrs) -> types.ModuleType:
    """sys.modules にスタブモジュールを登録して返す。"""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# ── ollama ──
if "ollama" not in sys.modules:
    _ol = _stub("ollama")
    _ol.generate = MagicMock(return_value={"response": "", "eval_count": 0})
    _ol.chat     = MagicMock(return_value={"message": {"content": ""}, "eval_count": 0})

# ── google.generativeai ──
if "google" not in sys.modules:
    _stub("google")
if "google.generativeai" not in sys.modules:
    _genai = _stub("google.generativeai")
    _genai.configure = MagicMock()
    _genai.GenerativeModel = MagicMock()
    _gt = _stub("google.generativeai.types")
    _gt.GenerationConfig = MagicMock()
    _genai.types = _gt

# ── kuzu ──
if "kuzu" not in sys.modules:
    _kz = _stub("kuzu")
    _kz.Database   = MagicMock()
    _kz.Connection = MagicMock()

# ── qdrant_client ──
if "qdrant_client" not in sys.modules:
    _qc = _stub("qdrant_client")
    _qc.QdrantClient = MagicMock()
    _qm = _stub("qdrant_client.models")
    for _a in [
        "Distance", "VectorParams", "PointStruct",
        "Filter", "FieldCondition", "MatchValue",
    ]:
        setattr(_qm, _a, MagicMock())
    _qc.models = _qm
    _stub("qdrant_client.http")
    _stub("qdrant_client.http.models")

# ── sentence_transformers ──
if "sentence_transformers" not in sys.modules:
    _st = _stub("sentence_transformers")
    _st.SentenceTransformer = MagicMock()

# ── apscheduler ──
for _ap in [
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.schedulers.background",
    "apscheduler.jobstores",
    "apscheduler.jobstores.sqlalchemy",
    "apscheduler.triggers",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "apscheduler.job",
]:
    if _ap not in sys.modules:
        _stub(_ap)

if "apscheduler.schedulers.background" in sys.modules:
    sys.modules["apscheduler.schedulers.background"].BackgroundScheduler = MagicMock()
if "apscheduler.jobstores.sqlalchemy" in sys.modules:
    sys.modules["apscheduler.jobstores.sqlalchemy"].SQLAlchemyJobStore = MagicMock()
if "apscheduler.triggers.cron" in sys.modules:
    sys.modules["apscheduler.triggers.cron"].CronTrigger = MagicMock()
if "apscheduler.triggers.interval" in sys.modules:
    sys.modules["apscheduler.triggers.interval"].IntervalTrigger = MagicMock()
if "apscheduler.job" in sys.modules:
    sys.modules["apscheduler.job"].Job = MagicMock()

# ── loguru ──
if "loguru" not in sys.modules:
    _lg = _stub("loguru")
    _lg.logger = MagicMock()

# ── faster_whisper ──
if "faster_whisper" not in sys.modules:
    _fw = _stub("faster_whisper")
    _fw.WhisperModel = MagicMock()

# ── playwright ──
for _pw in ["playwright", "playwright.async_api"]:
    if _pw not in sys.modules:
        _stub(_pw)
if "playwright.async_api" in sys.modules:
    _pwa = sys.modules["playwright.async_api"]
    _pwa.async_playwright = MagicMock()
    _pwa.Browser = MagicMock()
    _pwa.Page = MagicMock()

# ── その他ライブラリ ──
for _m in [
    "plyer", "feedparser", "aiohttp", "yt_dlp",
    "duckduckgo_search", "pdfminer", "pdfminer.high_level", "pdfminer.layout",
]:
    if _m not in sys.modules:
        _stub(_m)

if "duckduckgo_search" in sys.modules:
    sys.modules["duckduckgo_search"].DDGS = MagicMock()


# ======================================================================
# スタブ後に SBA モジュールをインポート
# ======================================================================

from src.sba.inference.tier1 import Tier1Engine, InferenceResult as Tier1Result  # noqa: E402
from src.sba.inference.tier2 import Tier2Engine, InferenceResult as Tier2Result  # noqa: E402
from src.sba.inference.tier3 import Tier3Engine, InferenceResult as Tier3Result  # noqa: E402
from src.sba.utils.vram_guard import ModelType                                    # noqa: E402


# ======================================================================
# フィクスチャ
# ======================================================================

# event_loop は function スコープ（pytest-asyncio v0.21+ デフォルト）
# session スコープにすると DeprecationWarning が出るため削除。
# asyncio_mode=auto（pytest.ini）により各テストが自動でイベントループを持つ。


@pytest.fixture
def mock_tier1():
    """Tier1Engine の AsyncMock フィクスチャ。"""
    mock = AsyncMock(spec=Tier1Engine)
    mock.chat  = AsyncMock(return_value=Tier1Result(text='{"result": "mock"}', latency_ms=0.0))
    mock.infer = AsyncMock(return_value=Tier1Result(text="mock generation",    latency_ms=0.0))
    mock.extract_json         = MagicMock(return_value={"result": "mock"})
    mock.get_latest_wait_time = MagicMock(return_value=0.1)
    mock.get_current_latency  = MagicMock(return_value=0.2)
    return mock


@pytest.fixture
def mock_tier2():
    """
    Tier2Engine の AsyncMock フィクスチャ。

    get_remaining_quota の daily_used は必ず int/float を返すこと。
    engine_router.py が `daily_used * 0.001` を計算するため None は禁止。
    """
    mock = AsyncMock(spec=Tier2Engine)
    mock.infer     = AsyncMock(return_value=Tier2Result(text="mock response", latency_ms=0.0))
    mock.summarize = AsyncMock(return_value=Tier2Result(text="mock summary",  latency_ms=0.0))
    mock.get_remaining_quota = MagicMock(return_value={
        "remaining_tokens": 500,
        "daily_used": 0,      # ← None 禁止。必ず数値を入れる
        "status": "active",
    })
    mock.extract_json = MagicMock(return_value={"result": "mock"})
    return mock


@pytest.fixture
def mock_tier3():
    """Tier3Engine の AsyncMock フィクスチャ。"""
    mock = AsyncMock(spec=Tier3Engine)
    mock.generate_code = AsyncMock(return_value=Tier3Result(text='print("mock")', latency_ms=0.0))
    mock.review_code   = AsyncMock(return_value=Tier3Result(
        text='{"issues": [], "score": 90, "summary": "OK"}', latency_ms=0.0
    ))
    mock.extract_json         = MagicMock(return_value={"score": 90})
    mock.get_latest_latency   = MagicMock(return_value=0.3)
    mock.get_latest_wait_time = MagicMock(return_value=0.0)
    return mock


@pytest.fixture
def mock_vram_guard():
    """VRAMGuard の MagicMock フィクスチャ。"""
    mock = MagicMock()
    mock.acquire_lock      = MagicMock(return_value=True)
    mock.release_lock      = MagicMock()
    mock.is_locked         = MagicMock(return_value=False)
    mock.get_current_model = MagicMock(return_value=ModelType.NONE)
    return mock
