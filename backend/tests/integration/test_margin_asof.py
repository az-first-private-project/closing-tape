"""P0-1：两融数据 T+1 披露，评分与日报不得读取同日行（未来函数回归）。

使用远期交易日隔离测试库已有数据；外层事务回滚，不落库。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.db.models import MarginTrading
from app.engine.report_input import _load_margin
from app.engine.sector_analysis import _load_market_sentiment_inputs

pytestmark = pytest.mark.asyncio

# 早于全部真实两融数据（2010 年起），避免测试库已有数据干扰
PREV = date(1990, 1, 4)
REPORT_DATE = date(1990, 1, 5)


async def _seed(db) -> None:
    db.add(
        MarginTrading(
            trade_date=PREV,
            margin_balance=22643.55,
            short_balance=100.0,
            margin_change=13.29,
            northward_net=5.0,
            src="test",
        )
    )
    db.add(
        MarginTrading(
            trade_date=REPORT_DATE,
            margin_balance=22709.77,
            short_balance=110.0,
            margin_change=66.22,
            northward_net=6.0,
            src="test",
        )
    )
    await db.flush()


async def test_sector_sentiment_ignores_same_day_margin(db_session):
    await _seed(db_session)

    inputs = await _load_market_sentiment_inputs(db_session, REPORT_DATE)

    assert inputs["as_of"] == PREV
    assert inputs["margin_data"]["trade_date"] == PREV.isoformat()
    assert inputs["margin_data"]["margin_balance"] == pytest.approx(22643.55)


async def test_report_input_ignores_same_day_margin(db_session):
    await _seed(db_session)

    margin = await _load_margin(db_session, REPORT_DATE)

    assert margin["available"] is True
    assert margin["trade_date"] == PREV.isoformat()
    assert margin["margin_balance"] == pytest.approx(22643.55)
    assert margin["margin_change"] == pytest.approx(13.29)


async def test_no_earlier_margin_returns_unavailable(db_session):
    db_session.add(
        MarginTrading(
            trade_date=REPORT_DATE,
            margin_balance=22709.77,
            short_balance=110.0,
            margin_change=66.22,
            src="test",
        )
    )
    await db_session.flush()

    inputs = await _load_market_sentiment_inputs(db_session, REPORT_DATE)
    margin = await _load_margin(db_session, REPORT_DATE)

    assert inputs["as_of"] is None
    assert inputs["margin_data"] is None
    assert margin == {"available": False, "reason": "no_margin_data"}
