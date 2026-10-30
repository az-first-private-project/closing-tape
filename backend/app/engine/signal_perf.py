"""信号前瞻收益统计（V6 T5）。

⚠️ **仅供事后统计，禁止被 strategy 层 import 为特征。**
本模块使用未来已经发生的数据评估信号质量；`app/strategy/*.py` 的前视静态扫描
会把这里的能力误当成特征来源，因此刻意放在 `app/engine/`，且全文件不得出现
`.shift(-`。

口径（任务书 T5，改动必须同步更新任务书与 API 契约）：
  - T 日收盘出信号 → T+1 交易日开盘买入；
  - 买入价 = T+1 `open × adj_factor`；
  - H 日收益 = 自 T+1 起第 H 个交易日 `close × adj_factor` / 买入价 − 1；
  - 超额 = 个股 H 日收益 − 沪深300 同窗口收益（指数不复权，用 `market_indices.close`，
    起点同为 T+1 收盘、终点同为第 H 个交易日收盘）；
  - 窗口是否成熟以**本地行情最后一天**为界：exit_date > data_end 一律 `mature=false`，
    避免交易日历里的未来日期把"还在路上"的信号算成熟；
  - 停牌/一字涨停的 T+1 记为 `fillable=false`，收益仍保留为参考值，但统计时
    整体排除，否则天梯会高估。

每个策略每天可能有多条 run（不同参数），本表按 `(algo_code, signal_date, code)`
唯一，因此只取该日**最近一次成功且非回测**的 run；这是唯一能在无 run_id 的
表结构下保证幂等的口径。
"""
from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BENCHMARK_INDEX
from app.core.logging import get_logger
from app.db.models.dim import DimStock
from app.db.models.fact import FactStockDaily, MarketIndex
from app.db.models.meta import TradeCalendar
from app.db.models.strategy import StgRun, StgSignal, StgSignalPerf
from app.engine._db import commit_or_flush, session_ctx

logger = get_logger(__name__)

HORIZONS: tuple[int, ...] = (5, 10, 20)
LADDER_WINDOWS: tuple[int, ...] = (20, 60, 120)
MIN_SAMPLE = 30
SPARK_POINTS = 20
_BATCH_SIZE = 5_000


# ============================================================
# 纯计算：单条信号的 T+1 买入与前瞻收益
# ============================================================

@dataclass(frozen=True)
class SignalBar:
    """计算前瞻收益所需的最小日线切片。"""

    open: float | None
    close: float | None
    adj_factor: float | None
    is_limit_up: bool = False
    is_suspended: bool = False


@dataclass(frozen=True)
class ForwardResult:
    entry_date: date | None
    entry_price: float | None
    returns: dict[int, float | None]
    excess: dict[int, float | None]
    mature: dict[int, bool]
    fillable: bool


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _adj(value: Any) -> float:
    """复权因子缺失/非法时退化为 1.0（与回测引擎 `_adj_factor` 一致）。"""
    parsed = _num(value)
    return parsed if parsed is not None and parsed > 0 else 1.0


def forward_window(
    signal_date: date,
    calendar: Sequence[date],
    horizons: Sequence[int] = HORIZONS,
) -> tuple[date | None, dict[int, date | None]]:
    """返回 T+1 与各 H 的出场日。

    `calendar` 必须是**升序、无重复**的开市日列表（DB 层保证）。
    交易日历不够长时，对应出场日为 None（窗口未走完）。
    """
    index = bisect_right(calendar, signal_date)
    if index >= len(calendar):
        return None, {horizon: None for horizon in horizons}
    entry_date = calendar[index]
    exits = {
        horizon: calendar[index + horizon] if index + horizon < len(calendar) else None
        for horizon in horizons
    }
    return entry_date, exits


