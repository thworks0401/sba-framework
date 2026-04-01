"""
Step2: 学習リソース自動探索エンジン

設計根拠（自律学習ループ設定書 §4、学習ソース・収集方針設定書 §2.1～2.3）:
  - SubSkill性質に応じてソース優先度を切り替え
  - API使用量・レート制限を確認
  - Learning Timeline を参照して重複URL除外
  - 信頼スコア初期値付与（0.95～0.50）
"""

from __future__ import annotations

import asyncio
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..storage.experiment_db import APIUsageRepository
from ..storage.knowledge_store import KnowledgeStore


class SourceType(Enum):
    """データソースタイプ"""
    WEB = "web"
    PDF = "pdf"
    ARXIV = "arxiv"
    YOUTUBE = "youtube"
    GITHUB = "github"
    STACKOVERFLOW = "stackoverflow"
    WIKIPEDIA = "wikipedia"


@dataclass
class ResourceCandidate:
    """学習リソース候補"""
    url: str  # リソースのURL/ID
    source_type: SourceType
    title: str = ""
    initial_trust_score: float = 0.80  # 初期信頼スコア（0.0～1.0）
    description: str = ""
    priority: int = 0  # 優先度（小さいほど高）


class ResourceFinder:
    """
    Step1で選定したSubSkillについて、
    最適な学習リソースを自律的に探索するエンジン。
    """

    # 信頼スコアの基準（学習ソース設定書 §2.1）
    TRUST_SCORES = {
        SourceType.ARXIV: 0.95,  # 学術論文
        SourceType.WIKIPEDIA: 0.90,  # 公式ドキュメント相当
        SourceType.PDF: 0.80,  # 専門サイト / 技術ブログ
        SourceType.GITHUB: 0.80,
        SourceType.STACKOVERFLOW: 0.50,  # Q&A / SNS
        SourceType.YOUTUBE: 0.65,  # 一般ブログ相当
        SourceType.WEB: 0.65,  # 一般Web
    }

    # SubSkill性質別ソース優先度
    TECH_BRAIN_SOURCES = [
        SourceType.GITHUB,
        SourceType.STACKOVERFLOW,
        SourceType.ARXIV,
        SourceType.WIKIPEDIA,
        SourceType.YOUTUBE,
        SourceType.WEB,
    ]

    GENERAL_BRAIN_SOURCES = [
        SourceType.WIKIPEDIA,
        SourceType.WEB,
        SourceType.ARXIV,
        SourceType.YOUTUBE,
        SourceType.PDF,
    ]

    def __init__(
        self,
        brain_name: str,
        knowledge_store: Optional[KnowledgeStore] = None,
        api_usage_repo: Optional[APIUsageRepository] = None,
    ) -> None:
        """
        Initialize ResourceFinder.

        Args:
            brain_name: Brain名
            knowledge_store: KnowledgeStore（重複チェック用）
            api_usage_repo: APIUsageRepository（レート制限確認用）
        """
        self.brain_name = brain_name
        self.knowledge_store = knowledge_store
        self.api_usage_repo = api_usage_repo

    def _get_source_priority(
        self, is_tech_brain: bool
    ) -> List[SourceType]:
        """Brain性質に応じてソース優先度を取得"""
        return self.TECH_BRAIN_SOURCES if is_tech_brain else self.GENERAL_BRAIN_SOURCES

    def _check_api_quota(self) -> Dict[str, bool]:
        """
        各APIのクォータ残量を確認。

        Returns:
            {api_name: quota_available}
        """
        if not self.api_usage_repo:
            return {
                "gemini": True,
                "youtube": True,
                "github": True,
                "stackoverflow": True,
                "arxiv": True,
            }

        quota_status = {}

        # Gemini: 残100以下で制限
        try:
            gemini_quota = self.api_usage_repo.get_remaining_quota("gemini")
            quota_status["gemini"] = gemini_quota >= 100
        except Exception:
            quota_status["gemini"] = False

        # YouTube: 日次10,000 units
        try:
            yt_quota = self.api_usage_repo.get_remaining_quota("youtube")
            quota_status["youtube"] = yt_quota >= 1000  # 安全マージン
        except Exception:
            quota_status["youtube"] = True  # yt-dlp は無制限

        # GitHub,  StackOverflow, arXiv は制限緩いため常にTrue
        quota_status["github"] = True
        quota_status["stackoverflow"] = True
        quota_status["arxiv"] = True

        return quota_status

    def _has_seen_url(self, url: str) -> bool:
        """
        Learning Timeline でURL を確認済みか判定。

        Args:
            url: URL

        Returns:
            既出ならTrue
        """
        if not self.knowledge_store:
            return False

        try:
            # Learning Timeline の ハッシュ値検索
            existing = self.knowledge_store.check_url_in_timeline(url)
            return existing
        except Exception:
            return False

    async def search_resources(
        self,
        subskill_id: str,
        query: str,
        is_tech_brain: bool = False,
        max_candidates: int = 10,
    ) -> List[ResourceCandidate]:
        """
        学習リソースを検索。

        Args:
            subskill_id: 対象SubSkill
            query: 学習クエリ（Step1で生成）
            is_tech_brain: Tech系 Brain かどうか
            max_candidates: 最大候補数

        Returns:
            ResourceCandidate のリスト
        """
        source_priority = self._get_source_priority(is_tech_brain)
        quota_status = self._check_api_quota()
        candidates: List[ResourceCandidate] = []

        # 各ソースタイプ別に検索を実行
        for source_type in source_priority:
            if len(candidates) >= max_candidates:
                break

            # クォータ確認
            if not self._should_use_source(source_type, quota_status):
                continue

            # ソース別検索実行
            try:
                results = await self._search_by_source(
                    source_type, query, subskill_id
                )
                # 既出URLをフィルタ
                for result in results:
                    if not self._has_seen_url(result.url):
                        candidates.append(result)
                        if len(candidates) >= max_candidates:
                            break
            except Exception as e:
                # ソース別エラーは無視して次へ
                pass

        return candidates

    def _should_use_source(
        self, source_type: SourceType, quota_status: Dict[str, bool]
    ) -> bool:
        """ソースを使用可能か判定（クォータ・性質から）"""
        if source_type == SourceType.ARXIV:
            return True  # 制限なし
        elif source_type == SourceType.WIKIPEDIA:
            return True  # 制限なし
        elif source_type == SourceType.WEB:
            return True  # duckduckgo 制限なし
        elif source_type == SourceType.PDF:
            return quota_status.get("gemini", False)  # 要約用 Gemini
        elif source_type == SourceType.YOUTUBE:
            return quota_status.get("youtube", False)
        elif source_type == SourceType.GITHUB:
            return quota_status.get("github", False)
        elif source_type == SourceType.STACKOVERFLOW:
            return quota_status.get("stackoverflow", False)
        return False

    async def _search_by_source(
        self,
        source_type: SourceType,
        query: str,
        subskill_id: str,
    ) -> List[ResourceCandidate]:
        """
        ソース別の検索実行（スタブ）。

        実装は 4-4～4-7 の各Fetcherで行う。
        ここでは空リストを返す。

        Args:
            source_type: ソースタイプ
            query: 検索クエリ
            subskill_id: SubSkill ID

        Returns:
            ResourceCandidate のリスト
        """
        # 実装注：各ソース別Fetcherが個別に実装される
        # 本メソッドはそれらを統合する窓口
        return []

    def rank_candidates(
        self,
        candidates: List[ResourceCandidate],
    ) -> List[ResourceCandidate]:
        """
        候補をランク付け（信頼度・優先度ソート）。

        Args:
            candidates: 候補リスト

        Returns:
            ランク付けされた候補リスト
        """
        # 信頼スコアが高い、かつ優先度が高い（値が小さい）順でソート
        ranked = sorted(
            candidates,
            key=lambda c: (-c.initial_trust_score, c.priority),
        )
        return ranked

    def estimate_quota_impact(
        self,
        candidates: List[ResourceCandidate],
    ) -> Dict[str, int]:
        """
        候補の取得に伴うAPI使用量を推定。

        Args:
            candidates: 候補リスト

        Returns:
            {api_name: 推定使用量}
        """
        impact = {}
        for candidate in candidates:
            if candidate.source_type == SourceType.ARXIV:
                impact["arxiv"] = impact.get("arxiv", 0) + 1
            elif candidate.source_type == SourceType.YOUTUBE:
                impact["youtube"] = impact.get("youtube", 0) + 100
            elif candidate.source_type == SourceType.GITHUB:
                impact["github"] = impact.get("github", 0) + 1
            elif candidate.source_type == SourceType.STACKOVERFLOW:
                impact["stackoverflow"] = impact.get("stackoverflow", 0) + 1
        return impact
