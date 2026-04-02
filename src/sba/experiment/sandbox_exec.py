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

【修正履歴】
  - vram_guard.acquire() → acquire_lock(ModelType.TIER3) に修正
    （VRAMGuard の正しい public API）
  - vram_guard.release() → release_lock(ModelType.TIER3) に修正
  - self.tier3.chat(prompt) → self.tier3.generate_code(prompt) に修正
    （Tier3Engine には chat() は存在せず generate_code() が正しいメソッド）
  - response.get("text","") → result.text に修正
    （戻り値は InferenceResult dataclass）
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..inference.tier3 import Tier3Engine
from ..storage.experiment_db import ExperimentRepository
from ..utils.vram_guard import VRAMGuard, ModelType
from .experiment_engine import ExperimentPlan
from .experiment_runner import ExperimentResult, ExperimentRunResult


logger = logging.getLogger(__name__)


def _extract_generated_code_result(result: object) -> tuple[str, Optional[str]]:
    """Tier3 の戻り値を後方互換的に正規化する。"""
    if result is None:
        return "", "Empty response"

    if isinstance(result, str):
        return result, None

    if isinstance(result, dict):
        text = result.get("text") or result.get("response") or ""
        error = result.get("error")
        return text if isinstance(text, str) else "", error if isinstance(error, str) else None

    text = getattr(result, "text", "")
    error = getattr(result, "error", None)
    return text if isinstance(text, str) else "", error if isinstance(error, str) else None


@dataclass
class SandboxExecutionResult:
    """サンドボックス実行結果"""
    stdout:                 str
    stderr:                 str
    return_code:            int
    execution_time_seconds: float
    timed_out:              bool = False


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
        self.brain_id        = brain_id
        self.tier3           = tier3
        self.exp_repo        = exp_repo
        self.vram_guard      = vram_guard
        self.timeout_seconds = timeout_seconds

    async def run(self, plan: ExperimentPlan) -> ExperimentRunResult:
        """種別C実験を実行"""
        start_time = datetime.now()
        result = ExperimentRunResult(
            experiment_id          = plan.experiment_id,
            result                 = ExperimentResult.FAILURE,
            score_change           = 0.0,
            output_text            = "",
            analysis_text          = "",
            execution_time_seconds = 0.0,
        )

        # VRAM排他制御で Tier3 を起動
        # 修正: acquire() → acquire_lock(ModelType.TIER3)
        if self.vram_guard:
            try:
                self.vram_guard.acquire_lock(ModelType.TIER3)
            except Exception as e:
                result.error = f"VRAM lock failed: {e}"
                return result

        try:
            # Step1: コード生成（Tier3使用）
            code = await self._generate_code(plan)
            if not code:
                result.error = "Failed to generate code"
                return result

            # Step2: サンドボックス実行
            sandbox_result = await self._execute_in_sandbox(code)
            result.output_text   = sandbox_result.stdout
            result.analysis_text = f"Exit code: {sandbox_result.return_code}"

            if sandbox_result.stderr:
                result.analysis_text += f"\nStderr: {sandbox_result.stderr[:500]}"

            # Step3: 結果判定
            if sandbox_result.timed_out:
                result.result       = ExperimentResult.FAILURE
                result.score_change = 0.0
                result.error        = "Execution timeout"
            elif sandbox_result.return_code == 0 and len(sandbox_result.stdout) > 0:
                result.result       = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif sandbox_result.return_code == 0:
                result.result       = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif sandbox_result.return_code != 0:
                result.result       = ExperimentResult.FAILURE
                result.score_change = 0.0
            else:
                result.result       = ExperimentResult.PARTIAL
                result.score_change = 0.02

            result.execution_time_seconds = sandbox_result.execution_time_seconds
            return result

        except Exception as e:
            logger.error(f"Error running code sandbox experiment: {e}")
            result.error                  = str(e)
            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result

        finally:
            # 修正: release() → release_lock(ModelType.TIER3)
            if self.vram_guard:
                try:
                    self.vram_guard.release_lock(ModelType.TIER3)
                except Exception:
                    pass  # ロック未取得の場合も安全に無視

    async def _generate_code(self, plan: ExperimentPlan) -> Optional[str]:
        """
        Tier3 でコードを生成。

        修正: tier3.chat() は存在しない。
             Tier3Engine の正しいメソッドは generate_code()。
             戻り値は InferenceResult（.text でアクセス）。
        """
        prompt = self.CODE_GENERATION_PROMPT.format(
            procedure_prompt = plan.procedure_prompt,
            subskill         = plan.subskill,
            hypothesis       = plan.hypothesis.text,
        )

        try:
            # 修正: tier3.chat(prompt) → tier3.generate_code(prompt)
            result = await self.tier3.generate_code(prompt)
            code_text, error_text = _extract_generated_code_result(result)

            if error_text:
                logger.error(f"Tier3 code generation error: {error_text}")
                return None

            # generate_code() は既にコードブロック抽出済みだが念のため確認
            if not code_text.strip():
                logger.warning("Empty code returned from Tier3")
                return None

            # もし ```python...``` ブロックが残っていた場合は除去
            code_match = re.search(r"```python\n(.*?)\n```", code_text, re.DOTALL)
            if code_match:
                code_text = code_match.group(1).strip()

            logger.debug(f"Generated code ({len(code_text)} chars)")
            return code_text

        except Exception as e:
            logger.error(f"Error generating code with Tier3: {e}")
            return None

    async def _execute_in_sandbox(self, code: str) -> SandboxExecutionResult:
        """生成されたコードをサンドボックス実行"""
        start_time = datetime.now()

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "experiment.py"

            try:
                script_path.write_text(code, encoding="utf-8")
            except Exception as e:
                logger.error(f"Error writing temp script: {e}")
                return SandboxExecutionResult(
                    stdout                 = "",
                    stderr                 = f"Error writing script: {e}",
                    return_code            = -1,
                    execution_time_seconds = (datetime.now() - start_time).total_seconds(),
                )

            try:
                proc = subprocess.Popen(
                    [sys.executable, str(script_path)],
                    stdout  = subprocess.PIPE,
                    stderr  = subprocess.PIPE,
                    text    = True,
                    cwd     = tmpdir,
                )
                stdout, stderr = proc.communicate(timeout=self.timeout_seconds)

                return SandboxExecutionResult(
                    stdout                 = stdout,
                    stderr                 = stderr,
                    return_code            = proc.returncode,
                    execution_time_seconds = (datetime.now() - start_time).total_seconds(),
                )

            except subprocess.TimeoutExpired:
                proc.kill()
                logger.warning(f"Code execution timeout after {self.timeout_seconds}s")
                return SandboxExecutionResult(
                    stdout                 = "",
                    stderr                 = "Execution timeout",
                    return_code            = -1,
                    execution_time_seconds = float(self.timeout_seconds),
                    timed_out              = True,
                )

            except Exception as e:
                logger.error(f"Error executing code in sandbox: {e}")
                return SandboxExecutionResult(
                    stdout                 = "",
                    stderr                 = str(e),
                    return_code            = -1,
                    execution_time_seconds = (datetime.now() - start_time).total_seconds(),
                )
