"""
実験種別C: コードサンドボックス実験

設計根拠（自己実験エンジン設定書 §6.2）:
  - Tier3（Qwen2.5-Coder:7B）でコード生成
  - subprocess でサンドボックス実行（制限環境）
  - stdout/stderr/終了コードで合否判定
  - VRAM排他制御と連携

セキュリティ要件:
  - 実行時間制限: 30秒 (timeout)
  - 専用一時ディレクトリ
  - 外部ネットワークアクセス禁止（制限は OS レベル）
  - ファイルWrite制限
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from ..inference.tier3 import Tier3Engine
from ..storage.experiment_db import ExperimentRepository
from ..utils.vram_guard import VRAMGuard
from .experiment_engine import ExperimentPlan, ExperimentType
from .experiment_runner import ExperimentResult, ExperimentRunResult


logger = logging.getLogger(__name__)


@dataclass
class SandboxExecutionResult:
    """サンドボックス実行結果"""
    stdout: str
    stderr: str
    return_code: int
    execution_time_seconds: float
    timed_out: bool = False


class SandboxExecutor:
    """
    Code Experiment（種別C）実行エンジン

    Qwen2.5-Coder で生成されたコードをサンドボックス環境で実行
    """

    CODE_GENERATION_PROMPT = """
あなたは Python コード生成エキスパートです。
以下の要件に基づいて、動作確認用の Python コードを生成してください。

【要件】
{procedure_prompt}

【SubSkill】
{subskill}

【背景知識】
{hypothesis}

【制約事項】
- 実行時間は 30 秒以内
- 外部ライブラリはNumpyとPandasのみ（デフォルトで利用可能）
- ファイルI/Oは /tmp 以下のみ許可
- ネットワークアクセスは禁止
- 完全に独立した実行可能なコードを出力

【出力形式】
```python
# コード全文
```

回答の先頭は ```python で、末尾は ``` です。
"""

    def __init__(
        self,
        brain_id: str,
        tier3: Tier3Engine,
        exp_repo: ExperimentRepository,
        vram_guard: Optional[VRAMGuard] = None,
        timeout_seconds: int = 30,
    ):
        """
        Args:
            brain_id: Brain ID
            tier3: Tier3Engine インスタンス
            exp_repo: ExperimentRepository
            vram_guard: VRAMGuard インスタンス
            timeout_seconds: 実行タイムアウト秒数
        """
        self.brain_id = brain_id
        self.tier3 = tier3
        self.exp_repo = exp_repo
        self.vram_guard = vram_guard
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        plan: ExperimentPlan,
    ) -> ExperimentRunResult:
        """
        種別C実験を実行

        Args:
            plan: ExperimentPlan

        Returns:
            ExperimentRunResult
        """
        start_time = datetime.now()
        result = ExperimentRunResult(
            experiment_id=plan.experiment_id,
            result=ExperimentResult.FAILURE,
            score_change=0.0,
            output_text="",
            analysis_text="",
            execution_time_seconds=0.0,
        )

        # VRAM排他制御で Tier3 起動
        if self.vram_guard:
            acquired = self.vram_guard.acquire(timeout=self.timeout_seconds)
            if not acquired:
                result.error = "VRAM lock timeout"
                return result

        try:
            # Step1: コード生成（Tier3使用）
            code = await self._generate_code(plan)
            if not code:
                result.error = "Failed to generate code"
                return result

            # Step2: サンドボックス実行
            sandbox_result = await self._execute_in_sandbox(code)
            result.output_text = sandbox_result.stdout
            result.analysis_text = f"Exit code: {sandbox_result.return_code}"

            if sandbox_result.stderr:
                result.analysis_text += f"\nStderr: {sandbox_result.stderr}"

            # Step3: 結果判定
            if sandbox_result.timed_out:
                result.result = ExperimentResult.FAILURE
                result.score_change = 0.0
                result.error = "Execution timeout"
            elif sandbox_result.return_code == 0 and len(sandbox_result.stdout) > 0:
                result.result = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif sandbox_result.return_code == 0 and len(sandbox_result.stderr) == 0:
                result.result = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif sandbox_result.return_code != 0:
                result.result = ExperimentResult.FAILURE
                result.score_change = 0.0
            else:
                result.result = ExperimentResult.PARTIAL
                result.score_change = 0.02

            result.execution_time_seconds = sandbox_result.execution_time_seconds
            return result

        except Exception as e:
            logger.error(f"Error running code sandbox experiment: {e}")
            result.error = str(e)
            result.result = ExperimentResult.FAILURE
            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result

        finally:
            # VRAM ロック解放
            if self.vram_guard:
                self.vram_guard.release()

    async def _generate_code(self, plan: ExperimentPlan) -> Optional[str]:
        """
        Tier3 でコードを生成

        Returns:
            Python コード文字列（失敗時は None）
        """
        prompt = self.CODE_GENERATION_PROMPT.format(
            procedure_prompt=plan.procedure_prompt,
            subskill=plan.subskill,
            hypothesis=plan.hypothesis.text,
        )

        try:
            response = await self.tier3.chat(prompt)
            code_text = response.get("text", "")

            # ```python ... ``` から抽出
            code_match = re.search(
                r"```python\n(.*?)\n```",
                code_text,
                re.DOTALL
            )
            if not code_match:
                logger.warning("No code block found in response")
                return None

            code = code_match.group(1).strip()
            if not code:
                return None

            logger.debug(f"Generated code ({len(code)} chars)")
            return code

        except Exception as e:
            logger.error(f"Error generating code with Tier3: {e}")
            return None

    async def _execute_in_sandbox(self, code: str) -> SandboxExecutionResult:
        """
        生成されたコードをサンドボックス実行

        Args:
            code: Python コード文字列

        Returns:
            SandboxExecutionResult
        """
        start_time = datetime.now()

        # 一時ディレクトリでコード実行
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "experiment.py"

            # コードを一時ファイルに保存
            try:
                script_path.write_text(code, encoding="utf-8")
            except Exception as e:
                logger.error(f"Error writing temp script: {e}")
                return SandboxExecutionResult(
                    stdout="",
                    stderr=f"Error writing script: {e}",
                    return_code=-1,
                    execution_time_seconds=(datetime.now() - start_time).total_seconds(),
                    timed_out=False,
                )

            # subprocess で実行（タイムアウト付き）
            try:
                proc = subprocess.Popen(
                    ["python", str(script_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=tmpdir,
                    timeout=self.timeout_seconds,
                )

                stdout, stderr = proc.communicate()

                execution_time = (datetime.now() - start_time).total_seconds()

                return SandboxExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    return_code=proc.returncode,
                    execution_time_seconds=execution_time,
                    timed_out=False,
                )

            except subprocess.TimeoutExpired:
                logger.warning(
                    f"Code execution timeout after {self.timeout_seconds}s"
                )
                return SandboxExecutionResult(
                    stdout="",
                    stderr="Execution timeout",
                    return_code=-1,
                    execution_time_seconds=self.timeout_seconds,
                    timed_out=True,
                )

            except Exception as e:
                logger.error(f"Error executing code in sandbox: {e}")
                return SandboxExecutionResult(
                    stdout="",
                    stderr=str(e),
                    return_code=-1,
                    execution_time_seconds=(datetime.now() - start_time).total_seconds(),
                    timed_out=False,
                )
