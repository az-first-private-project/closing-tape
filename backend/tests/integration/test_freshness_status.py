"""不变量 2/3：定向回补不覆盖新鲜度；失败与质量不合格不得记成功。"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app import pipeline
from app.db.models import MetaDataQuality
from app.ingest.base import FetchResult

pytestmark = pytest.mark.asyncio

OLD_SUCCESS = datetime(2020, 1, 1, tzinfo=timezone.utc)


async def _seed_quality(db, *, status: str = "ok") -> None:
    db.add(
        MetaDataQuality(
            dataset="stock_daily",
            display_name="个股日线",
            last_success_at=OLD_SUCCESS,
            last_attempt_at=OLD_SUCCESS,
            last_trade_date=date(2020, 1, 1),
            rows=123,
            status=status,
        )
    )
    await db.flush()


async def _quality(db) -> MetaDataQuality:
    # mark_success 内部 commit（savepoint），expire_on_commit=False 会留下身份映射旧值
    db.expire_all()
    result = await db.execute(
        select(MetaDataQuality).where(
            MetaDataQuality.dataset == "stock_daily"
        )
    )
    return result.scalar_one()


async def test_scoped_backfill_keeps_freshness_until_full_market_run(
    db_session, monkeypatch
) -> None:
    await _seed_quality(db_session)

    async def fake_dispatch(task, **kwargs):
        return FetchResult(rows=2, total=2)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)

    scoped = await pipeline.run_task(
        "history_fast", codes="600519", db=db_session
    )
    row = await _quality(db_session)
    assert scoped["status"] == "success"
    assert row.last_success_at == OLD_SUCCESS
    assert row.rows == 123

    full = await pipeline.run_task("history_fast", db=db_session)
    row = await _quality(db_session)
    assert full["status"] == "success"
    assert row.last_success_at > OLD_SUCCESS
    assert row.rows == 2


async def test_quality_failed_keeps_last_success_and_marks_partial(
    db_session, monkeypatch
) -> None:
    await _seed_quality(db_session)

    async def fake_dispatch(task, **kwargs):
        return FetchResult(
            rows=4999,
            total=5000,
            failed=1,
            quality_status="quality_failed",
            detail={"failed_codes": ["600000"]},
        )

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)

    out = await pipeline.run_task("history_fast", db=db_session)
    row = await _quality(db_session)

    assert out["status"] == "partial"
    assert out["quality_status"] == "quality_failed"
    assert row.status == "quality_failed"
    assert row.last_success_at == OLD_SUCCESS
    assert "600000" in (row.error or "")


async def test_non_ok_quality_without_failed_codes_is_not_written_as_success(
    db_session, monkeypatch
) -> None:
    await _seed_quality(db_session)

    async def fake_dispatch(task, **kwargs):
        return FetchResult(rows=10, total=10, quality_status="quality_failed")

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)

    out = await pipeline.run_task("history_fast", db=db_session)
    row = await _quality(db_session)

    assert out["status"] == "partial"
    assert row.status == "ok"
    assert row.last_success_at == OLD_SUCCESS