def compute_forward_result(
    signal_date: date,
    calendar: Sequence[date],
    bars: Mapping[date, SignalBar],
    benchmark_closes: Mapping[date, float | None],
    *,
    data_end: date | None = None,
    horizons: Sequence[int] = HORIZONS,
) -> ForwardResult:
    """计算一条信号的入场与 5/10/20 日前瞻收益。

    - `data_end` 是本地行情最后一天；出场日晚于它时该窗口记为未成熟，
      即使交易日历已经写到了未来；
    - `fillable=false` 只表示 T+1 不可成交，收益仍按开盘价计算供排查；
      统计聚合必须排除这些行。
    """
    entry_date, exit_dates = forward_window(signal_date, calendar, horizons)
    returns: dict[int, float | None] = {horizon: None for horizon in horizons}
    excess: dict[int, float | None] = {horizon: None for horizon in horizons}
    mature: dict[int, bool] = {horizon: False for horizon in horizons}

    if entry_date is None:
        return ForwardResult(None, None, returns, excess, mature, False)

    entry_bar = bars.get(entry_date)
    entry_open = _num(entry_bar.open) if entry_bar else None
    entry_price = (
        round(entry_open * _adj(entry_bar.adj_factor), 6)
        if entry_bar is not None and entry_open is not None and entry_open > 0
        else None
    )
    fillable = bool(
        entry_bar is not None
        and entry_price is not None
        and not entry_bar.is_limit_up
        and not entry_bar.is_suspended
    )

    bench_entry = _num(benchmark_closes.get(entry_date))
    for horizon in horizons:
        exit_date = exit_dates.get(horizon)
        if exit_date is None:
            continue
        if data_end is not None and exit_date > data_end:
            continue
        mature[horizon] = True

        exit_bar = bars.get(exit_date)
        exit_close = _num(exit_bar.close) if exit_bar else None
        if (
            entry_price is not None
            and exit_close is not None
            and exit_close > 0
        ):
            exit_price = exit_close * _adj(exit_bar.adj_factor)
            returns[horizon] = round((exit_price / entry_price - 1.0) * 100.0, 4)

        bench_exit = _num(benchmark_closes.get(exit_date))
        if (
            returns[horizon] is not None
            and bench_entry is not None
            and bench_entry > 0
            and bench_exit is not None
        ):
            bench_return = (bench_exit / bench_entry - 1.0) * 100.0
            excess[horizon] = round(returns[horizon] - bench_return, 4)

    return ForwardResult(entry_date, entry_price, returns, excess, mature, fillable)


# ============================================================
# 天梯聚合（纯函数）
# ============================================================

@dataclass(frozen=True)
class PerfRow:
    algo_code: str
    signal_date: date
    code: str
    fillable: bool
    mature_5: bool
    mature_10: bool
    mature_20: bool
    ret_5: float | None
    ret_10: float | None
    ret_20: float | None
    excess_5: float | None
    excess_10: float | None
    excess_20: float | None


@dataclass(frozen=True)
class LadderAlgo:
    algo_code: str
    algo_name: str


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _horizon_stats(rows: Sequence[PerfRow], horizon: int) -> dict[str, Any]:
    """单策略、单 horizon 的统计。四种状态互斥：valid / immature / unfillable / no_data。"""
    mature_key = f"mature_{horizon}"
    ret_key = f"ret_{horizon}"
    excess_key = f"excess_{horizon}"

    mature_rows = [row for row in rows if getattr(row, mature_key)]
    immature = len(rows) - len(mature_rows)
    valid = [
        row for row in mature_rows
        if row.fillable and getattr(row, ret_key) is not None
    ]
    unfillable = sum(1 for row in mature_rows if not row.fillable)
    no_data = len(mature_rows) - len(valid) - unfillable

    returns = [float(getattr(row, ret_key)) for row in valid]
    excesses = [
        float(getattr(row, excess_key))
        for row in valid
        if getattr(row, excess_key) is not None
    ]

    by_date: dict[date, list[float]] = {}
    for row in valid:
        by_date.setdefault(row.signal_date, []).append(float(getattr(row, ret_key)))
    spark = [
        {
            "trade_date": signal_date.isoformat(),
            "win_rate": round(sum(1 for value in values if value > 0) / len(values), 3),
            "samples": len(values),
        }
        for signal_date, values in sorted(by_date.items())[-SPARK_POINTS:]
    ]

    win_rate = (
        round(sum(1 for value in returns if value > 0) / len(returns), 3)
        if returns else None
    )
    return {
        "sample_signals": len(returns),
        "sample_stocks": len({row.code for row in valid}),
        "avg_ret": _round(statistics.fmean(returns), 2) if returns else None,
        "median_ret": _round(statistics.median(returns), 2) if returns else None,
        "win_rate": win_rate,
        "avg_excess": _round(statistics.fmean(excesses), 2) if excesses else None,
        "best": _round(max(returns), 2) if returns else None,
        "worst": _round(min(returns), 2) if returns else None,
        "immature": immature,
        "unfillable": unfillable,
        "no_data": no_data,
        "total_signals": len(rows),
        "low_sample": len(returns) < MIN_SAMPLE,
        "spark": spark,
    }


