"""
Tier2 推論エンジン（Gemini 2.5 Flash @ Google API）

設計根拠（推論エンジン・VRAM運用設定書 §2.2）:
  - モデル: Gemini 2.5 Flash（Google API 無料枠）
  - VRAM使用量: 0GB（外部API、ローカルVRAM消費なし）
  - 無料枠: 15 req/min・1,500 req/day・1M tokens/min
  - 用途: 大量テキスト処理・長文要約・Tier1フォールバック
  - 残枠チェック: api_usage.db から残量確認、100以下で呼び出し拒否

【SDK 移行】
  旧: google-generativeai (google.generativeai) → EOL 宣言済み
  新: google-genai (google.genai) に完全移行
  参考: https://github.com/google-gemini/deprecated-generative-ai-python/blob/main/README.md

  API の変化点:
    - クライアント: genai.Client(api_key=...) に変更
    - 推論: client.models.generate_content(model=..., contents=..., config=...) に変更
    - GenerationConfig: types.GenerateContentConfig に変更
    - usage_metadata フィールド名は同じ（candidates_token_count）
"""

from __future__ import annotations

import os
import json
import re
import time
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from ..storage.api_usage_db import APIUsageRepository
from ..config import SBAConfig


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
    APIレート管理は api_usage.db（SBAConfig.data 配下）と連携。
    """

    MODEL_NAME            = "gemini-2.5-flash-preview-04-17"
    API_NAME              = "gemini"
    MIN_TOKENS_THRESHOLD  = 100   # 残トークン 100 以下で Tier1 フォールバック

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_usage_db_path: Optional[str] = None,
    ) -> None:
        """
        Initialize Tier2 Engine.

        Args:
            api_key: Google API キー（省略時: 環境変数 GEMINI_API_KEY または sba_config.yaml）
            api_usage_db_path: APIUsageRepository DB パス（省略時: SBAConfig.data から解決）
        """
        # --- APIキー取得 ---
        self.api_key = api_key or self._resolve_api_key()
        if not self.api_key:
            raise Tier2Error(
                "Gemini API キーが設定されていません。"
                "環境変数 GEMINI_API_KEY または sba_config.yaml の api_keys.gemini に設定してください。"
            )

        # google-genai SDK: Client インスタンスを生成
        self.client = genai.Client(api_key=self.api_key)

        # --- API 使用量 DB パス解決 ---
        resolved_db_path = api_usage_db_path or self._resolve_db_path()
        self.api_repo = APIUsageRepository(resolved_db_path)
        self._latest_latency = 0.0

    @staticmethod
    def _resolve_api_key() -> Optional[str]:
        """
        API キーを以下の優先順で解決:
          1. 環境変数 GEMINI_API_KEY
          2. sba_config.yaml の api_keys.gemini
        """
        env_key = os.getenv("GEMINI_API_KEY")
        if env_key:
            return env_key

        try:
            cfg = SBAConfig.load_env()
            if cfg.api_keys.gemini:
                return cfg.api_keys.gemini
        except Exception:
            pass

        return None

    @staticmethod
    def _resolve_db_path() -> str:
        """
        api_usage.db のパスを SBAConfig から解決。
        SBAConfig がロードできない場合はフォールバックパスを返す。
        """
        try:
            cfg = SBAConfig.load_env()
            return str(cfg.data / "api_usage.db")
        except Exception:
            return "C:/TH_Works/SBA/data/api_usage.db"

    # ======================================================================
    # 推論
    # ======================================================================

    async def infer(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> InferenceResult:
        """
        Gemini 推論リクエストを実行。

        事前に api_usage.db の残量をチェック。不足時はエラーを返す。

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
                error=(
                    f"Tier2 quota exceeded "
                    f"(remaining={remaining}, threshold={self.MIN_TOKENS_THRESHOLD}). "
                    f"Fallback to Tier1."
                ),
            )

        try:
            start_time = time.time()

            # google-genai SDK の新しい呼び出し方式
            # client.models.generate_content() を使用
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )

            latency = time.time() - start_time
            self._latest_latency = latency

            # レスポンステキスト取得（新SDKも .text プロパティは同じ）
            text = response.text.strip() if response.text else ""

            # トークン計測（新SDKも usage_metadata は同じ構造）
            tokens_used = None
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens_used = getattr(
                    response.usage_metadata, "candidates_token_count", None
                )

            # APIレート記録
            estimated_input  = len(prompt.split()) + len(prompt) // 4
            estimated_output = tokens_used or (len(text.split()) + len(text) // 4)
            self.api_repo.increment_usage(
                self.API_NAME,
                req_count=1,
                token_count=estimated_input + estimated_output,
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
        """
        prompt = f"""以下のテキストを日本語で {max_length} 文字以下で要約してください。

【テキスト】
{text}

【要約】"""

        return await self.infer(
            prompt,
            max_tokens=int(max_length / 4),
            temperature=temperature,
            timeout_s=timeout_s,
        )

    def extract_json(self, text: str) -> Optional[dict]:
        """推論結果からJSON部分を抽出。"""
        # ```json ... ``` パターン
        code_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass

        # 生JSON パターン
        json_match = re.search(r'(\{.+?\})', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    # ======================================================================
    # 状態取得
    # ======================================================================

    def get_latest_latency(self) -> float:
        """最新レイテンシを取得（秒）"""
        return self._latest_latency

    async def is_alive(self) -> bool:
        """Gemini 接続確認。"""
        try:
            result = await self.infer("hello", max_tokens=10, timeout_s=5.0)
            return result.error is None
        except Exception:
            return False

    def get_remaining_quota(self) -> dict:
        """
        現在の Quota 状態を取得。

        Returns:
            {
                "remaining_tokens": int | None,
                "daily_used": int,
                "status": "active" | "throttled" | "stopped"
            }
        """
        remaining = self.api_repo.get_remaining_tokens(self.API_NAME)
        daily     = self.api_repo.get_today_usage(self.API_NAME)

        status = "active"
        if remaining is not None:
            if remaining < self.MIN_TOKENS_THRESHOLD:
                status = "stopped"
            elif remaining < self.MIN_TOKENS_THRESHOLD * 2:
                status = "throttled"

        return {
            "remaining_tokens": remaining,
            "daily_used":       daily.get("token_count", 0) if daily else 0,
            "status":           status,
        }
