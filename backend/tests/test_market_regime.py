"""市场状态机与配置建议测试（V7 T1）"""
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

from app.engine import market_regime as regime  # noqa: E402
from app.engine.market_regime import (  # noqa: E402
    build_allocation,
    build_evidence,
    classify_state,
    group_scores,
    maintains_state,
    pick_sectors,
    rolling_percentile,
    simulate_history,
    standardize,
)


def _row(**overrides) -> pd.Series:
    base = {
        "group_t": 0.0, "group_m": 0.0, "group_b": 0.0,
        "group_q": 0.0, "group_r": 0.0, "v_q": 0.5,
        "pct_Q1": 0.5, "x_Q3": 0.1, "pct_Q4": 0.5, "pct_Q5": 0.5,
        "pct_V5": 0.5, "pct_R6": 0.5, "x_R7": 0.0,
        "sentiment_lag5": 0.0, "q4_lag5": 0.5,
        "completeness": 0.9, "valid": True,
    }
    base.update(overrides)
    return pd.Series(base)


def _raw_frame(n: int = 12) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [date(2026, 1, 1) + timedelta(days=i) for i in range(n)],
        "close": [100 + i for i in range(n)],
        "high": [101 + i for i in range(n)],
        "low": [99 + i for i in range(n)],
        "ema20": [100 + i for i in range(n)],
        "ema60": [100 + i for i in range(n)],
        "macd_hist": [0.5] * n,
        "ma_alignment": [1] * n,
    })


# ============================================================
# 标准化
# ============================================================

def test_rolling_percentile_monotonic_and_missing() -> None:
    series = pd.Series(range(300), dtype=float)
    pct = rolling_percentile(series, window=750, min_periods=250)
    assert pct.iloc[:249].isna().all()
    assert pct.iloc[-1] > 0.95
    assert pct.dropna().between(0, 1).all()

    with_nan = series.copy()
    with_nan.iloc[-1] = np.nan
    assert np.isnan(rolling_percentile(with_nan).iloc[-1])

    constant = pd.Series([1.0] * 300)
    assert rolling_percentile(constant).iloc[-1] == pytest.approx(0.5)


def _standard_input(n: int = 300) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "ema_slope60": np.linspace(0.001, 0.01, n),
        "ma_alignment": [1] * n,
        "adx14": np.linspace(10, 40, n),
        "di_plus": [30] * n,
        "di_minus": [10] * n,
        "efficiency_ratio": np.linspace(0.1, 0.8, n),
        "choppiness": np.linspace(80, 40, n),
        "price_vs_ma120": np.linspace(-0.2, 0.2, n),
        "macd_hist": np.linspace(-1, 1, n),
        "rsi14": np.linspace(20, 80, n),
        "ret_20": np.linspace(-10, 10, n),
        "above_ma30_ratio": np.linspace(0.1, 0.8, n),
        "new_high_60_ratio": np.linspace(0.01, 0.2, n),
        "adv_dec_ratio": np.linspace(0.2, 0.8, n),
        "limit_up_cnt": [20] * n,
        "limit_down_cnt": [5] * n,
        "rv20": np.linspace(30, 10, n),
        "rv60": np.linspace(28, 12, n),
        "natr14": np.linspace(3, 1, n),
        "boll_bandwidth": np.linspace(20, 8, n),
        "max_drawdown_3m": np.linspace(20, 5, n),
        "amount_vs_ma20": np.linspace(0.7, 1.4, n),
        "turnover_ma5": np.linspace(1.0, 2.0, n),
        "volume_price_match": np.linspace(0.2, 0.9, n),
        "industry_heat_concentration": np.linspace(0.5, 0.2, n),
        "capital_efficiency": np.linspace(-1, 1, n),
        "margin_buy_ratio": np.linspace(0.05, 0.12, n),
        "erp": np.linspace(3, 6, n),
        "bond_equity_gap": np.linspace(0.5, 2.0, n),
        "term_spread": np.linspace(0.2, 0.8, n),
        "defensive_rel_momentum": np.linspace(8, -8, n),
        "broken_net_ratio": np.linspace(0.2, 0.1, n),
    })


def test_standardize_directions_and_discrete_values() -> None:
    pct, x = standardize(_standard_input())
    assert pct["T1"].dropna().between(0, 1).all()
    assert x["T1"].iloc[-1] > 0.9          # 单调上升 → 高分位
    assert x["T5"].iloc[-1] > 0.9          # 震荡度下降（逆向）→ 正向
    assert x["T2"].iloc[-1] == 1           # discrete 原样
    assert -1 <= x["Q3"].iloc[-1] <= 1     # bounded
    assert x["T3"].iloc[-1] > 0            # ADX 乘 +DI 方向


