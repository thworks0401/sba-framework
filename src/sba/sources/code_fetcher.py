"""
Step3 コードソース: GitHub API + Stack Overflow API

設計根拠（学習ソース設定書 §6）:
  - GitHub API で README・code・Issues 取得
  - ヘッダからレート残量を毎回記録
  - Stack Overflow API でQ&Aペア取得
  - 投票数・採択フラグを信頼スコアの補助指標として格納
"""

from __future__ import annotations

import asyncio
from typing import Optional, List, Dict
from dataclasses import dataclass
from datetime import datetime

import aiohttp


class CodeFetchError(Exception):
    """コード取得エラー"""


@dataclass
class GitHubResult:
    """GitHub検索結果"""
    repo_name: str
    url: str
    readme_content: str = ""
    code_snippets: List[str] = None  # ファイルパスまたはコード片
    issues: List[Dict] = None  # [{title, body, labels}]
    stars: int = 0
    language: str = ""
    last_updated: str = ""
    error: Optional[str] = None


@dataclass
class StackOverflowResult:
    """Stack Overflow Q&A"""
    question_id: int
    title: str
    question_body: str
    answer_body: str = ""
    score: int = 0  # 質問スコア
    answer_score: int = 0  # 回答スコア
    accepted: bool = False  # 採択フラグ
    tags: List[str] = None
    url: str = ""
    error: Optional[str] = None


