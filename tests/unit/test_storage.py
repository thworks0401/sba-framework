"""
T-1: Storage layer unit tests.

Qdrant + Kuzu + SQLite を束ねる KnowledgeStore の基本動作を、
軽量な埋め込みモックで検証する。

【修正履歴】
  2026-04-03 (fix #1):
    monkeypatch.setattr の lambda に classmethod ラッパー不要と記載していたが、
    実際は Embedder.get_instance (classmethod) に lambda を差し込むと
    呼び出し時に cls が第1引数として渡って TypeError が発生する。
    except で握り潰されてベクトルが生成されず Qdrant に何も入らない問題を修正。

  2026-04-03 (fix #2):
    monkeypatch ターゲットを Embedder.get_instance から
    QdrantVectorStore._get_embedder に変更。
    _get_embedder は通常のインスタンスメソッドなので
    lambda self: fake_embedder で正しく差し替えられる。
    classmethod の呼び出し規約の問題を根本的に回避する。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.sba.storage.vector_store import QdrantVectorStore
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

    【修正】monkeypatch ターゲットを QdrantVectorStore._get_embedder に変更。

    理由:
      Embedder.get_instance は classmethod のため、
      monkeypatch.setattr(Embedder, "get_instance", lambda: fake) とすると
      呼び出し時に cls が第1引数として渡り TypeError が発生する。
      except で握り潰されてベクトルが生成されず Qdrant に何も入らない。

      _get_embedder は通常のインスタンスメソッドなので
      lambda self: fake_embedder で正しく差し替えられる。
    """
    fake_embedder = _FakeEmbedder()

    # QdrantVectorStore._get_embedder を直接差し替える（classmethod 問題を回避）
    monkeypatch.setattr(
        QdrantVectorStore,
        "_get_embedder",
        lambda self: fake_embedder,
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
    assert bool(chunk["requires_human_review"]) is True
    assert bool(chunk["is_deprecated"]) is True
