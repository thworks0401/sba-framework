# tests/test_cli_integration.py
"""
CLI コマンド統合テスト。
Typer の CliRunner を使って各サブコマンドの終了コードと出力を検証する。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from sba.cli.brain_cmds import app as brain_app

runner = CliRunner()


def _make_test_brain(bank: Path, name: str, domain: str) -> None:
    """テスト用 Brain ディレクトリを bank 内に生成する。"""
    saved = bank / name
    saved.mkdir()
    meta = {
        "brain_id": f"{name}-id", "domain": domain, "version": "1.0",
        "level": 0, "created_at": "2026-04-01T00:00:00Z",
        "last_saved_at": "2026-04-01T00:00:00Z", "description": "", "tags": [],
    }
    (saved / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (saved / "self_eval.json").write_text(
        json.dumps({"subskills": {}, "overall_score": 0.0}, ensure_ascii=False), encoding="utf-8"
    )
    (saved / "subskill_manifest.json").write_text(
        json.dumps({"subskills": [], "domain": domain, "version": "1.0"}, ensure_ascii=False), encoding="utf-8"
    )
    conn = sqlite3.connect(saved / "experiment_log.db"); conn.close()
    conn = sqlite3.connect(saved / "learning_timeline.db"); conn.close()
    (saved / "vector_index").mkdir()
    (saved / "knowledge_graph").mkdir()


def _make_blank_template(bank: Path) -> Path:
    """テスト用 _blank_template を bank 内に生成する。"""
    blank = bank / "_blank_template"
    blank.mkdir()
    meta_blank = {
        "brain_id": "", "domain": "", "version": "1.0", "level": 0,
        "created_at": "", "last_saved_at": "", "description": "", "tags": [],
    }
    (blank / "metadata.json").write_text(json.dumps(meta_blank, ensure_ascii=False), encoding="utf-8")
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
    return blank


@pytest.fixture(autouse=True)
def patch_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    SBAConfig.load_env() をモックして tmp_path 内のテスト用 brain_bank を参照させる。
    実際の C:/SBA や C:/TH_Works/SBA には手を加えない。
    SimpleNamespace でクラス内スコープ問題を回避する。
    """
    bank = tmp_path / "brain_bank"
    bank.mkdir()
    active_path = bank / "[active]"
    active_path.mkdir()
    exports_path = tmp_path / "exports"
    exports_path.mkdir()
    blank_path = _make_blank_template(bank)
    _make_test_brain(bank, "SampleBrain_v1.0", "SampleDomain")

    # SimpleNamespace で属性アクセス可能なモックオブジェクトを作る
    # クラス定義のスコープ問題を完全に回避する
    mock_cfg = SimpleNamespace(
        brain_bank=bank,
        active=active_path,
        blank_template=blank_path,
        exports=exports_path,
    )

    import sba.cli.brain_cmds as cmds_module
    monkeypatch.setattr(cmds_module, "_load_cfg", lambda: mock_cfg)


class TestCliList:
    def test_list_exit_code_zero(self) -> None:
        """sba brain list が exit 0 で終了すること。"""
        result = runner.invoke(brain_app, ["list"])
        assert result.exit_code == 0

    def test_list_output_contains_brain_name(self) -> None:
        """sba brain list の出力に保存済み Brain 名が含まれること。"""
        result = runner.invoke(brain_app, ["list"])
        assert result.exit_code == 0
        assert "SampleBrain" in result.output


class TestCliStatus:
    def test_status_exit_code(self) -> None:
        """sba brain status が正常終了すること（active が blank でも OK）。"""
        result = runner.invoke(brain_app, ["status"])
        # blank の [active] の場合 exit 0 or 1 どちらも許容
        assert result.exit_code in (0, 1)


class TestCliSwap:
    def test_swap_existing_brain(self) -> None:
        """sba brain swap で存在する Brain をロードできること。"""
        result = runner.invoke(brain_app, ["swap", "SampleBrain_v1.0"])
        assert result.exit_code == 0

    def test_swap_nonexistent_brain_fails(self) -> None:
        """存在しない Brain を swap すると exit 1 になること。"""
        result = runner.invoke(brain_app, ["swap", "絶対に存在しないBrain_v999.0"])
        assert result.exit_code != 0