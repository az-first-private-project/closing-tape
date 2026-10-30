"""
绩效指标（方案 D5）
====================
输入是**归一化净值序列**（首个值为 1.0 或任意基准值，比率口径与起点无关）、
基准净值序列（可缺省）、逐笔成交与初始资金，输出契约 6.1 的 metrics 字段：

    total_return, annual_return, max_drawdown, sharpe, sortino, calmar,
    win_rate, profit_loss_ratio, turnover, trade_count,
    benchmark_return, excess_return, volatility

口径说明（写死，避免各处各算一套）：
  - 无风险利率 = 0；夏普/索提诺用日收益 × sqrt(252) 年化；
  - 最大回撤为**负值**（0 表示没回撤）；
  - 胜率按"完整买卖回合"计：FIFO 配对买卖（先买后卖），一个匹配片段算一个回合；
    买入成本含佣金，卖出收入扣佣金+印花税；
  - 换手率 = (买入金额 + 卖出金额) / 2 / 初始资金（区间单边换手，未年化）；
  - 基准缺失时 benchmark_return / excess_return 返回 None（不编造数字）。
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

#: 返回 metrics 的完整键集合（顺序即契约展示顺序）
METRIC_KEYS: tuple[str, ...] = (
    "total_return", "annual_return", "max_drawdown", "sharpe", "sortino",
    "calmar", "win_rate", "profit_loss_ratio", "turnover", "trade_count",
    "benchmark_return", "excess_return", "volatility",
)


def empty_metrics() -> dict[str, Any]:
    """没有净值时的零值指标（保证 API 结构稳定，不返回 500）。"""
    return {
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "calmar": 0.0,
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "turnover": 0.0,
        "trade_count": 0,
        "benchmark_return": None,
        "excess_return": None,
        "volatility": 0.0,
    }


def _as_numeric_series(values: Any) -> pd.Series:
    """任意序列 → float64 Series（去除非数值并 keep 原索引）。"""
    series = pd.Series(values) if not isinstance(values, pd.Series) else values.copy()
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _round(value: float, digits: int = 6) -> float:
    """安全四舍五入（NaN/inf → 0.0）。"""
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return round(number, digits)


def _fifo_rounds(trades: list[dict] | None) -> list[float]:
    """按 FIFO 把成交配对成"买卖回合"，返回每个回合的盈亏金额（元）。

    规则：
      - 只看 shares > 0 的实际成交，忽略失败单（shares=0，reason 为不可成交原因）；
      - 买入成本 = (price × shares + fee) / shares；
      - 卖出收入 = (price × shares - fee) / shares；
      - 一个卖出单可能匹配多个历史买入 lot，每个匹配片段算一个回合。
    """
    executed = [item for item in (trades or []) if int(item.get("shares") or 0) > 0]
    if not executed:
        return []

    # 同一日期先处理买入再处理卖出（稳定排序，保证可复现）
    ordered = sorted(
        executed,
        key=lambda item: (str(item.get("date") or ""), 0 if str(item.get("side")).lower() == "buy" else 1),
    )

    lots: dict[str, deque[list[float]]] = {}
    rounds: list[float] = []
    for item in ordered:
        shares = int(item.get("shares") or 0)
        price = float(item.get("price") or 0.0)
        fee = float(item.get("fee") or 0.0)
        if shares <= 0 or price <= 0:
            continue
        code = str(item.get("code") or "")
        side = str(item.get("side") or "").strip().lower()

        if side == "buy":
            cost_per_share = (price * shares + fee) / shares
            lots.setdefault(code, deque()).append([float(shares), cost_per_share])
        elif side == "sell":
            proceeds_per_share = (price * shares - fee) / shares
            queue = lots.get(code)
            if not queue:
                continue  # 没有对应买入（数据不完整），不配对
            remaining = float(shares)
            while remaining > 0 and queue:
                lot = queue[0]
                matched = min(remaining, lot[0])
                rounds.append((proceeds_per_share - lot[1]) * matched)
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= 1e-9:
                    queue.popleft()
    return rounds


def compute_metrics(
    nav: pd.Series,
    benchmark: pd.Series | None,
    trades: list[dict],
    rebalance_count: int,
    initial_cash: float,
) -> dict[str, Any]:
    """计算回测绩效指标（契约 6.1 metrics）。

    参数：
        nav:           归一化净值序列（绝对净值也行，只用到相对变化）
        benchmark:     归一化基准净值序列（可 None / 全空）
        trades:        逐笔成交（含失败单时按 shares=0 忽略）
        rebalance_count: 调仓次数（保留参数用于口径扩展/审计）
        initial_cash:  初始资金（换手率分母）
    """
    metrics = empty_metrics()

    nav_series = _as_numeric_series(nav).replace([np.inf, -np.inf], np.nan).dropna() \
        if nav is not None else pd.Series(dtype="float64")
    if len(nav_series) >= 1:
        base = float(nav_series.iloc[0])
        last = float(nav_series.iloc[-1])
        total_return = (last / base - 1.0) if base != 0 else 0.0
        metrics["total_return"] = _round(total_return, 6)

        periods = max(len(nav_series) - 1, 1)
        growth = 1.0 + total_return
        if growth <= 0:
            annual_return = -1.0
        else:
            annual_return = growth ** (TRADING_DAYS_PER_YEAR / periods) - 1.0
        metrics["annual_return"] = _round(annual_return, 6)

        returns = nav_series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if len(returns) >= 2:
            std = float(returns.std(ddof=1))
            mean = float(returns.mean())
            metrics["volatility"] = _round(std * math.sqrt(TRADING_DAYS_PER_YEAR), 6)
            if std > 0:
                metrics["sharpe"] = _round(mean / std * math.sqrt(TRADING_DAYS_PER_YEAR), 4)
            downside = returns[returns < 0]
            if len(downside) >= 2:
                downside_std = float(downside.std(ddof=1))
                if downside_std > 0:
                    metrics["sortino"] = _round(
                        mean / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR), 4
                    )

        running_max = nav_series.cummax()
        drawdown = nav_series / running_max.replace(0.0, np.nan) - 1.0
        max_drawdown = float(drawdown.min()) if len(drawdown.dropna()) else 0.0
        metrics["max_drawdown"] = _round(max_drawdown, 6)
        if max_drawdown < 0 and math.isfinite(annual_return):
            metrics["calmar"] = _round(annual_return / abs(max_drawdown), 4)

    # ---------------- 基准 ----------------
    benchmark_return: float | None = None
    if benchmark is not None:
        benchmark_series = _as_numeric_series(benchmark).replace([np.inf, -np.inf], np.nan).dropna()
        if len(benchmark_series) >= 1:
            bench_base = float(benchmark_series.iloc[0])
            bench_last = float(benchmark_series.iloc[-1])
            if bench_base != 0:
                benchmark_return = bench_last / bench_base - 1.0
                metrics["benchmark_return"] = _round(benchmark_return, 6)
                metrics["excess_return"] = _round(
                    float(metrics["total_return"]) - benchmark_return, 6
                )

    # ---------------- 成交类 ----------------
    executed = [item for item in (trades or []) if int(item.get("shares") or 0) > 0]
    metrics["trade_count"] = int(len(executed))

    buy_amount = 0.0
    sell_amount = 0.0
    for item in executed:
        amount = float(item.get("amount") or 0.0)
        side = str(item.get("side") or "").strip().lower()
        if side == "buy":
            buy_amount += amount
        elif side == "sell":
            sell_amount += amount

    if initial_cash and float(initial_cash) > 0:
        metrics["turnover"] = _round(
            (buy_amount + sell_amount) / 2.0 / float(initial_cash), 6
        )

    rounds = _fifo_rounds(trades)
    if rounds:
        wins = [item for item in rounds if item > 0]
        losses = [item for item in rounds if item < 0]
        metrics["win_rate"] = _round(len(wins) / len(rounds), 4)
        if wins and losses:
            avg_win = sum(wins) / len(wins)
            avg_loss = abs(sum(losses) / len(losses))
            if avg_loss > 0:
                metrics["profit_loss_ratio"] = _round(avg_win / avg_loss, 4)

    # rebalance_count 保留在函数签名中：用于换手口径未来扩展（如按调仓次数年化）
    _ = rebalance_count
    return metrics


__all__ = ["compute_metrics", "empty_metrics", "METRIC_KEYS", "TRADING_DAYS_PER_YEAR"]
