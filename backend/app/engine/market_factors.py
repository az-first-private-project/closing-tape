"""市场级原始因子引擎（V7 T1）
================================
把「指数 + 全 A 个股聚合 + 宏观利率 + 两融」压成每日一行的原始因子快照，
写入 `fact_market_daily`。标准化分位不在这里做（需要 750 日滚动窗口，
放在 `market_regime.py`），这里只保证：

- 所有窗口只使用 T 日及以前的数据，**没有未来函数**；
- 缺数据存 NULL，不用 0 冒充；
- 批量回补与单日增量共用同一套计算函数，避免两套口径。

批量入口 `compute_market_factors_range` 一次加载区间数据，按股票算滚动
MA30 / 60 日新高，再按日聚合；单日增量也走同一入口（区间长度为 1）。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    BENCHMARK_INDEX,
    DEFENSIVE_INDUSTRIES,
    POOL_BOARDS,
    STATUS_NORMAL,
    is_pool_stock_code,
)
from app.core.logging import get_logger
from app.db.models.dim import DimStock
from app.db.models.fact import (
    FactMarketDaily,
    FactStockDaily,
    MacroRate,
    MarginTrading,
    MarketIndex,
)
from app.engine._db import session_ctx

logger = get_logger(__name__)

# 需要多少历史：MA30 / 60 日新高 / 20 日收益 / 60 日波动
STOCK_LOOKBACK_DAYS = 120
INDEX_LOOKBACK_DAYS = 420
MIN_TOTAL_COUNT = 2500
FULL_COVERAGE_COUNT = 3500


# ============================================================
# 纯函数：技术指标
# ============================================================

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = true_range(high, low, close)
    atr_value = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_value.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_value.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean(), plus_di, minus_di


def macd_hist(close: pd.Series) -> pd.Series:
    dif = ema(close, 12) - ema(close, 26)
    dea = ema(dif, 9)
    return dif - dea


def choppiness(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    tr_sum = true_range(high, low, close).rolling(period).sum()
    span = high.rolling(period).max() - low.rolling(period).min()
    ratio = tr_sum / span.replace(0, np.nan)
    return 100 * np.log10(ratio) / math.log10(period)


def efficiency_ratio(close: pd.Series, period: int = 20) -> pd.Series:
    change = (close - close.shift(period)).abs()
    path = close.diff().abs().rolling(period).sum()
    return change / path.replace(0, np.nan)


def max_drawdown(close: pd.Series, period: int = 60) -> pd.Series:
    def _mdd(values: np.ndarray) -> float:
        peak = np.maximum.accumulate(values)
        return float(np.max(1 - values / peak)) * 100 if len(values) else np.nan

    return close.rolling(period).apply(_mdd, raw=True)


def boll_bandwidth(close: pd.Series, period: int = 20, width: float = 2.0) -> pd.Series:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + width * std
    lower = mid - width * std
    return (upper - lower) / mid.replace(0, np.nan) * 100


def annualized_vol(returns: pd.Series, period: int) -> pd.Series:
    return returns.rolling(period).std(ddof=1) * math.sqrt(252)


def _last(series: pd.Series, default: float | None = None) -> float | None:
    if series is None or len(series) == 0:
        return default
    value = series.iloc[-1]
    if value is None or not np.isfinite(value):
        return default
    return float(value)


# ============================================================
# 数据加载
# ============================================================

async def _load_index(
    session: AsyncSession, start: date, end: date
) -> pd.DataFrame:
    rows = (await session.execute(
        select(
            MarketIndex.trade_date,
            MarketIndex.open,
            MarketIndex.high,
            MarketIndex.low,
            MarketIndex.close,
            MarketIndex.amount,
        )
        .where(MarketIndex.index_code == BENCHMARK_INDEX)
        .where(MarketIndex.trade_date >= start)
        .where(MarketIndex.trade_date <= end)
        .order_by(MarketIndex.trade_date)
    )).all()
    frame = pd.DataFrame(rows, columns=[
        "trade_date", "open", "high", "low", "close", "amount"
    ])
    if frame.empty:
        return frame
    for column in ("open", "high", "low", "close", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


async def _load_stocks(
    session: AsyncSession, start: date, end: date
) -> pd.DataFrame:
    rows = (await session.execute(
        select(
            FactStockDaily.code,
            FactStockDaily.trade_date,
            FactStockDaily.close,
            FactStockDaily.high,
            FactStockDaily.amount,
            FactStockDaily.float_cap,
            FactStockDaily.pe_ttm,
            FactStockDaily.pb,
            FactStockDaily.dv_ttm,
            FactStockDaily.pct_chg,
            FactStockDaily.is_limit_up,
            FactStockDaily.is_limit_down,
            FactStockDaily.is_suspended,
        )
        .where(FactStockDaily.trade_date >= start)
        .where(FactStockDaily.trade_date <= end)
        .where(FactStockDaily.close.isnot(None))
        .where(FactStockDaily.code.in_(
            select(DimStock.code).where(
                DimStock.status == STATUS_NORMAL,
                DimStock.board.in_(POOL_BOARDS),
            )
        ))
    )).all()
    frame = pd.DataFrame(rows, columns=[
        "code", "trade_date", "close", "high", "amount", "float_cap",
        "pe_ttm", "pb", "dv_ttm", "pct_chg",
        "is_limit_up", "is_limit_down", "is_suspended",
    ])
    if frame.empty:
        return frame
    for column in ("close", "high", "amount", "float_cap", "pe_ttm", "pb", "dv_ttm", "pct_chg"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


async def _load_industry_map(session: AsyncSession) -> pd.DataFrame:
    rows = (await session.execute(
        select(DimStock.code, DimStock.industry_l1)
    )).all()
    return pd.DataFrame(rows, columns=["code", "industry_l1"]).drop_duplicates("code")


async def _load_macro(session: AsyncSession, start: date, end: date) -> pd.DataFrame:
    rows = (await session.execute(
        select(MacroRate.trade_date, MacroRate.cn_1y, MacroRate.cn_10y, MacroRate.term_spread)
        .where(MacroRate.trade_date >= start)
        .where(MacroRate.trade_date <= end)
        .order_by(MacroRate.trade_date)
    )).all()
    frame = pd.DataFrame(rows, columns=["trade_date", "cn_1y", "cn_10y", "term_spread"])
    if frame.empty:
        return frame
    for column in ("cn_1y", "cn_10y", "term_spread"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


async def _load_margin(session: AsyncSession, start: date, end: date) -> pd.DataFrame:
    rows = (await session.execute(
        select(MarginTrading.trade_date, MarginTrading.margin_buy_amount)
        .where(MarginTrading.trade_date >= start)
        .where(MarginTrading.trade_date <= end)
        .order_by(MarginTrading.trade_date)
    )).all()
    frame = pd.DataFrame(rows, columns=["trade_date", "margin_buy_amount"])
    if frame.empty:
        return frame
    frame["margin_buy_amount"] = pd.to_numeric(frame["margin_buy_amount"], errors="coerce")
    return frame.reset_index(drop=True)


# ============================================================
# 横截面聚合
# ============================================================

def _stock_rolling(stocks: pd.DataFrame) -> pd.DataFrame:
    """给每只股票追加 MA30 / 60 日新高标记；返回原粒度 DataFrame。"""
    if stocks.empty:
        return stocks
    frame = stocks.sort_values(["code", "trade_date"]).copy()
    grouped = frame.groupby("code", sort=False)
    frame["ma30"] = grouped["close"].transform(
        lambda series: series.rolling(30, min_periods=30).mean()
    )
    frame["high60"] = grouped["close"].transform(
        lambda series: series.rolling(60, min_periods=60).max().shift(1)
    )
    return frame


def _daily_cross_section(
    stocks: pd.DataFrame, industry_map: pd.DataFrame
) -> pd.DataFrame:
    """按交易日聚合全 A 广度、估值、行业集中度、防御篮子收益。"""
    if stocks.empty:
        return pd.DataFrame(columns=["trade_date"])
    # 双保险：即使调用方传入未过滤的原始行情，也不让北交所进入广度/温度口径
    frame = stocks[stocks["code"].map(is_pool_stock_code)].copy()
    if frame.empty:
        return pd.DataFrame(columns=["trade_date"])
    frame = _stock_rolling(frame)
    tradable = frame[~frame["is_suspended"].fillna(False).astype(bool)].copy()

    # 行业映射只用于集中度与防御篮子；缺失行业的股票不参与这两项
    if not industry_map.empty:
        tradable = tradable.merge(industry_map, on="code", how="left")
    else:
        tradable["industry_l1"] = None

    records: list[dict[str, Any]] = []
    for trade_date, day in tradable.groupby("trade_date", sort=True):
        total = len(day)
        if total == 0:
            continue
        valid_pct = day["pct_chg"].dropna()
        up = int((valid_pct > 0).sum())
        down = int((valid_pct < 0).sum())
        flat = int((valid_pct == 0).sum())
        limit_up = int(day["is_limit_up"].fillna(False).astype(bool).sum())
        limit_down = int(day["is_limit_down"].fillna(False).astype(bool).sum())

        above_ma30 = day["ma30"].notna() & (day["close"] > day["ma30"])
        ma30_sample = int(day["ma30"].notna().sum())
        new_high = day["high60"].notna() & (day["close"] >= day["high60"])
        high_sample = int(day["high60"].notna().sum())

        pb = day["pb"].dropna()
        broken = int((pb < 1).sum()) if len(pb) else None

        weights = day["float_cap"].where(day["float_cap"] > 0)
        dv = day["dv_ttm"]
        dv_mask = dv.notna() & weights.notna()
        dividend_yield = (
            float((dv[dv_mask] * weights[dv_mask]).sum() / weights[dv_mask].sum())
            if dv_mask.any() and weights[dv_mask].sum() > 0 else None
        )
        pe = day["pe_ttm"]
        pe_mask = pe.notna() & (pe > 0) & weights.notna()
        earnings_yield = (
            float(((1 / pe[pe_mask]) * weights[pe_mask]).sum() / weights[pe_mask].sum() * 100)
            if pe_mask.any() and weights[pe_mask].sum() > 0 else None
        )

        amount = day["amount"].dropna()
        total_amount = float(amount.sum()) if len(amount) else None
        cap_mask = weights.notna()
        market_turnover = (
            float(day.loc[cap_mask, "amount"].sum() / weights[cap_mask].sum() * 100)
            if cap_mask.any() and weights[cap_mask].sum() > 0 else None
        )

        industry_amount = (
            day.dropna(subset=["industry_l1"])
            .groupby("industry_l1")["amount"].sum()
        )
        concentration = None
        if len(industry_amount) >= 5:
            total_industry = float(industry_amount.sum())
            if total_industry > 0:
                concentration = float(industry_amount.nlargest(5).sum() / total_industry)

        defensive = day[day["industry_l1"].isin(DEFENSIVE_INDUSTRIES)]
        defensive_return = float(defensive["pct_chg"].mean()) if len(defensive) else None
        market_return = float(valid_pct.mean()) if len(valid_pct) else None

        records.append({
            "trade_date": trade_date,
            "up_count": up,
            "down_count": down,
            "flat_count": flat,
            "total_count": total,
            "limit_up_cnt": limit_up,
            "limit_down_cnt": limit_down,
            "adv_dec_ratio": (up / (up + down)) if (up + down) else None,
            "above_ma30_ratio": (
                float(above_ma30.sum() / ma30_sample) if ma30_sample else None
            ),
            "new_high_60_ratio": (
                float(new_high.sum() / high_sample) if high_sample else None
            ),
            "broken_net_ratio": (broken / len(pb)) if broken is not None and len(pb) else None,
            "dividend_yield": dividend_yield,
            "earnings_yield": earnings_yield,
            "amount": total_amount,
            "market_turnover": market_turnover,
            "industry_heat_concentration": concentration,
            "defensive_return": defensive_return,
            "market_return": market_return,
        })
    return pd.DataFrame(records)


# ============================================================
# 派生因子
# ============================================================

def _index_features(index: pd.DataFrame) -> pd.DataFrame:
    if index.empty:
        return index
    frame = index.copy()
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]

    frame["ret_1d"] = close.pct_change() * 100
    frame["ret_20"] = close.pct_change(20) * 100
    frame["ema20"] = ema(close, 20)
    frame["ema60"] = ema(close, 60)
    frame["ema120"] = ema(close, 120)
    frame["ema_slope60"] = (
        frame["ema60"].rolling(20).apply(
            lambda values: np.polyfit(np.arange(len(values)), values, 1)[0]
            if len(values) == 20 else np.nan,
            raw=True,
        ) / close.replace(0, np.nan)
    )
    frame["ma_alignment"] = np.select(
        [
            (close > frame["ema20"]) & (frame["ema20"] > frame["ema60"]),
            (close < frame["ema20"]) & (frame["ema20"] < frame["ema60"]),
        ],
        [1, -1],
        default=0,
    )
    adx_series, plus_di, minus_di = adx(high, low, close)
    frame["adx14"] = adx_series
    frame["di_plus"] = plus_di
    frame["di_minus"] = minus_di
    frame["efficiency_ratio"] = efficiency_ratio(close, 20)
    frame["choppiness"] = choppiness(high, low, close)
    frame["macd_hist"] = macd_hist(close)
    frame["rsi14"] = rsi(close, 14)
    ma120 = close.rolling(120, min_periods=120).mean()
    frame["price_vs_ma120"] = close / ma120.replace(0, np.nan) - 1
    frame["rv20"] = annualized_vol(frame["ret_1d"] / 100, 20) * 100
    frame["rv60"] = annualized_vol(frame["ret_1d"] / 100, 60) * 100
    frame["natr14"] = atr(high, low, close) / close.replace(0, np.nan) * 100
    frame["boll_bandwidth"] = boll_bandwidth(close)
    frame["max_drawdown_3m"] = max_drawdown(close, 60)
    return frame


def _derived(daily: pd.DataFrame, index_features: pd.DataFrame) -> pd.DataFrame:
    """把横截面聚合、指数特征、宏观与两融合成最终原始因子表。"""
    frame = daily.merge(
        index_features[[
            "trade_date", "close", "high", "low", "ret_1d", "ret_20", "ema20", "ema60", "ema120",
            "ema_slope60", "ma_alignment", "adx14", "di_plus", "di_minus",
            "efficiency_ratio", "choppiness", "macd_hist", "rsi14",
            "price_vs_ma120", "rv20", "rv60", "natr14", "boll_bandwidth",
            "max_drawdown_3m",
        ]],
        on="trade_date",
        how="left",
    ).sort_values("trade_date").reset_index(drop=True)

    amount = frame["amount"]
    frame["amount_ma20"] = amount.shift(1).rolling(20, min_periods=10).mean()
    frame["amount_vs_ma20"] = amount / frame["amount_ma20"].replace(0, np.nan)
    frame["turnover_ma5"] = frame["market_turnover"].rolling(5, min_periods=3).mean()

    amount_prev = amount.shift(1)
    ret = frame["ret_1d"]
    matched = ((ret > 0) & (amount > amount_prev)) | ((ret < 0) & (amount < amount_prev))
    frame["volume_price_match"] = matched.rolling(10, min_periods=5).mean()

    trillion = amount / 1e12
    daily_efficiency = ret / trillion.replace(0, np.nan)
    frame["capital_efficiency"] = daily_efficiency.rolling(20, min_periods=10).mean()

    if "defensive_return" in frame.columns:
        defensive_cum = (
            (1 + frame["defensive_return"] / 100).rolling(20, min_periods=15).apply(np.prod, raw=True) - 1
        ) * 100
        market_cum = (
            (1 + frame["market_return"] / 100).rolling(20, min_periods=15).apply(np.prod, raw=True) - 1
        ) * 100
        frame["defensive_rel_momentum"] = defensive_cum - market_cum
    return frame


def _merge_macro_margin(
    frame: pd.DataFrame, macro: pd.DataFrame, margin: pd.DataFrame
) -> pd.DataFrame:
    if not macro.empty:
        frame = frame.merge(macro, on="trade_date", how="left")
    else:
        frame["cn_1y"] = np.nan
        frame["cn_10y"] = np.nan
        frame["term_spread"] = np.nan

    # 两融 T+1 披露：只用 <= T 的最近一条，不做未来函数
    if not margin.empty:
        left = frame.sort_values("trade_date").copy()
        left["trade_date"] = pd.to_datetime(left["trade_date"])
        right = margin.sort_values("trade_date").copy()
        right["trade_date"] = pd.to_datetime(right["trade_date"])
        frame = pd.merge_asof(left, right, on="trade_date", direction="backward")
        frame["trade_date"] = frame["trade_date"].dt.date
    else:
        frame["margin_buy_amount"] = np.nan

    frame["erp"] = frame["earnings_yield"] - frame["cn_10y"]
    frame["bond_equity_gap"] = frame["dividend_yield"] - frame["cn_10y"]
    frame["margin_buy_ratio"] = frame["margin_buy_amount"] / frame["amount"].replace(0, np.nan)
    return frame


def _quality(row: dict[str, Any]) -> str:
    if not row.get("trade_date") or row.get("close") is None:
        return "partial"
    if (row.get("total_count") or 0) < MIN_TOTAL_COUNT:
        return "partial"
    if row.get("amount") is None or row.get("cn_10y") is None:
        return "partial"
    return "full"


# ============================================================
# 主计算 / 落库
# ============================================================

async def compute_market_factors_range(
    start: date, end: date, db: AsyncSession | None = None
) -> int:
    """计算并 upsert [start, end] 区间的原始因子，返回写入行数。"""
    async with session_ctx(db) as session:
        index = await _load_index(
            session, start - timedelta(days=INDEX_LOOKBACK_DAYS), end
        )
        stocks = await _load_stocks(
            session, start - timedelta(days=STOCK_LOOKBACK_DAYS), end
        )
        industry_map = await _load_industry_map(session)
        macro = await _load_macro(session, start - timedelta(days=30), end)
        margin = await _load_margin(session, start - timedelta(days=30), end)

        if index.empty or stocks.empty:
            logger.warning("市场因子 %s ~ %s：指数或个股日线为空", start, end)
            return 0

        index_features = _index_features(index)
        daily = _daily_cross_section(stocks, industry_map)
        if daily.empty:
            logger.warning("市场因子 %s ~ %s：横截面聚合为空", start, end)
            return 0

        frame = _derived(daily, index_features)
        frame = _merge_macro_margin(frame, macro, margin)
        frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]

        rows: list[dict[str, Any]] = []
        for record in frame.to_dict("records"):
            row = {
                key: (None if isinstance(value, float) and not np.isfinite(value) else value)
                for key, value in record.items()
                if key in _PERSIST_COLUMNS
            }
            row["index_code"] = BENCHMARK_INDEX
            row["data_quality"] = _quality(record)
            row["src"] = "engine"
            if isinstance(row.get("trade_date"), pd.Timestamp):
                row["trade_date"] = row["trade_date"].date()
            rows.append(row)

        if not rows:
            return 0
        stmt = pg_insert(FactMarketDaily).values(rows)
        update_columns = {
            key: value for key, value in rows[0].items() if key != "trade_date"
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[FactMarketDaily.trade_date],
            set_={key: getattr(stmt.excluded, key) for key in update_columns},
        )
        await session.execute(stmt)
        logger.info("市场因子 %s ~ %s：写入 %d 行", start, end, len(rows))
        return len(rows)


async def compute_and_save_market_factors(
    trade_date: date, db: AsyncSession | None = None
) -> dict:
    rows = await compute_market_factors_range(trade_date, trade_date, db=db)
    return {"rows": rows, "trade_date": trade_date.isoformat()}


_PERSIST_COLUMNS = {
    "trade_date", "close", "high", "low", "ret_1d", "ret_20", "ema20", "ema60", "ema120",
    "ema_slope60", "ma_alignment", "adx14", "di_plus", "di_minus",
    "efficiency_ratio", "choppiness", "macd_hist", "rsi14", "price_vs_ma120",
    "amount", "amount_ma20", "amount_vs_ma20", "market_turnover", "turnover_ma5",
    "volume_price_match", "industry_heat_concentration", "capital_efficiency",
    "up_count", "down_count", "flat_count", "total_count",
    "limit_up_cnt", "limit_down_cnt", "adv_dec_ratio", "above_ma30_ratio",
    "new_high_60_ratio", "broken_net_ratio", "dividend_yield", "earnings_yield",
    "cn_1y", "cn_10y", "erp", "bond_equity_gap", "term_spread",
    "defensive_rel_momentum", "margin_buy_amount", "margin_buy_ratio",
    "rv20", "rv60", "natr14", "boll_bandwidth", "max_drawdown_3m",
}


__all__ = [
    "compute_market_factors_range",
    "compute_and_save_market_factors",
    "adx",
    "atr",
    "annualized_vol",
    "boll_bandwidth",
    "choppiness",
    "efficiency_ratio",
    "ema",
    "macd_hist",
    "max_drawdown",
    "rsi",
    "true_range",
]
