"""
faster-whisper 音声認識ラッパー

設計根拠（推論エンジン・VRAM運用設定書 §4-6）:
  - モデル: faster-whisper medium
  - VRAM使用量: Ollama と共有（排他制御必須）
  - 実行フロー: VRAMロック取得 → Ollama unload → whisper実行 → Ollama reload → unlock
  - バッチ処理: 複数ファイルを順序実行
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum

from faster_whisper import WhisperModel

from ..utils.vram_guard import get_global_vram_guard, ModelType, VRAMGuardError


class WhisperError(Exception):
    """Whisper音声認識エラー"""


class AudioFormat(Enum):
    """オーディオフォーマット"""
    MP3 = "mp3"
    WAV = "wav"
    M4A = "m4a"
    OGG = "ogg"
    FLAC = "flac"
    OPUS = "opus"


@dataclass
class TranscriptionResult:
    """音声認識結果"""
    text: str
    language: str = "ja"
    latency_ms: float = 0.0
    segments: Optional[List[dict]] = None  # 詳細セグメント情報
    error: Optional[str] = None


class WhisperTranscriber:
    """
    faster-whisper mediumモデルによる音声認識。

    VRAM排他制御により、Ollama（Tier1/Tier3）との同時実行を防止。
    バッチ処理対応で、複数ファイルを順序実行。
    """

    MODEL_SIZE = "medium"  # faster-whisper model size
    DEFAULT_LANGUAGE = "ja"
    DEFAULT_DEVICE = "cuda"  # GPU推奨、フォールバック: cpu

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        """
        Initialize WhisperTranscriber.

        Args:
            language: 認識言語デフォルト（ja / en / など）
        """
        self.language = language
        self.vram_guard = get_global_vram_guard()
        self._model: Optional[WhisperModel] = None
        self._device = self._detect_device()
        self._latest_latency = 0.0

    def _detect_device(self) -> str:
        """
        推奨デバイス検出（GPU優先）。

        Returns:
            "cuda" or "cpu"
        """
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def _load_model(self) -> WhisperModel:
        """
        faster-whisper mediumモデル読み込み。

        Returns:
            WhisperModel instance
        """
        if self._model is not None:
            return self._model

        try:
            self._model = WhisperModel(
                self.MODEL_SIZE,
                device=self._device,
                compute_type="float16" if self._device == "cuda" else "int8",
            )
            return self._model
        except Exception as e:
            raise WhisperError(f"Failed to load Whisper model: {str(e)}")

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> TranscriptionResult:
        """
        音声ファイルを文字認識。

        実行前にVRAMロック取得・Ollama unload、
        完了後にOllama reload・ロック解放。

        Args:
            audio_path: 音声ファイルパス（mp3/wav/m4a/etc）
            language: 言語コード（None=自動検出）
            timeout_s: タイムアウト秒数

        Returns:
            TranscriptionResult: 認識結果
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            return TranscriptionResult(
                text="",
                latency_ms=0.0,
                error=f"Audio file not found: {audio_path}",
            )

        import time

        start_time = time.time()

        try:
            # VRAM排他ロック取得
            if not self.vram_guard.acquire_lock(ModelType.WHISPER):
                return TranscriptionResult(
                    text="",
                    latency_ms=0.0,
                    error=f"Failed to acquire VRAM lock within {timeout_s}s",
                )

            try:
                # モデル読み込み（ロック内で実行）
                model = self._load_model()

                # 音声認識実行（タイムアウト付き）
                try:
                    segments, info = await asyncio.wait_for(
                        asyncio.to_thread(
                            model.transcribe,
                            str(audio_path),
                            language=language or self.language,
                            beam_size=5,
                            best_of=5,
                            patience=1.0,
                        ),
                        timeout=timeout_s,
                    )

                    # テキスト集約
                    full_text = "".join(segment.text for segment in segments).strip()

                    # セグメント情報抽出
                    segment_list = [
                        {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text,
                        }
                        for seg in segments
                    ]

                    latency = time.time() - start_time
                    self._latest_latency = latency

                    return TranscriptionResult(
                        text=full_text,
                        language=info.language or self.language,
                        latency_ms=latency * 1000,
                        segments=segment_list,
                    )

                except asyncio.TimeoutError:
                    return TranscriptionResult(
                        text="",
                        latency_ms=0.0,
                        error=f"Transcription timeout after {timeout_s}s",
                    )

            finally:
                # VRAM排他ロック解放
                try:
                    self.vram_guard.release_lock(ModelType.WHISPER)
                except VRAMGuardError as e:
                    # ロック解放失敗は警告レベル（既に認識は完了しているため）
                    pass

            return TranscriptionResult(
                text="",
                latency_ms=0.0,
                error=f"Transcription error: unknown error occurred",
            )

        except Exception as e:
            return TranscriptionResult(
                text="",
                latency_ms=0.0,
                error=f"Transcription error: {str(e)}",
            )

    async def transcribe_batch(
        self,
        audio_paths: List[str],
        language: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> List[TranscriptionResult]:
        """
        複数音声ファイルをバッチ処理。

        順序実行（各ファイルごとにVRAMロック / アンロック）。

        Args:
            audio_paths: 音声ファイルパスリスト
            language: 言語コード
            timeout_s: 各ファイルのタイムアウト秒数

        Returns:
            TranscriptionResult のリスト
        """
        results = []
        for audio_path in audio_paths:
            result = await self.transcribe(audio_path, language, timeout_s)
            results.append(result)
        return results

    def get_latest_latency(self) -> float:
        """最新認識レイテンシを取得（ミリ秒）"""
        return self._latest_latency

    async def is_alive(self) -> bool:
        """
        Whisper稼働確認。

        Returns:
            True if Whisper model is available
        """
        try:
            # ダミー音声で確認
            # 実際には generate_dummy_audio で 1秒無音を作成
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                # 無音WAV作成（numpy/scipy不要版）
                import struct
                import wave

                with wave.open(tmp.name, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    # 1秒間の無音（0x0000）
                    wav.writeframes(b"\x00\x00" * 16000)

                try:
                    result = await self.transcribe(tmp.name, timeout_s=5.0)
                    return result.error is None
                finally:
                    Path(tmp.name).unlink()

        except Exception:
            return False

    def unload_model(self) -> None:
        """
        モデルをメモリから解放。

        メモリ節約または強制リロード時に使用。
        """
        self._model = None

    async def batch_transcribe_with_progress(
        self,
        audio_paths: List[str],
        language: Optional[str] = None,
        timeout_s: float = 60.0,
        on_progress: Optional[callable] = None,
    ) -> List[TranscriptionResult]:
        """
        バッチ処理（進捗コールバック付き）。

        Args:
            audio_paths: 音声ファイルパスリスト
            language: 言語コード
            timeout_s: 各ファイルのタイムアウト秒数
            on_progress: (current, total) → None コールバック

        Returns:
            TranscriptionResult のリスト
        """
        results = []
        total = len(audio_paths)

        for idx, audio_path in enumerate(audio_paths):
            result = await self.transcribe(audio_path, language, timeout_s)
            results.append(result)

            if on_progress:
                on_progress(idx + 1, total)

        return results
