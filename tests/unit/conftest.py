"""
tests/unit/conftest.py

unit テスト専用の conftest。

【目的】
  tests/conftest.py は qdrant_client を MagicMock として sys.modules に差し込んでいる。
  しかし test_storage.py は本物の QdrantClient(:memory:) が動かないと成立しないテストのため、
  ここで qdrant_client 関連の stub を sys.modules から除去して本物に差し替える。

【修正対象】
  - QdrantClient が MagicMock() になっていたため
    vector_store.search() が空リストを返し query_hybrid が [] になっていた。

【方針】
  qdrant_client 関連の stub のみ sys.modules から削除して本物に再インポートさせる。
  sentence_transformers は test_storage.py が _FakeEmbedder で代替するため stub のまま。
  この conftest は tests/unit/ 以下のテストにのみ適用される。
"""

from __future__ import annotations

import importlib
import sys

import pytest


# ======================================================================
# qdrant_client stub を sys.modules から除去して本物に差し替える
# ======================================================================

_QDRANT_KEYS = [
    "qdrant_client",
    "qdrant_client.models",
    "qdrant_client.http",
    "qdrant_client.http.models",
]

# KnowledgeStore / VectorStore モジュールもアンロード対象
_SBA_STORAGE_KEYS = [
    "src.sba.storage",
    "src.sba.storage.vector_store",
    "src.sba.storage.knowledge_store",
    "src.sba.storage.graph_store",
    "src.sba.storage.embedder",
]


def _restore_real_qdrant() -> None:
    """
    sys.modules から qdrant_client stub を全て削除して
    Python に本物のパッケージを再インポートさせる。
    SBA storage モジュールもアンロードして、
    次の import 時に本物の qdrant_client で再構築させる。
    """
    # qdrant_client stub を削除
    for key in _QDRANT_KEYS:
        sys.modules.pop(key, None)

    # SBA storage モジュールをアンロード
    for key in _SBA_STORAGE_KEYS:
        sys.modules.pop(key, None)

    # 本物の qdrant_client を再インポート（存在確認）
    try:
        importlib.import_module("qdrant_client")
    except ImportError as e:
        pytest.skip(f"qdrant_client が未インストールのためスキップ: {e}")


# モジュールロード時に即時実行（fixture より前に適用が必要）
_restore_real_qdrant()
