"""
SQLite スキーマ自動生成 + リポジトリ（learning_timeline.db）

設計（補足設計書 §2.3）:
  timeline テーブル: 学習履歴（learned_at, source_type, freshness）
  json カラム: qdrant_ids, kg_node_ids（関連ポイントID一覧）
"""

from __future__ import annotations

import sqlite3
import uuid
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class TimelineDBError(Exception):
    """TimelineDB 操作に関する例外"""


class TimelineRepository:
    """learning_timeline.db リポジトリ"""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """スキーマ作成"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS timeline (
                id           TEXT    PRIMARY KEY,
                learned_at   TEXT    NOT NULL,
                brain_id     TEXT    NOT NULL,
                source_type  TEXT    NOT NULL,
                url_or_path  TEXT,
                content_hash TEXT    NOT NULL UNIQUE,
                subskill     TEXT    NOT NULL,
                freshness    REAL    NOT NULL DEFAULT 1.0
                             CHECK(freshness BETWEEN 0.0 AND 1.0),
                is_outdated  INTEGER NOT NULL DEFAULT 0
                             CHECK(is_outdated IN (0,1)),
                qdrant_ids   TEXT,
                kg_node_ids  TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tl_subskill
            ON timeline(subskill)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tl_source_type
            ON timeline(source_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tl_learned_at
            ON timeline(learned_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tl_freshness
            ON timeline(freshness)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tl_brain_id
            ON timeline(brain_id)
        """)

        conn.commit()
        conn.close()

    # ======================================================================
    # 書き込み
    # ======================================================================

    def insert_timeline(
        self,
        brain_id: str,
        source_type: str,
        content_hash: str,
        subskill: str,
        url_or_path: str = "",
        qdrant_ids: Optional[list] = None,
        kg_node_ids: Optional[list] = None,
        freshness: float = 1.0,
    ) -> str:
        """
        学習タイムライン追加。

        Returns:
            timeline ID (UUID)
        """
        timeline_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        qdrant_ids_json   = json.dumps(qdrant_ids   or [])
        kg_node_ids_json  = json.dumps(kg_node_ids  or [])

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO timeline
            (id, learned_at, brain_id, source_type, url_or_path, content_hash,
             subskill, freshness, is_outdated, qdrant_ids, kg_node_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timeline_id, now, brain_id, source_type, url_or_path, content_hash,
            subskill, freshness, 0, qdrant_ids_json, kg_node_ids_json
        ))

        conn.commit()
        conn.close()

        return timeline_id

    # ======================================================================
    # 読み取り
    # ======================================================================

    def get_timeline_entry(self, timeline_id: str) -> dict | None:
        """タイムライン取得"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM timeline WHERE id = ?", (timeline_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return self._parse_row(dict(row))

    def check_duplicate_by_hash(self, content_hash: str) -> Optional[str]:
        """
        content_hash による重複チェック。

        Returns:
            既存 timeline_id or None
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM timeline WHERE content_hash = ?", (content_hash,))
        row = cursor.fetchone()
        conn.close()

        return row[0] if row else None

    def get_timeline_by_subskill(
        self,
        subskill: str,
        limit: int = 100,
    ) -> list[dict]:
        """SubSkill 別学習履歴"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM timeline
            WHERE subskill = ?
            ORDER BY learned_at DESC
            LIMIT ?
        """, (subskill, limit))

        results = [self._parse_row(dict(row)) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_timeline_by_kg_node(self, kg_node_id: str) -> Optional[dict]:
        """
        Kuzu KnowledgeChunk ID で Timeline エントリを検索。

        KnowledgeStore.mark_deprecated() から呼ばれる。
        JSON 文字列 kg_node_ids に対する LIKE 検索を使用する。

        Args:
            kg_node_id: Kuzu KnowledgeChunk UUID

        Returns:
            マッチした Timeline エントリ、見つからなければ None
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # kg_node_ids は JSON 配列文字列 e.g. '["uuid-a", "uuid-b"]'
        # UUID を JSON 文字列内で検索する（部分一致: "uuid"）
        cursor.execute(
            'SELECT * FROM timeline WHERE kg_node_ids LIKE ?',
            (f'%"{kg_node_id}"%',),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return self._parse_row(dict(row))

    def get_outdated_entries(
        self,
        brain_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """古い学習データ取得（freshness < 0.4）"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM timeline
            WHERE brain_id = ? AND is_outdated = 1
            ORDER BY freshness ASC
            LIMIT ?
        """, (brain_id, limit))

        results = [self._parse_row(dict(row)) for row in cursor.fetchall()]
        conn.close()
        return results

    # ======================================================================
    # 更新
    # ======================================================================

    def update_freshness(
        self,
        timeline_id: str,
        freshness: float,
    ) -> None:
        """freshness 更新"""
        is_outdated = 1 if freshness < 0.4 else 0

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE timeline
            SET freshness = ?, is_outdated = ?
            WHERE id = ?
        """, (freshness, is_outdated, timeline_id))

        conn.commit()
        conn.close()

    # ======================================================================
    # 統計
    # ======================================================================

    def get_stats(self, brain_id: str) -> dict:
        """学習統計"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT source_type) as source_types,
                   AVG(freshness) as avg_freshness,
                   COUNT(CASE WHEN is_outdated = 1 THEN 1 END) as outdated_count
            FROM timeline
            WHERE brain_id = ?
        """, (brain_id,))

        row = cursor.fetchone()
        conn.close()

        return {
            "total_entries":    row[0] or 0,
            "source_types":     row[1] or 0,
            "avg_freshness":    row[2] or 0.0,
            "outdated_entries": row[3] or 0,
        }

    # ======================================================================
    # 内部ユーティリティ
    # ======================================================================

    @staticmethod
    def _parse_row(row_dict: dict) -> dict:
        """JSON カラムをデシリアライズ"""
        row_dict["qdrant_ids"]  = json.loads(row_dict.get("qdrant_ids")  or "[]")
        row_dict["kg_node_ids"] = json.loads(row_dict.get("kg_node_ids") or "[]")
        return row_dict
