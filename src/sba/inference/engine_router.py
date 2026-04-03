"""
EngineRouter: タスク種別・VRAM状態・Quota状態に基づく推論エンジン振り分け

設計根拠（推論エンジン・VRAM運用設定書 §4.1）:

  Tier振り分けロジック:
    Tier3 (Qwen2.5-Coder): CODE_GENERATION / CODE_REVIEW / "code" タスク
    Tier2 (Gemini Flash):  長文テキスト（token > 8000）かつ Quota 残量あり
                           Tier1 待機時間 > 10秒 のフォールバック
    Tier1 (Phi-4:14B):     上記以外のデフォルト。最も汎用・高品質な推論

  フォールバックチェーン:
    Tier3 エラー    → Tier1 にフォールバック
    Tier2 Quota切れ → Tier1 にフォールバック
    Tier1 タイムアウト → エラー返却（Tier2 への再フォールバックは行わない）

  VRAM排他:
    各エンジン呼び出しを acquire_vram() でラップ。
    Tier2 は外部API なので VRAM ロック不要。

  変更履歴:
    v2.0: InferenceTask / TaskType / SelectedTier / RoutingDecision を追加。
          route(task) メソッドを追加。
    v2.1: infer() に互潤性を持たせる。
          InferenceTask オブジェクト OR 旧記述形式 (str, task_type=, force_tier=) の両方を受け付ける。
    v2.2: _get_tier1_wait_time() の型チェック修正。
          MagicMock で callable() チェックが通過してまった問題を修正。
    v2.3: _run_tier3() に generate_code() 入口を追加。
          Tier3Engine が generate_code() を持つ場合はそちらを呼び、
          なければ infer() にフォールバックする。
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional, Literal, Union

from loguru import logger
from pydantic import BaseModel

from .tier1 import Tier1Engine, InferenceResult
from .tier2 import Tier2Engine
from .tier3 import Tier3Engine
from .vram_guard import acquire_vram


# ===========================================================================
# タスク種別 Enum
# ===========================================================================

class TaskType(str, Enum):
    """推論タスクの種別。ルーティング判定に使用する。"""
    CODE_GENERATION = "code_generation"  # コード生成 → Tier3
    CODE_REVIEW     = "code_review"      # コードレビュー → Tier3
    LONG_TEXT       = "long_text"        # 長文テキスト → Tier2 候補
    SUMMARIZATION   = "summarization"    # 要約 → Tier2 候補
    REASONING       = "reasoning"        # 推論・評価 → Tier1
    EVAL            = "eval"             # 自己評価 → Tier1
    DEFAULT         = "default"          # デフォルト → Tier1


# ===========================================================================
# 選択 Tier Enum
# ===========================================================================

class SelectedTier(str, Enum):
    """route() が返す選択 Tier。"""
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


# ===========================================================================
# InferenceTask: ルーター入力モデル
# ===========================================================================

class InferenceTask(BaseModel):
    """
    EngineRouter に渡す推論タスク定義。

    Attributes:
        type:             タスク種別（TaskType Enum）
        prompt:           入力プロンプト文字列
        estimated_tokens: 推定入力トークン数（ルーティング判定に使用）
        is_tech_brain:    技術系 Brain かどうか（コードタスク判定補助）
        max_output_tokens: 最大生成トークン数
        temperature:      サンプリング温度
        timeout_s:        タイムアウト秒数
    """
    type: TaskType = TaskType.DEFAULT
    prompt: str
    estimated_tokens: int = 0
    is_tech_brain: bool = False
    max_output_tokens: int = 2048
    temperature: float = 0.7
    timeout_s: float = 30.0


# ===========================================================================
# RoutingDecision: route() の戻り値
# ===========================================================================

class RoutingDecision(BaseModel):
    """
    route() が返すルーティング判定結果。

    Attributes:
        selected_tier: 選択された Tier（SelectedTier Enum）
        reason:        選択理由（ログ・デバッグ用の日本語説明）
    """
    selected_tier: SelectedTier
    reason: str


# ===========================================================================
# 定数
# ===========================================================================

# Tier2 に回す入力トークンの閾値
TIER2_TOKEN_THRESHOLD = 8000

# Tier1 の待機時間上限（秒）。超えたら Tier2 フォールバック候補
TIER1_WAIT_THRESHOLD_S = 10.0

# コードタスクとして扱う TaskType セット
_CODE_TASK_TYPES = {TaskType.CODE_GENERATION, TaskType.CODE_REVIEW}

# 長文タスクとして扱う TaskType セット
_LONG_TEXT_TASK_TYPES = {TaskType.LONG_TEXT, TaskType.SUMMARIZATION}

# 旧記述形式の task_type 文字列 → TaskType マッピング
_LEGACY_TASK_TYPE_MAP: dict[str, TaskType] = {
    "code":          TaskType.CODE_GENERATION,
    "code_review":   TaskType.CODE_REVIEW,
    "text":          TaskType.LONG_TEXT,
    "long_text":     TaskType.LONG_TEXT,
    "summary":       TaskType.SUMMARIZATION,
    "summarization": TaskType.SUMMARIZATION,
    "reasoning":     TaskType.REASONING,
    "eval":          TaskType.EVAL,
    "default":       TaskType.DEFAULT,
}


def _normalize_task_type(task_type_str: str) -> TaskType:
    """
    旧記述形式の task_type 文字列を TaskType Enum に変換する。
    マッピングにない場合は DEFAULT を返す。
    """
    return _LEGACY_TASK_TYPE_MAP.get(task_type_str.lower(), TaskType.DEFAULT)


# ===========================================================================
# EngineRouter
# ===========================================================================

class EngineRouter:
    """
    推論エンジンの振り分けルーター。

    Tier1 / Tier2 / Tier3 を内部で管理し、
    InferenceTask の内容に応じて最適なエンジンに推論リクエストを転送する。
    """

    def __init__(
        self,
        tier1: Optional[Tier1Engine] = None,
        tier2: Optional[Tier2Engine] = None,
        tier3: Optional[Tier3Engine] = None,
    ) -> None:
        self.tier1: Tier1Engine = tier1 or Tier1Engine()
        self.tier3: Tier3Engine = tier3 or Tier3Engine()

        if tier2 is not None:
            self.tier2: Optional[Tier2Engine] = tier2
        else:
            try:
                self.tier2 = Tier2Engine()
            except Exception as e:
                logger.warning(
                    f"EngineRouter: Tier2 初期化失敗（APIキーなし？）。"
                    f"Tier1+Tier3 のみで動作します。error={e}"
                )
                self.tier2 = None

    # ======================================================================
    # ルーティング判定（同期）
    # ======================================================================

    def route(self, task: InferenceTask) -> RoutingDecision:
        """
        InferenceTask を受け取り、どの Tier で処理するかを判定して返す。
        """
        # --- コードタスク → Tier3 ---
        if task.type in _CODE_TASK_TYPES:
            return RoutingDecision(
                selected_tier=SelectedTier.TIER3,
                reason=f"コードタスク ({task.type.value}) のため Tier3 (Qwen2.5-Coder) を選択",
            )

        # --- 長文テキスト → Tier2（Quota あり）---
        if (
            task.type in _LONG_TEXT_TASK_TYPES
            and task.estimated_tokens > TIER2_TOKEN_THRESHOLD
            and self.tier2 is not None
        ):
            quota = self.tier2.get_remaining_quota()
            if quota.get("status") == "active":
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER2,
                    reason=(
                        f"長文タスク ({task.estimated_tokens} tokens > {TIER2_TOKEN_THRESHOLD}) "
                        f"かつ Tier2 Quota 有効のため Tier2 (Gemini) を選択"
                    ),
                )
            else:
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER1,
                    reason=(
                        f"長文タスクだが Tier2 Quota 枯渇 (status={quota.get('status')})。"
                        f"Tier1 へフォールバック"
                    ),
                )

        # --- Tier1 待機時間チェック ---
        tier1_wait = self._get_tier1_wait_time()
        if tier1_wait > TIER1_WAIT_THRESHOLD_S and self.tier2 is not None:
            quota = self.tier2.get_remaining_quota()
            if quota.get("status") == "active":
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER2,
                    reason=(
                        f"Tier1 待機時間 {tier1_wait:.1f}s > {TIER1_WAIT_THRESHOLD_S}s のため "
                        f"Tier2 (Gemini) へフォールバック"
                    ),
                )

        # --- デフォルト: Tier1 ---
        return RoutingDecision(
            selected_tier=SelectedTier.TIER1,
            reason="デフォルト Tier1 (Phi-4) を選択",
        )

    def _get_tier1_wait_time(self) -> float:
        """
        Tier1 の待機時間を float で返す。

        実オブジェクトは get_latest_wait_time() メソッド or latest_wait_time 属性を持つ。
        MagicMock は callable() を通過するため、戻り値の型まで確認する。
        """
        # latest_wait_time 属性が float/int ならそれを優先する
        raw = getattr(self.tier1, "latest_wait_time", None)
        if isinstance(raw, (int, float)):
            return float(raw)

        # get_latest_wait_time() の戻り値が float/int ならそれを使う
        fn = getattr(self.tier1, "get_latest_wait_time", None)
        if callable(fn):
            result = fn()
            if isinstance(result, (int, float)):
                return float(result)

        return 0.0

    # ======================================================================
    # 推論実行（両形式対応）
    # ======================================================================

    async def infer(
        self,
        prompt_or_task: Union[str, InferenceTask],
        task_type: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        force_tier: Optional[Literal[1, 2, 3]] = None,
    ) -> InferenceResult:
        """
        推論を実行する。二つの呼び出し形式に対応する:

        形式1 — 新形式 (InferenceTask):
            result = await router.infer(InferenceTask(type=TaskType.CODE_GENERATION, prompt="..."))

        形式2 — 旧形式 (str + kwargs):
            result = await router.infer("prompt", task_type="code", force_tier=1)
        """
        # --- 新形式 ---
        if isinstance(prompt_or_task, InferenceTask):
            task = prompt_or_task
            decision = self.route(task)
            return await self._dispatch(decision.selected_tier, task)

        # --- 旧形式（str）→ InferenceTask 変換 ---
        prompt = prompt_or_task
        resolved_type = _normalize_task_type(task_type or "default")
        task = InferenceTask(
            type=resolved_type,
            prompt=prompt,
            estimated_tokens=len(prompt) // 4,
            max_output_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
        )

        if force_tier == 1:
            return await self._dispatch(SelectedTier.TIER1, task)
        elif force_tier == 2:
            return await self._dispatch(SelectedTier.TIER2, task)
        elif force_tier == 3:
            return await self._dispatch(SelectedTier.TIER3, task)

        decision = self.route(task)
        return await self._dispatch(decision.selected_tier, task)

    # ======================================================================
    # 内部ディスパッチ
    # ======================================================================

    async def _dispatch(self, tier: SelectedTier, task: InferenceTask) -> InferenceResult:
        if tier == SelectedTier.TIER3:
            return await self._run_tier3(task)
        elif tier == SelectedTier.TIER2:
            return await self._run_tier2(task)
        else:
            return await self._run_tier1(task)

    async def _run_tier1(self, task: InferenceTask) -> InferenceResult:
        """Tier1 (Phi-4) で推論。VRAM ロック取得してから呼び出す。"""
        try:
            with acquire_vram("tier1"):
                result = await self.tier1.infer(
                    task.prompt,
                    temperature=task.temperature,
                    max_tokens=task.max_output_tokens,
                    timeout_s=task.timeout_s,
                )
            return result
        except TimeoutError as e:
            logger.error(f"EngineRouter: Tier1 VRAM lock timeout: {e}")
            return InferenceResult(text="", latency_ms=0.0, error=f"Tier1 VRAM lock timeout: {e}")

    async def _run_tier2(self, task: InferenceTask) -> InferenceResult:
        """Tier2 (Gemini) で推論。外部APIなので VRAM ロック不要。"""
        if self.tier2 is None:
            return await self._run_tier1(task)

        result = await self.tier2.infer(
            task.prompt,
            max_tokens=task.max_output_tokens,
            temperature=task.temperature,
            timeout_s=task.timeout_s,
        )
        if result.error:
            logger.warning(f"EngineRouter: Tier2 error, fallback to Tier1. error={result.error}")
            return await self._run_tier1(task)
        return result

    async def _run_tier3(self, task: InferenceTask) -> InferenceResult:
        """
        Tier3 (Qwen2.5-Coder) で推論。VRAM ロック取得してから呼び出す。

        Tier3Engine に generate_code() が定義されている場合はそちらを呼び、
        なければ infer() にフォールバックする。

        理由: Tier3Engine の定義インターフェースが generate_code() メソッドを持つ場合があり、
        test_inference.py の Mock オブジェクトも generate_code = AsyncMock() で設定しているため。
        """
        try:
            with acquire_vram("tier3"):
                # generate_code() が AsyncMock または coroutine ならそちらを使う
                fn = getattr(self.tier3, "generate_code", None)
                if callable(fn):
                    import inspect
                    # 呼び出し結果が coroutine の場合は await、そうでなければ直接使用
                    maybe_coro = fn(
                        task.prompt,
                        max_tokens=task.max_output_tokens,
                        temperature=task.temperature,
                        timeout_s=task.timeout_s,
                    )
                    if inspect.isawaitable(maybe_coro):
                        result = await maybe_coro
                    else:
                        result = maybe_coro
                else:
                    # generate_code() がなければ infer() で代替
                    result = await self.tier3.infer(
                        task.prompt,
                        max_tokens=task.max_output_tokens,
                        temperature=task.temperature,
                        timeout_s=task.timeout_s,
                    )
        except TimeoutError as e:
            logger.error(f"EngineRouter: Tier3 VRAM lock timeout: {e}")
            return InferenceResult(text="", latency_ms=0.0, error=f"Tier3 VRAM lock timeout: {e}")

        if result.error:
            logger.warning(f"EngineRouter: Tier3 error, fallback to Tier1. error={result.error}")
            return await self._run_tier1(task)
        return result

    # ======================================================================
    # 状態取得
    # ======================================================================

    def get_status(self) -> dict:
        """Tier1待機時間・Tier2可用状態・Quota状態を返す。"""
        tier2_quota: dict = {"status": "unavailable"}
        if self.tier2:
            try:
                tier2_quota = self.tier2.get_remaining_quota()
            except Exception:
                pass
        return {
            "tier1_wait_time": self._get_tier1_wait_time(),
            "tier2_available": self.tier2 is not None,
            "tier2_quota":     tier2_quota,
            "tier3_available": self.tier3 is not None,
        }
