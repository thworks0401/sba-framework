"""
スケジューラ: APScheduler 設定・自動実行管理

設計根拠（自己実験エンジン設定書 §9, タスクスケジュール v1.0 §5-6）:
  - 軽量実験：1時間ごと（5-15分, 3-5問）
  - 中量実験：6時間ごと（30-60分, 1-2ケース）
  - 重量実験：24時間ごと（2-4時間, 全SubSkill評価）
  - 自律学習ループ：設定可能インターバル
  - デイリーカウンタリセット：日付変更時（00:00）
  - SQLiteJobStore で永続化
  - NSSM Windows サービス登録

実装戦略:
  - APScheduler BackgroundScheduler
  - SQLiteJobStore（ジョブ永続化）
  - BlockingScheduler または BackgroundScheduler
  - Windows サービス登録用情報も提供
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional, Callable, Any, Dict, List

# APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.job import Job


logger = logging.getLogger(__name__)


class SBAScheduler:
    """
    SBA 統括スケジューラ

    3段階の実験 + 学習ループ + カウンタリセットを統制
    """

    def __init__(
        self,
        brain_id: str,
        brain_name: str,
        jobstore_path: str = "C:/SBA/data/scheduler_jobs.db",
    ):
        """
        Args:
            brain_id: Brain ID
            brain_name: Brain名
            jobstore_path:
