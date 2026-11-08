"""P2-1：板块 partial 名单元数据（不连接数据库）。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1.market import _query_partial_membership, _query_sectors

TRADE_DATE = date(2026, 11, 7)


class _Result:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _FakeSession:
    def __init__(self, results) -> None:
        self._results = list(results)
        self.statements: list[object] = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._results.pop(0))


def _daily(code: str, *, member_count: int | None, market_cap=None):
    return SimpleNamespace(
        sector_code=code,
        sector_name=f"{code} 名称",
        sector_type="concept",
        close=Decimal("100"),
        pct_chg=Decimal("1.2"),
        amount=None,
        turnover_rate=None,
        up_count=None,
        down_count=None,
        leading_stock=None,
        leading_stock_pct=None,
        market_cap=market_cap,
        float_cap=None,
        main_net_inflow=None,
        rs_20d=None,
        member_count=member_count,
    )


@pytest.mark.asyncio
async def test_partial_membership_returns_only_sample_lists():
    session = _FakeSession([[
        ("THS_PARTIAL", 100, 100),
        ("THS_FULL", 80, 0),
        ("THS_EMPTY", 0, 0),
    ]])

    result = await _query_partial_membership(session, TRADE_DATE)

    assert result == {"THS_PARTIAL": 100}


@pytest.mark.asyncio
async def test_partial_membership_skips_query_for_empty_codes():
    session = _FakeSession([])

    assert await _query_partial_membership(session, TRADE_DATE, []) == {}
    assert session.statements == []


@pytest.mark.asyncio
async def test_query_sectors_exposes_partial_metadata():
    session = _FakeSession([
        [
            _daily("THS_PARTIAL", member_count=None),
            _daily("THS_FULL", member_count=80, market_cap=Decimal("1e11")),
        ],
        [],
        [("THS_PARTIAL", 100, 100), ("THS_FULL", 80, 0)],
    ])

    items = await _query_sectors(session, TRADE_DATE)
    by_code = {item["sector_code"]: item for item in items}

    assert by_code["THS_PARTIAL"]["is_partial"] is True
    assert by_code["THS_PARTIAL"]["partial_member_count"] == 100
    assert by_code["THS_PARTIAL"]["member_count"] is None
    assert by_code["THS_FULL"]["is_partial"] is False
    assert by_code["THS_FULL"]["partial_member_count"] is None
    assert by_code["THS_FULL"]["member_count"] == 80
