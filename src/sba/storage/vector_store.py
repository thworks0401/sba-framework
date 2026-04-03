"""
Qdrant ベクトルストア実装

設計根拠（補足設計書 §1.2.1）:
  - Vector DB: Qdrant（ローカルモード）
  - Brain-scoped: brain_id ごとにコレクション分離
  - メタデータフィルタ: SubSkill 別の類似検索に対応
  - 重複検出: コサイン類似度 > 0.92

Brain Hot-Swap との相性:
  - Qdrant データはディレクトリベース（vector_index/ としてコピー可能）
  - Brain save/load 時にディレクトリごとバックアップ

【修正履歴】
  2026-04-03 (fix #1):
    __init__ で即時実行していた Embedder.get_instance() を廃止。
    遅延初期化を導入したが、キャッシュがあると monkeypatch 後の
    差し替えが無視されるため、キャッシュを持たず毎回
    Embedder.get_instance() に委論する方式に変更。
    本番時は Embedder 自身が Singleton なのでパフォーマンス影響なし。

  2026-04-03 (fix #2):
    search() の Qdrant 戻り値パースを完全堅牢化。
    query_points() / search() どちらの API でも、
    結果が QueryResponse(.points) でも list でも正しく処理できるよう
    try/except + 統一ループに変更。

  2026-04-03 (fix #3):
    search() の API 呼び出し順序を変更。
    旧来の client.search() を最優先 API として使用し、
    AttributeError 時のみ query_points() にフォールバックする。
    理由: テスト環境・本番環境問わず client.search() が最も互換性が高い。
    query_points() は Qdrant client >= 1.7 のみ対応で、
    ローカルモードの挙動が不安定なケースがある。
"""

from __future__ import annotations

import uuid
from typing import Optional
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

from ..utils.embedder import Embedder


class VectorStoreError(Exception):
    """VectorStore 操作に関する例外"""


