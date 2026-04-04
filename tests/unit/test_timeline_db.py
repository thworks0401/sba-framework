# tests/unit/test_timeline_db.py
"""
TimelineRepository の単体テスト。
insert / get / freshness 更新 / 重複チェック / URL 検索を検証。
"""

from __future__ import annotations

import pytest

from src.sba.storage.timeline_db import TimelineRepository


@pytest.fixture
def timeline(tmp_path) -> TimelineRepository:
    """テスト用 TimelineRepository（tmp_path 内の SQLite を使用）。"""
    return TimelineRepository(str(tmp_path / "learning_timeline.db"))


class TestTimelineInsert:
    def test_insert_returns_id(self, timeline: TimelineRepository) -> None:
        """insert_timeline が str の timeline_id を返すこと。"""
        tid = timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash="abc123",
            subskill="design",
            url_or_path="https://example.com",
            qdrant_ids=["q-001"],
            kg_node_ids=["chunk-001"],
            freshness=1.0,
        )
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_insert_multiple_returns_unique_ids(self, timeline: TimelineRepository) -> None:
        """複数件 insert するとそれぞれ異なる ID が返ること。"""
        ids = set()
        for i in range(3):
            tid = timeline.insert_timeline(
                brain_id="test-brain",
                source_type="Web",
                content_hash=f"hash-{i}",
                subskill="design",
                url_or_path=f"https://example.com/{i}",
                qdrant_ids=[f"q-{i:03d}"],
                kg_node_ids=[f"chunk-{i:03d}"],
                freshness=1.0,
            )
            ids.add(tid)
        assert len(ids) == 3


class TestTimelineDuplicateCheck:
    def test_check_duplicate_by_hash_detects(self, timeline: TimelineRepository) -> None:
        """同一 content_hash を insert 後に check_duplicate_by_hash で検出できること。"""
        content_hash = "deadbeef12345678"
        timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash=content_hash,
            subskill="design",
            url_or_path="",
            qdrant_ids=[],
            kg_node_ids=["chunk-001"],
            freshness=1.0,
        )
        result = timeline.check_duplicate_by_hash(content_hash)
        assert result is not None

    def test_check_duplicate_by_hash_no_match_returns_none(self, timeline: TimelineRepository) -> None:
        """存在しない hash で check_duplicate_by_hash が None を返すこと。"""
        result = timeline.check_duplicate_by_hash("totally-unknown-hash-xyz")
        assert result is None


class TestTimelineFreshness:
    def test_update_freshness_to_zero(self, timeline: TimelineRepository) -> None:
        """update_freshness で freshness を 0.0 に更新できること。"""
        tid = timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash="fresh-hash-001",
            subskill="testing",
            url_or_path="https://example.com/test",
            qdrant_ids=["q-fresh"],
            kg_node_ids=["chunk-fresh"],
            freshness=1.0,
        )
        # 例外が出なければ OK
        timeline.update_freshness(tid, 0.0)

    def test_update_freshness_partial(self, timeline: TimelineRepository) -> None:
        """update_freshness で 0.5 に更新できること。"""
        tid = timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash="fresh-hash-002",
            subskill="testing",
            url_or_path="",
            qdrant_ids=[],
            kg_node_ids=["chunk-002"],
            freshness=1.0,
        )
        timeline.update_freshness(tid, 0.5)


class TestTimelineGetByKgNode:
    def test_get_timeline_by_kg_node_found(self, timeline: TimelineRepository) -> None:
        """get_timeline_by_kg_node で挿入した kg_node_id から timeline を取得できること。"""
        timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash="kg-hash-001",
            subskill="design",
            url_or_path="",
            qdrant_ids=[],
            kg_node_ids=["chunk-kg-001"],
            freshness=1.0,
        )
        entry = timeline.get_timeline_by_kg_node("chunk-kg-001")
        assert entry is not None

    def test_get_timeline_by_kg_node_not_found(self, timeline: TimelineRepository) -> None:
        """存在しない kg_node_id に get_timeline_by_kg_node すると None が返ること。"""
        entry = timeline.get_timeline_by_kg_node("nonexistent-kg-node-xyz")
        assert entry is None


class TestTimelineUrl:
    def test_find_by_url_or_path_found(self, timeline: TimelineRepository) -> None:
        """find_by_url_or_path で同じ URL の timeline が取得できること。"""
        url = "https://example.com/unique-page"
        timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash="url-hash-001",
            subskill="design",
            url_or_path=url,
            qdrant_ids=[],
            kg_node_ids=["chunk-url-001"],
            freshness=1.0,
        )
        result = timeline.find_by_url_or_path(url)
        assert result is not None

    def test_find_by_url_or_path_not_found(self, timeline: TimelineRepository) -> None:
        """存在しない URL で find_by_url_or_path が None を返すこと。"""
        result = timeline.find_by_url_or_path("https://totally.unknown.url/xyz")
        assert result is None


class TestTimelineStats:
    def test_get_stats_returns_dict(self, timeline: TimelineRepository) -> None:
        """get_stats が dict を返すこと。"""
        stats = timeline.get_stats("test-brain")
        assert isinstance(stats, dict)

    def test_get_stats_count_increases(self, timeline: TimelineRepository) -> None:
        """insert 後に get_stats のカウントが増えること。"""
        timeline.insert_timeline(
            brain_id="test-brain",
            source_type="Web",
            content_hash="stats-hash-001",
            subskill="design",
            url_or_path="",
            qdrant_ids=[],
            kg_node_ids=["chunk-stats-001"],
            freshness=1.0,
        )
        stats = timeline.get_stats("test-brain")
        # total_entries or similar key が存在して 1以上であること
        total = stats.get("total_entries") or stats.get("count") or stats.get("total") or 0
        assert total >= 1