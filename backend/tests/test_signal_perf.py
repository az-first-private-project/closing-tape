"""信号前瞻收益统计单测（V6 T5）。

只测纯函数，不依赖数据库/网络：
  - T+1 入场、复权价、5/10/20 日收益与沪深 300 超额；
  - 停牌/一字涨停/缺 K 线 → `fillable=false`；
  - 交易日历不足与 `data_end` 截断 → 未成熟；
  - 天梯聚合的互斥分桶、胜率、低样本、spark 与排序。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pytest.importorskip("sqlalchemy", reason="sqlalchemy 未安装，跳过信号统计测试")

from app.engine.signal_perf import (  # noqa: E402
    HORIZONS,
    MIN_SAMPLE,
    LadderAlgo,
    PerfRow,
    SignalBar,
    build_ladder,
    compute_forward_result,
    forward_window,
)

CALENDAR = [date(2026, 1, 5) + timedelta(days=index) for index in range(30)]
SIGNAL = date(2026, 1, 4)  # 周日，不在交易日历中


# ============================================================
# 窗口定位
# ============================================================

def test_forward_window_picks_next_open_day():
    entry, exits = forward_window(SIGNAL, CALENDAR)
    assert entry == CALENDAR[0]
    assert exits == {5: CALENDAR[5], 10: CALENDAR[10], 20: CALENDAR[20]}


def test_forward_window_skips_signal_day_itself():
    entry, exits = forward_window(CALENDAR[3], CALENDAR)
    assert entry == CALENDAR[4]
    assert exits[5] == CALENDAR[9]


def test_forward_window_short_calendar_returns_none():
    entry, exits = forward_window(SIGNAL, CALENDAR[:4])
    assert entry == CALENDAR[0]
    assert exits == {5: None, 10: None, 20: None}

    entry, exits = forward_window(SIGNAL, [])
    assert entry is None
    assert exits == {5: None, 10: None, 20: None}


def test_forward_window_custom_horizons():
    entry, exits = forward_window(SIGNAL, CALENDAR, horizons=(1, 2))
    assert entry == CALENDAR[0]
    assert exits == {1: CALENDAR[1], 2: CALENDAR[2]}


# ============================================================
# 单条信号收益
# ============================================================

def _bars(entry: SignalBar | None = None) -> dict[date, SignalBar]:
    return {
        CALENDAR[0]: entry or SignalBar(open=5.0, close=5.2, adj_factor=2.0),
        CALENDAR[5]: SignalBar(open=11.0, close=11.0, adj_factor=1.0),
        CALENDAR[10]: SignalBar(open=12.0, close=12.0, adj_factor=1.0),
        CALENDAR[20]: SignalBar(open=9.0, close=9.0, adj_factor=1.0),
    }


def _bench() -> dict[date, float | None]:
    return {
        CALENDAR[0]: 4000.0,
        CALENDAR[5]: 4200.0,   # +5%
        CALENDAR[10]: 3800.0,  # -5%
        CALENDAR[20]: 4000.0,  # 0%
    }


def test_compute_returns_and_excess_with_adjusted_entry():
    result = compute_forward_result(
        SIGNAL, CALENDAR, _bars(), _bench(), data_end=CALENDAR[29]
    )
    assert result.entry_date == CALENDAR[0]
    assert result.entry_price == pytest.approx(10.0)  # 5.0 × 2.0
    assert result.fillable is True
    assert result.mature == {5: True, 10: True, 20: True}
    assert result.returns[5] == pytest.approx(10.0)
    assert result.returns[10] == pytest.approx(20.0)
    assert result.returns[20] == pytest.approx(-10.0)
    assert result.excess[5] == pytest.approx(5.0)
    assert result.excess[10] == pytest.approx(25.0)
    assert result.excess[20] == pytest.approx(-10.0)


def test_compute_applies_exit_adjust_factor():
    bars = _bars()
    bars[CALENDAR[5]] = SignalBar(open=11.0, close=5.5, adj_factor=2.0)
    result = compute_forward_result(SIGNAL, CALENDAR, bars, _bench(), data_end=CALENDAR[29])
    assert result.returns[5] == pytest.approx(10.0)


def test_compute_data_end_caps_maturity():
    result = compute_forward_result(
        SIGNAL, CALENDAR, _bars(), _bench(), data_end=CALENDAR[7]
    )
    assert result.mature == {5: True, 10: False, 20: False}
    assert result.returns[5] == pytest.approx(10.0)
    assert result.returns[10] is None
    assert result.returns[20] is None
    assert result.excess[10] is None


def test_compute_calendar_short_of_horizon_is_immature():
    result = compute_forward_result(
        SIGNAL, CALENDAR[:4], _bars(), _bench(), data_end=CALENDAR[3]
    )
    assert result.entry_date == CALENDAR[0]
    assert result.mature == {5: False, 10: False, 20: False}
    assert result.returns == {5: None, 10: None, 20: None}


def test_compute_missing_exit_bar_keeps_mature_but_no_return():
    bars = _bars()
    bars.pop(CALENDAR[5])
    result = compute_forward_result(SIGNAL, CALENDAR, bars, _bench(), data_end=CALENDAR[29])
    assert result.mature[5] is True
    assert result.returns[5] is None
    assert result.excess[5] is None
    assert result.returns[10] == pytest.approx(20.0)


def test_compute_missing_benchmark_entry_only_drops_excess():
    bench = _bench()
    bench.pop(CALENDAR[0])
    result = compute_forward_result(SIGNAL, CALENDAR, _bars(), bench, data_end=CALENDAR[29])
    assert result.returns[5] == pytest.approx(10.0)
    assert result.excess[5] is None


def test_compute_missing_benchmark_exit_only_drops_excess():
    bench = _bench()
    bench.pop(CALENDAR[10])
    result = compute_forward_result(SIGNAL, CALENDAR, _bars(), bench, data_end=CALENDAR[29])
    assert result.excess[5] == pytest.approx(5.0)
    assert result.returns[10] == pytest.approx(20.0)
    assert result.excess[10] is None


def test_compute_limit_up_and_suspended_are_not_fillable():
    for flag in ("is_limit_up", "is_suspended"):
        bars = _bars(SignalBar(open=10.0, close=11.0, adj_factor=1.0, **{flag: True}))
        result = compute_forward_result(
            SIGNAL, CALENDAR, bars, _bench(), data_end=CALENDAR[29]
        )
        assert result.fillable is False, flag
        # 不可成交仍保留参考收益，统计层负责排除
        assert result.entry_price == pytest.approx(10.0)
        assert result.returns[5] == pytest.approx(10.0)


def test_compute_missing_entry_bar_is_not_fillable():
    bars = _bars()
    bars.pop(CALENDAR[0])
    result = compute_forward_result(SIGNAL, CALENDAR, bars, _bench(), data_end=CALENDAR[29])
    assert result.entry_price is None
    assert result.fillable is False
    assert result.mature[5] is True
    assert result.returns[5] is None


def test_compute_rejects_non_positive_entry_open():
    for open_price in (0.0, -1.0, None):
        bars = _bars(SignalBar(open=open_price, close=10.0, adj_factor=1.0))
        result = compute_forward_result(
            SIGNAL, CALENDAR, bars, _bench(), data_end=CALENDAR[29]
        )
        assert result.entry_price is None, open_price
        assert result.fillable is False


def test_compute_missing_adj_factor_falls_back_to_one():
    for adj in (None, 0.0, -2.0):
        bars = _bars(SignalBar(open=10.0, close=10.0, adj_factor=adj))
        result = compute_forward_result(
            SIGNAL, CALENDAR, bars, _bench(), data_end=CALENDAR[29]
        )
        assert result.entry_price == pytest.approx(10.0)
        assert result.returns[5] == pytest.approx(10.0)


def test_compute_empty_bars_and_benchmark_do_not_crash():
    result = compute_forward_result(
        SIGNAL, CALENDAR, {}, {}, data_end=CALENDAR[29]
    )
    assert result.entry_price is None
    assert result.fillable is False
    assert result.returns == {5: None, 10: None, 20: None}
    assert result.excess == {5: None, 10: None, 20: None}


# ============================================================
# 天梯聚合
# ============================================================

def _row(
    algo: str,
    signal_date: date,
    code: str = "600000",
    *,
    fillable: bool = True,
    mature: tuple[bool, bool, bool] = (True, True, True),
    rets: tuple[float | None, float | None, float | None] = (1.0, 2.0, 3.0),
    excesses: tuple[float | None, float | None, float | None] = (0.5, 1.0, 1.5),
) -> PerfRow:
    return PerfRow(
        algo_code=algo,
        signal_date=signal_date,
        code=code,
        fillable=fillable,
        mature_5=mature[0],
        mature_10=mature[1],
        mature_20=mature[2],
        ret_5=rets[0],
        ret_10=rets[1],
        ret_20=rets[2],
        excess_5=excesses[0],
        excess_10=excesses[1],
        excess_20=excesses[2],
    )


def test_ladder_buckets_are_mutually_exclusive():
    rows = [
        _row("mix", CALENDAR[0], "600001", mature=(False, False, False),
             rets=(None, None, None), excesses=(None, None, None)),
        _row("mix", CALENDAR[1], "600002", fillable=False),
        _row("mix", CALENDAR[2], "600003", rets=(None, None, None),
             excesses=(None, None, None)),
    ]
    item = build_ladder(rows, [LadderAlgo("mix", "混合")], horizon=20)[0]
    assert item["immature"] == 1
    assert item["unfillable"] == 1
    assert item["no_data"] == 1
    assert item["sample_signals"] == 0
    assert item["total_signals"] == 3
    assert item["avg_ret"] is None
    assert item["win_rate"] is None
    assert item["spark"] == []
    assert item["low_sample"] is True


def test_ladder_ranks_by_excess_then_places_empty_last():
    rows = [
        _row("a3", CALENDAR[0], "600001", rets=(10, 10, 10), excesses=(5, 5, 5)),
        _row("a3", CALENDAR[0], "600002", rets=(-5, -5, -5), excesses=(-3, -3, -3)),
        _row("a3", CALENDAR[1], "600003", rets=(20, 20, 20), excesses=(10, 10, 10)),
        _row("a4", CALENDAR[0], "600004", rets=(50, 50, 50), excesses=(None, None, None)),
    ]
    algos = [LadderAlgo("a4", "A4"), LadderAlgo("a3", "A3"), LadderAlgo("b1", "B1")]
    items = build_ladder(rows, algos, horizon=20)

    assert [item["algo_code"] for item in items] == ["a3", "a4", "b1"]
    assert [item["rank"] for item in items] == [1, 2, 3]

    a3 = items[0]
    assert a3["sample_signals"] == 3
    assert a3["sample_stocks"] == 3
    assert a3["avg_ret"] == pytest.approx(8.33)
    assert a3["median_ret"] == pytest.approx(10.0)
    assert a3["win_rate"] == pytest.approx(0.667)
    assert a3["avg_excess"] == pytest.approx(4.0)
    assert a3["best"] == pytest.approx(20.0)
    assert a3["worst"] == pytest.approx(-5.0)
    assert a3["low_sample"] is True

    a4 = items[1]
    assert a4["avg_ret"] == pytest.approx(50.0)
    assert a4["avg_excess"] is None

    b1 = items[2]
    assert b1["sample_signals"] == 0
    assert b1["low_sample"] is True


def test_ladder_horizons_map_and_selected_horizon():
    rows = [_row("a3", CALENDAR[0], rets=(1.0, 2.0, 3.0), excesses=(0.1, 0.2, 0.3))]
    item = build_ladder(rows, [LadderAlgo("a3", "A3")], horizon=5)[0]

    assert set(item["horizons"]) == {str(value) for value in HORIZONS}
    assert item["horizons"]["5"]["avg_ret"] == pytest.approx(1.0)
    assert item["horizons"]["10"]["avg_ret"] == pytest.approx(2.0)
    assert item["horizons"]["20"]["avg_ret"] == pytest.approx(3.0)
    assert item["avg_ret"] == pytest.approx(1.0)
    assert item["avg_excess"] == pytest.approx(0.1)


def test_ladder_zero_return_is_not_a_win():
    rows = [_row("flat", CALENDAR[0], rets=(0.0, 0.0, 0.0), excesses=(0.0, 0.0, 0.0))]
    item = build_ladder(rows, [LadderAlgo("flat", "平")], horizon=20)[0]
    assert item["win_rate"] == 0.0
    assert item["avg_ret"] == 0.0
    assert item["low_sample"] is True


def test_ladder_spark_keeps_last_twenty_dates():
    rows = [
        _row("spark", date(2026, 1, 1) + timedelta(days=index),
             code=f"6{index:05d}", rets=(1, 1, 1), excesses=(0, 0, 0))
        for index in range(25)
    ]
    item = build_ladder(rows, [LadderAlgo("spark", "S")], horizon=20)[0]
    assert len(item["spark"]) == 20
    assert item["spark"][0]["trade_date"] == (date(2026, 1, 6)).isoformat()
    assert item["spark"][-1]["trade_date"] == (date(2026, 1, 25)).isoformat()
    assert all(point["win_rate"] == 1.0 and point["samples"] == 1 for point in item["spark"])


def test_ladder_low_sample_boundary():
    rows = [
        _row("edge", CALENDAR[0], code=f"6{index:05d}")
        for index in range(MIN_SAMPLE - 1)
    ]
    assert build_ladder(rows, [LadderAlgo("edge", "边界")], horizon=20)[0]["low_sample"] is True

    rows.append(_row("edge", CALENDAR[0], code="699999"))
    item = build_ladder(rows, [LadderAlgo("edge", "边界")], horizon=20)[0]
    assert item["sample_signals"] == MIN_SAMPLE
    assert item["low_sample"] is False
    # 同一交易日多只股票只算一个 spark 点
    assert len(item["spark"]) == 1
    assert item["spark"][0]["samples"] == MIN_SAMPLE


def test_ladder_empty_inputs():
    assert build_ladder([], [], horizon=20) == []
    items = build_ladder([], [LadderAlgo("only", "空")], horizon=20)
    assert len(items) == 1
    assert items[0]["rank"] == 1
    assert items[0]["sample_signals"] == 0
    assert items[0]["horizons"]["20"]["low_sample"] is True
