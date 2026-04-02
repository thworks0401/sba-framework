"""
実験実行エンジン：種別A/B/D 実行ロジック

設計根拠（自己実験エンジン設定書 §6.1～6.3）:
  - 種別A/B: Tier1 による自己問題生成→自己回答→自己採点
  - 種別D: ビジネスケース・シミュレーション実行
  - 結果を experiment_log.db に記録
  - Knowledge Base へ成功パターン・逆説的知識を格納
  - Self-Evaluation スコアを更新

【修正履歴】
  ExperimentRunnerA / B / D の run() メソッド全箇所で:
  - self.tier1.chat(prompt_str) → self.tier1.chat([{"role":"user","content":prompt}]) に修正
  - response.get("text","") → result.text に修正
  （Tier1Engine.chat() は list[dict] を受け取り InferenceResult を返す）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List

from ..inference.tier1 import Tier1Engine
from ..storage.experiment_db import ExperimentRepository
from ..storage.knowledge_store import KnowledgeStore
from .experiment_engine import ExperimentPlan, ExperimentType


logger = logging.getLogger(__name__)


class ExperimentResult(Enum):
    """実験結果"""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


@dataclass
class ExperimentRunResult:
    """実験実行結果"""
    experiment_id:          str
    result:                 ExperimentResult
    score_change:           float   # -0.05 ～ +0.05
    output_text:            str
    analysis_text:          str
    execution_time_seconds: float
    error:                  Optional[str]  = None
    related_knowledge_ids:  List[str]      = field(default_factory=list)


# ======================================================================
# 共通ユーティリティ
# ======================================================================

def _extract_json(text: str) -> Optional[Dict]:
    """テキストから JSON を抽出"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _extract_result_text(result: object) -> str:
    """Tier1 の戻り値を後方互換的に文字列へ正規化する。"""
    if result is None:
        return ""

    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content

        for key in ("text", "response", "content"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        return ""

    text = getattr(result, "text", None)
    return text if isinstance(text, str) else ""


async def _tier1_chat(tier1: Tier1Engine, prompt: str, max_tokens: int = 1024) -> str:
    """
    Tier1 チャット呼び出し共通ラッパー。

    修正: tier1.chat() は messages: list[dict] を要求し、InferenceResult を返す。
    """
    result = await tier1.chat(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return _extract_result_text(result)


# ======================================================================
# 種別A: 知識確認実験
# ======================================================================

class ExperimentRunnerA:
    """
    種別A: 知識確認実験

    自身で問題を生成 → 自身で回答 → 自身で採点
    """

    PROBLEM_GENERATION_PROMPT = """
あなたは専門知識を持つ出題者です。
以下の内容について、基礎から応用までのレベルの問題を{problem_count}個生成してください。

【対象知識】
{knowledge_base_excerpt}

【出力形式】JSON:
{{
  "problems": [
    {{"id": 1, "text": "問題文", "difficulty": "basic"}},
    ...
  ]
}}
"""

    SELF_EVALUATION_PROMPT = """
あなたは出題者かつ回答者です。
以下の問題に対して、自分の知識に基づいて自己回答してください。

【問題リスト】
{problems_json}

【出力形式】JSON:
{{
  "answers": [
    {{"problem_id": 1, "answer": "回答テキスト"}},
    ...
  ]
}}
"""

    SELF_GRADING_PROMPT = """
あなたは採点者です。
自身の回答を採点してください。正確性・完全性を自己評価してください。

【問題と回答】
{problems_and_answers}

【出力形式】JSON:
{{
  "scores": [
    {{"problem_id": 1, "score": 1.0, "feedback": "正確"}},
    ...
  ],
  "average_score": 0.85,
  "assessment": "success"
}}
"""

    def __init__(
        self,
        brain_id: str,
        tier1: Tier1Engine,
        exp_repo: ExperimentRepository,
        knowledge_store: Optional[KnowledgeStore] = None,
    ):
        self.brain_id        = brain_id
        self.tier1           = tier1
        self.exp_repo        = exp_repo
        self.knowledge_store = knowledge_store

    # 後方互換のために静的メソッドとして残す（RunnerB/Dから参照されている）
    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        return _extract_json(text)

    async def run(
        self,
        plan: ExperimentPlan,
        knowledge_excerpt: str = "",
    ) -> ExperimentRunResult:
        """種別A実験を実行"""
        start_time = datetime.now()
        result = ExperimentRunResult(
            experiment_id          = plan.experiment_id,
            result                 = ExperimentResult.FAILURE,
            score_change           = 0.0,
            output_text            = "",
            analysis_text          = "",
            execution_time_seconds = 0.0,
        )

        try:
            problem_count = 5

            # Step1: 問題生成
            prompt1 = self.PROBLEM_GENERATION_PROMPT.format(
                problem_count          = problem_count,
                knowledge_base_excerpt = knowledge_excerpt or plan.hypothesis.gap_description,
            )
            text1        = await _tier1_chat(self.tier1, prompt1)
            problems_json = _extract_json(text1)
            if not problems_json:
                result.error = "Failed to generate problems"
                return result

            # Step2: 自己回答
            prompt2 = self.SELF_EVALUATION_PROMPT.format(
                problem_count = problem_count,
                problems_json = json.dumps(problems_json, ensure_ascii=False),
            )
            text2        = await _tier1_chat(self.tier1, prompt2)
            answers_json = _extract_json(text2)
            if not answers_json:
                result.error = "Failed to generate answers"
                return result

            # Step3: 自己採点
            combined_qa = {
                "problems": problems_json.get("problems", []),
                "answers":  answers_json.get("answers", []),
            }
            prompt3 = self.SELF_GRADING_PROMPT.format(
                problems_and_answers = json.dumps(combined_qa, ensure_ascii=False),
            )
            text3        = await _tier1_chat(self.tier1, prompt3)
            grading_json = _extract_json(text3)
            if not grading_json:
                result.error = "Failed to grade answers"
                return result

            # 結果評価
            avg_score  = grading_json.get("average_score", 0.0)
            assessment = grading_json.get("assessment", "failure")

            result.output_text   = json.dumps(combined_qa, ensure_ascii=False, indent=2)
            result.analysis_text = f"Average score: {avg_score:.2%}, Assessment: {assessment}"

            if assessment == "success" and avg_score >= 0.8:
                result.result       = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif avg_score >= 0.6:
                result.result       = ExperimentResult.PARTIAL
                result.score_change = 0.02
            else:
                result.result       = ExperimentResult.FAILURE
                result.score_change = 0.0

            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result

        except Exception as e:
            logger.error(f"Error running experiment A: {e}")
            result.error                  = str(e)
            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result


# ======================================================================
# 種別B: 推論実験
# ======================================================================

class ExperimentRunnerB:
    """
    種別B: 推論実験

    論理問題生成 → 多段推論 → 矛盾チェック
    """

    REASONING_PROBLEM_PROMPT = """
あなたは論理パズルの出題者です。
以下の知識領域について、複数段階の推論が必要な問題を{problem_count}個生成してください。

【知識領域】
{subskill}

【要件】
- 単純な一段推論ではなく、複数ステップが必要
- 矛盾チェック・仮説検証が含まれるもの

【出力形式】JSON:
{{
  "problems": [
    {{"id": 1, "text": "問題文", "required_steps": 3}},
    ...
  ]
}}
"""

    REASONING_EXECUTION_PROMPT = """
あなたは推論エキスパートです。
以下の推論問題に対して、ステップバイステップで推論を実行してください。
各ステップで矛盾がないか確認してください。

【問題リスト】
{problems_json}

【出力形式】JSON:
{{
  "reasoning_results": [
    {{"problem_id": 1, "steps": ["Step 1: ...", "Step 2: ..."], "conclusion": "...", "consistent": true}},
    ...
  ],
  "contradiction_count": 0,
  "overall_coherence": 0.95
}}
"""

    def __init__(
        self,
        brain_id: str,
        tier1: Tier1Engine,
        exp_repo: ExperimentRepository,
    ):
        self.brain_id  = brain_id
        self.tier1     = tier1
        self.exp_repo  = exp_repo

    async def run(self, plan: ExperimentPlan) -> ExperimentRunResult:
        """種別B実験を実行"""
        start_time = datetime.now()
        result = ExperimentRunResult(
            experiment_id          = plan.experiment_id,
            result                 = ExperimentResult.FAILURE,
            score_change           = 0.0,
            output_text            = "",
            analysis_text          = "",
            execution_time_seconds = 0.0,
        )

        try:
            problem_count = 3

            # Step1: 推論問題生成
            prompt1 = self.REASONING_PROBLEM_PROMPT.format(
                problem_count = problem_count,
                subskill      = plan.subskill,
            )
            text1        = await _tier1_chat(self.tier1, prompt1)
            problems_json = _extract_json(text1)
            if not problems_json:
                result.error = "Failed to generate reasoning problems"
                return result

            # Step2: 推論実行 + 矛盾検出
            prompt2 = self.REASONING_EXECUTION_PROMPT.format(
                problems_json = json.dumps(problems_json, ensure_ascii=False),
            )
            text2          = await _tier1_chat(self.tier1, prompt2)
            reasoning_json = _extract_json(text2)
            if not reasoning_json:
                result.error = "Failed to execute reasoning"
                return result

            # 結果評価
            contradiction_count = reasoning_json.get("contradiction_count", 0)
            overall_coherence   = reasoning_json.get("overall_coherence", 0.0)

            result.output_text   = json.dumps(reasoning_json, ensure_ascii=False, indent=2)
            result.analysis_text = f"Contradictions: {contradiction_count}, Coherence: {overall_coherence:.2%}"

            if contradiction_count == 0 and overall_coherence >= 0.9:
                result.result       = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif contradiction_count <= 1 and overall_coherence >= 0.7:
                result.result       = ExperimentResult.PARTIAL
                result.score_change = 0.02
            else:
                result.result       = ExperimentResult.FAILURE
                result.score_change = 0.0

            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result

        except Exception as e:
            logger.error(f"Error running experiment B: {e}")
            result.error                  = str(e)
            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result


# ======================================================================
# 種別D: シミュレーション実験
# ======================================================================

class ExperimentRunnerD:
    """
    種別D: シミュレーション実験

    ビジネスケース・法律シナリオ・意思決定シミュレーション
    """

    SCENARIO_GENERATION_PROMPT = """
あなたはシミュレーションシナリオの設計者です。
以下の分野について、複雑な意思決定が必要なシナリオを{scenario_count}個生成してください。

【分野】
{subskill}

【要件】
- 実務的で現実的なシナリオ
- 複数の判断ポイントがある
- 結果の因果関係が明確

【出力形式】JSON:
{{
  "scenarios": [
    {{"id": 1, "context": "シナリオの背景", "question": "判断を求める質問"}},
    ...
  ]
}}
"""

    DECISION_EXECUTION_PROMPT = """
あなたは意思決定者です。
以下のビジネスシナリオについて、自身の知識・判断に基づいて決定を下してください。
その根拠も記述してください。

【シナリオ】
{scenarios_json}

【出力形式】JSON:
{{
  "decisions": [
    {{"scenario_id": 1, "decision": "決定内容", "rationale": "根拠", "risk_level": "medium"}},
    ...
  ]
}}
"""

    SELF_EVALUATION_PROMPT = """
あなたは経営・戦略コンサルタントです。
上記の意思決定の妥当性を自己評価してください。

【意思決定と根拠】
{decisions_json}

【採点基準】
- 妥当性（0-1.0）: 判断が現実的か
- 根拠の確かさ（0-1.0）: 根拠が論理的か
- リスク認識（0-1.0）: リスクを正しく認識しているか

【出力形式】JSON:
{{
  "evaluations": [
    {{"scenario_id": 1, "appropriateness": 0.85, "reasoning_quality": 0.9, "risk_awareness": 0.8}},
    ...
  ],
  "average_score": 0.85
}}
"""

    def __init__(
        self,
        brain_id: str,
        tier1: Tier1Engine,
        exp_repo: ExperimentRepository,
    ):
        self.brain_id  = brain_id
        self.tier1     = tier1
        self.exp_repo  = exp_repo

    async def run(self, plan: ExperimentPlan) -> ExperimentRunResult:
        """種別D実験を実行"""
        start_time = datetime.now()
        result = ExperimentRunResult(
            experiment_id          = plan.experiment_id,
            result                 = ExperimentResult.FAILURE,
            score_change           = 0.0,
            output_text            = "",
            analysis_text          = "",
            execution_time_seconds = 0.0,
        )

        try:
            scenario_count = 2

            # Step1: シナリオ生成
            prompt1 = self.SCENARIO_GENERATION_PROMPT.format(
                scenario_count = scenario_count,
                subskill       = plan.subskill,
            )
            text1          = await _tier1_chat(self.tier1, prompt1)
            scenarios_json = _extract_json(text1)
            if not scenarios_json:
                result.error = "Failed to generate scenarios"
                return result

            # Step2: 意思決定実行
            prompt2 = self.DECISION_EXECUTION_PROMPT.format(
                scenarios_json = json.dumps(scenarios_json, ensure_ascii=False),
            )
            text2          = await _tier1_chat(self.tier1, prompt2)
            decisions_json = _extract_json(text2)
            if not decisions_json:
                result.error = "Failed to execute decisions"
                return result

            # Step3: 自己評価
            prompt3 = self.SELF_EVALUATION_PROMPT.format(
                decisions_json = json.dumps(decisions_json, ensure_ascii=False),
            )
            text3           = await _tier1_chat(self.tier1, prompt3)
            evaluation_json = _extract_json(text3)
            if not evaluation_json:
                result.error = "Failed to evaluate decisions"
                return result

            # 結果評価
            avg_score = evaluation_json.get("average_score", 0.0)

            result.output_text   = json.dumps(evaluation_json, ensure_ascii=False, indent=2)
            result.analysis_text = f"Decision quality: {avg_score:.2%}"

            if avg_score >= 0.8:
                result.result       = ExperimentResult.SUCCESS
                result.score_change = 0.05
            elif avg_score >= 0.6:
                result.result       = ExperimentResult.PARTIAL
                result.score_change = 0.02
            else:
                result.result       = ExperimentResult.FAILURE
                result.score_change = 0.0

            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result

        except Exception as e:
            logger.error(f"Error running experiment D: {e}")
            result.error                  = str(e)
            result.execution_time_seconds = (datetime.now() - start_time).total_seconds()
            return result
