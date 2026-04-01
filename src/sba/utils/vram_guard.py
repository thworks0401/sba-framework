"""
VRAM排他制御ガード

設計根拠（推論エンジン・VRAM運用設定書 §4-5）:
  - RTX3060Ti 8GB VRAM は同時に1つの重いモデルのみ占有
  - 禁止組み合わせ:
    - Tier1（Phi-4:14B） + Tier3（Qwen2.5-Coder:7B）
    - Tier1 + Whisper
  - VRAMロック取得時に、競合モデルをアンロード
  - Whisper実行フロー: lock → unload Ollama → whisper → reload → unlock
"""

from __future__ import annotations

import threading
import time
from typing import Optional
from enum import Enum

import ollama


class ModelType(Enum):
    """モデルタイプ"""
    TIER1   = "tier1"    # Phi-4:14B
    TIER3   = "tier3"    # Qwen2.5-Coder:7B
    WHISPER = "whisper"  # faster-whisper
    NONE    = "none"


# Ollama モデル名マッピング（アンロード時に使用）
_OLLAMA_MODEL_NAMES = {
    ModelType.TIER1: "phi4",
    ModelType.TIER3: "qwen2.5-coder:7b",
}


class VRAMGuardError(Exception):
    """VRAM制御エラー"""


class VRAMGuard:
    """
    VRAM排他制御ガード。

    スレッドセーフなロックを提供し、同時に1つの重いモデルのみ実行を保証。
    """

    def __init__(self, timeout_s: float = 60.0) -> None:
        """
        Initialize VRAM Guard.

        Args:
            timeout_s: ロック取得タイムアウト秒数
        """
        self._lock = threading.Lock()
        self._current_model = ModelType.NONE
        self._timeout_s = timeout_s
        self._lock_time = None

    def acquire_lock(self, model_type: ModelType) -> bool:
        """
        VRAM排他ロックを取得。

        Args:
            model_type: 要求するモデルタイプ

        Returns:
            True if lock acquired

        Raises:
            VRAMGuardError: タイムアウトまたは禁止された組み合わせ
        """
        # 禁止組み合わせチェック
        self._check_compatibility(model_type)

        # ロック取得
        acquired = self._lock.acquire(timeout=self._timeout_s)
        if not acquired:
            raise VRAMGuardError(
                f"VRAM lock timeout ({self._timeout_s}s) waiting for {model_type.value}"
            )

        # 競合モデルをアンロード
        self._unload_conflicting_models(model_type)

        # 状態更新
        self._current_model = model_type
        self._lock_time = time.time()

        return True

    def release_lock(self, model_type: ModelType) -> None:
        """
        VRAM排他ロックを解放。

        Args:
            model_type: 解放するモデルタイプ

        Raises:
            VRAMGuardError: 所有していないロック
        """
        if self._current_model != model_type:
            raise VRAMGuardError(
                f"Cannot release lock for {model_type.value}: "
                f"currently held by {self._current_model.value}"
            )

        self._current_model = ModelType.NONE
        self._lock_time = None
        self._lock.release()

    def _check_compatibility(self, model_type: ModelType) -> None:
        """
        新規モデル起動の互換性をチェック。

        Raises:
            VRAMGuardError: 禁止された組み合わせ
        """
        if self._current_model == ModelType.NONE:
            return  # OK: 現在、どのモデルも動作していない

        # 禁止パターン（双方向）
        forbidden_pairs = [
            (ModelType.TIER1,   ModelType.TIER3),
            (ModelType.TIER3,   ModelType.TIER1),
            (ModelType.TIER1,   ModelType.WHISPER),
            (ModelType.WHISPER, ModelType.TIER1),
        ]

        for model1, model2 in forbidden_pairs:
            if self._current_model == model1 and model_type == model2:
                raise VRAMGuardError(
                    f"Cannot run {model_type.value} while {self._current_model.value} is active. "
                    f"These models share VRAM and cannot coexist."
                )

    def _unload_conflicting_models(self, model_type: ModelType) -> None:
        """
        起動するモデルと競合するOllamaモデルをアンロード。
        """
        # Whisper起動時は全OllamaモデルをVRAMから解放
        if model_type == ModelType.WHISPER:
            self._unload_ollama_all()

        # Tier1/Tier3 起動時は互換性チェック済みなので追加アンロード不要

    def _unload_ollama_all(self) -> None:
        """
        全Ollamaモデルを VRAM からアンロード。

        Ollama の keep_alive=0 を指定することで、モデルをメモリから即時解放する。
        空モデル名では動作しないため、既知のモデル名を個別にアンロードする。

        Whisper起動前に呼び出す。
        """
        for model_type, model_name in _OLLAMA_MODEL_NAMES.items():
            try:
                # keep_alive=0: 推論後にモデルを即時アンロード
                # prompt="" で最小コストで呼び出してアンロードをトリガー
                ollama.generate(
                    model=model_name,
                    prompt="",
                    keep_alive=0,   # 0 = 推論後即時 VRAM 解放
                    options={"num_predict": 1},
                )
            except Exception:
                # モデルが未ロードの場合など → 無視してOK
                pass

    def get_current_model(self) -> ModelType:
        """現在実行中のモデルタイプを取得"""
        return self._current_model

    def get_lock_duration(self) -> Optional[float]:
        """ロック保有時間を取得（秒）"""
        if self._lock_time is None:
            return None
        return time.time() - self._lock_time

    def is_locked(self) -> bool:
        """ロックが取得されているかを確認"""
        return self._current_model != ModelType.NONE

    def __enter__(self) -> "VRAMGuard":
        """Context manager support"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support - cleanup on exit"""
        if self.is_locked():
            try:
                self.release_lock(self._current_model)
            except VRAMGuardError:
                pass


# グローバルVRAMガードシングルトン
_global_vram_guard: Optional[VRAMGuard] = None


def get_global_vram_guard() -> VRAMGuard:
    """
    グローバルVRAMガードシングルトンを取得。

    プロセス内で1インスタンスのみ保持し、全コンポーネントで共有する。
    """
    global _global_vram_guard
    if _global_vram_guard is None:
        _global_vram_guard = VRAMGuard()
    return _global_vram_guard
