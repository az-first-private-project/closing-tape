"""
AI 日报结构化输入（方案 C6）
============================
职责边界（重要）：
  - 本模块只负责**从本地库汇总结构化事实**，不做 LLM 调用、不生成自然语言、
    不写 `daily_report` 表（那是 `services/report_generator.py` 与 pipeline 的职责）；
  - 输出字段名与 Lead 约定固定，`services/report_generator.py` 直接消费组 prompt。

为什么需要它（方案 C6 的"引用具体数据"要求）：
  旧日报靠 `sector_score.ai_summary` hack 拼接，字段散落、不可追溯。
  V5 要求日报里每个结论都能追到具体板块与数值：本函数把"指数 / 涨跌家数 /
  领涨领跌板块 / 三面评分 / 两融 / 新闻情感 / 板块龙头"一次性取齐，
  并显式标注哪些数据集缺失（`data_gaps`），让 LLM 不能编造不存在的数据。

返回结构（字段名固定，Lead 已确认）：
```
{
  "trade_date": "2026-10-21",
  "indices": [{"index_code", "index_name", "close", "pct_chg"}],
  "breadth": {"up", "down", "flat", "limit_up", "limit_down", "median_pct"},
  "top_sectors": [...], "bottom_sectors": [...],
  "sector_scores_coverage": {"scored", "total"},
  "margin": {"margin_balance", "short_balance", "margin_change", "northward_net", ...},
  "news_sentiment": {"mean", "count", "top_titles": [...], "top_items": [...]},
  "leaders_by_sector": {sector_code: [leader, ...]},
  "data_gaps": ["..."],
}
```
所有数值一律 JSON 可序列化（Decimal/date/NaN 已清洗），可直接 json.dumps。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BENCHMARK_INDEX, INDEX_MAP
from app.db.models.fact import (
    FactSectorDaily,
    MarginTrading,
    MarketIndex,
    SectorScore,
)
from app.db.models.news import NewsArticle
from app.engine._db import session_ctx, to_jsonable, trade_day_bounds
from app.engine.breadth import load_breadth

logger = logging.getLogger(__name__)

# 日报里"领涨/领跌板块"各取多少个（与前端 dashboard 的 top10 一致）
DEFAULT_SECTOR_LIMIT = 10
# 新闻情感标题最多带几条（prompt 成本控制）
NEWS_TOP_TITLES = 5
# 日报里每个板块最多带几只龙头（strength 3 + sentiment 2 已够用）
LEADERS_PER_SECTOR = 5


def _to_float(value: Any) -> float | None:
    """宽松转 float：None/NaN/Inf/Decimal/str → float 或 None。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(result) or np.isinf(result):
        return None
    return result


def _sector_item(score: SectorScore | None, pct_chg: float | None) -> dict:
    """板块条目：行情涨跌幅 + 三面评分（未评分时分数为 None，不冒充 5 分）。"""
    return {
        "sector_code": score.sector_code if score else None,
        "sector_name": score.sector_name if score else None,
        "pct_chg": pct_chg,
        "technical_score": _to_float(score.technical_score) if score else None,
        "sentiment_score": _to_float(score.sentiment_score) if score else None,
        "news_score": _to_float(score.news_score) if score else None,
        "composite_score": _to_float(score.composite_score) if score else None,
    }


async def _load_indices(session: AsyncSession, trade_date: date) -> list[dict]:
    """A 股主要指数（按 INDEX_MAP 顺序，保证输出稳定）。"""
    codes = [code for code, (_name, is_a_share) in INDEX_MAP.items() if is_a_share]
    rows = (await session.execute(
        select(
            MarketIndex.index_code,
            MarketIndex.index_name,
            MarketIndex.close,
            MarketIndex.pct_chg,
        ).where(
            MarketIndex.trade_date == trade_date,
            MarketIndex.index_code.in_(codes),
        )
    )).all()
    order = {code: index for index, code in enumerate(codes)}
    items = [
        {
            "index_code": row.index_code,
            "index_name": row.index_name,
            "close": _to_float(row.close),
            "pct_chg": _to_float(row.pct_chg),
        }
        for row in rows
    ]
    items.sort(key=lambda item: order.get(item["index_code"], 99))
    return items


