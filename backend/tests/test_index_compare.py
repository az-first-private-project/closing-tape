"""指数对比 / 风格轮动计算测试（V7 T3）"""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

np = pytest.importorskip("numpy")

from app.engine.index_compare import CYB50_CODE, HL50_CODE, build_index_compare  # noqa: E402


def _rows(days: int = 300, last_cyb: float | None = None, last_hl: float | None = None):
    start = date(2025, 1, 1)
    rows = []
    for index in range(days):
        day = start + timedelta(days=index)
        # 围绕 0.2 的平稳比价，避免单调序列把当前点推到极值
        cyb = 1000 + 10 * math.sin(2 * math.pi * index / 40)
        hl = 5000.0
        if index == days - 1 and last_cyb is not None:
            cyb = last_cyb
        if index == days - 1 and last_hl is not None:
            hl = last_hl
        rows.append({"index_code": CYB50_CODE, "trade_date": day, "close": cyb, "src": "sina"})
        rows.append({"index_code": HL50_CODE, "trade_date": day, "close": hl, "src": "csindex"})
    return rows


def test_build_index_compare_basic_contract() -> None:
    result = build_index_compare(_rows())
    assert result["R"] == pytest.approx(
        result["closes"]["cyb50"] / result["closes"]["hl50"], abs=0.0001
    )
    assert 0 <= result["percentile"] <= 100
    assert result["thresholds"]["low"] <= result["thresholds"]["high"]
    assert result["zone"] in {"低估区", "均衡区", "高估区"}
    assert result["allocation"]["batches"] == 5
    assert result["allocation"]["step_pct"] == 20
    assert result["allocation"]["cyb50_current"] is None
    assert result["allocation"]["hl50_current"] is None
    assert result["disclaimer"] == "历史数据回测不代表未来表现，不构成投资建议。"
    assert "相对强弱" in result["risk"]
    assert len(result["chart"]["history"]) <= 750
    dates = [point["date"] for point in result["chart"]["history"]]
    assert dates == sorted(dates)
    assert result["meta"]["common_start"] <= result["meta"]["common_end"]


def test_build_index_compare_zones_and_targets() -> None:
    low = build_index_compare(_rows(last_cyb=800))
    assert low["zone"] == "低估区"
    assert low["allocation"]["cyb50_target"] == 1.0
    assert low["allocation"]["hl50_target"] == 0.0

    high = build_index_compare(_rows(last_cyb=1700))
    assert high["zone"] == "高估区"
    assert high["allocation"]["cyb50_target"] == 0.0
    assert high["allocation"]["hl50_target"] == 1.0

    balanced = build_index_compare(_rows())
    assert balanced["zone"] == "均衡区"
    assert balanced["allocation"]["cyb50_target"] == 0.5
    assert balanced["allocation"]["hl50_target"] == 0.5


def test_build_index_compare_fixed_fallback() -> None:
    result = build_index_compare(_rows(days=100))
    assert result["thresholds"]["method"] == "固定分位"
    assert result["thresholds"]["low"] == 0.18
    assert result["thresholds"]["high"] == 0.30
    assert result["percentile"] is not None


def test_build_index_compare_uses_trade_date_cutoff() -> None:
    rows = _rows(days=300)
    cutoff = rows[-4]["trade_date"]
    expected = next(
        row["close"] for row in rows
        if row["index_code"] == CYB50_CODE and row["trade_date"] == cutoff
    )
    result = build_index_compare(rows, trade_date=cutoff)
    assert result["date"] == cutoff.isoformat()
    assert result["closes"]["cyb50"] == pytest.approx(expected, abs=0.01)


def test_build_index_compare_rejects_incomplete_data() -> None:
    only_one = [row for row in _rows(days=300) if row["index_code"] == CYB50_CODE]
    with pytest.raises(ValueError):
        build_index_compare(only_one)


def test_index_compare_route_registered() -> None:
    from app.api.v1 import market

    paths = {getattr(route, "path", "") for route in market.router.routes}
    assert "/api/v1/market/index-compare" in paths
