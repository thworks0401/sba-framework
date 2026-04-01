"""
pytest 共通設定と フィクスチャ

次のフィクスチャを提供:
  - event_loop: asyncio イベントループ
  - mock_tier1: Tier1 LLM モック
  - mock_tier2: Tier2 LLM モック
  - mock_tier3: Tier3 LLM モック
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.sba.inference.tier1 import Tier1Engine
from src.sba.inference.tier2 import Tier2Engine
from src.sba.inference.tier3 import Tier3Engine


@pytest.fixture(scope="session")
def event_loop():
    """pytest-asyncio 用イベントループ"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_tier1():
    """Tier1 エンジン モック"""
    mock = AsyncMock(spec=Tier1Engine)
    mock.chat = AsyncMock(return_value={"text": '{"result": "mock"}'})
    mock.generate = AsyncMock(return_value={"text": "mock generation"})
    return mock


@pytest.fixture
def mock_tier2():
    """Tier2 エンジン モック"""
    mock = AsyncMock(spec=Tier2Engine)
    mock.chat = AsyncMock(return_value={"text": "mock response"})
    mock.get_remaining_quota = MagicMock(return_value=500)
    return mock


@pytest.fixture
def mock_tier3():
    """Tier3 エンジン モック"""
    mock = AsyncMock(spec=Tier3Engine)
    mock.chat = AsyncMock(return_value={"text": "```python\nprint('mock')\n```"})
    return mock


@pytest.fixture
def mock_vram_guard():
    """VRAM Guard モック"""
    mock = MagicMock()
    mock.acquire = MagicMock(return_value=True)
    mock.release = MagicMock()
    return mock


# pytest-asyncio 設定
pytest_plugins = ('pytest_asyncio',)
