"""
スケジューラ: APScheduler 設定・自動実行管理

設計根拠（自己実験エンジン設定書 §9, タスクスケジュール v1.0 §5-6）:
  - 軽量実験：1時間ごと
  - 中量実験：6時間ごと
  - 重量実験：24時間ごと（夜間）
  - 自律学習ループ：設定可能インターバル
  - デイリーカウンタリセット：00:00
  - SQLiteJobStore で永続化
  - NSSM Windows サービス登録

【修正履歴】
  デフォルトパス "C:/SBA/data/scheduler_jobs.db" のハードコードを
  SBAConfig.load_env() 経由の自動解決に変更。
  SBAConfig がロードできない場合は C:/TH_Works/SBA/data/ にフォールバック。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any, Dict, List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.job import Job


logger = logging.getLogger(__name__)


def _resolve_default_jobstore_path() -> str:
    """jobstore DB パスを SBAConfig から解決。失敗時はフォールバック。"""
    try:
        from ..config import SBAConfig
        cfg = SBAConfig.load_env()
        return str(cfg.data / "scheduler_jobs.db")
    except Exception:
        return "C:/TH_Works/SBA/data/scheduler_jobs.db"


class SBAScheduler:
    """SBA 統括スケジューラ"""

    def __init__(
        self,
        brain_id: str,
        brain_name: str,
        jobstore_path: Optional[str] = None,
    ):
        """
        Args:
            brain_id:      Brain ID
            brain_name:    Brain名
            jobstore_path: Jobs DB パス（省略時: SBAConfig から自動解決）
        """
        self.brain_id   = brain_id
        self.brain_name = brain_name

        resolved_path = jobstore_path or _resolve_default_jobstore_path()
        self.jobstore_path = Path(resolved_path)
        self.jobstore_path.parent.mkdir(parents=True, exist_ok=True)

        jobstore_url = f"sqlite:///{self.jobstore_path}"

        self.scheduler = BackgroundScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(url=jobstore_url)
            },
            job_defaults={
                "coalesce":      True,
                "max_instances": 1,
            }
        )

        self._is_running      = False
        self._registered_jobs: Dict[str, Job] = {}

    # ======================================================================
    # ジョブ登録
    # ======================================================================

    def register_lightweight_experiment_job(
        self,
        callback: Callable,
        run_immediately: bool = False,
    ) -> Optional[Job]:
        """軽量実験ジョブ登録（1時間ごと）"""
        try:
            job = self.scheduler.add_job(
                callback,
                name            = "lightweight_experiment",
                trigger         = CronTrigger(minute=0),  # 毎時0分
                id              = "job_lightweight_exp",
                replace_existing = True,
            )
            self._registered_jobs["lightweight_experiment"] = job
            logger.info("Registered lightweight experiment job (hourly)")
            return job
        except Exception as e:
            logger.error(f"Error registering lightweight experiment job: {e}")
            return None

    def register_medium_experiment_job(self, callback: Callable) -> Optional[Job]:
        """中量実験ジョブ登録（6時間ごと）"""
        try:
            job = self.scheduler.add_job(
                callback,
                name            = "medium_experiment",
                trigger         = CronTrigger(hour="*/6", minute=0),
                id              = "job_medium_exp",
                replace_existing = True,
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
        run_hour: int = 3,
    ) -> Optional[Job]:
        """重量実験ジョブ登録（24時間ごと、夜間実行推奨）"""
        try:
            job = self.scheduler.add_job(
                callback,
                name            = "heavyweight_experiment",
                trigger         = CronTrigger(hour=run_hour, minute=0),
                id              = "job_heavyweight_exp",
                replace_existing = True,
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
        interval_minutes: int = 120,
    ) -> Optional[Job]:
        """自律学習ループジョブ登録"""
        try:
            job = self.scheduler.add_job(
                callback,
                name            = "learning_loop",
                trigger         = IntervalTrigger(minutes=interval_minutes),
                id              = "job_learning_loop",
                replace_existing = True,
            )
            self._registered_jobs["learning_loop"] = job
            logger.info(f"Registered learning loop job (every {interval_minutes} minutes)")
            return job
        except Exception as e:
            logger.error(f"Error registering learning loop job: {e}")
            return None

    def register_daily_counter_reset_job(self, callback: Callable) -> Optional[Job]:
        """デイリーカウンタリセットジョブ登録（毎日00:00）"""
        try:
            job = self.scheduler.add_job(
                callback,
                name            = "daily_counter_reset",
                trigger         = CronTrigger(hour=0, minute=0),
                id              = "job_daily_counter_reset",
                replace_existing = True,
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
        return [
            {
                "id":            job.id,
                "name":          job.name,
                "trigger":       str(job.trigger),
                "next_run_time": (
                    getattr(job, "next_run_time", None).isoformat()
                    if getattr(job, "next_run_time", None)
                    else None
                ),
            }
            for job in self.scheduler.get_jobs()
        ]

    def remove_job(self, job_id: str) -> bool:
        """ジョブ削除"""
        try:
            self.scheduler.remove_job(job_id)
            self._registered_jobs.pop(job_id, None)
            logger.info(f"Removed job: {job_id}")
            return True
        except Exception as e:
            logger.error(f"Error removing job {job_id}: {e}")
            return False

    # ======================================================================
    # NSSM サービス登録スクリプト生成
    # ======================================================================

    def get_nssm_registration_script(self, app_path: str) -> str:
        """NSSM サービス登録用 PowerShell スクリプト生成"""
        service_name = f"SBAScheduler_{self.brain_name}".replace(" ", "_")

        # SBAConfig からルートパスを取得
        try:
            from ..config import SBAConfig
            cfg        = SBAConfig.load_env()
            app_dir    = str(cfg.project_root)
            output_dir = str(cfg.logs)
        except Exception:
            app_dir    = "C:/TH_Works/SBA"
            output_dir = "C:/TH_Works/SBA/logs"

        script = f"""
