"""
tests/integration/conftest.py

integration テスト専用の conftest。

【目的】
  tests/conftest.py はテスト高速化のために apscheduler / qdrant_client などを
  sys.modules に MagicMock として差し込んでいる。
  しかし test_stability.py は本物の APScheduler が動かないと成立しないテストのため、
  ここで apscheduler 関連の stub を sys.modules から除去して本物に差し替える。

【修正対象】
  - BackgroundScheduler が MagicMock() になっていたため
    scheduler.start() がスレッドを立ち上げず 0 回実行になっていた。
  - add_job() が MagicMock を返すため job.id が
    '<MagicMock name="mock().add_job().id">' になっていた。

【方針】
  apscheduler の全サブモジュールを sys.modules から削除し、
  Python に本物のパッケージを再インポートさせる。
  この conftest は tests/integration/ 以下のテストにのみ適用される。
"""

from __future__ import annotations

import importlib
import sys

import pytest


# ======================================================================
# apscheduler stub を sys.modules から除去して本物に差し替える
# ======================================================================

_APSCHEDULER_KEYS = [
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.schedulers.background",
    "apscheduler.jobstores",
    "apscheduler.jobstores.sqlalchemy",
    "apscheduler.triggers",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "apscheduler.job",
]

# SBA scheduler モジュールも一緒にリロードが必要なため列挙
_SBA_SCHEDULER_KEYS = [
    "src.sba.scheduler",
    "src.sba.scheduler.scheduler",
]


def _restore_real_apscheduler() -> None:
    """
    sys.modules から apscheduler stub を全て削除して
    Python に本物のパッケージを再インポートさせる。
    SBA scheduler モジュールもアンロードして、
    次の import 時に本物の apscheduler で再構築させる。
    """
    # apscheduler stub を削除
    for key in _APSCHEDULER_KEYS:
        sys.modules.pop(key, None)

    # SBA scheduler モジュールをアンロード（次の import で再ロードさせる）
    for key in _SBA_SCHEDULER_KEYS:
        sys.modules.pop(key, None)

    # 本物の apscheduler を再インポート（存在確認）
    try:
        importlib.import_module("apscheduler.schedulers.background")
        importlib.import_module("apscheduler.triggers.cron")
        importlib.import_module("apscheduler.triggers.interval")
    except ImportError as e:
        pytest.skip(f"apscheduler が未インストールのためスキップ: {e}")


# モジュールロード時に即時実行（fixture より前に適用が必要）
_restore_real_apscheduler()
