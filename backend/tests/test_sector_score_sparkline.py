"""板块评分 sparkline 接口（P3-2）测试。

纯函数测试始终运行；端到端用例需要真实 PostgreSQL，设置 GFNA_TEST_DB=1 后运行。
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

os.environ.setdefault("SCHEDULER_ENABLED", "0")
os.environ.setdefault("AUTO_CREATE_TABLES", "0")

from app.api.v1.market import _align_sector_scores, _parse_sector_codes  # noqa: E402
from app.core.errors import InvalidParamError  # noqa: E402


def test_parse_sector_codes_dedupes_and_keeps_order() -> None:
    assert _parse_sector_codes(" THS_光伏设备,THS_BC电池, THS_光伏设备 ") == [
        "THS_光伏设备", "THS_BC电池",
    ]


@pytest.mark.parametrize("raw", ["", " , ", "THS 光伏设备", "X" * 33])
def test_parse_sector_codes_rejects_invalid(raw: str) -> None:
    with pytest.raises(InvalidParamError):
        _parse_sector_codes(raw)


def test_parse_sector_codes_rejects_too_many() -> None:
    raw = ",".join(f"S{i}" for i in range(101))
    with pytest.raises(InvalidParamError):
        _parse_sector_codes(raw)


def test_align_sector_scores_fills_gaps_with_null() -> None:
    d1, d2, d3 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
    rows = [
        ("THS_A", d1, Decimal("5.52")),
        ("THS_A", d3, Decimal("6.10")),
        ("THS_B", d3, 7.0),
    ]
    aligned = _align_sector_scores(["THS_B", "THS_A"], [d1, d2, d3], rows)

    assert [item["sector_code"] for item in aligned] == ["THS_B", "THS_A"]
    assert aligned[0]["scores"] == [None, None, 7.0]
    assert aligned[1]["scores"] == [5.52, None, 6.1]


def test_align_sector_scores_keeps_empty_code_series() -> None:
    day = date(2026, 11, 6)
    assert _align_sector_scores(["THS_无评分"], [day], []) == [
        {"sector_code": "THS_无评分", "scores": [None]},
    ]


@pytest.mark.skipif(
    not os.getenv("GFNA_TEST_DB"),
    reason="需要 PostgreSQL 与真实评分数据；设置 GFNA_TEST_DB=1 后运行",
)
def test_sector_score_sparkline_matches_dashboard() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        dashboard = client.get("/api/v1/market/dashboard").json()
        scored = [s for s in dashboard["sectors"] if s["composite_score"] is not None]
        scored.sort(key=lambda s: s["composite_score"], reverse=True)
        picked = scored[:10]
        assert picked, "当前交易日没有已评分板块"

        codes = [sector["sector_code"] for sector in picked]
        resp = client.get("/api/v1/market/sector-scores/sparkline", params={
            "codes": ",".join(codes),
            "days": 30,
            "end": dashboard["trade_date"],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["end"] == dashboard["trade_date"]
        assert [item["sector_code"] for item in body["items"]] == codes
        assert body["dates"] == sorted(body["dates"]), "横轴必须按日期升序"
        assert len(body["dates"]) <= 30

        by_code = {item["sector_code"]: item["scores"] for item in body["items"]}
        for sector in picked:
            series = by_code[sector["sector_code"]]
            assert len(series) == len(body["dates"])
            assert all(value is None or 0 <= value <= 10 for value in series)
            last = next(value for value in reversed(series) if value is not None)
            assert last == pytest.approx(sector["composite_score"], abs=0.005), (
                f"{sector['sector_code']} 序列末值必须等于当日综合分"
            )
