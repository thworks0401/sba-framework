"""
自己実験エンジン コア：仮説生成・実験設計

設計根拠（自己実験エンジン設定書 §4, §5）:
  - Step1: 弱点SubSkillから仮説テキストを生成（Tier1）
  - Step2: 仮説に基づき種別A/B/C/D を自動選択
  - Step2: 実験手順プロンプトを生成
  - Experiment Log に新規レコードを準備

実験種別:
  - A: 知識確認実験（問題生成→自己回答→正誤判定）
  - B: 推論実験（論理問題生成→多段推論→矛盾チェック）
  - C: コード実験（Tier3でコード生成→subprocess実行→結果検証）
  - D: シミュレーション実験（ビジネスケース生成→自己判断→評価）

【修正履歴】
  generate_hypothesis / select_experiment_type / generate_experiment_procedure の3メソッドで:
  - tier1.chat(prompt_str) → tier1.chat([{"role":"user","content":prompt}]) に修正
    （Tier1Engine.chat() は messages: list[dict] を要求する）
  - response.get("text","") → result.text に修正
    （戻り値は InferenceResult dataclass であり dict ではない）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List

from ..inference.tier1 import Tier1Engine
from ..storage.experiment_db import ExperimentRepository


logger = logging.getLogger(__name__)


class ExperimentType(Enum):
    """実験種別"""
    A = "knowledge_check"   # 知識確認実験
    B = "reasoning"         # 推論実験
    C = "code"              # コード実験
    D = "simulation"        # シミュレーション実験


@dataclass
class Hypothesis:
    """仮説データ"""
    text: str
    subskill: str
    confidence: float        # 0.0 - 1.0
    gap_description: str     # 知識ギャップの説明


@dataclass
class ExperimentPlan:
    """実験計画データ"""
    experiment_id: str
    hypothesis: Hypothesis
    experiment_type: ExperimentType
    subskill: str
    procedure_prompt: str
    expected_outcome: str
    success_criteria: str


class ExperimentEngine:
    """
    自己実験エンジン：仮説生成と実験設計を担当

    フロー:
      Step1 (仮説生成):
        - 弱点SubSkill + ギャップ情報を入力
        - Tier1が仮説テキストを生成

      Step2 (実験設計):
        - 仮説を分析して種別A/B/C/Dを決定
        - 実験手順・期待結果・成功基準を生成
        - Experiment Log に準備レコード作成
    """

    HYPOTHESIS_GENERATION_PROMPT_TEMPLATE = """
あなたは Brain（知識育成エージェント）です。
{brain_name} に与えられた弱点SubSkillについて、検証すべき仮説を生成してください。

【Brain情報】
- Domain: {domain}
- 弱点SubSkill: {weak_subskill}
- ギャップ説明: {gap_description}
- 現在スコア: {current_score:.1%}

【タスク】
このSubSkillについて「もし〇〇を試したらどうなるか」という形式の仮説を1つ生成してください。
実験を通じて検証可能で、かつ知識補完に役立つ仮説であることが重要です。

【出力形式】JSON:
{{
  "hypothesis": "〇〇について、△△という結果が得られるだろう",
  "confidence": 0.75,
  "rationale": "この仮説は、☆☆という理由で重要と考えられる"
}}
"""

    EXPERIMENT_TYPE_SELECTION_PROMPT_TEMPLATE = """
以下の仮説に対して、最適な実験種別を選択してください。

【仮説】
{hypothesis}

【Brain情報】
- Domain: {domain}
- SubSkill: {subskill}
- 実験可能な種別: A(知識確認), B(推論), C(コード), D(シミュレーション)

【選択基準】
- 知識確認が主目的 → A
- 論理・推論能力の検証が主目的 → B
- Tech系Brain かつ コード実装の検証 → C
- 意思決定・ビジネスシミュレーション → D

【タスク】
最適な種別を1つ選んでください。