def _sort_key(item: dict[str, Any]) -> tuple:
    """按所选 horizon 的平均超额降序；无样本的排最后，保证空策略不冒充第一。"""
    avg_excess = item.get("avg_excess")
    win_rate = item.get("win_rate")
    return (
        avg_excess is None,
        -(avg_excess if avg_excess is not None else 0.0),
        -(win_rate if win_rate is not None else 0.0),
        -int(item.get("sample_signals") or 0),
        item["algo_code"],
    )


def build_ladder(
    rows: Iterable[PerfRow],
    algos: Sequence[LadderAlgo],
    *,
    horizon: int = 20,
) -> list[dict[str, Any]]:
    """把前瞻收益明细聚合成策略天梯（含 5/10/20 三组统计）。"""
    grouped: dict[str, list[PerfRow]] = {}
    for row in rows:
        grouped.setdefault(row.algo_code, []).append(row)

    items: list[dict[str, Any]] = []
    for algo in algos:
        algo_rows = grouped.get(algo.algo_code, [])
        horizons = {
            str(value): _horizon_stats(algo_rows, value) for value in HORIZONS
        }
        selected = horizons[str(horizon)]
        items.append({
            "algo_code": algo.algo_code,
            "algo_name": algo.algo_name,
            **selected,
            "horizons": horizons,
        })

    items.sort(key=_sort_key)
    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items


# ============================================================
# 数据库读写
# ============================================================

async def _load_signals(
    session: AsyncSession,
    start: date,
    end: date,
    algos: Sequence[str],
) -> list[dict[str, Any]]:
    """取每个 (algo, signal_date) 最近一次成功非回测 run 的全部信号。"""
    latest = (
        select(StgRun.algo_code, StgRun.trade_date, StgRun.run_id)
        .where(
            StgRun.is_backtest.is_(False),
            StgRun.status == "success",
            StgRun.trade_date >= start,
            StgRun.trade_date <= end,
            StgRun.algo_code.in_(algos),
        )
        .distinct(StgRun.algo_code, StgRun.trade_date)
        .order_by(
            StgRun.algo_code,
            StgRun.trade_date,
            StgRun.created_at.desc().nullslast(),
            StgRun.run_id.desc(),
        )
        .subquery()
    )
    rows = (await session.execute(
        select(
            latest.c.algo_code,
            latest.c.trade_date,
            StgSignal.code,
            StgSignal.rank,
            StgSignal.score,
        )
        .select_from(latest)
        .join(StgSignal, StgSignal.run_id == latest.c.run_id)
        .order_by(latest.c.algo_code, latest.c.trade_date, StgSignal.code)
    )).all()
    return [
        {
            "algo_code": algo_code,
            "signal_date": signal_date,
            "code": code,
            "rank": rank,
            "score": score,
        }
        for algo_code, signal_date, code, rank, score in rows
    ]


async def _load_calendar(
    session: AsyncSession, start: date, end: date
) -> list[date]:
    rows = (await session.execute(
        select(TradeCalendar.cal_date)
        .where(
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date >= start,
            TradeCalendar.cal_date <= end,
        )
        .order_by(TradeCalendar.cal_date)
    )).scalars().all()
    return list(rows)


async def _load_data_end(session: AsyncSession) -> date | None:
    return (await session.execute(
        select(func.max(FactStockDaily.trade_date))
    )).scalar_one_or_none()


async def _load_bars(
    session: AsyncSession,
    pairs: set[tuple[str, date]],
) -> dict[str, dict[date, SignalBar]]:
    """只取需要的 (code, date)，避免把 5 年全市场日线读进内存。"""
    result: dict[str, dict[date, SignalBar]] = {}
    ordered = sorted(pairs)
    for offset in range(0, len(ordered), _BATCH_SIZE):
        batch = ordered[offset:offset + _BATCH_SIZE]
        rows = (await session.execute(
            select(
                FactStockDaily.code,
                FactStockDaily.trade_date,
                FactStockDaily.open,
                FactStockDaily.close,
                FactStockDaily.adj_factor,
                FactStockDaily.is_limit_up,
                FactStockDaily.is_suspended,
            ).where(
                tuple_(FactStockDaily.code, FactStockDaily.trade_date).in_(batch)
            )
        )).all()
        for code, trade_date, open_, close, adj, limit_up, suspended in rows:
            result.setdefault(code, {})[trade_date] = SignalBar(
                open=_num(open_),
                close=_num(close),
                adj_factor=_num(adj),
                is_limit_up=bool(limit_up),
                is_suspended=bool(suspended),
            )
    return result


