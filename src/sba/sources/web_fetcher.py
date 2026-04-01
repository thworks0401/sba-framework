"""
Step3 Webソース: DuckDuckGo + Jina Reader + Playwright

設計根拠（学習ソース設定書 §3）:
  - DuckDuckGo でURL一覧取得（APIキー不要・無料）
  - Jina Reader（r.jina.ai）でのMarkdown化（第一選択肢）
  - 失敗時のみPlaywright へフォールバック
  - 広告・ナビ除去・テキスト正規化の共通クリーニングパイプライン
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional, List, Dict
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from duckduckgo_search import DDGS
from playwright.async_api import async_playwright, Browser, Page


class WebFetchError(Exception):
    """Web取得エラー"""


@dataclass
class WebPageContent:
    """取得したWebページのコンテンツ"""
    url: str
    title: str = ""
    content: str = ""  # テキスト化・クリーニング済み
    source_format: str = "markdown"  # markdown / html / plain
    fetch_method: str = "jina"  # jina / playwright / direct
    error: Optional[str] = None


class WebCleaner:
    """テキストクリーニング・正規化"""

    # フィルタ対象パターン
    PATTERNS_TO_REMOVE = [
        r"\[Ad\].*?\[/Ad\]",  # 広告タグ
        r"<script.*?</script>",  # script
        r"<style.*?</style>",  # style
        r"<!--.*?-->",  # HTML comment
        r"\n\s*\n",  # 複数空行 → 1行
    ]

    # ナビゲーション・フッター除去パターン
    NAVIGATION_KEYWORDS = [
        "メニュー",
        "ナビゲーション",
        "カテゴリ",
        "関連記事",
        "コメント",
        "トラックバック",
        "著者について",
        "プライバシーポリシー",
        "利用規約",
        "サイトマップ",
    ]

    @staticmethod
    def clean_text(text: str) -> str:
        """
        テキストクリーニング・正規化。

        Args:
            text: 入力テキスト

        Returns:
            クリーニング済みテキスト
        """
        # パターン削除
        for pattern in WebCleaner.PATTERNS_TO_REMOVE:
            text = re.sub(pattern, " ", text, flags=re.DOTALL | re.IGNORECASE)

        # HTMLタグ除去
        text = re.sub(r"<[^>]+>", " ", text)

        # エンティティデコード（&#32; など）
        text = (
            text.replace("&nbsp;", " ")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
        )

        # 複数空白を1つにまとめる
        text = re.sub(r"\s+", " ", text)

        # 改行を整理
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)

        return text.strip()

    @staticmethod
    def remove_navigation_footer(text: str) -> str:
        """
        ナビゲーション・フッターを除去（ヒューリスティック）。

        Args:
            text: 入力テキスト

        Returns:
            除去済みテキスト
        """
        lines = text.split("\n")
        content_lines = []

        for line in lines:
            should_skip = False
            for keyword in WebCleaner.NAVIGATION_KEYWORDS:
                if keyword in line and len(line) < 50:
                    should_skip = True
                    break
            if not should_skip:
                content_lines.append(line)

        return "\n".join(content_lines)

    @staticmethod
    def normalize_whitespace(text: str) -> str:
        """空白・改行を正規化"""
        # タブをスペースに
        text = text.replace("\t", "    ")
        # 複数改行を2行にまとめる
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text


class JinaReader:
    """
    Jina Reader（r.jina.ai）経由でのWebページ取得。
    https://r.jina.ai/{URL} でMarkdown化。
    """

    JINA_ENDPOINT = "https://r.jina.ai"
    TIMEOUT_SECONDS = 10

    @staticmethod
    async def fetch(url: str, timeout: float = TIMEOUT_SECONDS) -> WebPageContent:
        """
        Jina Reader でページを取得。

        Args:
            url: ターゲットURL
            timeout: タイムアウト秒数

        Returns:
            WebPageContent
        """
        try:
            async with aiohttp.ClientSession() as session:
                jina_url = f"{JinaReader.JINA_ENDPOINT}/{url}"
                async with session.get(
                    jina_url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status != 200:
                        raise WebFetchError(f"HTTP {resp.status}")

                    content = await resp.text()
                    # Jina はMarkdown形式で返す
                    cleaned = WebCleaner.clean_text(content)
                    cleaned = WebCleaner.remove_navigation_footer(cleaned)
                    cleaned = WebCleaner.normalize_whitespace(cleaned)

                    return WebPageContent(
                        url=url,
                        content=cleaned,
                        source_format="markdown",
                        fetch_method="jina",
                    )
        except Exception as e:
            return WebPageContent(
                url=url,
                error=f"Jina Reader failed: {str(e)}",
            )


class PlaywrightFetcher:
    """
    Playwright 経由でのブラウザ自動化取得。
    SPA / JavaScript 必須サイト対応。
    """

    TIMEOUT_SECONDS = 20

    @staticmethod
    async def fetch(url: str, timeout: float = TIMEOUT_SECONDS) -> WebPageContent:
        """
        Playwright でページを取得（JavaScript実行）。

        Args:
            url: ターゲットURL
            timeout: タイムアウト秒数

        Returns:
            WebPageContent
        """
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                try:
                    await page.goto(url, timeout=int(timeout * 1000))
                    # メイン下 JavaScriptを実行させるため待機
                    await page.wait_for_load_state("networkidle")

                    # ページコンテンツをテキスト化
                    content = await page.content()
                    title = await page.title()

                    cleaned = WebCleaner.clean_text(content)
                    cleaned = WebCleaner.remove_navigation_footer(cleaned)
                    cleaned = WebCleaner.normalize_whitespace(cleaned)

                    return WebPageContent(
                        url=url,
                        title=title,
                        content=cleaned,
                        source_format="html",
                        fetch_method="playwright",
                    )
                finally:
                    await browser.close()

        except Exception as e:
            return WebPageContent(
                url=url,
                error=f"Playwright failed: {str(e)}",
            )


class WebFetcher:
    """
    Web検索・ページ取得の統合フッチャー。

    DuckDuckGo で検索 → URL一覧取得 → Jina Reader で Markdown化。
    失敗時は Playwright へフォールバック。
    """

    MAX_RESULTS_PER_SEARCH = 10
    RATE_LIMIT_THROTTLE_SEC = 1.0  # DuckDuckGo スロットリング

    def __init__(self) -> None:
        self.jina_reader = JinaReader()
        self.playwright_fetcher = PlaywrightFetcher()
        self.cleaner = WebCleaner()

    async def search(
        self,
        query: str,
        max_results: int = MAX_RESULTS_PER_SEARCH,
    ) -> List[Dict[str, str]]:
        """
        DuckDuckGo で検索。

        Args:
            query: 検索クエリ
            max_results: 最大結果数

        Returns:
            [{url, title, description}] のリスト
        """
        try:
            results = []
            # DuckDuckGo検索（同期API）
            with DDGS() as ddgs:
                for result in ddgs.text(query, max_results=max_results):
                    results.append(
                        {
                            "url": result.get("href", ""),
                            "title": result.get("title", ""),
                            "description": result.get("body", ""),
                        }
                    )
            return results
        except Exception as e:
            raise WebFetchError(f"DuckDuckGo search failed: {str(e)}")

    async def fetch_with_fallback(
        self,
        url: str,
        prefer_jina: bool = True,
    ) -> WebPageContent:
        """
        Jina Reader を試し、失敗時は Playwright へ自動フォールバック。

        Args:
            url: ターゲットURL
            prefer_jina: Jina 優先フラグ

        Returns:
            WebPageContent
        """
        if prefer_jina:
            # Jina を先に試す
            result = await JinaReader.fetch(url)
            if result.error is None:
                return result

            # Jina 失敗時は Playwright へ
            return await PlaywrightFetcher.fetch(url)
        else:
            #直接 Playwright
            return await PlaywrightFetcher.fetch(url)

    async def fetch_batch(
        self,
        urls: List[str],
        prefer_jina: bool = True,
    ) -> List[WebPageContent]:
        """
        複数URL をバッチ取得（並行実行）。

        Args:
            urls: URL リスト
            prefer_jina: Jina 優先フラグ

        Returns:
            WebPageContent のリスト
        """
        tasks = [
            self.fetch_with_fallback(url, prefer_jina) for url in urls
        ]
        return await asyncio.gather(*tasks)

    async def search_and_fetch(
        self,
        query: str,
        max_fetch: int = 5,
    ) -> List[WebPageContent]:
        """
        検索後、最初の max_fetch 件を取得。

        Args:
            query: 検索クエリ
            max_fetch: 最大取得件数

        Returns:
            WebPageContent のリスト
        """
        # 検索
        search_results = await self.search(query, max_results=max_fetch * 2)
        urls = [r["url"] for r in search_results if r["url"]][:max_fetch]

        # バッチ取得
        contents = await self.fetch_batch(urls)
        return contents
