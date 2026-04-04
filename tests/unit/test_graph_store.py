# tests/unit/test_graph_store.py
"""
KuzuGraphStore の単体テスト。
ノード追加・エッジ追加・deprecated/review フラグ・CONTRADICTS エッジを検証。
"""

from __future__ import annotations

import pytest

from src.sba.storage.graph_store import KuzuGraphStore


@pytest.fixture
def graph_store(tmp_path) -> KuzuGraphStore:
    """テスト用 KuzuGraphStore（tmp_path 内の knowledge_graph を使用）。"""
    kg_path = tmp_path / "knowledge_graph"
    kg_path.mkdir()
    return KuzuGraphStore(str(kg_path), "test-brain")


class TestGraphStoreSubSkillNode:
    def test_add_subskill_node_success(self, graph_store: KuzuGraphStore) -> None:
        """SubSkill ノードを追加できること。"""
        graph_store.add_subskill_node("design", "設計")

    def test_add_subskill_node_idempotent(self, graph_store: KuzuGraphStore) -> None:
        """同じ SubSkill を2回追加しても例外が出ないこと（冪等性）。"""
        graph_store.add_subskill_node("design", "設計")
        graph_store.add_subskill_node("design", "設計")

    def test_get_all_subskill_nodes_returns_added(self, graph_store: KuzuGraphStore) -> None:
        """追加した SubSkill ノードが get_all_subskill_nodes で返ること。"""
        graph_store.add_subskill_node("design", "設計")
        graph_store.add_subskill_node("testing", "テスト")
        nodes = graph_store.get_all_subskill_nodes()
        ids = [n["id"] for n in nodes]
        assert "design" in ids
        assert "testing" in ids

    def test_update_subskill_density(self, graph_store: KuzuGraphStore) -> None:
        """density_score を更新できること。"""
        graph_store.add_subskill_node("design", "設計")
        # 例外が出なければ OK
        graph_store.update_subskill_density("design", 0.75)


class TestGraphStoreKnowledgeChunk:
    def test_add_chunk_returns_id(self, graph_store: KuzuGraphStore) -> None:
        """knowledge chunk を追加すると chunk_id が返ること。"""
        graph_store.add_subskill_node("design", "設計")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Python uses dynamic typing.",
            trust_score=0.9,
            primary_subskill="design",
            source_type="Web",
            source_url="https://example.com",
            summary="Dynamic typing in Python",
        )
        assert isinstance(chunk_id, str)
        assert len(chunk_id) > 0

    def test_get_knowledge_chunk_returns_data(self, graph_store: KuzuGraphStore) -> None:
        """追加したチャンクを get_knowledge_chunk で取得できること。"""
        graph_store.add_subskill_node("design", "設計")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Immutable objects cannot be changed after creation.",
            trust_score=0.85,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        chunk = graph_store.get_knowledge_chunk(chunk_id)
        assert chunk is not None
        assert chunk["text"] == "Immutable objects cannot be changed after creation."

    def test_get_nonexistent_chunk_returns_none(self, graph_store: KuzuGraphStore) -> None:
        """存在しない chunk_id に get_knowledge_chunk すると None が返ること。"""
        result = graph_store.get_knowledge_chunk("nonexistent-id-99999")
        assert result is None

    def test_add_belongs_to_primary(self, graph_store: KuzuGraphStore) -> None:
        """BELONGS_TO_PRIMARY エッジ追加が例外なく完了すること。"""
        graph_store.add_subskill_node("design", "設計")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Type hints improve code readability.",
            trust_score=0.8,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.add_belongs_to_primary(chunk_id, "design")

    def test_add_related_to_secondary(self, graph_store: KuzuGraphStore) -> None:
        """RELATED_TO_SECONDARY エッジ追加が例外なく完了すること。"""
        graph_store.add_subskill_node("design", "設計")
        graph_store.add_subskill_node("testing", "テスト")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Test-driven development guides design.",
            trust_score=0.85,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.add_related_to_secondary(chunk_id, "testing", relevance=0.7)


class TestGraphStoreFlags:
    def test_mark_deprecated(self, graph_store: KuzuGraphStore) -> None:
        """mark_deprecated 後に is_deprecated が True になること。"""
        graph_store.add_subskill_node("design", "設計")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Old API usage pattern.",
            trust_score=0.6,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.mark_deprecated(chunk_id)
        chunk = graph_store.get_knowledge_chunk(chunk_id)
        assert bool(chunk["is_deprecated"]) is True

    def test_mark_requires_review(self, graph_store: KuzuGraphStore) -> None:
        """mark_requires_review 後に requires_human_review が True になること。"""
        graph_store.add_subskill_node("design", "設計")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Uncertain claim about performance.",
            trust_score=0.5,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.mark_requires_review(chunk_id)
        chunk = graph_store.get_knowledge_chunk(chunk_id)
        assert bool(chunk["requires_human_review"]) is True


class TestGraphStoreEdges:
    def test_add_contradicts_edge(self, graph_store: KuzuGraphStore) -> None:
        """CONTRADICTS エッジを追加できること。"""
        graph_store.add_subskill_node("design", "設計")
        id_a = graph_store.add_knowledge_chunk(
            text="Python is slow for numerical computation.",
            trust_score=0.6,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        id_b = graph_store.add_knowledge_chunk(
            text="Python with NumPy is fast for numerical computation.",
            trust_score=0.9,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        # 例外が出なければ OK
        graph_store.add_contradicts(id_a, id_b)

    def test_add_updates_edge(self, graph_store: KuzuGraphStore) -> None:
        """UPDATES エッジを追加できること（新知識が旧知識を更新）。"""
        graph_store.add_subskill_node("design", "設計")
        old_id = graph_store.add_knowledge_chunk(
            text="Use Python 3.9 for walrus operator support.",
            trust_score=0.7,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        new_id = graph_store.add_knowledge_chunk(
            text="Use Python 3.11 for better performance and error messages.",
            trust_score=0.95,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.add_updates(old_id, new_id)

    def test_get_related_chunks(self, graph_store: KuzuGraphStore) -> None:
        """get_related_chunks が primary_subskill を返すこと。"""
        graph_store.add_subskill_node("design", "設計")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Dependency injection reduces coupling.",
            trust_score=0.85,
            primary_subskill="design",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.add_belongs_to_primary(chunk_id, "design")
        related = graph_store.get_related_chunks(chunk_id)
        assert "primary_subskill" in related

    def test_get_chunks_by_subskill(self, graph_store: KuzuGraphStore) -> None:
        """get_chunks_by_subskill で SubSkill に属するチャンクが返ること。"""
        graph_store.add_subskill_node("testing", "テスト")
        chunk_id = graph_store.add_knowledge_chunk(
            text="Unit tests should be fast and isolated.",
            trust_score=0.9,
            primary_subskill="testing",
            source_type="Web",
            source_url="",
            summary="",
        )
        graph_store.add_belongs_to_primary(chunk_id, "testing")
        chunks = graph_store.get_chunks_by_subskill("testing")
        assert any(c.get("id") == chunk_id or c.get("chunk_id") == chunk_id for c in chunks)

    def test_get_graph_stats_returns_dict(self, graph_store: KuzuGraphStore) -> None:
        """get_graph_stats が dict を返すこと。"""
        stats = graph_store.get_graph_stats()
        assert isinstance(stats, dict)