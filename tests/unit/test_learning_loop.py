"""
T-3: Learning Loop - ユニットテスト（全書き換え版）

テストフェーズ タスクID: T-3
対象: LearningLoop の Step1 / Step4 / Step6 / サイクル完了

方針:
  - モックを使って外部依存を排除した純粋ユニットテスト
  - subskill_scores は必ず SubSkillEvaluation インスタンスで渡す（float 禁止）
  - pytest-asyncio は pytest.ini の asyncio_mode=auto で自動適用

実行:
  pytest tests/unit/test_learning_loop.py -v
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.sba.experiment.experiment_engine import ExperimentEngine
from src.sba.learning.gap_detector import GapDetector, KnowledgeGapResult
from src.sba.learning.knowledge_integrator import KnowledgeIntegrator
from src.sba.learning.learning_loop import LearningCycleResult, LearningLoop
from src.sba.learning.resource_finder import ResourceCandidate, ResourceFinder
from src.sba.learning.self_evaluator import (
    BrainLevel,
    SelfEvaluationResult,
    SelfEvaluator,
    SubSkillEvaluation,
)


# ────────────────────────────────────────────────────────────
# ヘルパー: テンポラリBrainディレクトリのセットアップ
# ────────────────────────────────────────────────────────────

def _make_brain_dir(
    tmp: str,
    subskills: list | None = None,
    eval_scores: dict | None = None,
) -> Path:
    """
    テスト用の Brain ディレクトリを作成して Path を返す。

    Args:
        tmp:        tempfile.TemporaryDirectory の path 文字列
        subskills:  subskill_manifest.json の subskills リスト
        eval_scores: self_eval.json の初期スコア辞書
    """
    brain_path = Path(tmp)

    manifest = {
        "domain": "Test",
        "subskills": subskills or [],
    }
    (brain_path / "subskill_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    (brain_path / "self_eval.json").write_text(
        json.dumps(eval_scores or {}, ensure_ascii=False), encoding="utf-8"
    )

    return brain_path


def _make_gap_result(subskill: str = "test", score: float = 0.5) -> KnowledgeGapResult:
    """テスト用の KnowledgeGapResult を返すヘルパー。"""
    return KnowledgeGapResult(
        target_subskill=subskill,
        current_score=score,
        gap_severity="high",
        gap_description=f"{subskill} スキルが不足している",
        suggested_query=f"{subskill} の学習クエリ",
    )


def _make_subskill_evaluation(subskill_id: str, score: float) -> SubSkillEvaluation:
    """
    テスト用の SubSkillEvaluation を返すヘルパー。
    subskill_scores モックに float を直接渡すのは厳禁。必ずこれを使う。
    """
    return SubSkillEvaluation(
        subskill_id=subskill_id,
        score=score,
        questions_asked=3,
        correct_answers=round(3 * score),
        evaluation_date=datetime.now().isoformat(),
    )


# ────────────────────────────────────────────────────────────
# Step1 テスト: ギャップ検出
# ────────────────────────────────────────────────────────────

class TestLearningLoopStep1:
    """Step1: ギャップ検出のテスト"""

    @pytest.mark.asyncio
    async def test_gap_detection_success(self):
        """ギャップ検出が正常に実行でき、結果が LearningCycleResult に反映される。"""
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(
                tmp,
                subskills=[
                    {"id": "design", "display_name": "設計"},
                    {"id": "implementation", "display_name": "実装"},
                ],
                eval_scores={"design": 0.7, "implementation": 0.5},
            )

            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.return_value = _make_gap_result(
                subskill="implementation", score=0.5
            )

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                gap_detector=gap_detector,
            )

            result = await loop.run_single_cycle()

            # Step1 の結果が正しく記録されていることを確認
            assert result.step1_gap is not None
            assert result.step1_gap["target_subskill"] == "implementation"
            assert result.step1_gap["current_score"] == 0.5
            gap_detector.detect_gap.assert_called_once()

    @pytest.mark.asyncio
    async def test_gap_detection_failure_handling(self):
        """ギャップ検出で例外が発生した場合、result.error に Step1 が記録される。"""
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(tmp)

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


# ────────────────────────────────────────────────────────────
# Step4 テスト: 自己実験
# ────────────────────────────────────────────────────────────

class TestLearningLoopStep4:
    """Step4: 自己実験のテスト"""

    @pytest.mark.asyncio
    async def test_experiment_design_called(self):
        """実験設計メソッドが正常に呼び出されることを確認。"""
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(
                tmp,
                subskills=[{"id": "design", "display_name": "設計"}],
                eval_scores={"design": 0.5},
            )

            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.return_value = _make_gap_result(
                subskill="design", score=0.5
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


# ────────────────────────────────────────────────────────────
# Step6 テスト: 自己評価
# ────────────────────────────────────────────────────────────

class TestLearningLoopStep6:
    """Step6: 自己評価のテスト"""

    @pytest.mark.asyncio
    async def test_self_evaluation_updated(self):
        """
        自己評価が正常に更新されることを確認。

        重要: subskill_scores に float を渡すと update_self_evaluation_file
        内の evaluation.score アクセスで AttributeError が発生する。
        必ず SubSkillEvaluation インスタンスを使うこと。
        """
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(
                tmp,
                subskills=[{"id": "test", "display_name": "Test"}],
                eval_scores={"test": 0.5},
            )

            evaluator = AsyncMock(spec=SelfEvaluator)

            # ↓ 正しい: SubSkillEvaluation インスタンスを使う
            eval_result = SelfEvaluationResult(
                brain_id="test_brain",
                overall_score=0.75,
                level=BrainLevel.LV1,
                subskill_scores={
                    "test": _make_subskill_evaluation("test", 0.75)
                },
                weakest_subskill="test",
                strongest_subskill="test",
                evaluation_date=datetime.now().isoformat(),
            )
            evaluator.evaluate_all_subskills.return_value = eval_result
            evaluator.update_self_evaluation_file = AsyncMock()

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                evaluator=evaluator,
            )

            # Step1 もちゃんと動かすためにダミー gap_detector を追加
            gap_detector = AsyncMock(spec=GapDetector)
            gap_detector.detect_gap.return_value = _make_gap_result(
                subskill="test", score=0.5
            )
            loop.gap_detector = gap_detector

            result = await loop.run_single_cycle()

            # 評価スコアとレベルが正しく記録されていることを確認
            assert result.step6_overall_score == 0.75
            assert result.step6_level == "Lv.1"
            evaluator.update_self_evaluation_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_self_evaluation_empty_subskills(self):
        """
        SubSkillが空の場合でも自己評価がクラッシュせず完了することを確認。
        （overall_score=0.0, level=Lv.1 がセットされる）
        """
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(tmp)  # subskills なし

            evaluator = AsyncMock(spec=SelfEvaluator)
            # SubSkillが空 → subskill_scores は空 dict（Noneではない）
            eval_result = SelfEvaluationResult(
                brain_id="test_brain",
                overall_score=0.0,
                level=BrainLevel.LV1,
                subskill_scores={},  # ← 空 dict（None 禁止）
                evaluation_date=datetime.now().isoformat(),
            )
            evaluator.evaluate_all_subskills.return_value = eval_result
            evaluator.update_self_evaluation_file = AsyncMock()

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
                evaluator=evaluator,
            )

            result = await loop.run_single_cycle()

            # エラーなく完了すること
            assert result.error is None or "Step" not in (result.error or "")
            assert result.step6_overall_score == 0.0


# ────────────────────────────────────────────────────────────
# サイクル完了テスト
# ────────────────────────────────────────────────────────────

class TestLearningLoopCycleCompletion:
    """1サイクル完了の全体テスト"""

    @pytest.mark.asyncio
    async def test_single_cycle_completion(self):
        """
        全コンポーネントなしで run_single_cycle が正常完了することを確認。
        （gap_detector/evaluator 等が None でもクラッシュしないこと）
        """
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(tmp)

            loop = LearningLoop(
                brain_id="test_brain",
                brain_name="Test Brain",
                active_brain_path=brain_path,
            )

            result = await loop.run_single_cycle()

            # 基本フィールドの確認
            assert result.brain_id == "test_brain"
            assert result.end_time != ""
            assert result.error is None
            assert loop.last_cycle_result is not None

    @pytest.mark.asyncio
    async def test_cycle_result_has_cycle_id(self):
        """サイクルIDが brain_id を含む形式で生成されることを確認。"""
        with tempfile.TemporaryDirectory() as tmp:
            brain_path = _make_brain_dir(tmp)

            loop = LearningLoop(
                brain_id="my_brain",
                brain_name="My Brain",
                active_brain_path=brain_path,
            )

            result = await loop.run_single_cycle()

            assert result.cycle_id.startswith("my_brain_")


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
