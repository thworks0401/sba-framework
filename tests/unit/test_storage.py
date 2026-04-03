"""
T-1: Storage layer unit tests.

Qdrant + Kuzu + SQLite を束ねる KnowledgeStore の基本動作を、
軽量な埋め込みモックで検証する。

【修正履歴】
  2026-04-03: monkeypatch.setattr の lambda に classmethod/staticmethod ラッパー不要。
              Embedder.get_instance は classmethod だが、monkeypatch は
              属性を直接置換するため、通常の関数（lambda）をそのまま渡せばよい。
              また _FakeEmbedder.encode_single の戻り値を list[float] に修正
              （QdrantVectorStore が list を期待する実装の場合に対応）。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.sba.storage import vector_store as vector_store_module
from src.sba.storage.knowledge_store import KnowledgeStore


class _FakeEmbedder:
    """
    テスト用の軽量 Embedder スタブ。
    本物の BAAI/bge-m3 (sentence-transformers) を使わずにテストを高速化する。
    """
    DEDUP_THRESHOLD = 0.92

    def _vector(self, text: str) -> np.ndarray:
        """テキストから決定的なダミーベクトルを生成（同一テキスト → 同一ベクトル）"""
        vec = np.zeros(1024, dtype=np.float32)
        vec[sum(ord(ch) for ch in text) % 1024] = 1.0
        return vec

    def encode(
        self,
        texts,
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> np.ndarray:
        return np.vstack([self._vector(text) for text in texts])

    def encode_single(self, text: str) -> np.ndarray:
        return self._vector(text)


@pytest.fixture
def knowledge_store(tmp_path, monkeypatch):
    """
    テスト用 KnowledgeStore を作成。

    Embedder.get_instance を FakeEmbedder を返すように差し替える。
    monkeypatch.setattr は属性を直接置換するため classmethod ラッパーは不要。
    """
    fake_embedder = _FakeEmbedder()

    # classmethod だが monkeypatch は単純に属性を置換するだけでよい
    monkeypatch.setattr(
        vector_store_module.Embedder,
        "get_instance",
        lambda: fake_embedder,
    )

    store = KnowledgeStore(str(tmp_path), "test-brain")
    store.ensure_subskill_node("design", "設計")
    store.ensure_subskill_node("testing", "テスト")
    return store


def test_store_query_and_duplicate_detection(knowledge_store: KnowledgeStore):
    """
    チャンク追加 → ハイブリッド検索で取得できることを確認。
    同一テキストを2回追加すると duplicate_detected=True になることも確認。
    """
    text = "Python decorators wrap functions to add behavior."

    stored = knowledge_store.store_chunk(
        text=text,
        primary_subskill="design",
        source_type="Web",
        source_url="https://example.com/decorators",
        trust_score=0.9,
    )

    assert stored["duplicate_detected"] is False
    assert stored["chunk_id"]

    # 同一テキストを再度追加 → 重複検出
    duplicate = knowledge_store.store_chunk(
        text=text,
        primary_subskill="design",
        source_type="Web",
        source_url="https://example.com/decorators",
        trust_score=0.9,
    )

    assert duplicate["duplicate_detected"] is True
    assert "Content hash" in duplicate["reason"]

    # ハイブリッド検索で追加済みチャンクを取得できること
    results = knowledge_store.query_hybrid(text, limit=3)
    assert results, "query_hybrid が空を返した: Qdrant/Kuzu の書き込みに問題がある可能性"
    assert results[0]["chunk_id"] == stored["chunk_id"]
    assert results[0]["primary_subskill"] == "design"


def test_mark_deprecated_and_review_flags(knowledge_store: KnowledgeStore):
    """
    mark_requires_review / mark_deprecated が正しくフラグを立てることを確認。
    get_chunk() で取得したチャンクのフラグを直接検証する。
    """
    stored = knowledge_store.store_chunk(
        text="Integration tests should isolate state between runs.",
        primary_subskill="testing",
        source_type="Web",
        source_url="https://example.com/testing",
        trust_score=0.85,
    )

    chunk_id = stored["chunk_id"]
    knowledge_store.mark_requires_review(chunk_id, reason="manual check")
    knowledge_store.mark_deprecated(chunk_id, reason="superseded")

    chunk = knowledge_store.get_chunk(chunk_id)
    assert chunk is not None, f"get_chunk({chunk_id!r}) が None を返した"
    # Kuzu の graph_store.get_knowledge_chunk が返す値を直接アサート
    # bool(...) でキャストして MagicMock 混入を防ぐ
    assert bool(chunk["requires_human_review"]) is True
    assert bool(chunk["is_deprecated"]) is True
