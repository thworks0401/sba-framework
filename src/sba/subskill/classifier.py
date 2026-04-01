"""
SubSkill自動仕分けエンジン

設計根拠（SubSkill体系設定書 §5）:
  - Tier1（Phi-4:14B）が最終判定
  - 主SubSkill：最も直接的に役立つ場面を基準に1つ選択
  - 副SubSkill：参照リンクのみ（物理重複なし）
  - 分類不能時：__unclassified__ へ仮格納
  - subskill_manifest.json のエイリアス辞書参照
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

from ..inference.tier1 import Tier1Engine
from ..inference.engine_router import EngineRouter, InferenceTask, TaskType


class ClassificationError(Exception):
    """分類エラー"""


@dataclass
class SubSkillClassification:
    """SubSkill分類結果"""
    text: str
    primary_subskill: str  # 主SubSkill
    secondary_subskills: List[str] = None  # 副SubSkill一覧
    confidence: float = 0.0  # 信頼度（0.0～1.0）
    reason: str = ""  # 分類根拠
    raw_json: Optional[dict] = None  # Tier1からの生JSONレスポンス


class SubSkillClassifier:
    """
    Phi-4:14B を使った SubSkill 自動分類エンジン。

    入力テキストから、Brainの subskill_manifest.json に基づいて
    主SubSkill + 副SubSkillを自動判定する。
    """

    UNCLASSIFIED_LABEL = "__unclassified__"
    DEFAULT_CONFIDENCE_THRESHOLD = 0.5

    def __init__(
        self,
        brain_name: str,
        subskill_manifest: dict,
        tier1_engine: Optional[Tier1Engine] = None,
        engine_router: Optional[EngineRouter] = None,
    ) -> None:
        """
        Initialize SubSkillClassifier.

        Args:
            brain_name: Brain名（e.g. "Python開発Brain"）
            subskill_manifest: subskill_manifest.json の dict
            tier1_engine: Tier1エンジン（Noneの場合は作成）
            engine_router: EngineRouter（Noneの場合は作成）
        """
        self.brain_name = brain_name
        self.subskill_manifest = subskill_manifest
        self.tier1_engine = tier1_engine or Tier1Engine()
        self.engine_router = engine_router or EngineRouter()

        # SubSkill一覧とエイリアス辞書を初期化
        self._init_subskills()

    def _init_subskills(self) -> None:
        """
        subskill_manifest.json から SubSkill情報を抽出。

        subskills 配列を処理し、id、display_name、aliases を収集。
        """
        self.subskill_ids = []  # id一覧
        self.subskill_names = {}  # id → display_name
        self.subskill_descriptions = {}  # id → description
        self.alias_to_id = {}  # alias → id

        subskills = self.subskill_manifest.get("subskills", [])
        for subskill in subskills:
            skill_id = subskill.get("id", "")
            display_name = subskill.get("display_name", "")
            description = subskill.get("description", "")
            aliases = subskill.get("aliases", [])

            if skill_id:
                self.subskill_ids.append(skill_id)
                self.subskill_names[skill_id] = display_name
                self.subskill_descriptions[skill_id] = description

                # エイリアス → id のマッピング
                self.alias_to_id[display_name] = skill_id
                for alias in aliases:
                    self.alias_to_id[alias] = skill_id

    def _build_prompt(self, text: str, max_length: int = 2000) -> str:
        """
        分類プロンプトを構築。

        Args:
            text: 分類対象テキスト
            max_length: テキスト最大長（トリミング）

        Returns:
            プロンプト文字列
        """
        # テキストがあまり長い場合は先頭をトリミング
        if len(text) > max_length:
            text = text[:max_length] + "..."

        # SubSkill候補一覧を作成
        subskill_list = ", ".join(
            [f"'{self.subskill_names.get(sid, sid)}'" for sid in self.subskill_ids]
        )

        prompt = f"""あなたは {self.brain_name} の知識分類器である。

以下のテキストが、どのSubSkillに該当するか判定せよ。

【候補SubSkill】
{subskill_list}

【テキスト】
{text}

判定ルール:
1. 候補SubSkillの中から、テキストが最も直接的に役立つ場面を基準に選ぶ。
2. 複数に該当する場合も、主SubSkillは1つだけ選べ。
3. 主SubSkillに最も関連する使用場面を説明せよ。
4. 関連性のある副SubSkillがあれば、全て列挙せよ。
5. 信頼度（確信度）を 0.0～1.0で数値化して出力せよ（1.0が最高信頼）。

