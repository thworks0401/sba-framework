"""
Tier1 推論エンジン（Phi-4:14B @ Ollama）

設計根拠（推論エンジン・VRAM運用設定書 §2.1）:
  - モデル: Phi-4:14B（Ollama ローカル動作、完全無料）
  - VRAM使用量: 約8.0GB（RTX3060Tiフル占有）
  - 待機時間計測: 10秒超過でTier2フォールバック判定
  - 直列化: asyncio.Semaphore(1) で同時1リクエストのみ実行

【修正履歴】
  旧実装: asyncio.Queue を使っていたが、Queue への put → 直接 Ollama 呼び出し →
          Queue から get という無意味なループになっており、並列呼び出し時に
          Ollama へ複数リクエストが同時に飛ぶ問題があった。
  新実装: asyncio.Semaphore(1) で確実に1リクエストずつ直列化する。
          待機時間もセマフォ取得までの時間として正確に計測できる。
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

    asyncio.Semaphore(1) で複数リクエストを直列化。
    セマフォ取得待ち時間を計測し、Tier2フォールバック判定に使用。
    """

    MODEL_NAME          = "phi4"
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS  = 2048

    def __init__(self) -> None:
        """Initialize Tier1 Engine."""
        # Semaphore(1): 同時に1コルーチンのみ Ollama を呼び出せる
        # Queue の代わりにセマフォを使うことで、並列呼び出し時の OOM を防ぐ
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)
        self.latest_wait_time: float = 0.0   # 最新のセマフォ待機時間（秒）
        self._current_latency: float = 0.0   # 最新の Ollama 推論時間（秒）

    async def infer(
        self,
        prompt: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        テキストベースの推論リクエストを実行。

        セマフォにより同時1リクエストのみ Ollama へ送信される。
        タイムアウトはセマフォ待機 + 推論時間の合計に適用。

        Args:
            prompt: 入力プロンプト
            temperature: サンプリング温度（0-2, 低いほど確定的）
            max_tokens: 最大生成トークン数
            timeout_s: タイムアウト秒数（待機 + 推論の合計）

        Returns:
            InferenceResult: 推論結果
        """
        overall_start = time.time()

        try:
            # セマフォ取得（タイムアウト付き）
            # 他のリクエストが実行中なら、ここで待機する
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 semaphore wait timeout after {timeout_s}s",
            )

        # セマフォ取得後: 待機時間を記録
        wait_time = time.time() - overall_start
        self.latest_wait_time = wait_time

        try:
            # Ollama 呼び出し（残りタイムアウト = 全体 - 待機時間）
            remaining_timeout = max(1.0, timeout_s - wait_time)
            infer_start = time.time()

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ollama.generate,
                    model=self.MODEL_NAME,
                    prompt=prompt,
                    options={
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                    stream=False,
                ),
                timeout=remaining_timeout,
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
                error=f"Tier1 inference timeout after {timeout_s}s",
            )
        except Exception as e:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 inference error: {str(e)}",
            )
        finally:
            # 必ずセマフォを解放（例外が発生しても）
            self._semaphore.release()

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
        overall_start = time.time()

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier1 chat semaphore wait timeout after {timeout_s}s",
            )

        wait_time = time.time() - overall_start
        self.latest_wait_time = wait_time

        try:
            remaining_timeout = max(1.0, timeout_s - wait_time)
            infer_start = time.time()

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ollama.chat,
                    model=self.MODEL_NAME,
                    messages=messages,
                    options={
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                    stream=False,
                ),
                timeout=remaining_timeout,
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
            self._semaphore.release()

    def extract_json(self, text: str) -> Optional[dict]:
        """
        推論結果テキストからJSON部分を抽出。

        マークダウンコードブロック ```json ... ``` または生JSON を検出。
        """
        # ```json ... ``` パターン
        code_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass

        # 生JSON パターン（{...} を検出）
        json_match = re.search(r'(\{.+?\})', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    def get_latest_wait_time(self) -> float:
        """
        最新のセマフォ待機時間を取得（Tier2フォールバック判定用）。

        Returns:
            秒単位の待機時間
        """
        return self.latest_wait_time

    def get_current_latency(self) -> float:
        """
        最新の Ollama 推論レイテンシを取得。

        Returns:
            秒単位のレイテンシ
        """
        return self._current_latency

    async def is_alive(self) -> bool:
        """
        Ollama 接続確認。

        Returns:
            True if Ollama is responsive
        """
        try:
            result = await self.infer("ping", max_tokens=10, timeout_s=5.0)
            return result.error is None
        except Exception:
            return False
