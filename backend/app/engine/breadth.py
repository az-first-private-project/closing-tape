"""
市场宽度（公共模块，V6 T3）
==========================
日报（`report_input.py`）与市场温度计（`market_temperature.py`）共用同一份口径。
本模块由 `report_input._load_breadth()` **原样搬移**而来：日报的调用不传
`exclude_suspended`，输出逐字段保持不变；温度计显式剔除停牌股。

口径：
  - 只统计 `close` 非空、`pct_chg` 可解析的个股；
  - `limit_up` / `limit_down` 直接取 `fact_stock_daily.is_limit_up/is_limit_down`，
    不自行按涨跌幅重算（涨跌停幅度已在采集层按主板/创业/科创/北交所区分）；
  - `median_pct` 为当日涨跌幅中位数（百分数）。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import POOL_BOARDS, STATUS_NORMAL
from app.db.models.dim import DimStock
from app.db.models.fact import FactStockDaily


def _to_float(value: Any) -> float | None:
    """宽松转 float：None/NaN/Inf/Decimal/str → float 或 None。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(result) or np.isinf(result):
        return None
    return result


async def load_breadth(
    session: AsyncSession,
    trade_date: date,
    *,
    exclude_suspended: bool = False,
) -> dict:
    """全市场涨跌家数 / 涨跌停家数 / 涨跌幅中位数。

    参数:
        exclude_suspended: 是否剔除停牌股。日报保持历史口径（False）；
                           市场温度计传 True，避免停牌股的 0% 涨跌幅稀释宽度。
    """
    stmt = select(
        FactStockDaily.pct_chg,
        FactStockDaily.is_limit_up,
        FactStockDaily.is_limit_down,
    ).where(
        FactStockDaily.trade_date == trade_date,
        FactStockDaily.close.isnot(None),
        FactStockDaily.code.in_(
            select(DimStock.code).where(
                DimStock.status == STATUS_NORMAL,
                DimStock.board.in_(POOL_BOARDS),
            )
        ),
    )
    if exclude_suspended:
        stmt = stmt.where(FactStockDaily.is_suspended.is_(False))

    rows = (await session.execute(stmt)).all()

    up = down = flat = limit_up = limit_down = 0
    pct_changes: list[float] = []
    for pct_chg, is_limit_up, is_limit_down in rows:
        value = _to_float(pct_chg)
        if value is None:
            continue
        pct_changes.append(value)
        if value > 0:
            up += 1
        elif value < 0:
            down += 1
        else:
            flat += 1
        if bool(is_limit_up):
            limit_up += 1
        if bool(is_limit_down):
            limit_down += 1

    median_pct = round(float(np.median(pct_changes)), 3) if pct_changes else None
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "median_pct": median_pct,
        "total": len(pct_changes),
    }


__all__ = ["load_breadth"]
