"""
板块分析（方案 C5：修复三个被点名的 P0/P1 bug + 全量评分落库）
==============================================================
V5 要修掉的三个具体问题（旧 `services/scoring.py` / `api/v1/analysis.py`）：

  1. **历史取数方向错**：旧代码用 `order_by(trade_date.asc()).limit(n)` 取"最早 n 天"
     而不是"最近 n 天"，导致技术分永远基于陈旧历史（P0）。
     本模块统一为 `ORDER BY trade_date DESC LIMIT n` → Python 端 reverse 成升序，
     SQL 层只保证"取最近"，序列方向交给指标计算前整理。
  2. **板块资金流匹配单位错**：旧代码拿板块 **BK 代码**去和中文名称列比较
     （`df["名称"] == sector_code`），永远匹配不到（P1）。
     本模块优先从 `fact_moneyflow` 按成分股聚合到板块；确实没有时再查 akshare，
     且必须用**中文名**匹配（`df["名称"] == sector_name`）。
  3. **评分不落库 / 只评 30 个板块**：V5 改为**全量板块**都算（本地 SQL 很快），
     `top_n` 只用于给下游 LLM 消息面取材（标记 `detail.is_top_n`），不影响落库。

评分口径（Lead 定稿，避免新闻在综合分里算两次 = 方案 B）：
  - 技术面 technical（0–10）：`engine/technical_score.score_sector_technical_detail`，
    趋势 40% / 动量 30% / 量能 30%；
  - 情绪面 sentiment（0–10）：**融资 / 北向 / 资金流 三子项**，
    权重 `SECTOR_SENTIMENT_WEIGHTS`（0.35/0.30/0.35），缺项按比例再分配；
  - 消息面 news（0–10）：独立第三面，`calc_news_sentiment` 聚合当日相关新闻；
  - 综合 `composite = technical*0.40 + sentiment*0.35 + news*0.25`
    （权重读 `config.WEIGHT_*`，可调）。

缺数据处理（硬性）：
  - 任一子项无数据 → 分数字段返回 `NEUTRAL_SCORE`（5.0），
    `detail` 里对应子项为 `null` 并在 `detail.reasons` 写明原因；
  - 历史不足 20 日 → 技术分 5.0，`detail.reasons.technical="insufficient_history"`；
  - 绝不因为缺数据抛异常（板块数据半成品时看板仍可展示）。
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    INGEST_NETWORK_ENABLED,
    NEUTRAL_SCORE,
    WEIGHT_NEWS,
    WEIGHT_SENTIMENT,
    WEIGHT_TECHNICAL,
)
from app.db.models.dim import DimSectorMember
from app.db.models.fact import (
    FactMoneyflow,
    FactSectorDaily,
    MarginTrading,
    SectorScore,
)
from app.db.models.news import NewsArticle
from app.engine._db import (
    commit_or_flush,
    session_ctx,
    to_jsonable,
    trade_day_bounds,
)
from app.engine.leaders import identify_leaders, sector_members_with_quotes
from app.engine.sentiment import (
    SECTOR_SENTIMENT_WEIGHTS,
    calc_margin_sentiment,
    calc_moneyflow_sentiment,
    calc_news_sentiment,
    calc_northward_sentiment,
    combine_sentiment,
)
from app.engine.technical_score import score_sector_technical_detail

logger = logging.getLogger(__name__)

# ==================== 评分权重（口径集中在此，可调） ====================

# 板块综合分三面权重：沿用旧版 40/35/25（来自 config，环境变量可覆盖）
SECTOR_COMPOSITE_WEIGHTS: dict[str, float] = {
    "technical": WEIGHT_TECHNICAL,
    "sentiment": WEIGHT_SENTIMENT,
    "news": WEIGHT_NEWS,
}

# 情绪面三子项权重（新闻已独立成第三面，不在此重复计权）：
# 融资 0.35 / 北向 0.30 / 资金流 0.35。北向长期缺数据，combine_sentiment 会
# 把它的权重按比例分给可用项，所以这里给它 30% 不会"永久拖累"情绪分。
SECTOR_SENTIMENT_PARTS_WEIGHTS: dict[str, float] = dict(SECTOR_SENTIMENT_WEIGHTS)

# 板块技术分默认回看窗口（交易日）与最少历史
SECTOR_TECH_LOOKBACK = 60
MIN_TECH_HISTORY = 20

# 每条 upsert 的行数（同时是自建 session 的 commit 粒度）
WRITE_CHUNK_ROWS = 800

# 进程内 akshare 板块资金流缓存：{行业/概念: (交易日, {中文名: 净流入元})}
_AK_MONEYFLOW_CACHE: dict[str, tuple[date, dict[str, float]]] = {}

# 一条新闻最多多长（防止把整篇正文塞进关键词匹配）
_NEWS_TEXT_LIMIT = 200


# ============================================================
# 内部工具
# ============================================================

def _to_float(value: Any) -> float | None:
    """宽松转 float：None/NaN/Inf/非法 → None。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _clamp10(value: float) -> float:
    """裁剪到 [0, 10] 并保留两位小数。"""
    return round(float(min(10.0, max(0.0, value))), 2)


