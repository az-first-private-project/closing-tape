"""
市场温度计（V6 T3）
====================
把六个既有指标归一化后加权成一个 0–100 的「温度分」，回答"现在市场能不能做"。

设计约束（任务书 T3）：
  - 只读本地库（`fact_stock_daily` / `fact_feature_daily` / `market_indices`），
    不新增数据源；
  - 归一化是 `constants.py` 里显式、单调的分段映射，**不做逐日 min-max 缩放**，
    保证温度分跨日可比；
  - 跌停家数与指数波动率是降温项（断点已把数值反转成得分）；
  - 任一子项缺数据 → 该项为 null，**禁止用 0 代入**；关键子项缺失
    （宽度 / MA20 趋势）→ `confidence="insufficient"`、总分为 null；
  - 停牌股不参与宽度统计；涨跌停直接用采集层已算好的 `is_limit_up/down`。

输出落 `market_temperature_daily`（每日一行，幂等 upsert）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    BENCHMARK_INDEX,
    TEMP_AMOUNT_LOOKBACK_DAYS,
    TEMP_AMOUNT_MA_DAYS,
    TEMP_AMOUNT_MIN_DAYS,
    TEMP_BREAKPOINTS,
    TEMP_COMPONENT_META,
    TEMP_COMPONENT_ORDER,
    TEMP_COOLING_COMPONENTS,
    TEMP_CRITICAL_COMPONENTS,
    TEMP_INDEX_VOL_DAYS,
    TEMP_MIN_COMPONENTS,
    TEMP_MIN_FEATURE_COVERAGE,
    TEMP_WEIGHTS,
)
from app.core.logging import get_logger
from app.db.models.fact import (
    FactFeatureDaily,
    FactStockDaily,
    MarketIndex,
    MarketTemperatureDaily,
)
from app.engine._db import session_ctx, to_jsonable
from app.engine.breadth import load_breadth

logger = get_logger(__name__)

CONFIDENCE_OK = "ok"
CONFIDENCE_PARTIAL = "partial"
CONFIDENCE_INSUFFICIENT = "insufficient"


# ============================================================
# 结果对象
# ============================================================

@dataclass
class TemperatureResult:
    """一次温度计算的全部产物（可直接 upsert 成 `market_temperature_daily` 一行）。"""

    trade_date: date
    temp_score: float | None
    confidence: str
    components: dict[str, Any]
    available_count: int
    component_total: int = len(TEMP_COMPONENT_ORDER)
    up_count: int | None = None
    down_count: int | None = None
    flat_count: int | None = None
    total_count: int | None = None
    limit_up_count: int | None = None
    limit_down_count: int | None = None
    median_pct: float | None = None
    above_ma20_ratio: float | None = None
    amount: float | None = None
    amount_vs_ma20: float | None = None
    index_vol_annual: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "up_count": self.up_count,
            "down_count": self.down_count,
            "flat_count": self.flat_count,
            "total_count": self.total_count,
            "limit_up_count": self.limit_up_count,
            "limit_down_count": self.limit_down_count,
            "median_pct": self.median_pct,
            "above_ma20_ratio": self.above_ma20_ratio,
            "amount": self.amount,
            "amount_vs_ma20": self.amount_vs_ma20,
            "index_vol_annual": self.index_vol_annual,
            "temp_score": self.temp_score,
            "confidence": self.confidence,
            "components": to_jsonable(self.components),
            "src": "engine",
            "updated_at": datetime.now(timezone.utc),
        }


# ============================================================
# 归一化
# ============================================================

def piecewise_normalize(
    value: float | None,
    breakpoints: tuple[tuple[float, float], ...],
) -> float | None:
    """固定断点的分段线性映射（原始值 → 0–100 分）。

    - 断点必须按原始值单调排列；得分单调即可（降温项是递减断点）；
    - 超出两端直接取端点得分，**不外推**，避免极端值产生负分或 >100；
    - value 为 None → None（缺数据不代入 0）。
    """
    if value is None or not math.isfinite(float(value)):
        return None
    x = float(value)
    points = breakpoints
    if x <= points[0][0]:
        return float(points[0][1])
    if x >= points[-1][0]:
        return float(points[-1][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y1)
            ratio = (x - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return None


def _component(
    key: str,
    *,
    raw: float | None,
    score: float | None,
    note: str,
) -> dict[str, Any]:
    """单个子项的展示/落库结构。score 为 None 表示该项缺数据。"""
    meta = TEMP_COMPONENT_META[key]
    return {
        "key": key,
        "label": meta["label"],
        "unit": meta["unit"],
        "raw": raw,
        "score": round(score, 1) if score is not None else None,
        "weight": TEMP_WEIGHTS[key],
        "direction": -1 if key in TEMP_COOLING_COMPONENTS else 1,
        "available": score is not None,
        "note": note,
    }


def combine_components(
    items: list[dict[str, Any]],
) -> tuple[float | None, str, int, list[str]]:
    """把六个子项合成温度分（纯函数，便于单测）。

    规则：
      - 关键子项（上涨占比 / 站上 MA20）缺失 → insufficient，总分 None；
      - 可用子项 < TEMP_MIN_COMPONENTS → insufficient；
      - 其余缺项 → partial：权重只在可用子项间重新归一，**不给缺项补 0**；
      - 六项齐全 → ok。

    返回 (temp_score, confidence, available_count, critical_missing)。
    """
    available = [item for item in items if item.get("score") is not None]
    by_key = {item.get("key"): item for item in items}
    critical_missing = [
        key for key in TEMP_CRITICAL_COMPONENTS
        if by_key.get(key, {}).get("score") is None
    ]

    if critical_missing or len(available) < TEMP_MIN_COMPONENTS:
        return None, CONFIDENCE_INSUFFICIENT, len(available), critical_missing

    weight_sum = sum(float(item["weight"]) for item in available)
    score = round(
        sum(float(item["score"]) * float(item["weight"]) for item in available)
        / weight_sum,
        1,
    )
    confidence = (
        CONFIDENCE_OK if len(available) == len(items) else CONFIDENCE_PARTIAL
    )
    return score, confidence, len(available), critical_missing


# ============================================================
# 数据读取
# ============================================================

async def _load_ma20_breadth(
    session: AsyncSession, trade_date: date
) -> tuple[float | None, int, int]:
    """站上 MA20 的比例。

    返回 (ratio, above, joined)：
      - ratio: 站上家数 / 有 MA20 特征的有效家数；样本为空 → None
      - joined: 当日有 MA20 特征的家数（覆盖率由调用方除以有效交易家数）
    """
    joined_rows = (await session.execute(
        select(FactStockDaily.close, FactFeatureDaily.ma20)
        .select_from(FactStockDaily)
        .join(
            FactFeatureDaily,
            and_(
                FactFeatureDaily.code == FactStockDaily.code,
                FactFeatureDaily.trade_date == FactStockDaily.trade_date,
            ),
        )
        .where(
            FactStockDaily.trade_date == trade_date,
            FactStockDaily.is_suspended.is_(False),
            FactStockDaily.close.isnot(None),
            FactFeatureDaily.ma20.isnot(None),
        )
    )).all()

    above = sum(
        1 for close, ma20 in joined_rows
        if close is not None and ma20 is not None and float(close) > float(ma20)
    )
    joined = len(joined_rows)
    return (round(above / joined, 4) if joined else None, above, joined)


async def _load_amount_ratio(
    session: AsyncSession, trade_date: date
) -> tuple[float | None, float | None, float | None, int]:
    """量能比 = 当日全市场成交额 / 前 20 个交易日均值。

    返回 (today_amount, ratio, prior_mean, prior_days)。
    """
    start = trade_date - timedelta(days=TEMP_AMOUNT_LOOKBACK_DAYS)
    rows = (await session.execute(
        select(FactStockDaily.trade_date, func.sum(FactStockDaily.amount))
        .where(
            FactStockDaily.trade_date >= start,
            FactStockDaily.trade_date <= trade_date,
            FactStockDaily.is_suspended.is_(False),
            FactStockDaily.amount.isnot(None),
        )
        .group_by(FactStockDaily.trade_date)
        .order_by(FactStockDaily.trade_date.desc())
        .limit(TEMP_AMOUNT_MA_DAYS + 1)
    )).all()

    today_amount: float | None = None
    prior: list[float] = []
    for row_date, amount in rows:
        if amount is None:
            continue
        value = float(amount)
        if row_date == trade_date:
            today_amount = value
        elif row_date < trade_date:
            prior.append(value)

    prior = prior[:TEMP_AMOUNT_MA_DAYS]
    if today_amount is None or len(prior) < TEMP_AMOUNT_MIN_DAYS:
        return today_amount, None, None, len(prior)

    mean = float(np.mean(prior))
    if mean <= 0:
        return today_amount, None, None, len(prior)
    return today_amount, round(today_amount / mean, 3), mean, len(prior)


async def _load_index_volatility(session: AsyncSession, trade_date: date) -> tuple[float | None, int]:
    """沪深300 近 20 个交易日涨跌幅的年化波动率（百分数）。"""
    rows = (await session.execute(
        select(MarketIndex.trade_date, MarketIndex.pct_chg)
        .where(
            MarketIndex.index_code == BENCHMARK_INDEX,
            MarketIndex.trade_date <= trade_date,
        )
        .order_by(MarketIndex.trade_date.desc())
        .limit(TEMP_INDEX_VOL_DAYS + 10)
    )).all()

    values: list[float] = []
    for _row_date, pct_chg in rows:
        if pct_chg is None:
            continue
        values.append(float(pct_chg))
        if len(values) >= TEMP_INDEX_VOL_DAYS:
            break

    if len(values) < TEMP_INDEX_VOL_DAYS:
        return None, len(values)
    # 与 pandas rolling.std() 默认一致：样本标准差（ddof=1）
    vol = float(np.std(values, ddof=1)) * math.sqrt(252)
    return round(vol, 3), len(values)


# ============================================================
# 主计算
# ============================================================

async def compute_temperature(
    session: AsyncSession, trade_date: date
) -> TemperatureResult:
    """计算某交易日的市场温度（只读，不落库）。"""
    breadth = await load_breadth(session, trade_date, exclude_suspended=True)
    valid_total = int(breadth["total"])

    (
        ma20_ratio,
        ma20_above,
        ma20_joined,
    ) = await _load_ma20_breadth(session, trade_date)
    if valid_total > 0:
        ma20_coverage = ma20_joined / valid_total
    else:
        ma20_coverage = 0.0

    today_amount, amount_ratio, prior_mean, prior_days = await _load_amount_ratio(
        session, trade_date
    )
    index_vol, index_vol_days = await _load_index_volatility(session, trade_date)

    items: list[dict[str, Any]] = []

    # 1) 上涨占比
    up_ratio = round(breadth["up"] / valid_total, 4) if valid_total > 0 else None
    items.append(_component(
        "up_ratio",
        raw=up_ratio,
        score=piecewise_normalize(up_ratio, TEMP_BREAKPOINTS["up_ratio"]),
        note=(
            f"上涨 {breadth['up']} / 有效 {valid_total}（已剔除停牌）"
            if valid_total > 0 else "当日无有效个股行情"
        ),
    ))

    # 2) 涨停家数
    limit_up = int(breadth["limit_up"]) if valid_total > 0 else None
    items.append(_component(
        "limit_up",
        raw=limit_up,
        score=piecewise_normalize(limit_up, TEMP_BREAKPOINTS["limit_up"]),
        note=f"{limit_up} 家" if limit_up is not None else "当日无有效个股行情",
    ))

    # 3) 跌停家数（降温项）
    limit_down = int(breadth["limit_down"]) if valid_total > 0 else None
    items.append(_component(
        "limit_down",
        raw=limit_down,
        score=piecewise_normalize(limit_down, TEMP_BREAKPOINTS["limit_down"]),
        note=(
            f"{limit_down} 家（降温项）" if limit_down is not None
            else "当日无有效个股行情"
        ),
    ))

    # 4) 站上 MA20 比例（特征覆盖率不足视为缺失）
    if ma20_ratio is not None and ma20_coverage >= TEMP_MIN_FEATURE_COVERAGE:
        above_raw = ma20_ratio
        above_note = (
            f"站上 {ma20_above} / 特征有效 {ma20_joined}"
            f"（覆盖率 {ma20_coverage:.1%}）"
        )
    else:
        above_raw = None
        above_note = (
            f"MA20 覆盖率 {ma20_coverage:.1%} < {TEMP_MIN_FEATURE_COVERAGE:.0%}，数据不足"
            if valid_total > 0 else "当日无有效个股行情"
        )
    items.append(_component(
        "above_ma20",
        raw=above_raw,
        score=piecewise_normalize(above_raw, TEMP_BREAKPOINTS["above_ma20"]),
        note=above_note,
    ))

    # 5) 量能比
    items.append(_component(
        "amount_ratio",
        raw=amount_ratio,
        score=piecewise_normalize(amount_ratio, TEMP_BREAKPOINTS["amount_ratio"]),
        note=(
            f"当日 {today_amount / 1e8:.2f} 亿 / 前 {prior_days} 日均 "
            f"{prior_mean / 1e8:.2f} 亿"
            if amount_ratio is not None and today_amount is not None and prior_mean
            else f"历史不足 {prior_days} 天（需 ≥{TEMP_AMOUNT_MIN_DAYS}）"
        ),
    ))

    # 6) 指数波动率（降温项）
    items.append(_component(
        "index_vol",
        raw=index_vol,
        score=piecewise_normalize(index_vol, TEMP_BREAKPOINTS["index_vol"]),
        note=(
            f"{index_vol:.2f}%（{index_vol_days} 日年化，降温项）"
            if index_vol is not None
            else f"沪深300 历史不足 {index_vol_days} 天（需 {TEMP_INDEX_VOL_DAYS}）"
        ),
    ))

    temp_score, confidence, available_count, _critical_missing = combine_components(items)

    components = {
        "available_count": available_count,
        "total_count": len(items),
        "items": items,
    }

    result = TemperatureResult(
        trade_date=trade_date,
        temp_score=temp_score,
        confidence=confidence,
        components=components,
        available_count=available_count,
        up_count=int(breadth["up"]) if valid_total > 0 else None,
        down_count=int(breadth["down"]) if valid_total > 0 else None,
        flat_count=int(breadth["flat"]) if valid_total > 0 else None,
        total_count=valid_total if valid_total > 0 else None,
        limit_up_count=limit_up,
        limit_down_count=limit_down,
        median_pct=breadth["median_pct"] if valid_total > 0 else None,
        above_ma20_ratio=above_raw,
        amount=round(today_amount, 2) if today_amount is not None else None,
        amount_vs_ma20=amount_ratio,
        index_vol_annual=index_vol,
    )
    logger.info(
        "温度计算完成 %s: score=%s confidence=%s available=%d/%d",
        trade_date, temp_score, confidence, available_count, len(items),
    )
    return result


async def save_temperature(session: AsyncSession, result: TemperatureResult) -> int:
    """幂等 upsert 一行（外部 session 下只执行 SQL，由调用方提交）。"""
    row = result.to_row()
    stmt = pg_insert(MarketTemperatureDaily).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketTemperatureDaily.trade_date],
        set_={key: value for key, value in row.items() if key != "trade_date"},
    )
    await session.execute(stmt)
    return 1


async def compute_and_save_temperature(
    trade_date: date,
    db: AsyncSession | None = None,
) -> dict:
    """计算并落库（pipeline / 回填脚本的统一入口）。"""
    async with session_ctx(db) as session:
        result = await compute_temperature(session, trade_date)
        await save_temperature(session, result)

    return {
        "rows": 1,
        "trade_date": trade_date.isoformat(),
        "temp_score": result.temp_score,
        "confidence": result.confidence,
        "available_components": result.available_count,
        "detail": {
            "temp_score": result.temp_score,
            "confidence": result.confidence,
            "available_components": result.available_count,
        },
    }


__all__ = [
    "TemperatureResult",
    "combine_components",
    "compute_temperature",
    "compute_and_save_temperature",
    "save_temperature",
    "piecewise_normalize",
]
