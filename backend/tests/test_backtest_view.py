"""V6 T9 回测派生视图单元测试（纯函数，不依赖数据库）。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.engine.backtest_view import (
    CASH_LABEL,
    OTHER_INDUSTRY,
    ROLLING_WINDOW,
    build_backtest_extras,
)


def _curve(rows: list[tuple[str, float, float | None]]) -> list[dict]:
    return [{"date": day, "nav": nav, "benchmark": bench} for day, nav, bench in rows]


def _trade(
    day: str,
    code: str,
    side: str,
    shares: int,
    price: float | None,
    *,
    fee: float = 0.0,
    amount: float | None = None,
) -> dict:
    return {
        "date": day,
        "code": code,
        "name": code,
        "side": side,
        "price": price,
        "shares": shares,
        "amount": price * shares if amount is None and price is not None else amount,
        "fee": fee,
        "reason": "rebalance",
    }


CURVE = _curve(
    [
        ("2024-01-02", 1.0, 1.0),
        ("2024-01-31", 1.10, 1.05),
        ("2024-02-01", 1.15, 1.07),
        ("2024-02-29", 1.20, 1.10),
        ("2024-03-01", 1.10, 1.05),
        ("2024-03-28", 1.32, 1.20),
    ]
)
TRADES = [
    _trade("2024-01-02", "600000", "buy", 100, 10.0, fee=5.0),
]


def test_monthly_returns_compound_to_total_return():
    extras = build_backtest_extras(CURVE, TRADES, {})
    monthly = extras["monthly_returns"]
    assert [item["ym"] for item in monthly] == ["2024-01", "2024-02", "2024-03"]

    total = 1.0
    for item in monthly:
        assert item["ret"] is not None
        total *= 1.0 + item["ret"] / 100.0
    # 4 位小数百分数舍入后复利仍应远小于验收阈值 0.1pp
    assert total - 1.0 == pytest.approx(0.32, abs=1e-6)

    assert monthly[0]["ret"] == pytest.approx(10.0)
    assert monthly[1]["ret"] == pytest.approx(9.0909, abs=1e-4)
    assert monthly[2]["ret"] == pytest.approx(10.0)

    assert monthly[0]["benchmark"] == pytest.approx(5.0)
    assert monthly[1]["benchmark"] == pytest.approx(4.7619, abs=1e-4)
    assert monthly[2]["benchmark"] == pytest.approx(9.0909, abs=1e-4)
    assert monthly[2]["excess"] == pytest.approx(0.9091, abs=1e-4)


def test_monthly_returns_benchmark_missing_is_null():
    curve = _curve([("2024-01-02", 1.0, None), ("2024-01-31", 1.1, None)])
    extras = build_backtest_extras(curve, TRADES, {})
    month = extras["monthly_returns"][0]
    assert month["ret"] == pytest.approx(10.0)
    assert month["benchmark"] is None
    assert month["excess"] is None


def test_drawdown_curve_matches_metrics_definition():
    extras = build_backtest_extras(CURVE, TRADES, {})
    curve = extras["drawdown_curve"]
    assert [item["date"] for item in curve] == [item["date"] for item in CURVE]
    assert curve[0]["drawdown"] == 0.0
    assert curve[3]["drawdown"] == 0.0
    assert curve[4]["drawdown"] == pytest.approx(-8.3333, abs=1e-4)
    assert curve[4]["peak"] == pytest.approx(1.2)
    worst = min(item["drawdown"] for item in curve)
    assert worst == pytest.approx(-8.3333, abs=1e-4)


def test_rolling_excess_window_and_nulls():
    start = date(2024, 1, 2)
    days = [(start + timedelta(days=index)).isoformat() for index in range(22)]
    curve = _curve(
        [(day, 1.0 + index * 0.01, 1.0 + index * 0.005) for index, day in enumerate(days)]
    )
    extras = build_backtest_extras(curve, TRADES, {})
    rolling = extras["rolling_excess_20d"]
    assert len(rolling) == len(curve)
    assert all(item["value"] is None for item in rolling[:ROLLING_WINDOW])
    assert rolling[ROLLING_WINDOW]["value"] == pytest.approx(10.0)
    assert rolling[21]["value"] == pytest.approx(9.8517, abs=1e-4)


def test_extras_unavailable_when_payload_empty():
    for curve, trades in (([], TRADES), (CURVE, []), ([], [])):
        extras = build_backtest_extras(curve, trades, {})
        assert extras["extras_available"] is False
        assert extras["monthly_returns"] == []
        assert extras["drawdown_curve"] == []
        assert extras["rolling_excess_20d"] == []
        assert extras["industry_exposure"] == []


INDUSTRY_TRADES = [
    _trade("2024-01-02", "600000", "buy", 1000, 10.0, fee=5.0),
    _trade("2024-01-02", "000001", "buy", 2000, 5.0, fee=5.0),
    # 被拒单（shares=0）不得建仓，也不得进入行业统计
    _trade("2024-01-02", "600002", "buy", 0, 8.0, amount=0.0),
    _trade("2024-02-01", "000001", "sell", 2000, 6.0, fee=12.0),
    _trade("2024-02-01", "600003", "buy", 500, 20.0, fee=5.0),
]
DAILY = {
    "600000": {"2024-01-02": 11.0, "2024-02-01": 12.0},
    "000001": {"2024-01-02": 6.0, "2024-02-01": 7.0},
    "600002": {"2024-01-02": 8.0},
    "600003": {"2024-02-01": {"close": 21.0}},
}
INDUSTRIES = {"600000": "银行", "000001": None, "600003": "电子"}


def test_industry_exposure_reconstructs_holdings_and_cash():
    extras = build_backtest_extras(
        CURVE, INDUSTRY_TRADES, DAILY, industry_by_code=INDUSTRIES, initial_cash=100_000.0
    )
    exposure = extras["industry_exposure"]
    assert [item["date"] for item in exposure] == ["2024-01-02", "2024-02-01"]

    first = exposure[0]
    assert first["cash"] == pytest.approx(79_990.0)
    assert first["total_value"] == pytest.approx(102_990.0)
    assert first["stock_count"] == 2
    assert first["other_count"] == 1
    weights = {item["name"]: item["weight"] for item in first["industries"]}
    assert weights["银行"] == pytest.approx(11_000 / 102_990 * 100, abs=1e-4)
    assert weights[OTHER_INDUSTRY] == pytest.approx(12_000 / 102_990 * 100, abs=1e-4)
    assert weights[CASH_LABEL] == pytest.approx(79_990 / 102_990 * 100, abs=1e-4)
    assert sum(weights.values()) == pytest.approx(100.0, abs=1e-9)

    second = exposure[1]
    weights = {item["name"]: item["weight"] for item in second["industries"]}
    assert second["other_count"] == 0
    assert "银行" in weights and "电子" in weights
    assert weights[CASH_LABEL] == pytest.approx(
        100.0 - weights["银行"] - weights["电子"], abs=1e-9
    )
    assert sum(weights.values()) == pytest.approx(100.0, abs=1e-9)


def test_industry_exposure_fallbacks_to_trade_price_when_close_missing():
    daily = {
        "600000": {"2024-01-02": 11.0},
        "000001": {"2024-01-02": 6.0},
    }
    extras = build_backtest_extras(
        CURVE, INDUSTRY_TRADES, daily, industry_by_code=INDUSTRIES, initial_cash=100_000.0
    )
    second = extras["industry_exposure"][1]
    names = {item["name"]: item for item in second["industries"]}
    # 600003 在调仓日无收盘价 → 回退当日成交价 20；600000 回退最后已知收盘 11
    assert names["电子"]["value"] == pytest.approx(500 * 20.0)
    assert names["银行"]["value"] == pytest.approx(1000 * 11.0)


def test_industry_exposure_empty_when_no_executed_trades():
    rejected = [_trade("2024-01-02", "600000", "buy", 0, 10.0, amount=0.0)]
    extras = build_backtest_extras(CURVE, rejected, DAILY, industry_by_code=INDUSTRIES)
    assert extras["extras_available"] is True
    assert extras["industry_exposure"] == []