class GitHubFetcher:
    """
    GitHub API v3 を使ったリポジトリ・コード検索。
    """

    BASE_URL = "https://api.github.com"
    TIMEOUT_SECONDS = 10

    def __init__(self, access_token: Optional[str] = None) -> None:
        """
        Initialize GitHubFetcher.

        Args:
            access_token: GitHub Personal Access Token
        """
        self.access_token = access_token
        self.remaining_quota = 5000  # 認証時の初期値

    def _get_headers(self) -> Dict[str, str]:
        """API リクエストヘッダを生成"""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.access_token:
            headers["Authorization"] = f"token {self.access_token}"
        return headers

    async def _update_rate_limit(self, response_headers: Dict) -> None:
        """レート制限情報をヘッダから抽出・記録"""
        try:
            remaining = int(response_headers.get("X-RateLimit-Remaining", 0))
            self.remaining_quota = remaining
        except Exception:
            pass

    async def search_repositories(
        self,
        query: str,
        max_results: int = 5,
    ) -> List[GitHubResult]:
        """
        リポジトリを検索。

        Args:
            query: 検索クエリ（e.g., "python asyncio"）
            max_results: 最大結果数

        Returns:
            GitHubResult のリスト
        """
        try:
            search_url = f"{self.BASE_URL}/search/repositories"
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": max_results,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    search_url,
                    headers=self._get_headers(),
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    self._await_update_rate_limit(resp.headers)

                    if resp.status != 200:
                        raise CodeFetchError(f"GitHub API: HTTP {resp.status}")

                    data = await resp.json()
                    results = []

                    for item in data.get("items", [])[:max_results]:
                        result = GitHubResult(
                            repo_name=item["full_name"],
                            url=item["html_url"],
                            stars=item["stargazers_count"],
                            language=item.get("language", ""),
                            last_updated=item["updated_at"],
                        )
                        results.append(result)

                    return results

        except Exception as e:
            raise CodeFetchError(f"Repository search failed: {str(e)}")

    async def fetch_readme(self, repo_full_name: str) -> str:
        """
        リポジトリの README を取得。

        Args:
            repo_full_name: Owner/Repo形式

        Returns:
            README テキスト
        """
        try:
            url = f"{self.BASE_URL}/repos/{repo_full_name}/readme"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={
                        **self._get_headers(),
                        "Accept": "application/vnd.github.raw",
                    },
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    self._await_update_rate_limit(resp.headers)

                    if resp.status == 200:
                        return await resp.text()
                    elif resp.status == 404:
                        return ""  # README 不在
                    else:
                        raise CodeFetchError(f"HTTP {resp.status}")

        except Exception as e:
            return ""

    async def fetch_issues(self, repo_full_name: str, max_issues: int = 10) -> List[Dict]:
        """
        リポジトリの Issues を取得。

        Args:
            repo_full_name: Owner/Repo形式
            max_issues: 最大件数

        Returns:
            [{title, body, labels}] のリスト
        """
        try:
            url = f"{self.BASE_URL}/repos/{repo_full_name}/issues"
            params = {
                "state": "all",
                "per_page": max_issues,
                "sort": "updated",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self._get_headers(),
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    self._await_update_rate_limit(resp.headers)

                    if resp.status != 200:
                        raise CodeFetchError(f"HTTP {resp.status}")

                    data = await resp.json()
                    return [
                        {
                            "title": issue["title"],
                            "body": issue["body"] or "",
                            "labels": [label["name"] for label in issue.get("labels", [])],
                        } for issue in data
                    ]

        except Exception as e:
            return []

    def get_remaining_quota(self) -> int:
        """残りレート制限を取得"""
        return self.remaining_quota

    async def _await_update_rate_limit(self, response_headers: Dict) -> None:
        """非同期wrapper"""
        self._update_rate_limit(response_headers)


class StackOverflowFetcher:
    """
    Stack Overflow API を使ったQ&A検索。
    """

    BASE_URL = "https://api.stackexchange.com/2.3"
    TIMEOUT_SECONDS = 10
    DAILY_LIMIT = 10000  # 日次制限

    def __init__(self) -> None:
        self.daily_used = 0

    async def search_questions(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        max_results: int = 5,
    ) -> List[StackOverflowResult]:
        """
        質問を検索・取得。

        Args:
            query: 検索クエリ
            tags: タグ一覧（絞り込み用）
            max_results: 最大結果数

        Returns:
            StackOverflowResult のリスト
        """
        try:
            search_url = f"{self.BASE_URL}/search/advanced"
            params = {
                "q": query,
                "site": "stackoverflow",
                "sort": "votes",
                "order": "desc",
                "pagesize": max_results,
                "filter": "withbody",  # 本文を含める
            }

            if tags:
                params["tags"] = ";".join(tags)

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    search_url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    # レート制限を記録
                    try:
                        self.daily_used += 1  # 1リクエスト = 1単位
                    except Exception:
                        pass

                    if resp.status != 200:
                        raise CodeFetchError(f"SO API: HTTP {resp.status}")

                    data = await resp.json()
                    results = []

                    for item in data.get("items", [])[:max_results]:
                        result = StackOverflowResult(
                            question_id=item["question_id"],
                            title=item["title"],
                            question_body=item["body"],
                            score=item["score"],
                            tags=item.get("tags", []),
                            url=item["link"],
                        )

                        # 採択回答があればそれも取得
                        if "accepted_answer_id" in item:
                            result.accepted = True
                            # ※実装注：別途API呼び出しが必要（省略）

                        results.append(result)

                    return results

        except Exception as e:
            raise CodeFetchError(f"Question search failed: {str(e)}")

    def get_daily_quota_used(self) -> int:
        """日次使用量を取得"""
        return self.daily_used


class CodeFetcher:
    """
    GitHub API + Stack Overflow API 統合フェッチャー。
    """

    def __init__(
        self,
        github_token: Optional[str] = None,
    ) -> None:
        self.github = GitHubFetcher(github_token)
        self.stackoverflow = StackOverflowFetcher()

    async def search_code_solutions(
        self,
        query: str,
        max_github_results: int = 3,
        max_so_results: int = 3,
    ) -> Dict:
        """
        GitHub リポジトリと Stack Overflow の Q&A を検索。

        Args:
            query: 検索クエリ
            max_github_results: GitHub 最大件数
            max_so_results: Stack Overflow 最大件数

        Returns:
            {github_results, stackoverflow_results}
        """
        tasks = [
            self.github.search_repositories(query, max_github_results),
            self.stackoverflow.search_questions(query, max_results=max_so_results),
        ]

        try:
            github_results, so_results = await asyncio.gather(*tasks)
            return {
                "github": github_results,
                "stackoverflow": so_results,
            }
        except Exception as e:
            raise CodeFetchError(f"Multi-source search failed: {str(e)}")

    async def fetch_repository_full_content(
        self,
        repo_full_name: str,
    ) -> GitHubResult:
        """
        リポジトリから README・Issues を統合取得。

        Args:
            repo_full_name: Owner/Repo形式

        Returns:
            GitHubResult
        """
        try:
            result = GitHubResult(
                repo_name=repo_full_name,
                url=f"https://github.com/{repo_full_name}",
            )

            # 並行取得
            tasks = [
                self.github.fetch_readme(repo_full_name),
                self.github.fetch_issues(repo_full_name),
            ]

            readme, issues = await asyncio.gather(*tasks)
            result.readme_content = readme
            result.issues = issues

            return result

        except Exception as e:
            return GitHubResult(
                repo_name=repo_full_name,
                url=f"https://github.com/{repo_full_name}",
                error=str(e),
            )

    def get_github_quota_status(self) -> Dict[str, int]:
        """GitHub API クォータ状態を取得"""
        return {
            "remaining": self.github.get_remaining_quota(),
            "daily_limit": 5000,
        }

    def get_stackoverflow_quota_status(self) -> Dict[str, int]:
        """Stack Overflow API クォータ状態を取得"""
        return {
            "daily_used": self.stackoverflow.get_daily_quota_used(),
            "daily_limit": self.stackoverflow.DAILY_LIMIT,
        }
