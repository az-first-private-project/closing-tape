"""不变量 1：板块成分 SCD2 区间完整性与 partial 取代。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import DimSectorMember, FactSectorDaily, TradeCalendar
from app.ingest import service
from app.ingest.base import FetchResult

pytestmark = pytest.mark.asyncio

TRADE_DATE = date(2026, 11, 6)
SECTOR = "THS_TEST_SCD2"
VALID_FROM = date(2021, 1, 1)


class _FakeJob:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def begin(self, *args, **kwargs) -> None:
        return None

    async def update(self, *args, **kwargs) -> None:
        return None

    async def finish(self, *args, **kwargs) -> None:
        return None


class _FakeChain:
    def __init__(self, codes: list[str]) -> None:
        self.codes = codes

    async def fetch_sector_members(
        self, sector_code, sector_name, sector_type, timeout=None
    ) -> FetchResult:
        frame = pd.DataFrame({"stock_code": self.codes})
        return FetchResult(
            rows=len(frame), total=len(frame), detail={"data": frame}
        )


@asynccontextmanager
async def _fake_lock(task: str):
    yield True


async def _seed(db, members: list[tuple[str, bool]]) -> None:
    db.add(TradeCalendar(cal_date=TRADE_DATE, is_open=True, source="test"))
    db.add(
        FactSectorDaily(
            sector_code=SECTOR,
            trade_date=TRADE_DATE,
            sector_name="测试板块",
            sector_type="concept",
            src="test",
        )
    )
    for stock_code, is_partial in members:
        db.add(
            DimSectorMember(
                sector_code=SECTOR,
                stock_code=stock_code,
                effective_from=VALID_FROM,
                effective_to=None,
                sector_name="测试板块",
                sector_type="concept",
                is_partial=is_partial,
            )
        )
    await db.flush()


def _patch_provider(monkeypatch, codes: list[str]) -> None:
    monkeypatch.setattr(service, "_advisory_lock", _fake_lock)
    monkeypatch.setattr(service, "_JobTracker", _FakeJob)
    monkeypatch.setattr(service, "get_provider_chain", lambda: _FakeChain(codes))


async def _rows(db) -> list[DimSectorMember]:
    result = await db.execute(
        select(DimSectorMember)
        .where(DimSectorMember.sector_code == SECTOR)
        .order_by(DimSectorMember.stock_code, DimSectorMember.effective_from)
    )
    return list(result.scalars().all())


def _assert_no_overlap(rows: list[DimSectorMember]) -> None:
    by_stock: dict[str, list[DimSectorMember]] = {}
    for row in rows:
        by_stock.setdefault(row.stock_code, []).append(row)
    for intervals in by_stock.values():
        intervals.sort(key=lambda item: item.effective_from)
        for row in intervals:
            assert row.effective_to is None or row.effective_to > row.effective_from
        for previous, current in zip(intervals, intervals[1:]):
            assert previous.effective_to is not None
            assert previous.effective_to <= current.effective_from


async def test_complete_list_replaces_partial_and_closes_removed(
    db_session, monkeypatch
) -> None:
    await _seed(
        db_session,
        [("600519", False), ("000001", True), ("000002", True)],
    )
    _patch_provider(monkeypatch, ["600519", "000002", "000003"])

    result = await service.collect_sector_members(TRADE_DATE, db=db_session)

    assert result.failed == 0
    assert result.detail["added"] == 2
    rows = await _rows(db_session)
    open_rows = [row for row in rows if row.effective_to is None]
    assert {
        (row.stock_code, row.is_partial) for row in open_rows
    } == {
        ("600519", False),
        ("000002", False),
        ("000003", False),
    }
    removed = [row for row in rows if row.stock_code == "000001"]
    assert removed and all(row.effective_to == TRADE_DATE for row in removed)
    replaced = [row for row in rows if row.stock_code == "000002"]
    assert len(replaced) == 2
    assert sum(1 for row in replaced if row.effective_to is None) == 1
    assert any(
        row.effective_to == TRADE_DATE and row.is_partial for row in replaced
    )
    _assert_no_overlap(rows)


async def test_empty_member_list_keeps_open_intervals(
    db_session, monkeypatch
) -> None:
    await _seed(db_session, [("600519", False), ("000001", False)])
    _patch_provider(monkeypatch, [])

    result = await service.collect_sector_members(TRADE_DATE, db=db_session)

    assert result.failed == 1
    assert result.quality_status == "stale"
    assert result.detail["added"] == 0
    assert result.detail["closed"] == 0
    rows = await _rows(db_session)
    assert len(rows) == 2
    assert all(row.effective_to is None for row in rows)
