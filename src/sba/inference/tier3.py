"""
Tier3 推論エンジン（Qwen2.5-Coder:7B @ Ollama）

設計根拠（推論エンジン・VRAM運用設定書 §2.3）:
  - モデル: Qwen2.5-Coder:7B（Ollama ローカル動作、完全無料）
  - VRAM使用量: 約5.0GB
  - 用途: Tech系Brain のコード生成・検証・自己実験
  - 注意: Tier1（Phi-4:14B）との同時起動は禁止（VRAM競合）
  - 実行前にVRAMロック取得、Tier1をアンロード
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional
from dataclasses import dataclass

import ollama


class Tier3Error(Exception):
    """Tier3推論エラー"""


@dataclass
class InferenceResult:
    """推論結果"""
    text: str
    latency_ms: float
    tokens_used: Optional[int] = None
    error: Optional[str] = None


class Tier3Engine:
    """
    Qwen2.5-Coder:7B @ Ollama によるコード生成エンジン。

    通常のテキスト推論には使わず、コード生成・検証タスク専用。
    Tier1との同時起動は禁止（VRAM排他制御必須）。
    """

    MODEL_NAME = "qwen2.5-coder:7b"
    DEFAULT_TEMPERATURE = 0.3  # コード生成は低め
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self) -> None:
        """Initialize Tier3 Engine."""
        self._latest_wait_time = 0.0
        self._latest_latency = 0.0

    async def generate_code(
        self,
        prompt: str,
        language: str = "python",
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        コード生成リクエスト。

        Args:
            prompt: コード生成プロンプト
            language: プログラミング言語（python / javascript / java など）
            temperature: サンプリング温度（デフォルト: 0.3 = 低・確定的）
            max_tokens: 最大生成トークン数
            timeout_s: タイムアウト秒数

        Returns:
            InferenceResult: 生成されたコード
        """
        # プロンプト前処理
        formatted_prompt = f"""以下の仕様に基づいて {language} コードを生成してください。

【仕様】
{prompt}

【コード】
```{language}
"""

        start_time = time.time()

        try:
            # Ollama呼び出し
            infer_start = time.time()
            response = ollama.generate(
                model=self.MODEL_NAME,
                prompt=formatted_prompt,
                temperature=temperature,
                num_predict=max_tokens,
                stream=False,
            )

            infer_time = time.time() - infer_start
            self._latest_latency = infer_time
            self._latest_wait_time = time.time() - start_time

            # レスポンス解析
            full_text = response.get("response", "").strip()

            # コードブロック抽出
            code_match = re.search(rf'```{language}\s*\n(.*?)\n```', full_text, re.DOTALL)
            if code_match:
                code = code_match.group(1)
            else:
                # バッククォートなしの場合もある
                code = full_text

            tokens_used = response.get("eval_count", None)

            return InferenceResult(
                text=code,
                latency_ms=infer_time * 1000,
                tokens_used=tokens_used,
            )

        except asyncio.TimeoutError:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier3 timeout after {timeout_s}s",
            )
        except Exception as e:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier3 code generation error: {str(e)}",
            )

    async def review_code(
        self,
        code: str,
        language: str = "python",
        focus: str = "correctness",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        コードレビュー・検証。

        Args:
            code: レビュー対象のコード
            language: 言語（python / javascript など）
            focus: レビュー観点（correctness / performance / security / style など）
            temperature: サンプリング温度
            max_tokens: 最大生成トークン数
            timeout_s: タイムアウト秒数

        Returns:
            InferenceResult: レビュー結果
        """
        prompt = f"""以下の {language} コードを {focus} の観点からレビューしてください。

【コード】
```{language}
{code}
```

【レビュー結果】JSON形式で以下の構造を返してください:
{{
  "issues": [
    {{"severity": "high|medium|low", "description": "...", "suggestion": "..."}}
  ],
  "score": 0-100,
  "summary": "..."
}}
"""

        start_time = time.time()

        try:
            infer_start = time.time()
            response = ollama.generate(
                model=self.MODEL_NAME,
                prompt=prompt,
                temperature=temperature,
                num_predict=max_tokens,
                stream=False,
            )

            infer_time = time.time() - infer_start
            self._latest_latency = infer_time
            self._latest_wait_time = time.time() - start_time

            text = response.get("response", "").strip()
            tokens_used = response.get("eval_count", None)

            return InferenceResult(
                text=text,
                latency_ms=infer_time * 1000,
                tokens_used=tokens_used,
            )

        except Exception as e:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier3 code review error: {str(e)}",
            )

    def extract_json(self, text: str) -> Optional[dict]:
        """
        推論結果からJSON部分を抽出。

        Args:
            text: 推論結果テキスト

        Returns:
            パースされたdict、またはNone
        """
        # ```json ... ``` パターン
        code_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
        if code_match:
            json_str = code_match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # 生JSON パターン
        json_match = re.search(r'(\{.+?\})', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        return None

    def get_latest_latency(self) -> float:
        """最新推論レイテンシを取得"""
        return self._latest_latency

    def get_latest_wait_time(self) -> float:
        """最新待機時間を取得"""
        return self._latest_wait_time

    async def is_alive(self) -> bool:
        """
        Ollama接続確認（Tier3モデル確認）。

        Returns:
            True if Qwen2.5-Coder is available
        """
        try:
            result = await self.generate_code("return 1 + 1", max_tokens=10, timeout_s=5.0)
            return result.error is None
        except Exception:
            return False
