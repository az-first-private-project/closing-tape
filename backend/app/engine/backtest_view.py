"""回测结果派生视图（V6 T9）。

全部从已落库的 ``equity_curve`` / ``trades`` 派生，**不重新回测**：
  - ``monthly_returns``：按"上月最后一个净值 → 本月最后一个净值"串联，
    首月以区间首个净值为基准；这样逐月复利严格等于 ``total_return``
    （任务书里"当月首末两点相除"会在月末与次月首日之间漏掉跳空，
    实测一年区间误差可达数个百分点，故按验收口径改为月链式）；
  - ``drawdown_curve``：``nav / 运行峰值 - 1``，与 ``metrics.max_drawdown`` 同口径；
  - ``rolling_excess_20d``：20 个交易日滚动 ``nav 收益 - benchmark 收益``；
  - ``industry_exposure``：由成交重建每个调仓日的持仓，按申万一级行业
    汇总收盘市值权重；行业为 NULL 归入"其他"并计数，现金显式列出。

百分比字段统一为百分数（4.2 表示 4.2%），金额单位为元。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from typing import Any, Mapping, Sequence

ROLLING_WINDOW = 20
OTHER_INDUSTRY = "其他"
CASH_LABEL = "现金"
_PERCENT_DIGITS = 4


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _pct(ratio: float) -> float:
    return round(ratio * 100.0, _PERCENT_DIGITS)


def _nav_points(curve: Sequence[Mapping[str, Any]] | None) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    for item in curve or []:
        raw_date = str(item.get("date") or "").strip()
        nav = _number(item.get("nav"))
        if not raw_date or nav is None:
            continue
        points.append((raw_date[:10], nav))
    points.sort(key=lambda pair: pair[0])
    return points


def _benchmark_by_month(
    curve: Sequence[Mapping[str, Any]] | None,
    months: Sequence[str],
) -> dict[str, float]:
    """每个月最后一个非空 benchmark 净值。"""
    result: dict[str, float] = {}
    for item in curve or []:
        raw_date = str(item.get("date") or "").strip()
        benchmark = _number(item.get("benchmark"))
        if not raw_date or benchmark is None or benchmark <= 0:
            continue
        ym = raw_date[:7]
        if months and (ym < months[0] or ym > months[-1]):
            continue
        result[ym] = benchmark
    return result


def _monthly_returns(
    curve: Sequence[Mapping[str, Any]] | None,
    nav_points: Sequence[tuple[str, float]],
) -> list[dict[str, Any]]:
    if not nav_points:
        return []

    month_end: dict[str, float] = {}
    month_first: dict[str, float] = {}
    month_order: list[str] = []
    for day, nav in nav_points:
        ym = day[:7]
        if ym not in month_end:
            month_order.append(ym)
            month_first[ym] = nav
        month_end[ym] = nav

    bench_by_month = _benchmark_by_month(curve, month_order)

    items: list[dict[str, Any]] = []
    prev_nav: float | None = None
    prev_bench: float | None = None
    for ym in month_order:
        end_nav = month_end[ym]
        base_nav = prev_nav if prev_nav is not None else month_first[ym]
        ret = end_nav / base_nav - 1.0 if base_nav else None
        prev_nav = end_nav

        benchmark_ret: float | None = None
        if ym in bench_by_month:
            month_bench = bench_by_month[ym]
            if prev_bench is not None:
                benchmark_ret = month_bench / prev_bench - 1.0
            else:
                # 基准起始月：用当月首个基准点做基准
                first_bench = _first_benchmark_in_month(curve, ym)
                if first_bench is not None:
                    benchmark_ret = month_bench / first_bench - 1.0
            prev_bench = month_bench

        excess = None
        if ret is not None and benchmark_ret is not None:
            excess = ret - benchmark_ret

        items.append(
            {
                "ym": ym,
                "ret": _pct(ret) if ret is not None else None,
                "benchmark": _pct(benchmark_ret) if benchmark_ret is not None else None,
                "excess": _pct(excess) if excess is not None else None,
            }
        )
    return items


def _first_benchmark_in_month(
    curve: Sequence[Mapping[str, Any]] | None,
    ym: str,
) -> float | None:
    first: float | None = None
    first_date: str | None = None
    for item in curve or []:
        raw_date = str(item.get("date") or "").strip()[:10]
        if not raw_date.startswith(ym):
            continue
        benchmark = _number(item.get("benchmark"))
        if benchmark is None or benchmark <= 0:
            continue
        if first_date is None or raw_date < first_date:
            first_date = raw_date
            first = benchmark
    return first


def _drawdown_curve(nav_points: Sequence[tuple[str, float]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    peak: float | None = None
    for day, nav in nav_points:
        peak = nav if peak is None else max(peak, nav)
        drawdown = nav / peak - 1.0 if peak else 0.0
        result.append(
            {
                "date": day,
                "nav": round(nav, 6),
                "peak": round(peak, 6),
                "drawdown": _pct(drawdown),
            }
        )
    return result


def _rolling_excess(
    curve: Sequence[Mapping[str, Any]] | None,
    nav_points: Sequence[tuple[str, float]],
) -> list[dict[str, Any]]:
    if not nav_points:
        return []
    bench_by_date: dict[str, float] = {}
    for item in curve or []:
        day = str(item.get("date") or "").strip()[:10]
        benchmark = _number(item.get("benchmark"))
        if day and benchmark is not None and benchmark > 0:
            bench_by_date[day] = benchmark

    result: list[dict[str, Any]] = []
    for index, (day, nav) in enumerate(nav_points):
        value: float | None = None
        if index >= ROLLING_WINDOW:
            base_day, base_nav = nav_points[index - ROLLING_WINDOW]
            base_bench = bench_by_date.get(base_day)
            end_bench = bench_by_date.get(day)
            if base_nav and nav > 0 and base_bench and end_bench:
                value = _pct(nav / base_nav - end_bench / base_bench)
        result.append({"date": day, "value": value})
    return result


# =====================================================================
# 行业分布
# =====================================================================

def _prepare_closes(
    daily_rows_by_code: Mapping[str, Any] | None,
) -> dict[str, tuple[list[str], list[float]]]:
    prepared: dict[str, tuple[list[str], list[float]]] = {}
    for code, rows in (daily_rows_by_code or {}).items():
        if not isinstance(rows, Mapping):
            continue
        pairs: list[tuple[str, float]] = []
        for raw_day, value in rows.items():
            close = _number(value.get("close") if isinstance(value, Mapping) else value)
            if close is None or close <= 0:
                continue
            day = str(raw_day)[:10]
            if day:
                pairs.append((day, close))
        pairs.sort(key=lambda pair: pair[0])
        prepared[str(code).zfill(6)] = ([pair[0] for pair in pairs], [pair[1] for pair in pairs])
    return prepared


def _last_close(
    prepared: Mapping[str, tuple[list[str], list[float]]],
    code: str,
    day: str,
) -> float | None:
    entry = prepared.get(code)
    if not entry:
        return None
    dates, closes = entry
    index = bisect_right(dates, day) - 1
    if index < 0:
        return None
    return closes[index]


def _executed_trades(trades: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for item in trades or []:
        shares = _number(item.get("shares"))
        code = str(item.get("code") or "").strip()
        side = str(item.get("side") or "").strip().lower()
        day = str(item.get("date") or "").strip()[:10]
        if not code or not day or shares is None or shares <= 0 or side not in ("buy", "sell"):
            continue
        price = _number(item.get("price"))
        amount = _number(item.get("amount"))
        if amount is None and price is not None:
            amount = price * shares
        executed.append(
            {
                "date": day,
                "code": code.zfill(6),
                "side": side,
                "shares": int(shares),
                "price": price,
                "amount": float(amount or 0.0),
                "fee": float(_number(item.get("fee")) or 0.0),
            }
        )
    executed.sort(key=lambda item: (item["date"], 0 if item["side"] == "sell" else 1))
    return executed


def _industry_exposure(
    trades: Sequence[Mapping[str, Any]] | None,
    daily_rows_by_code: Mapping[str, Any] | None,
    industry_by_code: Mapping[str, str | None] | None,
    initial_cash: float,
) -> list[dict[str, Any]]:
    executed = _executed_trades(trades)
    if not executed:
        return []

    prepared = _prepare_closes(daily_rows_by_code)
    industries = {str(code).zfill(6): name for code, name in (industry_by_code or {}).items()}

    holdings: dict[str, int] = {}
    last_price: dict[str, float] = {}
    cash = float(initial_cash)
    snapshots: list[dict[str, Any]] = []

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in executed:
        by_date[trade["date"]].append(trade)

    for day in sorted(by_date):
        for trade in by_date[day]:
            code = trade["code"]
            shares = trade["shares"]
            if trade["side"] == "buy":
                holdings[code] = holdings.get(code, 0) + shares
                cash -= trade["amount"] + trade["fee"]
            else:
                remaining = max(holdings.get(code, 0) - shares, 0)
                if remaining:
                    holdings[code] = remaining
                else:
                    holdings.pop(code, None)
                cash += trade["amount"] - trade["fee"]
            if trade["price"] is not None and trade["price"] > 0:
                last_price[code] = trade["price"]

        values: dict[str, float] = {}
        for code, shares in holdings.items():
            if shares <= 0:
                continue
            close = _last_close(prepared, code, day)
            if close is None:
                close = last_price.get(code)
            if close is None:
                continue
            values[code] = shares * close

        total_value = cash + sum(values.values())
        grouped: dict[str, dict[str, Any]] = {}
        other_count = 0
        for code, value in values.items():
            industry = (industries.get(code) or "").strip() or OTHER_INDUSTRY
            if industry == OTHER_INDUSTRY:
                other_count += 1
            bucket = grouped.setdefault(
                industry, {"name": industry, "value": 0.0, "count": 0}
            )
            bucket["value"] += value
            bucket["count"] += 1

        denominator = total_value if total_value > 0 else 0.0
        industries_out: list[dict[str, Any]] = []
        for bucket in grouped.values():
            weight = (bucket["value"] / denominator * 100.0) if denominator else 0.0
            industries_out.append(
                {
                    "name": bucket["name"],
                    "weight": round(weight, _PERCENT_DIGITS),
                    "value": round(bucket["value"], 2),
                    "count": bucket["count"],
                }
            )
        industries_out.sort(key=lambda item: (-item["weight"], item["name"]))

        cash_weight = round(100.0 - sum(item["weight"] for item in industries_out), _PERCENT_DIGITS)
        industries_out.append(
            {
                "name": CASH_LABEL,
                "weight": cash_weight,
                "value": round(cash, 2),
                "count": 0,
            }
        )
        snapshots.append(
            {
                "date": day,
                "total_value": round(total_value, 2),
                "cash": round(cash, 2),
                "cash_weight": cash_weight,
                "stock_count": sum(item["count"] for item in industries_out if item["name"] != CASH_LABEL),
                "other_count": other_count,
                "industries": industries_out,
            }
        )
    return snapshots


# =====================================================================
# 入口
# =====================================================================

def build_backtest_extras(
    curve: Sequence[Mapping[str, Any]] | None,
    trades: Sequence[Mapping[str, Any]] | None,
    daily_rows_by_code: Mapping[str, Any] | None = None,
    *,
    industry_by_code: Mapping[str, str | None] | None = None,
    initial_cash: float = 1_000_000.0,
) -> dict[str, Any]:
    """从已存 JSONB 派生四块视图（纯函数，不查库）。"""
    curve = list(curve or [])
    trades = list(trades or [])
    nav_points = _nav_points(curve)
    available = bool(curve) and bool(trades)

    if not available:
        return {
            "extras_available": False,
            "monthly_returns": [],
            "drawdown_curve": [],
            "rolling_excess_20d": [],
            "industry_exposure": [],
        }

    return {
        "extras_available": True,
        "monthly_returns": _monthly_returns(curve, nav_points),
        "drawdown_curve": _drawdown_curve(nav_points),
        "rolling_excess_20d": _rolling_excess(curve, nav_points),
        "industry_exposure": _industry_exposure(
            trades,
            daily_rows_by_code,
            industry_by_code,
            initial_cash if initial_cash and initial_cash > 0 else 1_000_000.0,
        ),
    }


__all__ = [
    "CASH_LABEL",
    "OTHER_INDUSTRY",
    "ROLLING_WINDOW",
    "build_backtest_extras",
]