async def _load_margin(session: AsyncSession, trade_date: date) -> dict:
    """最近一条两融/北向数据（容忍 T+1 披露；无数据显式标注）。"""
    row = (await session.execute(
        select(MarginTrading)
        .where(MarginTrading.trade_date <= trade_date)
        .order_by(MarginTrading.trade_date.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return {"available": False, "reason": "no_margin_data"}
    return {
        "available": True,
        "trade_date": row.trade_date.isoformat() if row.trade_date else None,
        "margin_balance": _to_float(row.margin_balance),
        "short_balance": _to_float(row.short_balance),
        "margin_change": _to_float(row.margin_change),
        "northward_net": _to_float(row.northward_net),
        "northward_available": row.northward_net is not None,
    }


async def _load_news_sentiment(session: AsyncSession, trade_date: date) -> dict:
    """当日新闻情感聚合：均值 / 条数 / 代表性标题。

    `mean` 为 -1..1 的原始情感均值（不是 0–10 分），供 prompt 直接描述
    "偏多/偏空"；`top_titles` 取 |情感| 最大的若干条，让 LLM 能引用具体新闻。
    """
    start, end = trade_day_bounds(trade_date)
    rows = (await session.execute(
        select(NewsArticle.title, NewsArticle.sentiment_score)
        .where(
            NewsArticle.publish_time >= start,
            NewsArticle.publish_time < end,
            NewsArticle.sentiment_score.isnot(None),
        )
        .order_by(NewsArticle.publish_time.desc())
        .limit(500)
    )).all()

    items: list[dict] = []
    for title, score in rows:
        sentiment = _to_float(score)
        if sentiment is None:
            continue
        items.append({
            "title": (title or "")[:120],
            "sentiment": round(sentiment, 3),
        })

    if not items:
        return {
            "mean": None,
            "count": 0,
            "top_titles": [],
            "top_items": [],
            "available": False,
            "reason": "no_scored_news",
        }

    mean_sentiment = round(float(np.mean([item["sentiment"] for item in items])), 3)
    top_items = sorted(
        items, key=lambda item: abs(item["sentiment"]), reverse=True
    )[:NEWS_TOP_TITLES]
    return {
        "mean": mean_sentiment,
        "count": len(items),
        "top_titles": [item["title"] for item in top_items],
        "top_items": top_items,
        "available": True,
    }


async def _load_sector_rows(
    session: AsyncSession, trade_date: date, limit: int
) -> tuple[list[dict], list[dict]]:
    """领涨/领跌板块（涨跌幅排序，附三面评分与当日领涨股；多取一些保证覆盖）。"""
    dailies = (await session.execute(
        select(
            FactSectorDaily.sector_code,
            FactSectorDaily.sector_name,
            FactSectorDaily.pct_chg,
            FactSectorDaily.leading_stock,
            FactSectorDaily.leading_stock_pct,
        ).where(FactSectorDaily.trade_date == trade_date)
        .order_by(FactSectorDaily.sector_code)
    )).all()
    pct_map = {
        row.sector_code: _to_float(row.pct_chg) for row in dailies
    }
    name_map = {row.sector_code: row.sector_name for row in dailies}
    leading_map = {
        row.sector_code: (
            row.leading_stock,
            _to_float(row.leading_stock_pct),
        )
        for row in dailies
    }
    scores = (await session.execute(
        select(SectorScore).where(SectorScore.trade_date == trade_date)
    )).scalars().all()
    score_map = {score.sector_code: score for score in scores}

    items: list[dict] = []
    for sector_code, pct_chg in pct_map.items():
        score = score_map.get(sector_code)
        item = _sector_item(score, pct_chg)
        item["sector_code"] = sector_code
        item["sector_name"] = name_map.get(sector_code) or (
            score.sector_name if score else None
        )
        leading_stock, leading_pct = leading_map.get(sector_code, (None, None))
        item["leading_stock"] = leading_stock
        item["leading_stock_pct"] = leading_pct
        items.append(item)

    # pct_chg 为 None 的板块排最后（不参与领涨领跌）
    ranked = sorted(
        items,
        key=lambda item: item["pct_chg"] if item["pct_chg"] is not None else -999.0,
        reverse=True,
    )
    top = [item for item in ranked if item["pct_chg"] is not None][:limit]
    bottom = [item for item in ranked if item["pct_chg"] is not None][-limit:]
    bottom.reverse()
    return top, bottom


async def _load_sector_leaders(
    session: AsyncSession, trade_date: date, limit: int
) -> dict[str, list[dict]]:
    """按 |涨跌幅| 取前 limit 个板块，返回 {sector_code: [龙头...]}。"""
    dailies = (await session.execute(
        select(FactSectorDaily.sector_code, FactSectorDaily.pct_chg)
        .where(FactSectorDaily.trade_date == trade_date)
        .order_by(FactSectorDaily.sector_code)
    )).all()
    ranked = sorted(
        dailies,
        key=lambda row: (abs(_to_float(row.pct_chg) or 0.0), row.sector_code),
        reverse=True,
    )[:limit]
    codes = [row.sector_code for row in ranked]
    if not codes:
        return {}

    scores = (await session.execute(
        select(SectorScore).where(
            SectorScore.trade_date == trade_date,
            SectorScore.sector_code.in_(codes),
        )
    )).scalars().all()

    leaders_by_sector: dict[str, list[dict]] = {}
    for score in scores:
        merged: list[dict] = []
        merged.extend(score.strength_leaders or [])
        merged.extend(score.sentiment_leaders or [])
        if merged:
            leaders_by_sector[score.sector_code] = to_jsonable(
                merged[:LEADERS_PER_SECTOR]
            )
    return leaders_by_sector


async def _load_sector_coverage(session: AsyncSession,
                                trade_date: date) -> dict:
    """评分覆盖率：已评分板块数 / 当日有行情板块数（日报需说明"覆盖了多少"）。"""
    total = (await session.execute(
        select(func.count(FactSectorDaily.sector_code)).where(
            FactSectorDaily.trade_date == trade_date
        )
    )).scalar() or 0
    scored = (await session.execute(
        select(func.count(SectorScore.sector_code)).where(
            SectorScore.trade_date == trade_date,
            SectorScore.composite_score.isnot(None),
        )
    )).scalar() or 0
    return {"scored": int(scored), "total": int(total)}


async def build_daily_report_input(
    trade_date: date,
    top_n: int = DEFAULT_SECTOR_LIMIT,
    db: AsyncSession | None = None,
) -> dict:
    """汇总 AI 日报所需的结构化输入（**只读本地库，不调用 LLM**）。

    参数:
        trade_date: 交易日
        top_n: 领涨/领跌板块各取多少个（默认 10）；同时决定 `leaders_by_sector`
               覆盖的板块数
        db: 外部共享 session（pipeline 传）；None 时自建会话

    返回:
        dict，字段见模块 docstring。所有数值 JSON 可序列化。
        数据缺失时对应字段为 None/空列表，并汇总到 `data_gaps`，
        让 LLM prompt 显式知道"哪块数据没有"而不是编造。
    """
    top_n = max(1, int(top_n))
    async with session_ctx(db) as session:
        indices = await _load_indices(session, trade_date)
        breadth = await load_breadth(session, trade_date)
        top_sectors, bottom_sectors = await _load_sector_rows(
            session, trade_date, top_n
        )
        coverage = await _load_sector_coverage(session, trade_date)
        margin = await _load_margin(session, trade_date)
        news_sentiment = await _load_news_sentiment(session, trade_date)
        leaders_by_sector = await _load_sector_leaders(
            session, trade_date, top_n
        )

    data_gaps: list[str] = []
    if not indices:
        data_gaps.append("index_daily_missing")
    if breadth["total"] == 0:
        data_gaps.append("stock_daily_missing")
    if coverage["scored"] == 0:
        data_gaps.append("sector_scores_missing")
    if not margin.get("available"):
        data_gaps.append("margin_missing")
    if not news_sentiment.get("available"):
        data_gaps.append("news_sentiment_missing")

    benchmark = next(
        (index for index in indices if index["index_code"] == BENCHMARK_INDEX), None
    )
    payload = {
        "trade_date": trade_date.isoformat(),
        "indices": indices,
        "benchmark": benchmark,
        "breadth": breadth,
        "top_sectors": top_sectors,
        "bottom_sectors": bottom_sectors,
        "sector_scores_coverage": coverage,
        "margin": margin,
        "news_sentiment": news_sentiment,
        "leaders_by_sector": leaders_by_sector,
        "data_gaps": data_gaps,
    }
    logger.info(
        "日报结构化输入完成: %s, 指数 %d, 板块覆盖 %d/%d, 新闻情感 %d 条, gaps=%s",
        trade_date, len(indices), coverage["scored"], coverage["total"],
        news_sentiment["count"], data_gaps,
    )
    return to_jsonable(payload)


__all__ = ["DEFAULT_SECTOR_LIMIT", "build_daily_report_input"]
