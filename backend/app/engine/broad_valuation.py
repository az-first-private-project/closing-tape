"""牛熊位置 × 估值（V7 T4）
==========================
按文件硬约束实现 6 个宽基指数的四面板：

1. 2005 年以来归一化走势（对数轴，支持 3 年 / 10 年 / 全周期）；
2. 回撤 × 盈利收益率四象限（PE 快照来自文件，非历史分位）；
3. 动量热力矩阵（1M/3M/6M/12M，本地价格指数计算）；
4. 21 年回撤史（沪深300）+ ERP 当前值/分位（文件快照，仅近 10 年样本）。

硬约束：
  - 只允许 6 个指数，不得出现上证50、中证1000；
  - PE 是当前 TTM 绝对值，不得说成历史估值分位；
  - ERP 样本 2016-11-15 ~ 2026-11-12，不得说成覆盖 21 年；
  - 归一化曲线各指数起点不同，绝对高度不可横向比较；
  - 不输出买卖/加仓/抄底/目标点位。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

import numpy as np
import pandas as pd

from app.core.constants import (
    BROAD_ERP_SNAPSHOT,
    BROAD_INDEX_MAP,
    BROAD_INDEX_SNAPSHOT,
    BROAD_INDEX_SNAPSHOT_DATE,
    BROAD_KEY_NODES,
    BROAD_SUMMARY,
)

DISCLAIMER = "本内容仅为历史数据与估值状态展示，不构成投资建议。市场有风险，决策需谨慎。"
REQUIRED_SENTENCE = "现在不是全市场便宜，而是便宜得不均匀"

RANGE_LABELS = {"3y": "近 3 年", "10y": "近 10 年", "full": "全周期"}
MOMENTUM_WINDOWS = {"m1": 21, "m3": 63, "m6": 126, "m12": 252}


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _range_start(as_of: date, key: str) -> date:
    if key == "3y":
        return as_of - timedelta(days=365 * 3)
    if key == "10y":
        return as_of - timedelta(days=365 * 10)
    return date(2005, 1, 4)


def _normalized_history(pivot: pd.DataFrame, as_of: date, start: date) -> list[dict[str, Any]]:
    sub = pivot.loc[(pivot.index.date >= start) & (pivot.index.date <= as_of)]
    if sub.empty:
        return []
    monthly = sub.resample("ME").last()
    for code in BROAD_INDEX_MAP:
        if code not in monthly.columns:
            continue
        first = monthly[code].first_valid_index()
        if first is None:
            continue
        base = monthly.loc[first, code]
        if base and np.isfinite(base) and base > 0:
            monthly[code] = monthly[code] / base * 100
    records: list[dict[str, Any]] = []
    for stamp, row in monthly.iterrows():
        record: dict[str, Any] = {"date": stamp.date().isoformat()}
        for code in BROAD_INDEX_MAP:
            value = row.get(code)
            record[code] = _round(value, 2) if value is not None and np.isfinite(value) else None
        records.append(record)
    return records


def _momentum(pivot: pd.DataFrame, as_of: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, name in BROAD_INDEX_MAP.items():
        series = pivot[code].dropna()
        series = series[series.index.date <= as_of]
        latest = float(series.iloc[-1]) if len(series) else None
        item: dict[str, Any] = {"code": code, "name": name}
        for key, window in MOMENTUM_WINDOWS.items():
            if latest is None or len(series) <= window:
                item[key] = None
                continue
            base = float(series.iloc[-1 - window])
            item[key] = _round((latest / base - 1) * 100, 2) if base else None
        rows.append(item)
    return rows


def _drawdown_history(pivot: pd.DataFrame, as_of: date) -> list[dict[str, Any]]:
    series = pivot["sh000300"].dropna()
    series = series[series.index.date <= as_of]
    if series.empty:
        return []
    peak = series.cummax()
    drawdown = (series / peak - 1) * 100
    monthly = drawdown.resample("ME").last()
    return [
        {"date": stamp.date().isoformat(), "drawdown": round(float(value), 2)}
        for stamp, value in monthly.items()
        if np.isfinite(value)
    ]


def _key_nodes(pivot: pd.DataFrame, as_of: date) -> list[dict[str, Any]]:
    series = pivot["sh000300"].dropna()
    series = series[series.index.date <= as_of]
    nodes: list[dict[str, Any]] = []
    for month, label, kind in BROAD_KEY_NODES:
        window = series[series.index.strftime("%Y-%m") == month]
        if window.empty:
            continue
        target = window.idxmax() if kind == "top" else window.idxmin()
        nodes.append({
            "date": target.date().isoformat(),
            "label": label,
            "type": kind,
        })
    return nodes


def build_broad_valuation(
    rows: Iterable[dict[str, Any]],
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """rows: [{index_code, trade_date, close, src?}]；只接受 6 个宽基。"""
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        raise ValueError("宽基指数没有数据")
    frame["index_code"] = frame["index_code"].astype(str)
    frame = frame[frame["index_code"].isin(BROAD_INDEX_MAP)]
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"])
    if frame.empty:
        raise ValueError("宽基指数没有有效收盘价")

    pivot = frame.pivot_table(
        index="trade_date", columns="index_code", values="close", aggfunc="last"
    ).sort_index()
    missing = [code for code in BROAD_INDEX_MAP if code not in pivot.columns]
    if missing:
        raise ValueError(f"宽基指数不完整：{missing}")

    # 按文件表格的日线起点裁剪，避免把更早的源数据混进 21 年图
    for code in BROAD_INDEX_MAP:
        start_text = str(BROAD_INDEX_SNAPSHOT[code]["start"])
        start = pd.Timestamp(start_text)
        pivot.loc[pivot.index < start, code] = np.nan
    if as_of is not None:
        pivot = pivot.loc[pivot.index.date <= as_of]
    common = pivot.dropna()
    if common.empty:
        raise ValueError("6 个宽基指数没有共同交易日")
    price_as_of = common.index[-1].date()
    pivot = pivot.loc[pivot.index.date <= price_as_of]

    normalized = {
        key: _normalized_history(pivot, price_as_of, _range_start(price_as_of, key))
        for key in RANGE_LABELS
    }
    momentum = _momentum(pivot, price_as_of)
    drawdown = _drawdown_history(pivot, price_as_of)
    nodes = _key_nodes(pivot, price_as_of)

    indices = []
    for code, name in BROAD_INDEX_MAP.items():
        snapshot = BROAD_INDEX_SNAPSHOT[code]
        indices.append({
            "code": code,
            "name": name,
            "pe": snapshot["pe"],
            "earnings_yield": snapshot["earnings_yield"],
            "drawdown": snapshot["drawdown"],
            "ytd": snapshot["ytd"],
            "annualized": snapshot["annualized"],
            "volatility": snapshot["volatility"],
            "start": snapshot["start"],
        })

    sources = {
        code: sorted(set(
            str(value) for value in frame.loc[frame["index_code"] == code, "src"].dropna()
        ))
        for code in BROAD_INDEX_MAP
    }

    return {
        "snapshot_date": BROAD_INDEX_SNAPSHOT_DATE,
        "price_as_of": price_as_of.isoformat(),
        "required_sentence": REQUIRED_SENTENCE,
        "summary": {
            "above_ma200": BROAD_SUMMARY["above_ma200"],
            "total": BROAD_SUMMARY["total"],
            "avg_drawdown": BROAD_SUMMARY["avg_drawdown"],
            "avg_drawdown_5y": BROAD_SUMMARY["avg_drawdown_5y"],
        },
        "erp": {
            **BROAD_ERP_SNAPSHOT,
            "note": "ERP 为市场级股债溢价，不是各指数 PE 分位；样本仅 2016-11-15 至 2026-11-12，不是 21 年。",
        },
        "indices": indices,
        "normalized": normalized,
        "momentum": momentum,
        "drawdown_history": drawdown,
        "key_nodes": nodes,
        "ranges": RANGE_LABELS,
        "insights": [
            "6 个宽基全部位于 200 日均线下方，平均距各自历史高点 -35.7%：位置偏冷，趋势仍弱。",
            "分化极大：中证红利距历史高点仅 -11.5%、PE 9.14、YTD +0.1%；中证500 -44.0%、PE 27.54；创业板指 -44.5%、PE 44.09；科创50 PE 94.15 而自 2020 年年化 3.36%。",
            "ERP 3.5698%，处近 10 年 78.9% 分位；历史上 ERP 高位多与中长期底部区域同时出现，但它是温度计不是秒表。",
            "21.9 年、5 轮牛熊：高点下移、波动收敛，只有拉到全周期并用对数轴才看得清。",
            "PE 是当前 TTM 绝对值，不是各指数自身历史估值分位；各指数日线起点不同，归一化后的绝对高度不可横向比较；ERP 只覆盖 2016-11 以来。",
        ],
        "analogy": (
            "把这张图当成一份体检报告：PE 是体温，回撤是血压，200 日均线是免疫线，"
            "ERP 是性价比温度计，波动率是心率；它描述当前状态，不开处方。"
        ),
        "caveats": [
            "PE 为当前 TTM 绝对值，不等于历史估值分位；不得据此说“处于历史低位”。",
            "ERP 样本区间 2016-11-15 至 2026-11-12，只有近 10 年，不是 21 年。",
            "归一化曲线各指数起点不同，绝对高度不能横向比较；对数刻度等距代表等倍数而非等点数。",
            "科创50、创业板指日线起点较短，回撤不能与 2005 年起算的指数做等长对比。",
            "价格、回撤、动量为本地指数库计算；PE/ERP 为文件硬数据快照，两类数字不要混用。",
            "数据截止 2026-11-13；当前快照来源腾讯自选股行情；ERP 样本见上。",
        ],
        "disclaimer": DISCLAIMER,
        "meta": {
            "codes": list(BROAD_INDEX_MAP),
            "index_names": dict(BROAD_INDEX_MAP),
            "sources": sources,
            "snapshot_date": BROAD_INDEX_SNAPSHOT_DATE,
            "price_as_of": price_as_of.isoformat(),
            "price_range": {
                "start": pivot.index[0].date().isoformat(),
                "end": price_as_of.isoformat(),
            },
            "normalized_frequency": "monthly",
        },
    }


__all__ = ["build_broad_valuation", "DISCLAIMER", "REQUIRED_SENTENCE"]