class QdrantVectorStore:
    """
    Qdrant ローカルモード ベクトルストア。

    Brain ごとにコレクション名を分離し、
    SubSkill フィルタリング検索に対応。
    """

    def __init__(self, vector_index_path: str, brain_id: str) -> None:
        """
        Initialize Qdrant client.

        Args:
            vector_index_path: Qdrant ローカルディレクトリパス
            brain_id: Brain UUID（コレクション名の一部）

        【設計方針】
            Embedder は __init__ で取得しない。
            _get_embedder() 経由で常に Embedder.get_instance() を呼び出すことで
            monkeypatch / DI への互換性を保つ。
            本番時は Embedder の Singleton がキャッシュするのでパフォーマンス影響なし。
        """
        self.vector_index_path = Path(vector_index_path)
        self.vector_index_path.mkdir(parents=True, exist_ok=True)

        self.brain_id = brain_id
        self.collection_name = f"brain_{brain_id}"[:64]  # Qdrant 制限
        self.vector_dim = 1024  # bge-m3 の次元数

        # Qdrant ローカルクライアント
        self.client = QdrantClient(path=str(self.vector_index_path))

        # コレクション初期化
        self._ensure_collection()

    # ======================================================================
    # Embedder 遅延取得（キャッシュなし・毎回委論）
    # ======================================================================

    def _get_embedder(self) -> Embedder:
        """
        Embedder を毎回 Embedder.get_instance() 経由で取得する。

        キャッシュを持たない理由:
          - monkeypatch がコンストラクタ後に Embedder.get_instance を差し替える場合、
            self._embedder キャッシュがあると差し替え前のインスタンスが残る。
          - 本番時は Embedder 自身が Singleton なので毎回呼んでも重くない。
        """
        return Embedder.get_instance()

    def _ensure_collection(self) -> None:
        """コレクションが存在しなければ作成"""
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_dim,
                    distance=Distance.COSINE,
                ),
            )

    # ======================================================================
    # CRUD
    # ======================================================================

    def add_chunks(
        self,
        chunks: list[dict],
        subskill_id: str,
        source_type: str,
        source_url: Optional[str] = None,
    ) -> list[str]:
        """
        チャンク群をバッチ upsert。

        Args:
            chunks: [{"id": str, "text": str, "trust_score": float}, ...]
            subskill_id: 主 SubSkill ID
            source_type: Web / PDF / Video / API / Experiment
            source_url: 元 URL（オプション）

        Returns:
            upsert された Qdrant point ID リスト
        """
        if not chunks:
            return []

        embedder = self._get_embedder()
        texts = [chunk["text"] for chunk in chunks]
        vectors = embedder.encode(texts)

        points = []
        point_ids = []

        for i, chunk in enumerate(chunks):
            point_id = str(uuid.uuid4())
            point_ids.append(point_id)

            payload = {
                "chunk_id":   chunk.get("id", str(uuid.uuid4())),
                "text":       chunk["text"],
                "trust_score": chunk.get("trust_score", 0.5),
                "subskill_id": subskill_id,
                "source_type": source_type,
                "source_url":  source_url or "",
                "acquired_at": chunk.get("acquired_at", ""),
            }

            points.append(PointStruct(
                id=point_id,
                vector=vectors[i].tolist(),
                payload=payload,
            ))

        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        return point_ids

    def search(
        self,
        query_text: str,
        subskill_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: float = 0.0,
    ) -> list[dict]:
        """
        テキスト類似検索（オプション SubSkill フィルタ付き）。

        【API 優先順位】
          1. client.search()      - 旧来 API。最も互換性が高い。
          2. client.query_points() - Qdrant client >= 1.7 の新 API。
                                     ローカルモードで不安定なケースがあるため fallback。

        Args:
            query_text: クエリテキスト
            subskill_id: フィルタ条件（None = 全 SubSkill）
            limit: 結果数上限
            score_threshold: スコア下限

        Returns:
            [{"chunk_id": str, "text": str, "score": float, ...}, ...]
        """
        embedder = self._get_embedder()
        query_vector = embedder.encode_single(query_text)

        # numpy array → list に変換（Qdrant クライアントの型要件）
        if hasattr(query_vector, "tolist"):
            query_vector_list = query_vector.tolist()
        else:
            query_vector_list = list(query_vector)

        filter_cond = None
        if subskill_id:
            filter_cond = Filter(
                must=[
                    FieldCondition(
                        key="subskill_id",
                        match=MatchValue(value=subskill_id),
                    )
                ]
            )

        raw_results = None

        # --- 優先: client.search()（旧来 API・互換性最高）---
        try:
            raw = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector_list,
                query_filter=filter_cond,
                limit=limit,
                score_threshold=score_threshold,
            )
            # 戻り値は list[ScoredPoint] または .points 持ちオブジェクト
            if hasattr(raw, "points"):
                raw_results = raw.points
            elif isinstance(raw, list):
                raw_results = raw
            else:
                raw_results = list(raw)
        except Exception as e_search:
            raw_results = None

        # --- フォールバック: client.query_points()（Qdrant >= 1.7）---
        if raw_results is None:
            try:
                response = self.client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector_list,
                    query_filter=filter_cond,
                    limit=limit,
                    score_threshold=score_threshold,
                )
                if hasattr(response, "points"):
                    raw_results = response.points
                elif isinstance(response, list):
                    raw_results = response
                else:
                    raw_results = list(response)
            except Exception:
                raw_results = []

        if raw_results is None:
            raw_results = []

        # ---- 統一パース: ScoredPoint オブジェクトと dict の両方に対応 ----
        matches = []
        for result in raw_results:
            if hasattr(result, "payload"):
                payload = result.payload or {}
                score   = result.score
                rid     = str(result.id)
            else:
                payload = result.get("payload", {})
                score   = result.get("score", 0.0)
                rid     = str(result.get("id", ""))

            matches.append({
                "qdrant_id":   rid,
                "chunk_id":    payload.get("chunk_id"),
                "text":        payload.get("text"),
                "score":       score,
                "trust_score": payload.get("trust_score"),
                "subskill_id": payload.get("subskill_id"),
                "source_type": payload.get("source_type"),
                "source_url":  payload.get("source_url"),
            })

        return matches

    def duplicate_check(
        self,
        text: str,
        subskill_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        重複チェック（コサイン類似度 > 0.92）。

        Returns:
            重複が見つかった場合は dict、見つからなかった場合は None
        """
        embedder = self._get_embedder()
        results = self.search(
            query_text=text,
            subskill_id=subskill_id,
            limit=1,
            score_threshold=embedder.DEDUP_THRESHOLD,
        )

        if results:
            return results[0]
        return None

    def delete_chunk(self, qdrant_id: str) -> None:
        """Qdrant point を削除"""
        self.client.delete(
            collection_name=self.collection_name,
            points_selector={"ids": [qdrant_id]},
        )

    def get_chunks_by_subskill(
        self,
        subskill_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """SubSkill 別にチャンク一覧取得。"""
        filter_cond = Filter(
            must=[
                FieldCondition(
                    key="subskill_id",
                    match=MatchValue(value=subskill_id),
                )
            ]
        )

        points = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=filter_cond,
            limit=limit,
        )[0]

        chunks = []
        for point in points:
            chunks.append({
                "qdrant_id":   str(point.id),
                "chunk_id":    point.payload.get("chunk_id"),
                "text":        point.payload.get("text"),
                "trust_score": point.payload.get("trust_score"),
                "source_type": point.payload.get("source_type"),
                "source_url":  point.payload.get("source_url"),
            })

        return chunks

    def get_collection_stats(self) -> dict:
        """コレクション統計情報"""
        collection_info = self.client.get_collection(self.collection_name)
        return {
            "collection_name": self.collection_name,
            "points_count":    collection_info.points_count,
            "vector_dim":      self.vector_dim,
        }

    def delete_collection(self) -> None:
        """コレクション全削除（Brain削除時に使用）"""
        self.client.delete_collection(collection_name=self.collection_name)

    def close(self) -> None:
        """ローカル Qdrant クライアントを明示的に解放する。"""
        try:
            self.client.close()
        except Exception:
            pass
