"""指数多周期收益纯函数单测（T1）。

每个用例锁定一条口径：
  - 周期按交易日（行数）计数；
  - 历史不足返回 None，不返回 0；
  - YTD 基准是「当年首根的前一根」；
  - as_of 之后的数据不参与（美股 T-1 锚点）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.engine.index_perf import window_returns  # noqa: E402


def _series(start: date, values: list[float]) -> list[tuple[date, float]]:
    """按自然日递增构造序列；函数只把行数当交易日，日期连续即可。"""
    from datetime import timedelta

    return [(start + timedelta(days=i), value) for i, value in enumerate(values)]


def test_windows_use_trading_day_rows():
    closes = _series(date(2026, 5, 1), [100.0 + i for i in range(70)])
    result = window_returns(closes, as_of=date(2026, 7, 9))

    assert set(result) == {"ret_1d", "ret_5d", "ret_20d", "ret_60d", "ret_ytd"}
    assert result["ret_1d"] == round((169 / 168 - 1) * 100, 4)
    assert result["ret_5d"] == round((169 / 164 - 1) * 100, 4)
    assert result["ret_20d"] == round((169 / 149 - 1) * 100, 4)
    assert result["ret_60d"] == round((169 / 109 - 1) * 100, 4)


def test_insufficient_history_returns_none_not_zero():
    closes = _series(date(2026, 5, 1), [100.0, 101.0, 102.0])
    result = window_returns(closes, as_of=date(2026, 5, 3))

    assert result["ret_1d"] == round((102 / 101 - 1) * 100, 4)
    assert result["ret_5d"] is None
    assert result["ret_20d"] is None
    assert result["ret_60d"] is None
    assert result["ret_ytd"] is None


def test_ytd_uses_last_close_before_year_start():
    closes = [
        (date(2025, 12, 30), 100.0),
        (date(2025, 12, 31), 101.0),
        (date(2026, 1, 2), 102.0),
        (date(2026, 1, 5), 103.0),
    ]
    result = window_returns(closes, as_of=date(2026, 1, 5))

    assert result["ret_ytd"] == round((103 / 101 - 1) * 100, 4)
    assert result["ret_1d"] == round((103 / 102 - 1) * 100, 4)


def test_ytd_none_when_no_prior_year_baseline():
    closes = [
        (date(2026, 1, 2), 102.0),
        (date(2026, 1, 5), 103.0),
    ]
    result = window_returns(closes, as_of=date(2026, 1, 5))

    assert result["ret_ytd"] is None


def test_as_of_ignores_future_rows_and_uses_latest_available():
    closes = [
        (date(2026, 1, 5), 100.0),
        (date(2026, 1, 6), 101.0),
        (date(2026, 1, 7), 999.0),
    ]
    result = window_returns(closes, as_of=date(2026, 1, 6))

    # 美股 T-1 场景：as_of 当天没有行时，锚点回退到最近一根
    assert result["ret_1d"] == round((101 / 100 - 1) * 100, 4)


def test_invalid_and_duplicate_closes_are_filtered():
    closes = [
        (date(2026, 1, 5), 100.0),
        (date(2026, 1, 5), 100.0),   # 同日重复：保留最后一条
        (date(2026, 1, 6), None),    # 空值剔除
        (date(2026, 1, 7), 0.0),     # 0 价剔除，不能当成"没涨跌"
    ]
    result = window_returns(closes, as_of=date(2026, 1, 10))

    assert result["ret_1d"] is None
    assert result["ret_5d"] is None