def test_group_scores_renormalize_missing() -> None:
    index = pd.RangeIndex(2)
    pct = pd.DataFrame({"V1": [0.5, 0.5], "V2": [0.5, 0.5]}, index=index)
    x = pd.DataFrame({
        "T1": [1.0, 1.0], "T2": [1.0, 1.0], "T3": [1.0, 1.0],
        "T4": [1.0, 1.0], "T5": [1.0, 1.0], "T6": [1.0, 1.0],
        "M1": [1.0, 1.0], "M2": [1.0, 1.0], "M3": [1.0, 1.0], "M4": [1.0, 1.0],
        "B1": [1.0, 1.0], "B2": [1.0, 1.0], "B3": [1.0, 1.0], "B4": [1.0, 1.0],
        "Q1": [1.0, 1.0], "Q2": [1.0, 1.0], "Q3": [1.0, 1.0],
        "Q4": [1.0, 1.0], "Q5": [1.0, 1.0], "Q6": [1.0, 1.0],
        "R1": [1.0, 1.0], "R2": [1.0, 1.0], "R3": [1.0, 1.0],
        "R4": [1.0, 1.0], "R6": [1.0, 1.0], "R7": [1.0, 1.0],
    }, index=index)
    result = group_scores(pct, x)
    assert result["group_t"].iloc[0] == pytest.approx(1.0)
    assert result["group_q"].iloc[0] == pytest.approx(1.0)
    assert result["v_q"].iloc[0] == pytest.approx(0.5)
    assert result["valid"].all()


# ============================================================
# 五态
# ============================================================

def _classify(row: pd.Series, index: int = 0, frame: pd.DataFrame | None = None) -> str:
    return classify_state(row, index, frame if frame is not None else _raw_frame())


def test_classify_risk_off_priority() -> None:
    row = _row(group_r=-0.6, v_q=0.8, group_m=-0.3, group_t=-0.6)
    assert _classify(row) == "risk_off"


def test_classify_trend_up() -> None:
    row = _row(group_t=0.5, group_m=0.3, group_b=0.6, group_q=0.4, x_Q3=0.2)
    assert _classify(row) == "trend_up"


def test_classify_repair_and_pull() -> None:
    assert _classify(_row(group_t=-0.1, group_m=0.2)) == "repair_up"
    assert _classify(_row(group_t=0.1, group_m=-0.2)) == "pull_down"


def test_classify_bottoming(monkeypatch) -> None:
    monkeypatch.setattr(regime, "_confirmations", lambda *args, **kwargs: 2)
    row = _row(group_t=0.1, v_q=0.2, pct_Q1=0.1, group_m=0.0)
    assert _classify(row) == "bottoming"

    monkeypatch.setattr(regime, "_confirmations", lambda *args, **kwargs: 2)
    row = _row(group_t=0.1, v_q=0.2, pct_Q1=0.1, group_m=-0.4)
    assert _classify(row) == "pull_down"


def test_maintains_state_uses_relaxed_thresholds() -> None:
    assert maintains_state("trend_up", _row(
        group_t=0.28, group_m=0.17, group_b=0.42, group_q=0.25, x_Q3=0.01
    ))
    assert not maintains_state("trend_up", _row(
        group_t=0.20, group_m=0.17, group_b=0.42, group_q=0.25, x_Q3=0.01
    ))


# ============================================================
# 配置
# ============================================================

def test_allocation_sums_to_100_and_multiple_of_5() -> None:
    for state in ("trend_up", "repair_up", "bottoming", "pull_down", "risk_off"):
        allocation = build_allocation(state, optimism=60, confidence=70, previous=None, sector_picks={})
        pcts = [bucket["pct"] for bucket in allocation["buckets"]]
        assert sum(pcts) == 100, state
        assert all(pct % 5 == 0 for pct in pcts), state
        assert all(pct >= 0 for pct in pcts), state


def test_allocation_low_confidence_tilts_to_balanced() -> None:
    normal = build_allocation("trend_up", 70, 80, None, {})
    cautious = build_allocation("trend_up", 70, 30, None, {})
    normal_map = {bucket["key"]: bucket["pct"] for bucket in normal["buckets"]}
    cautious_map = {bucket["key"]: bucket["pct"] for bucket in cautious["buckets"]}
    assert cautious_map["balanced"] > normal_map["balanced"]


