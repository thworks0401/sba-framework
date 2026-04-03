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
  __init__ で即時実行していた Embedder.get_instance() を廃止。
  monkeypatch がコンストラクタ後に適用されるテスト環境では
  モックが間に合わないため、初回使用時に取得する遅延初期化に変更。
  _get_embedder() メソッド経由で self._embedder キャッシュを利用する。
"""

from __future__ import annotations

import os
import uuid
from typing import Optional
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from ..utils.embedder import Embedder


class VectorStoreError(Exception):
    """VectorStore 操作に関する例外"""


class QdrantVectorStore:
    """
    Qdrant ローカルモード ベクトルストア。

    Brain ごとにコレクション名を分離し、
    SubSkill フィルタリング搜索に対応。
    """

    def __init__(self, vector_index_path: str, brain_id: str) -> None:
        """
        Initialize Qdrant client.

        Args:
            vector_index_path: Qdrant ローカルディレクトリパス
            brain_id: Brain UUID（コレクション名の一部）

        【遅延初期化】
            Embedder はここでは取得しない。
            monkeypatch / DI が __init__ の後に適用されるケースで
            モックが効かなくなるのを防ぐため、_get_embedder() で
            初回アクセス時に取得する設計にしている。
        """
        self.vector_index_path = Path(vector_index_path)
        self.vector_index_path.mkdir(parents=True, exist_ok=True)

        self.brain_id = brain_id
        self.collection_name = f"brain_{brain_id}"[:64]  # Qdrant 制限
        self.vector_dim = 1024  # bge-m3 の次元数

        # 遅延初期化: __init__ では None にしておく
        self._embedder: Optional[Embedder] = None

        # Qdrant ローカルクライアント
        self.client = QdrantClient(path=str(self.vector_index_path))

        # コレクション初期化
        self._ensure_collection()

    # ======================================================================
    # Embedder 遅延取得
    # ======================================================================

    def _get_embedder(self) -> Embedder:
        """
        Embedder シングルトンを遅延取得して返す。

        キャッシュ済みであればそのまま返し、
        未取得であれば Embedder.get_instance() を呼んで self._embedder にキャッシュする。
        テスト時に monkeypatch で差し替えたモックがここで正しく適用される。
        """
        if self._embedder is None:
            self._embedder = Embedder.get_instance()
        return self._embedder

    def _ensure_collection(self) -> None:
        """コレクションが存在しなければ作成"""
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            # コレクション非存在 → 新規作成
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_dim,
                    distance=Distance.COSINE,  # コサイン類似度
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

        # 遅延取得した Embedder を使用
        embedder = self._get_embedder()

        # テキストをまとめてベクトル化
        texts = [chunk["text"] for chunk in chunks]
        vectors = embedder.encode(texts)

        points = []
        point_ids = []

        for i, chunk in enumerate(chunks):
            point_id = str(uuid.uuid4())
            point_ids.append(point_id)

            # メタデータ（フィルタに使用）
            payload = {
                "chunk_id": chunk.get("id", str(uuid.uuid4())),
                "text": chunk["text"],
                "trust_score": chunk.get("trust_score", 0.5),
                "subskill_id": subskill_id,
                "source_type": source_type,
                "source_url": source_url or "",
                "acquired_at": chunk.get("acquired_at", ""),
            }

            point = PointStruct(
                id=point_id,
                vector=vectors[i].tolist(),
                payload=payload,
            )
            points.append(point)

        # バッチ upsert
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

        Args:
            query_text: クエリテキスト
            subskill_id: フィルタ条件（None = 全 SubSkill）
            limit: 結果数上限
            score_threshold: スコア下限

        Returns:
            [{"chunk_id": str, "text": str, "score": float, ...}, ...]
        """
        # 遅延取得した Embedder を使用
        embedder = self._get_embedder()
        query_vector = embedder.encode_single(query_text)

        # フィルタ条件
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

        # 新しいAPI: query_points を使用
        try:
            # 旧API (1.x) の互換性
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector.tolist(),
                query_filter=filter_cond,
                limit=limit,
                score_threshold=score_threshold,
            )
        except AttributeError:
            # 最新API では query を使う可能性
            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector.tolist(),
                query_filter=filter_cond,
                limit=limit,
                score_threshold=score_threshold,
            )

        matches = []
        for result in results.points if hasattr(results, 'points') else results:
            payload = result.payload if hasattr(result, 'payload') else result.get('payload', {})
            score = result.score if hasattr(result, 'score') else result.get('score', 0.0)

            matches.append({
                "qdrant_id": str(result.id) if hasattr(result, 'id') else str(result.get('id')),
                "chunk_id": payload.get("chunk_id"),
                "text": payload.get("text"),
                "score": score,
                "trust_score": payload.get("trust_score"),
                "subskill_id": payload.get("subskill_id"),
                "source_type": payload.get("source_type"),
                "source_url": payload.get("source_url"),
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
        # _get_embedder() 経由なので DEDUP_THRESHOLD もモック対象から正しく取得できる
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
        """
        SubSkill 別にチャンク一覧取得。

        Args:
            subskill_id: SubSkill ID
            limit: 結果数上限

        Returns:
            チャンク情報リスト
        """
        filter_cond = Filter(
            must=[
                FieldCondition(
                    key="subskill_id",
                    match=MatchValue(value=subskill_id),
                )
            ]
        )

        # スクロール取得
        points = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=filter_cond,
            limit=limit,
        )[0]

        chunks = []
        for point in points:
            chunks.append({
                "qdrant_id": str(point.id),
                "chunk_id": point.payload.get("chunk_id"),
                "text": point.payload.get("text"),
                "trust_score": point.payload.get("trust_score"),
                "source_type": point.payload.get("source_type"),
                "source_url": point.payload.get("source_url"),
            })

        return chunks

    def get_collection_stats(self) -> dict:
        """コレクション統計情報"""
        collection_info = self.client.get_collection(self.collection_name)
        return {
            "collection_name": self.collection_name,
            "points_count": collection_info.points_count,
            "vector_dim": self.vector_dim,
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