Jobs DB パス（APScheduler SQLiteJobStore）
        """
        self.brain_id = brain_id
        self.brain_name = brain_name
        self.jobstore_path = Path(jobstore_path)
        self.jobstore_path.parent.mkdir(parents=True, exist_ok=True)

        # ジョブストア設定
        jobstore_url = f"sqlite:///{self.jobstore_path}"

        # スケジューラ初期化
        self.scheduler = BackgroundScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(url=jobstore_url)
            },
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
            }
        )

        self._is_running = False
        self._registered_jobs: Dict[str, Job] = {}

    # ======================================================================
    # ジョブ登録
    # ======================================================================

    def register_lightweight_experiment_job(
        self,
        callback: Callable,
        run_immediately: bool = False,
    ) -> Optional[Job]:
        """
        軽量実験ジョブ登録（1時間ごと）

        Args:
            callback: 実行するコール。async or sync
            run_immediately: 起動時に即実行するか

        Returns:
            Job インスタンス
        """
        try:
            if run_immediately:
                logger.info("Running lightweight experiment immediately...")
                # immediately実行は別途呼び出し
                pass

            job = self.scheduler.add_job(
                callback,
                name="lightweight_experiment",
                trigger=CronTrigger(minute=0),  # 毎時0分
                id="job_lightweight_exp",
                replace_existing=True,
            )

            self._registered_jobs["lightweight_experiment"] = job
            logger.info("Registered lightweight experiment job (hourly)")
            return job

        except Exception as e:
            logger.error(f"Error registering lightweight experiment job: {e}")
            return None

    def register_medium_experiment_job(
        self,
        callback: Callable,
    ) -> Optional[Job]:
        """
        中量実験ジョブ登録（6時間ごと）

        Args:
            callback: 実行するコール

        Returns:
            Job インスタンス
        """
        try:
            job = self.scheduler.add_job(
                callback,
                name="medium_experiment",
                trigger=CronTrigger(hour="*/6", minute=0),  # 0時, 6時, 12時, 18時
                id="job_medium_exp",
                replace_existing=True,
            )

            self._registered_jobs["medium_experiment"] = job
            logger.info("Registered medium experiment job (every 6 hours)")
            return job

        except Exception as e:
            logger.error(f"Error registering medium experiment job: {e}")
            return None

    def register_heavyweight_experiment_job(
        self,
        callback: Callable,
        run_hour: int = 3,  # AM 3:00 デフォルト
    ) -> Optional[Job]:
        """
        重量実験ジョブ登録（24時間ごと、夜間実行推奨）

        Args:
            callback: 実行するコール
            run_hour: 実行時刻（時, 0-23）

        Returns:
            Job インスタンス
        """
        try:
            job = self.scheduler.add_job(
                callback,
                name="heavyweight_experiment",
                trigger=CronTrigger(hour=run_hour, minute=0),
                id="job_heavyweight_exp",
                replace_existing=True,
            )

            self._registered_jobs["heavyweight_experiment"] = job
            logger.info(f"Registered heavyweight experiment job (daily at {run_hour:02d}:00)")
            return job

        except Exception as e:
            logger.error(f"Error registering heavyweight experiment job: {e}")
            return None

    def register_learning_loop_job(
        self,
        callback: Callable,
        interval_minutes: int = 120,  # デフォルト2時間ごと
    ) -> Optional[Job]:
        """
        自律学習ループジョブ登録

        Args:
            callback: 実行するコール
            interval_minutes: インターバル（分）

        Returns:
            Job インスタンス
        """
        try:
            from apscheduler.triggers.interval import IntervalTrigger

            job = self.scheduler.add_job(
                callback,
                name="learning_loop",
                trigger=IntervalTrigger(minutes=interval_minutes),
                id="job_learning_loop",
                replace_existing=True,
            )

            self._registered_jobs["learning_loop"] = job
            logger.info(
                f"Registered learning loop job (every {interval_minutes} minutes)"
            )
            return job

        except Exception as e:
            logger.error(f"Error registering learning loop job: {e}")
            return None

    def register_daily_counter_reset_job(
        self,
        callback: Callable,
    ) -> Optional[Job]:
        """
        デイリーカウンタリセットジョブ登録（毎日00:00）

        Args:
            callback: 実行するコール

        Returns:
            Job インスタンス
        """
        try:
            job = self.scheduler.add_job(
                callback,
                name="daily_counter_reset",
                trigger=CronTrigger(hour=0, minute=0),
                id="job_daily_counter_reset",
                replace_existing=True,
            )

            self._registered_jobs["daily_counter_reset"] = job
            logger.info("Registered daily counter reset job (00:00 daily)")
            return job

        except Exception as e:
            logger.error(f"Error registering daily counter reset job: {e}")
            return None

    # ======================================================================
    # スケジューラ制御
    # ======================================================================

    def start(self) -> bool:
        """スケジューラ起動"""
        try:
            if self._is_running:
                logger.warning("Scheduler already running")
                return True

            self.scheduler.start()
            self._is_running = True

            logger.info(
                f"SBA Scheduler started for {self.brain_name} "
                f"({len(self._registered_jobs)} jobs)"
            )
            return True

        except Exception as e:
            logger.error(f"Error starting scheduler: {e}")
            return False

    def stop(self) -> bool:
        """スケジューラ停止"""
        try:
            if not self._is_running:
                logger.warning("Scheduler not running")
                return True

            self.scheduler.shutdown(wait=True)
            self._is_running = False

            logger.info("SBA Scheduler stopped")
            return True

        except Exception as e:
            logger.error(f"Error stopping scheduler: {e}")
            return False

    def pause(self) -> bool:
        """スケジューラ一時停止"""
        try:
            self.scheduler.pause()
            logger.info("SBA Scheduler paused")
            return True
        except Exception as e:
            logger.error(f"Error pausing scheduler: {e}")
            return False

    def resume(self) -> bool:
        """スケジューラ再開"""
        try:
            self.scheduler.resume()
            logger.info("SBA Scheduler resumed")
            return True
        except Exception as e:
            logger.error(f"Error resuming scheduler: {e}")
            return False

    # ======================================================================
    # ジョブ管理
    # ======================================================================

    def get_job_list(self) -> List[Dict[str, Any]]:
        """登録中のジョブ一覧"""
        jobs_list = []

        for job in self.scheduler.get_jobs():
            jobs_list.append({
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": job.next_run_time.isoformat()
                if job.next_run_time else None,
            })

        return jobs_list

    def log_job_list(self) -> None:
        """ジョブ一覧をログ出力"""
        logger.info("=== Registered Jobs ===")
        for job_info in self.get_job_list():
            logger.info(
                f"  {job_info['id']}: {job_info['name']} "
                f"(next run: {job_info['next_run_time']})"
            )

    def remove_job(self, job_id: str) -> bool:
        """ジョブ削除"""
        try:
            self.scheduler.remove_job(job_id)
            if job_id in self._registered_jobs:
                del self._registered_jobs[job_id]
            logger.info(f"Removed job: {job_id}")
            return True
        except Exception as e:
            logger.error(f"Error removing job {job_id}: {e}")
            return False

    # ======================================================================
    # NSSM Windows サービス登録用情報
    # ======================================================================

    def get_nssm_registration_script(self, app_path: str) -> str:
        """
        NSSM サービス登録用 PowerShell スクリプト生成

        Args:
            app_path: SBA アプリケーション起動スクリプトパス

        Returns:
            PowerShell スクリプト（テキスト）
        """
        service_name = f"SBAScheduler_{self.brain_name}".replace(" ", "_")

        script = f"""
