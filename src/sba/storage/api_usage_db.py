"""
SQLite スキーマ自動生成 + リポジトリ（api_usage.db）

設計（補足設計書 §2.3）:
  api_usage テーブル: 日次・月次使用量カウンタ
  api_stops テーブル: API 停止状態管理
  api_thresholds テーブル: スロットリング設定（変更可能）

注意: このDB は Brain Package 外に置く（C:/SBA/data/api_usage.db）
      Hot-Swap 対象外の共有 DB
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional


class APIUsageDBError(Exception):
    """APIUsageDB 操作に関する例外"""


class APIUsageRepository:
    """api_usage.db リポジトリ"""

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

        # api_usage テーブル
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                api_name     TEXT    NOT NULL,
                usage_date   TEXT    NOT NULL,
                usage_month  TEXT    NOT NULL,
                req_count    INTEGER NOT NULL DEFAULT 0,
                token_count  INTEGER NOT NULL DEFAULT 0,
                unit_count   INTEGER NOT NULL DEFAULT 0,
                updated_at   TEXT    NOT NULL,
                UNIQUE(api_name, usage_date)
            )
        """)

        # api_stops テーブル
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_stops (
                api_name     TEXT    PRIMARY KEY,
                stopped_at   TEXT,
                stop_reason  TEXT,
                resume_at    TEXT
            )
        """)

        # api_thresholds テーブル
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_thresholds (
                api_name       TEXT    PRIMARY KEY,
                daily_limit    INTEGER,
                monthly_limit  INTEGER,
                warn_pct       REAL    NOT NULL DEFAULT 0.70,
                throttle_pct   REAL    NOT NULL DEFAULT 0.85,
                stop_pct       REAL    NOT NULL DEFAULT 0.95
            )
        """)

        conn.commit()

        # デフォルト閾値設定
        self._init_default_thresholds(cursor)

        conn.commit()
        conn.close()

    def _init_default_thresholds(self, cursor) -> None:
        """デフォルト API 閾値初期化"""
        defaults = [
            ("gemini", 1500, 50000),               # Gemini: 日1500リクエスト, 月50000
            ("youtube", 100, 10000),              # YouTube: 日100リクエスト
            ("newsapi", 250, 20000),              # NewsAPI: 日250リクエスト
            ("github", 500, 50000),               # GitHub: 日500リクエスト
            ("stackoverflow", 300, 30000),        # StackOverflow: 日300リクエスト
            ("huggingface", 200, 10000),          # HuggingFace: 日200リクエスト
        ]

        for api_name, daily, monthly in defaults:
            cursor.execute("""
                INSERT OR IGNORE INTO api_thresholds
                (api_name, daily_limit, monthly_limit)
                VALUES (?, ?, ?)
            """, (api_name, daily, monthly))

    def increment_usage(
        self,
        api_name: str,
        req_count: int = 0,
        token_count: int = 0,
        unit_count: int = 0,
    ) -> None:
        """API 使用量カウント"""
        now = datetime.utcnow()
        usage_date = now.strftime("%Y-%m-%d")
        usage_month = now.strftime("%Y-%m")
        timestamp = now.isoformat() + "Z"

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO api_usage
            (api_name, usage_date, usage_month, req_count, token_count, unit_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(api_name, usage_date) DO UPDATE SET
                req_count = req_count + ?,
                token_count = token_count + ?,
                unit_count = unit_count + ?,
                updated_at = ?
        """, (
            api_name, usage_date, usage_month, req_count, token_count, unit_count, timestamp,
            req_count, token_count, unit_count, timestamp
        ))

        conn.commit()
        conn.close()

    def get_today_usage(self, api_name: str) -> dict:
        """今日のAPI使用量取得"""
        now = datetime.utcnow()
        usage_date = now.strftime("%Y-%m-%d")

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM api_usage
            WHERE api_name = ? AND usage_date = ?
        """, (api_name, usage_date))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return {
                "api_name": api_name,
                "usage_date": usage_date,
                "req_count": 0,
                "token_count": 0,
                "unit_count": 0,
            }

        return dict(row)

    def get_month_usage(self, api_name: str) -> dict:
        """今月のAPI使用量合計"""
        now = datetime.utcnow()
        usage_month = now.strftime("%Y-%m")

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT SUM(req_count) as total_req,
                   SUM(token_count) as total_token,
                   SUM(unit_count) as total_unit
            FROM api_usage
            WHERE api_name = ? AND usage_month = ?
        """, (api_name, usage_month))

        row = cursor.fetchone()
        conn.close()

        return {
            "api_name": api_name,
            "usage_month": usage_month,
            "total_req": row[0] or 0,
            "total_token": row[1] or 0,
            "total_unit": row[2] or 0,
        }

    def set_api_stopped(
        self,
        api_name: str,
        reason: str,
        resume_at: Optional[str] = None,
    ) -> None:
        """API 停止状態セット"""
        now = datetime.utcnow().isoformat() + "Z"

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO api_stops
            (api_name, stopped_at, stop_reason, resume_at)
            VALUES (?, ?, ?, ?)
        """, (api_name, now, reason, resume_at))

        conn.commit()
        conn.close()

    def clear_api_stopped(self, api_name: str) -> None:
        """API 停止状態クリア"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE api_stops
            SET stopped_at = NULL, stop_reason = NULL, resume_at = NULL
            WHERE api_name = ?
        """, (api_name,))

        conn.commit()
        conn.close()

    def get_api_stop_status(self, api_name: str) -> dict | None:
        """API 停止状態取得"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM api_stops WHERE api_name = ?", (api_name,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        row_dict = dict(row)
        # stopped_at が NULL = 停止状態なし
        if row_dict["stopped_at"] is None:
            return None

        return row_dict

    def get_threshold(self, api_name: str) -> dict | None:
        """API 閾値取得"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM api_thresholds WHERE api_name = ?", (api_name,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return dict(row)

    def get_all_api_status(self) -> dict:
        """全 API のステータスダッシュボード"""
        conn = self._get_conn()
        cursor = conn.cursor()

        # 今日の使用量
        now = datetime.utcnow()
        usage_date = now.strftime("%Y-%m-%d")

        cursor.execute("""
            SELECT api_name, req_count, token_count, unit_count
            FROM api_usage
            WHERE usage_date = ?
        """, (usage_date,))

        today_usage = {row[0]: {"req": row[1], "token": row[2], "unit": row[3]}
                       for row in cursor.fetchall()}

        # 停止状態
        cursor.execute("SELECT api_name, stopped_at, stop_reason FROM api_stops")
        stopped = {row[0]: {"stopped_at": row[1], "reason": row[2]}
                   for row in cursor.fetchall() if row[1]}

        conn.close()

        return {
            "today_usage": today_usage,
            "stopped_apis": stopped,
        }