【出力形式】JSON:
{{
  "experiment_type": "A",
  "reason": "この仮説は知識の穴を埋めるものなので知識確認実験が適切",
  "procedure_outline": "①問題を自動生成 ②自己回答 ③正誤判定"
}}
"""

    PROCEDURE_GENERATION_PROMPT_TEMPLATE = """
以下の仮説に対して、実験手順の詳細なプロンプトを生成してください。

【仮説】
{hypothesis}

【実験種別】
{experiment_type}

【Brain情報】
- Domain: {domain}
- SubSkill: {subskill}

【実験手順の例】
種別A: 自分で{problem_count}個の問題を生成 → 自己回答 → 自己採点
種別B: 論理問題を生成 → 多段推論 → 結果の矛盾チェック
種別C: コード生成 → 実行 → 結果検証
種別D: ビジネスシナリオを生成 → 判断実行 → 自己評価

【タスク】
この仮説を検証するための具体的な実験手順を設計してください。
結果として、後続の実験実行ステップで直接使えるプロンプトを生成してください。

【出力形式】JSON:
{{
  "procedure_prompt": "以下の条件で問題を生成してください：...",
  "expected_outcome": "正答率80%以上でスコアアップ",
  "success_criteria": "成功: 正答率>80%, 部分成功: 60-80%, 失敗: <60%",
  "estimated_duration_seconds": 300
}}
"""

    def __init__(
        self,
        brain_id: str,
        brain_name: str,
        domain: str,
        active_brain_path: Path,
        tier1: Optional[Tier1Engine] = None,
        exp_repo: Optional[ExperimentRepository] = None,
    ) -> None:
        self.brain_id          = brain_id
        self.brain_name        = brain_name
        self.domain            = domain
        self.active_brain_path = Path(active_brain_path)
        self.tier1             = tier1
        self.exp_repo          = exp_repo or ExperimentRepository(
            str(self.active_brain_path / "experiment_log.db")
        )

    # ======================================================================
    # 内部ユーティリティ
    # ======================================================================

    @staticmethod
    def _extract_json_from_text(text: str) -> Optional[dict]:
        """推論結果テキストから JSON を抽出"""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_result_text(result: object) -> str:
        """
        推論結果を文字列へ正規化する。

        実運用では InferenceResult を受け取り、テストでは dict / str が返ることも
        あるため、ここで後方互換的に吸収する。
        """
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

    async def _call_tier1(self, prompt: str, max_tokens: int = 1024) -> str:
        """
        Tier1 を呼び出してテキストを返す共通ラッパー。

        修正: tier1.chat() は messages: list[dict] を要求する。
             戻り値は InferenceResult（.text でアクセス）。
        """
        result = await self.tier1.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            response_format="json",
        )
        if getattr(result, "error", None):
            logger.error("Tier1 experiment call failed: %s", result.error)
        return self._extract_result_text(result)

    # ======================================================================
    # Step1: 仮説生成
    # ======================================================================

    async def generate_hypothesis(
        self,
        weak_subskill: str,
        gap_description: str,
        current_score: float,
    ) -> Optional[Hypothesis]:
        """弱点SubSkill から仮説テキストを生成（Tier1使用）"""
        if not self.tier1:
            logger.error("Tier1Engine not initialized")
            return None

        prompt = self.HYPOTHESIS_GENERATION_PROMPT_TEMPLATE.format(
            brain_name    = self.brain_name,
            domain        = self.domain,
            weak_subskill = weak_subskill,
            gap_description = gap_description,
            current_score = current_score,
        )

        try:
            response_text = await self._call_tier1(prompt)
            parsed = self._extract_json_from_text(response_text)
            if not parsed:
                logger.error(f"No JSON in hypothesis response: {response_text[:200]}")
                return None

            hypothesis = Hypothesis(
                text            = parsed.get("hypothesis", ""),
                subskill        = weak_subskill,
                confidence      = parsed.get("confidence", 0.5),
                gap_description = gap_description,
            )
            logger.info(f"Generated hypothesis for {weak_subskill}: confidence={hypothesis.confidence}")
            return hypothesis

        except Exception as e:
            logger.error(f"Error generating hypothesis: {e}")
            return None

    # ======================================================================
    # Step2: 実験種別選択
    # ======================================================================

    async def select_experiment_type(
        self,
        hypothesis: Hypothesis,
    ) -> Optional[ExperimentType]:
        """仮説から最適な実験種別を選択"""
        if not self.tier1:
            logger.error("Tier1Engine not initialized")
            return None

        prompt = self.EXPERIMENT_TYPE_SELECTION_PROMPT_TEMPLATE.format(
            hypothesis = hypothesis.text,
            domain     = self.domain,
            subskill   = hypothesis.subskill,
        )

        try:
            response_text = await self._call_tier1(prompt, max_tokens=256)
            parsed = self._extract_json_from_text(response_text)
            if not parsed:
                logger.error("No JSON in type selection response")
                return ExperimentType.A

            exp_type_str = parsed.get("experiment_type", "A")
            return ExperimentType[exp_type_str]

        except (KeyError, AttributeError):
            logger.warning("Invalid experiment type, defaulting to A")
            return ExperimentType.A
        except Exception as e:
            logger.error(f"Error selecting experiment type: {e}")
            return None

    # ======================================================================
    # Step2: 実験手順生成
    # ======================================================================

    async def generate_experiment_procedure(
        self,
        hypothesis: Hypothesis,
        experiment_type: ExperimentType,
    ) -> Optional[Dict]:
        """仮説と実験種別に基づいて実験手順を生成"""
        if not self.tier1:
            logger.error("Tier1Engine not initialized")
            return None

        problem_count = 5 if experiment_type == ExperimentType.A else 3

        prompt = self.PROCEDURE_GENERATION_PROMPT_TEMPLATE.format(
            hypothesis      = hypothesis.text,
            experiment_type = experiment_type.value,
            domain          = self.domain,
            subskill        = hypothesis.subskill,
            problem_count   = problem_count,
        )

        try:
            response_text = await self._call_tier1(prompt)
            parsed = self._extract_json_from_text(response_text)
            if not parsed:
                logger.error("No JSON in procedure response")
                return None

            return {
                "procedure_prompt":           parsed.get("procedure_prompt", ""),
                "expected_outcome":           parsed.get("expected_outcome", ""),
                "success_criteria":           parsed.get("success_criteria", ""),
                "estimated_duration_seconds": parsed.get("estimated_duration_seconds", 300),
            }

        except Exception as e:
            logger.error(f"Error generating experiment procedure: {e}")
            return None

    # ======================================================================
    # フルサイクル: 仮説生成 → 実験設計
    # ======================================================================

    async def design_experiment(
        self,
        weak_subskill: str,
        gap_description: str,
        current_score: float,
    ) -> Optional[ExperimentPlan]:
        """仮説生成から実験計画まで一連のステップを実行"""
        hypothesis = await self.generate_hypothesis(weak_subskill, gap_description, current_score)
        if not hypothesis:
            return None

        exp_type = await self.select_experiment_type(hypothesis)
        if not exp_type:
            exp_type = ExperimentType.A

        procedure_dict = await self.generate_experiment_procedure(hypothesis, exp_type)
        if not procedure_dict:
            return None

        experiment_id = f"exp_{self.brain_id}_{datetime.now().isoformat()}"

        plan = ExperimentPlan(
            experiment_id   = experiment_id,
            hypothesis      = hypothesis,
            experiment_type = exp_type,
            subskill        = weak_subskill,
            procedure_prompt = procedure_dict["procedure_prompt"],
            expected_outcome = procedure_dict["expected_outcome"],
            success_criteria = procedure_dict["success_criteria"],
        )
        logger.info(f"Designed experiment: {experiment_id} ({exp_type.value})")
        return plan