# SBA Scheduler - NSSM Windows Service Registration
# このスクリプトを PowerShell (管理者権限) で実行してください

$serviceName = "{service_name}"
$appPath = "{app_path}"
$nssmPath = "C:/tools/nssm/nssm.exe"

if (!(Test-Path $nssmPath)) {{
    Write-Error "NSSM not found at $nssmPath"
    exit 1
}}

if (!(Test-Path $appPath)) {{
    Write-Error "App not found at $appPath"
    exit 1
}}

if (Get-Service $serviceName -ErrorAction SilentlyContinue) {{
    Write-Host "Service $serviceName already exists. Removing..."
    & $nssmPath remove $serviceName confirm
}}

Write-Host "Installing service: $serviceName"
& $nssmPath install $serviceName "{app_path}"
& $nssmPath set $serviceName AppDirectory "{app_dir}"
& $nssmPath set $serviceName AppParameters "-m sba daemon"
& $nssmPath set $serviceName AppNoConsole 1
& $nssmPath set $serviceName OutputDir "{output_dir}"
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
            "brain_id":        self.brain_id,
            "brain_name":      self.brain_name,
            "is_running":      self._is_running,
            "scheduler_state": self.scheduler.state,
            "jobs":            self.get_job_list(),
        }

    def log_status_report(self) -> None:
        """ステータスレポートをログ出力"""
        report = self.get_status_report()
        logger.info("=== SBA Scheduler Status Report ===")
        logger.info(f"Brain: {report['brain_name']} ({report['brain_id']})")
        logger.info(f"Running: {report['is_running']}")
        logger.info(f"State: {report['scheduler_state']}")
        logger.info(f"Scheduled Jobs: {len(report['jobs'])}")
        for job_info in report["jobs"]:
            logger.info(
                f"  {job_info['id']}: {job_info['name']} "
                f"(next run: {job_info['next_run_time']})"
            )


# ======================================================================
# Singleton
# ======================================================================

_scheduler_instance: Optional[SBAScheduler] = None


def get_scheduler(
    brain_id: str,
    brain_name: str,
    jobstore_path: Optional[str] = None,
) -> SBAScheduler:
    """
    グローバルスケジューラインスタンスを取得（Singleton）。
    jobstore_path 省略時は SBAConfig から自動解決。
    """
    global _scheduler_instance

    if _scheduler_instance is None:
        _scheduler_instance = SBAScheduler(brain_id, brain_name, jobstore_path)

    return _scheduler_instance


def _load_active_brain_context() -> Dict[str, Any]:
    """[active] Brain の metadata / manifest を読み込む。"""
    from ..config import SBAConfig

    cfg = SBAConfig.load_env()
    metadata = json.loads((cfg.active / "metadata.json").read_text(encoding="utf-8-sig"))
    manifest = json.loads((cfg.active / "subskill_manifest.json").read_text(encoding="utf-8-sig"))

    brain_id = metadata.get("brain_id") or "active-brain"
    brain_name = metadata.get("name") or metadata.get("domain") or "Active Brain"
    domain = metadata.get("domain") or manifest.get("domain") or brain_name
    is_tech = any(
        token in str(domain).lower()
        for token in ("python", "tech", "code", "program", "開発")
    )

    return {
        "cfg": cfg,
        "metadata": metadata,
        "manifest": manifest,
        "brain_id": brain_id,
        "brain_name": brain_name,
        "domain": domain,
        "is_tech": is_tech,
    }


