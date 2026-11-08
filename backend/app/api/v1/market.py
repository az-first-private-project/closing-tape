"""
行情 API（替代旧 /api/v1/analysis/*）
=====================================
GET /api/v1/market/dashboard   盘后分析仪表盘（一次返回全部卡片数据）
GET /api/v1/market/indices     指数日线
GET /api/v1/market/sectors     板块日线（含评分）
GET /api/v1/market/sector/{c}  单板块详情

设计要点：
  - 所有数据**只读本地库**，绝不在请求路径打外部接口（方案 5 原则 1）
  - 无数据返回 200 + 空数组 + freshness 说明，不返回 500
  - 评分缺失时明确返回 null（前端显示"未评分"），不再用 5.0 冒充
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BENCHMARK_INDEX, INDEX_MAP
from app.core.errors import DataUnavailableError, InvalidParamError
from app.core.logging import get_logger
from app.db.models import (
    DailyReport, DimSectorMember, DimStock, FactSectorDaily, FactStockDaily, MarginTrading,
    MarketIndex, MarketTemperatureDaily, SectorScore,
)
from app.db.session import get_db
from app.engine.index_perf import WINDOW_KEYS, window_returns
from app.services.freshness import get_freshness_items

router = APIRouter(prefix="/api/v1/market", tags=["行情"])
logger = get_logger(__name__)

DASHBOARD_DATASETS = ["stock_daily", "sector_daily", "stock_scores", "daily_report", "news"]


# ============================================================
# 工具
# ============================================================

def _f(value: Any) -> float | None:
    """Decimal/None → float/None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _latest_trade_date(db: AsyncSession) -> date | None:
    """最新有板块或个股数据的交易日。

    优先用个股日线（覆盖面最广），没有再退回板块日线、指数日线。
    """
    for column in (FactStockDaily.trade_date, FactSectorDaily.trade_date,
                   MarketIndex.trade_date):
        row = (await db.execute(select(column).order_by(column.desc()).limit(1))).first()
        if row and row[0]:
            return row[0]
    return None


# ============================================================
# 仪表盘
# ============================================================