def _sector_news_keywords(sector_name: str | None) -> list[str]:
    """板块名 → 新闻匹配关键词（中文名，去掉"板块/概念/行业"等后缀）。"""
    if not sector_name:
        return []
    name = str(sector_name).strip()
    base = name
    for suffix in ("板块", "概念", "行业", "指数"):
        base = base.replace(suffix, "")
    base = base.strip()
    keywords = {name, base}
    # 关键词至少 2 个字，避免"AI"这种 1~2 字过度匹配（AI 属于行业名时仍保留 2 字）
    return [keyword for keyword in keywords if len(keyword) >= 2]


async def _load_news_articles(session: AsyncSession,
                              trade_date: date) -> list[dict]:
    """取当日已打情感分的新闻（一次查询，供全部板块复用）。"""
    start, end = trade_day_bounds(trade_date)
    stmt = (
        select(
            NewsArticle.title,
            NewsArticle.summary,
            NewsArticle.sentiment_score,
        )
        .where(
            NewsArticle.publish_time >= start,
            NewsArticle.publish_time < end,
            NewsArticle.sentiment_score.isnot(None),
        )
        .order_by(NewsArticle.publish_time.desc())
        .limit(500)
    )
    rows = (await session.execute(stmt)).all()
    articles: list[dict] = []
    for title, summary, score in rows:
        sentiment = _to_float(score)
        if sentiment is None:
            continue
        articles.append({
            "title": title or "",
            "text": f"{title or ''} {summary or ''}"[:_NEWS_TEXT_LIMIT],
            "sentiment": sentiment,
        })
    return articles


def _match_sector_news(articles: list[dict],
                       sector_name: str | None) -> list[dict]:
    """按板块中文名关键词筛出相关新闻。"""
    keywords = _sector_news_keywords(sector_name)
    if not keywords:
        return []
    matched: list[dict] = []
    for article in articles:
        text = article["text"]
        if any(keyword in text for keyword in keywords):
            matched.append(article)
    return matched


def _lookup_chinese_name(mapping: dict[str, float],
                         sector_name: str | None) -> float | None:
    """用**中文名**在资金流映射里精确查找（旧代码拿 BK 代码比中文名是 bug）。

    旧实现等价于 `df[df["名称"] == sector_code]`——用 BK 代码去比中文名称列，
    结果永远为空，板块资金流子项于是永久中性。V5 修正为用 `sector_name`
    与名称列（`df["名称"]`）做精确中文匹配。

    先精确匹配，再容忍首尾空格差异；不做模糊包含匹配，避免"半导体"误配
    "半导体设备"等不同板块（宁缺毋滥，缺数据会标记 null）。
    """
    if not mapping or not sector_name:
        return None
    name = str(sector_name).strip()
    if name in mapping:
        return mapping[name]
    for key, value in mapping.items():
        if str(key).strip() == name:
            return value
    return None


def _parse_akshare_moneyflow(df: Any) -> dict[str, float]:
    """把 akshare 板块资金流 DataFrame 解析成 {中文名: 主力净流入(元)}。

    akshare 列名随版本变化，这里按候选列名容错；单位按接口文档为**元**。
    """
    if df is None or getattr(df, "empty", True):
        return {}
    columns = [str(column) for column in df.columns]
    name_column = next(
        (candidate for candidate in ("名称", "板块名称") if candidate in columns),
        None,
    )
    net_column = next(
        (
            candidate
            for candidate in (
                "今日主力净流入-净额",
                "主力净流入-净额",
                "今日主力净流入",
                "主力净流入",
            )
            if candidate in columns
        ),
        None,
    )
    if name_column is None or net_column is None:
        logger.warning("akshare 板块资金流列名不识别: %s", columns)
        return {}

    mapping: dict[str, float] = {}
    for record in df.to_dict("records"):
        name = str(record.get(name_column, "")).strip()
        value = _to_float(record.get(net_column))
        if name and value is not None:
            mapping[name] = value
    return mapping


