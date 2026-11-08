"""启动清理僵尸任务（P1-4）纯逻辑单测。

不连数据库：用假 session 返回活跃行，验证只重置超过阈值的行、耗时与错误文案。
真实 SQL 更新由 ``tests/integration/test_task_recovery.py`` 覆盖。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from app.db.models.meta import IngestJob
from app.db.models.strategy import BacktestRun, StgRun
from app.services.task_recovery import (
    RESTART_ERROR, STALE_AFTER, _elapsed_ms, is_stale, recover_stale_tasks,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=3)
FRESH = NOW - timedelta(minutes=10)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """按调用顺序返回三批活跃行（ingest_job / stg_run / backtest_run）。"""

    def __init__(self, *batches):
        self._batches = list(batches)
        self.committed = False

    async def execute(self, _stmt):
        return _FakeResult(self._batches.pop(0))

    async def commit(self):
        self.committed = True


def _job(job_id: str, status: str, started_at: datetime) -> IngestJob:
    return IngestJob(
        job_id=job_id, task="snapshot", dataset="stock_daily",
        status=status, started_at=started_at,
    )


def _run(run_id: str, status: str, created_at: datetime) -> StgRun:
    return StgRun(
        run_id=run_id, algo_code="a3", trade_date=date(2026, 11, 6),
        params={}, status=status, created_at=created_at,
    )


def _backtest(bt_id: str, status: str, created_at: datetime) -> BacktestRun:
    return BacktestRun(
        bt_id=bt_id, algo_code="a3", start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 1), status=status, created_at=created_at,
    )


def test_is_stale_only_marks_beyond_threshold():
    assert is_stale(STALE, now=NOW) is True
    assert is_stale(FRESH, now=NOW) is False
    assert is_stale(NOW - STALE_AFTER, now=NOW) is False
    assert is_stale(None, now=NOW) is False


def test_is_stale_handles_naive_and_offset_timezones():
    # naive 视为 UTC；+08:00 的 17:00 == UTC 09:00，都是 3 小时前
    assert is_stale(datetime(2026, 11, 8, 9, 0), now=NOW) is True
    assert is_stale(
        datetime(2026, 11, 8, 17, 0, tzinfo=timezone(timedelta(hours=8))),
        now=NOW,
    ) is True


def test_elapsed_ms_handles_naive_and_offset():
    assert _elapsed_ms(STALE, NOW) == 3 * 3600 * 1000
    assert _elapsed_ms(
        datetime(2026, 11, 8, 17, 0, tzinfo=timezone(timedelta(hours=8))), NOW
    ) == 3 * 3600 * 1000
    assert _elapsed_ms(None, NOW) is None


def test_recover_marks_only_stale_rows_and_keeps_fresh():
    stale_job, fresh_job = _job("j-old", "running", STALE), _job("j-new", "running", FRESH)
    stale_run, fresh_run = _run("r-old", "running", STALE), _run("r-new", "running", FRESH)
    stale_bt = _backtest("bt-old", "queued", STALE)
    fresh_bt = _backtest("bt-new", "running", FRESH)
    session = _FakeSession(
        [stale_job, fresh_job], [stale_run, fresh_run], [stale_bt, fresh_bt],
    )

    counts = asyncio.run(recover_stale_tasks(session, now=NOW))

    assert counts == {"ingest_job": 1, "stg_run": 1, "backtest_run": 1}
    assert stale_job.status == "failed"
    assert stale_job.error == RESTART_ERROR
    assert stale_job.finished_at == NOW
    assert stale_job.elapsed_ms == 3 * 3600 * 1000
    assert fresh_job.status == "running" and fresh_job.error is None

    assert stale_run.status == "failed" and stale_run.error == RESTART_ERROR
    assert fresh_run.status == "running" and fresh_run.error is None

    assert stale_bt.status == "failed" and stale_bt.error == RESTART_ERROR
    assert fresh_bt.status == "running" and fresh_bt.error is None