@router.get("/dashboard")
async def get_dashboard(
    trade_date: Optional[date] = Query(None, description="交易日，默认最新"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """盘后分析仪表盘。响应结构见 docs/V5-API-CONTRACT.md 3.1。"""
    resolved = trade_date or await _latest_trade_date(db)
    freshness = await get_freshness_items(db, DASHBOARD_DATASETS)

    if resolved is None:
        return {
            "trade_date": str(date.today()),
            "indices": [], "us_indices": [], "sectors": [], "heatmap_data": [],
            "top_sectors": [], "bottom_sectors": [], "margin_data": {}, "report": None,
            "freshness": freshness,
            "stats": {"scored_sectors": 0, "total_sectors": 0, "signal_count": 0},
            "empty_reason": "数据库中还没有任何行情数据，请先执行采集任务",
        }

    indices = await _query_indices(db, resolved)
    us_indices = await _query_us_indices(db, resolved)
    sectors = await _query_sectors(db, resolved)
    margin = await _query_margin(db, resolved)
    report = await _query_report(db, resolved)

    scored = sum(1 for s in sectors if s.get("composite_score") is not None)
    by_pct = sorted(sectors, key=lambda s: (s["pct_chg"] if s["pct_chg"] is not None else -999),
                    reverse=True)
    heatmap = sorted(
        [{"sector_code": s["sector_code"], "sector_name": s["sector_name"],
          "pct_chg": s["pct_chg"] or 0.0} for s in sectors],
        key=lambda x: abs(x["pct_chg"]), reverse=True,
    )[:50]

    return {
        "trade_date": resolved.isoformat(),
        "indices": indices,
        "us_indices": us_indices,
        "sectors": sectors,
        "heatmap_data": heatmap,
        # C1：三个被截断数组的全量命中数（数组本身保持原有类型与顺序）
        "heatmap_total_matching": len(sectors),
        "top_sectors": by_pct[:10],
        "top_sectors_total_matching": len(sectors),
        "bottom_sectors": sorted(by_pct[-10:], key=lambda s: s["pct_chg"] or 0.0)[:10],
        "bottom_sectors_total_matching": len(sectors),
        "margin_data": margin,
        "report": report,
        "freshness": freshness,
        "stats": {
            "scored_sectors": scored,
            "total_sectors": len(sectors),
            "signal_count": await _count_signals(db, resolved),
        },
    }


# ============================================================
# 指数 / 板块
# ============================================================

@router.get("/indices")
async def get_indices(
    trade_date: Optional[date] = Query(None),
    days: int = Query(1, ge=1, le=250, description="回溯天数，>1 时返回序列"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    resolved = trade_date or await _latest_trade_date(db)
    if resolved is None:
        return {"trade_date": None, "items": []}

    if days == 1:
        return {"trade_date": resolved.isoformat(), "items": await _query_indices(db, resolved)}

    start = resolved - timedelta(days=days)
    rows = (await db.execute(
        select(MarketIndex).where(
            MarketIndex.trade_date >= start, MarketIndex.trade_date <= resolved
        ).order_by(MarketIndex.trade_date)
    )).scalars().all()
    series: dict[str, list[dict]] = {}
    for r in rows:
        series.setdefault(r.index_code, []).append({
            "trade_date": r.trade_date.isoformat(),
            "open": _f(r.open), "high": _f(r.high), "low": _f(r.low),
            "close": _f(r.close), "pct_chg": _f(r.pct_chg),
            "volume": r.volume, "amount": _f(r.amount),
        })
    return {"trade_date": resolved.isoformat(), "series": series}


@router.get("/sectors")
async def get_sectors(
    trade_date: Optional[date] = Query(None),
    sector_type: Optional[str] = Query(None, description="industry / concept"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    resolved = trade_date or await _latest_trade_date(db)
    if resolved is None:
        return {"trade_date": None, "items": []}
    items = await _query_sectors(db, resolved, sector_type)
    return {"trade_date": resolved.isoformat(), "count": len(items), "items": items}


#: 评分榜 sparkline 一次最多覆盖的板块数（榜单只取 Top 10，留足余量）
_MAX_SCORE_CODES = 100
_MAX_SECTOR_CODE_LEN = 32


def _parse_sector_codes(raw: str) -> list[str]:
    """逗号分隔板块代码 → 去重保序；非法或超量直接 422。"""
    codes: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if len(part) > _MAX_SECTOR_CODE_LEN or any(ch.isspace() for ch in part):
            raise InvalidParamError(f"无效板块代码: {part!r}", detail={"sector_code": part})
        if part not in seen:
            seen.add(part)
            codes.append(part)
    if not codes:
        raise InvalidParamError("codes 至少包含 1 个板块代码")
    if len(codes) > _MAX_SCORE_CODES:
        raise InvalidParamError(
            f"codes 最多 {_MAX_SCORE_CODES} 个，收到 {len(codes)} 个"
        )
    return codes


def _align_sector_scores(
    codes: list[str], dates: list[date], rows: list[tuple[str, date, Any]]
) -> list[dict]:
    """把稀疏评分行对齐到共同横轴：缺分日期填 null，不用 0 或前值补齐。"""
    values = {(code, trade_date): _f(score) for code, trade_date, score in rows}
    return [
        {"sector_code": code, "scores": [values.get((code, day)) for day in dates]}
        for code in codes
    ]


@router.get("/sector-scores/sparkline")
async def get_sector_score_sparkline(
    codes: str = Query(..., description="逗号分隔板块代码（最多 100 个）"),
    days: int = Query(30, ge=5, le=120, description="最近 N 个评分日"),
    end: Optional[date] = Query(None, description="截止交易日，默认最新评分日"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量近 N 个评分日综合分序列（P3-2 评分榜 sparkline）。

    横轴取库内实际存在的评分日（≤ end，最多 days 个）；某板块当日无评分时
    该点为 null，由前端断线并提示缺数据。
    """
    wanted = _parse_sector_codes(codes)
    target = end or (await db.execute(
        select(func.max(SectorScore.trade_date))
    )).scalar()
    if target is None:
        return {"end": None, "days": days, "dates": [], "items": []}

    axis = (await db.execute(
        select(SectorScore.trade_date)
        .where(SectorScore.trade_date <= target)
        .distinct()
        .order_by(SectorScore.trade_date.desc())
        .limit(days)
    )).scalars().all()
    dates = sorted(axis)
    if not dates:
        return {"end": target.isoformat(), "days": days, "dates": [], "items": []}

    rows = (await db.execute(
        select(SectorScore.sector_code, SectorScore.trade_date, SectorScore.composite_score)
        .where(
            SectorScore.sector_code.in_(wanted),
            SectorScore.trade_date >= dates[0],
            SectorScore.trade_date <= target,
        )
    )).all()
    return {
        "end": target.isoformat(),
        "days": days,
        "dates": [day.isoformat() for day in dates],
        "items": _align_sector_scores(wanted, dates, rows),
    }


@router.get("/temperature")
async def get_temperature(
    trade_date: Optional[date] = Query(None, description="交易日，默认最新有温度数据的交易日"),
    days: int = Query(1, ge=1, le=250, description="1=当日全字段；>1=返回最近 N 个交易日序列"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """市场温度计（V6 T3）。

    - `days=1`：返回指定/最新交易日的全部子项与总分；
    - `days>1`：返回最近 N 个**有温度数据**的交易日序列（按日期升序）；
    - 全无数据：按契约返回 `DATA_NOT_READY`（409），而不是空对象。
    """
    if trade_date is None:
        resolved = (await db.execute(
            select(func.max(MarketTemperatureDaily.trade_date))
        )).scalar()
        if resolved is None:
            raise DataUnavailableError(
                "还没有市场温度数据",
                next_action="运行每日管线（temperature 步骤）或 scripts/backfill_temperature.py",
            )
    else:
        resolved = trade_date
        exists = (await db.execute(
            select(MarketTemperatureDaily.trade_date).where(
                MarketTemperatureDaily.trade_date == resolved
            )
        )).first()
        if exists is None:
            raise DataUnavailableError(
                f"{resolved.isoformat()} 没有市场温度数据",
                next_action="确认该日是否为交易日，或先运行回填脚本",
                detail={"trade_date": resolved.isoformat()},
            )

    if days == 1:
        row = (await db.execute(
            select(MarketTemperatureDaily).where(
                MarketTemperatureDaily.trade_date == resolved
            )
        )).scalar_one()
        return {
            **_temperature_payload(row),
            "freshness": await get_freshness_items(db, ["market_temperature"]),
        }

    rows = (await db.execute(
        select(MarketTemperatureDaily)
        .where(MarketTemperatureDaily.trade_date <= resolved)
        .order_by(MarketTemperatureDaily.trade_date.desc())
        .limit(days)
    )).scalars().all()
    series = [_temperature_point(row) for row in reversed(rows)]
    return {
        "trade_date": resolved.isoformat(),
        "count": len(series),
        "series": series,
    }


@router.get("/sector/{sector_code}")
async def get_sector_detail(
    sector_code: str,
    trade_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单板块详情：行情 + 三面评分（含子项）+ 两类龙头 + 近 12 日趋势 + 成分股。"""
    resolved = trade_date or await _latest_trade_date(db)
    if resolved is None:
        return {"sector_code": sector_code, "empty_reason": "暂无任何行情数据"}

    daily = (await db.execute(
        select(FactSectorDaily).where(
            FactSectorDaily.sector_code == sector_code,
            FactSectorDaily.trade_date == resolved,
        )
    )).scalar_one_or_none()

    score = (await db.execute(
        select(SectorScore).where(
            SectorScore.trade_date == resolved, SectorScore.sector_code == sector_code
        )
    )).scalar_one_or_none()

    recent = (await db.execute(
        select(FactSectorDaily).where(
            FactSectorDaily.sector_code == sector_code,
            FactSectorDaily.trade_date <= resolved,
        ).order_by(FactSectorDaily.trade_date.desc()).limit(12)
    )).scalars().all()

    members = (await db.execute(
        select(DimSectorMember.stock_code, DimStock.name)
        .outerjoin(DimStock, DimStock.code == DimSectorMember.stock_code)
        .where(DimSectorMember.sector_code == sector_code)
        .where(DimSectorMember.effective_from <= resolved)
        .where((DimSectorMember.effective_to.is_(None)) |
               (DimSectorMember.effective_to > resolved))
        .limit(200)
    )).all()

    partial_count = (
        await _query_partial_membership(db, resolved, [sector_code])
    ).get(sector_code)
    members_total = (await db.execute(
        select(func.count())
        .select_from(DimSectorMember)
        .where(DimSectorMember.sector_code == sector_code)
        .where(DimSectorMember.effective_from <= resolved)
        .where((DimSectorMember.effective_to.is_(None)) |
               (DimSectorMember.effective_to > resolved))
    )).scalar_one()

    member_quotes: list[dict] = []
    if members:
        codes = [m[0] for m in members]
        name_map = {code: name for code, name in members}
        quotes = (await db.execute(
            select(FactStockDaily.code, FactStockDaily.close, FactStockDaily.pct_chg)
            .where(FactStockDaily.trade_date == resolved, FactStockDaily.code.in_(codes))
        )).all()
        qmap = {q[0]: q for q in quotes}
        member_quotes = [
            {
                "code": code,
                "name": name_map.get(code),
                "pct_chg": _f(qmap[code][2]) if code in qmap else None,
            }
            for code in codes
        ]

    return {
        "sector_code": sector_code,
        "sector_name": daily.sector_name if daily else None,
        "sector_type": daily.sector_type if daily else None,
        "is_partial": partial_count is not None,
        "partial_member_count": partial_count,
        "trade_date": resolved.isoformat(),
        "market_data": {
            "close": _f(daily.close) if daily else None,
            "pct_chg": _f(daily.pct_chg) if daily else None,
            "open": _f(daily.open) if daily else None,
            "high": _f(daily.high) if daily else None,
            "low": _f(daily.low) if daily else None,
            "volume": daily.volume if daily else None,
            "amount": _f(daily.amount) if daily else None,
            "turnover_rate": _f(daily.turnover_rate) if daily else None,
            "up_count": daily.up_count if daily else None,
            "down_count": daily.down_count if daily else None,
            "leading_stock": daily.leading_stock if daily else None,
            "leading_stock_pct": _f(daily.leading_stock_pct) if daily else None,
        },
        "scores": {
            "technical": _f(score.technical_score) if score else None,
            "sentiment": _f(score.sentiment_score) if score else None,
            "news": _f(score.news_score) if score else None,
            "composite": _f(score.composite_score) if score else None,
            "detail": (score.detail if score else None),
        } if score else None,
        "leaders": {
            "strength": score.strength_leaders if score else None,
            "sentiment": score.sentiment_leaders if score else None,
        },
        "recent_trend": [
            {"date": r.trade_date.isoformat(), "close": _f(r.close), "pct_chg": _f(r.pct_chg)}
            for r in reversed(recent)
        ],
        "members": member_quotes,
        # C1：members 最多返回 200 条，这里给出该日有效成分的全量命中数
        "members_total_matching": int(members_total or 0),
        "total_matching": int(members_total or 0),
        "freshness": await get_freshness_items(db, ["sector_daily", "stock_scores"]),
    }


# ============================================================
# 内部查询
# ============================================================

#: 多周期收益回看窗口（自然日）。400 天约 270 个交易日，覆盖 YTD + 60 日窗口
#: 以及「上一年最后收盘」这个 YTD 基准。
INDEX_PERF_LOOKBACK_DAYS = 400


async def _query_index_perf(
    db: AsyncSession, codes: list[str], trade_date: date
) -> dict[str, dict[str, float | None]]:
    """一次 SQL 取回所有指数的最长回看窗口，再在 Python 侧按指数切片。

    不要按指数逐个查：7 个指数 × 5 个窗口会变成 35 次查询。
    """
    unique_codes = list(dict.fromkeys(codes))
    if not unique_codes:
        return {}
    start = trade_date - timedelta(days=INDEX_PERF_LOOKBACK_DAYS)
    rows = (await db.execute(
        select(MarketIndex.index_code, MarketIndex.trade_date, MarketIndex.close)
        .where(MarketIndex.index_code.in_(unique_codes))
        .where(MarketIndex.trade_date >= start)
        .where(MarketIndex.trade_date <= trade_date)
        .order_by(MarketIndex.trade_date)
    )).all()

    series: dict[str, list[tuple[date, float]]] = {}
    for code, row_date, close in rows:
        value = _f(close)
        if value is not None:
            series.setdefault(code, []).append((row_date, value))
    return {
        code: window_returns(series.get(code, []), trade_date)
        for code in unique_codes
    }


def _relative(value: float | None, benchmark: float | None) -> float | None:
    """相对基准的超额（百分点）。任一侧缺失 → null，不拿 0 冒充。"""
    if value is None or benchmark is None:
        return None
    return round(value - benchmark, 4)


def _attach_perf(
    item: dict, perf: dict[str, float | None], benchmark: dict[str, float | None],
    is_a_share: bool,
) -> dict:
    for key in WINDOW_KEYS:
        item[key] = perf.get(key)
    # 跨市场比较无意义：美股只给绝对收益
    item["rel_300_20d"] = (
        _relative(perf.get("ret_20d"), benchmark.get("ret_20d")) if is_a_share else None
    )
    item["rel_300_ytd"] = (
        _relative(perf.get("ret_ytd"), benchmark.get("ret_ytd")) if is_a_share else None
    )
    return item


async def _query_indices(db: AsyncSession, trade_date: date) -> list[dict]:
    rows = (await db.execute(
        select(MarketIndex).where(MarketIndex.trade_date == trade_date)
    )).scalars().all()
    if not rows:
        return []

    perf_map = await _query_index_perf(db, [r.index_code for r in rows], trade_date)
    benchmark = perf_map.get(BENCHMARK_INDEX, {})

    order = {code: i for i, code in enumerate(INDEX_MAP.keys())}
    rows = sorted(rows, key=lambda r: order.get(r.index_code, 99))
    items: list[dict] = []
    for r in rows:
        item = {
            "index_code": r.index_code,
            "index_name": r.index_name,
            "open": _f(r.open), "high": _f(r.high), "low": _f(r.low),
            "close": _f(r.close), "pre_close": _f(r.pre_close),
            "pct_chg": _f(r.pct_chg),
            "volume": r.volume,
            "amount": _f(r.amount),
        }
        is_a_share = INDEX_MAP.get(r.index_code, ("", False))[1]
        items.append(_attach_perf(item, perf_map.get(r.index_code, {}), benchmark,
                                  is_a_share))
    return items


async def _query_us_indices(db: AsyncSession, trade_date: date) -> list[dict]:
    """美股指数：可能落后一个交易日，取最近 3 天内最新一条。"""
    start = trade_date - timedelta(days=3)
    rows = (await db.execute(
        select(MarketIndex)
        .where(MarketIndex.index_code.in_(["^DJI", "^GSPC", "^IXIC"]))
        .where(MarketIndex.trade_date >= start)
        .where(MarketIndex.trade_date <= trade_date)
        .order_by(MarketIndex.trade_date.desc())
    )).scalars().all()

    latest: dict[str, dict] = {}
    for r in rows:
        if r.index_code in latest:
            continue
        latest[r.index_code] = {
            "index_code": r.index_code,
            "index_name": r.index_name,
            "close": _f(r.close),
            "pct_chg": _f(r.pct_chg),
            "trade_date": r.trade_date.isoformat(),
        }
    if not latest:
        return []

    perf_map = await _query_index_perf(db, list(latest), trade_date)
    for code, item in latest.items():
        # as_of 用 A 股交易日；美股锚点会自动回退到最近一根收盘
        _attach_perf(item, perf_map.get(code, {}), {}, is_a_share=False)
    return list(latest.values())


async def _query_sectors(db: AsyncSession, trade_date: date,
                         sector_type: str | None = None) -> list[dict]:
    stmt = select(FactSectorDaily).where(FactSectorDaily.trade_date == trade_date)
    if sector_type:
        stmt = stmt.where(FactSectorDaily.sector_type == sector_type)
    dailies = (await db.execute(stmt)).scalars().all()
    if not dailies:
        return []

    scores = (await db.execute(
        select(SectorScore).where(SectorScore.trade_date == trade_date)
    )).scalars().all()
    score_map = {s.sector_code: s for s in scores}
    partial_map = await _query_partial_membership(
        db, trade_date, [d.sector_code for d in dailies]
    )

    items: list[dict] = []
    for d in dailies:
        s = score_map.get(d.sector_code)
        items.append({
            "sector_code": d.sector_code,
            "sector_name": d.sector_name,
            "sector_type": d.sector_type,
            "close": _f(d.close),
            "pct_chg": _f(d.pct_chg),
            "amount": _f(d.amount),
            "turnover_rate": _f(d.turnover_rate),
            "up_count": d.up_count,
            "down_count": d.down_count,
            "leading_stock": d.leading_stock,
            "leading_stock_pct": _f(d.leading_stock_pct),
            "market_cap": _f(d.market_cap),
            "float_cap": _f(d.float_cap),
            "main_net_inflow": _f(d.main_net_inflow),
            "rs_20d": _f(d.rs_20d),
            "member_count": d.member_count,
            "is_partial": d.sector_code in partial_map,
            "partial_member_count": partial_map.get(d.sector_code),
            "technical_score": _f(s.technical_score) if s else None,
            "sentiment_score": _f(s.sentiment_score) if s else None,
            "news_score": _f(s.news_score) if s else None,
            "composite_score": _f(s.composite_score) if s else None,
            "strength_leaders": s.strength_leaders if s else None,
            "sentiment_leaders": s.sentiment_leaders if s else None,
        })
    return items


async def _query_partial_membership(
    db: AsyncSession, trade_date: date, sector_codes: list[str] | None = None
) -> dict[str, int]:
    """返回「只有 partial 样本名单」的板块及其样本成员数。

    THS 对 pages>10 的板块只暴露前后各 5 页，整份名单入库时标记 is_partial。
    这类板块的名单不能参与家数/市值聚合，前端必须显式提示「名单不完整」。
    """
    if sector_codes is not None and not sector_codes:
        return {}
    stmt = (
        select(
            DimSectorMember.sector_code,
            func.count().label("total"),
            func.count().filter(DimSectorMember.is_partial.is_(True)).label("partial"),
        )
        .where(DimSectorMember.effective_from <= trade_date)
        .where(
            (DimSectorMember.effective_to.is_(None))
            | (DimSectorMember.effective_to > trade_date)
        )
        .group_by(DimSectorMember.sector_code)
    )
    if sector_codes is not None:
        stmt = stmt.where(DimSectorMember.sector_code.in_(sector_codes))
    rows = (await db.execute(stmt)).all()
    return {
        code: int(partial)
        for code, total, partial in rows
        if total and total == partial
    }


async def _query_margin(db: AsyncSession, trade_date: date) -> dict:
    row = (await db.execute(
        select(MarginTrading).where(MarginTrading.trade_date == trade_date)
    )).scalar_one_or_none()
    if not row:
        return {}
    return {
        "trade_date": row.trade_date.isoformat(),
        "margin_balance": _f(row.margin_balance),
        "short_balance": _f(row.short_balance),
        "margin_change": _f(row.margin_change),
        "northward_net": _f(row.northward_net),
    }


async def _query_report(db: AsyncSession, trade_date: date) -> dict | None:
    row = (await db.execute(
        select(DailyReport).where(DailyReport.trade_date == trade_date)
    )).scalar_one_or_none()
    if not row:
        return None
    return {
        "content": row.content,
        "structured": row.structured,
        "model": row.model,
        "is_fallback": bool(row.is_fallback),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _temperature_point(row: MarketTemperatureDaily) -> dict:
    """温度序列中的单个交易日数据点。"""
    return {
        "trade_date": row.trade_date.isoformat(),
        "temp_score": _f(row.temp_score),
        "confidence": row.confidence,
        "above_ma20_ratio": _f(row.above_ma20_ratio),
        "amount_vs_ma20": _f(row.amount_vs_ma20),
        "index_vol_annual": _f(row.index_vol_annual),
        "up_count": row.up_count,
        "down_count": row.down_count,
        "flat_count": row.flat_count,
        "total_count": row.total_count,
        "limit_up_count": row.limit_up_count,
        "limit_down_count": row.limit_down_count,
        "median_pct": _f(row.median_pct),
        "amount": _f(row.amount),
    }


def _temperature_payload(row: MarketTemperatureDaily) -> dict:
    """单日全字段（含六个子项的原始值/得分/说明）。"""
    return {
        **_temperature_point(row),
        "components": row.components or {},
        "src": row.src,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _count_signals(db: AsyncSession, trade_date: date) -> int:
    """当日信号数：每个策略只取最新一条有效 run，避免重复/回测 run 叠加。"""
    from app.db.models import StgRun
    latest = (
        select(StgRun.algo_code, StgRun.signal_count)
        .where(StgRun.trade_date == trade_date)
        .where(StgRun.is_backtest.is_(False))
        .where(StgRun.status == "success")
        .distinct(StgRun.algo_code)
        .order_by(
            StgRun.algo_code,
            StgRun.created_at.desc().nullslast(),
            StgRun.run_id.desc(),
        )
        .subquery()
    )
    total = (await db.execute(
        select(func.coalesce(func.sum(latest.c.signal_count), 0))
    )).scalar()
    return int(total or 0)


__all__ = ["router"]
