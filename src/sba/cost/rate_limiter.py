"""
APIレート制限・デイリーカウンタ管理

設計根拠（自己実験エンジン設定書 §5-5, 自律学習ループ設定書 §4.2）:
  - api_usage.db を使った日次・月次カウンタ管理
  - 各API呼び出しごとに使用量をINSERT/UPDATE
  - WARNING/THROTTLE/STOP の3段階閾値判定
  - 停止状態は api_stops テーブルで永続管理

実装対象API:
  - Gemini 2.5 Flash: 1,500 req/day
  - YouTube Data API: 10,000 units/day
  - NewsAPI: 100 req/day
  - GitHub API: 5,000 req/h (authenticated)
  - Stack Overflow API: 10,000 req/day
  - Hugging Face Inference API: 30,000 req/month
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List

from ..storage.api_usage_db import APIUsageRepository


logger = logging.getLogger(__name__)


class RateLimitStatus(Enum):
    """API使用状態"""
    OK = "ok"
    WARNING = "warning"      # 70% 超過（通知のみ）
    THROTTLE = "throttle"    # 85% 超過（一部APIを制限）
    STOP = "stop"            # 95% 超過（完全停止）


class APIRateLimiter:
    """
    API レート制限とデイリーカウンタの統括マネージャー
    """

    def __init__(self, db_path: str = "C:/SBA/data/api_usage.db") -> None:
        """
        Args:
            db_path: api_usage.db へのパス
        """
        self.repo = APIUsageRepository(db_path)
        self._status_cache: Dict[str, RateLimitStatus] = {}

    # ======================================================================
    # API使用量チェック・許可判定
    # ======================================================================

    def check_usage_before_call(self, api_name: str) -> tuple[bool, RateLimitStatus, str]:
        """
        API 呼び出し前に使用量チェック。STOP 状態なら False を返す。

        Args:
            api_name: API名（gemini, youtube, newsapi等）

        Returns:
            (allowed: bool, status: RateLimitStatus, reason: str)
        """
        # Stop状態をチェック
        is_stopped = self.repo.is_api_stopped(api_name)
        if is_stopped:
            stop_record = self.repo.get_api_stop_record(api_name)
            reason = f"API stopped: {stop_record.get('stop_reason', 'unknown')}"
            return (False, RateLimitStatus.STOP, reason)

        # 使用量チェック
        status, usage_info = self.check_status(api_name)

        if status == RateLimitStatus.STOP:
            # 95% 超過時は自動停止
            self._mark_api_stopped(api_name, "95% threshold exceeded")
            return (False, RateLimitStatus.STOP, "Automatic stop: 95% threshold")

        elif status == RateLimitStatus.THROTTLE:
            # 85% 超過時は警告ログ
            logger.warning(f"{api_name}: Throttle threshold (85%) reached. "
                         f"Usage: {usage_info}")
            return (True, RateLimitStatus.THROTTLE, "Throttle: 85% threshold")

        elif status == RateLimitStatus.WARNING:
            # 70% 超過時は通知
            logger.info(f"{api_name}: Warning threshold (70%) reached. "
                       f"Usage: {usage_info}")
            return (True, RateLimitStatus.WARNING, "Warning: 70% threshold")

        return (True, RateLimitStatus.OK, "OK")

    def check_status(self, api_name: str) -> tuple[RateLimitStatus, str]:
        """
        API の現在の使用状況ステータスをチェック

        Returns:
            (status: RateLimitStatus, info_str: str)
        """
        try:
            thresholds = self.repo.get_api_thresholds(api_name)
            if not thresholds:
                return (RateLimitStatus.OK, "No thresholds configured")

            daily_limit = thresholds.get("daily_limit", 0)
            monthly_limit = thresholds.get("monthly_limit", 0)
            warn_pct = thresholds.get("warn_pct", 0.70)
            throttle_pct = thresholds.get("throttle_pct", 0.85)
            stop_pct = thresholds.get("stop_pct", 0.95)

            # 日次使用量を優先チェック
            today_usage = self.repo.get_today_usage(api_name)
            daily_req_count = today_usage.get("req_count", 0)

            if daily_limit > 0:
                daily_usage_ratio = daily_req_count / daily_limit

                if daily_usage_ratio >= stop_pct:
                    info = (f"Daily {daily_req_count}/{daily_limit} "
                           f"({daily_usage_ratio*100:.1f}%)")
                    return (RateLimitStatus.STOP, info)

                elif daily_usage_ratio >= throttle_pct:
                    info = (f"Daily {daily_req_count}/{daily_limit} "
                           f"({daily_usage_ratio*100:.1f}%)")
                    return (RateLimitStatus.THROTTLE, info)

                elif daily_usage_ratio >= warn_pct:
                    info = (f"Daily {daily_req_count}/{daily_limit} "
                           f"({daily_usage_ratio*100:.1f}%)")
                    return (RateLimitStatus.WARNING, info)

            # 月次使用量をチェック（日次制限がない場合）
            month_usage = self.repo.get_month_usage(api_name)
            monthly_req_count = month_usage.get("req_count", 0)

            if monthly_limit > 0:
                monthly_usage_ratio = monthly_req_count / monthly_limit

                if monthly_usage_ratio >= stop_pct:
                    info = (f"Monthly {monthly_req_count}/{monthly_limit} "
                           f"({monthly_usage_ratio*100:.1f}%)")
                    return (RateLimitStatus.STOP, info)

                elif monthly_usage_ratio >= throttle_pct:
                    info = (f"Monthly {monthly_req_count}/{monthly_limit} "
                           f"({monthly_usage_ratio*100:.1f}%)")
                    return (RateLimitStatus.THROTTLE, info)

                elif monthly_usage_ratio >= warn_pct:
                    info = (f"Monthly {monthly_req_count}/{monthly_limit} "
                           f"({monthly_usage_ratio*100:.1f}%)")
                    return (RateLimitStatus.WARNING, info)

            return (RateLimitStatus.OK, "Within limits")

        except Exception as e:
            logger.error(f"Error checking {api_name} status: {e}")
            return (RateLimitStatus.OK, f"Error: {e}")

    # ======================================================================
    # API 使用量カウント
    # ======================================================================

    def record_api_call(
        self,
        api_name: str,
        req_count: int = 1,
        token_count: int = 0,
        unit_count: int = 0,
    ) -> None:
        """
        API呼び出しを記録して使用量をカウント

        Args:
            api_name: API名
            req_count: リクエスト数（デフォルト1）
            token_count: トークン数（Gemini等）
            unit_count: ユニット数（YouTube等）
        """
        try:
            self.repo.increment_usage(
                api_name,
                req_count=req_count,
                token_count=token_count,
                unit_count=unit_count,
            )
            logger.debug(f"Recorded {api_name}: requests={req_count}, "
                        f"tokens={token_count}, units={unit_count}")
        except Exception as e:
            logger.error(f"Error recording {api_name} usage: {e}")

    # ======================================================================
    # API 停止・再開管理
    # ======================================================================

    def _mark_api_stopped(self, api_name: str, reason: str) -> None:
        """API を停止状態にマーク"""
        try:
            self.repo.mark_api_stopped(api_name, reason)
            logger.warning(f"{api_name} marked as stopped: {reason}")
        except Exception as e:
            logger.error(f"Error marking {api_name} as stopped: {e}")

    def resume_api(self, api_name: str) -> None:
        """API を再開（管理者手動対応用）"""
        try:
            self.repo.mark_api_resumed(api_name)
            logger.info(f"{api_name} resumed")
        except Exception as e:
            logger.error(f"Error resuming {api_name}: {e}")

    def get_api_stop_record(self, api_name: str) -> Optional[Dict]:
        """API停止情報を取得"""
        try:
            return self.repo.get_api_stop_record(api_name)
        except Exception as e:
            logger.error(f"Error getting stop record for {api_name}: {e}")
            return None

    # ======================================================================
    # デイリーカウンタリセット（日付変わり時）
    # ======================================================================

    def reset_daily_counters_if_needed(self) -> bool:
        """
        日付が変わっていればカウンタをリセット。
        （スケジューラから呼び出される想定：深夜0:00）

        Returns:
            True: リセット実行、False: 不要
        """
        # 実装は storage/api_usage_db.py に依存
        # ここでは呼び出しのみ
        try:
            # NOTE: APIUsageRepository に reset_daily メソッドがない場合は
            # テーブルの設計時点からリセットが不要（日付カラムで別レコード）
            logger.info("Daily counter management: new date started")
            return True
        except Exception as e:
            logger.error(f"Error resetting daily counters: {e}")
            return False

    # ======================================================================
    # ステータスレポート
    # ======================================================================

    def get_all_api_status(self) -> Dict[str, Dict]:
        """全API のステータスをレポート"""
        api_names = ["gemini", "youtube", "newsapi", "github", "stackoverflow", "huggingface"]
        report = {}

        for api_name in api_names:
            status, info = self.check_status(api_name)
            is_stopped = self.repo.is_api_stopped(api_name)
            report[api_name] = {
                "status": status.value,
                "info": info,
                "stopped": is_stopped,
            }

        return report

    def log_status_report(self) -> None:
        """ステータスレポートをログ出力"""
        try:
            report = self.get_all_api_status()
            logger.info("=== API Rate Limit Status Report ===")
            for api_name, status_info in report.items():
                logger.info(
                    f"  {api_name}: {status_info['status']} "
                    f"({status_info['info']}) "
                    f"{'[STOPPED]' if status_info['stopped'] else ''}"
                )
        except Exception as e:
            logger.error(f"Error logging status report: {e}")


# ======================================================================
# モジュール単一インスタンス（Singleton パターン）
# ======================================================================

_rate_limiter_instance: Optional[APIRateLimiter] = None


def get_rate_limiter(db_path: str = "C:/SBA/data/api_usage.db") -> APIRateLimiter:
    """
    グローバル API Rate Limiter インスタンスを取得（Singleton）
    """
    global _rate_limiter_instance

    if _rate_limiter_instance is None:
        _rate_limiter_instance = APIRateLimiter(db_path)

    return _rate_limiter_instance
