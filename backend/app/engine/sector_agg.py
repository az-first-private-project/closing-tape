"""板块聚合指标（V6 T4）
========================
`fact_sector_daily` 原本只有行情快照，treemap 需要的「面积=市值、资金流、相对强度」
没有任何现成列。本模块按**当日 SCD2 名单**把成分股数据聚合回板块：

- `market_cap` / `float_cap`：成分股 `fact_stock_daily` 市值合计（元）
- `main_net_inflow`：成分股 `fact_moneyflow.main_net_t0` 合计（元）
- `rs_20d`：板块 20 交易日收益 − 沪深300 同窗口收益（百分点）
- `member_count`：当日名单成分数

口径与边界：
- 名单必须带 SCD2 时间条件（`effective_from <= d AND (effective_to IS NULL OR > d)`），
  不能直接读当前名单，否则历史回看会把后来纳入的股票算进过去。
- `is_partial=true` 的名单只覆盖 THS 前后各 5 页，只用于龙头，**不参与**市值/资金流/
  家数聚合（member_count 置 NULL），避免用 100 只样本冒充整个板块。
- 成分数 < 3 时市值/资金流置 NULL（小样本噪声大）；缺失一律 NULL，**不补 0**。
- `rs_20d` 要求板块与沪深300 在**同一组 21 个交易日**上都有收盘价，否则 NULL。
- 只 UPDATE 当日已有的板块行，不凭空插入没有行情快照的板块。
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BENCHMARK_INDEX
from app.core.logging import get_logger
from app.db.models.fact import FactSectorDaily, MarketIndex
from app.engine._db import commit_or_flush, session_ctx, to_jsonable

logger = get_logger(__name__)

#: 成分数低于该值不写市值/资金流（避免 1–2 只股票放大噪声）
MIN_MEMBERS = 3
#: 21 个收盘价 = 20 个交易日的收益区间
RS_WINDOW = 21


async def _membership_aggregate(session: AsyncSession, trade_date: date) -> list[dict]:
    """按 SCD2 名单聚合成分股市值与资金流。"""
    result = await session.execute(text("""
        WITH members AS (
            SELECT DISTINCT m.sector_code, m.stock_code, m.is_partial
            FROM dim_sector_member m
            WHERE m.effective_from <= :d
              AND (m.effective_to IS NULL OR m.effective_to > :d)
        )
        SELECT m.sector_code,
               count(*) FILTER (WHERE NOT m.is_partial) AS member_count,
               count(s.market_cap) FILTER (WHERE NOT m.is_partial) AS cap_n,
               sum(s.market_cap) FILTER (WHERE NOT m.is_partial) AS market_cap,
               sum(s.float_cap) FILTER (WHERE NOT m.is_partial) AS float_cap,
               sum(f.main_net_t0) FILTER (WHERE NOT m.is_partial) AS main_net_inflow,
               bool_or(m.is_partial) AS has_partial
        FROM members m
        LEFT JOIN fact_stock_daily s
               ON s.code = m.stock_code AND s.trade_date = :d
        LEFT JOIN fact_moneyflow f
               ON f.code = m.stock_code AND f.trade_date = :d
        GROUP BY m.sector_code
    """), {"d": trade_date})
    return [dict(row) for row in result.mappings().all()]


async def _rs_lookup(session: AsyncSession, trade_date: date) -> dict[str, float]:
    """板块 20 日相对沪深300 超额收益（百分点）。

    先取全市场最近 21 个有板块行情的交易日，再要求每个板块在这 21 天
    都有收盘价；沪深300 在窗口首尾都要有值。任一条件不满足 → 不进入结果集。
    """
    dates = list((await session.execute(
        select(FactSectorDaily.trade_date)
        .where(FactSectorDaily.trade_date <= trade_date)
        .distinct()
        .order_by(FactSectorDaily.trade_date.desc())
        .limit(RS_WINDOW)
    )).scalars().all())
    if len(dates) < RS_WINDOW:
        return {}
    start, end = dates[-1], dates[0]

    closes: dict[str, dict[date, float]] = {}
    for code, day, close in (await session.execute(
        select(FactSectorDaily.sector_code, FactSectorDaily.trade_date,
               FactSectorDaily.close)
        .where(FactSectorDaily.trade_date.in_(dates))
        .where(FactSectorDaily.close.isnot(None))
    )).all():
        closes.setdefault(code, {})[day] = float(close)

    bench = {
        day: float(close)
        for day, close in (await session.execute(
            select(MarketIndex.trade_date, MarketIndex.close)
            .where(MarketIndex.index_code == BENCHMARK_INDEX)
            .where(MarketIndex.trade_date.in_((start, end)))
            .where(MarketIndex.close.isnot(None))
        )).all()
    }
    if start not in bench or end not in bench or bench[start] == 0:
        logger.warning("沪深300 窗口缺失，rs_20d 全部为空：%s ~ %s", start, end)
        return {}

    bench_return = (bench[end] / bench[start] - 1.0) * 100.0
    values: dict[str, float] = {}
    for code, series in closes.items():
        if len(series) < RS_WINDOW:
            continue
        first, last = series.get(start), series.get(end)
        if not first or not last:
            continue
        sector_return = (last / first - 1.0) * 100.0
        values[code] = round(sector_return - bench_return, 3)
    return values


async def compute_sector_metrics(session: AsyncSession,
                                 trade_date: date) -> list[dict[str, Any]]:
    """计算当日全部板块的聚合指标（不写库）。"""
    codes = list((await session.execute(
        select(FactSectorDaily.sector_code)
        .where(FactSectorDaily.trade_date == trade_date)
    )).scalars().all())
    if not codes:
        return []

    aggregates = {row["sector_code"]: row
                  for row in await _membership_aggregate(session, trade_date)}
    rs_map = await _rs_lookup(session, trade_date)

    metrics: list[dict[str, Any]] = []
    for code in codes:
        row = aggregates.get(code) or {}
        has_partial = bool(row.get("has_partial"))
        member_count = int(row.get("member_count") or 0)
        enough = not has_partial and member_count >= MIN_MEMBERS
        metrics.append({
            "sector_code": code,
            "member_count": None if has_partial else member_count,
            "market_cap": row.get("market_cap") if enough else None,
            "float_cap": row.get("float_cap") if enough else None,
            "main_net_inflow": row.get("main_net_inflow") if enough else None,
            "rs_20d": rs_map.get(code),
        })
    return metrics


async def save_sector_metrics(session: AsyncSession, trade_date: date,
                              metrics: list[dict[str, Any]]) -> int:
    """把聚合结果写回 `fact_sector_daily`（只更新当日已有行）。"""
    if not metrics:
        return 0
    payload = [
        {
            "sector_code": item["sector_code"],
            "market_cap": to_jsonable(item.get("market_cap")),
            "float_cap": to_jsonable(item.get("float_cap")),
            "main_net_inflow": to_jsonable(item.get("main_net_inflow")),
            "rs_20d": to_jsonable(item.get("rs_20d")),
            "member_count": item.get("member_count"),
        }
        for item in metrics
    ]
    result = await session.execute(text("""
        WITH v AS (
            SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                sector_code text,
                market_cap numeric,
                float_cap numeric,
                main_net_inflow numeric,
                rs_20d numeric,
                member_count integer
            )
        )
        UPDATE fact_sector_daily f
           SET market_cap = v.market_cap,
               float_cap = v.float_cap,
               main_net_inflow = v.main_net_inflow,
               rs_20d = v.rs_20d,
               member_count = v.member_count
          FROM v
         WHERE f.trade_date = :d AND f.sector_code = v.sector_code
    """), {"rows": json.dumps(payload, ensure_ascii=False), "d": trade_date})
    return int(result.rowcount or 0)


async def aggregate_sector_metrics(session: AsyncSession, trade_date: date) -> dict:
    """计算并写入当日板块聚合指标，返回统计摘要。"""
    metrics = await compute_sector_metrics(session, trade_date)
    rows = await save_sector_metrics(session, trade_date, metrics)
    summary = {
        "rows": rows,
        "trade_date": trade_date.isoformat(),
        "with_cap": sum(1 for m in metrics if m["market_cap"] is not None),
        "with_inflow": sum(1 for m in metrics if m["main_net_inflow"] is not None),
        "with_rs": sum(1 for m in metrics if m["rs_20d"] is not None),
        "sectors": len(metrics),
    }
    logger.info(
        "板块聚合完成 %s: 更新 %d/%d（市值 %d、资金流 %d、rs20 %d）",
        trade_date, rows, len(metrics), summary["with_cap"],
        summary["with_inflow"], summary["with_rs"],
    )
    return summary


async def compute_and_save_sector_metrics(trade_date: date,
                                          db: AsyncSession | None = None) -> dict:
    """供管线 / 回填脚本调用：支持外部共享会话。"""
    async with session_ctx(db) as session:
        summary = await aggregate_sector_metrics(session, trade_date)
        await commit_or_flush(session, external=db is not None)
    return summary


__all__ = [
    "MIN_MEMBERS",
    "RS_WINDOW",
    "aggregate_sector_metrics",
    "compute_and_save_sector_metrics",
    "compute_sector_metrics",
    "save_sector_metrics",
]
