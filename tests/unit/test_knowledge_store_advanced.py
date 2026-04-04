# tests/unit/test_knowledge_store_advanced.py
"""
KnowledgeStore の高度な統合テスト（Phase 2 追加分）。
矛盾検出・知識更新フロー・SubSkill 概要・統計・URL チェックを検証。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.sba.storage.vector_store import QdrantVectorStore
from src.sba.storage.knowledge_store import KnowledgeStore


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
def ks(tmp_path, monkeypatch) -> KnowledgeStore:
    """テスト用 KnowledgeStore（FakeEmbedder + tmp_path）。"""
    fake = _FakeEmbedder()
    monkeypatch.setattr(QdrantVectorStore, "_get_embedder", lambda self: fake)
    store = KnowledgeStore(str(tmp_path), "brain-adv")
    store.ensure_subskill_node("design", "設計")
    store.ensure_subskill_node("testing", "テスト")
    store.ensure_subskill_node("impl", "実装")
    return store


class TestKnowledgeStoreHybridSearch:
    def test_hybrid_search_returns_list(self, ks: KnowledgeStore) -> None:
        """query_hybrid がリストを返すこと。"""
        ks.store_chunk("Python generators are memory efficient.", "design", "Web", trust_score=0.9)
        results = ks.query_hybrid("Python generators", limit=5)
        assert isinstance(results, list)

    def test_hybrid_search_result_keys(self, ks: KnowledgeStore) -> None:
        """query_hybrid の結果に必須キーが含まれること。"""
        ks.store_chunk("List comprehensions are concise.", "design", "Web", trust_score=0.85)
        results = ks.query_hybrid("List comprehensions", limit=3)
        if results:
            required = {"chunk_id", "text", "score", "trust_score", "source_type"}
            assert required.issubset(results[0].keys())

    def test_hybrid_search_with_subskill_filter(self, ks: KnowledgeStore) -> None:
        """subskill_id フィルタ付き hybrid 検索が動作すること。"""
        ks.store_chunk("Mock objects simulate dependencies in tests.", "testing", "Web", trust_score=0.9)
        results = ks.query_hybrid("mock dependencies", subskill_id="testing", limit=5)
        assert isinstance(results, list)


class TestKnowledgeStoreSearchSimilar:
    def test_search_similar_returns_list(self, ks: KnowledgeStore) -> None:
        """search_similar がリストを返すこと。"""
        ks.store_chunk("Context managers handle resource cleanup.", "impl", "Web", trust_score=0.9)
        results = ks.search_similar("context managers", limit=5)
        assert isinstance(results, list)

    def test_search_similar_score_threshold(self, ks: KnowledgeStore) -> None:
        """score_threshold=0.99 で結果が限定されること（極端な閾値）。"""
        ks.store_chunk("Abstract base classes define interfaces.", "design", "Web", trust_score=0.9)
        results = ks.search_similar("totally different topic", limit=5, score_threshold=0.99)
        assert isinstance(results, list)
        # 全く異なるテキストでは空になるか低スコアのみ
        assert all(r["score"] >= 0.99 for r in results)


class TestKnowledgeStoreMarkKnowledgeUpdate:
    def test_mark_knowledge_update_deprecates_old(self, ks: KnowledgeStore) -> None:
        """mark_knowledge_update で旧チャンクが deprecated になること。"""
        old = ks.store_chunk("Python 3.8 introduced walrus operator.", "design", "Web", trust_score=0.7)
        new = ks.store_chunk("Python 3.12 introduced significant performance gains.", "design", "Web", trust_score=0.95)

        old_id = old["chunk_id"]
        new_id = new["chunk_id"]

        ks.mark_knowledge_update(old_id, new_id)

        old_chunk = ks.get_chunk(old_id)
        assert bool(old_chunk["is_deprecated"]) is True

    def test_mark_knowledge_update_new_chunk_active(self, ks: KnowledgeStore) -> None:
        """mark_knowledge_update で新チャンクは deprecated にならないこと。"""
        old = ks.store_chunk("Old way to open files in Python.", "impl", "Web", trust_score=0.6)
        new = ks.store_chunk("Use context manager with open() for safe file handling.", "impl", "Web", trust_score=0.95)

        ks.mark_knowledge_update(old["chunk_id"], new["chunk_id"])

        new_chunk = ks.get_chunk(new["chunk_id"])
        assert not bool(new_chunk["is_deprecated"])


class TestKnowledgeStoreCheckUrl:
    def test_check_url_in_timeline_found(self, ks: KnowledgeStore) -> None:
        """store_chunk 後に check_url_in_timeline で同 URL が見つかること。"""
        url = "https://docs.python.org/3/unique-page"
        ks.store_chunk("Official Python docs content.", "design", "Web", source_url=url, trust_score=0.95)
        result = ks.check_url_in_timeline(url)
        assert result is not None

    def test_check_url_in_timeline_not_found(self, ks: KnowledgeStore) -> None:
        """未追加 URL で check_url_in_timeline が None を返すこと。"""
        result = ks.check_url_in_timeline("https://not.added.url/page")
        assert result is None


class TestKnowledgeStoreStats:
    def test_get_knowledge_base_stats_returns_dict(self, ks: KnowledgeStore) -> None:
        """get_knowledge_base_stats が dict を返すこと。"""
        stats = ks.get_knowledge_base_stats()
        assert isinstance(stats, dict)
        assert "vector_store" in stats
        assert "graph_store" in stats
        assert "timeline" in stats

    def test_get_subskill_overview_returns_list(self, ks: KnowledgeStore) -> None:
        """get_subskill_overview がリストを返すこと。"""
        overview = ks.get_subskill_overview()
        assert isinstance(overview, list)

    def test_get_subskill_overview_after_store(self, ks: KnowledgeStore) -> None:
        """store_chunk 後の get_subskill_overview に chunk_count が含まれること。"""
        ks.store_chunk("Dataclasses reduce boilerplate.", "design", "Web", trust_score=0.9)
        overview = ks.get_subskill_overview()
        design_entry = next((s for s in overview if s.get("id") == "design"), None)
        if design_entry:
            assert "chunk_count" in design_entry
            assert design_entry["chunk_count"] >= 1