"""
指标库单元测试（不依赖数据库 / 不联网）
======================================
断言用**闭式解**或可手算的序列，而不是"跑一遍不报错"。

重点防回归：
  - RSI 必须是 Wilder 平滑（不是 rolling.mean 的简化版）
  - MACD 金叉必须是"当日上穿"，且不能用未来数据
  - ATR/BOLL 的窗口与 ddof 口径
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pandas")

from app.engine.indicators import (  # noqa: E402
    atr, boll, boll_bandwidth, compute_features, ema, golden_cross, kdj, ma,
    ma_bullish, macd, momentum, previous_high, previous_range_pct, rsi,
    true_range, volume_ratio,
)


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


# ============================================================
# 基础指标
# ============================================================

def test_ma_is_mean_of_window():
    series = _series([1, 2, 3, 4, 5])
    result = ma(series, 3)
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_ema_matches_manual_recursion():
    series = _series([1.0, 2.0, 3.0])
    span = 2
    alpha = 2 / (span + 1)
    expected = [1.0]
    for value in series.iloc[1:]:
        expected.append(alpha * value + (1 - alpha) * expected[-1])
    result = ema(series, span)
    assert list(result) == pytest.approx(expected, rel=1e-9)


def test_rsi_all_gains_is_100():
    series = _series(list(range(1, 40)))
    result = rsi(series, 14)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    series = _series(list(range(40, 1, -1)))
    result = rsi(series, 14)
    assert result.iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_matches_wilder_reference():
    """用经典 Wilder 数据（前 15 个样本）核对首值。"""
    closes = _series([
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
    ])
    result = rsi(closes, 14)
    # Wilder 原例：第 15 个交易日的 RSI ≈ 70.53
    assert result.iloc[14] == pytest.approx(70.53, abs=0.5)


def test_macd_relationship():
    series = _series(np.linspace(10, 30, 120).tolist())
    dif, dea, hist = macd(series)
    assert (hist - (dif - dea)).abs().max() < 1e-9
    # 单调上涨时 DIF > 0 且最终高于 DEA
    assert dif.iloc[-1] > 0
    assert dif.iloc[-1] > dea.iloc[-1]


def test_boll_uses_population_std():
    series = _series([10.0] * 19 + [12.0])
    mid, upper, lower = boll(series, 20, 2.0)
    std = series.std(ddof=0)
    assert mid.iloc[-1] == pytest.approx(series.mean())
    assert upper.iloc[-1] == pytest.approx(series.mean() + 2 * std)
    assert lower.iloc[-1] == pytest.approx(series.mean() - 2 * std)


def test_true_range_uses_previous_close():
    high = _series([10.0, 12.0])
    low = _series([9.0, 11.0])
    close = _series([9.5, 11.5])
    tr = true_range(high, low, close)
    # 第二天: max(12-11, |12-9.5|, |11-9.5|) = 2.5
    assert tr.iloc[1] == pytest.approx(2.5)


def test_atr_positive_and_smoothed():
    rng = np.random.default_rng(7)
    close = _series(np.cumsum(rng.normal(0, 1, 60)) + 50)
    high = close + 1
    low = close - 1
    result = atr(high, low, close, 14)
    assert result.iloc[-1] > 0
    assert result.iloc[:13].isna().all()


def test_momentum_is_percent_change():
    series = _series([100.0, 110.0])
    result = momentum(series, 1)
    assert result.iloc[1] == pytest.approx(10.0)


def test_volume_ratio_equals_five_over_twenty():
    volume = _series([100.0] * 19 + [200.0])
    result = volume_ratio(volume, 5, 20)
    expected = volume.iloc[-5:].mean() / volume.iloc[-20:].mean()
    assert result.iloc[-1] == pytest.approx(expected)


# ============================================================
# 形态判定
# ============================================================

def test_golden_cross_is_same_day_only():
    fast = _series([1.0, 1.0, 2.0, 3.0])
    slow = _series([1.5, 1.5, 1.5, 1.5])
    result = golden_cross(fast, slow)
    # 只有第 3 天（index=2）是"当日上穿"
    assert list(result) == [False, False, True, False]


def test_golden_cross_no_lookahead():
    """用未来值构造的"假金叉"不应该被提前识别。"""
    fast = _series([1.0, 1.0, 1.0, 5.0])
    slow = _series([1.5, 1.5, 1.5, 1.5])
    result = golden_cross(fast, slow)
    assert not result.iloc[2]          # 第 3 天还不知道第 4 天会突破


def test_ma_bullish():
    ma5 = _series([10.0, 9.0, 11.0])
    ma10 = _series([9.0, 9.5, 10.0])
    ma20 = _series([8.0, 9.0, 9.0])
    assert list(ma_bullish(ma5, ma10, ma20)) == [True, False, True]


def test_kdj_seed_identity_and_nan_profile():
    """KDJ：RSV 需 9 日窗口；K/D 以 50 为种子；J = 3K − 2D。"""
    n = 30
    high = _series([10.0 + index * 0.2 for index in range(n)])
    low = high - 1.0
    close = high - 0.5
    k, d, j = kdj(high, low, close)

    assert k.iloc[:8].isna().all() and d.iloc[:8].isna().all()
    assert (j - (3.0 * k - 2.0 * d)).abs().max() < 1e-9

    lowest = low.iloc[:9].min()
    highest = high.iloc[:9].max()
    rsv = (close.iloc[8] - lowest) / (highest - lowest) * 100.0
    assert k.iloc[8] == pytest.approx((2.0 / 3.0) * 50.0 + (1.0 / 3.0) * rsv)
    assert d.iloc[8] == pytest.approx((2.0 / 3.0) * 50.0 + (1.0 / 3.0) * k.iloc[8])


def test_boll_bandwidth_percent():
    mid = _series([10.0] * 5)
    upper = mid + 2.0
    lower = mid - 2.0
    width = boll_bandwidth(mid, upper, lower)
    assert width.iloc[-1] == pytest.approx(40.0)


def test_previous_high_and_range_exclude_today():
    """突破条件必须只看前 N 日，不能把当日高点算进「前高」。"""
    close = _series([10.0, 11.0, 12.0, 13.0, 20.0])
    assert previous_high(close, 3).iloc[3] == pytest.approx(12.0)
    # 前 3 日（10,11,12）区间宽度 = (12-10)/12
    assert previous_range_pct(close, 3).iloc[3] == pytest.approx(2.0 / 12.0 * 100.0)
    # 当日 20 不参与前高计算
    assert previous_high(close, 3).iloc[4] == pytest.approx(13.0)


# ============================================================
# 全量特征
# ============================================================

def test_compute_features_requires_columns():
    with pytest.raises(ValueError):
        compute_features(pd.DataFrame({"close": [1, 2]}))


def test_compute_features_shape_and_no_future_fill():
    rng = np.random.default_rng(3)
    n = 100
    close = np.cumsum(rng.normal(0, 1, n)) + 50
    df = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "open": close, "high": close + 1, "low": close - 1, "close": close,
        "volume": rng.integers(1e6, 5e6, n), "amount": rng.uniform(1e8, 5e8, n),
    })
    out = compute_features(df)

    for col in ("ma5", "ma20", "ma60", "ma120", "rsi14", "rsi6", "roc10",
                "macd_dif", "boll_upper", "boll_width", "boll_width_pct60",
                "atr14", "momentum_20", "volatility_20", "volume_ratio", "ma_bullish",
                "ema5", "ema20", "ema60", "ema20_up", "ema_golden_cross", "ema_cross_3d",
                "kdj_k", "kdj_d", "kdj_j", "kdj_j_min2", "kdj_golden_cross",
                "high20_close", "range20_pct", "amp60", "vol_ma5"):
        assert col in out.columns

    assert len(out) == n
    # 前 59 行没有 MA60（不做前向填充，避免未来函数）
    assert out["ma60"].iloc[:59].isna().all()
    assert not np.isnan(out["ma60"].iloc[59])
    # 带宽分位需要 60 日历史（带宽自身 20 日 + 前移 1 日）
    assert out["boll_width_pct60"].iloc[:79].isna().all()
    assert out["boll_width_pct60"].notna().iloc[-1]
    assert not np.isnan(out["kdj_j"].iloc[8])
    # 布尔列不能有 NaN
    assert out["ma_bullish"].dtype == bool
    assert out["macd_golden_cross"].dtype == bool
    assert out["ema_cross_3d"].dtype == bool
    assert out["kdj_golden_cross"].dtype == bool


def test_feature_upsert_chunk_respects_asyncpg_param_limit():
    """单条 INSERT 的绑定参数不得超过 asyncpg 32767 上限（特征列增加时自动缩块）。"""
    from app.engine.features import WRITE_CHUNK_ROWS, _chunk_rows
    from app.engine.indicators import FEATURE_COLUMNS

    rows = _chunk_rows()
    assert 0 < rows <= WRITE_CHUNK_ROWS
    assert rows * (len(FEATURE_COLUMNS) + 2) <= 32_000