def _fetch_sector_moneyflow(sector_type: str | None,
                            trade_date: date) -> tuple[dict[str, float], str | None]:
    """同步拉取板块资金流：**东财优先，同花顺兜底**（进程内按"类型 + 交易日"缓存）。

    返回 `(mapping, source)`，`source ∈ {"akshare","ths",None}` —— 来源必须如实标记，
    否则审计时无法判断某个评分到底基于哪个数据源。

    说明：
      - 只在 `INGEST_NETWORK_ENABLED=1` 时尝试联网；
      - 任一源失败都不会抛异常；空结果也缓存，避免 86 个板块各失败一次。
    """
    kind = "industry" if str(sector_type) == "industry" else "concept"
    cached = _AK_MONEYFLOW_CACHE.get(kind)
    if cached is not None and cached[0] == trade_date:
        return cached[1], cached[2]

    mapping: dict[str, float] = {}
    source: str | None = None
    if not INGEST_NETWORK_ENABLED:
        _AK_MONEYFLOW_CACHE[kind] = (trade_date, mapping, None)
        return mapping, None

    try:
        import akshare as ak  # 延迟导入：离线/测试环境没有 akshare 也不影响 import 本模块

        sector_kind = "行业资金流" if kind == "industry" else "概念资金流"
        frame = ak.stock_sector_fund_flow_rank(
            indicator="今日", sector_type=sector_kind
        )
        mapping = _parse_akshare_moneyflow(frame)
        if mapping:
            source = "akshare"
        else:
            logger.warning("akshare 板块资金流为空（%s）", sector_kind)
    except Exception as exc:  # noqa: BLE001 - 外部数据源失败不能打断评分
        logger.warning("akshare 板块资金流获取失败（%s）: %s", kind, exc)
        mapping = {}

    # 东财不可达时的**同花顺兜底**。
    # 实测经验：`stock_fund_flow_industry` 与 `stock_fund_flow_concept` 两个接口会**交替可用**
    # （同一进程里常常只有一个能通），所以这里一次取「合并表」（行业+概念，同名行业优先），
    # 再按名字匹配 —— 避免行业板块或概念板块一半拿不到资金流。
    if not mapping:
        merged = _AK_MONEYFLOW_CACHE.get("merged")
        if merged is not None and merged[0] == trade_date:
            mapping, source = merged[1], merged[2]
        else:
            try:
                from app.ingest.ths_sector import fetch_net_inflow_map_sync
                mapping = fetch_net_inflow_map_sync(None)
                source = "ths" if mapping else None
                if mapping:
                    logger.info("板块资金流使用同花顺兜底（合并表 %d 个板块）", len(mapping))
            except Exception as exc:  # noqa: BLE001
                logger.debug("同花顺资金流兜底失败: %s", exc)
                mapping, source = {}, None
            _AK_MONEYFLOW_CACHE["merged"] = (trade_date, mapping, source)

    _AK_MONEYFLOW_CACHE[kind] = (trade_date, mapping, source)
    return mapping, source


# ============================================================
# 1. 板块历史取数 + 技术分
# ============================================================

async def _load_sector_history(session: AsyncSession, sector_code: str,
                               trade_date: date, lookback: int) -> list[tuple[Any, Any]]:
    """取板块最近 `lookback` 个交易日（**DESC + LIMIT 才是最近**）。"""
    stmt = (
        select(FactSectorDaily.trade_date, FactSectorDaily.close, FactSectorDaily.volume)
        .where(
            FactSectorDaily.sector_code == sector_code,
            FactSectorDaily.trade_date <= trade_date,
        )
        .order_by(FactSectorDaily.trade_date.desc())
        .limit(max(1, int(lookback)))
    )
    rows = (await session.execute(stmt)).all()
    # SQL 取的是"最近 N 天"（倒序），指标计算需要时间升序 → reverse
    return list(reversed(rows))


