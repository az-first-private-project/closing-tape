"""市场温度计测试（V7 T1 新口径）"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from app.engine import report_input  # noqa: E402
from app.engine.breadth import load_breadth  # noqa: E402
from app.engine.market_temperature import (  # noqa: E402
    combine_components,
    compute_temperature_frame,
    temperature_band,
    weekly_summary,
)


# ============================================================
# 档位与合成
# ============================================================

@pytest.mark.parametrize("score,label,rank", [
    (0, "冰点", 1), (19, "冰点", 1),
    (20, "清冷", 2), (39, "清冷", 2),
    (40, "平稳", 3), (59, "平稳", 3),
    (60, "温热", 4), (79, "温热", 4),
    (80, "沸腾", 5), (100, "沸腾", 5),
])
def test_temperature_band(score, label, rank) -> None:
    assert temperature_band(score) == (label, rank)


def test_temperature_band_missing() -> None:
    assert temperature_band(None) is None
    assert temperature_band(float("nan")) is None


ALL_ITEMS = [
    {"key": key, "score": 60.0, "weight": weight, "available": True}
    for key, weight in (
        ("turnover", 0.20), ("amount", 0.20), ("limit_up", 0.15),
        ("margin_buy", 0.15), ("new_high_60", 0.10),
        ("rsi14_norm", 0.10), ("rv20_inv", 0.10),
    )
]


def test_combine_all_available() -> None:
    score, confidence, available = combine_components(ALL_ITEMS)
    assert score == 60
    assert confidence == "ok"
    assert available == 7


def test_combine_missing_noncritical_renormalizes() -> None:
    items = [dict(item) for item in ALL_ITEMS]
    items[3] = {**items[3], "score": None, "available": False}  # margin_buy
    items[0] = {**items[0], "score": 100.0}
    items[1] = {**items[1], "score": 0.0}
    score, confidence, available = combine_components(items)
    assert confidence == "partial"
    assert available == 6
    assert score is not None and 0 <= score <= 100


def test_combine_missing_critical_is_insufficient() -> None:
    for key in ("turnover", "amount"):
        items = [dict(item) for item in ALL_ITEMS]
        index = [item["key"] for item in items].index(key)
        items[index] = {**items[index], "score": None, "available": False}
        score, confidence, _ = combine_components(items)
        assert score is None
        assert confidence == "insufficient"


def test_combine_too_few_is_insufficient() -> None:
    items = [dict(item) for item in ALL_ITEMS]
    for index in (2, 3, 4):
        items[index] = {**items[index], "score": None, "available": False}
    score, confidence, available = combine_components(items)
    assert score is None
    assert confidence == "insufficient"
    assert available == 4


# ============================================================
# 全历史计算
# ============================================================

def _raw_frame(n: int = 320) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [date(2023, 1, 1) + timedelta(days=i) for i in range(n)],
        "market_turnover": np.linspace(1.0, 2.0, n),
        "amount": np.linspace(8e11, 1.6e12, n),
        "limit_up_cnt": np.linspace(20, 80, n),
        "margin_buy_ratio": np.linspace(0.05, 0.12, n),
        "new_high_60_ratio": np.linspace(0.01, 0.1, n),
        "rsi14": np.linspace(30, 70, n),
        "rv20": np.linspace(30, 15, n),
        "up_count": [2500] * n,
        "down_count": [2500] * n,
        "flat_count": [100] * n,
        "total_count": [5100] * n,
        "limit_down_cnt": [10] * n,
        "amount_vs_ma20": [1.0] * n,
    })


def test_compute_temperature_frame_outputs_score() -> None:
    frame = compute_temperature_frame(_raw_frame())
    assert len(frame) == 320
    assert frame.iloc[:249]["temp_score"].isna().all()
    last = frame.iloc[-1]
    assert last["temp_score"] is not None
    assert 0 <= last["temp_score"] <= 100
    assert last["confidence"] == "ok"
    assert len(last["components"]["items"]) == 7


def test_compute_temperature_frame_partial_and_insufficient() -> None:
    raw = _raw_frame()
    raw["margin_buy_ratio"] = np.nan
    frame = compute_temperature_frame(raw)
    assert frame.iloc[-1]["confidence"] == "partial"
    assert frame.iloc[-1]["temp_score"] is not None

    raw["amount"] = np.nan
    frame = compute_temperature_frame(raw)
    assert frame.iloc[-1]["confidence"] == "insufficient"
    assert frame.iloc[-1]["temp_score"] is None


# ============================================================
# 一周统计
# ============================================================

def test_weekly_summary_rising() -> None:
    series = [
        {"trade_date": f"2026-11-0{i + 1}", "temp_score": temp}
        for i, temp in enumerate([30, 35, 45, 55, 65])
    ]
    summary = weekly_summary(series)
    assert summary["current_temp"] == 65
    assert summary["current_label"] == "温热"
    assert summary["week_avg"] == 46
    assert summary["week_slope"] > 1.5
    assert summary["week_trend"] == "升温"
    assert "转暖" in summary["emotion_conclusion"] or "回暖" in summary["emotion_conclusion"]
    assert len(summary["series"]) == 5


def test_weekly_summary_cooling_and_extreme() -> None:
    series = [
        {"trade_date": f"2026-11-0{i + 1}", "temp_score": temp}
        for i, temp in enumerate([85, 82, 80, 78, 76])
    ]
    summary = weekly_summary(series)
    assert summary["week_slope"] < -1.5
    assert summary["week_trend"] == "降温"
    assert summary["current_label"] == "温热"
    assert summary["extreme_note"] == ""
    assert summary["series"][0]["label"] == "沸腾"

    summary = weekly_summary([{"trade_date": "2026-11-06", "temp_score": 88}])
    assert summary["current_label"] == "沸腾"
    assert "追高" in summary["extreme_note"]


def test_weekly_summary_empty() -> None:
    summary = weekly_summary([])
    assert summary["current_temp"] is None
    assert summary["week_trend"] == "数据不足"
    assert "数据积累" in summary["emotion_conclusion"]


# ============================================================
# 市场宽度（保留原测试，公共模块仍有消费者）
# ============================================================

class _FakeResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def all(self) -> list[tuple]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[tuple]):
        self.rows = rows
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.rows)


def test_load_breadth_keeps_output_shape() -> None:
    session = _FakeSession([
        (2.0, True, False),
        (-1.0, False, False),
        (0.0, False, True),
        (3.0, False, False),
        (None, False, False),
    ])
    result = asyncio.run(load_breadth(session, date(2026, 1, 5)))
    assert result == {
        "up": 2,
        "down": 1,
        "flat": 1,
        "limit_up": 1,
        "limit_down": 1,
        "median_pct": 1.0,
        "total": 4,
    }


def test_load_breadth_excludes_suspended_only_when_asked() -> None:
    session_default = _FakeSession([])
    asyncio.run(load_breadth(session_default, date(2026, 1, 5)))
    assert "is_suspended" not in str(session_default.statements[0])

    session_temperature = _FakeSession([])
    asyncio.run(load_breadth(session_temperature, date(2026, 1, 5), exclude_suspended=True))
    assert "is_suspended" in str(session_temperature.statements[0])


def test_report_input_uses_shared_breadth_module() -> None:
    assert not hasattr(report_input, "_load_breadth")
    assert report_input.load_breadth is load_breadth