def test_allocation_daily_change_capped() -> None:
    previous = build_allocation("risk_off", 20, 80, None, {})
    previous["buckets"] = [
        {"key": "defensive", "pct": 5},
        {"key": "offensive", "pct": 80},
        {"key": "balanced", "pct": 15},
    ]
    allocation = build_allocation("trend_up", 90, 80, previous, {})
    pcts = {bucket["key"]: bucket["pct"] for bucket in allocation["buckets"]}
    assert sum(pcts.values()) == 100
    assert abs(pcts["defensive"] - 5) <= 15
    assert abs(pcts["offensive"] - 80) <= 15
    assert abs(pcts["balanced"] - 15) <= 15


def test_allocation_vs_yesterday() -> None:
    previous = build_allocation("bottoming", 50, 60, None, {})
    allocation = build_allocation("bottoming", 50, 60, previous, {})
    assert all(value == 0 for value in allocation["vs_yesterday"].values())


# ============================================================
# 证据与板块
# ============================================================

def test_build_evidence_returns_five_items() -> None:
    frame = _raw_frame()
    frame.loc[0, "ema_slope60"] = 0.01
    row = _row(group_t=0.5, group_m=0.3, group_b=0.6, group_q=0.4, x_Q3=0.2)
    row["x_T1"] = 0.9
    row["pct_T1"] = 0.95
    row["x_M3"] = 0.7
    row["pct_M3"] = 0.85
    row["x_Q1"] = -0.8
    row["pct_Q1"] = 0.1
    row["x_V1"] = 0.6
    row["pct_V1"] = 0.8
    row["x_R1"] = 0.5
    row["pct_R1"] = 0.75
    evidence = build_evidence(row, "trend_up")
    assert len(evidence) == 5
    assert all(item["plain"] for item in evidence)
    assert all("『" in item["direction"] for item in evidence)


def test_pick_sectors_keeps_template_when_no_strength() -> None:
    picks = pick_sectors(pd.DataFrame(), date(2026, 1, 1), "defensive")
    assert picks == ["红利低波", "银行", "公用事业"]


def test_pick_sectors_adds_dynamic_names() -> None:
    strength = pd.DataFrame(
        [{"半导体": 12.0, "软件开发": 8.0, "白酒": -2.0}],
        index=[date(2026, 1, 1)],
    )
    picks = pick_sectors(strength, date(2026, 1, 1), "offensive")
    assert picks[:3] == ["科技成长", "半导体", "券商"]
    assert "软件开发" in picks


# ============================================================
# 状态机（迟滞）
# ============================================================

def test_simulate_history_confirms_three_days_and_min_residence() -> None:
    frame = _raw_frame(12)
    for column in (
        "ema_slope60", "adx14", "di_plus", "di_minus", "efficiency_ratio",
        "choppiness", "price_vs_ma120", "macd_hist", "rsi14", "ret_20",
        "above_ma30_ratio", "new_high_60_ratio", "adv_dec_ratio",
        "rv20", "rv60", "natr14", "boll_bandwidth", "max_drawdown_3m",
        "amount_vs_ma20", "turnover_ma5", "volume_price_match",
        "industry_heat_concentration", "capital_efficiency", "margin_buy_ratio",
        "erp", "bond_equity_gap", "term_spread", "defensive_rel_momentum",
        "broken_net_ratio",
    ):
        frame[column] = 0.5
    for key in ("group_t", "group_m", "group_b", "group_q", "group_r"):
        frame[key] = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    frame["group_b"] = [0.6] * 12
    frame["group_m"] = [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, -0.3, -0.3, -0.3, -0.3, -0.3, -0.3]
    frame["v_q"] = 0.5
    frame["x_Q3"] = 0.2
    frame["pct_Q1"] = 0.5
    frame["pct_Q4"] = 0.5
    frame["pct_Q5"] = 0.5
    frame["pct_V5"] = 0.5
    frame["valid"] = True
    frame["completeness"] = 0.9
    result = simulate_history(frame)
    codes = result["regime_code"].tolist()
    assert codes[0] == "trend_up"
    # 连续 3 个交易日满足才切换：第 3 个 pull_down 日（索引 8）才生效
    assert codes[7] == "trend_up"
    assert codes[8] == "pull_down"
