"""
T-8: Scheduler stability smoke tests.

24時間試験の代替として、短時間で繰り返しジョブが安定実行されることを確認する。
"""

from __future__ import annotations

import threading
import time

from apscheduler.triggers.interval import IntervalTrigger

from src.sba.scheduler.scheduler import SBAScheduler


_RUNS = []
_RUNS_LOCK = threading.Lock()


def record_scheduler_run():
    with _RUNS_LOCK:
        _RUNS.append(time.time())


def test_scheduler_runs_repeating_jobs_and_stops_cleanly(tmp_path):
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=str(tmp_path / "jobs.db"),
    )

    _RUNS.clear()

    scheduler.scheduler.add_job(
        record_scheduler_run,
        trigger=IntervalTrigger(seconds=1),
        id="job_smoke",
        replace_existing=True,
    )

    assert scheduler.start() is True
    try:
        time.sleep(2.6)
    finally:
        assert scheduler.stop() is True

    assert len(_RUNS) >= 2
    assert scheduler.get_status_report()["is_running"] is False


def test_scheduler_registers_public_jobs(tmp_path):
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=str(tmp_path / "jobs.db"),
    )

    callback = lambda: None

    scheduler.register_lightweight_experiment_job(callback)
    scheduler.register_medium_experiment_job(callback)
    scheduler.register_heavyweight_experiment_job(callback, run_hour=1)
    scheduler.register_learning_loop_job(callback, interval_minutes=120)
    scheduler.register_daily_counter_reset_job(callback)

    job_ids = {job["id"] for job in scheduler.get_job_list()}
    assert {
        "job_lightweight_exp",
        "job_medium_exp",
        "job_heavyweight_exp",
        "job_learning_loop",
        "job_daily_counter_reset",
    }.issubset(job_ids)
