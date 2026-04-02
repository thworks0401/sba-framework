"""
通知・ログ整備: 人間介入通知 + 構造化ロギング

設計根拠（自己実験エンジン設定書 §5-7）:
  - plyer: Windows デスクトップ通知
  - human_review.log: 要確認フラグが必要な項目
  - loguru: 構造化ログ（sba.log / experiments.log）
  - ログローテーション設定
  - 複数カテゴリのログストリーム分離

ログ分類:
  - sba.log: 全般的なSBA稼働ログ
  - experiments.log: 実験実行専用ログ
  - learning_loop.log: 学習ループ専用ログ
  - human_review.log: 人間確認が必要な項目（JSON Lines）

【修正履歴】
  デフォルトパス "C:/SBA/logs" のハードコードを
  SBAConfig.load_env() 経由の自動解決に変更。
  SBAConfig がロードできない場合は C:/TH_Works/SBA/logs にフォールバック。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

from loguru import logger as loguru_logger


def _resolve_default_log_dir() -> str:
    """ログディレクトリを SBAConfig から解決。失敗時はフォールバック。"""
    try:
        from ..config import SBAConfig
        cfg = SBAConfig.load_env()
        return str(cfg.logs)
    except Exception:
        return "C:/TH_Works/SBA/logs"


class NotificationType(Enum):
    """通知タイプ"""
    INFO         = "info"
    WARNING      = "warning"
    ERROR        = "error"
    SUCCESS      = "success"
    HUMAN_REVIEW = "human_review"


class HumanReviewItem:
    """人間確認が必要な項目"""

    def __init__(
        self,
        item_type: str,
        message:   str,
        severity:  str = "medium",
        context:   Optional[Dict[str, Any]] = None,
    ):
        self.item_type = item_type
        self.message   = message
        self.severity  = severity
        self.context   = context or {}
        self.timestamp = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "type":      self.item_type,
            "severity":  self.severity,
            "message":   self.message,
            "context":   self.context,
        }

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class SBANotifier:
    """通知・ログ統括マネージャー"""

    def __init__(
        self,
        log_dir:  Optional[str] = None,
        app_name: str           = "SBA Framework",
    ):
        """
        Args:
            log_dir:  ログディレクトリパス（省略時: SBAConfig から自動解決）
            app_name: アプリケーション名（通知用）
        """
        resolved = log_dir or _resolve_default_log_dir()
        self.log_dir = Path(resolved)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.app_name              = app_name
        self.human_review_log_path = self.log_dir / "human_review.log"

        self._setup_loguru()

    def _setup_loguru(self) -> None:
        """loguru ログシステム初期化"""
        loguru_logger.remove()

        # メインログ（全コンポーネント）
        loguru_logger.add(
            str(self.log_dir / "sba.log"),
            format    = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            level     = "DEBUG",
            rotation  = "100 MB",
            retention = "30 days",
            encoding  = "utf-8",
        )

        # 実験専用ログ
        loguru_logger.add(
            str(self.log_dir / "experiments.log"),
            format    = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level     = "INFO",
            rotation  = "50 MB",
            retention = "14 days",
            filter    = lambda record: "experiment" in record["name"].lower(),
            encoding  = "utf-8",
        )

        # 学習ループ専用ログ
        loguru_logger.add(
            str(self.log_dir / "learning_loop.log"),
            format    = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level     = "INFO",
            rotation  = "50 MB",
            retention = "14 days",
            filter    = lambda record: "learning" in record["name"].lower(),
            encoding  = "utf-8",
        )

        # コンソール（WARNING以上）
        loguru_logger.add(
            lambda msg: print(msg, end=""),
            format = "{time:HH:mm:ss} | {level: <8} | {message}",
            level  = "WARNING",
        )

    # ======================================================================
    # 通知
    # ======================================================================

    def send_notification(
        self,
        notification_type: NotificationType,
        title:             str,
        message:           str,
        timeout_seconds:   int = 10,
    ) -> bool:
        """Windows デスクトップ通知を送信"""
        try:
            from plyer import notification
            notification.notify(
                title    = f"[{self.app_name}] {title}",
                message  = message,
                app_name = self.app_name,
                timeout  = timeout_seconds,
            )
            loguru_logger.info(
                f"Notification sent: {notification_type.value} | {title} | {message[:100]}"
            )
            return True
        except ImportError:
            loguru_logger.warning("plyer not installed, skipping notification")
            return False
        except Exception as e:
            loguru_logger.error(f"Error sending notification: {e}")
            return False

    # ======================================================================
    # 人間確認フラグ記録
    # ======================================================================

    def log_human_review_item(self, item: HumanReviewItem) -> bool:
        """人間確認が必要な項目を記録"""
        try:
            with open(self.human_review_log_path, "a", encoding="utf-8") as f:
                f.write(item.to_json_line() + "\n")

            loguru_logger.info(
                f"Human review item logged: {item.item_type} ({item.severity})"
            )

            if item.severity == "high":
                self.send_notification(
                    NotificationType.HUMAN_REVIEW,
                    f"Human Review Required: {item.item_type}",
                    item.message,
                    timeout_seconds=20,
                )
            return True
        except Exception as e:
            loguru_logger.error(f"Error logging human review item: {e}")
            return False

    # ======================================================================
    # ログレベル別メソッド
    # ======================================================================

    def debug(self, message: str, **kwargs) -> None:
        loguru_logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        loguru_logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        loguru_logger.warning(message, **kwargs)
        self.send_notification(
            NotificationType.WARNING, "Warning", message[:200], timeout_seconds=15
        )

    def error(self, message: str, **kwargs) -> None:
        loguru_logger.error(message, **kwargs)
        self.send_notification(
            NotificationType.ERROR, "Error", message[:200], timeout_seconds=15
        )

    def success(self, message: str, **kwargs) -> None:
        loguru_logger.info(f"SUCCESS: {message}", **kwargs)
        self.send_notification(
            NotificationType.SUCCESS, "Success", message[:200], timeout_seconds=10
        )

    # ======================================================================
    # 実験・学習ログ特化メソッド
    # ======================================================================

    def log_experiment_result(
        self,
        experiment_id: str,
        subskill:      str,
        result:        str,
        score_change:  float,
        details:       Optional[Dict] = None,
    ) -> None:
        """実験結果をログ記録"""
        msg = (
            f"Experiment: {experiment_id} | {subskill} | {result} | "
            f"Score: {score_change:+.2f}"
        )
        if details:
            msg += f" | Details: {details}"
        loguru_logger.info(msg)

    def log_learning_cycle_result(
        self,
        cycle_id:      str,
        brain_name:    str,
        overall_score: float,
        level:         str,
        details:       Optional[Dict] = None,
    ) -> None:
        """学習サイクルの結果をログ記録"""
        msg = (
            f"Learning Cycle: {cycle_id} | {brain_name} | "
            f"Score: {overall_score:.2%} | Lv: {level}"
        )
        if details:
            msg += f" | Details: {details}"
        loguru_logger.info(msg)

    # ======================================================================
    # 統計レポート
    # ======================================================================

    def get_human_review_items(
        self,
        limit:            Optional[int] = 100,
        severity_filter:  Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """人間確認ログから項目を読み込み"""
        items = []
        try:
            if not self.human_review_log_path.exists():
                return items

            with open(self.human_review_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        if severity_filter and item.get("severity") != severity_filter:
                            continue
                        items.append(item)
                        if limit and len(items) >= limit:
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            loguru_logger.error(f"Error reading human review items: {e}")
        return items

    def log_human_review_summary(self) -> None:
        """人間確認ログの集計レポート"""
        items = self.get_human_review_items(limit=None)
        if not items:
            loguru_logger.info("No human review items")
            return

        severity_counter = {"high": 0, "medium": 0, "low": 0}
        type_counter:     Dict[str, int] = {}

        for item in items:
            s = item.get("severity", "unknown")
            if s in severity_counter:
                severity_counter[s] += 1
            t = item.get("type", "unknown")
            type_counter[t] = type_counter.get(t, 0) + 1

        loguru_logger.info("=== Human Review Summary ===")
        loguru_logger.info(f"Total Items: {len(items)}")
        loguru_logger.info(
            f"By Severity: High={severity_counter['high']}, "
            f"Medium={severity_counter['medium']}, Low={severity_counter['low']}"
        )
        for item_type, count in type_counter.items():
            loguru_logger.info(f"  {item_type}: {count}")


# ======================================================================
# Singleton
# ======================================================================

_notifier_instance: Optional[SBANotifier] = None


def get_notifier(
    log_dir:  Optional[str] = None,
    app_name: str           = "SBA Framework",
) -> SBANotifier:
    """
    グローバル通知マネージャーインスタンスを取得（Singleton）。
    log_dir 省略時は SBAConfig から自動解決。
    """
    global _notifier_instance

    if _notifier_instance is None:
        _notifier_instance = SBANotifier(log_dir, app_name)

    return _notifier_instance
