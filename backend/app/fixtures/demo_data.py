"""
离线演示数据（确定性生成，无网络依赖）
=====================================
用途：
  1. `ENV=demo` 时通过 `POST /api/v1/admin/seed-demo` 灌入，用于演示/验收；
  2. CI 与本地测试在没有行情源时验证"指标 → 因子 → 选股 → 回测"全链路。

与 V3 的 `seed-demo` 的区别：
  - 不再用 `hash()` 造价格（结果不可复现），改用固定种子的 numpy RNG
  - 生成的是**完整时间序列**（约 1 年），而不是单日快照 —— 这样技术指标才有意义
  - 数据完全落在真实表里，后续 features/scores/strategy 与真实数据走同一条代码路径
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import TIMEZONE
from app.core.constants import BENCHMARK_INDEX, board_of, exchange_of
from app.core.logging import get_logger
from app.core.soe import classify_ownership
from app.db.models import (
    DimStock, FactMoneyflow, FactSectorDaily, FactStockDaily, MarginTrading, MarketIndex,
)

logger = get_logger(__name__)

SEED = 20261021
DAYS = 250

# 12 个演示板块（含成分股代码前缀规则，便于生成成分表）
SECTORS = [
    ("BK1036", "半导体", "industry"),
    ("BK1037", "新能源", "industry"),
    ("BK1038", "医药", "industry"),
    ("BK1039", "银行", "industry"),
    ("BK1040", "白酒", "industry"),
    ("BK1041", "军工", "industry"),
    ("BK1042", "人工智能", "concept"),
    ("BK1043", "汽车", "industry"),
    ("BK1044", "地产", "industry"),
    ("BK1045", "有色金属", "industry"),
]

STOCK_POOL = [
    ("600519", "贵州茅台"), ("000858", "五粮液"), ("601318", "中国平安"),
    ("600036", "招商银行"), ("601988", "中国银行"), ("600028", "中国石化"),
    ("601857", "中国石油"), ("601088", "中国神华"), ("600900", "长江电力"),
    ("601668", "中国建筑"), ("601390", "中国中铁"), ("601766", "中国中车"),
    ("600760", "中航沈飞"), ("000768", "中航西飞"), ("600893", "航发动力"),
    ("688981", "中芯国际"), ("603986", "兆易创新"), ("002371", "北方华创"),
    ("300750", "宁德时代"), ("002594", "比亚迪"), ("601127", "赛力斯"),
    ("300274", "阳光电源"), ("002460", "赣锋锂业"), ("600438", "通威股份"),
    ("600276", "恒瑞医药"), ("300760", "迈瑞医疗"), ("603259", "药明康德"),
    ("000333", "美的集团"), ("000651", "格力电器"), ("600690", "海尔智家"),
    ("600030", "中信证券"), ("601166", "兴业银行"), ("600000", "浦发银行"),
    ("601899", "紫金矿业"), ("600111", "北方稀土"), ("000831", "中国稀土"),
    ("600048", "保利发展"), ("000002", "万科A"), ("600383", "金地集团"),
    ("002415", "海康威视"), ("000063", "中兴通讯"), ("600050", "中国联通"),
]


def _trading_days(n: int, end: date | None = None) -> list[date]:
    """生成最近 n 个"工作日"（演示数据不依赖真实日历）。"""
    end = end or date.today()
    days: list[date] = []
    cursor = end
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


async def seed_demo_data(db: AsyncSession, days: int = DAYS) -> dict:
    """灌入演示数据（先清空同源数据，保证幂等）。"""
    rng = np.random.default_rng(SEED)
    trade_days = _trading_days(days)
    start = trade_days[0]

    from sqlalchemy import delete
    await db.execute(delete(FactStockDaily).where(FactStockDaily.trade_date >= start))
    await db.execute(delete(FactSectorDaily).where(FactSectorDaily.trade_date >= start))
    await db.execute(delete(MarketIndex).where(MarketIndex.trade_date >= start))
    await db.execute(delete(MarginTrading).where(MarginTrading.trade_date >= start))
    await db.execute(delete(DimStock))
    await db.commit()

    # ---- 维度 ----
    for code, name in STOCK_POOL:
        db.add(DimStock(
            code=code,
            exchange=exchange_of(code),
            board=board_of(code),
            name=name,
            industry_l1=SECTORS[hash(code) % len(SECTORS)][1],
            industry_l2=None,
            ownership=classify_ownership(code, name),
            list_date=date(2015, 1, 5),
            is_st=False,
            status=1,
        ))
    await db.commit()

    # ---- 个股日线：几何随机游走 + 行业共性因子 ----
    sector_drift = {s[0]: rng.normal(0.0002, 0.0006) for s in SECTORS}
    for idx, (code, _name) in enumerate(STOCK_POOL):
        base = float(rng.uniform(8, 200))
        sector_code = SECTORS[idx % len(SECTORS)][0]
        drift = sector_drift[sector_code] + rng.normal(0, 0.0002)
        vol = float(rng.uniform(0.012, 0.035))
        prices = [base]
        for _ in range(len(trade_days) - 1):
            prices.append(max(0.5, prices[-1] * (1 + drift + rng.normal(0, vol))))

        rows = []
        prev_close = None
        for d, close in zip(trade_days, prices):
            close = round(close, 3)
            open_ = round(close * (1 + rng.normal(0, 0.004)), 3)
            high = round(max(open_, close) * (1 + abs(rng.normal(0, 0.006))), 3)
            low = round(min(open_, close) * (1 - abs(rng.normal(0, 0.006))), 3)
            pct = round((close / prev_close - 1) * 100, 3) if prev_close else 0.0
            volume = int(abs(rng.normal(1.2e7, 3e6)))
            amount = round(volume * close, 2)
            adj = round(1 + (len(trade_days) - trade_days.index(d)) * 0.0002, 6)

            rows.append(dict(
                code=code, trade_date=d,
                open=open_, high=high, low=low, close=close,
                pre_close=prev_close if prev_close else close,
                pct_chg=pct,
                amplitude=round((high - low) / close * 100, 3),
                volume=volume, amount=amount,
                turnover_rate=round(float(abs(rng.normal(2.5, 1.0))), 4),
                adj_factor=adj,
                market_cap=round(close * 1e9, 2), float_cap=round(close * 7e8, 2),
                pe_ttm=round(float(abs(rng.normal(25, 12))) + 3, 4),
                pb=round(float(abs(rng.normal(3, 1.5))) + 0.5, 4),
                ps_ttm=round(float(abs(rng.normal(5, 2))) + 0.5, 4),
                dv_ttm=round(float(abs(rng.normal(2.5, 1.5))), 4),
                is_limit_up=False, is_limit_down=False, is_suspended=False,
                src="demo",
            ))
            prev_close = close

        await db.execute(pg_insert(FactStockDaily).values(rows).on_conflict_do_nothing())
    await db.commit()

    # ---- 板块日线（成分股均值 + 噪声） ----
    for sector_code, sector_name, sector_type in SECTORS:
        members = [STOCK_POOL[i][0] for i in range(len(STOCK_POOL))
                   if SECTORS[i % len(SECTORS)][0] == sector_code]
        drift = sector_drift[sector_code]
        level = float(rng.uniform(800, 2000))
        rows = []
        prev_close = None
        for d in trade_days:
            level = max(100.0, level * (1 + drift + rng.normal(0, 0.008)))
            close = round(level, 3)
            prev = prev_close or close
            rows.append(dict(
                sector_code=sector_code, sector_name=sector_name, sector_type=sector_type,
                trade_date=d, open=round(close * 0.998, 3), high=round(close * 1.01, 3),
                low=round(close * 0.99, 3), close=close, pre_close=prev,
                pct_chg=round((close / prev - 1) * 100, 3) if prev_close else 0.0,
                amplitude=round(abs(rng.normal(1.5, 0.5)), 3),
                volume=int(abs(rng.normal(5e8, 1e8))),
                amount=round(abs(rng.normal(5e10, 1e10)), 2),
                turnover_rate=round(abs(rng.normal(2.0, 0.8)), 4),
                up_count=int(len(members) * 0.6), down_count=int(len(members) * 0.3),
                leading_stock=None, leading_stock_pct=None, src="demo",
            ))
            prev_close = close
        await db.execute(pg_insert(FactSectorDaily).values(rows).on_conflict_do_nothing())
    await db.commit()

    # ---- 指数 ----
    for index_code, index_name in [
        ("sh000001", "上证指数"), ("sz399001", "深证成指"),
        ("sz399006", "创业板指"), ("sh000688", "科创50"), (BENCHMARK_INDEX, "沪深300"),
    ]:
        level = 3200.0 if index_code.startswith("sh0000") else 12000.0
        rows = []
        prev = None
        for d in trade_days:
            level = max(100.0, level * (1 + rng.normal(0.0003, 0.009)))
            close = round(level, 3)
            rows.append(dict(
                index_code=index_code, index_name=index_name, trade_date=d,
                open=round(close * 0.999, 3), high=round(close * 1.008, 3),
                low=round(close * 0.992, 3), close=close,
                pre_close=prev or close,
                pct_chg=round((close / (prev or close) - 1) * 100, 3),
                volume=int(abs(rng.normal(3e10, 5e9))), amount=None, src="demo",
            ))
            prev = close
        await db.execute(pg_insert(MarketIndex).values(rows).on_conflict_do_nothing())
    await db.commit()

    # ---- 两融（北向留空，模拟 2024-08 之后的状态）----
    balance = 15000.0
    for d in trade_days:
        change = float(rng.normal(0, 60))
        balance = max(5000.0, balance + change)
        db.add(MarginTrading(trade_date=d, margin_balance=round(balance, 2),
                             short_balance=150.0, margin_change=round(change, 2),
                             northward_net=None, src="demo"))
    await db.commit()

    # ---- 个股资金流（A2 策略输入；主力净流入用价格方向 + 噪声模拟）----
    mf_rows = []
    for code, _name in STOCK_POOL:
        ema_flow = 0.0
        for d in trade_days:
            daily_pct = float(rng.normal(0.3, 1.5))
            main = round(daily_pct * 1e7 * float(rng.uniform(0.5, 2.0)), 2)
            ema_flow = main if ema_flow == 0 else ema_flow * 0.6 + main * 0.4
            mf_rows.append(dict(
                code=code, trade_date=d,
                main_net_t0=main,
                main_net_t1=round(main * float(rng.uniform(0.5, 1.2)), 2),
                main_net_t2=round(main * float(rng.uniform(0.5, 1.2)), 2),
                main_net_t3=round(main * float(rng.uniform(0.5, 1.2)), 2),
                main_net_t4=round(main * float(rng.uniform(0.5, 1.2)), 2),
                main_net_5d=round(ema_flow * 5, 2),
                north_hold_ratio=None, src="demo",
            ))
    for chunk in range(0, len(mf_rows), 1000):
        await db.execute(pg_insert(FactMoneyflow)
                         .values(mf_rows[chunk:chunk + 1000])
                         .on_conflict_do_nothing())
    await db.commit()

    # ---- 板块成分（SCD2，供龙头识别与板块详情使用）----
    from app.db.models import DimSectorMember
    await db.execute(delete(DimSectorMember))
    for sector_code, sector_name, sector_type in SECTORS:
        members = [STOCK_POOL[i][0] for i in range(len(STOCK_POOL))
                   if SECTORS[i % len(SECTORS)][0] == sector_code]
        for member in members:
            db.add(DimSectorMember(
                sector_code=sector_code, stock_code=member,
                effective_from=trade_days[0],
                sector_name=sector_name, sector_type=sector_type,
            ))
    await db.commit()

    from app.pipeline import mark_success
    for dataset, rows_count in (
        ("stock_universe", len(STOCK_POOL)),
        ("stock_daily", len(STOCK_POOL) * len(trade_days)),
        ("sector_daily", len(SECTORS) * len(trade_days)),
        ("index_daily", 5 * len(trade_days)),
        ("margin", len(trade_days)),
    ):
        await mark_success(db, dataset, rows=rows_count, coverage=1.0,
                           trade_date=trade_days[-1], detail={"source": "demo"})

    logger.info("演示数据灌入完成：%d 只标的 × %d 个交易日", len(STOCK_POOL), len(trade_days))
    return {
        "status": "ok",
        "mode": "demo",
        "stocks": len(STOCK_POOL),
        "sectors": len(SECTORS),
        "days": len(trade_days),
        "start": trade_days[0].isoformat(),
        "end": trade_days[-1].isoformat(),
        "hint": "接着执行 POST /api/v1/admin/ingest/features、/scores、/sector_scores、/algos",
    }


__all__ = ["seed_demo_data", "STOCK_POOL", "SECTORS"]
