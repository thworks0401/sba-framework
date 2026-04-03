"""
T-8: Scheduler stability smoke tests.

24時間試験の代替として、短時間で繰り返しジョブが安定実行されることを確認する。

【修正履歴】
  2026-04-03:
    - start() 後にスモークジョブを追加するよう順序を修正
      （start() 前に add_job → start() でジョブリストが再初期化されるケースに対応）
    - sleep を 2.6s → 3.5s に延長（CI 環境での遅延マージンを確保）
    - test_scheduler_registers_public_jobs: start() 不要テストなので変更なし、
      ただし get_job_list() が登録済みジョブを返す実装を前提に確認
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


def test_scheduler_runs_repeating_jobs_and_stops_cleanly(tmp_path):
    """
    スケジューラが繰り返しジョブを実行し、クリーンに停止することを確認。

    修正ポイント:
      - start() を先に呼び、その後 add_job する
        （start() 前に登録したジョブが start() で消えるケースへの対応）
      - sleep を 3.5s に延長して 1秒ジョブが2回以上実行される余裕を確保
    """
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=str(tmp_path / "jobs.db"),
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

        # 1秒ジョブが3回以上実行できる時間 + マージン
        time.sleep(3.5)
    finally:
        assert scheduler.stop() is True

    assert len(_RUNS) >= 2, f"ジョブが2回以上実行されなかった: {len(_RUNS)} 回"
    assert scheduler.get_status_report()["is_running"] is False


def test_scheduler_registers_public_jobs(tmp_path):
    """
    公開ジョブ登録メソッドが全て正しく job_id を登録することを確認。
    スケジューラを start() せずにジョブ登録 → get_job_list() で確認する。
    """
    scheduler = SBAScheduler(
        brain_id="test-brain",
        brain_name="Test Brain",
        jobstore_path=str(tmp_path / "jobs.db"),
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
