"""
EngineRouter + VramGuard ユニットテスト

全てのエンジンを mock に差し替えて、
振り分けロジック・VRAM排他制御・フォールバックチェーンを検証する。
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sba.inference.tier1 import InferenceResult
from src.sba.inference.engine_router import EngineRouter, TIER1_WAIT_THRESHOLD_S
from src.sba.inference.vram_guard import (
    acquire_vram,
    force_release,
    get_vram_status,
    _VRAM_LOCK,
    _set_current_model,
)


# ======================================================================
# ヘルパー
# ======================================================================

def _make_ok_result(text: str = "ok") -> InferenceResult:
    return InferenceResult(text=text, latency_ms=100.0)


def _make_error_result(msg: str = "err") -> InferenceResult:
    return InferenceResult(text="", latency_ms=0.0, error=msg)


def _make_tier1(result: InferenceResult = None) -> MagicMock:
    t = MagicMock()
    t.latest_wait_time = 0.0
    t.infer = AsyncMock(return_value=result or _make_ok_result())
    return t


def _make_tier2(result: InferenceResult = None, status: str = "active") -> MagicMock:
    t = MagicMock()
    t.infer = AsyncMock(return_value=result or _make_ok_result("tier2_response"))
    t.get_remaining_quota = MagicMock(return_value={"status": status})
    return t


def _make_tier3(result: InferenceResult = None) -> MagicMock:
    t = MagicMock()
    t.infer = AsyncMock(return_value=result or _make_ok_result("tier3_response"))
    return t


# ======================================================================
# EngineRouter ルーティングテスト
# ======================================================================

class TestEngineRouterRouting:

    @pytest.mark.asyncio
    async def test_code_task_routes_to_tier3(self):
        """task_type='code' は Tier3 に振り分けられる"""
        tier3 = _make_tier3()
        router = EngineRouter(tier1=_make_tier1(), tier2=_make_tier2(), tier3=tier3)

        result = await router.infer("def hello(): pass", task_type="code")

        tier3.infer.assert_called_once()
        assert result.text == "tier3_response"

    @pytest.mark.asyncio
    async def test_long_text_routes_to_tier2_when_quota_available(self):
        """長文テキスト（> 8000 token 相当）かつ Quota あり → Tier2"""
        tier2 = _make_tier2(status="active")
        # 8000 * 4 = 32000文字超のプロンプトを作成
        long_prompt = "あ" * 33000
        router = EngineRouter(tier1=_make_tier1(), tier2=tier2, tier3=_make_tier3())

        result = await router.infer(long_prompt, task_type="summary")

        tier2.infer.assert_called_once()
        assert result.text == "tier2_response"

    @pytest.mark.asyncio
    async def test_long_text_falls_back_to_tier1_when_tier2_quota_low(self):
        """長文テキストでも Tier2 Quota 枯渇時は Tier1 にフォールバック"""
        tier1 = _make_tier1()
        tier2 = _make_tier2(status="stopped")
        long_prompt = "あ" * 33000
        router = EngineRouter(tier1=tier1, tier2=tier2, tier3=_make_tier3())

        result = await router.infer(long_prompt, task_type="summary")

        tier1.infer.assert_called_once()
        tier2.infer.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier1_wait_timeout_falls_back_to_tier2(self):
        """Tier1 待機時間超過時 → Tier2 フォールバック"""
        tier1 = _make_tier1()
        tier1.latest_wait_time = TIER1_WAIT_THRESHOLD_S + 1.0  # 閾値超過
        tier2 = _make_tier2(status="active")
        router = EngineRouter(tier1=tier1, tier2=tier2, tier3=_make_tier3())

        result = await router.infer("短いプロンプト", task_type="default")

        tier2.infer.assert_called_once()
        assert result.text == "tier2_response"

    @pytest.mark.asyncio
    async def test_default_task_routes_to_tier1(self):
        """デフォルトタスクは Tier1"""
        tier1 = _make_tier1()
        router = EngineRouter(tier1=tier1, tier2=_make_tier2(), tier3=_make_tier3())

        result = await router.infer("普通のプロンプト", task_type="default")

        tier1.infer.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier3_error_falls_back_to_tier1(self):
        """Tier3 エラー → Tier1 フォールバック"""
        tier1 = _make_tier1()
        tier3 = _make_tier3(result=_make_error_result("Tier3 fail"))
        router = EngineRouter(tier1=tier1, tier2=_make_tier2(), tier3=tier3)

        result = await router.infer("code task", task_type="code")

        # Tier3 エラー → Tier1 で最終応答
        tier1.infer.assert_called_once()
        assert result.text == "ok"

    @pytest.mark.asyncio
    async def test_tier2_error_falls_back_to_tier1(self):
        """Tier2 エラー → Tier1 フォールバック"""
        tier1 = _make_tier1()
        tier2 = _make_tier2(result=_make_error_result("Tier2 fail"), status="active")
        long_prompt = "あ" * 33000
        router = EngineRouter(tier1=tier1, tier2=tier2, tier3=_make_tier3())

        result = await router.infer(long_prompt, task_type="summary")

        tier1.infer.assert_called_once()
        assert result.text == "ok"

    @pytest.mark.asyncio
    async def test_force_tier1(self):
        """force_tier=1 は常に Tier1"""
        tier1 = _make_tier1()
        tier3 = _make_tier3()
        router = EngineRouter(tier1=tier1, tier2=_make_tier2(), tier3=tier3)

        await router.infer("code", task_type="code", force_tier=1)

        tier1.infer.assert_called_once()
        tier3.infer.assert_not_called()


# ======================================================================
# VramGuard テスト
# ======================================================================

class TestVramGuard:

    def setup_method(self):
        """各テスト前に VRAM 状態をリセット"""
        force_release(unload_current=False)
        _set_current_model(None)

    def test_acquire_sets_current_model(self):
        """acquire_vram で _current_model が更新される"""
        with acquire_vram("tier3"):
            status = get_vram_status()
            assert status["current_model"] == "tier3"

    def test_lock_is_released_after_context(self):
        """コンテキスト終了後にロックが解放される"""
        with acquire_vram("tier1"):
            pass  # ロック取得・解放

        # ロック解放後は別スレッドから取得可能
        result = []
        def try_acquire():
            try:
                with acquire_vram("tier3", timeout_s=1.0):
                    result.append(True)
            except Exception:
                result.append(False)

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=3.0)
        assert result == [True], "ロック解放後に別スレッドが取得できなかった"

    def test_incompatible_models_trigger_unload(self):
        """非互換エンジン切り替え時に unload_ollama_model が呼ばれる"""
        _set_current_model("tier1")  # tier1 がすでにロード済みの状態を模擬

        with patch("src.sba.inference.vram_guard.unload_ollama_model") as mock_unload:
            with acquire_vram("tier3"):
                pass
            mock_unload.assert_called_once_with("tier1")

    def test_same_engine_no_unload(self):
        """同じエンジンを再取得してもアンロードは発生しない"""
        _set_current_model("tier1")

        with patch("src.sba.inference.vram_guard.unload_ollama_model") as mock_unload:
            with acquire_vram("tier1"):
                pass
            mock_unload.assert_not_called()

    def test_unknown_engine_raises_value_error(self):
        """不明なエンジン名は ValueError"""
        with pytest.raises(ValueError, match="unknown engine"):
            with acquire_vram("tier_unknown"):
                pass

    def test_vram_status_after_acquire(self):
        """acquire_vram 後のステータスが正しい"""
        with acquire_vram("tier3"):
            status = get_vram_status()
            assert status["vram_used_gb"] == 5.0
            assert status["vram_free_gb"] == 3.0
            assert status["is_locked"] is True
