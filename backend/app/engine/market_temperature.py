"""市场温度计（V7 T1 新口径）
==============================
7 个子项各自取 3 年滚动分位，加权成 0–100 温度分：

    temp = 100 * (
      0.20*换手率分位 + 0.20*成交额分位 + 0.15*涨停家数分位
    + 0.15*融资买入占比分位 + 0.10*60日新高占比分位
    + 0.10*RSI归一 + 0.10*(1-20日波动分位) )

- 缺子项：剔除后组内重新归一，**不补 0**；
- 换手率/成交额是关键子项，缺失则 `insufficient`、总分为 null；
- 五档：冰点(1) / 清冷(2) / 平稳(3) / 温热(4) / 沸腾(5)；
- 一周序列的均值/极值/斜率与情绪结论由 `weekly_summary` 生成。

输入来自 `fact_market_daily`，所有窗口只用 T 日及以前的数据。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    MARKET_PERCENTILE_MIN_PERIODS,
    MARKET_PERCENTILE_WINDOW,
    TEMP7_COMPONENT_META,
    TEMP7_CRITICAL_COMPONENTS,
    TEMP7_MIN_COMPONENTS,
    TEMP7_WEIGHTS,
    TEMP_BANDS,
)
from app.core.logging import get_logger
from app.db.models.fact import FactMarketDaily, MarketTemperatureDaily
from app.engine._db import session_ctx, to_jsonable
from app.engine.market_regime import rolling_percentile

logger = get_logger(__name__)

CONFIDENCE_OK = "ok"
CONFIDENCE_PARTIAL = "partial"
CONFIDENCE_INSUFFICIENT = "insufficient"

_RAW_COLUMNS = (
    "trade_date", "market_turnover", "amount", "limit_up_cnt",
    "margin_buy_ratio", "new_high_60_ratio", "rsi14", "rv20",
    "up_count", "down_count", "flat_count", "total_count",
    "limit_down_cnt", "amount_vs_ma20",
)


# ============================================================
# 档位
# ============================================================

def temperature_band(score: float | None) -> tuple[str, int] | None:
    if score is None or not np.isfinite(score):
        return None
    for upper, label, rank in TEMP_BANDS:
        if score <= upper:
            return label, rank
    return TEMP_BANDS[-1][1], TEMP_BANDS[-1][2]


def _finite(value: Any) -> bool:
    try:
        return value is not None and np.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _component(
    key: str, *, raw: float | None, score: float | None, note: str
) -> dict[str, Any]:
    meta = TEMP7_COMPONENT_META[key]
    return {
        "key": key,
        "label": meta["label"],
        "unit": meta["unit"],
        "raw": round(float(raw), 4) if _finite(raw) else None,
        "score": round(float(score), 1) if _finite(score) else None,
        "weight": TEMP7_WEIGHTS[key],
        "direction": -1 if key == "rv20_inv" else 1,
        "available": _finite(score),
        "note": note,
    }


def combine_components(items: list[dict[str, Any]]) -> tuple[int | None, str, int]:
    """合成温度分：缺项权重重归一；关键项缺失 → insufficient。"""
    available = [item for item in items if item.get("available")]
    by_key = {item["key"]: item for item in items}
    critical_missing = [
        key for key in TEMP7_CRITICAL_COMPONENTS
        if not by_key.get(key, {}).get("available")
    ]
    if critical_missing or len(available) < TEMP7_MIN_COMPONENTS:
        return None, CONFIDENCE_INSUFFICIENT, len(available)
    weight_sum = sum(float(item["weight"]) for item in available)
    score = round(
        sum(float(item["score"]) * float(item["weight"]) for item in available) / weight_sum
    )
    confidence = CONFIDENCE_OK if len(available) == len(items) else CONFIDENCE_PARTIAL
    return int(max(0, min(100, score))), confidence, len(available)


def _pct(series: pd.Series, index: int) -> float | None:
    value = series.iloc[index]
    return float(value) if _finite(value) else None


def compute_temperature_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """输入全历史原始因子，输出每日温度分（前 250 日为空）。"""
    columns = [
        "trade_date", "temp_score", "confidence",
        "available_count", "total_count", "components",
    ]
    if raw.empty:
        return pd.DataFrame(columns=columns)

    frame = raw.sort_values("trade_date").reset_index(drop=True)
    percentiles = {
        key: rolling_percentile(
            pd.to_numeric(frame[column], errors="coerce"),
            MARKET_PERCENTILE_WINDOW,
            MARKET_PERCENTILE_MIN_PERIODS,
        )
        for key, column in (
            ("turnover", "market_turnover"),
            ("amount", "amount"),
            ("limit_up", "limit_up_cnt"),
            ("margin_buy", "margin_buy_ratio"),
            ("new_high_60", "new_high_60_ratio"),
            ("rv20", "rv20"),
        )
    }

    records: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        rsi_value = row.get("rsi14")
        rsi_norm = (
            float(np.clip((float(rsi_value) - 30) / 40, 0, 1))
            if _finite(rsi_value) else None
        )
        rv_pct = _pct(percentiles["rv20"], index)
        turnover_pct = _pct(percentiles["turnover"], index)
        amount_pct = _pct(percentiles["amount"], index)
        limit_up_pct = _pct(percentiles["limit_up"], index)
        margin_pct = _pct(percentiles["margin_buy"], index)
        high_pct = _pct(percentiles["new_high_60"], index)

        items = [
            _component(
                "turnover", raw=row.get("market_turnover"),
                score=turnover_pct * 100 if turnover_pct is not None else None,
                note=f"全市场换手率 {float(row['market_turnover']):.2f}%"
                if _finite(row.get("market_turnover")) else "换手率缺失",
            ),
            _component(
                "amount", raw=row.get("amount"),
                score=amount_pct * 100 if amount_pct is not None else None,
                note=f"成交额 {float(row['amount']) / 1e12:.2f} 万亿"
                if _finite(row.get("amount")) else "成交额缺失",
            ),
            _component(
                "limit_up", raw=row.get("limit_up_cnt"),
                score=limit_up_pct * 100 if limit_up_pct is not None else None,
                note=f"{int(row['limit_up_cnt'])} 家涨停"
                if _finite(row.get("limit_up_cnt")) else "涨停数缺失",
            ),
            _component(
                "margin_buy", raw=row.get("margin_buy_ratio"),
                score=margin_pct * 100 if margin_pct is not None else None,
                note=f"融资买入占成交额 {float(row['margin_buy_ratio']) * 100:.1f}%"
                if _finite(row.get("margin_buy_ratio")) else "融资买入数据未覆盖",
            ),
            _component(
                "new_high_60", raw=row.get("new_high_60_ratio"),
                score=high_pct * 100 if high_pct is not None else None,
                note=f"创60日新高 {float(row['new_high_60_ratio']) * 100:.1f}%"
                if _finite(row.get("new_high_60_ratio")) else "新高占比缺失",
            ),
            _component(
                "rsi14_norm", raw=rsi_value,
                score=rsi_norm * 100 if rsi_norm is not None else None,
                note=f"指数 RSI {float(rsi_value):.1f}" if _finite(rsi_value) else "RSI 缺失",
            ),
            _component(
                "rv20_inv", raw=row.get("rv20"),
                score=(1 - rv_pct) * 100 if rv_pct is not None else None,
                note=f"20日年化波动 {float(row['rv20']):.1f}%"
                if _finite(row.get("rv20")) else "波动率缺失",
            ),
        ]
        score, confidence, available = combine_components(items)
        records.append({
            "trade_date": row["trade_date"],
            "temp_score": score,
            "confidence": confidence,
            "available_count": available,
            "total_count": len(items),
            "components": {
                "available_count": available,
                "total_count": len(items),
                "items": items,
            },
        })
    return pd.DataFrame(records, columns=columns)


def _to_row(record: dict[str, Any], raw_row: pd.Series | None) -> dict[str, Any]:
    def _int(name: str) -> int | None:
        if raw_row is None or not _finite(raw_row.get(name)):
            return None
        return int(float(raw_row[name]))

    return {
        "trade_date": record["trade_date"],
        "up_count": _int("up_count"),
        "down_count": _int("down_count"),
        "flat_count": _int("flat_count"),
        "total_count": _int("total_count"),
        "limit_up_count": _int("limit_up_cnt"),
        "limit_down_count": _int("limit_down_cnt"),
        "median_pct": None,
        "above_ma20_ratio": None,
        "amount": float(raw_row["amount"]) if raw_row is not None and _finite(raw_row.get("amount")) else None,
        "amount_vs_ma20": float(raw_row["amount_vs_ma20"]) if raw_row is not None and _finite(raw_row.get("amount_vs_ma20")) else None,
        "index_vol_annual": float(raw_row["rv20"]) if raw_row is not None and _finite(raw_row.get("rv20")) else None,
        "temp_score": record["temp_score"],
        "confidence": record["confidence"],
        "components": to_jsonable(record["components"]),
        "src": "engine",
    }


def temperature_rows(raw: pd.DataFrame) -> list[dict[str, Any]]:
    """把原始因子表转成可落库的温度行（批量回补与单日共用）。"""
    computed = compute_temperature_frame(raw)
    if computed.empty:
        return []
    # computed.total_count 是子项个数，与原始因子的 total_count（股票数）重名
    merged = computed.drop(columns=["total_count"], errors="ignore").merge(
        raw, on="trade_date", how="left"
    )
    return [_to_row(record, record) for record in merged.to_dict("records")]


async def compute_temperature(
    session: AsyncSession, trade_date: date
) -> dict[str, Any]:
    """计算某交易日温度（只读，不落库）。"""
    rows = (await session.execute(
        select(FactMarketDaily)
        .where(FactMarketDaily.trade_date <= trade_date)
        .order_by(FactMarketDaily.trade_date)
    )).scalars().all()
    if not rows:
        raise ValueError(f"{trade_date} 没有市场因子数据")
    raw = pd.DataFrame([{
        column: getattr(row, column) for column in _RAW_COLUMNS
    } for row in rows])
    for row in temperature_rows(raw):
        if row["trade_date"] == trade_date:
            return row
    raise ValueError(f"{trade_date} 不在因子序列中")


async def save_temperature(session: AsyncSession, row: dict[str, Any]) -> int:
    stmt = pg_insert(MarketTemperatureDaily).values(**row)
    update_columns = {key: value for key, value in row.items() if key != "trade_date"}
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketTemperatureDaily.trade_date],
        set_={key: getattr(stmt.excluded, key) for key in update_columns},
    )
    await session.execute(stmt)
    return 1


async def compute_and_save_temperature(
    trade_date: date, db: AsyncSession | None = None
) -> dict:
    async with session_ctx(db) as session:
        row = await compute_temperature(session, trade_date)
        await save_temperature(session, row)
    return {
        "rows": 1,
        "trade_date": trade_date.isoformat(),
        "temp_score": row["temp_score"],
        "confidence": row["confidence"],
        "available_components": row["components"].get("available_count"),
        "detail": {
            "temp_score": row["temp_score"],
            "confidence": row["confidence"],
        },
    }


# ============================================================
# 一周序列与情绪结论
# ============================================================

def weekly_summary(series: list[dict[str, Any]]) -> dict[str, Any]:
    """输入按日期升序的最近 5 个交易日温度点，输出周统计与情绪结论。"""
    points = []
    for item in series:
        temp = item.get("temp_score")
        if not _finite(temp):
            continue
        band = temperature_band(float(temp)) or ("数据不足", 0)
        trade_date = item.get("trade_date")
        points.append({
            "date": trade_date if isinstance(trade_date, str) else trade_date.isoformat(),
            "temp": int(round(float(temp))),
            "label": band[0],
            "score": band[1],
        })
    points = points[-5:]

    if not points:
        return {
            "current_temp": None, "current_label": "数据不足", "current_score": None,
            "series": [], "week_avg": None, "week_max": None, "week_min": None,
            "week_slope": None, "week_trend": "数据不足",
            "emotion_conclusion": "数据积累中，暂不判断市场情绪", "extreme_note": "",
        }

    temps = [point["temp"] for point in points]
    current = points[-1]
    labels = [point["label"] for point in points]
    slope = float(np.polyfit(np.arange(len(temps)), temps, 1)[0]) if len(temps) >= 2 else 0.0
    if slope > 1.5:
        week_trend = "升温"
    elif slope < -1.5:
        week_trend = "降温"
    else:
        week_trend = "走平"

    band_order = {label: rank for _, label, rank in TEMP_BANDS}
    start_rank = band_order.get(labels[0], 0)
    current_rank = band_order.get(current["label"], 0)
    if slope > 1.5 and current_rank > start_rank:
        emotion = "情绪持续回暖"
    elif slope > 0.5:
        emotion = "情绪缓慢转暖"
    elif slope < -1.5 and current_rank < start_rank:
        emotion = "情绪持续降温"
    elif slope < -0.5:
        emotion = "情绪缓慢转冷"
    else:
        emotion = "情绪基本平稳"

    extreme_note = ""
    if current["label"] == "沸腾":
        extreme_note = "交投很热，追高容易拥挤"
    elif current["label"] == "冰点":
        extreme_note = "情绪低迷，观望居多"

    return {
        "current_temp": current["temp"],
        "current_label": current["label"],
        "current_score": current["score"],
        "series": points,
        "week_avg": int(round(float(np.mean(temps)))),
        "week_max": int(max(temps)),
        "week_min": int(min(temps)),
        "week_slope": round(slope, 2),
        "week_trend": week_trend,
        "emotion_conclusion": (
            f"当前{current['label']}，一周从{labels[0]}到{current['label']}，{emotion}"
        ),
        "extreme_note": extreme_note,
    }


__all__ = [
    "compute_temperature",
    "compute_temperature_frame",
    "temperature_rows",
    "compute_and_save_temperature",
    "save_temperature",
    "temperature_band",
    "weekly_summary",
]
