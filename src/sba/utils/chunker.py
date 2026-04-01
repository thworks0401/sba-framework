"""
テキストチャンカー実装

設計根拠（補足設計書 §3.2）:
  - 目標チャンクサイズ: 400-600 トークン
  - 分割戦略: 文境界優先（句読点で区切り）
  - オーバーラップ: 50 トークン固定（設計書では「50 tokens」と明記）
  - コード: 関数単位で保持
  - 動画字幕: 30-60秒セグメント単位

【修正履歴】
  旧実装: OVERLAP_RATIO = 0.5（50% 比率）→ 500トークンのチャンクで250トークンの
          オーバーラップになり、設計書の意図より大幅に多い重複データが生成されていた。
  新実装: OVERLAP_TOKENS = 50 の固定値に変更。
          chunk_text() でチャンク確定後、次のチャンクの先頭に前チャンクの末尾50トークン
          相当の文を引き継ぐ実装に修正。
"""

from __future__ import annotations

import re
from typing import Optional


class ChunkerError(Exception):
    """Chunker 操作に関する例外"""


class SimpleTokenizer:
    """
    簡易トークナイザー（正確な BPE ではなく、概算トークン数を計算）。

    概算: 日本語は 1 文字 = 1 トークン、英語は 1 単語 = 1.3 トークン。
    """

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        テキスト内のトークン数を概算。

        Args:
            text: テキスト

        Returns:
            推定トークン数（最低 1）
        """
        # 日本語・中国語・全角文字（CJK統一漢字）
        cjk_chars = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)

        # 英数字を単語として count
        words = text.split()
        english_words = sum(1 for w in words if any(c.isascii() and c.isalnum() for c in w))

        return max(cjk_chars + int(english_words * 1.3), 1)


class TextChunker:
    """
    テキストチャンカー。

    目標サイズ: 400-600 トークン
    分割戦略: 文境界優先
    オーバーラップ: 50 トークン固定（補足設計書 §3.2）
    """

    TARGET_TOKENS  = 500   # 目標チャンクサイズ（トークン）
    MIN_TOKENS     = 400   # 最小チャンクサイズ
    MAX_TOKENS     = 600   # 最大チャンクサイズ
    DROP_BELOW     = 50    # このトークン数未満のチャンクは破棄（補足設計書§3.2）
    OVERLAP_TOKENS = 50    # オーバーラップ: 50 トークン固定（比率ではなく固定値）

    def __init__(self) -> None:
        self.tokenizer = SimpleTokenizer()

    def chunk_text(
        self,
        text: str,
        min_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ) -> list[str]:
        """
        テキストをチャンク分割。

        各チャンク確定後、次のチャンクの先頭に前チャンク末尾 50 トークン分の
        文を引き継ぐことでオーバーラップを実現する。

        Args:
            text: 入力テキスト
            min_tokens: 最小トークン数（デフォルト 400）
            max_tokens: 最大トークン数（デフォルト 600）

        Returns:
            チャンク文字列リスト（50 トークン未満は除去済み）
        """
        min_tokens = min_tokens or self.MIN_TOKENS
        max_tokens = max_tokens or self.MAX_TOKENS

        if not text.strip():
            return []

        # --- Step 1: 段落 → 文に展開 ---
        sentences: list[str] = []
        for paragraph in re.split(r'\n\n+', text):
            if paragraph.strip():
                sentences.extend(self._split_sentences(paragraph))

        if not sentences:
            return []

        # --- Step 2: 文を結合してチャンク化（オーバーラップ付き） ---
        chunks: list[str] = []
        buffer: list[str] = []
        buffer_tokens: int = 0
        # オーバーラップとして次のチャンクに引き継ぐ文群
        overlap_carry: list[str] = []

        for sentence in sentences:
            sentence_tokens = self.tokenizer.estimate_tokens(sentence)

            # オーバーラップ引き継ぎで新バッファを初期化
            if not buffer and overlap_carry:
                buffer = list(overlap_carry)
                buffer_tokens = sum(self.tokenizer.estimate_tokens(s) for s in buffer)

            test_tokens = buffer_tokens + sentence_tokens

            if test_tokens < min_tokens:
                # まだ足りない → バッファに積む
                buffer.append(sentence)
                buffer_tokens = test_tokens

            elif min_tokens <= test_tokens <= max_tokens:
                # ちょうど良いサイズ → チャンク確定
                buffer.append(sentence)
                chunk_text = " ".join(buffer)
                chunks.append(chunk_text)
                # オーバーラップ: 確定チャンク末尾から 50 トークン分の文を引き継ぐ
                overlap_carry = self._extract_overlap_sentences(buffer)
                buffer = []
                buffer_tokens = 0

            else:
                # オーバー → 現バッファを確定してから新バッファ開始
                if buffer:
                    chunk_text = " ".join(buffer)
                    chunks.append(chunk_text)
                    overlap_carry = self._extract_overlap_sentences(buffer)

                buffer = [sentence]
                buffer_tokens = sentence_tokens

        # 残りを処理
        if buffer:
            # オーバーラップ引き継ぎを含む場合は既にバッファに入っている
            chunk_text = " ".join(buffer)
            chunks.append(chunk_text)

        # DROP_BELOW 未満は除去
        return [c for c in chunks if self.tokenizer.estimate_tokens(c) >= self.DROP_BELOW]

    def _extract_overlap_sentences(self, sentences: list[str]) -> list[str]:
        """
        文リストの末尾から OVERLAP_TOKENS トークン分の文を抽出してオーバーラップ用に返す。

        Args:
            sentences: チャンクを構成する文リスト

        Returns:
            オーバーラップとして次チャンクに引き継ぐ文リスト
        """
        carry: list[str] = []
        total = 0

        # 末尾から積み上げて OVERLAP_TOKENS に達するまで取得
        for sentence in reversed(sentences):
            tokens = self.tokenizer.estimate_tokens(sentence)
            if total + tokens > self.OVERLAP_TOKENS:
                break
            carry.insert(0, sentence)
            total += tokens

        return carry

    def _split_sentences(self, text: str) -> list[str]:
        """
        テキストを文で分割（句点・ピリオド区切り）。

        Returns:
            文リスト
        """
        pattern = r'[。.!?！？]+'
        result: list[str] = []
        prev_end = 0

        for match in re.finditer(pattern, text):
            sentence = text[prev_end:match.end()].strip()
            if sentence:
                result.append(sentence)
            prev_end = match.end()

        # 末尾の句点なし部分
        if prev_end < len(text):
            last = text[prev_end:].strip()
            if last:
                result.append(last)

        return [s for s in result if s]

    def chunk_code(self, code_text: str) -> list[str]:
        """
        コードテキストをチャンク（関数・クラス単位で保持）。

        設計：コードブロックは関数・クラス単位で分割し、
        テキストより粗い粒度で保持。

        Note:
            現実装は def/class の行頭マッチ。
            デコレータ付き関数（@decorator\\ndef func）や
            インデントされた内部クラスの取りこぼしがある。
            Phase 4 タスク 4-7（GitHub/コードソース）実装前に
            AST パーサ（ast.parse）への置き換えを推奨。

        Returns:
            コードチャンクリスト
        """
        pattern = r'^(def |class )'
        lines = code_text.split('\n')
        chunks: list[str] = []
        buffer: list[str] = []

        for line in lines:
            if re.match(pattern, line) and buffer:
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
        動画字幕をセグメント化（30-60秒）。

        Args:
            transcript_text: 字幕テキスト（タイムスタンプ付き可）
            timestamp_format: タイムスタンプ形式かどうか

        Returns:
            [{"text": str, "start_time": float, "end_time": float}, ...]
        """
        pattern = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
        lines = transcript_text.split('\n')
        segments: list[dict] = []
        current_segment: list[dict] = []

        for line in lines:
            match = re.search(pattern, line)

            if match:
                h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())
                start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
                end   = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
                current_segment.append({"start": start, "end": end, "text": ""})

            elif line.strip() and current_segment:
                current_segment[-1]["text"] += line.strip() + " "

            elif not line.strip() and current_segment:
                segment_text = " ".join(s["text"] for s in current_segment).strip()
                if segment_text:
                    segments.append({
                        "text":       segment_text,
                        "start_time": current_segment[0]["start"],
                        "end_time":   current_segment[-1]["end"],
                    })
                current_segment = []

        # 残り
        if current_segment:
            segment_text = " ".join(s["text"] for s in current_segment).strip()
            if segment_text:
                segments.append({
                    "text":       segment_text,
                    "start_time": current_segment[0]["start"],
                    "end_time":   current_segment[-1]["end"],
                })

        return segments

    def estimate_chunks_count(self, text: str) -> int:
        """
        テキストをチャンク分割した時の概算チャンク数。

        Algorithm: 全トークン数 / 目標トークン数（オーバーラップ補正込み）
        """
        total_tokens = self.tokenizer.estimate_tokens(text)
        effective_chunk = self.TARGET_TOKENS - self.OVERLAP_TOKENS  # 実質進み幅
        return max(1, int(total_tokens / effective_chunk))
