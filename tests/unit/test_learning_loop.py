"""
T-3: Learning Loop - ユニットテスト

テストフェーズ タスクID: T-3
対象: Step1～6の各コンポーネント
方針: モックを使って各Step の動作を単体テスト

実行:
  pytest tests/unit/test_learning_loop.py -v
"""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import json
import tempfile

from src.sba.learning.learning_loop import LearningLoop, LearningCycleResult
from src.sba.learning.gap_detector import GapDetector, KnowledgeGapResult
from src.sba.learning.resource_finder import ResourceFinder, ResourceCandidate
from src.sba.learning.knowledge_integrator import KnowledgeIntegrator
from src.sba.learning.self_evaluator import SelfEvaluator, SelfEvaluationResult, BrainLevel
from src.sba.experiment.experiment_engine import ExperimentEngine


class TestLearningLoopStep1:
    """Step1: ギャップ検出のテスト"""

    @pytest.mark.asyncio
    async def test_gap_detection_success(self):
        """ギャップ検出が正常に実行できることを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            # SubSkill マニフェスト作成
            manifest = {
                "domain": "Python開発",
                "subskills": [
                    {"id": "design", "display_name": "設計"},
                    {"id": "implementation", "display_name": "実装"},
                ]
            }
            manifest_path = brain_path / "subskill_manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Self-Evaluation 作成
            self_eval = {
                "design": 0.7,
                "implementation": 0.5,
            }
            eval_path = brain_path / "self_eval.json"
            eval_path.write_text(json.dumps(self_eval))

            # モック作成
            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.return_value = KnowledgeGapResult(
                target_subskill="implementation",
                current_score=0.5,
                gap_severity="high",
                gap_description="実装スキルが不足している",
                suggested_query="Pythonのコード実装方法",
            )

            # LearningLoop 実行
            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                gap_detector=gap_detector,
            )

            result = await loop.run_single_cycle()

            # 確認
            assert result.step1_gap is not None
            assert result.step1_gap["target_subskill"] == "implementation"
            assert result.step1_gap["current_score"] == 0.5
            gap_detector.detect_gap.assert_called_once()

    @pytest.mark.asyncio
    async def test_gap_detection_failure_handling(self):
        """ギャップ検出失敗時のエラーハンドリングを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            # マニフェスト作成
            manifest = {"domain": "Test", "subskills": []}
            manifest_path = brain_path / "subskill_manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # モック（エラー発生）
            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.side_effect = Exception("Mock error")

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                gap_detector=gap_detector,
            )

            result = await loop.run_single_cycle()

            # エラーが記録されていることを確認
            assert result.error is not None
            assert "Step1" in result.error


class TestLearningLoopStep4:
    """Step4: 自己実験のテスト"""

    @pytest.mark.asyncio
    async def test_experiment_design_called(self):
        """実験設計が正常に呼び出されることを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            # マニフェスト + Self-Eval 作成
            manifest = {
                "domain": "Python開発",
                "subskills": [{"id": "design", "display_name": "設計"}]
            }
            manifest_path = brain_path / "subskill_manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            self_eval = {"design": 0.5}
            eval_path = brain_path / "self_eval.json"
            eval_path.write_text(json.dumps(self_eval))

            # モック
            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.return_value = KnowledgeGapResult(
                target_subskill="design",
                current_score=0.5,
                gap_severity="high",
                gap_description="設計スキルが不足している",
                suggested_query="設計パターン",
            )

            experiment_engine = AsyncMock(spec=ExperimentEngine)
            experiment_engine.design_experiment.return_value = MagicMock()

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                gap_detector=gap_detector,
                experiment_engine=experiment_engine,
            )

            result = await loop.run_single_cycle()

            # 実験設計が呼び出されたことを確認
            experiment_engine.design_experiment.assert_called_once()


class TestLearningLoopStep6:
    """Step6: 自己評価のテスト"""

    @pytest.mark.asyncio
    async def test_self_evaluation_updated(self):
        """自己評価が正常に更新されることを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            # マニフェスト作成
            manifest = {
                "domain": "Test",
                "subskills": [{"id": "test", "display_name": "Test"}]
            }
            manifest_path = brain_path / "subskill_manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            self_eval = {"test": 0.5}
            eval_path = brain_path / "self_eval.json"
            eval_path.write_text(json.dumps(self_eval))

            # モック
            evaluator = AsyncMock(spec=SelfEvaluator)
            eval_result = SelfEvaluationResult(
                brain_id="test_brain",
                subskill_scores={"test": 0.75},
                overall_score=0.75,
                level=BrainLevel.LV1,
            )
            evaluator.evaluate_all_subskills.return_value = eval_result
            evaluator.update_self_evaluation_file = AsyncMock()

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                evaluator=evaluator,
            )

            # ダミー gap_detector を追加（Step1 をスキップさせない）
            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.return_value = KnowledgeGapResult(
                target_subskill="test",
                current_score=0.5,
                gap_severity="medium",
                gap_description="テストスキル不足",
                suggested_query="test query",
            )
            loop.gap_detector = gap_detector

            result = await loop.run_single_cycle()

            # 評価が更新されたことを確認
            assert result.step6_overall_score == 0.75
            assert result.step6_level == "Lv.1"
            evaluator.update_self_evaluation_file.assert_called_once()


class TestLearningLoopCycleCompletion:
    """1サイクル完了のテスト"""

    @pytest.mark.asyncio
    async def test_single_cycle_completion(self):
        """1サイクルが正常に完了することを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            # マニフェスト + Self-Eval 作成
            manifest = {"domain": "Test", "subskills": []}
            manifest_path = brain_path / "subskill_manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            self_eval = {}
            eval_path = brain_path / "self_eval.json"
            eval_path.write_text(json.dumps(self_eval))

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
            )

            result = await loop.run_single_cycle()

            # サイクル完了を確認
            assert result.brain_id == "test_brain"
            assert result.end_time != ""
            assert result.error is None
            assert loop.last_cycle_result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
