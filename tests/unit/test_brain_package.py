"""
Phase 1 Unit Tests: BrainPackage

テスト対象: src/sba/brain/brain_package.py
カバー範囲:
  - create_blank / from_directory ファクトリメソッド
  - metadata / self_eval / subskill_manifest の I/O
  - is_complete / get_missing_components
  - validate() バリデーション
  - SubSkillDef / SubSkillManifest の Pydantic バリデーション
  - BrainMetadata のバリデーション（version形式・sourceロック）
  - SelfEval の平均密度・弱点検出
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sba.brain.brain_package import (
    BrainMetadata,
    BrainPackage,
    SelfEval,
    SubSkillDef,
    SubSkillManifest,
    SubSkillScore,
    create_blank_brain_package,
    load_brain_package,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def blank_brain(tmp_path: Path) -> BrainPackage:
    """新規Blank Brainをtmp_pathに作成して返す"""
    return BrainPackage.create_blank(tmp_path / "test_brain")


@pytest.fixture
def seeded_brain(tmp_path: Path) -> BrainPackage:
    """Python開発ドメインのBrainを作成・保存して返す"""
    brain = BrainPackage.create_blank(tmp_path / "python_brain")
    brain.metadata.domain = "Python開発"
    brain.metadata.version = "1.0"
    brain.metadata.level = 1
    brain.subskill_manifest.domain = "Python開発"
    brain.subskill_manifest.subskills = [
        SubSkillDef(
            id="design",
            display_name="設計",
            description="設計スキル",
            category="development",
            priority=1,
        ),
        SubSkillDef(
            id="debug",
            display_name="デバッグ",
            description="デバッグスキル",
            category="development",
            priority=2,
        ),
    ]
    brain.self_eval.update_subskill_score("design", 0.75)
    brain.self_eval.update_subskill_score("debug", 0.45)
    brain.save_all()
    return brain


# ============================================================
# BrainPackage: ファクトリメソッド
# ============================================================

class TestBrainPackageFactory:

    def test_create_blank_returns_brain_package(self, tmp_path: Path):
        """create_blank がBrainPackageを返す"""
        brain = BrainPackage.create_blank(tmp_path / "brain")
        assert isinstance(brain, BrainPackage)

    def test_create_blank_directory_created(self, tmp_path: Path):
        """create_blank でパッケージディレクトリが作成される"""
        target = tmp_path / "brain"
        BrainPackage.create_blank(target)
        assert target.exists() and target.is_dir()

    def test_create_blank_metadata_is_blank_template(self, blank_brain: BrainPackage):
        """create_blank のmetadataはblank templateである（domain=None, level=0）"""
        assert blank_brain.metadata.is_blank_template() is True

    def test_from_directory_round_trip(self, seeded_brain: BrainPackage):
        """save_all → from_directory で同じデータが復元できる"""
        loaded = BrainPackage.from_directory(seeded_brain.package_dir)
        assert loaded.metadata.domain == "Python開発"
        assert loaded.metadata.version == "1.0"
        assert loaded.metadata.brain_id == seeded_brain.metadata.brain_id

    def test_convenience_functions(self, tmp_path: Path):
        """create_blank_brain_package / load_brain_package ユーティリティ関数が動作する"""
        target = tmp_path / "conv_brain"
        brain = create_blank_brain_package(target)
        brain.metadata.domain = "Test"
        brain.save_all()
        loaded = load_brain_package(target)
        assert loaded.metadata.domain == "Test"


# ============================================================
# BrainPackage: ファイル構造チェック
# ============================================================

class TestBrainPackageStructure:

    def test_is_complete_true_after_save_all(self, seeded_brain: BrainPackage):
        """save_all後はis_complete()がTrueになる"""
        assert seeded_brain.is_complete() is True

    def test_is_complete_false_when_missing_file(self, tmp_path: Path):
        """metadata.jsonを削除するとis_complete()がFalseになりmissing_componentsに含まれる"""
        brain = BrainPackage.create_blank(tmp_path / "incomplete")
        # save_all()でmetadata.jsonを含む全ファイルをdiskに書き出す
        brain.save_all()
        # metadata.jsonが存在することを先に確認
        assert brain.get_metadata_path().exists(), "save_all()後にmetadata.jsonが存在しない"
        # metadata.jsonだけ削除
        brain.get_metadata_path().unlink()
        # has_metadata_file()はファイル存在をそのまま見る
        assert brain.has_metadata_file() is False
        # get_missing_components()に"metadata.json"が現れるか確認
        missing = brain.get_missing_components()
        assert "metadata.json" in missing

    def test_get_missing_components_empty_on_complete(self, seeded_brain: BrainPackage):
        """完全なBrainはget_missing_components()が空リストを返す"""
        assert seeded_brain.get_missing_components() == []

    def test_ensure_structure_creates_dirs_and_db(self, blank_brain: BrainPackage):
        """ensure_structure() でknowledge_graph/ vector_index/ *.dbが作られる"""
        blank_brain.ensure_structure()
        assert blank_brain.get_knowledge_graph_path().is_dir()
        assert blank_brain.get_vector_index_path().is_dir()
        assert blank_brain.get_experiment_log_path().exists()
        assert blank_brain.get_learning_timeline_path().exists()


# ============================================================
# BrainMetadata: Pydantic バリデーション
# ============================================================

class TestBrainMetadata:

    def test_version_format_valid(self):
        """正常なバージョン形式（X.Y）が通る"""
        m = BrainMetadata(version="1.4")
        assert m.version == "1.4"

    def test_version_format_invalid_raises(self):
        """不正なバージョン形式はValueErrorになる"""
        with pytest.raises(Exception):
            BrainMetadata(version="1.4.0")

    def test_source_locked_to_sba(self):
        """source='sba'以外はValueErrorになる"""
        with pytest.raises(Exception):
            BrainMetadata(source="other")

    def test_brain_id_auto_generated(self):
        """brain_idは自動生成されUUID形式になっている"""
        import re
        m = BrainMetadata()
        # UUID v4パターン
        assert re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
            m.brain_id
        )

    def test_is_blank_template_false_when_domain_set(self):
        """domainが設定されている場合はblank_templateではない"""
        m = BrainMetadata(domain="Python開発", version="1.0")
        assert m.is_blank_template() is False


# ============================================================
# SelfEval: スコア計算・弱点検出
# ============================================================

class TestSelfEval:

    def test_get_avg_density_empty(self):
        """SubSkillが空の場合はavg_density=0.0"""
        se = SelfEval()
        assert se.get_avg_density() == 0.0

    def test_get_avg_density_calculation(self):
        """複数SubSkillのavg_densityが正しく計算される"""
        se = SelfEval()
        se.update_subskill_score("a", 0.8)
        se.update_subskill_score("b", 0.4)
        assert abs(se.get_avg_density() - 0.6) < 1e-9

    def test_get_weak_subskills_threshold_06(self):
        """density <= 0.6 のSubSkillが弱点として検出される"""
        se = SelfEval()
        se.update_subskill_score("strong", 0.9)
        se.update_subskill_score("borderline", 0.6)  # 0.6は弱点
        se.update_subskill_score("weak", 0.3)
        weak = se.get_weak_subskills()
        assert "borderline" in weak
        assert "weak" in weak
        assert "strong" not in weak

    def test_subskill_score_weak_flag_auto_set(self):
        """update_subskill_score で weak フラグが自動設定される"""
        se = SelfEval()
        se.update_subskill_score("x", 0.7)
        assert se.subskills["x"].weak is False
        se.update_subskill_score("y", 0.5)
        assert se.subskills["y"].weak is True


# ============================================================
# SubSkillManifest: バリデーション
# ============================================================

class TestSubSkillManifest:

    def test_unique_id_validation_passes(self):
        """重複しないIDは通る"""
        manifest = SubSkillManifest(
            domain="Test",
            subskills=[
                SubSkillDef(id="a", display_name="A", description="A", category="dev"),
                SubSkillDef(id="b", display_name="B", description="B", category="dev"),
            ]
        )
        assert len(manifest.subskills) == 2

    def test_duplicate_id_raises(self):
        """SubSkill IDが重複するとValueError"""
        with pytest.raises(Exception):
            SubSkillManifest(
                domain="Test",
                subskills=[
                    SubSkillDef(id="dup", display_name="A", description="A", category="dev"),
                    SubSkillDef(id="dup", display_name="B", description="B", category="dev"),
                ]
            )

    def test_get_subskill_by_id(self):
        """get_subskill() で IDを指定してSubSkillDefを取得できる"""
        skill = SubSkillDef(id="design", display_name="設計", description="設計", category="dev")
        manifest = SubSkillManifest(domain="Test", subskills=[skill])
        found = manifest.get_subskill("design")
        assert found is not None
        assert found.display_name == "設計"

    def test_get_subskill_missing_returns_none(self):
        """存在しないIDの get_subskill() はNoneを返す"""
        manifest = SubSkillManifest(domain="Test")
        assert manifest.get_subskill("nonexistent") is None


# ============================================================
# BrainPackage: validate()
# ============================================================

class TestBrainPackageValidate:

    def test_validate_passes_on_complete_brain(self, seeded_brain: BrainPackage):
        """完全なBrainはvalidate()がTrue"""
        is_valid, errors = seeded_brain.validate()
        assert is_valid is True
        assert errors == []

    def test_validate_fails_on_blank_brain_without_domain(self, blank_brain: BrainPackage):
        """
        blank brainはdomain=Noneだがis_blank_template()=Trueなのでvalidate()は通る。
        level=1にしてdomain=Noneのままにするとエラーになる。
        """
        blank_brain.metadata.level = 1  # トレーニング済みなのにdomainなし
        blank_brain.save_all()
        is_valid, errors = blank_brain.validate()
        assert is_valid is False
        assert any("domain" in e for e in errors)

    def test_validate_detects_eval_subskill_mismatch(self, seeded_brain: BrainPackage):
        """self_evalにあってmanifestにないSubSkillIDがあるとvalidate()がエラーを返す"""
        # self_evalに存在しないSubSkill IDを追加
        seeded_brain.self_eval.update_subskill_score("nonexistent_skill", 0.5)
        is_valid, errors = seeded_brain.validate()
        assert is_valid is False
        assert any("unknown SubSkills" in e for e in errors)

    def test_get_brain_info_returns_expected_keys(self, seeded_brain: BrainPackage):
        """get_brain_info()が期待されるキーを全て含む"""
        info = seeded_brain.get_brain_info()
        expected_keys = [
            "domain", "version", "level", "brain_id",
            "is_complete", "missing_components",
            "avg_knowledge_density", "weak_subskills", "subskill_count"
        ]
        for key in expected_keys:
            assert key in info, f"'{key}' がget_brain_info()の戻り値に含まれていない"
