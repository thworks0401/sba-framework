"""
T-6: Integration Test - 1サイクル学習フロー

テストフェーズ タスクID: T-6
対象: Step1～6 の完全フロー
シナリオ: Python開発BrainでSubSkill「設計」の学習ギャップを検出
         → リソース探索 → 取得 → 仕分け → 統合 → 評価

実行:
  pytest tests/integration/test_learning_cycle.py -v
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import json

from src.sba.learning.learning_loop import LearningLoop
from src.sba.learning.gap_detector import GapDetector, KnowledgeGapResult
from src.sba.learning.resource_finder import ResourceFinder, ResourceCandidate, SourceType
from src.sba.learning.knowledge_integrator import KnowledgeIntegrator
from src.sba.learning.self_evaluator import SelfEvaluator, SelfEvaluationResult, BrainLevel
from src.sba.subskill.classifier import SubSkillClassifier
from src.sba.experiment.experiment_engine import ExperimentEngine


@pytest.fixture
def temp_brain_path():
    """テンポラリー Brain ディレクトリを作成"""
    with tempfile.TemporaryDirectory() as tmpdir:
        brain_path = Path(tmpdir)

        # SubSkill マニフェスト
        manifest = {
            "domain": "Python開発",
            "subskills": [
                {"id": "design", "display_name": "設計", "priority": 1},
                {"id": "implementation", "display_name": "実装", "priority": 2},
                {"id": "testing", "display_name": "テスト", "priority": 3},
            ]
        }
        (brain_path / "subskill_manifest.json").write_text(json.dumps(manifest))

        # Self-Evaluation
        self_eval = {
            "design": 0.55,
            "implementation": 0.70,
            "testing": 0.65,
        }
        (brain_path / "self_eval.json").write_text(json.dumps(self_eval))

        # Experiment Log DB の初期化
        exp_log_path = brain_path / "experiment_log.db"
        exp_log_path.touch()

        yield brain_path


class TestFullLearningCycle:
    """1サイクル学習フローの統合テスト"""

    @pytest.mark.asyncio
    async def test_single_learning_cycle_with_mocks(self, temp_brain_path):
        """1サイクル学習フローが正常に実行されることを確認"""

        # モック準備
        gap_detector = AsyncMock(spec=GapDetector)
        gap_detector.detect_gap.return_value = KnowledgeGapResult(
            target_subskill="design",
            current_score=0.55,
            gap_severity="medium",
            gap_description="設計スキルが不足している",
            suggested_query="Pythonプロジェクトの設計パターン",
        )

        resource_finder = AsyncMock(spec=ResourceFinder)
        resource_finder.search_resources.return_value = [
            ResourceCandidate(
                source_type=SourceType.WEB,
                url="https://example.com/design-patterns",
                title="Design Patterns in Python",
                trust_score=0.80,
                priority=1,
            ),
            ResourceCandidate(
                source_type=SourceType.ARXIV,
                url="https://arxiv.org/abs/2101.00001",
                title="Modern Python Architecture",
                trust_score=0.90,
                priority=2,
            ),
        ]

        knowledge_integrator = AsyncMock(spec=KnowledgeIntegrator)
        knowledge_integrator.reconcile_knowledge_base.return_value = {
            "contradictions_found": 0,
            "updated_Knowledge_count": 2,
        }

        self_evaluator = AsyncMock(spec=SelfEvaluator)
        eval_result = SelfEvaluationResult(
            brain_id="python_dev_brain",
            subskill_scores={
                "design": 0.60,
                "implementation": 0.70,
                "testing": 0.65,
            },
            overall_score=0.655,
            level=BrainLevel.LV1,
        )
        self_evaluator.evaluate_all_subskills.return_value = eval_result
        self_evaluator.update_self_evaluation_file = AsyncMock()

        experiment_engine = AsyncMock(spec=ExperimentEngine)
        experiment_engine.design_experiment.return_value = MagicMock()

        # LearningLoop 初期化
        loop = LearningLoop(
            brain_id="python_dev_brain",
            brain_name="Python開発 Brain",
            active_brain_path=temp_brain_path,
            gap_detector=gap_detector,
            resource_finder=resource_finder,
            knowledge_integrator=knowledge_integrator,
            evaluator=self_evaluator,
            experiment_engine=experiment_engine,
        )

        # サイクル実行
        result = await loop.run_single_cycle()

        # 確認
        assert result.brain_id == "python_dev_brain"
        assert result.end_time != ""
        assert result.error is None

        # Step1: ギャップ検出
        assert result.step1_gap is not None
        assert result.step1_gap["target_subskill"] == "design"
        gap_detector.detect_gap.assert_called_once()

        # Step2: リソース探索
        assert result.step2_resources == 2
        resource_finder.search_resources.assert_called_once()

        # Step4: 実験設計
        experiment_engine.design_experiment.assert_called_once_with(
            "design",
            "Pythonプロジェクトの設計パターン",
            0.55,
        )

        # Step5: 知識統合
        assert result.step5_contradictions == 0
        knowledge_integrator.reconcile_knowledge_base.assert_called_once()

        # Step6: 自己評価
        assert result.step6_overall_score == 0.655
        assert result.step6_level == "Lv.1"
        self_evaluator.evaluate_all_subskills.assert_called_once()
        self_evaluator.update_self_evaluation_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_cycle_logs_structure(self, temp_brain_path):
        """サイクルログが正しく構造化されていることを確認"""

        gap_detector = AsyncMock(spec=GapDetector)
        gap_detector.detect_gap.return_value = KnowledgeGapResult(
            target_subskill="implementation",
            current_score=0.70,
            gap_severity="low",
            gap_description="実装スキル問題",
            suggested_query="test query",
        )

        loop = LearningLoop(
            brain_id="test_brain",
            brain_name="Test Brain",
            active_brain_path=temp_brain_path,
            gap_detector=gap_detector,
        )

        result = await loop.run_single_cycle()

        # ログエントリが記録されていることを確認
        assert len(result.logs) > 0
        assert any("Step1" in log for log in result.logs)
        assert result.cycle_id.startswith("test_brain_")

    @pytest.mark.asyncio
    async def test_cycle_error_recovery(self, temp_brain_path):
        """サイクル実行中のエラーから正常に回復することを確認"""

        gap_detector = AsyncMock(spec=GapDetector)
        gap_detector.detect_gap.side_effect = Exception("Simulated error")

        evaluator = AsyncMock(spec=SelfEvaluator)
        evaluator.evaluate_all_subskills.return_value = SelfEvaluationResult(
            brain_id="test_brain",
            subskill_scores={"test": 0.5},
            overall_score=0.5,
            level=BrainLevel.LV1,
        )
        evaluator.update_self_evaluation_file = AsyncMock()

        loop = LearningLoop(
            brain_id="test_brain",
            brain_name="Test Brain",
            active_brain_path=temp_brain_path,
            gap_detector=gap_detector,
            evaluator=evaluator,
        )

        result = await loop.run_single_cycle()

        # エラーが記録されていることを確認
        assert result.error is not None
        assert "Step1" in result.error
        # ただしサイクル自体は完了している
        assert result.end_time != ""

    @pytest.mark.asyncio
    async def test_knowledge_update_flow(self, temp_brain_path):
        """知識更新フローが正常に機能することを確認"""

        gap_detector = AsyncMock(spec=GapDetector)
        gap_detector.detect_gap.return_value = KnowledgeGapResult(
            target_subskill="design",
            current_score=0.55,
            gap_severity="high",
            gap_description="設計スキルが不足",
            suggested_query="query",
        )

        # 知識統合が新しい知識を報告
        knowledge_integrator = AsyncMock(spec=KnowledgeIntegrator)
        knowledge_integrator.reconcile_knowledge_base.return_value = {
            "contradictions_found": 0,
            "updated_knowledge_count": 3,
            "new_knowledge_count": 1,
        }

        # 自己評価がスコア改善を報告
        self_evaluator = AsyncMock(spec=SelfEvaluator)
        eval_result = SelfEvaluationResult(
            brain_id="test_brain",
            subskill_scores={"design": 0.60},  # 0.55 → 0.60 に改善
            overall_score=0.60,
            level=BrainLevel.LV1,
        )
        self_evaluator.evaluate_all_subskills.return_value = eval_result
        self_evaluator.update_self_evaluation_file = AsyncMock()

        loop = LearningLoop(
            brain_id="test_brain",
            brain_name="Test Brain",
            active_brain_path=temp_brain_path,
            gap_detector=gap_detector,
            knowledge_integrator=knowledge_integrator,
            evaluator=self_evaluator,
        )

        result = await loop.run_single_cycle()

        # スコアが改善されていることを確認
        assert result.step6_overall_score == 0.60


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
