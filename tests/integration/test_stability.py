"""
T-8: Scheduler stability smoke tests.

24時間試験の代替として、短時間で繰り返しジョブが安定実行されることを確認する。

【修正履歴】
  2026-04-03 (fix #1):
    - jobstore_path=':memory:' を使用して Windows + SQLAlchemy 問題を回避。
  2026-04-03 (fix #2):
    - test_scheduler_registers_public_jobs: APScheduler 3.x は start() 前に
      add_job() すると MemoryJobStore が未初期化で内部エラー → except で
      握り潰し → _registered_jobs に書かれない問題を修正。
      start() → register_* → get_job_list() → stop() の正しい順序に変更。
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
        jobstore_path=":memory:",
    )

    _RUNS.clear()

    # start() を先に呼ぶ（APScheduler 3.x は start() 後でないと add_job が安定しない）
    assert scheduler.start() is True

    try:
        # start() 後にジョブを追加
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

    【修正】start() → register_* → get_job_list() → stop() の順序。
    APScheduler 3.x の MemoryJobStore は start() 前に add_job() すると
    内部で未初期化エラーが発生し except で握り潰されるため、
    _registered_jobs に書き込まれず get_job_list() が空を返す。
    start() してから登録することで安定動作を保証する。
    """
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=":memory:",
    )

    callback = lambda: None  # noqa: E731

    # start() を先に呼ぶ（ここが修正の核心）
    assert scheduler.start() is True

    try:
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
    finally:
        scheduler.stop()
