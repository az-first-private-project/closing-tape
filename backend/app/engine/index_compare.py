"""指数对比 / 风格轮动计算（V7 T3）
==================================
创业板50（sz399673）与红利低波50（H30269）的价格指数比价：

    R = 创业板50 收盘点位 ÷ 红利低波50 收盘点位

- R 只由后端用收盘点位计算，前端/文案不得自行推算；
- 阈值优先使用**滚动 20/80 分位**（只用截至当日的历史，避免前视）；
  历史不足 250 日时退回提示词默认固定阈值 0.18 / 0.30；
- 三态：R < low 偏成长 / low <= R <= high 均衡 / R > high 偏红利；
- 不输出旧配对阈值（0.4 / 0.62），不做涨跌预测。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd

from app.core.constants import (
    STYLE_INDEX_MAP,
    STYLE_ROTATION_FIXED_HIGH,
    STYLE_ROTATION_FIXED_LOW,
    STYLE_ROTATION_MIN_PERIODS,
    STYLE_ROTATION_WINDOW_DAYS,
)

CYB50_CODE = "sz399673"
HL50_CODE = "H30269"

DISCLAIMER = "历史数据回测不代表未来表现，不构成投资建议。"


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _pct_change(current: float, previous: float | None) -> float | None:
    if previous is None or not np.isfinite(previous) or previous == 0:
        return None
    return (current / previous - 1) * 100


def _rolling_percentile(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    for index in range(len(values)):
        current = values[index]
        if not np.isfinite(current):
            continue
        start = max(0, index - window + 1)
        window_values = values[start:index + 1]
        valid = window_values[np.isfinite(window_values)]
        if len(valid) < min_periods:
            continue
        out[index] = float(np.mean(valid < current) * 100)
    return pd.Series(out, index=series.index)


def _zone(current: float, low: float, high: float) -> str:
    if current < low:
        return "低估区"
    if current > high:
        return "高估区"
    return "均衡区"


def _targets(zone: str) -> tuple[float, float]:
    if zone == "低估区":
        return 1.0, 0.0
    if zone == "高估区":
        return 0.0, 1.0
    return 0.5, 0.5


def _headline(zone: str) -> str:
    return {
        "低估区": "比价处低估区，成长相对红利便宜",
        "均衡区": "比价处均衡区，两风格各半持有",
        "高估区": "比价处高估区，成长相对红利偏贵",
    }[zone]


def _readout(
    current_r: float,
    low: float,
    high: float,
    percentile: float | None,
    r_20d: float | None,
    r_1y: float | None,
) -> str:
    zone = _zone(current_r, low, high)
    parts = [
        f"当前 R={current_r:.4f}，处于滚动分位 {percentile:.1f}%，位于{zone}。"
        if percentile is not None else
        f"当前 R={current_r:.4f}，位于{zone}。"
    ]
    if r_20d is not None and r_1y is not None:
        parts.append(
            f"近 20 日 R {'上行' if r_20d >= 0 else '下行'} {abs(r_20d):.2f}%，"
            f"近一年 {'上行' if r_1y >= 0 else '下行'} {abs(r_1y):.2f}%。"
        )
    if zone == "均衡区":
        parts.append(
            f"距下界约 {(current_r - low) / low * 100:.1f}%，"
            f"距上界约 {(high - current_r) / high * 100:.1f}%。"
        )
    elif zone == "低估区":
        parts.append(f"距下界约 {(low - current_r) / low * 100:.1f}%，尚未回到均衡区。")
    else:
        parts.append(f"距上界约 {(current_r - high) / high * 100:.1f}%，尚未回到均衡区。")
    return "".join(parts)


def _action(zone: str) -> str:
    if zone == "低估区":
        return (
            "目标偏成长：创业板50 100% / 红利低波50 0%；"
            "从当前仓位分 5 批调整，每批不超过总仓位 20%，分批、不一次性梭哈。"
        )
    if zone == "高估区":
        return (
            "目标偏红利：创业板50 0% / 红利低波50 100%；"
            "从当前仓位分 5 批调整，每批不超过总仓位 20%，分批、不一次性梭哈。"
        )
    return (
        "目标均衡：创业板50 50% / 红利低波50 50%；"
        "维持各半，不在均衡区频繁切换；如需调整仍按 5 批、每批不超过 20% 执行。"
    )


def build_index_compare(
    rows: Iterable[dict[str, Any]],
    *,
    trade_date: date | None = None,
    window_days: int = STYLE_ROTATION_WINDOW_DAYS,
    min_periods: int = STYLE_ROTATION_MIN_PERIODS,
    chart_days: int = STYLE_ROTATION_WINDOW_DAYS,
) -> dict[str, Any]:
    """把两只指数的日线记录转成页面所需的完整 JSON。

    rows: [{index_code, trade_date, close, src?}, ...]
    """
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        raise ValueError("风格指数没有数据")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"])
    if trade_date is not None:
        frame = frame[frame["trade_date"] <= pd.Timestamp(trade_date)]
    if frame.empty:
        raise ValueError("指定日期之前没有风格指数数据")

    pivot = frame.pivot_table(
        index="trade_date", columns="index_code", values="close", aggfunc="last"
    ).sort_index()
    if CYB50_CODE not in pivot.columns or HL50_CODE not in pivot.columns:
        raise ValueError("创业板50 或 红利低波50 数据不完整")
    pivot = pivot[[CYB50_CODE, HL50_CODE]].dropna()
    if pivot.empty:
        raise ValueError("两只指数没有共同交易日")
    pivot.columns = ["cyb50", "hl50"]
    r_series = pivot["cyb50"] / pivot["hl50"]

    q20 = r_series.rolling(window_days, min_periods=min_periods).quantile(0.20)
    q80 = r_series.rolling(window_days, min_periods=min_periods).quantile(0.80)
    rolling_pct = _rolling_percentile(r_series, window_days, min_periods)

    current_r = float(r_series.iloc[-1])
    current_low = q20.iloc[-1]
    current_high = q80.iloc[-1]
    current_pct = rolling_pct.iloc[-1]
    rolling_ready = all(np.isfinite(value) for value in (current_low, current_high, current_pct))

    sample_low = float(np.percentile(r_series.to_numpy(), 20))
    sample_high = float(np.percentile(r_series.to_numpy(), 80))
    sample_pct = float(np.mean(r_series.to_numpy() < current_r) * 100)

    if rolling_ready:
        low, high, percentile = float(current_low), float(current_high), float(current_pct)
        method = "滚动分位"
    else:
        low, high = STYLE_ROTATION_FIXED_LOW, STYLE_ROTATION_FIXED_HIGH
        percentile = sample_pct
        method = "固定分位"

    zone = _zone(current_r, low, high)
    cyb_target, hl_target = _targets(zone)
    r_20d = _pct_change(current_r, float(r_series.iloc[-21])) if len(r_series) > 20 else None
    r_1y = _pct_change(current_r, float(r_series.iloc[-251])) if len(r_series) > 250 else None

    index_dates = {
        code: pd.Timestamp(frame.loc[frame["index_code"] == code, "trade_date"].max()).date().isoformat()
        for code in (CYB50_CODE, HL50_CODE)
    }
    sources = {
        code: sorted(set(
            str(value) for value in frame.loc[frame["index_code"] == code, "src"].dropna()
        ))
        for code in (CYB50_CODE, HL50_CODE)
    }
    data_date = pivot.index[-1].date()
    chart = pivot.tail(chart_days)
    chart_history = [
        {"date": index.date().isoformat(), "R": round(float(value), 4)}
        for index, value in (chart["cyb50"] / chart["hl50"]).items()
    ]

    return {
        "date": data_date.isoformat(),
        "R": round(current_r, 4),
        "closes": {
            "cyb50": round(float(pivot["cyb50"].iloc[-1]), 2),
            "hl50": round(float(pivot["hl50"].iloc[-1]), 2),
        },
        "percentile": round(percentile, 1),
        "zone": zone,
        "thresholds": {
            "low": round(low, 4),
            "high": round(high, 4),
            "method": method,
            "window_days": window_days,
            "min_periods": min_periods,
            "fixed_low": STYLE_ROTATION_FIXED_LOW,
            "fixed_high": STYLE_ROTATION_FIXED_HIGH,
            "sample_q20": round(sample_low, 4),
            "sample_q80": round(sample_high, 4),
            "sample_percentile": round(sample_pct, 1),
        },
        "headline": _headline(zone),
        "chart": {
            "series_note": "R = 创业板50 收盘点位 ÷ 红利低波50 收盘点位；折线为最近窗口日频序列。",
            "annotations": [
                f"下阈值 {low:.4f}（{method}）",
                f"上阈值 {high:.4f}（{method}）",
                f"当前点 {current_r:.4f}",
            ],
            "readout": _readout(current_r, low, high, percentile, r_20d, r_1y),
            "history": chart_history,
        },
        "allocation": {
            "cyb50_target": cyb_target,
            "hl50_target": hl_target,
            "cyb50_current": None,
            "hl50_current": None,
            "batches": 5,
            "step_pct": 20,
            "action": _action(zone),
            "rebalance_trigger": "R 跌破下界转偏成长，升破上界转偏红利；否则维持当前目标。",
            "note": "未提供当前持仓，以上为目标仓位；如已有持仓，按差额分 5 批调整。",
        },
        "risk": (
            "这是基于历史比价的相对强弱信号，不是绝对估值判断；"
            "高 R 不代表立刻反转，红利端收益高度依赖分红再投。"
        ),
        "disclaimer": DISCLAIMER,
        "meta": {
            "index_dates": index_dates,
            "common_start": pivot.index[0].date().isoformat(),
            "common_end": data_date.isoformat(),
            "counts": {
                code: int((frame["index_code"] == code).sum())
                for code in (CYB50_CODE, HL50_CODE)
            },
            "sources": sources,
            "window_days": window_days,
            "min_periods": min_periods,
            "is_stale": index_dates[CYB50_CODE] != index_dates[HL50_CODE],
            "index_names": {
                CYB50_CODE: STYLE_INDEX_MAP[CYB50_CODE],
                HL50_CODE: STYLE_INDEX_MAP[HL50_CODE],
            },
        },
        "caveats": [
            "R 用两只价格指数收盘点位计算；红利端收益描述可提及含分红，但 R 本身不含分红。",
            "H30269 为红利低波50（中证红利低波动指数），不使用中证红利、红利低波100 或 ETF 价格替代。",
            "阈值优先使用滚动 20/80 分位，仅用截至当日的历史；历史不足 250 日时退回固定阈值 0.18 / 0.30。",
            "创业板50 与 H30269 共同历史约 12.4 年（2014-06 起），页面不宣称近 15 年。",
        ],
    }


__all__ = ["build_index_compare", "DISCLAIMER"]