async def calc_sector_technical(
    sector_code: str,
    trade_date: date,
    lookback: int = SECTOR_TECH_LOOKBACK,
    db: AsyncSession | None = None,
) -> tuple[float, dict]:
    """计算单板块技术面评分（0–10）+ 可解释明细。

    参数:
        sector_code: 板块代码（如 BK1036）
        trade_date: 交易日
        lookback: 回看交易日数（默认 60；技术指标至少需要 20 个交易日）
        db: 外部共享 session；None 时自建会话

    返回:
        (score, detail)
        - 正常：score 0–10，detail 含 trend/momentum/volume 子分与 reason="ok"；
        - 历史不足 20 日：score=5.0，
          detail 至少包含 `{"reason": "insufficient_history", "rows": n}`。

    修复点：历史取数必须是"最近 N 天"。
    旧实现 `order_by(trade_date.asc()).limit(30)` 取到的是**上市最早**的 30 天，
    技术分因此永远停留在陈旧区间，这是被点名的 P0 bug。
    """
    lookback = max(MIN_TECH_HISTORY, int(lookback))
    async with session_ctx(db) as session:
        rows = await _load_sector_history(session, sector_code, trade_date, lookback)

    row_count = len(rows)
    if row_count < MIN_TECH_HISTORY:
        detail = {
            "reason": "insufficient_history",
            "rows": row_count,
            "min_rows": MIN_TECH_HISTORY,
            "lookback": lookback,
            "sector_code": sector_code,
            "trend": None,
            "momentum": None,
            "volume": None,
        }
        logger.info("板块 %s 历史仅 %d 行（<%d），技术分给中性",
                    sector_code, row_count, MIN_TECH_HISTORY)
        return _clamp10(NEUTRAL_SCORE), detail

    close = pd.Series(
        [_to_float(row.close) for row in rows], dtype="float64"
    )
    volume = pd.Series(
        [_to_float(row.volume) or 0.0 for row in rows], dtype="float64"
    )
    score, detail = score_sector_technical_detail(close, volume)
    detail = dict(detail)
    detail["sector_code"] = sector_code
    detail["lookback"] = lookback
    # 历史区间（便于排查"最近 60 天"到底取到哪几天）
    first, last = rows[0][0], rows[-1][0]
    detail["history_start"] = first.isoformat() if hasattr(first, "isoformat") else str(first)
    detail["history_end"] = last.isoformat() if hasattr(last, "isoformat") else str(last)
    return _clamp10(float(score)), detail


# ============================================================
# 2. 市场级情绪输入（融资 / 北向）
# ============================================================

async def _load_market_sentiment_inputs(session: AsyncSession,
                                        trade_date: date) -> dict:
    """读取融资/北向输入（市场级，所有板块共享）。

    取数方向：`trade_date <= d ORDER BY trade_date DESC LIMIT 5`（最近 5 个交易日），
    容忍数据晚一天（两融披露通常 T+1），并记录 `as_of` 便于追溯。
    """
    stmt = (
        select(
            MarginTrading.trade_date,
            MarginTrading.margin_balance,
            MarginTrading.short_balance,
            MarginTrading.margin_change,
            MarginTrading.northward_net,
        )
        .where(MarginTrading.trade_date <= trade_date)
        .order_by(MarginTrading.trade_date.desc())
        .limit(5)
    )
    rows = list(reversed((await session.execute(stmt)).all()))
    if not rows:
        return {
            "margin_score": None,
            "northward_score": None,
            "margin_data": None,
            "as_of": None,
            "reason": "no_margin_data",
        }

    latest = rows[-1]
    as_of = latest[0]
    margin_balance = _to_float(latest[1])
    short_balance = _to_float(latest[2])
    margin_change = _to_float(latest[3])
    northward_net = _to_float(latest[4])
    recent_changes = [
        value for value in (_to_float(row[3]) for row in rows) if value is not None
    ]

    margin_available = any(
        value is not None for value in (margin_balance, short_balance, margin_change)
    )
    margin_score = None
    if margin_available:
        margin_score = calc_margin_sentiment(
            {
                "margin_balance": margin_balance,
                "short_balance": short_balance,
                "margin_change": margin_change,
            },
            recent_changes,
        )

    northward_score = None
    northward_reason = None
    if northward_net is not None:
        northward_score = calc_northward_sentiment(northward_net)
    else:
        northward_reason = "northward_unavailable"

    return {
        "margin_score": margin_score,
        "northward_score": northward_score,
        "margin_data": {
            "trade_date": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
            "margin_balance": margin_balance,
            "short_balance": short_balance,
            "margin_change": margin_change,
            "northward_net": northward_net,
            "recent_changes": recent_changes[-5:],
        },
        "as_of": as_of,
        "margin_reason": None if margin_available else "no_margin_data",
        "northward_reason": northward_reason,
    }


