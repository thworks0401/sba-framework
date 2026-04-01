"""
Step3 PDFソース: arXiv + PDFMiner + 要約

設計根拠（学習ソース設定書 §4）:
  - arXiv API でペーパー検索・PDF取得
  - PDFMiner でテキスト抽出
  - 見出し構造を可能な範囲で保持
  - 長文は Gemini（Tier2）で要約・構造化
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime

import aiohttp
import feedparser
from io import BytesIO

from ..inference.tier2 import Tier2Engine


class PDFError(Exception):
    """PDF処理エラー"""


@dataclass
class PDFContent:
    """抽出したPDFコンテンツ"""
    title: str = ""
    authors: List[str] = None
    abstract: str = ""
    full_text: str = ""
    sections: Dict[str, str] = None  # セクション名 → テキスト
    extracted_date: str = ""
    source_url: str = ""
    arxiv_id: str = ""  # arXiv ID
    error: Optional[str] = None


class ArXivSearcher:
    """arXiv API を使ったペーパー検索"""

    BASE_URL = "http://export.arxiv.org/api/query"
    TIMEOUT_SECONDS = 10

    @staticmethod
    async def search(
        query: str,
        max_results: int = 5,
        start_index: int = 0,
    ) -> List[Dict[str, str]]:
        """
        arXiv でペーパー検索。

        Args:
            query: 検索クエリ
            max_results: 最大結果数
            start_index: オフセット

        Returns:
            [{title, authors, arxiv_id, pdf_url, summary}] のリスト
        """
        try:
            # arXiv API クエリ（OpenSearch形式）
            params = {
                "search_query": f"all:{query}",
                "start": start_index,
                "max_results": max_results,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    ArXivSearcher.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=ArXivSearcher.TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status != 200:
                        raise PDFError(f"arXiv API: HTTP {resp.status}")

                    content = await resp.text()
                    feed = feedparser.parse(content)

                    results = []
                    for entry in feed.entries:
                        arxiv_id = entry.id.split("/abs/")[-1]
                        pdf_url = f"http://arxiv.org/pdf/{arxiv_id}.pdf"
                        authors = [auth.name for auth in entry.authors]

                        results.append({
                            "title": entry.title,
                            "authors": authors,
                            "arxiv_id": arxiv_id,
                            "pdf_url": pdf_url,
                            "summary": entry.summary,
                            "published": entry.published,
                        })
                    return results

        except Exception as e:
            raise PDFError(f"arXiv search failed: {str(e)}")

    @staticmethod
    async def fetch_pdf(pdf_url: str, timeout: float = 30.0) -> bytes:
        """
        PDFファイルをダウンロード。

        Args:
            pdf_url: PDF URL
            timeout: タイムアウト秒数

        Returns:
            PDFバイナリ
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    pdf_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        raise PDFError(f"HTTP {resp.status}")
                    return await resp.read()
        except Exception as e:
            raise PDFError(f"PDF download failed: {str(e)}")


class PDFTextExtractor:
    """PDFMiner を使ったテキスト抽出"""

    @staticmethod
    async def extract(pdf_bytes: bytes) -> PDFContent:
        """
        PDFからテキストを抽出。

        Args:
            pdf_bytes: PDFバイナリ

        Returns:
            PDFContent
        """
        try:
            # 非同期実行との互換性のため、同期的に実行
            from pdfminer.high_level import extract_text_to_fp
            from pdfminer.layout import LAParams
            import io

            output = io.StringIO()
            with io.BytesIO(pdf_bytes) as fp:
                # テキスト抽出
                extract_text_to_fp(
                    fp,
                    output,
                    laparams=LAParams(),
                )
            text = output.getvalue()

            # セクション構造を推測（見出しパターン）
            sections = PDFTextExtractor._extract_sections(text)

            return PDFContent(
                full_text=text,
                sections=sections,
                extracted_date=datetime.now().isoformat(),
            )

        except Exception as e:
            return PDFContent(error=f"PDF extraction failed: {str(e)}")

    @staticmethod
    def _extract_sections(text: str) -> Dict[str, str]:
        """
        テキストからセクション構造を推測。

        Args:
            text: 抽出テキスト

        Returns:
            {section_name: section_text}
        """
        sections = {}
        current_section = "Introduction"
        current_text = []

        # 一般的な見出しパターン（英論文想定）
        heading_pattern = r"^(Introduction|Abstract|Related Work|Method|Results?|Conclusion|References|1\.)\s*$"

        for line in text.split("\n"):
            if re.match(heading_pattern, line.strip(), re.IGNORECASE):
                # セクション切り替え
                if current_text:
                    sections[current_section] = "\n".join(current_text)
                current_section = line.strip()
                current_text = []
            else:
                current_text.append(line)

        # 最後のセクション
        if current_text:
            sections[current_section] = "\n".join(current_text)

        return sections


