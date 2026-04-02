"""
SQLite スキーマ自動生成 + リポジトリ（experiment_log.db）

設計（補足設計書 §2.3）:
  experiments テーブル: 実験ログ（UUID, SubSkill別, 結果）
  インデックス: subskill, result, executed_at, brain_id
"""

from __future__ import annotations

import sqlite3
import uuid
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class ExperimentDBError(Exception):
    """ExperimentDB 操作に関する例外"""


class ExperimentRepository:
    """experiment_log.db リポジトリ"""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """接続（Row Factory 有効化）"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """スキーマが存在しなければ作成"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id          TEXT    PRIMARY KEY,
                executed_at TEXT    NOT NULL,
                brain_id    TEXT    NOT NULL,
                subskill    TEXT    NOT NULL,
                exp_type    TEXT    NOT NULL CHECK(exp_type IN ('A','B','C','D')),
                hypothesis  TEXT    NOT NULL,
                plan        TEXT    NOT NULL,
                input_data  TEXT,
                output_data TEXT,
                result      TEXT    NOT NULL CHECK(result IN ('SUCCESS','FAILURE','PARTIAL')),
                analysis    TEXT,
                delta_score REAL    NOT NULL DEFAULT 0.0,
                exec_ms     INTEGER,
                created_at  TEXT    NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exp_subskill
            ON experiments(subskill)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exp_result
            ON experiments(result)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exp_executed_at
            ON experiments(executed_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exp_brain_id
            ON experiments(brain_id)
        """)

        conn.commit()
        conn.close()

    def insert_experiment(
        self,
        brain_id: str,
        subskill: str,
        exp_type: str,
        hypothesis: str,
        result: str,
        exp_id: Optional[str] = None,
        plan: str = "",
        input_data: str = "",
        output_data: str = "",
        analysis: str = "",
        delta_score: float = 0.0,
        exec_ms: Optional[int] = None,
    ) -> str:
        """
        新規実験ログ.

        Returns:
            experiment ID (UUID)
        """
        exp_id = exp_id or str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO experiments
            (id, executed_at, brain_id, subskill, exp_type, hypothesis, plan,
             input_data, output_data, result, analysis, delta_score, exec_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            exp_id, now, brain_id, subskill, exp_type, hypothesis, plan,
            input_data, output_data, result, analysis, delta_score, exec_ms, now
        ))

        conn.commit()
        conn.close()

        return exp_id

    def get_experiment(self, exp_id: str) -> dict | None:
        """実験ログ取得"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return dict(row)

    def get_experiments_by_subskill(
        self,
        subskill: str,
        limit: int = 100,
    ) -> list[dict]:
        """SubSkill 別実験一覧"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM experiments
            WHERE subskill = ?
            ORDER BY executed_at DESC
            LIMIT ?
        """, (subskill, limit))

        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results

    def get_experiments_by_result(
        self,
        result: str,
        limit: int = 100,
    ) -> list[dict]:
        """結果別一覧"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM experiments
            WHERE result = ?
            ORDER BY executed_at DESC
            LIMIT ?
        """, (result, limit))

        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results

    def update_experiment_analysis(
        self,
        exp_id: str,
        analysis: str,
        delta_score: float,
    ) -> None:
        """実験分析・スコア更新"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE experiments
            SET analysis = ?, delta_score = ?
            WHERE id = ?
        """, (analysis, delta_score, exp_id))

        conn.commit()
        conn.close()

    def get_stats(self, brain_id: str) -> dict:
        """実験統計"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN result = 'SUCCESS' THEN 1 ELSE 0 END) as success,
                   SUM(CASE WHEN result = 'FAILURE' THEN 1 ELSE 0 END) as failure,
                   SUM(CASE WHEN result = 'PARTIAL' THEN 1 ELSE 0 END) as partial,
                   AVG(delta_score) as avg_delta
            FROM experiments
            WHERE brain_id = ?
        """, (brain_id,))

        row = cursor.fetchone()
        conn.close()

        return {
            "total": row[0] or 0,
            "success": row[1] or 0,
            "failure": row[2] or 0,
            "partial": row[3] or 0,
            "avg_delta": row[4] or 0.0,
        }
