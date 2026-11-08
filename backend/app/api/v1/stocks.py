"""
个股 API（方案 E4：个股详情页）
==============================
GET /api/v1/stocks/search?q=        代码/名称模糊搜索
GET /api/v1/stocks/sparkline/batch  批量近 N 日收盘（共识榜/评分榜走势）
GET /api/v1/stocks/{code}           个股快照：行情 + 指标 + 因子评分 + 所属板块
GET /api/v1/stocks/{code}/kline      K 线序列（含均线，供前端画图）
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParamError, NotFoundError
from app.core.pagination import pagination_meta
from app.db.models import (
    DimSectorMember, DimStock, FactFeatureDaily, FactStockDaily, FactStockScoreDaily,
    MarketIndex,
)
from app.db.session import get_db
from app.services.freshness import get_freshness_items

router = APIRouter(prefix="/api/v1/stocks", tags=["个股"])


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _latest_trade_date(db: AsyncSession) -> date | None:
    row = (await db.execute(
        select(FactStockDaily.trade_date).order_by(FactStockDaily.trade_date.desc()).limit(1)
    )).first()
    return row[0] if row else None


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=1, max_length=32, description="代码或名称片段"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, description="分页偏移（C1）"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """代码前缀或名称包含匹配。停牌/退市标的也返回，但带 status 标记。"""
    pattern = f"%{q}%"
    filters = or_(DimStock.code.like(f"{q}%"), DimStock.name.like(pattern))
    total = (await db.execute(
        select(func.count()).select_from(DimStock).where(filters)
    )).scalar_one()
    rows = (await db.execute(
        select(DimStock.code, DimStock.name, DimStock.industry_l1, DimStock.board,
               DimStock.is_st, DimStock.status)
        .where(filters)
        .order_by(DimStock.code)
        .offset(offset)
        .limit(limit)
    )).all()
    return {
        "items": [
            {"code": r[0], "name": r[1], "industry_l1": r[2], "board": r[3],
             "is_st": bool(r[4]), "status": r[5]}
            for r in rows
        ],
        **pagination_meta(total=total, limit=limit, offset=offset, returned=len(rows)),
    }


#: 批量走势一次最多覆盖的股票数（共识榜前端默认 100 条）
_MAX_BATCH_CODES = 200


def _parse_batch_codes(raw: str) -> list[str]:
    """逗号分隔代码 → 去重保序的 6 位代码；非法或超量直接 422。"""
    codes: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or len(part) > 6:
            raise InvalidParamError(f"无效股票代码: {part!r}", detail={"code": part})
        code = part.zfill(6)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    if not codes:
        raise InvalidParamError("codes 至少包含 1 个股票代码")
    if len(codes) > _MAX_BATCH_CODES:
        raise InvalidParamError(
            f"codes 最多 {_MAX_BATCH_CODES} 个，收到 {len(codes)} 个"
        )
    return codes


@router.get("/sparkline/batch")
async def get_sparkline_batch(
    codes: str = Query(..., description="逗号分隔的股票代码（最多 200 个）"),
    days: int = Query(30, ge=5, le=120),
    end: Optional[date] = Query(None, description="截止交易日，默认最新行情日"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量近 N 日收盘序列，口径与 4.2 的 kline 一致（未复权、时间升序）。

    一次查询覆盖整页标的，替代前端逐股请求；缺失/停牌代码返回空数组，
    由前端显示「—」，不参与补齐或插值。
    """
    wanted = _parse_batch_codes(codes)
    target = end or await _latest_trade_date(db)
    series: dict[str, list[float | None]] = {code: [] for code in wanted}
    if target is not None:
        # 多取日历天以覆盖节假日，按代码各保留最近 days 个交易日
        floor = target - timedelta(days=days * 2 + 10)
        rows = (await db.execute(
            select(FactStockDaily.code, FactStockDaily.trade_date, FactStockDaily.close)
            .where(
                FactStockDaily.code.in_(wanted),
                FactStockDaily.trade_date >= floor,
                FactStockDaily.trade_date <= target,
            )
            .order_by(FactStockDaily.code, FactStockDaily.trade_date.desc())
        )).all()
        for code, _trade_date, close in rows:
            bucket = series[code]
            if len(bucket) < days:
                bucket.append(_f(close))
        for code in wanted:
            series[code].reverse()
    return {
        "end": target.isoformat() if target else None,
        "days": days,
        "adjust_basis": "raw",
        "items": [{"code": code, "closes": series[code]} for code in wanted],
    }


