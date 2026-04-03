"""
Step6: 育成Lv自己評価・次学習優先度決定エンジン

設計根拠（自律学習ループ設定書 §8、成長評価・Lv定義設定書）:
  - SubSkill別のランダム問題生成・自己回答・自己採点
  - Lv.1: 平均80%以上、Lv.2: 平均95%以上、Lv.3: 平均99%以上
  - 3回連続通過でLvUP
  - self_eval.json を更新、優先度キューを再計算

【修正履歴】
  2026-04-03: SelfEvaluationResult.subskill_scores のデフォルト値を
              None → field(default_factory=dict) に修正。
              None のまま update_self_evaluation_file に渡すと
              .items() でクラッシュするため。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import asyncio

from ..inference.tier1 import Tier1Engine


class BrainLevel(Enum):
    """Brain育成レベル"""
    LV1 = "Lv.1"  # Practitioner: SubSkill平均スコア >= 0.80
    LV2 = "Lv.2"  # Expert:       SubSkill平均スコア >= 0.95
    LV3 = "Lv.3"  # Domain Sovereign: SubSkill平均スコア >= 0.99


@dataclass
class SubSkillEvaluation:
    """
    1SubSkillの評価結果。
    update_self_evaluation_file 内で evaluation.score にアクセスするため
    必ず SubSkillEvaluation インスタンスで渡すこと（float値は不可）。
    """
    subskill_id: str
    score: float           # 0.0 ～ 1.0
    questions_asked: int = 0
    correct_answers: int = 0
    evaluation_date: str = ""
    lv_promotion_count: int = 0  # 連続通過回数（3回でLvUP）


@dataclass
class SelfEvaluationResult:
    """
    自己評価全体の結果。

    【重要】subskill_scores は Dict[str, SubSkillEvaluation] 型。
    デフォルトを field(default_factory=dict) にすることで
    None による AttributeError を防ぐ。
    テスト等でモックを渡す際も必ず SubSkillEvaluation オブジェクトを使うこと。
    """
    brain_id: str
    overall_score: float   # 全SubSkillの平均スコア
    level: BrainLevel
    # ↓ None 禁止。必ず dict で初期化（None だと .items() でクラッシュ）
    subskill_scores: Dict[str, SubSkillEvaluation] = field(default_factory=dict)
    weakest_subskill: str = ""
    strongest_subskill: str = ""
    evaluation_date: str = ""


class SelfEvaluator:
    """
    Brainの自己評価エンジン。
    SubSkill別の問題生成 → 自己採点 → Lv判定を実行する。
    """

    QUESTIONS_PER_SUBSKILL = 3  # 1SubSkillあたりの問題数
    LV_THRESHOLD = {
        BrainLevel.LV1: 0.80,
        BrainLevel.LV2: 0.95,
        BrainLevel.LV3: 0.99,
    }
    PROMOTION_THRESHOLD = 3  # このスコアで3回連続通過でLvUP

    def __init__(
        self,
        brain_name: str,
        brain_id: str,
        tier1_engine: Optional[Tier1Engine] = None,
    ) -> None:
        """
        Args:
            brain_name: Brain表示名
            brain_id:   Brain ID
            tier1_engine: Tier1エンジン（省略時は自動生成）
        """
        self.brain_name = brain_name
        self.brain_id = brain_id
        self.tier1_engine = tier1_engine or Tier1Engine()

    async def evaluate_subskill(
        self,
        subskill_id: str,
        subskill_description: str,
        num_questions: int = QUESTIONS_PER_SUBSKILL,
    ) -> SubSkillEvaluation:
        """
        1つのSubSkillを評価する。

        Args:
            subskill_id:          SubSkill ID
            subskill_description: SubSkill説明文
            num_questions:        問題数

        Returns:
            SubSkillEvaluation
        """
        correct = 0

        for _ in range(num_questions):
            is_correct = await self._generate_and_answer_question(
                subskill_id, subskill_description
            )
            if is_correct:
                correct += 1

        score = correct / num_questions
        return SubSkillEvaluation(
            subskill_id=subskill_id,
            score=score,
            questions_asked=num_questions,
            correct_answers=correct,
            evaluation_date=datetime.now().isoformat(),
        )

    async def _generate_and_answer_question(
        self,
        subskill_id: str,
        description: str,
    ) -> bool:
        """
        1問の問題生成 → 自己採点。

        Args:
            subskill_id: SubSkill ID
            description: SubSkill説明

        Returns:
            正解なら True
        """
        prompt = (
            f"あなたは {self.brain_name} 自己評価システムである。\n\n"
            f"【対象SubSkill】\n"
            f"ID: {subskill_id}\n"
            f"説明: {description}\n\n"
            "このSubSkillに関する実務的な問題を1つ生成し、自分で回答・採点しなさい。\n\n"
            "【出力形式】\n"
            "以下のJSON形式で出力せよ:\n"
            "{\n"
            '  "question": "問題文",\n'
            '  "answer": "あなたの回答",\n'
            '  "correct_answer": "正解（参考）",\n'
            '  "is_correct": true/false,\n'
            '  "explanation": "採点根拠"\n'
            "}\n\n"
            "JSONのみを出力せよ。"
        )

        try:
            result = await self.tier1_engine.infer(
                prompt=prompt,
                temperature=0.6,
                max_tokens=500,
                timeout_s=15.0,
                response_format="json",
            )

            if result.error:
                return False

            parsed = self.tier1_engine.extract_json(result.text)
            if parsed:
                return bool(parsed.get("is_correct", False))

            return False

        except Exception:
            return False

    async def evaluate_all_subskills(
        self,
        subskill_manifest: dict,
    ) -> SelfEvaluationResult:
        """
        全SubSkillを並行評価する。

        Args:
            subskill_manifest: subskill_manifest.json の内容

        Returns:
            SelfEvaluationResult
        """
        subskills = subskill_manifest.get("subskills", [])

        # 並行評価
        tasks = [
            self.evaluate_subskill(
                sk.get("id", ""),
                sk.get("description", ""),
            )
            for sk in subskills
        ]
        evaluations: List[SubSkillEvaluation] = list(await asyncio.gather(*tasks))

        # 辞書化（subskill_id → SubSkillEvaluation）
        eval_dict: Dict[str, SubSkillEvaluation] = {
            ev.subskill_id: ev for ev in evaluations
        }

        # 全体スコア計算
        scores = [ev.score for ev in evaluations]
        overall_score = sum(scores) / len(scores) if scores else 0.0

        return SelfEvaluationResult(
            brain_id=self.brain_id,
            overall_score=overall_score,
            level=self._determine_level(overall_score),
            subskill_scores=eval_dict,
            weakest_subskill=(
                min(eval_dict.items(), key=lambda x: x[1].score)[0]
                if eval_dict else ""
            ),
            strongest_subskill=(
                max(eval_dict.items(), key=lambda x: x[1].score)[0]
                if eval_dict else ""
            ),
            evaluation_date=datetime.now().isoformat(),
        )

    def _determine_level(self, overall_score: float) -> BrainLevel:
        """全体スコアから育成Lvを判定する。"""
        if overall_score >= self.LV_THRESHOLD[BrainLevel.LV3]:
            return BrainLevel.LV3
        elif overall_score >= self.LV_THRESHOLD[BrainLevel.LV2]:
            return BrainLevel.LV2
        else:
            return BrainLevel.LV1

    async def update_self_evaluation_file(
        self,
        self_eval_path: Path,
        eval_result: SelfEvaluationResult,
    ) -> None:
        """
        self_eval.json を更新する。

        Args:
            self_eval_path: ファイルパス
            eval_result:    評価結果（subskill_scores は必ず SubSkillEvaluation 型）

        Raises:
            AttributeError: subskill_scores に float 等が渡された場合
                            → 必ず SubSkillEvaluation インスタンスを渡すこと
        """
        # 既存ファイルを読み込み（存在しない場合は新規）
        if self_eval_path.exists():
            with open(self_eval_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {
                "brain_id": self.brain_id,
                "scores": {},
                "level": BrainLevel.LV1.value,
                "history": [],
            }

        # SubSkill別スコアを更新
        # evaluation は SubSkillEvaluation インスタンスであること（.score を使う）
        for subskill_id, evaluation in eval_result.subskill_scores.items():
            data["scores"][subskill_id] = evaluation.score

        # Lv・全体スコアを更新
        data["level"] = eval_result.level.value
        data["overall_score"] = eval_result.overall_score

        # 弱点フラグ更新（スコア 0.6 以下を弱点とみなす）
        data["weak_subskills"] = [
            subskill_id
            for subskill_id, evaluation in eval_result.subskill_scores.items()
            if evaluation.score <= 0.6
        ]

        # 履歴に追記
        if "history" not in data:
            data["history"] = []
        data["history"].append({
            "timestamp": eval_result.evaluation_date,
            "overall_score": eval_result.overall_score,
            "level": eval_result.level.value,
        })

        # ファイルに保存（UTF-8 / BOMなし）
        with open(self_eval_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def calculate_next_priority_queue(
        self,
        eval_result: SelfEvaluationResult,
        max_items: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        評価結果から次ループの優先度キューを計算する。

        Args:
            eval_result: 評価結果
            max_items:   最大項目数

        Returns:
            [(subskill_id, score)] のリスト（スコア昇順 = 弱点優先）
        """
        items = [
            (sk_id, ev.score)
            for sk_id, ev in eval_result.subskill_scores.items()
        ]
        # スコア昇順（低いほど優先）
        items.sort(key=lambda x: x[1])
        return items[:max_items]
