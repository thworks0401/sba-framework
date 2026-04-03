"""
VRAM排他制御ガード

設計根拠（推論エンジン・VRAM運用設定書 §3.1）:
  RTX3060Ti 8GB VRAM の排他制御。

  VRAM消費量:
    Tier1 (Phi-4:14B):     ~8.0GB → 単独でフル占有
    Tier3 (Qwen2.5-Coder): ~5.0GB
    Whisper (faster-whisper): ~2.0GB

  排他ルール:
    - Tier1稼働中: Tier3・Whisper同時起動禁止
    - Tier1/Tier3は排他ロック必須 (threading.Lock)
    - Whisper使用前にOllamaモデルを必ずアンロード

  実装方針:
    - グローバル threading.Lock で VRAM 排他を実現
    - 現在ロードされているモデル名を _current_model で追跡
    - Ollama REST API でモデルをアンロード（DELETE /api/generate）
    - context manager (__enter__/__exit__) でロック取得・解放
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Optional, Generator

import httpx
from loguru import logger


# ======================================================================
# VRAM使用量定義（GB）
# ======================================================================

VRAM_USAGE: dict[str, float] = {
    "tier1":   8.0,   # Phi-4:14B
    "tier3":   5.0,   # Qwen2.5-Coder:7B
    "whisper": 2.0,   # faster-whisper
}

# RTX3060Ti 総VRAM
TOTAL_VRAM_GB = 8.0

# Ollama モデル名マッピング
OLLAMA_MODEL_NAMES: dict[str, str] = {
    "tier1": "phi4",
    "tier3": "qwen2.5-coder:7b",
}

# Ollama API エンドポイント
OLLAMA_BASE_URL = "http://localhost:11434"


# ======================================================================
# グローバル VRAM ロック
# ======================================================================

_VRAM_LOCK = threading.Lock()
_current_model: Optional[str] = None  # 現在 VRAM にロードされているエンジン名


def _get_current_model() -> Optional[str]:
    """現在 VRAM にロードされているエンジン名を返す。"""
    return _current_model


def _set_current_model(name: Optional[str]) -> None:
    """現在 VRAM にロードされているエンジン名を更新する。"""
    global _current_model
    _current_model = name


# ======================================================================
# Ollama アンロード
# ======================================================================

def unload_ollama_model(engine_name: str, timeout_s: float = 10.0) -> bool:
    """
    Ollama から指定エンジンのモデルをアンロードする。

    Ollama REST API: POST /api/generate with keep_alive=0 でモデルをメモリから解放。
    DELETE /api/generate は Ollama バージョンによってサポートされないため
    keep_alive=0 方式を使用する（全バージョン対応）。

    Args:
        engine_name: "tier1" または "tier3"
        timeout_s:   HTTP タイムアウト秒数

    Returns:
        True: アンロード成功 / False: 失敗またはスキップ
    """
    model_name = OLLAMA_MODEL_NAMES.get(engine_name)
    if not model_name:
        logger.warning(f"VramGuard: unknown engine '{engine_name}' for unload")
        return False

    try:
        # keep_alive=0 でモデルをメモリから解放
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model_name, "keep_alive": 0},
            timeout=timeout_s,
        )
        if response.status_code == 200:
            logger.info(f"VramGuard: unloaded {engine_name} ({model_name}) from VRAM")
            return True
        else:
            logger.warning(
                f"VramGuard: unload {engine_name} returned HTTP {response.status_code}"
            )
            return False
    except Exception as e:
        logger.warning(f"VramGuard: unload {engine_name} failed: {e}")
        return False


# ======================================================================
# 互換性チェック
# ======================================================================

def _check_compatibility(requesting: str, current: Optional[str]) -> bool:
    """
    リクエストするエンジンと現在ロード中のエンジンが共存できるか判定。

    排他ルール:
      - tier1 は他の全エンジンと排他（8GB フル占有）
      - tier3 は tier1 と排他（tier3同士は発生しない: 直列化済み）
      - whisper は tier1・tier3 と排他

    Args:
        requesting: これからロードしようとするエンジン名
        current:    現在 VRAM にロードされているエンジン名（None = 空き）

    Returns:
        True: 共存可能 / False: アンロードが必要
    """
    if current is None:
        return True  # VRAM 空き → 何でも OK

    # 同じエンジンを再要求 → 共存可能（再ロード不要）
    if requesting == current:
        return True

    # tier1 は全エンジンと排他
    if requesting == "tier1" or current == "tier1":
        return False

    # tier3 と whisper も排他
    if set([requesting, current]) == {"tier3", "whisper"}:
        return False

    return True


# ======================================================================
# VramGuard コンテキストマネージャ
# ======================================================================

@contextmanager
def acquire_vram(engine_name: str, timeout_s: float = 60.0) -> Generator[None, None, None]:
    """
    VRAM 排他ロックを取得して処理を実行するコンテキストマネージャ。

    使い方:
        with acquire_vram("tier1"):
            result = engine.infer(...)

    処理フロー:
      1. _VRAM_LOCK を取得（排他制御の入口）
      2. 現在ロードされているエンジンと互換性チェック
      3. 非互換の場合: 既存エンジンを Ollama からアンロード
      4. 現在エンジン名を更新
      5. 呼び出し元のコードを実行
      6. ロックを解放（finally で保証）

    Args:
        engine_name: 使用するエンジン名 ("tier1" / "tier3" / "whisper")
        timeout_s:   ロック取得タイムアウト秒数

    Raises:
        TimeoutError: ロック取得タイムアウト
        ValueError:   不明なエンジン名
    """
    if engine_name not in VRAM_USAGE:
        raise ValueError(f"VramGuard: unknown engine '{engine_name}'")

    # タイムアウト付きロック取得
    deadline = time.monotonic() + timeout_s
    acquired = False

    while time.monotonic() < deadline:
        acquired = _VRAM_LOCK.acquire(blocking=False)
        if acquired:
            break
        time.sleep(0.1)

    if not acquired:
        raise TimeoutError(
            f"VramGuard: failed to acquire VRAM lock for '{engine_name}' "
            f"within {timeout_s}s"
        )

    try:
        current = _get_current_model()

        if not _check_compatibility(engine_name, current):
            # 非互換エンジンが VRAM にいる → アンロード
            logger.info(
                f"VramGuard: evicting '{current}' to load '{engine_name}'"
            )
            if current in ("tier1", "tier3"):
                unload_ollama_model(current)
            # Whisper のアンロードは呼び出し元が責任を持つ
            # (faster-whisper はオブジェクト破棄でアンロードされる)

        _set_current_model(engine_name)
        logger.debug(f"VramGuard: acquired for '{engine_name}' (was: '{current}')")

        yield

    finally:
        _VRAM_LOCK.release()
        logger.debug(f"VramGuard: released lock for '{engine_name}'")


def force_release(unload_current: bool = False) -> None:
    """
    緊急用: VRAM ロックを強制解放する。
    テスト・クラッシュリカバリ時のみ使用。

    Args:
        unload_current: True の場合、現在ロードされているモデルもアンロードする
    """
    global _current_model

    if unload_current and _current_model in ("tier1", "tier3"):
        unload_ollama_model(_current_model)

    _current_model = None

    # ロックが取得状態の場合のみ解放
    # release() は取得していない状態で呼ぶと RuntimeError になるため try で保護
    try:
        _VRAM_LOCK.release()
        logger.warning("VramGuard: force released VRAM lock")
    except RuntimeError:
        pass  # ロックが取得されていない状態 = 正常


def get_vram_status() -> dict:
    """
    現在の VRAM 使用状況を返す。

    Returns:
        {
            "current_model": str | None,  # 現在ロード中のエンジン名
            "vram_used_gb":  float,       # 推定 VRAM 使用量
            "vram_free_gb":  float,       # 推定空き VRAM
            "is_locked":     bool,        # ロック状態
        }
    """
    current = _get_current_model()
    used    = VRAM_USAGE.get(current, 0.0) if current else 0.0
    locked  = not _VRAM_LOCK.acquire(blocking=False)

    if not locked:
        # 取得できた = ロックされていなかった → 取得したので解放
        _VRAM_LOCK.release()

    return {
        "current_model": current,
        "vram_used_gb":  used,
        "vram_free_gb":  TOTAL_VRAM_GB - used,
        "is_locked":     locked,
    }
