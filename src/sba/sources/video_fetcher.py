"""
Step3 動画ソース: yt-dlp + faster-whisper + VRAM排他制御

設計根拠（学習ソース設定書 §5）:
  - yt-dlp で字幕を優先取得
  - 字幕なし時のみ WhisperTranscriber を呼び出す
  - VRAM排他制御と連携
  - 30-60秒セグメント単位でチャンク化
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from pathlib import Path
from datetime import timedelta

import yt_dlp

from ..sources.whisper_transcriber import WhisperTranscriber, TranscriptionResult


class VideoError(Exception):
    """動画処理エラー"""


@dataclass
class VideoSegment:
    """動画セグメント"""
    start_time: float  # 秒
    end_time: float
    text: str


@dataclass
class VideoContent:
    """動画の処理済みコンテンツ"""
    url: str
    title: str = ""
    duration_seconds: float = 0.0
    segments: List[VideoSegment] = None  # セグメント一覧
    full_transcript: str = ""  # 全テキスト
    source: str = ""  # "subtitle" / "transcription"
    error: Optional[str] = None


class SubtitleExtractor:
    """yt-dlp を使った字幕取得"""

    SUBTITLE_PREFER_ORDER = ["en", "ja", "zh-Hans", "auto"]

    @staticmethod
    async def extract_subtitles(video_url: str) -> Tuple[str, List[Dict]]:
        """
        yt-dlp で動画の字幕を取得。

        Args:
            video_url: YouTube URL

        Returns:
            (字幕言語コード, [{text, start, end}] のリスト)
        """
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "writesubtitles": True,
                "skip_download": True,
                "subtitlesformat": "vtt",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                available_langs = info.get("subtitles", {})

                if not available_langs:
                    return None, []

                # 優先言語で字幕を選択
                selected_lang = None
                for lang in SubtitleExtractor.SUBTITLE_PREFER_ORDER:
                    if lang in available_langs:
                        selected_lang = lang
                        break

                if not selected_lang:
                    selected_lang = list(available_langs.keys())[0]

                # 字幕テキストを取得
                subtitles = available_langs[selected_lang]
                segments = []

                for subtitle in subtitles:
                    start_sec = subtitle.get("start", 0.0)
                    end_sec = subtitle.get("end", 0.0)
                    text = subtitle.get("text", "").strip()

                    # タイムコードを削除（VTT形式から）
                    text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}", "", text)
                    text = text.strip()

                    if text:
                        segments.append({
                            "start": start_sec,
                            "end": end_sec,
                            "text": text,
                        })

                return selected_lang, segments

        except Exception as e:
            raise VideoError(f"Subtitle extraction failed: {str(e)}")

    @staticmethod
    async def get_video_info(video_url: str) -> Dict:
        """動画のメタデータを取得"""
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                return {
                    "title": info.get("title", ""),
                    "duration": info.get("duration", 0),  # 秒
                    "url": video_url,
                }

        except Exception as e:
            raise VideoError(f"Get video info failed: {str(e)}")


class VideoSegmenter:
    """動画テキストをセグメント化（30-60秒単位）"""

    TARGET_SEGMENT_DURATION = 45  # 秒

    @staticmethod
    def segment_by_time(
        segments: List[Dict],
        target_duration: float = TARGET_SEGMENT_DURATION,
    ) -> List[VideoSegment]:
        """
        字幕セグメントを時間単位で統合。

        Args:
            segments: [{start, end, text}] のリスト
            target_duration: 目標セグメント長（秒）

        Returns:
            VideoSegment のリスト
        """
        if not segments:
            return []

        combined_segments = []
        current_start = None
        current_text = []
        current_end = None

        for segment in segments:
            start = segment["start"]
            end = segment["end"]
            text = segment["text"]

            if current_start is None:
                current_start = start

            current_text.append(text)
            current_end = end

            # 目標時間に達したか判定
            if (current_end - current_start) >= target_duration:
                combined_segments.append(
                    VideoSegment(
                        start_time=current_start,
                        end_time=current_end,
                        text=" ".join(current_text),
                    )
                )
                current_start = None
                current_text = []
                current_end = None

        # 残りを追加
        if current_text:
            combined_segments.append(
                VideoSegment(
                    start_time=current_start,
                    end_time=current_end,
                    text=" ".join(current_text),
                )
            )

        return combined_segments


class VideoFetcher:
    """
    YouTube + yt-dlp + faster-whisper 統合フェッチャー。
    字幕優先、なし時のみ Whisper を呼び出す（VRAM排他制御付き）。
    """

    def __init__(
        self,
        whisper_transcriber: Optional[WhisperTranscriber] = None,
    ) -> None:
        self.whisper = whisper_transcriber or WhisperTranscriber()
        self.subtitle_extractor = SubtitleExtractor()
        self.segmenter = VideoSegmenter()

    async def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> List[Dict]:
        """
        yt-dlp の ytsearch を使って動画候補を検索。

        YouTube Data API に依存せず、学習候補収集の入口として使う。
        """
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)

            entries = data.get("entries", []) if isinstance(data, dict) else []
            results = []
            for entry in entries[:max_results]:
                if not entry:
                    continue
                video_id = entry.get("id")
                url = entry.get("url")
                if video_id and not url:
                    url = f"https://www.youtube.com/watch?v={video_id}"
                results.append(
                    {
                        "url": url or "",
                        "title": entry.get("title", ""),
                        "description": entry.get("description", ""),
                        "duration": entry.get("duration"),
                    }
                )
            return results
        except Exception as e:
            raise VideoError(f"Video search failed: {str(e)}")

    async def fetch_video_content(
        self,
        video_url: str,
        use_whisper_if_no_subtitles: bool = True,
    ) -> VideoContent:
        """
        動画をフェッチ・処理。

        字幕がある場合はそれを使用。
        ない場合かつ use_whisper_if_no_subtitles=True なら
        Whisper で音声→テキスト変換（VRAM排他制御内）。

        Args:
            video_url: YouTube URL
            use_whisper_if_no_subtitles: Whisper 使用フラグ

        Returns:
            VideoContent
        """
        try:
            # メタデータ取得
            info = await self.subtitle_extractor.get_video_info(video_url)
            title = info["title"]
            duration = info["duration"]

            # 字幕取得を試みる
            lang, subtitle_segments = await self.subtitle_extractor.extract_subtitles(
                video_url
            )

            if subtitle_segments:
                # 字幕がある場合
                video_segments = self.segmenter.segment_by_time(subtitle_segments)
                full_text = "\n".join([seg.text for seg in video_segments])

                return VideoContent(
                    url=video_url,
                    title=title,
                    duration_seconds=duration,
                    segments=video_segments,
                    full_transcript=full_text,
                    source="subtitle",
                )

            elif use_whisper_if_no_subtitles:
                # 字幕なし、Whisper で音声→テキスト
                # ※実装注：動画ダウンロードと Whisper 処理は別ステップ
                # ここではダウンロード・Whisper 呼び出しのスタブ
                return await self._transcribe_with_whisper(video_url, title, duration)

            else:
                # 字幕なし、Whisper 不使用
                return VideoContent(
                    url=video_url,
                    title=title,
                    duration_seconds=duration,
                    segments=[],
                    source="none",
                )

        except Exception as e:
            return VideoContent(
                url=video_url,
                error=str(e),
            )

    async def _transcribe_with_whisper(
        self,
        video_url: str,
        title: str,
        duration: float,
    ) -> VideoContent:
        """
        Whisper で音声認識（ダウンロード→認識→セグメント化）。

        Args:
            video_url: YouTube URL
            title: 動画タイトル
            duration: 動画長（秒）

        Returns:
            VideoContent
        """
        try:
            audio_path = await self._download_audio(video_url)
            if not audio_path:
                return VideoContent(
                    url=video_url,
                    title=title,
                    error="Audio download failed",
                )

            result = await self.whisper.transcribe(audio_path)
            try:
                Path(audio_path).unlink(missing_ok=True)
            except Exception:
                pass

            if result.error:
                return VideoContent(
                    url=video_url,
                    title=title,
                    error=result.error,
                )

            if result.segments:
                segments = [
                    VideoSegment(
                        start_time=float(segment.get("start", 0.0)),
                        end_time=float(segment.get("end", 0.0)),
                        text=segment.get("text", "").strip(),
                    )
                    for segment in result.segments
                    if segment.get("text")
                ]
            else:
                segments = [
                    VideoSegment(
                        start_time=0.0,
                        end_time=duration,
                        text=result.text.strip(),
                    )
                ]

            return VideoContent(
                url=video_url,
                title=title,
                duration_seconds=duration,
                segments=segments,
                full_transcript=result.text,
                source="transcription",
            )

        except Exception as e:
            return VideoContent(
                url=video_url,
                title=title,
                error=f"Whisper transcription failed: {str(e)}",
            )

    async def _download_audio(self, video_url: str) -> Optional[str]:
        """yt-dlp で音声のみを一時ダウンロードしてパスを返す。"""
        temp_dir = Path(tempfile.mkdtemp(prefix="sba_video_"))
        output_template = str(temp_dir / "audio.%(ext)s")

        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "bestaudio/best",
                "outtmpl": output_template,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                requested = info.get("requested_downloads") or []
                if requested:
                    filepath = requested[0].get("filepath")
                    if filepath and Path(filepath).exists():
                        return filepath

            for path in temp_dir.iterdir():
                if path.is_file():
                    return str(path)
        except Exception:
            return None

        return None

    async def fetch_batch(
        self,
        video_urls: List[str],
    ) -> List[VideoContent]:
        """
        複数動画をバッチ処理。

        Args:
            video_urls: YouTube URL のリスト

        Returns:
            VideoContent のリスト
        """
        tasks = [self.fetch_video_content(url) for url in video_urls]
        return await asyncio.gather(*tasks)
