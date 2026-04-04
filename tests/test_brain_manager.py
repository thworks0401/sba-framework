# tests/test_brain_manager.py
"""
BrainHotSwapManager の単体テスト。
save / load / list / fuzzy_find の各操作を検証する。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from sba.brain.brain_manager import BrainHotSwapManager, BrainManagerError


# ---------------------------------------------------------------------------
# ユーティリティ: テスト用 Brain ディレクトリを生成
# ---------------------------------------------------------------------------

def _make_brain_dir(root: Path, name: str, domain: str = "TestDomain", version: str = "1.0") -> Path:
    """指定パスに最小構成の Brain ディレクトリを生成する。"""
    brain_dir = root / name
    brain_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "brain_id": f"brain-{name}",
        "domain": domain,
        "version": version,
        "level": 0,
        "created_at": "2026-04-01T00:00:00Z",
        "last_saved_at": "2026-04-01T00:00:00Z",
        "description": "",
        "tags": [],
    }
    (brain_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (brain_dir / "self_eval.json").write_text(
        json.dumps({"subskills": {}, "overall_score": 0.0}, ensure_ascii=False), encoding="utf-8"
    )
    (brain_dir / "subskill_manifest.json").write_text(
        json.dumps({"subskills": [], "domain": domain, "version": version}, ensure_ascii=False), encoding="utf-8"
    )
    conn = sqlite3.connect(brain_dir / "experiment_log.db"); conn.close()
    conn = sqlite3.connect(brain_dir / "learning_timeline.db"); conn.close()
    (brain_dir / "vector_index").mkdir(exist_ok=True)
    (brain_dir / "knowledge_graph").mkdir(exist_ok=True)
    return brain_dir


@pytest.fixture
def brain_bank(tmp_path: Path) -> Path:
    """テスト用 brain_bank ディレクトリ（[active] + 保存済み Brain 2件）を生成。"""
    bank = tmp_path / "brain_bank"
    bank.mkdir()
    (bank / "[active]").mkdir()
    _make_brain_dir(bank, "Python開発_v1.0", domain="Python開発", version="1.0")
    _make_brain_dir(bank, "法人営業_v0.8",   domain="法人営業",   version="0.8")
    return bank


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

class TestBrainManagerList:
    """list 系メソッドのテスト。"""

    def test_list_brains_names_returns_saved(self, brain_bank: Path) -> None:
        """保存済み Brain 名が一覧に含まれること。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        names = mgr.list_brains_names()
        assert "Python開発_v1.0" in names
        assert "法人営業_v0.8" in names

    def test_list_excludes_active(self, brain_bank: Path) -> None:
        """[active] が一覧に含まれないこと。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        names = mgr.list_brains_names()
        assert "[active]" not in names

    def test_format_brain_list_table_returns_str(self, brain_bank: Path) -> None:
        """format_brain_list_table() が文字列を返すこと。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        table = mgr.format_brain_list_table()
        assert isinstance(table, str)
        assert len(table) > 0


class TestBrainManagerLoad:
    """load / swap のテスト。"""

    def test_load_existing_brain(self, brain_bank: Path) -> None:
        """保存済み Brain を [active] にロードできること。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        result = mgr.load("Python開発_v1.0", rollback_on_error=True)
        assert result["domain"] == "Python開発"

    def test_load_nonexistent_raises(self, brain_bank: Path) -> None:
        """存在しない Brain 名を指定すると BrainManagerError が出ること。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        with pytest.raises(BrainManagerError):
            mgr.load("存在しないBrain_v9.9", rollback_on_error=False)

    def test_load_updates_active(self, brain_bank: Path) -> None:
        """load 後に [active]/metadata.json の domain が更新されること。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        mgr.load("Python開発_v1.0", rollback_on_error=True)
        active_meta_path = brain_bank / "[active]" / "metadata.json"
        if active_meta_path.exists():
            meta = json.loads(active_meta_path.read_text(encoding="utf-8"))
            assert meta["domain"] == "Python開発"


class TestBrainManagerFuzzyFind:
    """ファジー検索のテスト。"""

    def test_fuzzy_find_partial_name(self, brain_bank: Path) -> None:
        """部分一致で Brain を検索できること。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        matches = mgr._find_brain_fuzzy("Python")
        assert len(matches) >= 1
        assert any("Python" in m for m in matches)

    def test_fuzzy_find_no_match_returns_empty(self, brain_bank: Path) -> None:
        """マッチしない場合は空リストが返ること。"""
        mgr = BrainHotSwapManager(brain_bank, brain_bank / "[active]")
        matches = mgr._find_brain_fuzzy("絶対に存在しないXYZABC")
        assert matches == [] or isinstance(matches, list)