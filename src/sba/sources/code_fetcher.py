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
import re
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
                    await self._await_update_rate_limit(resp.headers)

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
                    await self._await_update_rate_limit(resp.headers)

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
                    await self._await_update_rate_limit(resp.headers)

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

    async def fetch_code_snippets(
        self,
        repo_full_name: str,
        max_files: int = 3,
    ) -> List[str]:
        """
        リポジトリから代表的なコード断片を抽出。

        まず default branch を取得し、そのツリーから主要言語ファイルを数件拾う。
        """
        try:
            repo_url = f"{self.BASE_URL}/repos/{repo_full_name}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    repo_url,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    await self._await_update_rate_limit(resp.headers)
                    if resp.status != 200:
                        return []
                    repo_data = await resp.json()

                branch = repo_data.get("default_branch", "main")
                tree_url = f"{self.BASE_URL}/repos/{repo_full_name}/git/trees/{branch}"
                async with session.get(
                    tree_url,
                    headers=self._get_headers(),
                    params={"recursive": 1},
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    await self._await_update_rate_limit(resp.headers)
                    if resp.status != 200:
                        return []
                    tree_data = await resp.json()

                code_entries = [
                    item for item in tree_data.get("tree", [])
                    if item.get("type") == "blob"
                    and item.get("path", "").endswith(
                        (".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".cpp", ".c", ".cs")
                    )
                ][:max_files]

                snippets: List[str] = []
                for entry in code_entries:
                    blob_url = entry.get("url")
                    if not blob_url:
                        continue
                    async with session.get(
                        blob_url,
                        headers=self._get_headers(),
                        timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                    ) as resp:
                        await self._await_update_rate_limit(resp.headers)
                        if resp.status != 200:
                            continue
                        blob_data = await resp.json()
                        encoded = blob_data.get("content", "")
                        if not encoded:
                            continue

                        import base64

                        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
                        preview = decoded[:1500].strip()
                        if preview:
                            snippets.append(f"# {entry.get('path')}\n{preview}")

                return snippets
        except Exception:
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

    async def fetch_answer(self, answer_id: int) -> str:
        """採択回答本文を取得。"""
        try:
            url = f"{self.BASE_URL}/answers/{answer_id}"
            params = {
                "site": "stackoverflow",
                "filter": "withbody",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    self.daily_used += 1
                    if resp.status != 200:
                        return ""
                    data = await resp.json()
                    items = data.get("items", [])
                    if not items:
                        return ""
                    return items[0].get("body", "")
        except Exception:
            return ""

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
                            result.answer_body = await self.fetch_answer(
                                item["accepted_answer_id"]
                            )

                        results.append(result)

                    return results

        except Exception as e:
            raise CodeFetchError(f"Question search failed: {str(e)}")

    async def fetch_question_detail(self, question_url_or_id: str | int) -> Optional[StackOverflowResult]:
        """質問本文と採択回答を取得。"""
        try:
            question_id = self._extract_question_id(question_url_or_id)
            if question_id is None:
                return None

            url = f"{self.BASE_URL}/questions/{question_id}"
            params = {
                "site": "stackoverflow",
                "filter": "withbody",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS),
                ) as resp:
                    self.daily_used += 1
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    items = data.get("items", [])
                    if not items:
                        return None

                    item = items[0]
                    result = StackOverflowResult(
                        question_id=item["question_id"],
                        title=item.get("title", ""),
                        question_body=item.get("body", ""),
                        score=item.get("score", 0),
                        tags=item.get("tags", []),
                        url=item.get("link", ""),
                    )

                    accepted_answer_id = item.get("accepted_answer_id")
                    if accepted_answer_id:
                        result.accepted = True
                        result.answer_body = await self.fetch_answer(accepted_answer_id)

                    return result
        except Exception:
            return None

    def get_daily_quota_used(self) -> int:
        """日次使用量を取得"""
        return self.daily_used

    @staticmethod
    def _extract_question_id(question_url_or_id: str | int) -> Optional[int]:
        if isinstance(question_url_or_id, int):
            return question_url_or_id

        text = str(question_url_or_id).strip()
        if text.isdigit():
            return int(text)

        match = re.search(r"/questions/(\d+)", text)
        if match:
            return int(match.group(1))
        return None


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
        try:
            tasks = []
            task_order = []

            if max_github_results > 0:
                tasks.append(self.github.search_repositories(query, max_github_results))
                task_order.append("github")
            if max_so_results > 0:
                tasks.append(self.stackoverflow.search_questions(query, max_results=max_so_results))
                task_order.append("stackoverflow")

            resolved = await asyncio.gather(*tasks) if tasks else []
            result_map = {name: value for name, value in zip(task_order, resolved)}
            return {
                "github": result_map.get("github", []),
                "stackoverflow": result_map.get("stackoverflow", []),
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
                self.github.fetch_code_snippets(repo_full_name),
            ]

            readme, issues, code_snippets = await asyncio.gather(*tasks)
            result.readme_content = readme
            result.issues = issues
            result.code_snippets = code_snippets

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
