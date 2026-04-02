"""
Step6: 育成Lv自己評価・次学習優先度決定エンジン

設計根拠（自律学習ループ設定書 §8、成長評価・Lv定義設定書）:
  - SubSkill別のランダム問題生成・自己回答・自己採点
  - Lv.1: 平均80%以上、Lv.2: 平均95%以上、Lv.3: 平均99%以上
  - 3回連続通過でLvUP
  - self_eval.json を更新、優先度キューを再計算
"""

from __future__ import annotations

import json
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum
import asyncio

from ..inference.tier1 import Tier1Engine


class BrainLevel(Enum):
    """Brain育成レベル"""
    LV1 = "Lv.1"  # Practitioner: 80%以上
    LV2 = "Lv.2"  # Expert: 95%以上
    LV3 = "Lv.3"  # Domain Sovereign: 99%以上


@dataclass
class SubSkillEvaluation:
    """SubSkill評価結果"""
    subskill_id: str
    score: float  # 0.0～1.0
    questions_asked: int = 0
    correct_answers: int = 0
    evaluation_date: str = ""
    lv_promotion_count: int = 0  # 連続通過回数


@dataclass
class SelfEvaluationResult:
    """自己評価全体の結果"""
    brain_id: str
    overall_score: float  # 全SubSkillの平均スコア
    level: BrainLevel
    subskill_scores: Dict[str, SubSkillEvaluation] = None
    weakest_subskill: str = ""
    strongest_subskill: str = ""
    evaluation_date: str = ""


class SelfEvaluator:
    """
    Brainの自己評価エンジン。
    SubSkill別の問題生成→自己採点→Lv判定を実行する。
    """

    QUESTIONS_PER_SUBSKILL = 3  # 1SubSkill あたりの問題数
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
        Initialize SelfEvaluator.

        Args:
            brain_name: Brain表示名
            brain_id: Brain ID
            tier1_engine: Tier1エンジン
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
        1つのSubSkillを評価。

        Args:
            subskill_id: SubSkill ID
            subskill_description: SubSkill説明
            num_questions: 質問数

        Returns:
            SubSkillEvaluation
        """
        correct = 0

        for _ in range(num_questions):
            # 問題生成～自己採点
            is_correct = await self._generate_and_answer_question(
                subskill_id, subskill_description
            )
            if is_correct:
                correct += 1

        score = correct / num_questions
        from datetime import datetime
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
        1問の問題生成～自己採点。

        Args:
            subskill_id: SubSkill ID
            description: 説明

        Returns:
            正解ならTrue
        """
        prompt = f"""あなたは {self.brain_name} 自己評価システムである。

【対象SubSkill】
ID: {subskill_id}
説明: {description}

このSubSkillに関する実務的な問題を1つ生成し、自分で回答・採点しなさい。

【出力形式】
以下のJSON形式で出力せよ:
{{
  "question": "問題文",
  "answer": "あなたの回答",
  "correct_answer": "正解（参考）",
  "is_correct": true/false,
  "explanation": "採点根拠"
}}

JSON のみを出力せよ。"""

        try:
            result = await self.tier1_engine.infer(
                prompt=prompt,
                temperature=0.6,
                max_tokens=500,
                timeout_s=15.0,
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
        全SubSkillを並行評価。

        Args:
            subskill_manifest: subskill_manifest.json

        Returns:
            SelfEvaluationResult
        """
        subskills = subskill_manifest.get("subskills", [])

        # 並行評価
        tasks = []
        for sk in subskills:
            sk_id = sk.get("id")
            sk_desc = sk.get("description", "")
            tasks.append(self.evaluate_subskill(sk_id, sk_desc))

        evaluations = await asyncio.gather(*tasks)

        # 結果を辞書化
        eval_dict = {ev.subskill_id: ev for ev in evaluations}

        # 全体スコア・弱点を計算
        scores = [ev.score for ev in evaluations]
        overall_score = sum(scores) / len(scores) if scores else 0.0

        from datetime import datetime
        return SelfEvaluationResult(
            brain_id=self.brain_id,
            overall_score=overall_score,
            level=self._determine_level(overall_score),
            subskill_scores=eval_dict,
            weakest_subskill=min(eval_dict.items(), key=lambda x: x[1].score)[0] if eval_dict else "",
            strongest_subskill=max(eval_dict.items(), key=lambda x: x[1].score)[0] if eval_dict else "",
            evaluation_date=datetime.now().isoformat(),
        )

    def _determine_level(self, overall_score: float) -> BrainLevel:
        """全体スコアからLvを判定"""
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
        self_eval.json を更新。

        Args:
            self_eval_path: ファイルパス
            eval_result: 評価結果
        """
        # 既存ファイルを読み込み（ない場合は新規）
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

        # スコア更新
        for subskill_id, evaluation in eval_result.subskill_scores.items():
            data["scores"][subskill_id] = evaluation.score

        # Lv更新
        data["level"] = eval_result.level.value
        data["overall_score"] = eval_result.overall_score

        # 弱点フラグ更新（0.6以下）
        data["weak_subskills"] = [
            subskill_id for subskill_id, evaluation in
            eval_result.subskill_scores.items()
            if evaluation.score <= 0.6
        ]

        # 履歴に追加
        if "history" not in data:
            data["history"] = []
        data["history"].append({
            "timestamp": eval_result.evaluation_date,
            "overall_score": eval_result.overall_score,
            "level": eval_result.level.value,
        })

        # ファイルに保存
        with open(self_eval_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def calculate_next_priority_queue(
        self,
        eval_result: SelfEvaluationResult,
        max_items: int = 5,
    ) -> List[Tuple[str, float]]:
        """
        評価結果から次ループの優先度キューを計算。

        Args:
            eval_result: 評価結果
            max_items: 最大項目数

        Returns:
            [(subskill_id, score)] のリスト（スコア昇順）
        """
        items = [
            (sk_id, ev.score) for sk_id, ev in
            eval_result.subskill_scores.items()
        ]
        # スコア昇順（低いほど優先）
        items.sort(key=lambda x: x[1])
        return items[:max_items]
