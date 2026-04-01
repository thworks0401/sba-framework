"""
Step5: 知識統合・矛盾検出・再構造化エンジン

設計根拠（自律学習ループ設定書 §7）:
  - 新規ノードと既存ノードの矛盾検出
  - 信頼スコア比較で主系を決定
  - 旧ノードを deprecated 化
  - 判断不能時は requires_human_review フラグ
"""

from __future__ import annotations

import asyncio
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime

from ..storage.knowledge_store import KnowledgeStore
from ..storage.graph_store import KuzuGraphStore
from ..inference.tier1 import Tier1Engine


@dataclass
class ContradictionResult:
    """矛盾検出結果"""
    existing_node_id: str
    new_node_id: str
    contradiction_score: float  # 0.0～1.0（1.0が最高矛盾度）
    primary_node_id: str  # スコア勝者
    requires_human_review: bool = False
    reason: str = ""


class KnowledgeIntegrator:
    """
    新規知識と既存Knowledge Baseの統合・矛盾検出。
    """

    CONTRADICTION_THRESHOLD = 0.7  # 矛盾判定閾値
    TRUST_SCORE_MARGIN = 0.15  # スコア同点判定マージン

    def __init__(
        self,
        knowledge_store: Optional[KnowledgeStore] = None,
        graph_store: Optional[KuzuGraphStore] = None,
        tier1_engine: Optional[Tier1Engine] = None,
    ) -> None:
        """
        Initialize KnowledgeIntegrator.

        Args:
            knowledge_store: KnowledgeStore
            graph_store: KuzuGraphStore
            tier1_engine: Tier1エンジン（矛盾判定用）
        """
        self.knowledge_store = knowledge_store
        self.graph_store = graph_store
        self.tier1_engine = tier1_engine or Tier1Engine()

    async def detect_contradictions(
        self,
        new_chunks: List[Dict],
    ) -> List[ContradictionResult]:
        """
        新規チャンクと既存ノードの矛盾を検出。

        Args:
            new_chunks: 新規Knowledge Chunk のリスト
                [{ id, text, trust_score, subskill, ... }]

        Returns:
            ContradictionResult のリスト
        """
        contradictions = []

        for new_chunk in new_chunks:
            new_id = new_chunk.get("id")
            new_text = new_chunk.get("text", "")
            new_trust = new_chunk.get("trust_score", 0.5)

            # 既存ノードから高類似度のものを検索
            try:
                similar_nodes = await self._find_similar_existing_nodes(
                    new_text, threshold=0.85
                )
            except Exception:
                similar_nodes = []

            for existing_node in similar_nodes:
                existing_id = existing_node.get("id")
                existing_trust = existing_node.get("trust_score", 0.5)

                # 矛盾度スコアを計算
                contradiction_score = await self._compute_contradiction_score(
                    new_text, existing_node.get("text", "")
                )

                if contradiction_score >= self.CONTRADICTION_THRESHOLD:
                    # 主系を決定
                    if abs(new_trust - existing_trust) < self.TRUST_SCORE_MARGIN:
                        # スコア同点 → 人間判定に委譲
                        primary_id = new_id  # 仮決定
                        requires_review = True
                    else:
                        # スコアで勝者を決定
                        primary_id = new_id if new_trust > existing_trust else existing_id
                        requires_review = False

                    result = ContradictionResult(
                        existing_node_id=existing_id,
                        new_node_id=new_id,
                        contradiction_score=contradiction_score,
                        primary_node_id=primary_id,
                        requires_human_review=requires_review,
                        reason=f"矛盾スコア: {contradiction_score:.2f}",
                    )

                    contradictions.append(result)

        return contradictions

    async def _find_similar_existing_nodes(
        self,
        text: str,
        threshold: float = 0.85,
    ) -> List[Dict]:
        """
        既存ノードから高類似の候補を検索。

        Args:
            text: 検索テキスト
            threshold: 類似度閾値

        Returns:
            候補ノードのリスト
        """
        if not self.knowledge_store:
            return []

        try:
            similar = await self.knowledge_store.search_similar(
                text, limit=5
            )
            return [item for item in similar if item.get("similarity", 0) >= threshold]
        except Exception:
            return []

    async def _compute_contradiction_score(
        self,
        new_text: str,
        existing_text: str,
    ) -> float:
        """
        2つのテキストの矛盾度を計算（Tier1を使用）。

        Args:
            new_text: 新規テキスト
            existing_text: 既存テキスト

        Returns:
            矛盾スコア（0.0～1.0）
        """
        prompt = f"""以下の2つのテキストが矛盾しているか判定せよ。
矛盾スコアを 0.0～1.0 で数値化して返せ。

【テキスト1（既存）】
{existing_text[:500]}

【テキスト2（新規）】
{new_text[:500]}

矛盾点を検出し、スコア（0.0=完全一致、1.0=完全矛盾）のみを返せ。
出力: 数値のみ（小数点第2位まで）"""

        try:
            result = await self.tier1_engine.infer(
                prompt=prompt,
                temperature=0.2,
                max_tokens=20,
                timeout_s=10.0,
            )

            if result.error:
                return 0.3  # デフォルト：やや矛盾

            # テキストから数値を抽出
            import re
            match = re.search(r"0\.\d+", result.text)
            if match:
                return float(match.group())
            return 0.3

        except Exception:
            return 0.3

    async def handle_contradictions(
        self,
        contradictions: List[ContradictionResult],
    ) -> Dict:
        """
        矛盾を解決・グラフを再構築。

        Args:
            contradictions: 矛盾検出結果のリスト

        Returns:
            {deprecated_nodes, updated_edges, human_review_items}
        """
        deprecated_nodes = []
        updated_edges = []
        human_review_items = []

        for contradiction in contradictions:
            # 敗者を deprecated 化
            loser_id = (
                contradiction.existing_node_id
                if contradiction.primary_node_id == contradiction.new_node_id
                else contradiction.new_node_id
            )

            deprecated_nodes.append({
                "node_id": loser_id,
                "reason": contradiction.reason,
                "marked_at": datetime.now().isoformat(),
            })

            # グラフエッジを追加（矛盾関係を記録）
            if self.graph_store:
                try:
                    await self.graph_store.add_contradiction_edge(
                        contradiction.existing_node_id,
                        contradiction.new_node_id,
                        contradiction.contradiction_score,
                    )
                except Exception:
                    pass

            # 人間確認が必要な場合を記録
            if contradiction.requires_human_review:
                human_review_items.append({
                    "existing_id": contradiction.existing_node_id,
                    "new_id": contradiction.new_node_id,
                    "contradiction_score": contradiction.contradiction_score,
                    "status": "pending_review",
                })

        return {
            "deprecated_nodes": deprecated_nodes,
            "updated_edges": updated_edges,
            "human_review_items": human_review_items,
        }

    async def reconcile_knowledge_base(
        self,
        new_chunks: List[Dict],
        brain_id: str,
    ) -> Dict:
        """
        新規チャンクをKnowledge Baseに統合。

        Args:
            new_chunks: 新規チャンク
            brain_id: Brain ID

        Returns:
            統合結果の サマリ
        """
        # 矛盾検出
        contradictions = await self.detect_contradictions(new_chunks)

        # 矛盾処理
        resolution = await self.handle_contradictions(contradictions)

        # 新規チャンク格納
        stored_count = 0
        try:
            for chunk in new_chunks:
                if self.knowledge_store:
                    await self.knowledge_store.store_chunk(chunk, brain_id)
                    stored_count += 1
        except Exception:
            pass

        return {
            "stored_chunks": stored_count,
            "contradictions_found": len(contradictions),
            "contradictions_resolved": len(resolution["deprecated_nodes"]),
            "human_review_items": len(resolution["human_review_items"]),
            "details": resolution,
        }
