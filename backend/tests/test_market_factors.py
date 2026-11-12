"""市场原始因子纯函数测试（V7 T1）"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from app.engine.market_factors import (  # noqa: E402
    _daily_cross_section,
    _derived,
    _index_features,
    _quality,
    _stock_rolling,
    annualized_vol,
    adx,
    atr,
    boll_bandwidth,
    choppiness,
    efficiency_ratio,
    ema,
    macd_hist,
    max_drawdown,
    rsi,
)


def _prices(n: int = 120, start: float = 100.0, drift: float = 0.3) -> pd.Series:
    return pd.Series([start + i * drift + (i % 5) * 0.2 for i in range(n)])


def test_ema_and_rsi_bounds() -> None:
    close = _prices()
    assert len(ema(close, 20)) == len(close)
    values = rsi(close, 14).dropna()
    assert not values.empty
    assert values.between(0, 100).all()


def test_adx_atr_choppiness_and_efficiency() -> None:
    close = _prices()
    high = close + 0.5
    low = close - 0.5
    adx_value, plus_di, minus_di = adx(high, low, close)
    assert adx_value.dropna().between(0, 100).all()
    assert (plus_di.dropna() >= 0).all() and (minus_di.dropna() >= 0).all()
    assert (atr(high, low, close).dropna() >= 0).all()
    assert choppiness(high, low, close).dropna().between(-100, 200).all()
    er = efficiency_ratio(close, 20).dropna()
    assert er.between(0, 1.01).all()


def test_macd_mdd_boll_and_vol() -> None:
    close = _prices()
    assert len(macd_hist(close)) == len(close)
    mdd = max_drawdown(close, 60).dropna()
    assert (mdd >= 0).all()
    bw = boll_bandwidth(close).dropna()
    assert (bw > 0).all()
    vol = annualized_vol(close.pct_change(), 20).dropna()
    assert (vol >= 0).all()


def _stock_frame() -> pd.DataFrame:
    rows = []
    start = date(2026, 1, 1)
    for code, industry, base, pct in [
        ("000001", "银行", 10.0, 1.0),
        ("000002", "半导体", 20.0, -1.0),
        ("000003", "电力", 30.0, 0.0),
        ("000004", "软件", 40.0, 2.0),
        ("000005", "煤炭", 50.0, -2.0),
        ("000006", "白酒", 60.0, 0.5),
    ]:
        for i in range(70):
            rows.append({
                "code": code,
                "trade_date": start + timedelta(days=i),
                "close": base + i * 0.1,
                "high": base + i * 0.1 + 0.2,
                "amount": 1e8,
                "float_cap": 1e10,
                "pe_ttm": 20.0,
                "pb": 0.8,
                "dv_ttm": 2.0,
                "pct_chg": pct,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_suspended": False,
            })
    return pd.DataFrame(rows)


def test_stock_rolling_new_high_uses_prior_60_days() -> None:
    frame = _stock_rolling(_stock_frame())
    last = frame.sort_values("trade_date").groupby("code").tail(1)
    # 每日 +0.1 的单调上涨在最后一天应创 60 日新高
    assert last["high60"].notna().all()
    assert (last["close"] >= last["high60"]).all()


def test_daily_cross_section_counts_and_industry_metrics() -> None:
    frame = _stock_frame()
    industry = pd.DataFrame(
        [(code, ind) for code, ind in [
            ("000001", "银行"), ("000002", "半导体"), ("000003", "电力"),
            ("000004", "软件"), ("000005", "煤炭"), ("000006", "白酒"),
        ]],
        columns=["code", "industry_l1"],
    )
    daily = _daily_cross_section(frame, industry)
    last = daily.iloc[-1]
    assert last["total_count"] == 6
    assert last["up_count"] == 3
    assert last["down_count"] == 2
    assert last["adv_dec_ratio"] == pytest.approx(3 / 5)
    assert last["above_ma30_ratio"] == pytest.approx(1.0)
    assert last["new_high_60_ratio"] == pytest.approx(1.0)
    assert last["broken_net_ratio"] == pytest.approx(1.0)
    assert last["defensive_return"] == pytest.approx((1.0 + 0.0 + (-2.0) + 0.5) / 4)
    assert last["industry_heat_concentration"] is None or 0 <= last["industry_heat_concentration"] <= 1


def test_derived_amount_ratio_and_quality() -> None:
    daily = pd.DataFrame({
        "trade_date": [date(2026, 1, 1) + timedelta(days=i) for i in range(25)],
        "amount": [1e11] * 25,
        "market_turnover": [1.5] * 25,
        "defensive_return": [0.6] * 25,
        "market_return": [0.4] * 25,
    })
    index = pd.DataFrame({
        "trade_date": daily["trade_date"],
        "close": [100 + i for i in range(25)],
        "high": [101 + i for i in range(25)],
        "low": [99 + i for i in range(25)],
        "open": [100 + i for i in range(25)],
        "amount": [1e11] * 25,
    })
    features = _index_features(index)
    derived = _derived(daily, features)
    assert derived.iloc[-1]["amount_vs_ma20"] == pytest.approx(1.0)
    assert derived.iloc[-1]["turnover_ma5"] == pytest.approx(1.5)
    # 成交额恒定 → 上涨日没有放量，量价配合为 0
    assert derived.iloc[-1]["volume_price_match"] == pytest.approx(0.0)
    # 防御篮子每日 +0.6%、全 A +0.4%，20 日累计超额约 +4.4pct
    assert 4.0 < derived.iloc[-1]["defensive_rel_momentum"] < 5.0

    rising_amount = daily.copy()
    rising_amount["amount"] = [1e11 + i * 1e9 for i in range(25)]
    derived_rising = _derived(rising_amount, features)
    assert derived_rising.iloc[-1]["volume_price_match"] == pytest.approx(1.0)

    assert _quality({"trade_date": date(2026, 1, 1), "close": 100, "total_count": 4000,
                     "amount": 1e11, "cn_10y": 1.8}) == "full"
    assert _quality({"trade_date": date(2026, 1, 1), "close": 100, "total_count": 100,
                     "amount": 1e11, "cn_10y": 1.8}) == "partial"
    assert _quality({"trade_date": date(2026, 1, 1), "close": None}) == "partial"
