"""
pytest 共通設定とフィクスチャ

次のフィクスチャを提供:
  - event_loop: asyncio イベントループ
  - mock_tier1: Tier1 LLM モック
  - mock_tier2: Tier2 LLM モック
  - mock_tier3: Tier3 LLM モック
  - mock_vram_guard: VRAMGuard モック

【修正履歴】
  - mock_tier1/2/3.chat の戻り値を dict から InferenceResult に変更
    （Tier1Engine.chat() / Tier2Engine.infer() の実際の戻り値型に合わせる）
  - mock_tier3 の chat() を generate_code() に変更
    （Tier3Engine には chat() は存在せず generate_code() が正しい）
  - mock_vram_guard の acquire/release を acquire_lock/release_lock に変更
    （VRAMGuard の正しい public API に合わせる）
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.sba.inference.tier1 import Tier1Engine, InferenceResult as Tier1Result
from src.sba.inference.tier2 import Tier2Engine, InferenceResult as Tier2Result
from src.sba.inference.tier3 import Tier3Engine, InferenceResult as Tier3Result
from src.sba.utils.vram_guard import ModelType


@pytest.fixture(scope="session")
def event_loop():
    """pytest-asyncio 用イベントループ"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_tier1():
    """
    Tier1 エンジン モック。

    chat() / infer() は InferenceResult を返す。
    テスト側で戻り値を変えたい場合は:
        mock_tier1.chat.return_value = Tier1Result(text="...", latency_ms=0.0)
    """
    mock = AsyncMock(spec=Tier1Engine)
    # 修正: dict → InferenceResult（.text でアクセスできる形式）
    mock.chat = AsyncMock(
        return_value=Tier1Result(
            text='{"result": "mock"}',
            latency_ms=0.0,
        )
    )
    mock.infer = AsyncMock(
        return_value=Tier1Result(
            text="mock generation",
            latency_ms=0.0,
        )
    )
    mock.extract_json = MagicMock(return_value={"result": "mock"})
    mock.get_latest_wait_time = MagicMock(return_value=0.1)
    mock.get_current_latency = MagicMock(return_value=0.2)
    return mock


@pytest.fixture
def mock_tier2():
    """
    Tier2 エンジン モック。

    infer() / summarize() は InferenceResult を返す。
    """
    mock = AsyncMock(spec=Tier2Engine)
    # 修正: dict → InferenceResult
    mock.infer = AsyncMock(
        return_value=Tier2Result(
            text="mock response",
            latency_ms=0.0,
        )
    )
    mock.summarize = AsyncMock(
        return_value=Tier2Result(
            text="mock summary",
            latency_ms=0.0,
        )
    )
    mock.get_remaining_quota = MagicMock(return_value={
        "remaining_tokens": 500,
        "daily_used": 100,
        "status": "active",
    })
    mock.extract_json = MagicMock(return_value={"result": "mock"})
    return mock


@pytest.fixture
def mock_tier3():
    """
    Tier3 エンジン モック。

    generate_code() / review_code() は InferenceResult を返す。
    Tier3Engine に chat() は存在しないので設定しない。
    """
    mock = AsyncMock(spec=Tier3Engine)
    # 修正: chat() → generate_code()（Tier3 の正しい API）
    mock.generate_code = AsyncMock(
        return_value=Tier3Result(
            text='print("mock")',
            latency_ms=0.0,
        )
    )
    mock.review_code = AsyncMock(
        return_value=Tier3Result(
            text='{"issues": [], "score": 90, "summary": "OK"}',
            latency_ms=0.0,
        )
    )
    mock.extract_json = MagicMock(return_value={"score": 90})
    mock.get_latest_latency = MagicMock(return_value=0.3)
    mock.get_latest_wait_time = MagicMock(return_value=0.0)
    return mock


@pytest.fixture
def mock_vram_guard():
    """
    VRAMGuard モック。

    修正: acquire/release → acquire_lock(ModelType)/release_lock(ModelType)
    （VRAMGuard の正しい public API に合わせる）
    """
    mock = MagicMock()
    # 修正: acquire_lock / release_lock を設定
    mock.acquire_lock = MagicMock(return_value=True)
    mock.release_lock = MagicMock()
    mock.is_locked = MagicMock(return_value=False)
    mock.get_current_model = MagicMock(return_value=None)
    return mock


# pytest-asyncio 設定
pytest_plugins = ('pytest_asyncio',)