async def _load_benchmark(
    session: AsyncSession, start: date, end: date
) -> dict[date, float | None]:
    rows = (await session.execute(
        select(MarketIndex.trade_date, MarketIndex.close)
        .where(
            MarketIndex.index_code == BENCHMARK_INDEX,
            MarketIndex.trade_date >= start,
            MarketIndex.trade_date <= end,
        )
    )).all()
    return {trade_date: _num(close) for trade_date, close in rows}


async def _load_industries(
    session: AsyncSession, codes: Sequence[str]
) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    ordered = sorted(set(codes))
    for offset in range(0, len(ordered), _BATCH_SIZE):
        batch = ordered[offset:offset + _BATCH_SIZE]
        rows = (await session.execute(
            select(DimStock.code, DimStock.industry_l1)
            .where(DimStock.code.in_(batch))
        )).all()
        result.update({code: industry for code, industry in rows})
    return result


async def recompute_signal_perf(
    start: date,
    end: date,
    *,
    algos: Sequence[str],
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """重算 `signal_date ∈ [start, end]` 的信号前瞻收益（幂等）。

    先算后删再插：同一区间重复执行结果完全一致；区间内已不存在的信号行
    会被清掉，避免策略重跑后留下幽灵记录。
    """
    if start > end:
        start, end = end, start
    algo_list = sorted({str(code) for code in algos if code})
    if not algo_list:
        return {"rows": 0, "detail": {"reason": "no active algos"}}

    async with session_ctx(db) as session:
        signals = await _load_signals(session, start, end, algo_list)
        data_end = await _load_data_end(session)

        now = datetime.now(timezone.utc)
        rows_to_write: list[dict[str, Any]] = []
        if signals:
            scan_end = end + timedelta(days=60)
            calendar = await _load_calendar(
                session, start - timedelta(days=5), scan_end
            )
            pairs: set[tuple[str, date]] = set()
            for signal in signals:
                entry_date, exits = forward_window(signal["signal_date"], calendar)
                if entry_date is not None:
                    pairs.add((signal["code"], entry_date))
                for exit_date in exits.values():
                    if exit_date is not None:
                        pairs.add((signal["code"], exit_date))

            bars = await _load_bars(session, pairs)
            benchmark = await _load_benchmark(session, start, scan_end)
            industries = await _load_industries(
                session, [signal["code"] for signal in signals]
            )

            for signal in signals:
                result = compute_forward_result(
                    signal["signal_date"],
                    calendar,
                    bars.get(signal["code"], {}),
                    benchmark,
                    data_end=data_end,
                )
                row: dict[str, Any] = {
                    "algo_code": signal["algo_code"],
                    "signal_date": signal["signal_date"],
                    "code": signal["code"],
                    "rank": signal["rank"],
                    "score": signal["score"],
                    "entry_date": result.entry_date,
                    "entry_price": result.entry_price,
                    "fillable": result.fillable,
                    "industry_l1": industries.get(signal["code"]),
                    "computed_at": now,
                }
                for horizon in HORIZONS:
                    row[f"ret_{horizon}"] = result.returns[horizon]
                    row[f"excess_{horizon}"] = result.excess[horizon]
                    row[f"mature_{horizon}"] = result.mature[horizon]
                rows_to_write.append(row)

        await session.execute(
            delete(StgSignalPerf).where(
                StgSignalPerf.algo_code.in_(algo_list),
                StgSignalPerf.signal_date >= start,
                StgSignalPerf.signal_date <= end,
            )
        )
        for offset in range(0, len(rows_to_write), 1_000):
            batch = rows_to_write[offset:offset + 1_000]
            stmt = pg_insert(StgSignalPerf).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["algo_code", "signal_date", "code"],
                set_={
                    column: getattr(stmt.excluded, column)
                    for column in (
                        "rank", "score", "entry_date", "entry_price",
                        "ret_5", "ret_10", "ret_20",
                        "excess_5", "excess_10", "excess_20",
                        "mature_5", "mature_10", "mature_20",
                        "fillable", "industry_l1", "computed_at",
                    )
                },
            )
            await session.execute(stmt)

        await commit_or_flush(session, external=db is not None)

    fillable_count = sum(1 for row in rows_to_write if row["fillable"])
    mature_20 = sum(1 for row in rows_to_write if row["mature_20"])
    logger.info(
        "信号前瞻收益重算完成 %s~%s：signals=%d rows=%d fillable=%d mature_20=%d",
        start, end, len(signals), len(rows_to_write), fillable_count, mature_20,
    )
    return {
        "rows": len(rows_to_write),
        "detail": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "data_end": data_end.isoformat() if data_end else None,
            "signals": len(signals),
            "fillable": fillable_count,
            "mature_20": mature_20,
            "algo_count": len(algo_list),
        },
    }


