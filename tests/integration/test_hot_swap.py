"""
T-5: Brain Hot-Swap integration tests.
"""

from __future__ import annotations

import shutil

import pytest

from src.sba.brain.brain_manager import BrainHotSwapManager, BrainManagerError
from src.sba.brain.brain_package import BrainPackage, SubSkillDef


def _seed_brain(path, domain: str, version: str = "1.0") -> BrainPackage:
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


def test_hot_swap_round_trip(tmp_path):
    brain_bank = tmp_path / "brain_bank"
    brain_bank.mkdir()
    active = tmp_path / "active"

    original = _seed_brain(active, "Python")
    manager = BrainHotSwapManager(brain_bank, active)

    save_python = manager.save()
    assert save_python["success"] is True
    assert (brain_bank / "Python_v1.0").exists()

    _seed_brain(active, "Finance")
    save_finance = manager.save()
    assert save_finance["success"] is True
    assert (brain_bank / "Finance_v1.0").exists()

    load_result = manager.load("Python_v1.0")
    assert load_result["success"] is True

    active_brain = BrainPackage.from_directory(active)
    assert active_brain.metadata.domain == "Python"
    assert active_brain.metadata.brain_id == original.metadata.brain_id


def test_hot_swap_keeps_active_brain_when_target_is_invalid(tmp_path):
    brain_bank = tmp_path / "brain_bank"
    brain_bank.mkdir()
    active = tmp_path / "active"

    baseline = _seed_brain(active, "Stable")
    manager = BrainHotSwapManager(brain_bank, active)
    manager.save()

    corrupted = brain_bank / "Broken_v1.0"
    corrupted.mkdir()
    (corrupted / "metadata.json").write_text("{not valid json}", encoding="utf-8")

    with pytest.raises(BrainManagerError):
        manager.load("Broken_v1.0")

    active_brain = BrainPackage.from_directory(active)
    assert active_brain.metadata.domain == "Stable"
    assert active_brain.metadata.brain_id == baseline.metadata.brain_id

