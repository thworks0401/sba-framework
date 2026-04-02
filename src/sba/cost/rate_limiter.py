"""
APIレート制限・デイリーカウンタ管理

設計根拠（自己実験エンジン設定書 §5-5, 自律学習ループ設定書 §4.2）:
  - api_usage.db を使った日次・月次カウンタ管理
  - 各API呼び出しごとに使用量をINSERT/UPDATE
  - WARNING/THROTTLE/STOP の3段階閾値判定
  - 停止状態は api_stops テーブルで永続管理

【修正履歴】
  APIUsageRepository に存在しないメソッドを呼んでいた以下を全て修正:
  - repo.is_api_stopped()     → repo.get_api_stop_status() is not None で判定
  - repo.get_api_stop_record()→ repo.get_api_stop_status()
  - repo.mark_api_stopped()   → repo.set_api_stopped()
  - repo.mark_api_resumed()   → repo.clear_api_stopped()
  - repo.get_api_thresholds() → repo.get_threshold()

  デフォルトの DB パスを SBAConfig.load_env() 経由で解決するよう修正。
  SBAConfig が使えない場合は C:/TH_Works/SBA/data/ にフォールバック。
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Tuple

from ..storage.api_usage_db import APIUsageRepository


logger = logging.getLogger(__name__)


def _resolve_default_db_path() -> str:
    """
    api_usage.db のデフォルトパスを SBAConfig から解決。
    SBAConfig がロードできない場合は C:/TH_Works/SBA/data/ を使用。
    """
    try:
        from ..config import SBAConfig
        cfg = SBAConfig.load_env()
        return str(cfg.data / "api_usage.db")
    except Exception:
        return "C:/TH_Works/SBA/data/api_usage.db"


class RateLimitStatus(Enum):
    """API使用状態"""
    OK       = "ok"
    WARNING  = "warning"   # 70% 超過（通知のみ）
    THROTTLE = "throttle"  # 85% 超過（一部APIを制限）
    STOP     = "stop"      # 95% 超過（完全停止）


class APIRateLimiter:
    """API レート制限とデイリーカウンタの統括マネージャー"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Args:
            db_path: api_usage.db へのパス（省略時: SBAConfig から自動解決）
        """
        resolved = db_path or _resolve_default_db_path()
        self.repo = APIUsageRepository(resolved)

    # ======================================================================
    # API使用量チェック・許可判定
    # ======================================================================

    def check_usage_before_call(
        self, api_name: str
    ) -> Tuple[bool, RateLimitStatus, str]:
        """
        API 呼び出し前に使用量チェック。STOP 状態なら False を返す。

        Returns:
            (allowed: bool, status: RateLimitStatus, reason: str)
        """
        # Stop状態チェック
        # 修正: is_api_stopped() → get_api_stop_status() is not None
        stop_record = self.repo.get_api_stop_status(api_name)
        if stop_record is not None:
            reason = f"API stopped: {stop_record.get('stop_reason', 'unknown')}"
            return (False, RateLimitStatus.STOP, reason)

        # 使用量チェック
        status, usage_info = self.check_status(api_name)

        if status == RateLimitStatus.STOP:
            if self._has_resume_override(api_name):
                return (True, RateLimitStatus.THROTTLE, "Manual resume override active")

            # 95% 超過時は自動停止
            # 修正: mark_api_stopped() → set_api_stopped()
            self._set_api_stopped(api_name, "95% threshold exceeded")
            return (False, RateLimitStatus.STOP, "Automatic stop: 95% threshold")

        elif status == RateLimitStatus.THROTTLE:
            logger.warning(
                f"{api_name}: Throttle threshold (85%) reached. Usage: {usage_info}"
            )
            return (True, RateLimitStatus.THROTTLE, "Throttle: 85% threshold")

        elif status == RateLimitStatus.WARNING:
            logger.info(
                f"{api_name}: Warning threshold (70%) reached. Usage: {usage_info}"
            )
            return (True, RateLimitStatus.WARNING, "Warning: 70% threshold")

        return (True, RateLimitStatus.OK, "OK")

    def check_status(self, api_name: str) -> Tuple[RateLimitStatus, str]:
        """
        API の現在の使用状況ステータスをチェック。

        Returns:
            (status: RateLimitStatus, info_str: str)
        """
        try:
            # 修正: get_api_thresholds() → get_threshold()
            thresholds = self.repo.get_threshold(api_name)
            if not thresholds:
                return (RateLimitStatus.OK, "No thresholds configured")

            daily_limit   = thresholds.get("daily_limit", 0)
            monthly_limit = thresholds.get("monthly_limit", 0)
            warn_pct      = thresholds.get("warn_pct",     0.70)
            throttle_pct  = thresholds.get("throttle_pct", 0.85)
            stop_pct      = thresholds.get("stop_pct",     0.95)

            # 日次使用量を優先チェック
            today_usage     = self.repo.get_today_usage(api_name)
            daily_req_count = today_usage.get("req_count", 0)

            if daily_limit > 0:
                ratio = daily_req_count / daily_limit
                info  = f"Daily {daily_req_count}/{daily_limit} ({ratio*100:.1f}%)"

                if ratio >= stop_pct:
                    return (RateLimitStatus.STOP, info)
                elif ratio >= throttle_pct:
                    return (RateLimitStatus.THROTTLE, info)
                elif ratio >= warn_pct:
                    return (RateLimitStatus.WARNING, info)

            # 月次使用量チェック（日次制限がない場合）
            month_usage       = self.repo.get_month_usage(api_name)
            monthly_req_count = month_usage.get("req_count", month_usage.get("total_req", 0))

            if monthly_limit > 0:
                ratio = monthly_req_count / monthly_limit
                info  = f"Monthly {monthly_req_count}/{monthly_limit} ({ratio*100:.1f}%)"

                if ratio >= stop_pct:
                    return (RateLimitStatus.STOP, info)
                elif ratio >= throttle_pct:
                    return (RateLimitStatus.THROTTLE, info)
                elif ratio >= warn_pct:
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
        api_name:    str,
        req_count:   int = 1,
        token_count: int = 0,
        unit_count:  int = 0,
    ) -> None:
        """API呼び出しを記録して使用量をカウント"""
        try:
            self.repo.increment_usage(
                api_name,
                req_count   = req_count,
                token_count = token_count,
                unit_count  = unit_count,
            )
            logger.debug(
                f"Recorded {api_name}: requests={req_count}, "
                f"tokens={token_count}, units={unit_count}"
            )
        except Exception as e:
            logger.error(f"Error recording {api_name} usage: {e}")

    # ======================================================================
    # API 停止・再開管理
    # ======================================================================

    def _set_api_stopped(self, api_name: str, reason: str) -> None:
        """API を停止状態にマーク（内部メソッド）"""
        try:
            # 修正: mark_api_stopped() → set_api_stopped()
            self.repo.set_api_stopped(api_name, reason)
            logger.warning(f"{api_name} marked as stopped: {reason}")
        except Exception as e:
            logger.error(f"Error marking {api_name} as stopped: {e}")

    def resume_api(self, api_name: str) -> None:
        """API を再開（管理者手動対応用）。"""
        try:
            # 修正: mark_api_resumed() → clear_api_stopped()
            self.repo.clear_api_stopped(api_name)
            logger.info(f"{api_name} resumed")
        except Exception as e:
            logger.error(f"Error resuming {api_name}: {e}")

    def get_api_stop_status(self, api_name: str) -> Optional[Dict]:
        """API停止情報を取得"""
        try:
            # 修正: get_api_stop_record() → get_api_stop_status()
            return self.repo.get_api_stop_status(api_name)
        except Exception as e:
            logger.error(f"Error getting stop status for {api_name}: {e}")
            return None

    # ======================================================================
    # デイリーカウンタリセット
    # ======================================================================

    def reset_daily_counters_if_needed(self) -> bool:
        """
        日付が変わっていればカウンタをリセット。
        （api_usage テーブルは usage_date カラムで日次管理のため自動的にリセット不要）
        """
        try:
            logger.info("Daily counter management: new date started")
            return True
        except Exception as e:
            logger.error(f"Error in daily counter management: {e}")
            return False

    # ======================================================================
    # ステータスレポート
    # ======================================================================

    def get_all_api_status(self) -> Dict[str, Dict]:
        """全API のステータスをレポート"""
        api_names = [
            "gemini", "youtube", "newsapi",
            "github", "stackoverflow", "huggingface",
        ]
        report = {}

        for api_name in api_names:
            status, info = self.check_status(api_name)
            # 修正: is_api_stopped() → get_api_stop_status() is not None
            is_stopped = self.repo.get_api_stop_status(api_name) is not None
            report[api_name] = {
                "status":  status.value,
                "info":    info,
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

    def _has_resume_override(self, api_name: str) -> bool:
        """
        手動 resume 後に同一使用量で即再停止しないためのワンショット判定。

        直近の resume_at が、当日の usage.updated_at 以後であれば、
        まだ新しい API 呼び出しが発生していないとみなして一時的に許可する。
        """
        try:
            override = self.repo.get_api_resume_override(api_name)
            if not override:
                return False

            today_usage = self.repo.get_today_usage(api_name)
            updated_at = today_usage.get("updated_at")
            resume_at = override.get("resume_at")
            if not updated_at or not resume_at:
                return False

            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            resume_dt = datetime.fromisoformat(resume_at.replace("Z", "+00:00"))
            return updated_dt <= resume_dt
        except Exception:
            return False


# ======================================================================
# Singleton
# ======================================================================

_rate_limiter_instance: Optional[APIRateLimiter] = None


def get_rate_limiter(db_path: Optional[str] = None) -> APIRateLimiter:
    """
    グローバル API Rate Limiter インスタンスを取得（Singleton）。
    db_path 省略時は SBAConfig から自動解決。
    """
    global _rate_limiter_instance

    if _rate_limiter_instance is None:
        _rate_limiter_instance = APIRateLimiter(db_path)

    return _rate_limiter_instance