async def recent_window_start(
    session: AsyncSession, end: date, days: int = 25
) -> date | None:
    """最近 N 个交易日窗口的起点（含 end 当天或之前最近交易日）。"""
    rows = (await session.execute(
        select(TradeCalendar.cal_date)
        .where(
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date <= end,
        )
        .order_by(TradeCalendar.cal_date.desc())
        .limit(max(1, days))
    )).scalars().all()
    return min(rows) if rows else None


async def latest_signal_date(
    session: AsyncSession, algos: Sequence[str]
) -> date | None:
    return (await session.execute(
        select(func.max(StgSignalPerf.signal_date)).where(
            StgSignalPerf.algo_code.in_(sorted(set(algos)))
        )
    )).scalar_one_or_none()


async def ladder_snapshot(
    session: AsyncSession,
    algos: Sequence[LadderAlgo],
    *,
    window: int = 60,
    horizon: int = 20,
    trade_date: date | None = None,
) -> dict[str, Any]:
    """读取天梯数据（只读，不落库）。"""
    algo_codes = [algo.algo_code for algo in algos]
    target = trade_date
    if target is None:
        target = await latest_signal_date(session, algo_codes)

    window_days = [window] if window > 0 else [60]
    open_dates = (await session.execute(
        select(TradeCalendar.cal_date)
        .where(
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date <= (target or date.today()),
        )
        .order_by(TradeCalendar.cal_date.desc())
        .limit(window_days[0])
    )).scalars().all()
    if open_dates:
        target = max(open_dates)
        start = min(open_dates)
    else:
        start = target or date.today()

    if target is None:
        target = date.today()

    rows = (await session.execute(
        select(
            StgSignalPerf.algo_code,
            StgSignalPerf.signal_date,
            StgSignalPerf.code,
            StgSignalPerf.fillable,
            StgSignalPerf.mature_5,
            StgSignalPerf.mature_10,
            StgSignalPerf.mature_20,
            StgSignalPerf.ret_5,
            StgSignalPerf.ret_10,
            StgSignalPerf.ret_20,
            StgSignalPerf.excess_5,
            StgSignalPerf.excess_10,
            StgSignalPerf.excess_20,
        ).where(
            StgSignalPerf.algo_code.in_(algo_codes),
            StgSignalPerf.signal_date >= start,
            StgSignalPerf.signal_date <= target,
        )
    )).all()

    perf_rows = [
        PerfRow(
            algo_code=algo_code,
            signal_date=signal_date,
            code=code,
            fillable=bool(fillable),
            mature_5=bool(mature_5),
            mature_10=bool(mature_10),
            mature_20=bool(mature_20),
            ret_5=_num(ret_5),
            ret_10=_num(ret_10),
            ret_20=_num(ret_20),
            excess_5=_num(excess_5),
            excess_10=_num(excess_10),
            excess_20=_num(excess_20),
        )
        for (
            algo_code, signal_date, code, fillable,
            mature_5, mature_10, mature_20,
            ret_5, ret_10, ret_20,
            excess_5, excess_10, excess_20,
        ) in rows
    ]

    items = build_ladder(perf_rows, algos, horizon=horizon)
    selected_immature = sum(item.get("immature") or 0 for item in items)
    selected_unfillable = sum(item.get("unfillable") or 0 for item in items)
    return {
        "trade_date": target.isoformat(),
        "window": window,
        "horizon": horizon,
        "benchmark": BENCHMARK_INDEX,
        "window_start": start.isoformat(),
        "items": items,
        "immature_total": selected_immature,
        "unfillable_total": selected_unfillable,
    }


__all__ = [
    "HORIZONS",
    "LADDER_WINDOWS",
    "MIN_SAMPLE",
    "ForwardResult",
    "LadderAlgo",
    "PerfRow",
    "SignalBar",
    "build_ladder",
    "compute_forward_result",
    "forward_window",
    "latest_signal_date",
    "ladder_snapshot",
    "recent_window_start",
    "recompute_signal_perf",
]
