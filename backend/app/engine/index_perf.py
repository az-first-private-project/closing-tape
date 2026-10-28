"""指数多周期收益（纯函数，不做任何 IO）。

口径约定（与 fact/market 表一致）：
  - 周期按**交易日**计数，closes 必须是「日期 + 收盘价」并按日期升序；
  - 返回值是**百分数**（5.0 表示 +5%），与 ``pct_chg`` 同口径，前端直接加 %；
  - 历史长度不足该周期时返回 ``None``，**绝不返回 0**（缺数据不是"没涨跌"）；
  - ``ret_ytd`` 以「当年首个交易日的前一交易日」收盘为基准；若该基准不在
    closes 里（例如数据从当年才开始的次新指数），返回 ``None``。

为什么锚点取 ``as_of`` 当天或之前最后一根：美股指数按 T-1 收盘展示，
A 股 ``as_of`` 当天没有美股数据；若强行要求日期完全相等，美股窗口会永远为空。
对 A 股指数而言 ``as_of`` 当天必有行（否则接口根本不会返回该指数）。
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

WINDOW_KEYS = ("ret_1d", "ret_5d", "ret_20d", "ret_60d", "ret_ytd")

# 每个窗口回看的交易日数；ret_ytd 单独计算
_WINDOWS: dict[str, int] = {
    "ret_1d": 1,
    "ret_5d": 5,
    "ret_20d": 20,
    "ret_60d": 60,
}


def _clean_points(
    closes: Sequence[tuple[date, float | None]],
) -> list[tuple[date, float]]:
    """剔除空值/非法价并按日期升序；同一天重复时保留最后一条。"""
    by_date: dict[date, float] = {}
    for item in closes:
        if not item:
            continue
        trade_date, close = item[0], item[1]
        if trade_date is None or close is None:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        # 指数收盘价不可能 <= 0；0 会被当成"没涨跌"，必须排除
        if value <= 0:
            continue
        by_date[trade_date] = value
    return sorted(by_date.items(), key=lambda pair: pair[0])


def _pct_change(current: float, base: float) -> float | None:
    if base <= 0:
        return None
    return round((current / base - 1.0) * 100.0, 4)


def window_returns(
    closes: list[tuple[date, float]],
    as_of: date,
) -> dict[str, float | None]:
    """计算 ``as_of`` 时点的 1/5/20/60 日与年初至今收益。

    Args:
        closes: 按日期升序的 ``(trade_date, close)``；可含 as_of 之后的日期，
            函数只取 ``trade_date <= as_of`` 的部分。
        as_of: 观察日；取该日或之前最后一根收盘作为锚点。

    Returns:
        固定包含 ``ret_1d ret_5d ret_20d ret_60d ret_ytd`` 五个键；
        数据不足的键为 ``None``。
    """
    points = _clean_points([(d, c) for d, c in closes if d is not None and d <= as_of])
    result: dict[str, float | None] = {key: None for key in WINDOW_KEYS}
    if not points:
        return result

    anchor_idx = len(points) - 1
    anchor_date, anchor_close = points[anchor_idx]

    for key, lookback in _WINDOWS.items():
        base_idx = anchor_idx - lookback
        if base_idx < 0:
            continue
        result[key] = _pct_change(anchor_close, points[base_idx][1])

    # YTD：找到锚点所在年份的第一根，基准是它的前一根（上一年最后交易日）
    year = anchor_date.year
    first_of_year = None
    for idx, (trade_date, _) in enumerate(points):
        if trade_date.year == year:
            first_of_year = idx
            break
    if first_of_year is not None and first_of_year > 0:
        result["ret_ytd"] = _pct_change(anchor_close, points[first_of_year - 1][1])

    return result


__all__ = ["WINDOW_KEYS", "window_returns"]
