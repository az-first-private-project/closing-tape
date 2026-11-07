"""W0-3：数据不变量脚本的检测与放行。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import (
    BacktestRun,
    DimSectorMember,
    FactFeatureDaily,
    FactMoneyflow,
    FactStockDaily,
    FactStockScoreDaily,
    IngestJob,
    MetaDataQuality,
    StgRun,
    StgSignal,
    TradeCalendar,
)
from app.quality.invariants import collect_invariants

pytestmark = pytest.mark.asyncio

DAYS = [
    date(2026, 11, 2),
    date(2026, 11, 3),
    date(2026, 11, 4),
    date(2026, 11, 5),
    date(2026, 11, 6),
]
LATEST = DAYS[-1]
CODES = ["000001", "600519"]
NOW = datetime.now(timezone.utc)


async def _seed_clean(db) -> None:
    for day in DAYS:
        db.add(TradeCalendar(cal_date=day, is_open=True, source="test"))
        for code in CODES:
            db.add(FactStockDaily(code=code, trade_date=day, close=10.0, src="test"))
            db.add(FactFeatureDaily(code=code, trade_date=day))
            db.add(FactStockScoreDaily(code=code, trade_date=day))
            db.add(FactMoneyflow(code=code, trade_date=day, src="test"))
    for dataset, trade_date in (
        ("stock_daily", None),
        ("stock_features", LATEST),
        ("stock_scores", LATEST),
        ("moneyflow", None),
    ):
        db.add(
            MetaDataQuality(
                dataset=dataset,
                last_success_at=NOW,
                last_attempt_at=NOW,
                last_trade_date=trade_date,
                rows=len(CODES),
                status="ok",
            )
        )
    db.add(
        DimSectorMember(
            sector_code="THS_TEST_CLEAN",
            stock_code="600519",
            effective_from=date(2021, 1, 1),
            effective_to=None,
            sector_name="测试板块",
            sector_type="concept",
            is_partial=False,
        )
    )
    await db.flush()


async def test_clean_database_has_no_violation(db_session) -> None:
    await _seed_clean(db_session)

    report = await collect_invariants(db_session)

    assert report.ok, report.as_dict()
    assert report.violations == []
    checks = {item.name: item for item in report.checks}
    assert checks["scd2_overlap"].count == 0
    assert checks["open_partial_sectors"].count == 0
    assert checks["stock_daily_recent_coverage"].count == 0
    assert checks["moneyflow_recent_coverage"].count == 0
    assert checks["moneyflow_freshness"].count == 0


async def test_overlaps_stale_jobs_and_failed_outputs_are_reported(
    db_session
) -> None:
    await _seed_clean(db_session)
    db_session.add_all(
        [
            DimSectorMember(
                sector_code="THS_TEST_BAD",
                stock_code="600519",
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 6, 1),
                sector_name="坏区间",
                sector_type="concept",
                is_partial=False,
            ),
            DimSectorMember(
                sector_code="THS_TEST_BAD",
                stock_code="600519",
                effective_from=date(2026, 5, 1),
                effective_to=None,
                sector_name="坏区间",
                sector_type="concept",
                is_partial=False,
            ),
            IngestJob(
                job_id="stale-job",
                task="backfill_daily",
                dataset="stock_daily",
                status="running",
                started_at=NOW - timedelta(hours=3),
            ),
            StgRun(
                run_id="failed-run",
                algo_code="A1",
                trade_date=LATEST,
                params={},
                status="failed",
            ),
            StgSignal(run_id="failed-run", code="600519", rank=1),
            BacktestRun(
                bt_id="failed-bt",
                algo_code="A1",
                start_date=DAYS[0],
                end_date=LATEST,
                metrics={"total_return": 0.1},
                status="failed",
            ),
        ]
    )
    await db_session.flush()

    report = await collect_invariants(db_session)
    names = {item.name for item in report.violations}

    assert not report.ok
    assert {
        "scd2_overlap",
        "stale_running_jobs",
        "failed_run_with_signals",
        "failed_backtest_with_metrics",
    } <= names


async def test_empty_database_still_reports(db_session) -> None:
    report = await collect_invariants(db_session)

    assert not report.ok
    assert [item.name for item in report.violations] == ["stock_daily_empty"]
