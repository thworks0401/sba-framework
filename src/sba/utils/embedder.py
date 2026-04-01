"""
Embedder — BAAI/bge-m3 ラッパー

設計根拠（補足設計書 §1.2.3）:
  - モデル: BAAI/bge-m3（多言語対応・日本語品質高）
  - device: CPU 固定（RTX 3060Ti 8GB VRAM を Phi-4:14B に専有させるため）
  - シングルトン: モデルロードは重い（初回 ~3秒）ので、プロセス内で1インスタンスのみ保持

使い方:
    embedder = Embedder.get_instance()
    vec  = embedder.encode_single("Pythonのデコレータとは")
    vecs = embedder.encode(["テキスト1", "テキスト2"])
"""

from __future__ import annotations

import threading
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer


class EmbedderError(Exception):
    """Embedder 操作に関する例外"""


class Embedder:
    """
    BAAI/bge-m3 のシングルトンラッパー。

    Note:
        device は常に CPU を使用する。
        VRAM は Phi-4:14B (Tier1) / Qwen2.5-Coder (Tier3) 専用とする設計のため、
        GPU 搭載環境でも Embedder を CUDA に乗せてはならない（補足設計書 §1.2.3）。
    """

    MODEL_NAME = "BAAI/bge-m3"
    # 補足設計書§2.1: コサイン類似度での重複判定閾値
    DEDUP_THRESHOLD = 0.92

    _instance: "Embedder | None" = None
    _lock = threading.Lock()

    # ------------------------------------------------------------------
    # シングルトン取得
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "Embedder":
        """スレッドセーフなシングルトン取得"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        # 補足設計書 §1.2.3: VRAMは Phi-4 専有のため Embedder は強制 CPU
        self.model = SentenceTransformer(self.MODEL_NAME, device="cpu")

    # ------------------------------------------------------------------
    # エンコード
    # ------------------------------------------------------------------

    def encode(
        self,
        texts: List[str],
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> np.ndarray:
        """
        テキストのリストをベクトル化する。

        Args:
            texts:         ベクトル化するテキストのリスト
            batch_size:    バッチサイズ（デフォルト 32）
            show_progress: tqdm プログレスバーを表示するか

        Returns:
            shape (len(texts), 1024) の numpy 配列（L2 正規化済み）
        """
        if not texts:
            return np.empty((0, 1024), dtype=np.float32)

        return self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,   # コサイン類似度用に L2 正規化
            show_progress_bar=show_progress,
        )

    def encode_single(self, text: str) -> np.ndarray:
        """
        単一テキストをベクトル化する。

        Returns:
            shape (1024,) の numpy 配列
        """
        return self.encode([text])[0]

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    def cosine_similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """
        L2 正規化済みベクトル間のコサイン類似度を返す。
        encode() は normalize_embeddings=True なので内積で計算できる。
        """
        return float(np.dot(vec_a, vec_b))

    def is_duplicate(self, vec_a: np.ndarray, vec_b: np.ndarray) -> bool:
        """
        補足設計書§2.1 の重複判定（コサイン類似度 > 0.92 で重複とみなす）
        """
        return self.cosine_similarity(vec_a, vec_b) > self.DEDUP_THRESHOLD
