"""
EngineRouter: タスク種別・VRAM状態・Quota状態に基づく推論エンジン振り分け

設計根拠（推論エンジン・VRAM運用設定書 §4.1）:

  Tier振り分けロジック:
    Tier3 (Qwen2.5-Coder): task_type="code" のコード生成・検証タスク
    Tier2 (Gemini Flash):  長文テキスト（token > 8000）かつ Quota 残量あり
                           Tier1 待機時間 > 10秒 のフォールバック
    Tier1 (Phi-4:14B):     上記以外のデフォルト。最も汎用・高品質な推論

  フォールバックチェーン:
    Tier3 エラー   → Tier1 にフォールバック
    Tier2 Quota切れ → Tier1 にフォールバック
    Tier1 タイムアウト → エラー返却（Tier2 への再フォールバックは行わない）

  VRAM排他:
    各エンジン呼び出しを vram_guard.acquire_vram() でラップ。
    Tier2 は外部API なので VRAM ロック不要。
"""

from __future__ import annotations

import asyncio
from typing import Optional, Literal

from loguru import logger

from .tier1 import Tier1Engine, InferenceResult
from .tier2 import Tier2Engine
from .tier3 import Tier3Engine
from .vram_guard import acquire_vram


# タスク種別の型定義
TaskType = Literal["code", "text", "summary", "eval", "default"]

# Tier2 に回す入力トークンの閾値（文字数 ÷ 4 がおおよそのトークン数）
TIER2_TOKEN_THRESHOLD = 8000

# Tier1 の待機時間上限（秒）。超えたら Tier2 フォールバック候補
TIER1_WAIT_THRESHOLD_S = 10.0


