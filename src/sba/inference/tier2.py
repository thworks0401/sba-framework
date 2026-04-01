"""
Tier2 推論エンジン（Gemini 2.5 Flash @ Google API）

設計根拠（推論エンジン・VRAM運用設定書 §2.2）:
  - モデル: Gemini 2.5 Flash（Google API 無料枠）
  - VRAM使用量: 0GB（外部API、ローカルVRAM消費なし）
  - 無料枠: 15 req/min・1,500 req/day・1M tokens/min
  - 用途: 大量テキスト処理・長文要約・Tier1フォールバック
  - 残枠チェック: api_usage.db から残량確認、100以下で呼び出し拒否
"""

from __future__ import annotations

import os
import json
import re
import time
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

import google.generativeai as genai

from ..storage.api_usage_db import APIUsageRepository


class Tier2Error(Exception):
    """Tier2推論エラー"""


@dataclass
class InferenceResult:
    """推論結果"""
    text: str
    latency_ms: float
    tokens_used: Optional[int] = None
    error: Optional[str] = None


class Tier2Engine:
    """
    Gemini 2.5 Flash による補完推論エンジン。

    大量テキスト処理・要約・Tier1待機時間超過時のフォールバック用。
    APIレート管理はapi_usage.db と連携。
    """

    MODEL_NAME = "gemini-2.5-flash"
    API_NAME = "gemini"
    MIN_TOKENS_THRESHOLD = 100  # 残トークン100以下でTier1フォールバック

    def __init__(self, api_key: Optional[str] = None, api_usage_db_path: Optional[str] = None) -> None:
        """
        Initialize Tier2 Engine.

        Args:
            api_key: Google API キー（env: GEMINI_API_KEY）
            api_usage_db_path: APIUsageRepository DB パス（デフォルト: C:/SBA/data/api_usage.db）
        """
        # APIキー取得
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise Tier2Error("GEMINI_API_KEY not set in environment")

        genai.configure(api_key=self.api_key)
        self.client = genai.GenerativeModel(self.MODEL_NAME)

        # APIレート管理
        if api_usage_db_path is None:
            api_usage_db_path = "C:/SBA/data/api_usage.db"
        self.api_repo = APIUsageRepository(str(api_usage_db_path))
        self._latest_latency = 0.0

    async def infer(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        Gemini推論リクエストを実行。

        事前に api_usage.db の残量をチェック。不足時はエラー。

        Args:
            prompt: 入力プロンプト
            max_tokens: 最大生成トークン数
            temperature: サンプリング温度（0-2）
            timeout_s: タイムアウト秒数

        Returns:
            InferenceResult: 推論結果
        """
        # 残量チェック
        remaining = self.api_repo.get_remaining_tokens(self.API_NAME)
        if remaining is not None and remaining < self.MIN_TOKENS_THRESHOLD:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier2 quota exceeded (remaining={remaining}, threshold={self.MIN_TOKENS_THRESHOLD}). "
                      f"Fallback to Tier1.",
            )

        try:
            start_time = time.time()

            # Gemini呼び出し
            response = self.client.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
                request_options={"timeout": timeout_s},
            )

            latency = time.time() - start_time
            self._latest_latency = latency

            text = response.text.strip() if response.text else ""

            # トークン計測（Gemini APIから取得可能な場合）
            tokens_used = None
            if hasattr(response, "usage_metadata"):
                tokens_used = response.usage_metadata.candidates_token_count

            # APIレート記録
            estimated_input_tokens = len(prompt.split()) + len(prompt) // 4
            estimated_output_tokens = tokens_used or (len(text.split()) + len(text) // 4)
            self.api_repo.increment_usage(
                self.API_NAME,
                req_count=1,
                token_count=estimated_input_tokens + estimated_output_tokens,
            )

            return InferenceResult(
                text=text,
                latency_ms=latency * 1000,
                tokens_used=tokens_used,
            )

        except TimeoutError:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier2 timeout after {timeout_s}s",
            )
        except Exception as e:
            return InferenceResult(
                text="",
                latency_ms=0.0,
                error=f"Tier2 inference error: {str(e)}",
            )

    async def summarize(
        self,
        text: str,
        max_length: int = 500,
        temperature: float = 0.3,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        大量テキスト要約（Tier2特化タスク）。

        Args:
            text: 要約対象のテキスト
            max_length: 要約の最大文字数
            temperature: 低めの温度で確定的に
            timeout_s: タイムアウト秒数

        Returns:
            InferenceResult: 要約結果
        """
        prompt = f"""以下のテキストを日本語で {max_length} 文字以下で要約してください。

【テキスト】
{text}

【要約】"""

        return await self.infer(
            prompt,
            max_tokens=int(max_length / 4),  # 概算: 4文字 ≈ 1トークン
            temperature=temperature,
            timeout_s=timeout_s,
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
        """最新レイテンシを取得"""
        return self._latest_latency

    async def is_alive(self) -> bool:
        """
        Gemini接続確認。

        Returns:
            True if Gemini is responsive
        """
        try:
            result = await self.infer("hello", max_tokens=10, timeout_s=5.0)
            return result.error is None
        except Exception:
            return False

    def get_remaining_quota(self) -> dict:
        """
        現在のQuota状態を取得。

        Returns:
            {"remaining_tokens": int, "daily_used": int, "status": "active"|"throttled"|"stopped"}
        """
        remaining = self.api_repo.get_remaining_tokens(self.API_NAME)
        daily = self.api_repo.get_today_usage(self.API_NAME)

        status = "active"
        if remaining is not None:
            if remaining < self.MIN_TOKENS_THRESHOLD:
                status = "stopped"
            elif remaining < self.MIN_TOKENS_THRESHOLD * 2:
                status = "throttled"

        return {
            "remaining_tokens": remaining,
            "daily_used": daily.get("token_count", 0) if daily else 0,
            "status": status,
        }
