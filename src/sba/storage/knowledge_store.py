"""
KnowledgeStore — Qdrant + Kuzu + SQLite 統合インターフェース

設計根拠（補足設計書 §2.5）:
  - 3ストア（Vector + Graph + Relational）の一括操作
  - store_chunk: アトミックな書き込み
  - query_hybrid: ベクトル + グラフ統合検索
  - mark_deprecated: チャンク非推奨化

【修正履歴】
  mark_deprecated(): 旧実装は get_timeline_by_subskill("") で全件スキャンしていた。
  Brain 成長後は数千〜数万エントリになる可能性があり、パフォーマンス問題になるため、
  get_timeline_by_kg_node(chunk_id) による直接検索に変更。
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional
from pathlib import Path

from .vector_store import QdrantVectorStore
from .graph_store import KuzuGraphStore
from .timeline_db import TimelineRepository


class KnowledgeStoreError(Exception):
    """KnowledgeStore 操作に関する例外"""


class KnowledgeStore:
    """
    Knowledge Base 統合インターフェース。

    Qdrant（ベクトル検索）+ Kuzu（グラフ構造）+ SQLite（タイムライン）
    を統合的に操作。

    チャンク追加時は 3つのストア全てにアトミックに書き込む設計。
    """

    def __init__(
        self,
        brain_package_path: str,
        brain_id: str,
    ) -> None:
        """
        Initialize Knowledge Base.

        Args:
            brain_package_path: Brain Package ディレクトリパス
            brain_id: Brain UUID
        """
        self.brain_package_path = Path(brain_package_path)
        self.brain_id = brain_id

        vector_index_path     = self.brain_package_path / "vector_index"
        knowledge_graph_path  = self.brain_package_path / "knowledge_graph"
        learning_timeline_path = self.brain_package_path / "learning_timeline.db"

        self.vector_store  = QdrantVectorStore(str(vector_index_path), brain_id)
        self.graph_store   = KuzuGraphStore(str(knowledge_graph_path), brain_id)
        self.timeline_repo = TimelineRepository(str(learning_timeline_path))

    # ======================================================================
    # 統合操作: アトミックなチャンク追加
    # ======================================================================

    def store_chunk(
        self,
        text: str,
        primary_subskill: str,
        source_type: str,
        source_url: str = "",
        trust_score: float = 0.5,
        summary: str = "",
        secondary_subskills: Optional[list] = None,
    ) -> dict:
        """
        チャンクを 3つのストア全てに統合的に保存。

        コンテンツハッシュ + ベクトル類似度の二段階重複チェック付き。

        Returns:
            {
                "chunk_id": str | None,
                "qdrant_ids": list[str],
                "timeline_id": str,
                "duplicate_detected": bool,
                "reason": str,  # 重複時のみ
            }
        """
        # --- Step 1: コンテンツハッシュで完全重複チェック ---
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        existing = self.timeline_repo.check_duplicate_by_hash(content_hash)

        if existing:
            return {
                "chunk_id": None,
                "qdrant_ids": [],
                "duplicate_detected": True,
                "reason": f"Content hash match: {existing}",
            }

        # --- Step 2: ベクトル類似度で重複チェック（コサイン類似度 > 0.92）---
        duplicate_check = self.vector_store.duplicate_check(text, primary_subskill)

        if duplicate_check:
            return {
                "chunk_id": None,
                "qdrant_ids": [],
                "duplicate_detected": True,
                "reason": f"Vector similarity > 0.92: {duplicate_check['chunk_id']}",
            }

        # --- Step 3: 重複なし → 3ストアにアトミック書き込み ---

        # 3-1. Kuzu にノード追加
        chunk_id = self.graph_store.add_knowledge_chunk(
            text=text,
            trust_score=trust_score,
            primary_subskill=primary_subskill,
            source_type=source_type,
            source_url=source_url,
            summary=summary,
        )

        # 3-2. Qdrant にベクトル追加
        qdrant_ids = self.vector_store.add_chunks(
            chunks=[{"id": chunk_id, "text": text, "trust_score": trust_score}],
            subskill_id=primary_subskill,
            source_type=source_type,
            source_url=source_url,
        )

        # 3-3. Kuzu に Qdrant ID を記録
        if qdrant_ids:
            self.graph_store.update_knowledge_chunk(chunk_id, qdrant_id=qdrant_ids[0])

        # 3-4. Secondary SubSkill リンク
        if secondary_subskills:
            for sec_skill in secondary_subskills:
                self.graph_store.add_related_to_secondary(
                    chunk_id=chunk_id,
                    subskill_id=sec_skill,
                    relevance=0.5,
                )

        # 3-5. Timeline に記録
        timeline_id = self.timeline_repo.insert_timeline(
            brain_id=self.brain_id,
            source_type=source_type,
            content_hash=content_hash,
            subskill=primary_subskill,
            url_or_path=source_url,
            qdrant_ids=qdrant_ids,
            kg_node_ids=[chunk_id],
            freshness=1.0,
        )

        return {
            "chunk_id": chunk_id,
            "qdrant_ids": qdrant_ids,
            "timeline_id": timeline_id,
            "duplicate_detected": False,
        }

    # ======================================================================
    # ハイブリッド検索
    # ======================================================================

    def query_hybrid(
        self,
        query_text: str,
        subskill_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        ハイブリッド検索: ベクトル類似度 + グラフ関連性。

        Returns:
            [
                {
                    "chunk_id": str,
                    "text": str,
                    "score": float,
                    "trust_score": float,
                    "source_type": str,
                    "related_subskills": [str, ...],
                },
                ...
            ]
        """
        vector_results = self.vector_store.search(
            query_text=query_text,
            subskill_id=subskill_id,
            limit=limit,
        )

        results = []
        for v_result in vector_results:
            chunk_id = v_result["chunk_id"]
            related  = self.graph_store.get_related_chunks(chunk_id)

            results.append({
                "chunk_id":          chunk_id,
                "qdrant_id":         v_result["qdrant_id"],
                "text":              v_result["text"],
                "score":             v_result["score"],
                "trust_score":       v_result["trust_score"],
                "source_type":       v_result["source_type"],
                "source_url":        v_result["source_url"],
                "primary_subskill":  related.get("primary_subskill"),
                "related_subskills": related.get("secondary_subskills", []),
            })

        return results

    # ======================================================================
    # ノード管理
    # ======================================================================

    def ensure_subskill_node(self, subskill_id: str, display_name: str) -> None:
        """SubSkill ノード確保（存在しなければ作成）"""
        self.graph_store.add_subskill_node(subskill_id, display_name)

    def update_subskill_density(self, subskill_id: str, density_score: float) -> None:
        """SubSkill density_score 更新"""
        self.graph_store.update_subskill_density(subskill_id, density_score)

    # ======================================================================
    # チャンク管理
    # ======================================================================

    def mark_deprecated(self, chunk_id: str, reason: str = "") -> None:
        """
        チャンクを deprecated マーク。

        Kuzu 上のフラグ + Timeline の freshness を 0.0 に更新。

        【修正】旧実装: get_timeline_by_subskill("") で全件スキャン
               新実装: get_timeline_by_kg_node(chunk_id) で直接検索
        """
        # Kuzu のフラグを立てる
        self.graph_store.mark_deprecated(chunk_id)

        # Timeline の freshness を 0.0 に更新（outdated フラグも立つ）
        entry = self.timeline_repo.get_timeline_by_kg_node(chunk_id)
        if entry:
            self.timeline_repo.update_freshness(entry["id"], 0.0)

    def mark_requires_review(self, chunk_id: str, reason: str = "") -> None:
        """チャンクに human_review フラグ付与"""
        self.graph_store.mark_requires_review(chunk_id)

    def get_chunk(self, chunk_id: str) -> dict | None:
        """チャンク詳細取得"""
        chunk = self.graph_store.get_knowledge_chunk(chunk_id)
        if not chunk:
            return None

        related = self.graph_store.get_related_chunks(chunk_id)
        chunk.update({"related_subskills": related.get("secondary_subskills", [])})
        return chunk

    def get_chunks_by_subskill(self, subskill_id: str) -> list[dict]:
        """SubSkill 別チャンク一覧"""
        return self.graph_store.get_chunks_by_subskill(subskill_id)

    # ======================================================================
    # 矛盾検出・知識統合
    # ======================================================================

    def detect_contradiction(
        self,
        new_chunk_id: str,
        trust_score_threshold: float = 0.7,
    ) -> Optional[str]:
        """
        新規チャンクと既存チャンク間の矛盾検出。

        TODO (Phase 4 タスク 4-8):
            現実装は未実装スタブ（return None）。
            knowledge_integrator.py 実装時に Tier1 LLM による
            矛盾判定ロジックをここに追加する。
            補足設計書 A-4: 矛盾検出 → CONTRADICTS エッジ付与。

        Returns:
            矛盾が見つかった既存 chunk_id、見つからなければ None
        """
        return None  # TODO: Phase 4 タスク 4-8 で実装

    def add_contradiction_edge(self, chunk_a_id: str, chunk_b_id: str) -> None:
        """矛盾ノード間にエッジ付与"""
        self.graph_store.add_contradicts(chunk_a_id, chunk_b_id)

    def mark_knowledge_update(self, old_chunk_id: str, new_chunk_id: str) -> None:
        """旧チャンクを新チャンクが上書き: UPDATES エッジ"""
        self.graph_store.add_updates(old_chunk_id, new_chunk_id)
        self.mark_deprecated(old_chunk_id, reason="Replaced by newer knowledge")

    # ======================================================================
    # 統計・ダッシュボード
    # ======================================================================

    def get_knowledge_base_stats(self) -> dict:
        """Knowledge Base 統計"""
        return {
            "vector_store": self.vector_store.get_collection_stats(),
            "graph_store":  self.graph_store.get_graph_stats(),
            "timeline":     self.timeline_repo.get_stats(self.brain_id),
        }

    def get_subskill_overview(self) -> list[dict]:
        """SubSkill ごとの概要"""
        subskills = self.graph_store.get_all_subskill_nodes()

        for skill in subskills:
            chunks = self.get_chunks_by_subskill(skill["id"])
            skill.update({
                "chunk_count":      len(chunks),
                "avg_trust_score":  sum(c["trust_score"] for c in chunks) / max(len(chunks), 1),
            })

        return subskills