# ============================================================
# 3. 板块资金流情绪（成分股聚合 → akshare 兜底 → 中性）
# ============================================================

async def _calc_moneyflow_sentiment(
    session: AsyncSession,
    sector_code: str,
    sector_name: str | None,
    sector_type: str | None,
    trade_date: date,
) -> tuple[float | None, dict]:
    """板块主力资金流情绪分。

    取数优先级（方案 C5 要求）：
      1. `fact_moneyflow`：按**当日有效成分股**聚合 `main_net_t0` 到板块（本地库，首选）；
      2. akshare `stock_sector_fund_flow_rank`：**用中文名精确匹配**（进程内缓存，失败即止）；
      3. 都没有 → 返回 (None, {"available": False, "reason": ...})，由上层给中性分并标记 null。
    """
    # ---------- 1) 本地库：成分股资金流聚合 ----------
    member_subquery = (
        select(DimSectorMember.stock_code)
        .where(
            DimSectorMember.sector_code == sector_code,
            DimSectorMember.effective_from <= trade_date,
            or_(
                DimSectorMember.effective_to.is_(None),
                DimSectorMember.effective_to > trade_date,
            ),
        )
    )
    row = (await session.execute(
        select(
            func.count(FactMoneyflow.main_net_t0),
            func.coalesce(func.sum(FactMoneyflow.main_net_t0), 0),
        ).where(
            FactMoneyflow.trade_date == trade_date,
            FactMoneyflow.code.in_(member_subquery),
        )
    )).one()
    member_count = int(row[0] or 0)
    if member_count > 0:
        net_flow = float(row[1] or 0.0)
        return calc_moneyflow_sentiment(net_flow), {
            "available": True,
            "source": "fact_moneyflow",
            "members_with_flow": member_count,
            "net_flow": round(net_flow, 2),
        }

    # ---------- 2) 外部兜底：东财 → 同花顺（中文名匹配，绝不拿 BK 代码比中文名） ----------
    # 放到线程池执行：akshare 是同步 HTTP 调用，直接 await 会阻塞事件循环
    mapping, source = await asyncio.to_thread(
        _fetch_sector_moneyflow, sector_type, trade_date
    )
    net_flow = _lookup_chinese_name(mapping, sector_name)
    if net_flow is not None:
        return calc_moneyflow_sentiment(net_flow), {
            "available": True,
            "source": source or "unknown",
            "net_flow": round(float(net_flow), 2),
            "matched_name": sector_name,
        }

    # ---------- 3) 中性 ----------
    return None, {
        "available": False,
        "source": "unavailable",
        "reason": "no_moneyflow_data",
    }


# ============================================================
# 4. 全量板块评分
# ============================================================

async def _load_sector_dailies(session: AsyncSession,
                               trade_date: date) -> list[FactSectorDaily]:
    """当日全部板块行情（不按 30 个截断，全量评分）。"""
    stmt = (
        select(FactSectorDaily)
        .where(FactSectorDaily.trade_date == trade_date)
        .order_by(FactSectorDaily.sector_code)
    )
    return list((await session.execute(stmt)).scalars().all())


