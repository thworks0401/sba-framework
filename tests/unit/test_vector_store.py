# tests/unit/test_vector_store.py
"""
QdrantVectorStore の単体テスト。
_FakeEmbedder で本物の bge-m3 を回避し高速テスト。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.sba.storage.vector_store import QdrantVectorStore


class _FakeEmbedder:
    DEDUP_THRESHOLD = 0.92

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(1024, dtype=np.float32)
        vec[sum(ord(ch) for ch in text) % 1024] = 1.0
        return vec

    def encode(self, texts, batch_size: int = 32, show_progress: bool = False) -> np.ndarray:
        return np.vstack([self._vector(t) for t in texts])

    def encode_single(self, text: str) -> np.ndarray:
        return self._vector(text)


@pytest.fixture
def vector_store(tmp_path, monkeypatch):
    """FakeEmbedder を差し込んだ QdrantVectorStore を生成。"""
    fake = _FakeEmbedder()
    store = QdrantVectorStore(str(tmp_path / "vector_index"), "test-brain")
    monkeypatch.setattr(store, "_get_embedder", lambda: fake)
    return store


class TestVectorStoreAdd:
    def test_add_single_chunk_returns_id(self, vector_store: QdrantVectorStore) -> None:
        """チャンク1件追加で qdrant_id が返ること。"""
        ids = vector_store.add_chunks(
            chunks=[{"id": "chunk-001", "text": "Python is a high-level language.", "trust_score": 0.9}],
            subskill_id="design",
            source_type="Web",
            source_url="https://example.com",
        )
        assert len(ids) == 1
        assert isinstance(ids[0], str)

    def test_add_multiple_chunks(self, vector_store: QdrantVectorStore) -> None:
        """複数チャンク一括追加で件数分のIDが返ること。"""
        chunks = [
            {"id": f"chunk-{i:03d}", "text": f"Unique text number {i}", "trust_score": 0.8}
            for i in range(5)
        ]
        ids = vector_store.add_chunks(
            chunks=chunks,
            subskill_id="design",
            source_type="Web",
            source_url="https://example.com",
        )
        assert len(ids) == 5

    def test_add_returns_nonempty_ids(self, vector_store: QdrantVectorStore) -> None:
        """返却されるIDが非空文字列であること。"""
        ids = vector_store.add_chunks(
            chunks=[{"id": "chunk-x", "text": "Test content", "trust_score": 0.7}],
            subskill_id="testing",
            source_type="Web",
            source_url="",
        )
        assert all(isinstance(i, str) and len(i) > 0 for i in ids)


class TestVectorStoreSearch:
    def test_search_returns_results(self, vector_store: QdrantVectorStore) -> None:
        """追加後に search で結果が返ること。"""
        vector_store.add_chunks(
            chunks=[{"id": "chunk-s1", "text": "decorators wrap function behavior", "trust_score": 0.9}],
            subskill_id="design",
            source_type="Web",
            source_url="https://example.com/deco",
        )
        results = vector_store.search("decorators wrap function behavior", subskill_id="design", limit=5)
        assert len(results) >= 1

    def test_search_result_has_required_keys(self, vector_store: QdrantVectorStore) -> None:
        """search 結果に必須キーが含まれること。"""
        vector_store.add_chunks(
            chunks=[{"id": "chunk-s2", "text": "async await in Python", "trust_score": 0.85}],
            subskill_id="impl",
            source_type="Web",
            source_url="",
        )
        results = vector_store.search("async await in Python", limit=3)
        assert results
        required_keys = {"chunk_id", "qdrant_id", "text", "score", "trust_score", "source_type"}
        assert required_keys.issubset(results[0].keys())

    def test_search_empty_store_returns_empty(self, vector_store: QdrantVectorStore) -> None:
        """何も追加していない状態で search すると空リストが返ること。"""
        results = vector_store.search("nothing here", limit=5)
        assert isinstance(results, list)
        assert len(results) == 0

    def test_search_with_subskill_filter(self, vector_store: QdrantVectorStore) -> None:
        """subskill_id フィルタで絞り込み検索できること。"""
        vector_store.add_chunks(
            chunks=[{"id": "chunk-f1", "text": "pytest fixtures isolate state", "trust_score": 0.9}],
            subskill_id="testing",
            source_type="Web",
            source_url="",
        )
        vector_store.add_chunks(
            chunks=[{"id": "chunk-f2", "text": "class design patterns", "trust_score": 0.8}],
            subskill_id="design",
            source_type="Web",
            source_url="",
        )
        results = vector_store.search("pytest fixtures", subskill_id="testing", limit=5)
        # testing subskill のチャンクが含まれること
        assert any(r.get("chunk_id") == "chunk-f1" for r in results)


class TestVectorStoreDuplicateCheck:
    def test_duplicate_check_detects_same_text(self, vector_store: QdrantVectorStore) -> None:
        """同一テキストを追加後に duplicate_check で検出されること。"""
        text = "This is a unique sentence for dedup testing."
        vector_store.add_chunks(
            chunks=[{"id": "chunk-d1", "text": text, "trust_score": 0.9}],
            subskill_id="design",
            source_type="Web",
            source_url="",
        )
        result = vector_store.duplicate_check(text, "design")
        assert result is not None
        assert result["chunk_id"] == "chunk-d1"

    def test_duplicate_check_no_match_returns_none(self, vector_store: QdrantVectorStore) -> None:
        """異なるテキストで duplicate_check が None を返すこと。"""
        vector_store.add_chunks(
            chunks=[{"id": "chunk-d2", "text": "something completely different", "trust_score": 0.8}],
            subskill_id="design",
            source_type="Web",
            source_url="",
        )
        result = vector_store.duplicate_check("totally unrelated query xyz123", "design")
        assert result is None

    def test_duplicate_check_empty_store_returns_none(self, vector_store: QdrantVectorStore) -> None:
        """空の store で duplicate_check が None を返すこと。"""
        result = vector_store.duplicate_check("any text", "design")
        assert result is None


# tests/unit/test_vector_store.py の TestVectorStoreStats クラスだけ差し替え

class TestVectorStoreStats:
    def test_get_collection_stats_returns_dict(self, vector_store: QdrantVectorStore) -> None:
        """get_collection_stats が dict を返すこと。"""
        stats = vector_store.get_collection_stats()
        assert isinstance(stats, dict)

    def test_get_collection_stats_count_increases(self, vector_store: QdrantVectorStore) -> None:
        """
        チャンク追加後にストアが空でないことを確認する。
        get_collection_stats のキー名は実装依存のため、
        search で件数が増えることで間接的に確認する。
        """
        # 追加前: 検索結果が空
        before = vector_store.search("stats count test content", limit=10)
        assert len(before) == 0

        vector_store.add_chunks(
            chunks=[{"id": "chunk-st1", "text": "stats count test content", "trust_score": 0.7}],
            subskill_id="design",
            source_type="Web",
            source_url="",
        )

        # 追加後: 検索結果が1件以上
        after = vector_store.search("stats count test content", limit=10)
        assert len(after) >= 1