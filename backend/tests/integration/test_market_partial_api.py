"""P2-1：板块列表与详情的 partial 名单元数据（真实 _test 数据库）。

使用远期交易日隔离数据，避免与测试库已有行情冲突；外层事务回滚，不落库。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.api.v1.market import _query_sectors, get_sector_detail
from app.db.models import DimSectorMember, FactSectorDaily

pytestmark = pytest.mark.asyncio

TRADE_DATE = date(2099, 1, 5)
PARTIAL = "THS_P2_1_PARTIAL"
FULL = "THS_P2_1_FULL"
VALID_FROM = date(2021, 1, 1)


async def _seed(db) -> None:
    for code in (PARTIAL, FULL):
        db.add(
            FactSectorDaily(
                sector_code=code,
                trade_date=TRADE_DATE,
                sector_name=code,
                sector_type="concept",
                src="test",
            )
        )
    for index in range(3):
        db.add(
            DimSectorMember(
                sector_code=PARTIAL,
                stock_code=f"P0000{index + 1}",
                effective_from=VALID_FROM,
                effective_to=None,
                sector_name=PARTIAL,
                sector_type="concept",
                is_partial=True,
            )
        )
    for index in range(2):
        db.add(
            DimSectorMember(
                sector_code=FULL,
                stock_code=f"F0000{index + 1}",
                effective_from=VALID_FROM,
                effective_to=None,
                sector_name=FULL,
                sector_type="concept",
                is_partial=False,
            )
        )
    await db.flush()


async def test_sector_detail_marks_pure_partial(db_session):
    await _seed(db_session)

    detail = await get_sector_detail(PARTIAL, TRADE_DATE, db_session)

    assert detail["is_partial"] is True
    assert detail["partial_member_count"] == 3
    assert len(detail["members"]) == 3

    full_detail = await get_sector_detail(FULL, TRADE_DATE, db_session)

    assert full_detail["is_partial"] is False
    assert full_detail["partial_member_count"] is None
    assert len(full_detail["members"]) == 2


async def test_sector_list_exposes_partial_metadata(db_session):
    await _seed(db_session)

    items = await _query_sectors(db_session, TRADE_DATE)
    by_code = {item["sector_code"]: item for item in items}

    assert by_code[PARTIAL]["is_partial"] is True
    assert by_code[PARTIAL]["partial_member_count"] == 3
    assert by_code[PARTIAL]["member_count"] is None
    assert by_code[FULL]["is_partial"] is False
    assert by_code[FULL]["partial_member_count"] is None