class EngineRouter:
    """
    推論エンジンの振り分けルーター。

    Tier1 / Tier2 / Tier3 を内部で管理し、
    タスク種別・トークン量・VRAM状態・Quota状態に応じて
    最適なエンジンに推論リクエストを転送する。

    Tier2 はAPIキー必須のため、キーが取得できない場合は
    Tier2 なしで動作する（Tier1 + Tier3 のみ）。
    """

    def __init__(
        self,
        tier1: Optional[Tier1Engine] = None,
        tier2: Optional[Tier2Engine] = None,
        tier3: Optional[Tier3Engine] = None,
    ) -> None:
        """
        Args:
            tier1: Tier1Engine インスタンス（省略時: 自動生成）
            tier2: Tier2Engine インスタンス（省略時: 自動生成、失敗時は None）
            tier3: Tier3Engine インスタンス（省略時: 自動生成）
        """
        self.tier1: Tier1Engine = tier1 or Tier1Engine()
        self.tier3: Tier3Engine = tier3 or Tier3Engine()

        # Tier2 は APIキーが必要。取得できない場合は None で動作継続
        if tier2 is not None:
            self.tier2: Optional[Tier2Engine] = tier2
        else:
            try:
                self.tier2 = Tier2Engine()
            except Exception as e:
                logger.warning(
                    f"EngineRouter: Tier2 初期化失敗（APIキーなし？）。"
                    f"Tier1+Tier3 のみで動作します。error={e}"
                )
                self.tier2 = None

    # ======================================================================
    # ルーティング判定
    # ======================================================================

    def _select_engine(
        self,
        prompt: str,
        task_type: TaskType,
        force_tier: Optional[Literal[1, 2, 3]] = None,
    ) -> Literal["tier1", "tier2", "tier3"]:
        """
        タスク種別・プロンプト長・Quota状態からエンジンを選択。

        Args:
            prompt:     入力プロンプト文字列
            task_type:  タスク種別
            force_tier: 強制指定（テスト・上書き用）

        Returns:
            "tier1" | "tier2" | "tier3"
        """
        if force_tier == 1:
            return "tier1"
        if force_tier == 2:
            return "tier2"
        if force_tier == 3:
            return "tier3"

        # --- コードタスク → Tier3 ---
        if task_type == "code":
            logger.debug("EngineRouter: code task → Tier3")
            return "tier3"

        # --- 長文テキスト → Tier2（Quota あり）---
        estimated_tokens = len(prompt) // 4
        if (
            task_type in ("summary", "text")
            and estimated_tokens > TIER2_TOKEN_THRESHOLD
            and self.tier2 is not None
        ):
            # Quota 残量確認
            quota = self.tier2.get_remaining_quota()
            if quota["status"] == "active":
                logger.debug(
                    f"EngineRouter: long text ({estimated_tokens} tokens) → Tier2"
                )
                return "tier2"
            else:
                logger.info(
                    f"EngineRouter: Tier2 quota={quota['status']}, "
                    f"falling back to Tier1"
                )

        # --- Tier1 待機時間チェック ---
        # 直前の待機時間が閾値超過 かつ Tier2 が使えるなら Tier2 へ
        if (
            self.tier1.latest_wait_time > TIER1_WAIT_THRESHOLD_S
            and self.tier2 is not None
        ):
            quota = self.tier2.get_remaining_quota()
            if quota["status"] == "active":
                logger.info(
                    f"EngineRouter: Tier1 wait {self.tier1.latest_wait_time:.1f}s > "
                    f"{TIER1_WAIT_THRESHOLD_S}s → Tier2 fallback"
                )
                return "tier2"

        # --- デフォルト: Tier1 ---
        return "tier1"

    # ======================================================================
    # 推論実行
    # ======================================================================

    async def infer(
        self,
        prompt: str,
        task_type: TaskType = "default",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        force_tier: Optional[Literal[1, 2, 3]] = None,
    ) -> InferenceResult:
        """
        推論を実行。エンジン選択 → VRAM排他 → 推論 → フォールバック の順で処理。

        Args:
            prompt:     入力プロンプト
            task_type:  タスク種別 ("code" / "text" / "summary" / "eval" / "default")
            max_tokens: 最大生成トークン数
            temperature: サンプリング温度
            timeout_s:  タイムアウト秒数
            force_tier: エンジン強制指定（1/2/3）

        Returns:
            InferenceResult
        """
        selected = self._select_engine(prompt, task_type, force_tier)

        if selected == "tier3":
            return await self._run_tier3(
                prompt, max_tokens, temperature, timeout_s
            )
        elif selected == "tier2":
            return await self._run_tier2(
                prompt, max_tokens, temperature, timeout_s
            )
        else:
            return await self._run_tier1(
                prompt, max_tokens, temperature, timeout_s
            )

    async def _run_tier1(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> InferenceResult:
        """Tier1 (Phi-4) で推論。VRAM ロック取得してから呼び出す。"""
        try:
            with acquire_vram("tier1"):
                result = await self.tier1.infer(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                )
            return result
        except TimeoutError as e:
            logger.error(f"EngineRouter: Tier1 VRAM lock timeout: {e}")
            return InferenceResult(
                text="", latency_ms=0.0,
                error=f"Tier1 VRAM lock timeout: {e}"
            )

    async def _run_tier2(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> InferenceResult:
        """Tier2 (Gemini) で推論。外部APIなので VRAM ロック不要。"""
        if self.tier2 is None:
            logger.warning("EngineRouter: Tier2 unavailable, falling back to Tier1")
            return await self._run_tier1(prompt, max_tokens, temperature, timeout_s)

        result = await self.tier2.infer(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
        )

        # Quota 切れ・エラー時は Tier1 にフォールバック
        if result.error:
            logger.warning(
                f"EngineRouter: Tier2 error, falling back to Tier1. error={result.error}"
            )
            return await self._run_tier1(prompt, max_tokens, temperature, timeout_s)

        return result

    async def _run_tier3(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> InferenceResult:
        """Tier3 (Qwen2.5-Coder) で推論。VRAM ロック取得してから呼び出す。"""
        try:
            with acquire_vram("tier3"):
                result = await self.tier3.infer(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout_s=timeout_s,
                )
        except TimeoutError as e:
            logger.error(f"EngineRouter: Tier3 VRAM lock timeout: {e}")
            return InferenceResult(
                text="", latency_ms=0.0,
                error=f"Tier3 VRAM lock timeout: {e}"
            )

        # Tier3 エラー時は Tier1 にフォールバック
        if result.error:
            logger.warning(
                f"EngineRouter: Tier3 error, falling back to Tier1. error={result.error}"
            )
            return await self._run_tier1(prompt, max_tokens, temperature, timeout_s)

        return result

    # ======================================================================
    # 状態取得
    # ======================================================================

    def get_status(self) -> dict:
        """
        エンジン状態サマリを返す。

        Returns:
            {
                "tier1_wait_time":  float,  # 直前の Tier1 待機時間（秒）
                "tier2_available":  bool,   # Tier2 が使用可能か
                "tier2_quota":      dict,   # Tier2 Quota 状態
                "tier3_available":  bool,   # Tier3 が使用可能か
            }
        """
        tier2_quota: dict = {"status": "unavailable"}
        if self.tier2:
            try:
                tier2_quota = self.tier2.get_remaining_quota()
            except Exception:
                pass

        return {
            "tier1_wait_time": self.tier1.latest_wait_time,
            "tier2_available": self.tier2 is not None,
            "tier2_quota":     tier2_quota,
            "tier3_available": self.tier3 is not None,
        }
