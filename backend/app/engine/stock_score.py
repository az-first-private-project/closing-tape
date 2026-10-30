"""
个股综合评分（方案 C2 + C3 + C4）
==================================
流程：
  1. 取某交易日的**横截面**（个股日线估值字段 + 预计算技术指标）；
  2. 六因子各算 0–1 分位（`engine/factors.py`，已做缺失/负值处理）；
  3. 分位 → 0–10 分：`score = 10 × percentile`，保留两位小数；
  4. 综合分按配置权重加权：value 0.20 / quality 0.15 / momentum 0.25 /
     volatility 0.15 / liquidity 0.15 / sentiment 0.10（见 `FACTOR_WEIGHTS`，可调）；
  5. upsert 到 `fact_stock_score_daily`，`detail` JSONB 保留**原始因子值 + 分位 +
     所用权重**，保证任何一只股票的分数都能追溯（方案 C4 可归因要求）。

口径约定：
  - 所有 `*_score` 均为 0–10，`round(..., 2)`；
  - 横截面先剔除**停牌股**（无成交、指标失真），ST 股保留（由策略层按需过滤）；
  - 不加行业中性化：行业内 z-score 属于选股策略的可选步骤，
    见 `engine/cross_section.industry_neutral_zscore`；直接全市场分位保持与
    "板块/个股统一口径"一致；
  - 缺数据的因子给中性 0.5 分位（5.0 分），不因数据缺失被系统性判为最差。
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dim import DimStock
from app.db.models.fact import FactFeatureDaily, FactStockDaily, FactStockScoreDaily
from app.engine._db import commit_or_flush, session_ctx, to_jsonable
from app.engine.factors import (
    composite_factor,
    liquidity_factor,
    momentum_factor,
    quality_factor,
    sentiment_factor,
    value_factor,
    volatility_factor,
)

logger = logging.getLogger(__name__)

# 六因子权重（配置化、可调）。
# 设计理由：A 股日频选股中动量与量价最有效，故 momentum 25% 最高；
# 价值/质量合计 35% 提供安全边际；波动 15% 控制回撤；情绪只作辅助 10%。
FACTOR_WEIGHTS: dict[str, float] = {
    "value": 0.20,
    "quality": 0.15,
    "momentum": 0.25,
    "volatility": 0.15,
    "liquidity": 0.15,
    "sentiment": 0.10,
}

# 落库分数字段（与 FactStockScoreDaily 列名一致）
SCORE_COLUMNS = [
    "value_score", "quality_score", "momentum_score", "volatility_score",
    "liquidity_score", "sentiment_score", "composite_score",
]

# 每批 upsert 行数（同时是自建 session 的 commit 粒度）
WRITE_CHUNK_ROWS = 800

# 需要写进 detail 的原始因子输入列
_RAW_INPUT_COLUMNS = (
    "pe_ttm", "pb", "ps_ttm", "dv_ttm",
    "momentum_20", "momentum_60", "volatility_20", "amount_ma20", "amount",
)


# ============================================================
# 纯函数：横截面 → 分数
# ============================================================

def compute_factor_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """横截面 → 六因子分位矩阵（0–1，列为因子名）。

    纯函数；输入列见 `engine/factors` 各函数说明。缺失列会被因子函数降级为
    中性 0.5，不会抛异常（数据未就绪时仍能给出一致的中性评分）。
    """
    return pd.DataFrame({
        "value": value_factor(df),
        "quality": quality_factor(df),
        "momentum": momentum_factor(df),
        "volatility": volatility_factor(df),
        "liquidity": liquidity_factor(df),
        "sentiment": sentiment_factor(df),
    }, index=df.index)


def _scores_from_percentiles(percentiles: pd.DataFrame) -> pd.DataFrame:
    """因子分位 → 0–10 分 + 加权综合分（纯函数）。

    - 单因子分：`10 × percentile`；
    - 综合分：先按 `FACTOR_WEIGHTS` 加权分位，再 ×10；
    - 全部 `round(..., 2)` 并裁剪到 [0, 10]。
    """
    scores: dict[str, pd.Series] = {}
    for factor in FACTOR_WEIGHTS:
        percentile = percentiles[factor].fillna(0.5).astype("float64")
        scores[f"{factor}_score"] = (10.0 * percentile).round(2)

    weighted = composite_factor(
        {factor: percentiles[factor] for factor in FACTOR_WEIGHTS},
        FACTOR_WEIGHTS,
    )
    scores["composite_score"] = (10.0 * weighted).round(2)

    result = pd.DataFrame(scores, index=percentiles.index)
    return result.clip(lower=0.0, upper=10.0)


def score_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """对某交易日横截面打分（**纯函数**，不触库，供单测与离线研究复用）。

    参数:
        df: 一行一只股票的横截面 DataFrame；可选列：
            pe_ttm / pb / ps_ttm / dv_ttm / momentum_20 / momentum_60 /
            volatility_20 / amount_ma20（或 amount）/ news_sentiment / industry_l1
            缺列时对应因子退化为中性分位 0.5。

    返回:
        与输入同索引的 DataFrame，列 = `[value_score, quality_score,
        momentum_score, volatility_score, liquidity_score, sentiment_score,
        composite_score]`，取值 0–10（两位小数）。
    """
    percentiles = compute_factor_percentiles(df)
    return _scores_from_percentiles(percentiles)


# ============================================================
# 数据库读写
# ============================================================

async def _load_cross_section(session: AsyncSession,
                              trade_date: date) -> pd.DataFrame:
    """读取某交易日全市场横截面（日线估值 + 预计算技术指标 + 行业）。"""
    stmt = (
        select(
            FactStockDaily.code,
            DimStock.name,
            DimStock.industry_l1,
            FactStockDaily.pe_ttm,
            FactStockDaily.pb,
            FactStockDaily.ps_ttm,
            FactStockDaily.dv_ttm,
            FactStockDaily.amount,
            FactFeatureDaily.momentum_20,
            FactFeatureDaily.momentum_60,
            FactFeatureDaily.volatility_20,
            FactFeatureDaily.amount_ma20,
        )
        .select_from(FactStockDaily)
        .outerjoin(DimStock, DimStock.code == FactStockDaily.code)
        .outerjoin(
            FactFeatureDaily,
            and_(
                FactFeatureDaily.code == FactStockDaily.code,
                FactFeatureDaily.trade_date == FactStockDaily.trade_date,
            ),
        )
        .where(
            FactStockDaily.trade_date == trade_date,
            FactStockDaily.close.isnot(None),
            # 停牌股不参与横截面排名：它们没有可信的当日量价与换手数据
            or_(
                FactStockDaily.is_suspended.is_(None),
                FactStockDaily.is_suspended.is_(False),
            ),
        )
        .order_by(FactStockDaily.code)
    )
    raw_rows = (await session.execute(stmt)).all()
    if not raw_rows:
        return pd.DataFrame()

    records = [tuple(row) for row in raw_rows]
    frame = pd.DataFrame(records, columns=[
        "code", "name", "industry_l1",
        "pe_ttm", "pb", "ps_ttm", "dv_ttm", "amount",
        "momentum_20", "momentum_60", "volatility_20", "amount_ma20",
    ])
    for column in (
        "pe_ttm", "pb", "ps_ttm", "dv_ttm", "amount",
        "momentum_20", "momentum_60", "volatility_20", "amount_ma20",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


def _build_score_rows(
    trade_date: date,
    cross_section: pd.DataFrame,
    percentiles: pd.DataFrame,
    scores: pd.DataFrame,
) -> list[dict]:
    """组装待 upsert 的行：分数字段 + 可追溯 detail（原始值/分位/权重）。"""
    raw_records = cross_section.to_dict("records")
    percentile_records = percentiles.to_dict("records")
    score_records = scores.to_dict("records")

    rows: list[dict] = []
    for index, raw in enumerate(raw_records):
        score = score_records[index]
        percentile = percentile_records[index]

        raw_detail: dict[str, Any] = {}
        for column in _RAW_INPUT_COLUMNS:
            value = raw.get(column)
            try:
                number = float(value)
            except (TypeError, ValueError):
                raw_detail[column] = None
                continue
            raw_detail[column] = None if (math.isnan(number) or math.isinf(number)) else number

        detail = {
            "raw": to_jsonable(raw_detail),
            "percentile": {
                factor: (None if pd.isna(percentile.get(factor))
                         else round(float(percentile.get(factor)), 4))
                for factor in FACTOR_WEIGHTS
            },
            "weights": dict(FACTOR_WEIGHTS),
            "industry_l1": raw.get("industry_l1"),
            # 个股级新闻情感尚未接入（新闻是板块/市场级），情绪因子暂为中性 0.5
            "sentiment_source": "neutral",
        }

        row: dict[str, Any] = {
            "code": str(raw.get("code")),
            "trade_date": trade_date,
            "detail": to_jsonable(detail),
        }
        for column in SCORE_COLUMNS:
            row[column] = to_jsonable(score.get(column))
        rows.append(row)
    return rows


async def _upsert_score_rows(session: AsyncSession, rows: list[dict],
                             external: bool) -> int:
    """分块 upsert `fact_stock_score_daily`（幂等，可重算）。"""
    written = 0
    for offset in range(0, len(rows), WRITE_CHUNK_ROWS):
        chunk = rows[offset:offset + WRITE_CHUNK_ROWS]
        if not chunk:
            continue
        stmt = pg_insert(FactStockScoreDaily).values(chunk)
        update_columns: dict[str, Any] = {
            column: stmt.excluded[column] for column in SCORE_COLUMNS
        }
        update_columns["detail"] = stmt.excluded["detail"]
        update_columns["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "trade_date"],
            set_=update_columns,
        )
        await session.execute(stmt)
        await commit_or_flush(session, external)
        written += len(chunk)
    return written


async def compute_stock_scores(
    trade_date: date,
    db: AsyncSession | None = None,
) -> int:
    """计算并落库单个交易日全市场个股因子评分。

    参数:
        trade_date: 交易日
        db: 外部共享 session（pipeline 传）；None 时自建事务

    返回:
        int: 写入（upsert）行数；当日无行情时返回 0（不抛异常）。

    前置数据:
        - `fact_stock_daily`（当日行情，估值字段）；
        - `fact_feature_daily`（技术指标，C1 产物）。缺失时动量/波动/流动性退化为
          中性分，不会失败，但会记录 warning 以便发现链路顺序问题。
    """
    async with session_ctx(db) as session:
        cross_section = await _load_cross_section(session, trade_date)
        if cross_section.empty:
            logger.warning("%s 无个股行情，跳过评分", trade_date)
            return 0

        percentiles = compute_factor_percentiles(cross_section)
        scores = _scores_from_percentiles(percentiles)
        rows = _build_score_rows(trade_date, cross_section, percentiles, scores)
        written = await _upsert_score_rows(session, rows, external=db is not None)

    logger.info("个股评分完成: %s, %d 只", trade_date, written)
    return written


async def compute_stock_scores_range(
    start: date,
    end: date,
    db: AsyncSession | None = None,
) -> int:
    """对 `[start, end]` 内有行情的所有交易日逐日评分（历史回补/重算用）。

    参数:
        start: 起始交易日（含）
        end: 结束交易日（含）
        db: 外部共享 session；None 时每天自建事务

    返回:
        int: 所有交易日写入行数之和。

    注意:
        逐日计算是**有意为之**：因子分位是横截面概念，必须按日截取，
        不能把多日数据混在一起排名（否则会出现"跨日比较"的未来函数）。
    """
    if end < start:
        raise ValueError(f"结束日期 {end} 早于开始日期 {start}")

    async with session_ctx(db) as session:
        stmt = (
            select(FactStockDaily.trade_date)
            .where(
                FactStockDaily.trade_date >= start,
                FactStockDaily.trade_date <= end,
            )
            .distinct()
            .order_by(FactStockDaily.trade_date)
        )
        trade_dates = [row[0] for row in (await session.execute(stmt)).all()]

    total = 0
    for trade_date in trade_dates:
        total += await compute_stock_scores(trade_date, db=db)
    logger.info("个股评分区间完成: %s~%s, %d 个交易日, %d 行",
                start, end, len(trade_dates), total)
    return total


__all__ = [
    "FACTOR_WEIGHTS",
    "SCORE_COLUMNS",
    "compute_factor_percentiles",
    "score_cross_section",
    "compute_stock_scores",
    "compute_stock_scores_range",
]
