"""
市场温度计单测（V6 T3）
========================
不依赖数据库/网络：只测纯函数与 SQL 语句构造。

覆盖：
  - 分段归一化：任务书示例断点（30%→20、50%→50、70%→80）、端点截断、降温项单调递减；
  - 六个子项权重合计 1.0、断点按原始值单调；
  - 合成规则：缺关键子项 → insufficient + null；缺非关键子项 → partial 且权重重归一
    （**不能给缺项补 0**）；可用子项不足 → insufficient；
  - 市场宽度抽公共模块后输出字段不变；温度计口径可按需剔除停牌。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pytest.importorskip("numpy", reason="numpy 未安装，跳过温度计测试")
pytest.importorskip("sqlalchemy", reason="sqlalchemy 未安装，跳过温度计测试")

from app.core.constants import (  # noqa: E402
    TEMP_BREAKPOINTS,
    TEMP_COMPONENT_META,
    TEMP_COMPONENT_ORDER,
    TEMP_COOLING_COMPONENTS,
    TEMP_WEIGHTS,
)
from app.engine import report_input  # noqa: E402
from app.engine.breadth import load_breadth  # noqa: E402
from app.engine.market_temperature import (  # noqa: E402
    combine_components,
    piecewise_normalize,
)


# ============================================================
# 分段归一化
# ============================================================

def test_up_ratio_breakpoints_match_taskbook():
    """任务书示例：50%→50 分、70%→80 分、30%→20 分。"""
    breakpoints = TEMP_BREAKPOINTS["up_ratio"]
    assert piecewise_normalize(0.30, breakpoints) == pytest.approx(20.0)
    assert piecewise_normalize(0.50, breakpoints) == pytest.approx(50.0)
    assert piecewise_normalize(0.70, breakpoints) == pytest.approx(80.0)


def test_piecewise_interpolates_and_clamps():
    breakpoints = TEMP_BREAKPOINTS["up_ratio"]
    # 0.5→50、0.7→80 的中点应为 65（线性、单调）
    assert piecewise_normalize(0.60, breakpoints) == pytest.approx(65.0)
    # 超出两端取端点，不外推
    assert piecewise_normalize(-0.5, breakpoints) == pytest.approx(0.0)
    assert piecewise_normalize(1.5, breakpoints) == pytest.approx(100.0)
    # 缺数据不得代入 0
    assert piecewise_normalize(None, breakpoints) is None
    assert piecewise_normalize(float("nan"), breakpoints) is None


def test_cooling_components_are_monotonic_decreasing():
    """跌停家数 / 指数波动率越高 → 得分越低（降温项）。"""
    for key in TEMP_COOLING_COMPONENTS:
        breakpoints = TEMP_BREAKPOINTS[key]
        scores = [piecewise_normalize(raw, breakpoints) for raw, _ in breakpoints]
        assert scores == sorted(scores, reverse=True), key
        assert scores[0] == pytest.approx(breakpoints[0][1])
        assert scores[-1] == pytest.approx(breakpoints[-1][1])


def test_all_components_normalize_inside_0_100_and_monotonic():
    for key in TEMP_COMPONENT_ORDER:
        breakpoints = TEMP_BREAKPOINTS[key]
        raw_values = [raw for raw, _ in breakpoints]
        # 在断点之间取样，验证单调性与范围
        samples: list[float] = []
        for x0, x1 in zip(raw_values, raw_values[1:]):
            samples.extend([x0, x0 + (x1 - x0) / 2, x1])
        scores = [piecewise_normalize(value, breakpoints) for value in samples]
        assert all(score is not None and 0.0 <= score <= 100.0 for score in scores), key
        if key in TEMP_COOLING_COMPONENTS:
            assert all(a >= b for a, b in zip(scores, scores[1:])), key
        else:
            assert all(a <= b for a, b in zip(scores, scores[1:])), key


def test_weights_and_metadata_are_complete():
    assert set(TEMP_WEIGHTS) == set(TEMP_COMPONENT_ORDER)
    assert sum(TEMP_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(TEMP_BREAKPOINTS) == set(TEMP_COMPONENT_ORDER)
    assert set(TEMP_COMPONENT_META) == set(TEMP_COMPONENT_ORDER)
    for key, breakpoints in TEMP_BREAKPOINTS.items():
        assert len(breakpoints) >= 2
        raw_values = [raw for raw, _ in breakpoints]
        assert raw_values == sorted(raw_values), key


# ============================================================
# 合成规则
# ============================================================

ALL_SCORES = {
    "up_ratio": 80.0,
    "limit_up": 60.0,
    "limit_down": 40.0,
    "above_ma20": 50.0,
    "amount_ratio": 30.0,
    "index_vol": 20.0,
}


def _items(scores: dict[str, float | None]) -> list[dict]:
    return [
        {
            "key": key,
            "score": scores.get(key),
            "weight": TEMP_WEIGHTS[key],
            "available": scores.get(key) is not None,
        }
        for key in TEMP_COMPONENT_ORDER
    ]


def test_combine_all_available_is_ok():
    score, confidence, available, critical_missing = combine_components(_items(ALL_SCORES))
    # 0.30*80 + 0.15*60 + 0.10*40 + 0.20*50 + 0.15*30 + 0.10*20 = 53.5
    assert score == pytest.approx(53.5)
    assert confidence == "ok"
    assert available == 6
    assert critical_missing == []


def test_combine_missing_noncritical_is_partial_without_zero_fill():
    scores = dict(ALL_SCORES)
    scores["amount_ratio"] = None
    score, confidence, available, critical_missing = combine_components(_items(scores))
    # 缺项权重重归一：49.0 / 0.85 ≈ 57.6，而不是把 amount_ratio 当 0 → 49.0
    assert score == pytest.approx(57.6)
    assert confidence == "partial"
    assert available == 5
    assert critical_missing == []


def test_combine_missing_above_ma20_is_insufficient():
    scores = dict(ALL_SCORES)
    scores["above_ma20"] = None
    score, confidence, available, critical_missing = combine_components(_items(scores))
    assert score is None
    assert confidence == "insufficient"
    assert available == 5
    assert critical_missing == ["above_ma20"]


def test_combine_missing_up_ratio_is_insufficient():
    scores = dict(ALL_SCORES)
    scores["up_ratio"] = None
    score, confidence, _, critical_missing = combine_components(_items(scores))
    assert score is None
    assert confidence == "insufficient"
    assert critical_missing == ["up_ratio"]


def test_combine_too_few_components_is_insufficient():
    scores = dict(ALL_SCORES)
    scores["limit_up"] = None
    scores["amount_ratio"] = None
    scores["index_vol"] = None
    score, confidence, available, _ = combine_components(_items(scores))
    assert score is None
    assert confidence == "insufficient"
    assert available == 3


# ============================================================
# 市场宽度（公共模块）
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


def test_load_breadth_keeps_output_shape():
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


def test_load_breadth_excludes_suspended_only_when_asked():
    session_default = _FakeSession([])
    asyncio.run(load_breadth(session_default, date(2026, 1, 5)))
    assert "is_suspended" not in str(session_default.statements[0])

    session_temperature = _FakeSession([])
    asyncio.run(load_breadth(session_temperature, date(2026, 1, 5), exclude_suspended=True))
    assert "is_suspended" in str(session_temperature.statements[0])


def test_report_input_uses_shared_breadth_module():
    """搬移后日报侧不再保留第二份实现。"""
    assert not hasattr(report_input, "_load_breadth")
    assert report_input.load_breadth is load_breadth