# SBA Scheduler - NSSM Windows Service Registration
# このスクリプトを PowerShell (管理者権限) で実行してください

$serviceName = "{service_name}"
$appPath = "{app_path}"

# NSSM のパスを設定（予め C:/tools/nssm に配置されていると仮定）
$nssmPath = "C:/tools/nssm/nssm.exe"

if (!(Test-Path $nssmPath)) {{
    Write-Error "NSSM not found at $nssmPath"
    exit 1
}}

if (!(Test-Path $appPath)) {{
    Write-Error "App not found at $appPath"
    exit 1
}}

# サービスが既に存在する場合は削除
if (Get-Service $serviceName -ErrorAction SilentlyContinue) {{
    Write-Host "Service $serviceName already exists. Removing..."
    & $nssmPath remove $serviceName confirm
}}

# 新しいサービスを登録
Write-Host "Installing service: $serviceName"
& $nssmPath install $serviceName "python" "$appPath"

# サービス設定
& $nssmPath set $serviceName AppDirectory "C:/TH_Works/SBA"
& $nssmPath set $serviceName AppNoConsole 1
& $nssmPath set $serviceName OutputDir "C:/SBA/logs"
& $nssmPath set $serviceName AppRotateFiles 1
& $nssmPath set $serviceName AppRotateOnline 1

Write-Host "Service installed successfully"
Write-Host "Start service with: net start $serviceName"
"""
        return script

    # ======================================================================
    # ステータスレポート
    # ======================================================================

    def get_status_report(self) -> Dict[str, Any]:
        """スケジューラステータスレポート"""
        return {
            "brain_id": self.brain_id,
            "brain_name": self.brain_name,
            "is_running": self._is_running,
            "scheduler_state": self.scheduler.state,
            "jobs": self.get_job_list(),
        }

    def log_status_report(self) -> None:
        """ステータスレポートをログ出力"""
        report = self.get_status_report()
        logger.info("=== SBA Scheduler Status Report ===")
        logger.info(f"Brain: {report['brain_name']} ({report['brain_id']})")
        logger.info(f"Running: {report['is_running']}")
        logger.info(f"State: {report['scheduler_state']}")
        logger.info(f"Scheduled Jobs: {len(report['jobs'])}")
        self.log_job_list()


# ======================================================================
# グローバルインスタンス（Singleton）
# ======================================================================

_scheduler_instance: Optional[SBAScheduler] = None


def get_scheduler(
    brain_id: str,
    brain_name: str,
    jobstore_path: str = "C:/SBA/data/scheduler_jobs.db",
) -> SBAScheduler:
    """
    グローバルスケジューラインスタンスを取得（Singleton）
    """
    global _scheduler_instance

    if _scheduler_instance is None:
        _scheduler_instance = SBAScheduler(brain_id, brain_name, jobstore_path)

    return _scheduler_instance