async def score_all_sectors(
    trade_date: date,
    top_n: int = 30,
    db: AsyncSession | None = None,
) -> list[dict]:
    """对**全部板块**计算三面评分并组装待落库行（不写库，写库用 `save_sector_scores`）。

    参数:
        trade_date: 交易日
        top_n: 标记"消息面 Top N"（按 |涨跌幅| 排序），**不影响全量评分与落库**；
               下游 LLM 日报只给 top_n 个板块的详细消息面，避免 prompt 过长。
        db: 外部共享 session；None 时自建会话

    返回:
        list[dict]，每个板块一行，字段：
        sector_code / sector_name / sector_type / pct_chg /
        technical_score / sentiment_score / news_score / composite_score /
        strength_leaders / sentiment_leaders / detail。
        按 |pct_chg| 降序排列；当日无板块行情时返回 []。

    数据流:
        技术分 ← FactSectorDaily 最近 60 日；
        情绪分 ← 融资(市场) + 北向(市场) + 板块资金流，缺项权重再分配；
        消息分 ← 当日新闻按板块中文名匹配（无匹配给 NEUTRAL_SCORE 且 detail 标 null）；
        龙头   ← DimSectorMember(SCD2) + FactStockDaily 当日行情。
    """
    async with session_ctx(db) as session:
        dailies = await _load_sector_dailies(session, trade_date)
        if not dailies:
            logger.warning("%s 无板块日线，跳过板块评分", trade_date)
            return []

        market_sentiment = await _load_market_sentiment_inputs(session, trade_date)
        articles = await _load_news_articles(session, trade_date)

        # 全量板块按 |pct_chg| 排序：top_n 只做标记，其余板块照样算、照样落库
        def _abs_pct(item: FactSectorDaily) -> float:
            return abs(_to_float(item.pct_chg) or 0.0)

        ordered = sorted(dailies, key=_abs_pct, reverse=True)
        rank_map = {item.sector_code: rank for rank, item in enumerate(ordered, 1)}

        rows: list[dict] = []
        for daily in ordered:
            rank = rank_map[daily.sector_code]
            pct_chg = _to_float(daily.pct_chg)

            technical_score, technical_detail = await calc_sector_technical(
                daily.sector_code, trade_date, db=session
            )

            moneyflow_score, moneyflow_detail = await _calc_moneyflow_sentiment(
                session=session,
                sector_code=daily.sector_code,
                sector_name=daily.sector_name,
                sector_type=daily.sector_type,
                trade_date=trade_date,
            )

            parts = {
                "margin": market_sentiment["margin_score"],
                "northward": market_sentiment["northward_score"],
                "moneyflow": moneyflow_score,
            }
            sentiment_score, sentiment_detail = combine_sentiment(
                parts, SECTOR_SENTIMENT_PARTS_WEIGHTS
            )

            matched_news = _match_sector_news(articles, daily.sector_name)
            if matched_news:
                news_value = calc_news_sentiment(
                    [article["sentiment"] for article in matched_news]
                )
                news_available = True
                news_reason = None
            else:
                news_value = None
                news_available = False
                news_reason = "no_sector_news"

            # 消息面独立第三面：缺数据用中性分，但 detail.news 保留 null 供前端区分
            news_face_score = news_value if news_available else _clamp10(NEUTRAL_SCORE)

            composite_score = _clamp10(
                technical_score * SECTOR_COMPOSITE_WEIGHTS["technical"]
                + sentiment_score * SECTOR_COMPOSITE_WEIGHTS["sentiment"]
                + news_face_score * SECTOR_COMPOSITE_WEIGHTS["news"]
            )

            members = await sector_members_with_quotes(
                daily.sector_code, trade_date, db=session
            )
            leaders = identify_leaders(members)

            detail = {
                # ---- 三面 + 子项（前端雷达/子项展示口径）----
                "trend": technical_detail.get("trend"),
                "momentum": technical_detail.get("momentum"),
                "volume": technical_detail.get("volume"),
                "margin": market_sentiment["margin_score"],
                "northward": market_sentiment["northward_score"],
                "moneyflow": moneyflow_score,
                "news": news_value,
                "weights": {
                    # 技术三因子
                    "trend": technical_detail.get("weights", {}).get("trend"),
                    "momentum": technical_detail.get("weights", {}).get("momentum"),
                    "volume": technical_detail.get("weights", {}).get("volume"),
                    # 情绪三子项
                    **{key: value for key, value in SECTOR_SENTIMENT_PARTS_WEIGHTS.items()},
                    # 三面综合
                    **SECTOR_COMPOSITE_WEIGHTS,
                },
                # ---- 可追溯信息（缺数据原因必须可见）----
                "available": {
                    "margin": market_sentiment["margin_score"] is not None,
                    "northward": market_sentiment["northward_score"] is not None,
                    "moneyflow": moneyflow_score is not None,
                    "news": news_available,
                },
                "reasons": {
                    key: value
                    for key, value in {
                        "technical": technical_detail.get("reason"),
                        "margin": market_sentiment.get("margin_reason"),
                        "northward": market_sentiment.get("northward_reason"),
                        "moneyflow": moneyflow_detail.get("reason"),
                        "news": news_reason,
                    }.items()
                    if value not in (None, "ok")
                },
                "sentiment_effective_weights": sentiment_detail.get("effective_weights"),
                "technical_rows": technical_detail.get("rows"),
                "moneyflow_source": moneyflow_detail.get("source"),
                "news_samples": len(matched_news),
                "sector_pct_chg": pct_chg,
                "rank_by_abs_pct": rank,
                "is_top_n": rank <= int(top_n),
                "leaders_count": {
                    "strength": len(leaders["strength_leaders"]),
                    "sentiment": len(leaders["sentiment_leaders"]),
                },
            }

            rows.append({
                "sector_code": daily.sector_code,
                "sector_name": daily.sector_name,
                "sector_type": daily.sector_type,
                "pct_chg": pct_chg,
                "technical_score": technical_score,
                "sentiment_score": sentiment_score,
                "news_score": news_face_score,
                "composite_score": composite_score,
                "strength_leaders": to_jsonable(leaders["strength_leaders"]),
                "sentiment_leaders": to_jsonable(leaders["sentiment_leaders"]),
                "detail": to_jsonable(detail),
            })

        logger.info(
            "板块评分完成: %s, 全量 %d 个板块（top_n=%d 仅供下游消息面取材）",
            trade_date, len(rows), top_n,
        )
        return rows


