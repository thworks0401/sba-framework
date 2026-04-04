# tests/test_blank_template.py
"""
BlankTemplate の単体テスト。
_blank_template から新規 Brain のクローンが正しく生成されることを検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sba.brain.blank_template import BlankTemplate, BlankTemplateError


# ---------------------------------------------------------------------------
# フィクスチャ: 最小限の _blank_template ディレクトリを tmp に作成
# ---------------------------------------------------------------------------

@pytest.fixture
def blank_template_dir(tmp_path: Path) -> Path:
    """テスト用 _blank_template ディレクトリを生成する。"""
    template_dir = tmp_path / "_blank_template"
    template_dir.mkdir()

    # 最小限の metadata.json (blank状態)
    metadata = {
        "brain_id": "",
        "domain": "",
        "version": "1.0",
        "level": 0,
        "created_at": "",
        "last_saved_at": "",
        "description": "Blank Brain Template",
        "tags": [],
    }
    (template_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (template_dir / "self_eval.json").write_text(
        json.dumps({"subskills": {}, "overall_score": 0.0}, ensure_ascii=False), encoding="utf-8"
    )
    (template_dir / "subskill_manifest.json").write_text(
        json.dumps({"subskills": [], "domain": "", "version": "1.0"}, ensure_ascii=False), encoding="utf-8"
    )
    # 空ファイル (DB プレースホルダ)
    (template_dir / "experiment_log.db").touch()
    (template_dir / "learning_timeline.db").touch()
    (template_dir / "vector_index").mkdir()
    (template_dir / "knowledge_graph").mkdir()

    return template_dir


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

class TestBlankTemplateClone:
    """BlankTemplate.clone_to() のクローン生成テスト。"""

    def test_clone_creates_directory(self, blank_template_dir: Path, tmp_path: Path) -> None:
        """clone_to() でターゲットディレクトリが作成されること。"""
        template = BlankTemplate(blank_template_dir)
        target = tmp_path / "brain_bank" / "Python開発_v1.0"
        cloned = template.clone_to(target, domain="Python開発", version="1.0", brain_name="Python開発_v1.0")
        assert cloned.exists()
        assert cloned.is_dir()

    def test_clone_metadata_domain_set(self, blank_template_dir: Path, tmp_path: Path) -> None:
        """clone後の metadata.json に domain が正しくセットされること。"""
        template = BlankTemplate(blank_template_dir)
        target = tmp_path / "DomainBrain_v1.0"
        template.clone_to(target, domain="SomeDomain", version="1.0", brain_name="DomainBrain_v1.0")
        meta = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        assert meta["domain"] == "SomeDomain"

    def test_clone_metadata_version_set(self, blank_template_dir: Path, tmp_path: Path) -> None:
        """clone後の metadata.json に version が正しくセットされること。"""
        template = BlankTemplate(blank_template_dir)
        target = tmp_path / "VersionBrain_v2.5"
        template.clone_to(target, domain="TestDomain", version="2.5", brain_name="VersionBrain_v2.5")
        meta = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        assert meta["version"] == "2.5"

    def test_clone_brain_id_generated(self, blank_template_dir: Path, tmp_path: Path) -> None:
        """clone後の metadata.json に brain_id が自動生成されること。"""
        template = BlankTemplate(blank_template_dir)
        target = tmp_path / "IdBrain_v1.0"
        template.clone_to(target, domain="TestDomain", version="1.0", brain_name="IdBrain_v1.0")
        meta = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("brain_id"), "brain_id が空"

    def test_clone_existing_target_raises(self, blank_template_dir: Path, tmp_path: Path) -> None:
        """既存のターゲットに clone_to すると例外が出ること。"""
        template = BlankTemplate(blank_template_dir)
        target = tmp_path / "ExistingBrain_v1.0"
        target.mkdir()  # 先に作っておく
        with pytest.raises(Exception):
            template.clone_to(target, domain="X", version="1.0", brain_name="ExistingBrain_v1.0")

    def test_template_readonly_after_clone(self, blank_template_dir: Path, tmp_path: Path) -> None:
        """clone後もテンプレート元ディレクトリが変化しないこと (master copy 不変)。"""
        template = BlankTemplate(blank_template_dir)
        before_meta = (blank_template_dir / "metadata.json").read_text(encoding="utf-8")
        target = tmp_path / "ReadonlyTest_v1.0"
        template.clone_to(target, domain="TestDomain", version="1.0", brain_name="ReadonlyTest_v1.0")
        after_meta = (blank_template_dir / "metadata.json").read_text(encoding="utf-8")
        assert before_meta == after_meta, "_blank_template が変更されている"
    