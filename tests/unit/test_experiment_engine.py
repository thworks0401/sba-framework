"""
T-4: Self-Experimentation Engine - ユニットテスト

テストフェーズ タスクID: T-4
対象: 実験種別A/B/C/D + サンドボックス実行
方針: 各実験タイプの実行フロー検証 + セキュリティ確認

実行:
  pytest tests/unit/test_experiment_engine.py -v

【修正履歴】
  - tier1.chat の mock 戻り値を dict から InferenceResult に変更
    （experiment_engine.py の _call_tier1() は result.text でアクセスする）
  - tier3.generate_code の mock 戻り値を str から InferenceResult に変更
    （sandbox_exec.py の _generate_code() は result.text / result.error でアクセスする）
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.sba.experiment.experiment_engine import (
    ExperimentEngine, ExperimentPlan, ExperimentType, Hypothesis,
)
from src.sba.experiment.experiment_runner import (
    ExperimentResult,
    ExperimentRunnerA,
    ExperimentRunnerB,
    ExperimentRunnerD,
)
from src.sba.experiment.sandbox_exec import SandboxExecutor
from src.sba.inference.tier1 import InferenceResult as Tier1Result, Tier1Engine
from src.sba.inference.tier3 import InferenceResult as Tier3Result, Tier3Engine
from src.sba.storage.experiment_db import ExperimentRepository


# ======================================================================
# ヘルパー: InferenceResult を簡単に作るショートカット
# ======================================================================

def _t1(text: str) -> Tier1Result:
    """Tier1 モック用の InferenceResult を生成"""
    return Tier1Result(text=text, latency_ms=0.0)


def _t3(text: str) -> Tier3Result:
    """Tier3 モック用の InferenceResult を生成"""
    return Tier3Result(text=text, latency_ms=0.0)


# ======================================================================
# 仮説生成・実験種別選択
# ======================================================================

class TestExperimentEngineHypothesis:
    """仮説生成のテスト"""

    @pytest.mark.asyncio
    async def test_hypothesis_generation(self):
        """仮説生成が正常に実行できることを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            tier1 = AsyncMock(spec=Tier1Engine)
            # 修正: dict → Tier1Result
            tier1.chat = AsyncMock(return_value=_t1(
                '{"hypothesis": "if X then Y", "confidence": 0.8, "rationale": "reason"}'
            ))

            exp_repo = MagicMock(spec=ExperimentRepository)

            engine = ExperimentEngine(
                brain_id="test_brain",
                brain_name="Test Brain",
                domain="Test Domain",
                active_brain_path=brain_path,
                tier1=tier1,
                exp_repo=exp_repo,
            )

            hypothesis = await engine.generate_hypothesis(
                weak_subskill="test_subskill",
                gap_description="gap desc",
                current_score=0.5,
            )

            assert hypothesis is not None
            assert hypothesis.text == "if X then Y"
            assert hypothesis.confidence == 0.8
            tier1.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_experiment_type_selection(self):
        """実験種別選択が正常に実行できることを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            tier1 = AsyncMock(spec=Tier1Engine)
            # 修正: dict → Tier1Result
            tier1.chat = AsyncMock(return_value=_t1(
                '{"experiment_type": "A", "reason": "knowledge check"}'
            ))

            exp_repo = MagicMock(spec=ExperimentRepository)

            engine = ExperimentEngine(
                brain_id="test_brain",
                brain_name="Test Brain",
                domain="Test Domain",
                active_brain_path=brain_path,
                tier1=tier1,
                exp_repo=exp_repo,
            )

            hypothesis = Hypothesis(
                text="test hypothesis",
                subskill="test_subskill",
                confidence=0.8,
                gap_description="gap",
            )

            exp_type = await engine.select_experiment_type(hypothesis)

            assert exp_type == ExperimentType.A
            tier1.chat.assert_called_once()


# ======================================================================
# 種別A: 知識確認実験
# ======================================================================

class TestExperimentRunnerA:
    """実験種別A: 知識確認のテスト"""

    @pytest.mark.asyncio
    async def test_experiment_a_success(self):
        """種別A（知識確認）が正常に実行できることを確認"""
        tier1 = AsyncMock(spec=Tier1Engine)

        # 修正: 3ステップの LLM 呼び出しを InferenceResult でシミュレート
        tier1.chat = AsyncMock(side_effect=[
            _t1('{"problems": [{"id": 1, "text": "Q1"}, {"id": 2, "text": "Q2"}]}'),
            _t1('{"answers": [{"problem_id": 1, "answer": "A1"}, {"problem_id": 2, "answer": "A2"}]}'),
            _t1('{"scores": [{"problem_id": 1, "score": 1.0}, {"problem_id": 2, "score": 0.8}], '
                '"average_score": 0.9, "assessment": "success"}'),
        ])

        exp_repo = MagicMock(spec=ExperimentRepository)

        runner = ExperimentRunnerA(
            brain_id="test_brain",
            tier1=tier1,
            exp_repo=exp_repo,
        )

        plan = ExperimentPlan(
            experiment_id="exp_001",
            hypothesis=Hypothesis(
                text="test hypo",
                subskill="test_subskill",
                confidence=0.8,
                gap_description="gap",
            ),
            experiment_type=ExperimentType.A,
            subskill="test_subskill",
            procedure_prompt="procedure",
            expected_outcome="expected",
            success_criteria="criteria",
        )

        result = await runner.run(plan, knowledge_excerpt="test knowledge")

        assert result.result == ExperimentResult.SUCCESS
        assert result.score_change == 0.05
        assert tier1.chat.call_count == 3


# ======================================================================
# サンドボックス実行（種別C）
# ======================================================================

class TestSandboxExecutor:
    """Code Experiment - サンドボックス実行のテスト"""

    @pytest.mark.asyncio
    async def test_code_generation_and_execution(self):
        """コード生成とサンドボックス実行が正常に動作することを確認"""
        tier3 = AsyncMock(spec=Tier3Engine)
        # 修正: str → Tier3Result（result.text / result.error でアクセスされる）
        tier3.generate_code = AsyncMock(
            return_value=_t3('print("Hello, World!")')
        )

        exp_repo = MagicMock(spec=ExperimentRepository)

        executor = SandboxExecutor(
            brain_id="test_brain",
            tier3=tier3,
            exp_repo=exp_repo,
            timeout_seconds=10,
        )

        plan = ExperimentPlan(
            experiment_id="exp_code_001",
            hypothesis=Hypothesis(
                text="test hypo",
                subskill="implementation",
                confidence=0.8,
                gap_description="gap",
            ),
            experiment_type=ExperimentType.C,
            subskill="implementation",
            procedure_prompt="generate hello world code",
            expected_outcome="prints hello world",
            success_criteria="return code 0",
        )

        result = await executor.run(plan)

        assert result.result == ExperimentResult.SUCCESS
        assert "Hello, World!" in result.output_text
        assert result.execution_time_seconds > 0

    @pytest.mark.asyncio
    async def test_sandbox_timeout_protection(self):
        """サンドボックスのタイムアウト保護が正常に動作することを確認"""
        tier3 = AsyncMock(spec=Tier3Engine)
        # 修正: str → Tier3Result
        tier3.generate_code = AsyncMock(
            return_value=_t3("import time\ntime.sleep(100)")
        )

        exp_repo = MagicMock(spec=ExperimentRepository)

        executor = SandboxExecutor(
            brain_id="test_brain",
            tier3=tier3,
            exp_repo=exp_repo,
            timeout_seconds=2,
        )

        plan = ExperimentPlan(
            experiment_id="exp_timeout_001",
            hypothesis=Hypothesis(
                text="test hypo",
                subskill="implementation",
                confidence=0.8,
                gap_description="gap",
            ),
            experiment_type=ExperimentType.C,
            subskill="implementation",
            procedure_prompt="long running code",
            expected_outcome="timeout",
            success_criteria="timeout detected",
        )

        result = await executor.run(plan)

        assert result.result == ExperimentResult.FAILURE
        assert (
            (result.error is not None and "timeout" in result.error.lower())
            or result.execution_time_seconds >= 2
        )


# ======================================================================
# 統合: 仮説生成 → 実験設計
# ======================================================================

class TestExperimentIntegration:
    """実験エンジン統合テスト"""

    @pytest.mark.asyncio
    async def test_full_experiment_cycle(self):
        """仮説生成から実験計画作成までの完全サイクルを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            tier1 = AsyncMock(spec=Tier1Engine)
            # 修正: dict → Tier1Result（3ステップ分）
            tier1.chat = AsyncMock(side_effect=[
                _t1('{"hypothesis": "test", "confidence": 0.8, "rationale": "reason"}'),
                _t1('{"experiment_type": "A", "reason": "knowledge"}'),
                _t1('{"procedure_prompt": "proc", "expected_outcome": "exp", '
                    '"success_criteria": "criteria", "estimated_duration_seconds": 300}'),
            ])

            exp_repo = MagicMock(spec=ExperimentRepository)

            engine = ExperimentEngine(
                brain_id="test_brain",
                brain_name="Test Brain",
                domain="Test Domain",
                active_brain_path=brain_path,
                tier1=tier1,
                exp_repo=exp_repo,
            )

            plan = await engine.design_experiment(
                weak_subskill="test_subskill",
                gap_description="gap desc",
                current_score=0.5,
            )

            assert plan is not None
            assert plan.experiment_type == ExperimentType.A
            assert plan.subskill == "test_subskill"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
