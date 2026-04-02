"""
T-2: Inference engine + VRAM guard tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sba.inference.engine_router import (
    EngineRouter,
    InferenceTask,
    SelectedTier,
    TaskType,
)
from src.sba.inference.tier1 import InferenceResult as Tier1Result
from src.sba.inference.tier2 import InferenceResult as Tier2Result
from src.sba.inference.tier3 import InferenceResult as Tier3Result
from src.sba.utils.vram_guard import ModelType, VRAMGuard, VRAMGuardError


def _build_router():
    tier1 = MagicMock()
    tier1.get_latest_wait_time.return_value = 0.1
    tier1.get_current_latency.return_value = 0.2
    tier1.infer = AsyncMock(return_value=Tier1Result(text="tier1", latency_ms=10))

    tier2 = MagicMock()
    tier2.get_remaining_quota.return_value = {
        "remaining_tokens": 500,
        "daily_used": 100,
        "status": "active",
    }
    tier2.infer = AsyncMock(return_value=Tier2Result(text="tier2", latency_ms=20))

    tier3 = MagicMock()
    tier3.get_latest_latency.return_value = 0.3
    tier3.get_latest_wait_time.return_value = 0.0
    tier3.generate_code = AsyncMock(return_value=Tier3Result(text="print(1)", latency_ms=30))

    return EngineRouter(tier1=tier1, tier2=tier2, tier3=tier3), tier1, tier2, tier3


def test_router_routes_code_tasks_to_tier3():
    router, _, _, _ = _build_router()

    decision = router.route(
        InferenceTask(
            type=TaskType.CODE_GENERATION,
            prompt="generate python",
            estimated_tokens=200,
            is_tech_brain=True,
        )
    )

    assert decision.selected_tier == SelectedTier.TIER3


def test_router_routes_long_text_to_tier2_when_quota_available():
    router, _, _, _ = _build_router()

    decision = router.route(
        InferenceTask(
            type=TaskType.LONG_TEXT,
            prompt="summarize",
            estimated_tokens=9000,
        )
    )

    assert decision.selected_tier == SelectedTier.TIER2


def test_router_falls_back_to_tier1_when_tier2_quota_is_low():
    router, tier1, tier2, _ = _build_router()
    tier2.get_remaining_quota.return_value = {
        "remaining_tokens": 50,
        "daily_used": 1400,
        "status": "stopped",
    }
    tier1.get_latest_wait_time.return_value = 1.5

    decision = router.route(
        InferenceTask(
            type=TaskType.SUMMARIZATION,
            prompt="summarize",
            estimated_tokens=9500,
        )
    )

    assert decision.selected_tier == SelectedTier.TIER1
    assert "fallback" in decision.reason.lower()


@pytest.mark.asyncio
async def test_router_dispatches_to_selected_engine():
    router, _, _, tier3 = _build_router()

    result = await router.infer(
        InferenceTask(
            type=TaskType.CODE_REVIEW,
            prompt="review code",
            estimated_tokens=300,
            is_tech_brain=True,
            max_output_tokens=512,
        )
    )

    assert result.text == "print(1)"
    tier3.generate_code.assert_awaited_once()


def test_vram_guard_blocks_incompatible_models():
    guard = VRAMGuard(timeout_s=0.1)
    guard.acquire_lock(ModelType.TIER1)

    with pytest.raises(VRAMGuardError):
        guard.acquire_lock(ModelType.TIER3)

    guard.release_lock(ModelType.TIER1)
    assert guard.is_locked() is False


@patch("src.sba.utils.vram_guard.ollama.generate")
def test_vram_guard_unloads_ollama_before_whisper(mock_generate):
    guard = VRAMGuard(timeout_s=0.1)

    guard.acquire_lock(ModelType.WHISPER)
    try:
        assert guard.get_current_model() == ModelType.WHISPER
        assert mock_generate.call_count == 2
    finally:
        guard.release_lock(ModelType.WHISPER)

