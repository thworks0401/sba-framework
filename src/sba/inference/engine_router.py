"""
エンジンルーター（Tier自動選択ロジック）

設計根拠（推論エンジン・VRAM運用設定書 §3.1）:
  - 判定フロー:
    1. コード生成・検証かつTech系Brain → Tier3
    2. トークン数>8000またはTier1待機>10秒 → Tier2
    3. その他 → Tier1
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from dataclasses import dataclass

from .tier1 import Tier1Engine, InferenceResult as Tier1Result
from .tier2 import Tier2Engine, InferenceResult as Tier2Result
from .tier3 import Tier3Engine, InferenceResult as Tier3Result


class TaskType(Enum):
    """タスク分類"""
    CODE_GENERATION = "code_generation"  # コード生成
    CODE_REVIEW = "code_review"  # コードレビュー
    LONG_TEXT = "long_text"  # 大量テキスト処理（>8000トークン）
    SUMMARIZATION = "summarization"  # 要約
    REASONING = "reasoning"  # 通常推論（デフォルト）


class SelectedTier(Enum):
    """選択されたTier"""
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


@dataclass
class InferenceTask:
    """推論タスク定義"""
    type: TaskType
    prompt: str
    estimated_tokens: int
    is_tech_brain: bool = False
    max_output_tokens: int = 2048
    temperature: float = 0.7
    timeout_s: float = 30.0


@dataclass
class RoutingDecision:
    """ルーティング判定結果"""
    selected_tier: SelectedTier
    reason: str
    estimated_wait_time_s: float


class EngineRouter:
    """
    推論エンジン自動ルーター。

    タスク特性とシステム状態に基づいて、
    Tier1/Tier2/Tier3への自動振り分けを実行。
    """

    # ルーティング判定の閾値
    TOKEN_THRESHOLD_TIER2 = 8000  # この値を超えるとTier2候補
    TIER1_WAIT_THRESHOLD_S = 10.0  # この秒数を超えるとTier2候補
    TIER2_MIN_REMAINING_TOKENS = 100  # Tier2残量下限

    def __init__(
        self,
        tier1: Optional[Tier1Engine] = None,
        tier2: Optional[Tier2Engine] = None,
        tier3: Optional[Tier3Engine] = None,
    ) -> None:
        """
        Initialize Engine Router.

        Args:
            tier1: Tier1エンジンインスタンス（デフォルト: 新規作成）
            tier2: Tier2エンジンインスタンス（デフォルト: 新規作成）
            tier3: Tier3エンジンインスタンス（デフォルト: 新規作成）
        """
        self.tier1 = tier1 or Tier1Engine()
        self.tier2 = tier2 or Tier2Engine()
        self.tier3 = tier3 or Tier3Engine()

    def route(self, task: InferenceTask) -> RoutingDecision:
        """
        タスクを適切なTierにルーティング。

        ルーティングロジック:
        1. コード系かつTech系Brain → Tier3
        2. トークン>8000またはTier1待機>10秒 → Tier2検討
           - Tier2残量不足なら Tier1フォールバック
        3. その他 → Tier1

        Args:
            task: InferenceTask

        Returns:
            RoutingDecision: ルーティング判定
        """
        # 優先度1: コード系タスク + Tech系Brain → Tier3
        if self._is_code_task(task) and task.is_tech_brain:
            return RoutingDecision(
                selected_tier=SelectedTier.TIER3,
                reason="Code task on Tech Brain",
                estimated_wait_time_s=0.0,
            )

        # 優先度2: 大量テキスト or Tier1待機超過 → Tier2検討
        if self._should_use_tier2(task):
            # Tier2残量確認
            quota = self.tier2.get_remaining_quota()
            if quota["remaining_tokens"] is not None and quota["remaining_tokens"] > self.TIER2_MIN_REMAINING_TOKENS:
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER2,
                    reason=f"Long text ({task.estimated_tokens}t) or Tier1 wait excessive",
                    estimated_wait_time_s=quota["daily_used"] * 0.001,  # 概算
                )
            else:
                # Tier2 Quota不足 → Tier1フォールバック
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER1,
                    reason="Tier2 quota insufficient, fallback to Tier1",
                    estimated_wait_time_s=self.tier1.get_latest_wait_time(),
                )

        # 優先度3: デフォルト → Tier1
        return RoutingDecision(
            selected_tier=SelectedTier.TIER1,
            reason="Default routing",
            estimated_wait_time_s=self.tier1.get_latest_wait_time(),
        )

    def _is_code_task(self, task: InferenceTask) -> bool:
        """
        タスクがコード系かを判定。

        Args:
            task: InferenceTask

        Returns:
            True if code-related task
        """
        return task.type in [TaskType.CODE_GENERATION, TaskType.CODE_REVIEW]

    def _should_use_tier2(self, task: InferenceTask) -> bool:
        """
        Tier2使用の判定条件。

        Args:
            task: InferenceTask

        Returns:
            True if Tier2 should be considered
        """
        # トークン>8000
        if task.estimated_tokens > self.TOKEN_THRESHOLD_TIER2:
            return True

        # Tier1待機時間>10秒
        if self.tier1.get_latest_wait_time() > self.TIER1_WAIT_THRESHOLD_S:
            return True

        # 明示的にlong_text / summarization
        if task.type in [TaskType.LONG_TEXT, TaskType.SUMMARIZATION]:
            return True

        return False

    def get_tier_status(self) -> dict:
        """
        全Tierの状態を取得。

        Returns:
            {"tier1": {...}, "tier2": {...}, "tier3": {...}}
        """
        tier2_quota = self.tier2.get_remaining_quota()

        return {
            "tier1": {
                "latency_ms": self.tier1.get_current_latency() * 1000,
                "wait_time_s": self.tier1.get_latest_wait_time(),
                "available": True,  # Tier1は通常常に利用可能
            },
            "tier2": {
                "status": tier2_quota["status"],
                "remaining_tokens": tier2_quota["remaining_tokens"],
                "daily_used": tier2_quota["daily_used"],
            },
            "tier3": {
                "latency_ms": self.tier3.get_latest_latency() * 1000,
                "wait_time_s": self.tier3.get_latest_wait_time(),
                "available": True,
            },
        }

    async def infer(self, task: InferenceTask) -> Tier1Result | Tier2Result | Tier3Result:
        """
        タスクをルーティングして実行。

        Args:
            task: InferenceTask

        Returns:
            推論結果（InferenceResult）
        """
        decision = self.route(task)

        if decision.selected_tier == SelectedTier.TIER1:
            return await self.tier1.infer(
                task.prompt,
                temperature=task.temperature,
                max_tokens=task.max_output_tokens,
                timeout_s=task.timeout_s,
            )

        elif decision.selected_tier == SelectedTier.TIER2:
            return await self.tier2.infer(
                task.prompt,
                max_tokens=task.max_output_tokens,
                temperature=task.temperature,
                timeout_s=task.timeout_s,
            )

        elif decision.selected_tier == SelectedTier.TIER3:
            return await self.tier3.generate_code(
                task.prompt,
                temperature=task.temperature,
                max_tokens=task.max_output_tokens,
                timeout_s=task.timeout_s,
            )

        else:
            raise ValueError(f"Unknown tier: {decision.selected_tier}")
