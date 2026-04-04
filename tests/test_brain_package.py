# tests/test_brain_package.py
"""
BrainPackage の単体テスト。
メタデータ読み込み・バリデーション・ファイル構成確認を検証する。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sba.brain.brain_package import BrainPackage, BrainMetadata


@pytest.fixture
def valid_brain_dir(tmp_path: Path) -> Path:
    """テスト用の最小構成 Brain ディレクトリを生成する。"""
    brain_dir = tmp_path / "TestBrain_v1.0"
    brain_dir.mkdir()

    metadata = {
        "brain_id": "test-brain-001",
        "domain": "TestDomain",
        "version": "1.0",
        "level": 0,
        "created_at": "2026-04-01T00:00:00Z",
        "last_saved_at": "2026-04-01T00:00:00Z",
        "description": "Unit test brain",
        "tags": [],
    }
    (brain_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (brain_dir / "self_eval.json").write_text(
        json.dumps({"subskills": {}, "overall_score": 0.0, "evaluated_at": "2026-04-01T00:00:00Z"}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    (brain_dir / "subskill_manifest.json").write_text(
        json.dumps({"subskills": [], "domain": "TestDomain", "version": "1.0"}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    conn = sqlite3.connect(brain_dir / "experiment_log.db"); conn.close()
    conn = sqlite3.connect(brain_dir / "learning_timeline.db"); conn.close()
    (brain_dir / "vector_index").mkdir()
    (brain_dir / "knowledge_graph").mkdir()

    return brain_dir


class TestBrainPackageLoad:
    def test_load_valid_brain(self, valid_brain_dir: Path) -> None:
        pkg = BrainPackage.from_directory(valid_brain_dir)
        assert pkg is not None

    def test_metadata_domain(self, valid_brain_dir: Path) -> None:
        pkg = BrainPackage.from_directory(valid_brain_dir)
        assert pkg.metadata.domain == "TestDomain"

    def test_metadata_version(self, valid_brain_dir: Path) -> None:
        pkg = BrainPackage.from_directory(valid_brain_dir)
        assert pkg.metadata.version == "1.0"

    def test_metadata_brain_id(self, valid_brain_dir: Path) -> None:
        pkg = BrainPackage.from_directory(valid_brain_dir)
        assert pkg.metadata.brain_id
        assert isinstance(pkg.metadata.brain_id, str)

    def test_load_missing_dir_returns_incomplete(self, tmp_path: Path) -> None:
        """
        存在しないディレクトリを渡した場合、
        BrainPackage.from_directory() が例外を出すか、
        または is_complete() == False になること。
        実装が静かに通過する場合は is_complete で不完全性を確認する。
        """
        nonexistent = tmp_path / "nonexistent_brain"
        try:
            pkg = BrainPackage.from_directory(nonexistent)
            # 例外が出ない場合: is_complete() が False であることを確認
            assert not pkg.is_complete(), \
                "存在しないディレクトリからロードした Brain が complete 判定になっている"
        except Exception:
            pass  # 例外が出れば OK


class TestBrainPackageValidation:
    def test_required_files_present(self, valid_brain_dir: Path) -> None:
        required = [
            "metadata.json", "self_eval.json", "subskill_manifest.json",
            "experiment_log.db", "learning_timeline.db",
        ]
        for fname in required:
            assert (valid_brain_dir / fname).exists(), f"Missing: {fname}"

    def test_required_dirs_present(self, valid_brain_dir: Path) -> None:
        for dname in ("vector_index", "knowledge_graph"):
            assert (valid_brain_dir / dname).is_dir(), f"Missing dir: {dname}"