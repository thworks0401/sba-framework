"""
Step1: 知識ギャップ検出エンジン

設計根拠（自律学習ループ設定書 §3）:
  - Self-EvaluationのSubSkillスコアを読み込み
  - 最低スコアのSubSkillを選定
  - 弱点フラグ（0.6以下）・補完学習優先度キュー・クールダウン管理
  - 学習クエリをTier1で生成
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..storage.knowledge_store import KnowledgeStore
from ..inference.tier1 import Tier1Engine


class GapDetectionError(Exception):
    """ギャップ検出エラー"""


@dataclass
class KnowledgeGapResult:
    """知識ギャップ検出結果"""
    target_subskill: str  # 対象SubSkill
    current_score: float  # 現在のスコア（0.0～1.0）
    gap_severity: str  # "critical" / "high" / "medium" / "low"
    gap_description: str  # ギャップの説明
    suggested_query: str  # 学習クエリの提案
    priority_reasons: List[str] = None  # 優先度を高める理由


class GapDetector:
    """
    BrainのSubSkillごとの知識密度スコアから、
    最も補完が必要なSubSkillを検出・優先度付けするエンジン。
    """

    WEAK_THRESHOLD = 0.6  # 0.6以下で弱点フラグ
    CRITICAL_THRESHOLD = 0.3  # 0.3以下で critical 判定
    COOLDOWN_HOURS = 24  # 直近学習のクールダウン時間
    PRIORITY_QUEUE_SIZE = 5  # 優先度キューサイズ

    def __init__(
        self,
        brain_name: str,
        knowledge_store: Optional[KnowledgeStore] = None,
        tier1_engine: Optional[Tier1Engine] = None,
    ) -> None:
        """
        Initialize GapDetector.

        Args:
            brain_name: Brain名
            knowledge_store: KnowledgeStore インスタンス
            tier1_engine: Tier1エンジン（クエリ生成用）
        """
        self.brain_name = brain_name
        self.knowledge_store = knowledge_store
        self.tier1_engine = tier1_engine or Tier1Engine()
        self.learning_history: Dict[str, datetime] = {}  # SubSkill → 最後の学習日時

    def load_self_evaluation(
        self, self_eval_path: Path
    ) -> Dict[str, float]:
        """
        self_eval.json から SubSkillon スコアを読み込む。

        Args:
            self_eval_path: self_eval.json のパス

        Returns:
            {subskill_id: score (0.0～1.0)} の dict
        """
        if not self_eval_path.exists():
            return {}

        try:
            with open(self_eval_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Pydantic v2 SelfEval model: subskills: {subskill_id: {density, weak, priority}}
            subskills = data.get("subskills", {})
            if subskills:
                # Extract density from SubSkillScore objects
                return {skill_id: score.get("density", 0.0) if isinstance(score, dict) else score 
                        for skill_id, score in subskills.items()}
            
            # Legacy fallback: scores: {subskill_id: score}
            return data.get("scores", {})
        except Exception:
            return {}

    def _calculate_gap_severity(self, score: float) -> str:
        """スコアからギャップ深刻度を判定"""
        if score <= self.CRITICAL_THRESHOLD:
            return "critical"
        elif score <= 0.5:
            return "high"
        elif score <= 0.75:
            return "medium"
        else:
            return "low"

    def _is_in_cooldown(self, subskill_id: str) -> bool:
        """SubSkillがクールダウン中かを判定"""
        if subskill_id not in self.learning_history:
            return False
        last_learning = self.learning_history[subskill_id]
        return datetime.now() < last_learning + timedelta(hours=self.COOLDOWN_HOURS)

    async def detect_gap(
        self,
        self_eval_path: Path,
        subskill_manifest: dict,
    ) -> KnowledgeGapResult:
        """
        最優先の知識ギャップを検出。

        Args:
            self_eval_path: self_eval.json のパス
            subskill_manifest: SubSkill定義（説明取得用）

        Returns:
            KnowledgeGapResult オブジェクト
        """
        # スコアを読み込み
        scores = self.load_self_evaluation(self_eval_path)
        
        # Empty scores: Brainが初期化直後の場合、manifestから最初のSubSkillを選択
        if not scores:
            subskill_list = subskill_manifest.get("subskills", [])
            if subskill_list:
                default_subskill = subskill_list[0]
                return KnowledgeGapResult(
                    target_subskill=default_subskill.get("id", "learning"),
                    current_score=0.0,
                    gap_severity="critical",
                    gap_description=f"新規 Brain: {default_subskill.get('display_name', 'Learning')} から開始",
                    suggested_query=f"{default_subskill.get('description', 'learning')} の基礎",
                    priority_reasons=["Blank Brain - High priority learning required"]
                )
            else:
                # No manifest either, use fallback
                return KnowledgeGapResult(
                    target_subskill="learning",
                    current_score=0.0,
                    gap_severity="critical",
                    gap_description="新規 Brain: 初期学習フェーズ",
                    suggested_query="基本的な概念と理論",
                    priority_reasons=["Blank Brain - learning from scratch"]
                )

        # SubSkillをスコアでソート（昇順 = 弱い順）
        sorted_subskills = sorted(scores.items(), key=lambda x: x[1])

        # クールダウン・弱点フラグを組み合わせて優先度を計算
        candidates = []
        for subskill_id, score in sorted_subskills:
            in_cooldown = self._is_in_cooldown(subskill_id)
            is_weak = score <= self.WEAK_THRESHOLD
            severity = self._calculate_gap_severity(score)
            # スコアが低い、かつ弱点、かつクールダウン外が最優先
            priority_score = (
                score  # スコアが低いほど高優先
                + (100 if in_cooldown else 0)  # クールダウン中は遠ざける
                - (50 if is_weak else 0)  # 弱点フラグは優先度UP
            )
            candidates.append({
                "subskill_id": subskill_id,
                "score": score,
                "priority_score": priority_score,
                "severity": severity,
                "in_cooldown": in_cooldown,
                "is_weak": is_weak,
            })

        # 優先度でソート
        candidates.sort(key=lambda x: x["priority_score"])
        best_candidate = candidates[0]

        subskill_id = best_candidate["subskill_id"]
        score = best_candidate["score"]
        severity = best_candidate["severity"]

        # SubSkill説明を取得
        subskill_desc = self._get_subskill_description(
            subskill_id, subskill_manifest
        )

        # Tier1で学習クエリを生成
        query = await self._generate_learning_query(subskill_id, subskill_desc, score)

        priority_reasons = []
        if best_candidate["is_weak"]:
            priority_reasons.append("弱点フラグが立っている（スコア≤0.6）")
        if not best_candidate["in_cooldown"]:
            priority_reasons.append("クールダウン期間外")
        if best_candidate["severity"] in ["critical", "high"]:
            priority_reasons.append(f"ギャップ深刻度: {best_candidate['severity']}")

        # 学習履歴を更新
        self.learning_history[subskill_id] = datetime.now()

        # シンプルな学習クエリを生成（Tier1での複雑な処理は避ける）
        simple_query = self._generate_simple_query(subskill_id, subskill_desc, score)

        priority_reasons = []
        if best_candidate["is_weak"]:
            priority_reasons.append("弱点フラグが立っている（スコア≤0.6）")
        if not best_candidate["in_cooldown"]:
            priority_reasons.append("クールダウン期間外")
        if best_candidate["severity"] in ["critical", "high"]:
            priority_reasons.append(f"ギャップ深刻度: {best_candidate['severity']}")

        return KnowledgeGapResult(
            target_subskill=subskill_id,
            current_score=score,
            gap_severity=severity,
            gap_description=subskill_desc or subskill_id,
            suggested_query=simple_query,
            priority_reasons=priority_reasons,
        )

    def _get_subskill_description(
        self, subskill_id: str, manifest: dict
    ) -> str:
        """SubSkill定義から説明を取得"""
        subskills = manifest.get("subskills", [])
        for sk in subskills:
            if sk.get("id") == subskill_id:
                return sk.get("description", "")
        return ""

    def _generate_simple_query(
        self,
        subskill_id: str,
        subskill_description: str,
        current_score: float,
    ) -> str:
        """
        Simple learning query generation without Tier1 (avoids hang).

        Args:
            subskill_id: SubSkill ID
            subskill_description: SubSkill説明
            current_score: 現在のスコア

        Returns:
            学習クエリ文字列
        """
        # Build a simple, actionable learning query
        base_query = f"{self.brain_name} {subskill_id}"
        
        if current_score < 0.3:
            # Critical gap: focus on fundamentals
            if subskill_description:
                return f"{subskill_description} 基礎 入門".strip()
            return f"{base_query} 基礎"
        elif current_score < 0.6:
            # Significant gap: focus on key concepts
            if subskill_description:
                return f"{subskill_description} 重要 概念".strip()
            return f"{base_query} 概念"
        else:
            # Minor gap: advanced topics
            if subskill_description:
                return f"{subskill_description} 応用 実装".strip()
            return f"{base_query} 応用"

    def get_priority_queue(
        self,
        self_eval_path: Path,
        max_items: int = None,
    ) -> List[Tuple[str, float]]:
        """
        優先度キューを取得（複数SubSkillの優先順位）。

        Args:
            self_eval_path: self_eval.json のパス
            max_items: 最大件数（デフォルト: PRIORITY_QUEUE_SIZE）

        Returns:
            [(subskill_id, score)] のリスト（スコア昇順）
        """
        max_items = max_items or self.PRIORITY_QUEUE_SIZE
        scores = self.load_self_evaluation(self_eval_path)

        # スコア昇順に並べる
        sorted_items = sorted(scores.items(), key=lambda x: x[1])
        return sorted_items[:max_items]

    def mark_learning_completed(self, subskill_id: str) -> None:
        """学習完了を記録"""
        self.learning_history[subskill_id] = datetime.now()
