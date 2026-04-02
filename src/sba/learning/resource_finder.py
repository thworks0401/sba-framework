"""
Step2: 学習リソース自動探索エンジン

設計根拠（自律学習ループ設定書 §4、学習ソース・収集方針設定書 §2.1～2.3）:
  - SubSkill性質に応じてソース優先度を切り替え
  - API使用量・レート制限を確認
  - Learning Timeline を参照して重複URL除外
  - 信頼スコア初期値付与（0.95～0.50）

【修正履歴】
  - インポート先を誤った experiment_db から正しい api_usage_db に修正
  - _check_api_quota() 内の get_remaining_quota() を
    APIUsageRepository の正しいメソッド get_remaining_tokens() に修正
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from enum import Enum

# 修正: experiment_db ではなく api_usage_db が正しいインポート元
from ..config import SBAConfig
from ..cost.rate_limiter import APIRateLimiter, RateLimitStatus
from ..sources.code_fetcher import CodeFetcher
from ..sources.pdf_fetcher import PDFFetcher
from ..sources.video_fetcher import VideoFetcher
from ..sources.web_fetcher import WebFetcher
from ..storage.api_usage_db import APIUsageRepository
from ..storage.knowledge_store import KnowledgeStore


class SourceType(Enum):
    """データソースタイプ"""
    WEB           = "web"
    PDF           = "pdf"
    ARXIV         = "arxiv"
    YOUTUBE       = "youtube"
    GITHUB        = "github"
    STACKOVERFLOW = "stackoverflow"
    WIKIPEDIA     = "wikipedia"


@dataclass
class ResourceCandidate:
    """学習リソース候補"""
    url: str               # リソースのURL/ID
    source_type: SourceType
    title: str             = ""
    initial_trust_score: float = 0.80  # 初期信頼スコア（0.0～1.0）
    description: str       = ""
    priority: int          = 0         # 優先度（小さいほど高）
    trust_score: Optional[float] = None

    def __post_init__(self) -> None:
        # 旧呼び出し側の trust_score=... を吸収する
        if self.trust_score is None:
            self.trust_score = self.initial_trust_score
        else:
            self.initial_trust_score = self.trust_score


class ResourceFinder:
    """
    Step1で選定したSubSkillについて、
    最適な学習リソースを自律的に探索するエンジン。
    """

    # 信頼スコアの基準（学習ソース設定書 §2.1）
    TRUST_SCORES = {
        SourceType.ARXIV:         0.95,  # 学術論文
        SourceType.WIKIPEDIA:     0.90,  # 公式ドキュメント相当
        SourceType.PDF:           0.80,  # 専門サイト / 技術ブログ
        SourceType.GITHUB:        0.80,
        SourceType.STACKOVERFLOW: 0.50,  # Q&A / SNS
        SourceType.YOUTUBE:       0.65,  # 一般ブログ相当
        SourceType.WEB:           0.65,  # 一般Web
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

    # クォータ判定の安全マージン
    GEMINI_SAFE_REMAINING   = 100   # 残100以下でGemini不可
    YOUTUBE_SAFE_REMAINING  = 1000  # 残1000 units 以下でYouTube API 不可

    def __init__(
        self,
        brain_name: str,
        knowledge_store: Optional[KnowledgeStore] = None,
        api_usage_repo: Optional[APIUsageRepository] = None,
        rate_limiter: Optional[APIRateLimiter] = None,
        web_fetcher: Optional[WebFetcher] = None,
        pdf_fetcher: Optional[PDFFetcher] = None,
        video_fetcher: Optional[VideoFetcher] = None,
        code_fetcher: Optional[CodeFetcher] = None,
    ) -> None:
        """
        Initialize ResourceFinder.

        Args:
            brain_name: Brain名
            knowledge_store: KnowledgeStore（重複チェック用）
            api_usage_repo: APIUsageRepository（レート制限確認用）
        """
        self.brain_name      = brain_name
        self.knowledge_store = knowledge_store
        self.api_usage_repo  = api_usage_repo
        self.rate_limiter    = rate_limiter
        self.web_fetcher     = web_fetcher or WebFetcher()
        self.pdf_fetcher     = pdf_fetcher or PDFFetcher()
        self.video_fetcher   = video_fetcher or VideoFetcher()
        self.code_fetcher    = code_fetcher or CodeFetcher(
            github_token=self._resolve_github_token()
        )

    def _get_source_priority(self, is_tech_brain: bool) -> List[SourceType]:
        """Brain性質に応じてソース優先度を取得"""
        return self.TECH_BRAIN_SOURCES if is_tech_brain else self.GENERAL_BRAIN_SOURCES

    def _check_api_quota(self) -> Dict[str, bool]:
        """
        各APIのクォータ残量を確認。

        Returns:
            {api_name: quota_available (True=使用可)}
        """
        if self.rate_limiter:
            quota_status: Dict[str, bool] = {}
            for api_name in ("gemini", "youtube", "github", "stackoverflow"):
                allowed, status, _ = self.rate_limiter.check_usage_before_call(api_name)
                quota_status[api_name] = allowed and status != RateLimitStatus.STOP
            quota_status["arxiv"] = True
            return quota_status

        if not self.api_usage_repo:
            # リポジトリ未設定時は全て利用可能とみなす
            return {
                "gemini":        True,
                "youtube":       True,
                "github":        True,
                "stackoverflow": True,
                "arxiv":         True,
            }

        quota_status: Dict[str, bool] = {}

        # Gemini: 残トークン 100 以下で制限
        # 修正: get_remaining_quota() は存在しない → get_remaining_tokens() を使用
        try:
            remaining = self.api_usage_repo.get_remaining_tokens("gemini")
            # None は「無制限 / 閾値未設定」なので利用可
            quota_status["gemini"] = (remaining is None) or (remaining >= self.GEMINI_SAFE_REMAINING)
        except Exception:
            quota_status["gemini"] = False

        # YouTube: yt-dlp 字幕取得は無制限。YouTube Data API 利用時のみ制限確認。
        # get_remaining_requests() でリクエスト残量を確認
        try:
            remaining = self.api_usage_repo.get_remaining_requests("youtube")
            quota_status["youtube"] = (remaining is None) or (remaining >= self.YOUTUBE_SAFE_REMAINING)
        except Exception:
            quota_status["youtube"] = True  # yt-dlp は制限なし

        # GitHub / StackOverflow / arXiv は無制限 or 制限が緩いので常にTrue
        quota_status["github"]        = True
        quota_status["stackoverflow"] = True
        quota_status["arxiv"]         = True

        return quota_status

    def _has_seen_url(self, url: str) -> bool:
        """
        Learning Timeline で URL を確認済みか判定。

        Returns:
            既出なら True
        """
        if not self.knowledge_store:
            return False

        try:
            existing = self.knowledge_store.check_url_in_timeline(url)
            return bool(existing)
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
            subskill_id:    対象SubSkill
            query:          学習クエリ（Step1で生成）
            is_tech_brain:  Tech系 Brain かどうか
            max_candidates: 最大候補数

        Returns:
            ResourceCandidate のリスト
        """
        # TODO: Phase 4-5: Implement actual resource search with timeout
        # Currently returns empty to prevent hanging on network requests
        # The Fetcher integration (WEB, ARXIV, YOUTUBE, GITHUB, etc.) is in progress
        return []
        
        # import asyncio
        # 
        # source_priority = self._get_source_priority(is_tech_brain)
        # quota_status    = self._check_api_quota()
        # candidates: List[ResourceCandidate] = []
        #
        # for source_type in source_priority:
        #     if len(candidates) >= max_candidates:
        #         break
        #
        #     if not self._should_use_source(source_type, quota_status):
        #         continue
        #
        #     try:
        #         # Add timeout per source to prevent hanging
        #         results = await asyncio.wait_for(
        #             self._search_by_source(source_type, query, subskill_id),
        #             timeout=3.0,  # 3 second timeout per source
        #         )
        #         for result in results:
        #             if not self._has_seen_url(result.url):
        #                 candidates.append(result)
        #                 if len(candidates) >= max_candidates:
        #                     break
        #     except asyncio.TimeoutError:
        #         # Skip this source on timeout
        #         pass
        #     except Exception:
        #         pass  # ソース別エラーは無視して次へ
        #
        # return candidates

    def _should_use_source(
        self, source_type: SourceType, quota_status: Dict[str, bool]
    ) -> bool:
        """ソースを使用可能か判定（クォータ・性質から）"""
        if source_type in (SourceType.ARXIV, SourceType.WIKIPEDIA, SourceType.WEB):
            return True  # 無制限ソース
        elif source_type == SourceType.PDF:
            return quota_status.get("gemini", False)  # 要約用 Gemini が必要
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
        ソース別の検索実行。

        TODO (Phase 4 タスク 4-4〜4-7):
            各 Fetcher クラスへの統合呼び出しを実装する。
            - WEB:           WebFetcher.search()
            - PDF / ARXIV:   PDFFetcher.search_arxiv()
            - YOUTUBE:       VideoFetcher.search()
            - GITHUB:        CodeFetcher.search_github()
            - STACKOVERFLOW: CodeFetcher.search_stackoverflow()

        Returns:
            ResourceCandidate のリスト（現在は空 = Fetcher統合待ち）
        """
        if source_type == SourceType.WEB:
            return await self._search_web(query, source_type=SourceType.WEB)
        if source_type == SourceType.WIKIPEDIA:
            return await self._search_web(
                f"site:wikipedia.org {query}",
                source_type=SourceType.WIKIPEDIA,
            )
        if source_type in (SourceType.ARXIV, SourceType.PDF):
            return await self._search_papers(query, source_type)
        if source_type == SourceType.YOUTUBE:
            return await self._search_youtube(query)
        if source_type in (SourceType.GITHUB, SourceType.STACKOVERFLOW):
            return await self._search_code_sources(query, source_type)
        return []

    def rank_candidates(
        self,
        candidates: List[ResourceCandidate],
    ) -> List[ResourceCandidate]:
        """
        候補をランク付け（信頼度・優先度ソート）。

        Returns:
            ランク付けされた候補リスト
        """
        return sorted(
            candidates,
            key=lambda c: (-c.initial_trust_score, c.priority),
        )

    def estimate_quota_impact(
        self,
        candidates: List[ResourceCandidate],
    ) -> Dict[str, int]:
        """
        候補の取得に伴うAPI使用量を推定。

        Returns:
            {api_name: 推定使用量}
        """
        impact: Dict[str, int] = {}
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

    # ======================================================================
    # Source-specific search implementations
    # ======================================================================

    async def _search_web(
        self,
        query: str,
        source_type: SourceType,
        max_results: int = 4,
    ) -> List[ResourceCandidate]:
        results = await self.web_fetcher.search(query, max_results=max_results)
        trust = self.TRUST_SCORES[source_type]

        return [
            ResourceCandidate(
                source_type=source_type,
                url=item.get("url", ""),
                title=item.get("title", ""),
                description=item.get("description", ""),
                initial_trust_score=trust,
                priority=index,
            )
            for index, item in enumerate(results, start=1)
            if item.get("url")
        ]

    async def _search_papers(
        self,
        query: str,
        source_type: SourceType,
        max_results: int = 4,
    ) -> List[ResourceCandidate]:
        papers = await self.pdf_fetcher.search_papers(query, max_results=max_results)
        trust = self.TRUST_SCORES[source_type]

        return [
            ResourceCandidate(
                source_type=source_type,
                url=item.get("pdf_url") or item.get("arxiv_url") or "",
                title=item.get("title", ""),
                description=item.get("summary", ""),
                initial_trust_score=trust,
                priority=index,
            )
            for index, item in enumerate(papers, start=1)
            if item.get("pdf_url") or item.get("arxiv_id")
        ]

    async def _search_youtube(
        self,
        query: str,
        max_results: int = 4,
    ) -> List[ResourceCandidate]:
        results = await self.video_fetcher.search(query, max_results=max_results)
        self._record_api_call("youtube", req_count=1)

        candidates = []
        for index, item in enumerate(results, start=1):
            url = item.get("url") or ""
            if not url:
                continue

            duration = item.get("duration")
            description = item.get("description") or ""
            if duration:
                description = f"{description} duration={duration}s".strip()

            candidates.append(
                ResourceCandidate(
                    source_type=SourceType.YOUTUBE,
                    url=url,
                    title=item.get("title", ""),
                    description=description,
                    initial_trust_score=self.TRUST_SCORES[SourceType.YOUTUBE],
                    priority=index,
                )
            )
        return candidates

    async def _search_code_sources(
        self,
        query: str,
        source_type: SourceType,
        max_results: int = 4,
    ) -> List[ResourceCandidate]:
        bundle = await self.code_fetcher.search_code_solutions(
            query,
            max_github_results=max_results if source_type == SourceType.GITHUB else 0,
            max_so_results=max_results if source_type == SourceType.STACKOVERFLOW else 0,
        )

        candidates: List[ResourceCandidate] = []

        if source_type == SourceType.GITHUB:
            self._record_api_call("github", req_count=1)
            for index, repo in enumerate(bundle.get("github", []), start=1):
                trust = min(
                    0.95,
                    self.TRUST_SCORES[SourceType.GITHUB] + min(repo.stars, 5000) / 25000,
                )
                description = f"{repo.language} / stars={repo.stars}".strip(" /")
                candidates.append(
                    ResourceCandidate(
                        source_type=SourceType.GITHUB,
                        url=repo.url,
                        title=repo.repo_name,
                        description=description,
                        initial_trust_score=trust,
                        priority=index,
                    )
                )

        if source_type == SourceType.STACKOVERFLOW:
            self._record_api_call("stackoverflow", req_count=1)
            for index, qa in enumerate(bundle.get("stackoverflow", []), start=1):
                trust_bonus = 0.0
                if qa.accepted:
                    trust_bonus += 0.1
                trust_bonus += min(max(qa.score, 0), 50) / 500
                trust = min(0.85, self.TRUST_SCORES[SourceType.STACKOVERFLOW] + trust_bonus)
                candidates.append(
                    ResourceCandidate(
                        source_type=SourceType.STACKOVERFLOW,
                        url=qa.url,
                        title=qa.title,
                        description=f"score={qa.score}, accepted={qa.accepted}",
                        initial_trust_score=trust,
                        priority=index,
                    )
                )

        return candidates

    def _record_api_call(
        self,
        api_name: str,
        req_count: int = 1,
        token_count: int = 0,
        unit_count: int = 0,
    ) -> None:
        if self.rate_limiter:
            self.rate_limiter.record_api_call(
                api_name,
                req_count=req_count,
                token_count=token_count,
                unit_count=unit_count,
            )
            return

        if self.api_usage_repo:
            self.api_usage_repo.increment_usage(
                api_name,
                req_count=req_count,
                token_count=token_count,
                unit_count=unit_count,
            )

    @staticmethod
    def _resolve_github_token() -> Optional[str]:
        env_token = os.environ.get("GITHUB_TOKEN")
        if env_token:
            return env_token

        try:
            cfg = SBAConfig.load_env()
            return cfg.api_keys.github or None
        except Exception:
            return None