class PDFFetcher:
    """
    arXiv + PDFMiner + Gemini (Tier2) 統合フェッチャー。
    """

    def __init__(self, tier2_engine: Optional[Tier2Engine] = None) -> None:
        self.tier2_engine = tier2_engine or Tier2Engine()
        self.searcher = ArXivSearcher()
        self.extractor = PDFTextExtractor()

    async def search_papers(
        self,
        query: str,
        max_results: int = 5,
    ) -> List[Dict[str, str]]:
        """
        arXiv でペーパー検索。

        Args:
            query: 検索クエリ
            max_results: 最大結果数

        Returns:
            検索結果リスト
        """
        return await self.searcher.search(query, max_results)

    async def fetch_and_extract(
        self,
        arxiv_id: str,
        pdf_url: str,
        title: str = "",
        authors: Optional[List[str]] = None,
        summarize: bool = False,
    ) -> PDFContent:
        """
        arXiv ペーパーをダウンロード・抽出。

        Args:
            arxiv_id: arXiv ID
            pdf_url: PDF URL
            title: 論文タイトル
            authors: 著者一覧
            summarize: Tier2で要約するか

        Returns:
            PDFContent
        """
        try:
            # PDFダウンロード
            pdf_bytes = await self.searcher.fetch_pdf(pdf_url)

            # テキスト抽出
            content = await self.extractor.extract(pdf_bytes)
            content.arxiv_id = arxiv_id
            content.title = title
            content.authors = authors or []
            content.source_url = pdf_url

            # 要約処理（長文の場合）
            if summarize and len(content.full_text) > 5000:
                summary = await self._summarize_with_tier2(content.full_text)
                content.full_text = summary

            return content

        except Exception as e:
            return PDFContent(
                arxiv_id=arxiv_id,
                source_url=pdf_url,
                title=title,
                authors=authors or [],
                error=str(e),
            )

    async def _summarize_with_tier2(
        self,
        text: str,
        max_length: int = 2000,
    ) -> str:
        """
        Tier2（Gemini）を使って長文を要約。

        Args:
            text: 入力テキスト
            max_length: 最大要約長

        Returns:
            要約テキスト
        """
        try:
            result = await self.tier2_engine.summarize(
                text=text,
                max_length=max_length,
                temperature=0.3,
                timeout_s=30.0,
            )

            if result.error:
                return text[:max_length]  # フォールバック

            return result.text
        except Exception:
            return text[:max_length]

    async def search_and_fetch_papers(
        self,
        query: str,
        max_results: int = 3,
        summarize: bool = True,
    ) -> List[PDFContent]:
        """
        検索後、複数ペーパーを取得・抽出。

        Args:
            query: 検索クエリ
            max_results: 最大件数
            summarize: 要約するか

        Returns:
            PDFContent のリスト
        """
        try:
            # 検索
            search_results = await self.search_papers(query, max_results)

            # バッチ取得・抽出
            tasks = [
                self.fetch_and_extract(
                    arxiv_id=result["arxiv_id"],
                    pdf_url=result["pdf_url"],
                    title=result["title"],
                    authors=result.get("authors", []),
                    summarize=summarize,
                ) for result in search_results
            ]

            contents = await asyncio.gather(*tasks)
            return contents

        except Exception as e:
            return [PDFContent(error=f"Batch fetch failed: {str(e)}")]
