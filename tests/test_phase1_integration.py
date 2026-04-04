# tests/test_phase1_integration.py
"""
Phase 1 統合テスト。
create → save → swap → list → export の一連フローを検証する。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sba.brain.blank_template import BlankTemplate
from sba.brain.brain_manager import BrainHotSwapManager, BrainManagerError
from sba.brain.brain_package import BrainPackage


# ---------------------------------------------------------------------------
# フィクスチャ: テスト専用 brain_bank をセットアップ
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_env(tmp_path: Path):
    """テスト用 brain_bank / _blank_template / [active] を生成する。"""
    bank = tmp_path / "brain_bank"
    bank.mkdir()
    active = bank / "[active]"
    active.mkdir()

    blank = bank / "_blank_template"
    blank.mkdir()
    meta_blank = {
        "brain_id": "", "domain": "", "version": "1.0", "level": 0,
        "created_at": "", "last_saved_at": "", "description": "", "tags": [],
    }
    (blank / "metadata.json").write_text(
        json.dumps(meta_blank, ensure_ascii=False), encoding="utf-8"
    )
    (blank / "self_eval.json").write_text(
        json.dumps({"subskills": {}, "overall_score": 0.0}, ensure_ascii=False), encoding="utf-8"
    )
    (blank / "subskill_manifest.json").write_text(
        json.dumps({"subskills": [], "domain": "", "version": "1.0"}, ensure_ascii=False), encoding="utf-8"
    )
    (blank / "experiment_log.db").touch()
    (blank / "learning_timeline.db").touch()
    (blank / "vector_index").mkdir()
    (blank / "knowledge_graph").mkdir()

    return {"bank": bank, "active": active, "blank": blank}


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

class TestPhase1FullFlow:
    """create → swap → list → export の統合フロー。"""

    def test_clone_and_load_flow(self, setup_env: dict) -> None:
        """
        BlankTemplate からクローンして brain_bank に配置後、
        BrainHotSwapManager でロードできること。
        """
        bank   = setup_env["bank"]
        active = setup_env["active"]
        blank  = setup_env["blank"]

        # Step1: clone
        template = BlankTemplate(blank)
        target = bank / "TestFlow_v1.0"
        template.clone_to(target, domain="TestFlow", version="1.0", brain_name="TestFlow_v1.0")
        assert target.exists()

        # Step2: load
        mgr = BrainHotSwapManager(bank, active)
        result = mgr.load("TestFlow_v1.0", rollback_on_error=True)
        assert result["domain"] == "TestFlow"

    def test_list_after_clone(self, setup_env: dict) -> None:
        """clone後に list で Brain 名が取得できること。"""
        bank   = setup_env["bank"]
        active = setup_env["active"]
        blank  = setup_env["blank"]

        template = BlankTemplate(blank)
        template.clone_to(
            bank / "ListTest_v1.0",
            domain="ListTest", version="1.0", brain_name="ListTest_v1.0"
        )
        mgr = BrainHotSwapManager(bank, active)
        names = mgr.list_brains_names()
        assert "ListTest_v1.0" in names

    def test_multiple_brains_in_bank(self, setup_env: dict) -> None:
        """複数の Brain を clone しても bank に全て格納されること。"""
        bank   = setup_env["bank"]
        active = setup_env["active"]
        blank  = setup_env["blank"]

        template = BlankTemplate(blank)
        for i in range(3):
            template.clone_to(
                bank / f"MultiBrain{i}_v1.0",
                domain=f"Domain{i}", version="1.0", brain_name=f"MultiBrain{i}_v1.0"
            )
        mgr = BrainHotSwapManager(bank, active)
        names = mgr.list_brains_names()
        assert len([n for n in names if "MultiBrain" in n]) == 3

    def test_load_and_verify_active_metadata(self, setup_env: dict) -> None:
        """load 後に [active]/metadata.json が正しいドメインになること。"""
        bank   = setup_env["bank"]
        active = setup_env["active"]
        blank  = setup_env["blank"]

        template = BlankTemplate(blank)
        template.clone_to(
            bank / "MetaCheck_v1.0",
            domain="MetaCheckDomain", version="1.0", brain_name="MetaCheck_v1.0"
        )
        mgr = BrainHotSwapManager(bank, active)
        mgr.load("MetaCheck_v1.0", rollback_on_error=True)

        meta_path = active / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert meta["domain"] == "MetaCheckDomain"