# ============================================================
# 5. 落库
# ============================================================

async def save_sector_scores(
    trade_date: date,
    rows: list[dict],
    db: AsyncSession | None = None,
) -> int:
    """把 `score_all_sectors` 的结果 upsert 到 `sector_score`（幂等）。

    参数:
        trade_date: 交易日
        rows: `score_all_sectors` 的返回值（或兼容同字段的 dict 列表）
        db: 外部共享 session；None 时自建事务

    返回:
        int: 写入行数。
    """
    if not rows:
        logger.warning("%s 无板块评分可保存", trade_date)
        return 0

    payload: list[dict] = []
    for row in rows:
        sector_code = row.get("sector_code")
        if not sector_code:
            continue
        payload.append({
            "trade_date": trade_date,
            "sector_code": str(sector_code),
            "sector_name": row.get("sector_name"),
            "technical_score": to_jsonable(row.get("technical_score")),
            "sentiment_score": to_jsonable(row.get("sentiment_score")),
            "news_score": to_jsonable(row.get("news_score")),
            "composite_score": to_jsonable(row.get("composite_score")),
            "strength_leaders": to_jsonable(row.get("strength_leaders")),
            "sentiment_leaders": to_jsonable(row.get("sentiment_leaders")),
            "detail": to_jsonable(row.get("detail")),
        })

    written = 0
    async with session_ctx(db) as session:
        for offset in range(0, len(payload), WRITE_CHUNK_ROWS):
            chunk = payload[offset:offset + WRITE_CHUNK_ROWS]
            if not chunk:
                continue
            stmt = pg_insert(SectorScore).values(chunk)
            update_columns: dict[str, Any] = {
                column: stmt.excluded[column]
                for column in (
                    "sector_name", "technical_score", "sentiment_score",
                    "news_score", "composite_score", "strength_leaders",
                    "sentiment_leaders", "detail",
                )
            }
            update_columns["updated_at"] = func.now()
            stmt = stmt.on_conflict_do_update(
                index_elements=["trade_date", "sector_code"],
                set_=update_columns,
            )
            await session.execute(stmt)
            await commit_or_flush(session, external=db is not None)
            written += len(chunk)

    logger.info("板块评分落库完成: %s, %d 行", trade_date, written)
    return written


async def compute_and_save_sector_scores(
    trade_date: date,
    top_n: int = 30,
    db: AsyncSession | None = None,
) -> int:
    """便捷入口：全量板块评分 + 落库（pipeline / CLI 的 `sector_scores` 任务用）。

    返回写入行数；无板块数据时返回 0。
    """
    rows = await score_all_sectors(trade_date, top_n=top_n, db=db)
    if not rows:
        return 0
    return await save_sector_scores(trade_date, rows, db=db)


__all__ = [
    "SECTOR_COMPOSITE_WEIGHTS",
    "SECTOR_SENTIMENT_PARTS_WEIGHTS",
    "SECTOR_TECH_LOOKBACK",
    "MIN_TECH_HISTORY",
    "calc_sector_technical",
    "score_all_sectors",
    "save_sector_scores",
    "compute_and_save_sector_scores",
]
