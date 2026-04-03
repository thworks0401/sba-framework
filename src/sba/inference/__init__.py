"""
推論エンジンパッケージ

エクスポート:
  - Tier1Engine:    Phi-4:14B @ Ollama（メイン推論）
  - Tier2Engine:    Gemini 2.5 Flash @ Google API（長文・フォールバック）
  - Tier3Engine:    Qwen2.5-Coder:7B @ Ollama（コード特化）
  - EngineRouter:   タスク種別・VRAM・Quota に基づく振り分けルーター
  - VramGuard:      VRAM 排他制御ユーティリティ
  - InferenceResult: 推論結果データクラス
"""

from .tier1 import Tier1Engine, InferenceResult
from .tier2 import Tier2Engine
from .tier3 import Tier3Engine
from .engine_router import EngineRouter, TaskType
from .vram_guard import (
    acquire_vram,
    unload_ollama_model,
    get_vram_status,
    force_release,
    VRAM_USAGE,
)

__all__ = [
    "Tier1Engine",
    "Tier2Engine",
    "Tier3Engine",
    "EngineRouter",
    "TaskType",
    "InferenceResult",
    "acquire_vram",
    "unload_ollama_model",
    "get_vram_status",
    "force_release",
    "VRAM_USAGE",
]
