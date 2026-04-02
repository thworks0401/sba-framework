"""
T-1: Storage layer unit tests.

Qdrant + Kuzu + SQLite を束ねる KnowledgeStore の基本動作を、
軽量な埋め込みモックで検証する。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.sba.storage import vector_store as vector_store_module
from src.sba.storage.knowledge_store import KnowledgeStore


class _FakeEmbedder:
    DEDUP_THRESHOLD = 0.92

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(1024, dtype=np.float32)
        vec[sum(ord(ch) for ch in text) % 1024] = 1.0
        return vec

    def encode(self, texts, batch_size: int = 32, show_progress: bool = False) -> np.ndarray:
        return np.vstack([self._vector(text) for text in texts])

    def encode_single(self, text: str) -> np.ndarray:
        return self._vector(text)


@pytest.fixture
def knowledge_store(tmp_path, monkeypatch):
    fake_embedder = _FakeEmbedder()
    monkeypatch.setattr(
        vector_store_module.Embedder,
        "get_instance",
        classmethod(lambda cls: fake_embedder),
    )

    store = KnowledgeStore(str(tmp_path), "test-brain")
    store.ensure_subskill_node("design", "設計")
    store.ensure_subskill_node("testing", "テスト")
    return store


def test_store_query_and_duplicate_detection(knowledge_store: KnowledgeStore):
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

    duplicate = knowledge_store.store_chunk(
        text=text,
        primary_subskill="design",
        source_type="Web",
        source_url="https://example.com/decorators",
        trust_score=0.9,
    )

    assert duplicate["duplicate_detected"] is True
    assert "Content hash" in duplicate["reason"]

    results = knowledge_store.query_hybrid(text, limit=3)
    assert results
    assert results[0]["chunk_id"] == stored["chunk_id"]
    assert results[0]["primary_subskill"] == "design"


def test_mark_deprecated_and_review_flags(knowledge_store: KnowledgeStore):
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
    assert chunk is not None
    assert chunk["requires_human_review"] is True
    assert chunk["is_deprecated"] is True

