"""牛熊位置 × 估值计算测试（V7 T4）"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pd = pytest.importorskip("pandas")

from app.core.constants import (  # noqa: E402
    BROAD_ERP_SNAPSHOT,
    BROAD_INDEX_MAP,
    BROAD_INDEX_SNAPSHOT,
    BROAD_SUMMARY,
)
from app.engine.broad_valuation import (  # noqa: E402
    DISCLAIMER,
    REQUIRED_SENTENCE,
    build_broad_valuation,
)


def _rows(days: int = 800) -> list[dict]:
    start = date(2023, 1, 1)
    rows: list[dict] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for index, code in enumerate(BROAD_INDEX_MAP):
            # 6 条不同斜率/波动的序列
            close = 1000 + index * 200 + offset * (0.5 + index * 0.05) + (offset % 7)
            rows.append({
                "index_code": code,
                "trade_date": day,
                "close": close,
                "src": "sina" if code != "sh000922" else "csindex",
            })
    return rows


def test_build_broad_valuation_contract() -> None:
    result = build_broad_valuation(_rows())
    assert result["snapshot_date"] == "2026-11-13"
    assert result["required_sentence"] == REQUIRED_SENTENCE
    assert result["summary"] == {
        "above_ma200": BROAD_SUMMARY["above_ma200"],
        "total": BROAD_SUMMARY["total"],
        "avg_drawdown": BROAD_SUMMARY["avg_drawdown"],
        "avg_drawdown_5y": BROAD_SUMMARY["avg_drawdown_5y"],
    }
    assert result["erp"]["erp"] == BROAD_ERP_SNAPSHOT["erp"]
    assert result["erp"]["percentile"] == BROAD_ERP_SNAPSHOT["percentile"]
    assert "不是 21 年" in result["erp"]["note"]

    codes = [item["code"] for item in result["indices"]]
    assert codes == list(BROAD_INDEX_MAP)
    assert len(codes) == 6
    assert "上证50" not in "".join(item["name"] for item in result["indices"])
    assert "中证1000" not in "".join(item["name"] for item in result["indices"])

    for item in result["indices"]:
        assert item["pe"] == BROAD_INDEX_SNAPSHOT[item["code"]]["pe"]
        assert item["earnings_yield"] == BROAD_INDEX_SNAPSHOT[item["code"]]["earnings_yield"]

    assert set(result["normalized"]) == {"3y", "10y", "full"}
    assert len(result["momentum"]) == 6
    assert result["momentum"][0]["m1"] is not None
    assert len(result["drawdown_history"]) > 0
    assert result["disclaimer"] == DISCLAIMER
    assert any("PE" in item and "历史估值分位" in item for item in result["caveats"])
    assert any("对数刻度" in item for item in result["caveats"])


def test_normalized_starts_at_100() -> None:
    result = build_broad_valuation(_rows())
    full = result["normalized"]["full"]
    assert full
    first = full[0]
    for code in BROAD_INDEX_MAP:
        assert first[code] == pytest.approx(100.0)


def test_build_broad_valuation_rejects_missing_index() -> None:
    rows = [row for row in _rows() if row["index_code"] != "sh000922"]
    with pytest.raises(ValueError):
        build_broad_valuation(rows)


def test_bull_bear_route_registered() -> None:
    from app.api.v1 import market

    paths = {getattr(route, "path", "") for route in market.router.routes}
    assert "/api/v1/market/bull-bear" in paths