@router.get("/{code}")
async def get_stock_detail(
    code: str,
    trade_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """个股快照。响应结构见 docs/V5-API-CONTRACT.md 4.1。"""
    code = code.zfill(6)
    dim = (await db.execute(select(DimStock).where(DimStock.code == code))).scalar_one_or_none()
    if dim is None:
        raise NotFoundError(f"标的不存在: {code}", detail={"code": code})

    resolved = trade_date or await _latest_trade_date(db)
    if trade_date is not None:
        # 指定日期无该股行情（停牌/非交易日/超出覆盖）时，回退到之前最近的交易日
        fallback = (await db.execute(
            select(func.max(FactStockDaily.trade_date)).where(
                FactStockDaily.code == code,
                FactStockDaily.trade_date <= trade_date,
            )
        )).scalar_one_or_none()
        if fallback is not None:
            resolved = fallback
    daily = feature = score = None
    if resolved:
        daily = (await db.execute(
            select(FactStockDaily).where(
                FactStockDaily.code == code, FactStockDaily.trade_date == resolved
            )
        )).scalar_one_or_none()
        feature = (await db.execute(
            select(FactFeatureDaily).where(
                FactFeatureDaily.code == code, FactFeatureDaily.trade_date == resolved
            )
        )).scalar_one_or_none()
        score = (await db.execute(
            select(FactStockScoreDaily).where(
                FactStockScoreDaily.code == code, FactStockScoreDaily.trade_date == resolved
            )
        )).scalar_one_or_none()

    # 当前所属板块（SCD2 有效区间）
    sectors = []
    sectors_total = 0
    if resolved:
        rows = (await db.execute(
            select(DimSectorMember.sector_code, DimSectorMember.sector_name)
            .where(DimSectorMember.stock_code == code)
            .where(DimSectorMember.effective_from <= resolved)
            .where((DimSectorMember.effective_to.is_(None)) |
                   (DimSectorMember.effective_to > resolved))
        )).all()
        sectors_total = len(rows)
        # C1：响应仍最多 20 条，全量命中数由 sectors_total_matching 提供
        sectors = [{"sector_code": r[0], "sector_name": r[1]} for r in rows[:20]]

    return {
        "code": dim.code,
        "name": dim.name,
        "exchange": dim.exchange,
        "board": dim.board,
        "industry_l1": dim.industry_l1,
        "industry_l2": dim.industry_l2,
        "ownership": dim.ownership,
        "is_st": bool(dim.is_st),
        "list_date": dim.list_date.isoformat() if dim.list_date else None,
        "status": dim.status,
        "trade_date": resolved.isoformat() if resolved else None,
        "latest": {
            "trade_date": daily.trade_date.isoformat() if daily else None,
            "open": _f(daily.open) if daily else None,
            "high": _f(daily.high) if daily else None,
            "low": _f(daily.low) if daily else None,
            "close": _f(daily.close) if daily else None,
            "pre_close": _f(daily.pre_close) if daily else None,
            "pct_chg": _f(daily.pct_chg) if daily else None,
            "volume": daily.volume if daily else None,
            "amount": _f(daily.amount) if daily else None,
            "turnover_rate": _f(daily.turnover_rate) if daily else None,
            "market_cap": _f(daily.market_cap) if daily else None,
            "float_cap": _f(daily.float_cap) if daily else None,
            "pe_ttm": _f(daily.pe_ttm) if daily else None,
            "pb": _f(daily.pb) if daily else None,
            "ps_ttm": _f(daily.ps_ttm) if daily else None,
            "dv_ttm": _f(daily.dv_ttm) if daily else None,
            "is_limit_up": bool(daily.is_limit_up) if daily else None,
            "is_limit_down": bool(daily.is_limit_down) if daily else None,
            "is_suspended": bool(daily.is_suspended) if daily else None,
        },
        "features": {
            "ma5": _f(feature.ma5), "ma10": _f(feature.ma10),
            "ma20": _f(feature.ma20), "ma60": _f(feature.ma60),
            "rsi14": _f(feature.rsi14),
            "macd_dif": _f(feature.macd_dif), "macd_dea": _f(feature.macd_dea),
            "macd_hist": _f(feature.macd_hist),
            "macd_golden_cross": bool(feature.macd_golden_cross),
            "boll_mid": _f(feature.boll_mid), "boll_upper": _f(feature.boll_upper),
            "boll_lower": _f(feature.boll_lower), "atr14": _f(feature.atr14),
            "momentum_5": _f(feature.momentum_5), "momentum_20": _f(feature.momentum_20),
            "momentum_60": _f(feature.momentum_60),
            "volatility_20": _f(feature.volatility_20),
            "volume_ratio": _f(feature.volume_ratio),
            "ma_bullish": bool(feature.ma_bullish),
        } if feature else None,
        "scores": {
            "value": _f(score.value_score),
            "quality": _f(score.quality_score),
            "momentum": _f(score.momentum_score),
            "volatility": _f(score.volatility_score),
            "liquidity": _f(score.liquidity_score),
            "sentiment": _f(score.sentiment_score),
            "composite": _f(score.composite_score),
            "detail": score.detail,
        } if score else None,
        "sectors": sectors,
        "sectors_total_matching": sectors_total,
        "freshness": await get_freshness_items(db, ["stock_daily", "stock_features", "stock_scores"]),
    }


@router.get("/{code}/kline")
async def get_kline(
    code: str,
    limit: int = Query(250, ge=20, le=1500),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """K 线序列（时间升序）。含 MA5/MA20/MA60、BOLL 与成交量，够前端画图。

    复权口径 `adjust_basis = "raw"`：OHLC 直接取 `fact_stock_daily.close`（未复权），
    MA/BOLL 也由 `features.py` 在同一组未复权收盘价上计算（见 `_load_daily_frames`，
    不读 `adj_factor`）。前端标记回测成交价时也按此口径落位；回测估值虽用后复权，
    但 `trades[].price` 存的是未复权成交价（backtest.py `_buy_price(bar.open)`）。
    """
    code = code.zfill(6)
    end = end or await _latest_trade_date(db)
    if end is None:
        return {"code": code, "adjust_basis": "raw", "items": []}
    stmt = select(FactStockDaily.trade_date, FactStockDaily.open, FactStockDaily.high,
                  FactStockDaily.low, FactStockDaily.close, FactStockDaily.volume,
                  FactStockDaily.amount, FactStockDaily.pct_chg).where(
        FactStockDaily.code == code, FactStockDaily.trade_date <= end
    )
    if start:
        stmt = stmt.where(FactStockDaily.trade_date >= start)
    rows = (await db.execute(
        stmt.order_by(FactStockDaily.trade_date.desc()).limit(limit)
    )).all()

    dates = [r[0] for r in rows]
    features: dict[date, FactFeatureDaily] = {}
    if dates:
        frows = (await db.execute(
            select(FactFeatureDaily).where(
                FactFeatureDaily.code == code,
                FactFeatureDaily.trade_date >= min(dates),
                FactFeatureDaily.trade_date <= max(dates),
            )
        )).scalars().all()
        features = {f.trade_date: f for f in frows}

    items = []
    for r in reversed(rows):
        f = features.get(r[0])
        items.append({
            "trade_date": r[0].isoformat(),
            "open": _f(r[1]), "high": _f(r[2]), "low": _f(r[3]), "close": _f(r[4]),
            "volume": r[5], "amount": _f(r[6]), "pct_chg": _f(r[7]),
            "ma5": _f(f.ma5) if f else None,
            "ma20": _f(f.ma20) if f else None,
            "ma60": _f(f.ma60) if f else None,
            "boll_mid": _f(f.boll_mid) if f else None,
            "boll_upper": _f(f.boll_upper) if f else None,
            "boll_lower": _f(f.boll_lower) if f else None,
        })
    return {"code": code, "adjust_basis": "raw", "count": len(items), "items": items}


@router.get("/{code}/benchmark")
async def get_benchmark_series(
    code: str,
    limit: int = Query(250, ge=20, le=1500),
    benchmark: str = Query("sh000300", description="基准指数代码"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """个股与基准的归一化对比序列（选股详情页用）。"""
    code = code.zfill(6)
    latest = await _latest_trade_date(db)
    if latest is None:
        return {"code": code, "benchmark": benchmark, "items": []}

    stock_rows = (await db.execute(
        select(FactStockDaily.trade_date, FactStockDaily.close)
        .where(FactStockDaily.code == code)
        .order_by(FactStockDaily.trade_date.desc()).limit(limit)
    )).all()
    start = min(r[0] for r in stock_rows) if stock_rows else latest - timedelta(days=365)

    bench_rows = (await db.execute(
        select(MarketIndex.trade_date, MarketIndex.close)
        .where(MarketIndex.index_code == benchmark)
        .where(MarketIndex.trade_date >= start)
        .where(MarketIndex.trade_date <= latest)
        .order_by(MarketIndex.trade_date)
    )).all()

    def _norm(rows) -> dict[date, float]:
        rows = [(r[0], _f(r[1])) for r in rows if _f(r[1])]
        if not rows:
            return {}
        base = rows[0][1]
        return {d: round(v / base, 6) for d, v in rows if base}

    stock_norm = _norm(list(reversed(stock_rows)))
    bench_norm = _norm(list(bench_rows))
    dates = sorted(set(stock_norm) | set(bench_norm))
    return {
        "code": code,
        "benchmark": benchmark,
        "items": [
            {"trade_date": d.isoformat(),
             "stock": stock_norm.get(d), "benchmark": bench_norm.get(d)}
            for d in dates
        ],
    }


__all__ = ["router"]
