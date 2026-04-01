"""
T-4: Self-Experimentation Engine - ユニットテスト

テストフェーズ タスクID: T-4
対象: 実験種別A/B/C/D + サンドボックス実行
方針: 各実験タイプの実行フロー検証 + セキュリティ確認

実行:
  pytest tests/unit/test_experiment_engine.py -v
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import json

from src.sba.experiment.experiment_engine import (
    ExperimentEngine, ExperimentType, Hypothesis
)
from src.sba.experiment.experiment_runner import (
    ExperimentRunnerA, ExperimentRunnerB, ExperimentRunnerD,
    ExperimentResult
)
from src.sba.experiment.sandbox_exec import SandboxExecutor
from src.sba.inference.tier1 import Tier1Engine
from src.sba.inference.tier3 import Tier3Engine
from src.sba.storage.experiment_db import ExperimentRepository


class TestExperimentEngineHypothesis:
    """仮説生成のテスト"""

    @pytest.mark.asyncio
    async def test_hypothesis_generation(self):
        """仮説生成が正常に実行できることを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            # モック Tier1
            tier1 = AsyncMock(spec=Tier1Engine)
            tier1.chat.return_value = {
                "text": '{"hypothesis": "if X then Y", "confidence": 0.8, "rationale": "reason"}'
            }

            # ExperimentRepository ダミー
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
            tier1.chat.return_value = {
                "text": '{"experiment_type": "A", "reason": "knowledge check"}'
            }

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


class TestExperimentRunnerA:
    """実験種別A: 知識確認の テスト"""

    @pytest.mark.asyncio
    async def test_experiment_a_success(self):
        """種別A（知識確認）が正常に実行できることを確認"""
        tier1 = AsyncMock(spec=Tier1Engine)

        # 3段階のLLM呼び出しをシミュレート
        responses = [
            {"text": '{"problems": [{"id": 1, "text": "Q1"}, {"id": 2, "text": "Q2"}]}'},
            {"text": '{"answers": [{"problem_id": 1, "answer": "A1"}, {"problem_id": 2, "answer": "A2"}]}'},
            {"text": '{"scores": [{"problem_id": 1, "score": 1.0}, {"problem_id": 2, "score": 0.8}], "average_score": 0.9, "assessment": "success"}'},
        ]
        tier1.chat.side_effect = responses

        exp_repo = MagicMock(spec=ExperimentRepository)

        runner = ExperimentRunnerA(
            brain_id="test_brain",
            tier1=tier1,
            exp_repo=exp_repo,
        )

        from src.sba.experiment.experiment_engine import ExperimentPlan
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


class TestSandboxExecutor:
    """Code Experiment - サンドボックス実行のテスト"""

    @pytest.mark.asyncio
    async def test_code_generation_and_execution(self):
        """コード生成とサンドボックス実行が正常に実行できることを確認"""
        tier3 = AsyncMock(spec=Tier3Engine)
        # Tier3Engine の generate_code をモック
        tier3.generate_code.return_value = \
            'print("Hello, World!")'

        exp_repo = MagicMock(spec=ExperimentRepository)

        executor = SandboxExecutor(
            brain_id="test_brain",
            tier3=tier3,
            exp_repo=exp_repo,
            timeout_seconds=10,
        )

        from src.sba.experiment.experiment_engine import ExperimentPlan
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

        # 実行成功を確認
        assert result.result == ExperimentResult.SUCCESS
        assert "Hello, World!" in result.output_text
        assert result.execution_time_seconds > 0

    @pytest.mark.asyncio
    async def test_sandbox_timeout_protection(self):
        """サンドボックスのタイムアウト保護が正常に動作することを確認"""
        tier3 = AsyncMock(spec=Tier3Engine)
        tier3.generate_code.return_value = \
            'import time\ntime.sleep(100)'

        exp_repo = MagicMock(spec=ExperimentRepository)

        executor = SandboxExecutor(
            brain_id="test_brain",
            tier3=tier3,
            exp_repo=exp_repo,
            timeout_seconds=2,  # 短いタイムアウト
        )

        from src.sba.experiment.experiment_engine import ExperimentPlan
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

        # タイムアウト検出を確認
        assert result.result == ExperimentResult.FAILURE
        assert "timeout" in result.error.lower() or result.execution_time_seconds >= 2


class TestExperimentIntegration:
    """実験エンジン統合テスト"""

    @pytest.mark.asyncio
    async def test_full_experiment_cycle(self):
        """仮説生成から実験実行までの完全サイクルを確認"""
        with tempfile.TemporaryDirectory() as tmpdir:
            brain_path = Path(tmpdir)

            tier1 = AsyncMock(spec=Tier1Engine)
            tier1.chat.side_effect = [
                # 仮説生成
                {"text": '{"hypothesis": "test", "confidence": 0.8, "rationale": "reason"}'},
                # 種別選択
                {"text": '{"experiment_type": "A", "reason": "knowledge"}'},
                # 手順生成
                {"text": '{"procedure_prompt": "proc", "expected_outcome": "exp", "success_criteria": "criteria"}'},
            ]

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
