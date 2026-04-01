"""
Phase 4 統合テスト（簡易版）: 自律学習ループ基本構成テスト

テスト観点:
  - SubSkill分類エンジンの基本インターフェース
  - ギャップ検出のロジック
  - リソース検出の種別分類
  - Learning Loop の構造
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# モック Ollama パッケージ（インポートエラー回避）
import sys
sys.modules["ollama"] = MagicMock()

from src.sba.subskill.classifier import SubSkillClassifier
from src.sba.learning.gap_detector import GapDetector
from src.sba.learning.resource_finder import ResourceFinder, SourceType, ResourceCandidate
from src.sba.sources.web_fetcher import WebCleaner
from src.sba.learning.self_evaluator import SelfEvaluator, BrainLevel


# ---- Fixtures ----

@pytest.fixture
def mock_manifest():
    """SubSkill定義モック"""
    return {
        "domain": "Python開発",
        "subskills": [
            {"id": "design", "display_name": "設計", "description": "アーキテクチャ", "aliases": []},
            {"id": "impl", "display_name": "実装", "description": "コーディング", "aliases": []},
        ]
    }


@pytest.fixture
def mock_eval_json(tmp_path):
    """self_eval.json モック"""
    eval_path = tmp_path / "self_eval.json"
    with open(eval_path, "w") as f:
        json.dump({"scores": {"design": 0.75, "impl": 0.45}}, f)
    return eval_path


# ---- Tests ----

def test_subskill_classifier_init(mock_manifest):
    """SubSkillClassifier 初期化テスト"""
    classifier = SubSkillClassifier("TestBrain", mock_manifest)
    assert len(classifier.subskill_ids) == 2
    assert classifier.subskill_names["design"] == "設計"
    print("✓ SubSkillClassifier初期化成功")


def test_subskill_manifest_parsing(mock_manifest):
    """SubSkill定義パース テスト"""
    classifier = SubSkillClassifier("TestBrain", mock_manifest)
    assert "design" in classifier.subskill_ids
    assert classifier.get_subskill_display_name("design") == "設計"
    print("✓ SubSkill定義パース成功")


def test_gap_detector_init():
    """GapDetector 初期化テスト"""
    detector = GapDetector("Python開発Brain")
    assert detector.brain_name == "Python開発Brain"
    assert detector.WEAK_THRESHOLD == 0.6
    print("✓ GapDetector初期化成功")


def test_gap_detector_load_eval(mock_eval_json):
    """self_eval.json 読み込みテスト"""
    detector = GapDetector("TestBrain")
    scores = detector.load_self_evaluation(mock_eval_json)
    assert scores["design"] == 0.75
    assert scores["impl"] == 0.45
    print("✓ self_eval.json 読み込み成功")


def test_gap_detector_priority_queue(mock_eval_json):
    """優先度キュー計算テスト"""
    detector = GapDetector("TestBrain")
    queue = detector.get_priority_queue(mock_eval_json, max_items=2)
    # impl がスコア低いため先頭
    assert queue[0][0] == "impl"
    assert queue[0][1] == 0.45
    print("✓ 優先度キュー計算成功")


def test_resource_finder_init():
    """ResourceFinder 初期化テスト"""
    finder = ResourceFinder("TestBrain")
    assert finder.brain_name == "TestBrain"
    print("✓ ResourceFinder初期化成功")


def test_resource_priority_tech_brain():
    """Tech系Brain のソース優先度テスト"""
    finder = ResourceFinder("TestBrain")
    tech_sources = finder._get_source_priority(is_tech_brain=True)
    assert tech_sources[0] == SourceType.GITHUB  # Github 優先
    print("✓ Tech系ソース優先度指定成功")


def test_resource_priority_general_brain():
    """一般Brain のソース優先度テスト"""
    finder = ResourceFinder("TestBrain")
    general_sources = finder._get_source_priority(is_tech_brain=False)
    assert general_sources[0] == SourceType.WIKIPEDIA  # Wikipedia 優先
    print("✓ 一般ソース優先度指定成功")


def test_resource_candidate():
    """ResourceCandidate 生成テスト"""
    candidate = ResourceCandidate(
        url="https://example.com",
        source_type=SourceType.WEB,
        title="Example",
        initial_trust_score=0.80,
    )
    assert candidate.url == "https://example.com"
    assert candidate.initial_trust_score == 0.80
    print("✓ リソース候補生成成功")


def test_web_cleaner_remove_tags():
    """HTMLタグ除去テスト"""
    text = "<p>Hello <b>World</b></p>"
    cleaned = WebCleaner.clean_text(text)
    assert "<" not in cleaned
    assert "Hello" in cleaned
    print("✓ HTMLタグ除去成功")


def test_web_cleaner_normalize():
    """テキスト正規化テスト"""
    text = "Line1\n\n\n\nLine2"
    normalized = WebCleaner.normalize_whitespace(text)
    assert normalized.count("\n\n") <= 1  # 複数改行が削除
    print("✓ テキスト正規化成功")


def test_self_evaluator_level_determination():
    """Lv判定テスト"""
    evaluator = SelfEvaluator("TestBrain", "test_001")
    assert evaluator._determine_level(0.75) == BrainLevel.LV1
    assert evaluator._determine_level(0.96) == BrainLevel.LV2
    assert evaluator._determine_level(0.99) == BrainLevel.LV3
    print("✓ Lv判定成功")


def test_learning_loop_structure(tmp_path, mock_manifest):
    """LearningLoop 構造テスト"""
    from src.sba.learning.learning_loop import LearningLoop

    brain_path = tmp_path / "brain"
    brain_path.mkdir()
    (brain_path / "subskill_manifest.json").write_text(json.dumps(mock_manifest))

    loop = LearningLoop(
        brain_id="test_001",
        brain_name="TestBrain",
        active_brain_path=brain_path,
    )

    assert loop.brain_id == "test_001"
    assert loop.is_running == False
    print("✓ LearningLoop構造テスト成功")


# ---- Phase 3 連携テスト ----

def test_phase3_integration_engine_router_available():
    """Phase 3 EngineRouter が利用可能か確認"""
    try:
        from src.sba.inference.engine_router import EngineRouter, TaskType
        assert TaskType.REASONING is not None
        print("✓ Phase 3 EngineRouter 統合確認")
    except ImportError as e:
        pytest.skip(f"EngineRouter import failed: {e}")


def test_phase3_integration_vram_guard_available():
    """Phase 3 VRAMGuard が利用可能か確認"""
    try:
        from src.sba.utils.vram_guard import get_global_vram_guard, ModelType
        assert ModelType.TIER1 is not None
        print("✓ Phase 3 VRAMGuard 統合確認")
    except ImportError as e:
        pytest.skip(f"VRAMGuard import failed: {e}")


# ---- Summary ----

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
