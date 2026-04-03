"""
Phase 1 Integration Tests: Brain Hot-Swap

テスト対象: src/sba/brain/brain_manager.py (BrainHotSwapManager)
カバー範囲:
  - save: 正常保存・自動バージョンインクリメント・一時フォルダクリーンアップ
  - load: 正常読み込み・ファジー検索・無効Brainでのロールバック
  - list_brains: テンプレート/[active]/一時フォルダの除外
  - get_active_brain: 現在のBrain情報取得
  - list_brains_names / format_brain_list_table
  - スレッドセーフ（並行save）
  - BrainManagerError のエラーハンドリング
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from src.sba.brain.brain_manager import BrainHotSwapManager, BrainManagerError
from src.sba.brain.brain_package import BrainPackage, SubSkillDef


# ============================================================
# Fixtures / ヘルパー
# ============================================================

def _seed_brain(path: Path, domain: str, version: str = "1.0") -> BrainPackage:
    """指定パスにBrainを作成してdisk保存"""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    brain = BrainPackage.create_blank(path)
    brain.metadata.domain = domain
    brain.metadata.version = version
    brain.metadata.level = 1
    brain.subskill_manifest.domain = domain
    brain.subskill_manifest.subskills = [
        SubSkillDef(
            id="design",
            display_name="設計",
            description=f"{domain} design skill",
            category="development",
            priority=1,
        )
    ]
    brain.self_eval.update_subskill_score("design", 0.45)
    brain.save_all()
    return brain


@pytest.fixture
def manager(tmp_path: Path):
    """brain_bank + active を tmp_path 以下に作成してManagerを返す"""
    bank = tmp_path / "brain_bank"
    bank.mkdir()
    active = tmp_path / "active"
    _seed_brain(active, "Baseline")
    return BrainHotSwapManager(bank, active), bank, active


# ============================================================
# save() テスト
# ============================================================

class TestBrainManagerSave:

    def test_save_success(self, manager):
        """save() が成功してbrain_bankにフォルダが作成される"""
        mgr, bank, active = manager
        result = mgr.save()
        assert result["success"] is True
        assert (bank / "Baseline_v1.0").is_dir()

    def test_save_returns_expected_keys(self, manager):
        """save() の戻り値に必要なキーが含まれる"""
        mgr, bank, active = manager
        result = mgr.save()
        for key in ["success", "brain_id", "domain", "version", "saved_path", "registry"]:
            assert key in result

    def test_save_auto_increments_version_on_duplicate(self, manager):
        """同じドメイン+バージョンを2回saveするとバージョンが自動インクリメントされる"""
        mgr, bank, active = manager
        mgr.save()  # Baseline_v1.0 として保存
        result2 = mgr.save()  # バージョン衝突 → v1.1になるはず
        assert result2["version"] == "1.1"
        assert (bank / "Baseline_v1.1").is_dir()

    def test_save_no_temp_folders_remaining(self, manager):
        """save()後に brain_save_* 一時フォルダが残っていない"""
        mgr, bank, active = manager
        mgr.save()
        temp_folders = [d for d in bank.iterdir() if d.name.startswith("brain_save_")]
        assert temp_folders == []

    def test_save_with_description(self, manager):
        """save(description=...) でmetadata.jsonに save_description が記録される"""
        import json
        mgr, bank, active = manager
        mgr.save(description="Phase1テスト保存")
        saved_metadata_path = bank / "Baseline_v1.0" / "metadata.json"
        with open(saved_metadata_path, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta.get("save_description") == "Phase1テスト保存"


# ============================================================
# load() テスト
# ============================================================

class TestBrainManagerLoad:

    def test_load_replaces_active_brain(self, manager):
        """load() でactiveのBrainが指定したBrainに置き換えられる"""
        mgr, bank, active = manager
        original = _seed_brain(active, "Python")
        mgr.save()  # Python_v1.0 を保存

        # Financeに入れ替え
        _seed_brain(active, "Finance")
        mgr.save()  # Finance_v1.0 を保存

        # Pythonに戻す
        result = mgr.load("Python_v1.0")
        assert result["success"] is True

        loaded = BrainPackage.from_directory(active)
        assert loaded.metadata.domain == "Python"
        assert loaded.metadata.brain_id == original.metadata.brain_id

    def test_load_invalid_name_raises(self, manager):
        """存在しないBrain名をloadするとBrainManagerError"""
        mgr, bank, active = manager
        with pytest.raises(BrainManagerError, match="見つかりません"):
            mgr.load("NonExistent_v9.9")

    def test_load_rollback_on_corrupted_brain(self, manager):
        """壊れたBrainをloadしようとしたとき、activeが元の状態に戻る"""
        mgr, bank, active = manager
        baseline_id = BrainPackage.from_directory(active).metadata.brain_id
        mgr.save()  # Baseline_v1.0 を保存

        # 壊れたBrainを brain_bank に置く
        broken = bank / "Broken_v1.0"
        broken.mkdir()
        (broken / "metadata.json").write_text("{not valid json}", encoding="utf-8")

        with pytest.raises(BrainManagerError):
            mgr.load("Broken_v1.0")

        # activeが元に戻っているか確認
        active_brain = BrainPackage.from_directory(active)
        assert active_brain.metadata.brain_id == baseline_id

    def test_load_updates_last_loaded_at(self, manager):
        """load() 後にactiveのmetadata.jsonにlast_loaded_atが記録される"""
        import json
        mgr, bank, active = manager
        mgr.save()  # Baseline_v1.0 保存
        mgr.load("Baseline_v1.0")
        with open(active / "metadata.json", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta.get("last_loaded_at") is not None

    def test_load_fuzzy_match(self, manager):
        """不完全なBrain名でもあいまい検索でロードできる"""
        mgr, bank, active = manager
        mgr.save()  # Baseline_v1.0
        # 別のBrainに一時的に変更
        _seed_brain(active, "Finance")
        # 「base」でBaselineを見つけてload
        result = mgr.load("Baseline")
        assert result["success"] is True
        loaded = BrainPackage.from_directory(active)
        assert loaded.metadata.domain == "Baseline"


# ============================================================
# list_brains() テスト
# ============================================================

class TestBrainManagerList:

    def test_list_brains_returns_saved_brains(self, manager):
        """save() したBrainがlist_brains()に現れる"""
        mgr, bank, active = manager
        mgr.save()
        brains = mgr.list_brains()
        names = [b["name"] for b in brains]
        assert "Baseline_v1.0" in names

    def test_list_brains_excludes_active_and_template(self, tmp_path: Path):
        """[active]と_blank_templateはlist_brains()に含まれない"""
        bank = tmp_path / "bank"
        bank.mkdir()
        active = tmp_path / "active"
        _seed_brain(active, "TestDomain")

        # _blank_templateをbank内に作成
        template = bank / "_blank_template"
        template.mkdir()
        _seed_brain(template, "_blank")

        # [active]もbank内に作成（edge case）
        active_link = bank / "[active]"
        active_link.mkdir()
        _seed_brain(active_link, "ActiveDomain")

        mgr = BrainHotSwapManager(bank, active)
        names = [b["name"] for b in mgr.list_brains()]
        assert "_blank_template" not in names
        assert "[active]" not in names

    def test_list_brains_excludes_temp_folders(self, tmp_path: Path):
        """brain_save_* 一時フォルダはlist_brains()に含まれない"""
        bank = tmp_path / "bank"
        bank.mkdir()
        active = tmp_path / "active"
        _seed_brain(active, "TestDomain")

        # 一時フォルダを手動で作成（保存途中を模擬）
        temp = bank / "brain_save_abc123"
        temp.mkdir()
        _seed_brain(temp, "Temp")

        mgr = BrainHotSwapManager(bank, active)
        names = [b["name"] for b in mgr.list_brains()]
        assert "brain_save_abc123" not in names

    def test_list_brains_names_returns_strings(self, manager):
        """list_brains_names() が文字列リストを返す"""
        mgr, bank, active = manager
        mgr.save()
        names = mgr.list_brains_names()
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_format_brain_list_table_contains_header(self, manager):
        """format_brain_list_table() の出力に'Name'ヘッダーが含まれる"""
        mgr, bank, active = manager
        mgr.save()
        table = mgr.format_brain_list_table()
        assert "Name" in table
        assert "Domain" in table


# ============================================================
# get_active_brain() テスト
# ============================================================

class TestBrainManagerStatus:

    def test_get_active_brain_returns_correct_domain(self, manager):
        """get_active_brain() が現在のBrainのdomain情報を返す"""
        mgr, bank, active = manager
        info = mgr.get_active_brain()
        assert info["domain"] == "Baseline"
        assert info["name"] == "[active]"

    def test_get_active_brain_has_brain_id(self, manager):
        """get_active_brain() の戻り値に brain_id が含まれる"""
        mgr, bank, active = manager
        info = mgr.get_active_brain()
        assert info["brain_id"] is not None


# ============================================================
# 初期化エラー テスト
# ============================================================

class TestBrainManagerInit:

    def test_init_raises_on_missing_bank(self, tmp_path: Path):
        """brain_bankが存在しない場合BrainManagerError"""
        active = tmp_path / "active"
        active.mkdir()
        with pytest.raises(BrainManagerError):
            BrainHotSwapManager(tmp_path / "nonexistent_bank", active)

    def test_init_raises_on_missing_active(self, tmp_path: Path):
        """[active]が存在しない場合BrainManagerError"""
        bank = tmp_path / "bank"
        bank.mkdir()
        with pytest.raises(BrainManagerError):
            BrainHotSwapManager(bank, tmp_path / "nonexistent_active")


# ============================================================
# スレッドセーフ テスト
# ============================================================

class TestBrainManagerThreadSafety:

    def test_concurrent_save_does_not_corrupt(self, tmp_path: Path):
        """
        2スレッドが同時にsave()してもbrain_bankが壊れない。
        どちらのバージョン（v1.0 or v1.1）も正常なBrainとして保存されていること。
        """
        bank = tmp_path / "bank"
        bank.mkdir()
        active = tmp_path / "active"
        _seed_brain(active, "Concurrent")
        mgr = BrainHotSwapManager(bank, active)

        errors: list[Exception] = []

        def save_worker():
            try:
                mgr.save()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=save_worker)
        t2 = threading.Thread(target=save_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # エラーなしで完了すること
        assert errors == [], f"並行save中にエラーが発生: {errors}"

        # 保存されたBrainが少なくとも1つ以上あること
        saved = mgr.list_brains()
        assert len(saved) >= 1
