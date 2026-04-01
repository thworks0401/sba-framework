"""
テキストチャンカー実装

設計根拠（補足設計書 §3.2）:
  - 目標チャンクサイズ: 400-600 トークン
  - 分割戦略: 文境界優先（句読点で区切り）
  - オーバーラップ: 50%
  - コード: 関数単位で保持
  - 動画字幕: 30-60秒セグメント単位
"""

from __future__ import annotations

import re
from typing import Optional


class ChunkerError(Exception):
    """Chunker 操作に関する例外"""


class SimpleTokenizer:
    """
    簡易トークナイザー（正確な BPE ではなく、概算トークン数を計算）.

    BPE トークナイザーの代替として使用。
    概算: 日本語は 1 文字 = 1 トークン、英語は 1 単語 = 1.3 トークン。
    """

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        テキスト内のトークン数を概算.

        Args:
            text: テキスト

        Returns:
            推定トークン数
        """
        # 日本語・中国語・全角文字
        cjk_chars = sum(1 for c in text if ord(c) >= 0x4E00 and ord(c) <= 0x9FFF)

        # 英数字・記号を単語として count
        # 簡略: スペース区切りで分割
        words = text.split()
        english_words = sum(1 for w in words if any(c.isascii() and c.isalnum() for c in w))

        # 概算
        tokens = cjk_chars + int(english_words * 1.3)

        return max(tokens, 1)


class TextChunker:
    """
    テキストチャンカー。

    目標サイズ: 400-600 トークン
    分割戦略: 文境界優先
    オーバーラップ: 50%
    """

    TARGET_TOKENS = 500       # 目標
    MIN_TOKENS = 400
    MAX_TOKENS = 600
    OVERLAP_RATIO = 0.5  # 50%

    def __init__(self) -> None:
        self.tokenizer = SimpleTokenizer()

    def chunk_text(
        self,
        text: str,
        min_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ) -> list[str]:
        """
        テキストをチャンク分割.

        Args:
            text: 入力テキスト
            min_tokens: 最小トークン数（デフォルト 400）
            max_tokens: 最大トークン数（デフォルト 600）

        Returns:
            チャンク文字列リスト
        """
        min_tokens = min_tokens or self.MIN_TOKENS
        max_tokens = max_tokens or self.MAX_TOKENS

        if not text.strip():
            return []

        # 1. 段落で分割
        paragraphs = re.split(r'\n\n+', text)

        chunks = []

        for paragraph in paragraphs:
            if not paragraph.strip():
                continue

            # 2. 段落をさらに文で分割
            sentences = self._split_sentences(paragraph)

            # 3. 文を結合してチャンク化（トークン数ベース）
            chunk_buffer = []
            buffer_tokens = 0

            for sentence in sentences:
                sentence_tokens = self.tokenizer.estimate_tokens(sentence)

                # バッファに追加できるか判定
                test_tokens = buffer_tokens + sentence_tokens
                test_text = " ".join(chunk_buffer) + (" " if chunk_buffer else "") + sentence

                if test_tokens < min_tokens:
                    # バッファに追加
                    chunk_buffer.append(sentence)
                    buffer_tokens = test_tokens

                elif min_tokens <= test_tokens <= max_tokens:
                    # ちょうど良い
                    chunk_buffer.append(sentence)
                    chunks.append(" ".join(chunk_buffer))
                    chunk_buffer = []
                    buffer_tokens = 0

                else:
                    # オーバー → 現バッファを確定、新バッファ開始
                    if chunk_buffer:
                        chunks.append(" ".join(chunk_buffer))

                    chunk_buffer = [sentence]
                    buffer_tokens = sentence_tokens

            # 残りをチャンク化
            if chunk_buffer:
                chunks.append(" ".join(chunk_buffer))

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """
        テキストを文で分割（句点・ピリオド区切り）.

        Returns:
            文リスト
        """
        # 。で分割（日本語主体のテキスト）
        # 最後のピリオドも含める
        pattern = r'[。.!?！？]+'

        sentences = re.split(pattern, text)

        # 分割マーカ再挿入
        matches = re.finditer(pattern, text)
        result = []

        prev_end = 0
        for match in matches:
            sentence = text[prev_end:match.end()].strip()
            if sentence:
                result.append(sentence)
            prev_end = match.end()

        # 最後の部分
        if prev_end < len(text):
            last = text[prev_end:].strip()
            if last:
                result.append(last)

        return [s for s in result if s]

    def chunk_code(self, code_text: str) -> list[str]:
        """
        コードテキストをチャンク（関数単位で保持）.

        設計：コードブロックは関数・クラス単位で分割し、
        テキストより粗い粒度で保持。

        Returns:
            コードはチャンクリスト
        """
        # 簡略実装: def / class で分割
        # 本実装では AST パーサを使用
        pattern = r'^(def |class )'

        lines = code_text.split('\n')
        chunks = []
        buffer = []

        for line in lines:
            if re.match(pattern, line) and buffer:
                # 新関数・クラス開始 → 前のチャンク確定
                chunks.append('\n'.join(buffer))
                buffer = [line]
            else:
                buffer.append(line)

        if buffer:
            chunks.append('\n'.join(buffer))

        return chunks

    def chunk_video_transcript(
        self,
        transcript_text: str,
        timestamp_format: bool = True,
    ) -> list[dict]:
        """
        動画字幕をセグメント化（30-60秒）.

        Args:
            transcript_text: 字幕テキスト（タイムスタンプ付き可）
            timestamp_format: タイムスタンプ形式かどうか

        Returns:
            [
                {
                    "text": str,
                    "start_time": float,  # 秒
                    "end_time": float,
                },
                ...
            ]
        """
        # タイムスタンプ形式の字幕を想定
        # 例: "00:00:05,123 --> 00:00:10,456\nテキスト\n\n"

        pattern = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'

        lines = transcript_text.split('\n')

        segments = []
        current_segment = []
        start_time = None

        for line in lines:
            match = re.search(pattern, line)

            if match:
                # タイムスタンプ行
                h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())

                start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
                end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000

                if start_time is None:
                    start_time = start

                current_segment.append({
                    "start": start,
                    "end": end,
                    "text": "",
                })

            elif line.strip() and current_segment:
                # テキスト行
                current_segment[-1]["text"] += line.strip() + " "

            elif not line.strip() and current_segment:
                # 空行 → セグメント確定
                segment_text = " ".join(s["text"] for s in current_segment).strip()

                if segment_text:
                    segments.append({
                        "text": segment_text,
                        "start_time": current_segment[0]["start"],
                        "end_time": current_segment[-1]["end"],
                    })

                current_segment = []

        # 残り
        if current_segment:
            segment_text = " ".join(s["text"] for s in current_segment).strip()
            if segment_text:
                segments.append({
                    "text": segment_text,
                    "start_time": current_segment[0]["start"],
                    "end_time": current_segment[-1]["end"],
                })

        return segments

    def estimate_chunks_count(self, text: str) -> int:
        """
        テキストをチャンク分割した時の概算チャンク数.

        Algorithm: 全トークン数 / 目標トークン数 * オーバーラップ補正
        """
        total_tokens = self.tokenizer.estimate_tokens(text)
        num_chunks = max(1, total_tokens // self.TARGET_TOKENS)

        # オーバーラップ補正
        num_chunks = int(num_chunks * (1 + self.OVERLAP_RATIO / 2))

        return num_chunks