def build_learning_runtime(
    jobstore_path: Optional[str] = None,
) -> Dict[str, Any]:
    """daemon 用の学習ループ / 周辺コンポーネントを構築する。"""
    from ..cost.rate_limiter import get_rate_limiter
    from ..experiment.experiment_engine import ExperimentEngine, ExperimentType
    from ..inference.tier1 import Tier1Engine
    from ..learning.gap_detector import GapDetector
    from ..learning.knowledge_integrator import KnowledgeIntegrator
    from ..learning.learning_loop import LearningLoop
    from ..learning.resource_finder import ResourceFinder
    from ..learning.self_evaluator import SelfEvaluator
    from ..storage.knowledge_store import KnowledgeStore
    from ..subskill.classifier import SubSkillClassifier
    from ..utils.notifier import get_notifier

    ctx = _load_active_brain_context()
    cfg = ctx["cfg"]

    tier1 = Tier1Engine()
    knowledge_store = KnowledgeStore(str(cfg.active), ctx["brain_id"])
    rate_limiter = get_rate_limiter(str(cfg.data / "api_usage.db"))
    notifier = get_notifier(str(cfg.logs))

    gap_detector = GapDetector(
        brain_name=ctx["brain_name"],
        knowledge_store=knowledge_store,
        tier1_engine=tier1,
    )
    resource_finder = ResourceFinder(
        brain_name=ctx["brain_name"],
        knowledge_store=knowledge_store,
        api_usage_repo=rate_limiter.repo,
        rate_limiter=rate_limiter,
    )
    classifier = SubSkillClassifier(
        ctx["brain_name"],
        ctx["manifest"],
        tier1_engine=tier1,
    )
    integrator = KnowledgeIntegrator(
        knowledge_store=knowledge_store,
        graph_store=knowledge_store.graph_store,
        tier1_engine=tier1,
    )
    evaluator = SelfEvaluator(
        brain_name=ctx["brain_name"],
        brain_id=ctx["brain_id"],
        tier1_engine=tier1,
    )
    experiment_engine = ExperimentEngine(
        brain_id=ctx["brain_id"],
        brain_name=ctx["brain_name"],
        domain=ctx["domain"],
        active_brain_path=cfg.active,
        tier1=tier1,
    )

    learning_loop = LearningLoop(
        brain_id=ctx["brain_id"],
        brain_name=ctx["brain_name"],
        active_brain_path=cfg.active,
        gap_detector=gap_detector,
        resource_finder=resource_finder,
        classifier=classifier,
        integrator=integrator,
        evaluator=evaluator,
        experiment_engine=experiment_engine,
        knowledge_store=knowledge_store,
        notifier=notifier,
        rate_limiter=rate_limiter,
    )

    scheduler = get_scheduler(ctx["brain_id"], ctx["brain_name"], jobstore_path)

    return {
        "context": ctx,
        "scheduler": scheduler,
        "learning_loop": learning_loop,
        "rate_limiter": rate_limiter,
        "notifier": notifier,
        "experiment_type_light": ExperimentType.A,
        "experiment_type_medium": ExperimentType.B,
        "experiment_type_heavy": ExperimentType.C if ctx["is_tech"] else ExperimentType.D,
    }


def start_daemon(
    jobstore_path: Optional[str] = None,
    learning_interval_minutes: int = 120,
    heavyweight_hour: int = 3,
) -> None:
    """
    APScheduler + 学習ループ + 実験ジョブを起動し、常駐待機する。

    NSSM / `python -m sba daemon` から呼ばれるエントリポイント。
    """
    runtime = build_learning_runtime(jobstore_path)
    scheduler: SBAScheduler = runtime["scheduler"]
    learning_loop = runtime["learning_loop"]
    rate_limiter = runtime["rate_limiter"]
    notifier = runtime["notifier"]

    def run_async(label: str, async_fn: Callable[[], Any]) -> None:
        try:
            asyncio.run(async_fn())
        except Exception as e:
            logger.error("%s job failed: %s", label, e)
            try:
                notifier.error(f"{label} job failed: {e}")
            except Exception:
                pass

    scheduler.register_lightweight_experiment_job(
        lambda: run_async(
            "lightweight_experiment",
            lambda: learning_loop.run_targeted_experiment(runtime["experiment_type_light"]),
        )
    )
    scheduler.register_medium_experiment_job(
        lambda: run_async(
            "medium_experiment",
            lambda: learning_loop.run_targeted_experiment(runtime["experiment_type_medium"]),
        )
    )
    scheduler.register_heavyweight_experiment_job(
        lambda: run_async(
            "heavyweight_experiment",
            lambda: learning_loop.run_targeted_experiment(runtime["experiment_type_heavy"]),
        ),
        run_hour=heavyweight_hour,
    )
    scheduler.register_learning_loop_job(
        lambda: run_async("learning_loop", learning_loop.run_single_cycle),
        interval_minutes=learning_interval_minutes,
    )
    scheduler.register_daily_counter_reset_job(rate_limiter.reset_daily_counters_if_needed)

    if not scheduler.start():
        raise RuntimeError("Failed to start SBA daemon scheduler")

    scheduler.log_status_report()
    try:
        notifier.success("SBA daemon started")
    except Exception:
        pass

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("SBA daemon stopping by keyboard interrupt")
    finally:
        scheduler.stop()
