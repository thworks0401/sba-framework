"""
Tier1 推論エンジン（Phi-4:14B @ Ollama）

設計根拠（推論エンジン・VRAM運用設定書 §2.1）:
  - モデル: Phi-4:14B（Ollama ローカル動作、完全無料）
  - VRAM使用量: 約8.0GB（RTX3060Tiフル占有）
  - 待機時間計測: 10秒超過でTier2フォールバック判定
  - タスクキュー: asyncio.Queue で直列化
"""

from __future__ import annotations

import asyncio
import json
import time
import re
from typing import Optional
from dataclasses import dataclass

import ollama


class Tier1Error(Exception):
    """Tier1推論エラー"""


@dataclass
class InferenceResult:
    """推論結果"""
    text: str
    latency_ms: float
    tokens_used: Optional[int] = None
    error: Optional[str] = None


class Tier1Engine:
    """
    Phi-4:14B @ Ollama による推論エンジン。

    非同期キュー処理で複数リクエストを直列化。
    待機時間を計測し、Tier2フォールバックの判定に使用。
    """

    MODEL_NAME = "phi4"
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 2048

    def __init__(self, queue_maxsize: int = 100) -> None:
        """
        Initialize Tier1 Engine.

        Args:
            queue_maxsize: asyncio.Queue の最大サイズ
        """
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self.latest_wait_time = 0.0  # 最新の待機時間（秒）
        self._current_latency = 0.0  # 現在の推論遅延

    async def infer(
        self,
        prompt: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        テキストベースの推論リクエストを実行。

        Args:
            prompt: 入力プロンプト
            temperature: サンプリング温度（0-2, 低いほど確定的）
            max_tokens: 最大生成トークン数
            timeout_s: タイムアウト秒数

        Returns:
            InferenceResult: 推論結果（テキスト、レイテンシ等）
        """
        start_time = time.time()

        try:
            # キューに入れて実行を待つ
            await asyncio.wait_for(
                self.queue.put(("infer", prompt, temperature, max_tokens)),
                timeout=timeout_s
            )

            # 待機時間を計測
            queue_wait_time = time.time() - start_time
            self.latest_wait_time = queue_wait_time

            # Ollama呼び出し
            infer_start = time.time()
            response = ollama.generate(
                model=self.MODEL_NAME,
                prompt=prompt,
                temperature=temperature,
                num_predict=max_tokens,
                stream=False,  # 非ストリーミング
            )

            infer_time = time.time() - infer_start
            self._current_latency = infer_time

            # レスポンス解析
            text = response.get("response", "").strip()
            tokens_used = response.get("eval_count", None)

            return InferenceResult(
                text=text,
                latency_ms=infer_time * 1000,
                tokens_used=tokens_used,
            )

        except asyncio.TimeoutError:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 timeout after {timeout_s}s",
            )
        except Exception as e:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 inference error: {str(e)}",
            )
        finally:
            # キューから取り出す
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

    async def chat(
        self,
        messages: list[dict],
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        チャット形式（会話履歴付き）の推論リクエスト。

        Args:
            messages: [{"role": "user"/"assistant", "content": "..."}, ...]
            temperature: サンプリング温度
            max_tokens: 最大生成トークン数
            timeout_s: タイムアウト秒数

        Returns:
            InferenceResult: 推論結果
        """
        start_time = time.time()

        try:
            # キューに入れて実行を待つ
            await asyncio.wait_for(
                self.queue.put(("chat", messages, temperature, max_tokens)),
                timeout=timeout_s
            )

            # 待機時間を計測
            queue_wait_time = time.time() - start_time
            self.latest_wait_time = queue_wait_time

            # Ollama呼び出し（chat）
            infer_start = time.time()
            response = ollama.chat(
                model=self.MODEL_NAME,
                messages=messages,
                temperature=temperature,
                num_predict=max_tokens,
                stream=False,
            )

            infer_time = time.time() - infer_start
            self._current_latency = infer_time

            # レスポンス解析
            text = response.get("message", {}).get("content", "").strip()
            tokens_used = response.get("eval_count", None)

            return InferenceResult(
                text=text,
                latency_ms=infer_time * 1000,
                tokens_used=tokens_used,
            )

        except asyncio.TimeoutError:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 chat timeout after {timeout_s}s",
            )
        except Exception as e:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 chat error: {str(e)}",
            )
        finally:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

    def extract_json(self, text: str) -> Optional[dict]:
        """
        推論結果テキストからJSON部分を抽出。

        マークダウンコードブロック ```json ... ``` または
        生JSON を検出。

        Args:
            text: 推論結果テキスト

        Returns:
            パースされたdict、またはNone（JSON不在時）
        """
        # ```json ... ``` パターン
        code_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
        if code_match:
            json_str = code_match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # 生JSON パターン（{...} を検出）
        json_match = re.search(r'(\{.+?\})', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        return None

    def get_latest_wait_time(self) -> float:
        """
        最新の待機時間を取得（Tier2フォールバック判定用）。

        Returns:
            秒単位の待機時間
        """
        return self.latest_wait_time

    def get_current_latency(self) -> float:
        """
        最新の推論レイテンシを取得。

        Returns:
            秒単位のレイテンシ
        """
        return self._current_latency

    async def is_alive(self) -> bool:
        """
        Ollama接続確認。

        Returns:
            True if Ollama is responsive, False otherwise
        """
        try:
            # 軽量なリクエストで疎通確認
            result = await self.infer("ping", max_tokens=10, timeout_s=5.0)
            return result.error is None
        except Exception:
            return False
