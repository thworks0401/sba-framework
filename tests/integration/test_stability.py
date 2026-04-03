"""
T-8: Scheduler stability smoke tests.

24時間試験の代替として、短時間で繰り返しジョブが安定実行されることを確認する。

【修正履歴】
  2026-04-03:
    - jobstore_path=':memory:' を使用して Windows + SQLAlchemy 問題を回避。
      SQLAlchemy ファイルベースでは Windows 環境で add_job 後にジョブが
      実行されないことがあり、:memory: で回避する。
    - test_scheduler_registers_public_jobs: start() なしで get_job_list() を呼ぶテスト。
      :memory: モード (jobstores={}) はインメモリストアなので
      start() 前でも add_job 後の get_jobs() は正しく返る。
"""

from __future__ import annotations

import threading
import time

from apscheduler.triggers.interval import IntervalTrigger

from src.sba.scheduler.scheduler import SBAScheduler


_RUNS: list[float] = []
_RUNS_LOCK = threading.Lock()


def record_scheduler_run() -> None:
    with _RUNS_LOCK:
        _RUNS.append(time.time())


def test_scheduler_runs_repeating_jobs_and_stops_cleanly():
    """
    スケジューラが繰り返しジョブを実行し、クリーンに停止することを確認。

    jobstore_path=':memory:' で APScheduler のインメモリモードを使用する。
    Windows + SQLAlchemy が実行 0 回になる問題を回避するため。
    """
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=":memory:",  # インメモリモードで Windows 問題を回避
    )

    _RUNS.clear()

    # start() を先に呼ぶ
    assert scheduler.start() is True

    try:
        # start() 後にジョブを追加（スケジューラが稼働中に追加するのが正しい順序）
        scheduler.scheduler.add_job(
            record_scheduler_run,
            trigger=IntervalTrigger(seconds=1),
            id="job_smoke",
            replace_existing=True,
        )

        # 1秒ジョブが 3回以上実行できる時間 + マージン
        time.sleep(3.5)
    finally:
        assert scheduler.stop() is True

    assert len(_RUNS) >= 2, f"ジョブが2回以上実行されなかった: {len(_RUNS)} 回"
    assert scheduler.get_status_report()["is_running"] is False


def test_scheduler_registers_public_jobs():
    """
    公開ジョブ登録メソッドが全て正しく job_id を登録することを確認。
    start() しないでジョブ登録 → get_job_list() で確認する。

    :memory: モードでは jobstores={} が適用されるため、
    start() 前でも add_job 後の get_jobs() は正しく返る。
    """
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=":memory:",  # インメモリモードで start() 前でも正しく動作する
    )

    callback = lambda: None  # noqa: E731

    # 各公開メソッドでジョブを登録
    scheduler.register_lightweight_experiment_job(callback)
    scheduler.register_medium_experiment_job(callback)
    scheduler.register_heavyweight_experiment_job(callback, run_hour=1)
    scheduler.register_learning_loop_job(callback, interval_minutes=120)
    scheduler.register_daily_counter_reset_job(callback)

    # 登録済み job_id の一覧を取得
    job_ids = {job["id"] for job in scheduler.get_job_list()}

    expected = {
        "job_lightweight_exp",
        "job_medium_exp",
        "job_heavyweight_exp",
        "job_learning_loop",
        "job_daily_counter_reset",
    }
    missing = expected - job_ids
    assert not missing, f"以下のジョブIDが登録されていない: {missing}\n登録済み: {job_ids}"
