"""启动清理与矩阵重试的真实 SQL 验证（P1-4）。

仅在 ``GFNA_TEST_DB=1`` 且 gfna_test 已迁移时运行；外层事务回滚，不写测试库。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.models.meta import IngestJob
from app.db.models.strategy import BacktestRun, StgRun
from app.services.task_recovery import RESTART_ERROR, recover_stale_tasks
from app.strategy.grid import requeue_failed_cells

pytestmark = pytest.mark.asyncio


def _job(status: str, started_at: datetime) -> IngestJob:
    return IngestJob(
        job_id=str(uuid4()), task="snapshot", dataset="stock_daily",
        status=status, started_at=started_at,
    )


def _run(status: str, created_at: datetime) -> StgRun:
    return StgRun(
        run_id=str(uuid4()), algo_code="a3", trade_date=date(2026, 11, 6),
        params={}, status=status, created_at=created_at,
    )


def _backtest(status: str, created_at: datetime, *, grid_id: str | None = None,
              error: str | None = None) -> BacktestRun:
    return BacktestRun(
        bt_id=str(uuid4()), algo_code="a3",
        start_date=date(2026, 1, 1), end_date=date(2026, 6, 1),
        params={"rebalance_days": 20, "max_positions": 20,
                "strategy": {"limit": 20, "min_score": 6.0}},
        status=status, created_at=created_at, grid_id=grid_id, error=error,
        grid_cell={"rebalance_days": 20, "max_positions": 20,
                   "start_date": "2026-01-01", "end_date": "2026-06-01"},
    )


async def test_recover_stale_tasks_updates_real_rows(db_session):
    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=3)
    fresh = now - timedelta(minutes=5)

    stale_job, fresh_job = _job("running", stale), _job("running", fresh)
    stale_run, fresh_run = _run("running", stale), _run("running", fresh)
    stale_bt = _backtest("queued", stale, error="原错误")
    fresh_bt = _backtest("running", fresh)
    old_success = _backtest("success", stale)
    db_session.add_all([stale_job, fresh_job, stale_run, fresh_run,
                        stale_bt, fresh_bt, old_success])
    await db_session.flush()

    counts = await recover_stale_tasks(db_session, now=now)
    await db_session.flush()

    assert counts["ingest_job"] >= 1
    assert counts["stg_run"] >= 1
    assert counts["backtest_run"] >= 1
    assert stale_job.status == "failed" and stale_job.error == RESTART_ERROR
    assert stale_run.status == "failed" and stale_run.error == RESTART_ERROR
    assert stale_bt.status == "failed" and stale_bt.error == RESTART_ERROR
    assert fresh_job.status == "running" and fresh_job.error is None
    assert fresh_run.status == "running" and fresh_run.error is None
    assert fresh_bt.status == "running" and fresh_bt.error is None
    assert old_success.status == "success"


async def test_requeue_failed_cells_updates_real_rows(db_session):
    grid_id = str(uuid4())
    failed = _backtest("failed", datetime.now(timezone.utc), grid_id=grid_id,
                       error="旧失败")
    success = _backtest("success", datetime.now(timezone.utc), grid_id=grid_id)
    db_session.add_all([failed, success])
    await db_session.flush()

    result = await requeue_failed_cells(db_session, grid_id)

    assert [item["bt_id"] for item in result["pending"]] == [failed.bt_id]
    assert result["pending"][0]["strategy"]["min_score"] == 6.0
    assert failed.status == "queued" and failed.error is None
    assert success.status == "success"