出力形式は以下のJSON形式とする:
{{
  "primary_subskill": "主SubSkill名（display_nameで）",
  "secondary_subskills": ["副SubSkill1", "副SubSkill2", ...],
  "confidence": 0.85,
  "reason": "分類根拠を日本語で簡潔に説明"
}}

JSON出力のみを返せ。他の文字は出力するな。"""
        return prompt

    async def classify(
        self,
        text: str,
        temperature: float = 0.3,
        timeout_s: float = 30.0,
    ) -> SubSkillClassification:
        """
        テキストをSubSkillに分類。

        Args:
            text: 分類対象テキスト
            temperature: Tier1の温度パラメータ
            timeout_s: タイムアウト秒数

        Returns:
            SubSkillClassification オブジェクト
        """
        prompt = self._build_prompt(text)

        # InferenceTask を構築（ルーティング対象外なので Tier1 直接呼び出し可能だが、念のため EngineRouter を通す）
        task = InferenceTask(
            type=TaskType.REASONING,  # 分類は推論タスク
            prompt=prompt,
            estimated_tokens=len(prompt.split()) + 200,
            is_tech_brain=False,
            max_output_tokens=512,
            temperature=temperature,
            timeout_s=timeout_s,
        )

        try:
            # EngineRouter で Tier 選択（ほぼ Tier1 になるはず）
            routing_decision = self.engine_router.route(task)

            # Tier1を直接呼び出し
            result = await self.tier1_engine.infer(
                prompt=prompt,
                temperature=temperature,
                max_tokens=512,
                timeout_s=timeout_s,
            )

            if result.error:
                return SubSkillClassification(
                    text=text,
                    primary_subskill=self.UNCLASSIFIED_LABEL,
                    secondary_subskills=[],
                    confidence=0.0,
                    reason=f"分類エラー: {result.error}",
                )

            # JSON抽出
            json_text = self.tier1_engine.extract_json(result.text)
            if not json_text:
                return SubSkillClassification(
                    text=text,
                    primary_subskill=self.UNCLASSIFIED_LABEL,
                    secondary_subskills=[],
                    confidence=0.0,
                    reason="Tier1からのJSON形式が返されませんでした",
                )

            parsed = json.loads(json_text)

            # 結果を正規化
            primary_name = parsed.get("primary_subskill", "")
            primary_id = self.alias_to_id.get(primary_name, primary_name)

            secondary_names = parsed.get("secondary_subskills", [])
            secondary_ids = [
                self.alias_to_id.get(name, name) for name in secondary_names
            ]

            confidence = float(parsed.get("confidence", 0.0))
            reason = parsed.get("reason", "")

            # 主SubSkillの検証
            if primary_id not in self.subskill_ids:
                primary_id = self.UNCLASSIFIED_LABEL

            return SubSkillClassification(
                text=text[:500],  # 保存時は先頭500文字のみ
                primary_subskill=primary_id,
                secondary_subskills=secondary_ids,
                confidence=confidence,
                reason=reason,
                raw_json=parsed,
            )

        except json.JSONDecodeError:
            return SubSkillClassification(
                text=text[:500],
                primary_subskill=self.UNCLASSIFIED_LABEL,
                secondary_subskills=[],
                confidence=0.0,
                reason="JSON パース失敗",
            )

        except Exception as e:
            return SubSkillClassification(
                text=text[:500],
                primary_subskill=self.UNCLASSIFIED_LABEL,
                secondary_subskills=[],
                confidence=0.0,
                reason=f"分類エラー: {str(e)}",
            )

    async def classify_batch(
        self,
        texts: List[str],
        temperature: float = 0.3,
        timeout_s: float = 30.0,
    ) -> List[SubSkillClassification]:
        """
        複数テキストを一括分類。

        Args:
            texts: 分類対象テキストリスト
            temperature: 温度パラメータ
            timeout_s: タイムアウト

        Returns:
            SubSkillClassification リスト
        """
        tasks = [
            self.classify(text, temperature, timeout_s) for text in texts
        ]
        return await asyncio.gather(*tasks)

    def get_subskill_display_name(self, subskill_id: str) -> str:
        """SubSkill IDから表示名を取得"""
        return self.subskill_names.get(subskill_id, subskill_id)

    def get_subskill_description(self, subskill_id: str) -> str:
        """SubSkill IDから説明を取得"""
        return self.subskill_descriptions.get(subskill_id, "")

    def is_unclassified(self, subskill_id: str) -> bool:
        """未分類かどうかを判定"""
        return subskill_id == self.UNCLASSIFIED_LABEL

    def get_all_subskill_ids(self) -> List[str]:
        """全SubSkill IDを取得"""
        return self.subskill_ids.copy()
