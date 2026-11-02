"""成交量单位回归：腾讯科创板（68 开头）返回股，主板/创业板返回手。"""
from __future__ import annotations

from datetime import date

import pytest

from app.ingest.bulk_backfill import _price_rows


def _bar(day: str, close: float, volume: float) -> dict:
    return {
        "trade_date": date.fromisoformat(day),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    }


def _meta(name: str, total_share: float, float_share: float) -> dict:
    return {"name": name, "total_share": total_share, "float_share": float_share}


@pytest.mark.parametrize(
    ("code", "src", "raw_volume", "expected_volume"),
    [
        ("688981", "tencent", 29_998_989, 29_998_989),
        ("688981", "sina", 299_989.89, 29_998_989),
        ("600519", "tencent", 59_927, 5_992_700),
        ("600519", "sina", 59_927.29, 5_992_729),
    ],
)
def test_volume_unit_by_board_and_source(
    code: str, src: str, raw_volume: float, expected_volume: int
) -> None:
    raw = [
        _bar("2026-10-29", 101.0 if code == "688981" else 1354.0, raw_volume),
        _bar("2026-10-30", 96.79 if code == "688981" else 1392.05, raw_volume),
    ]
    qfq = [{"trade_date": row["trade_date"], "close": row["close"]} for row in raw]
    meta = (
        _meta("中芯国际", 8_562_381_909, 2_000_593_909)
        if code == "688981"
        else _meta("贵州茅台", 1_250_081_601, 1_250_081_601)
    )

    rows = _price_rows(
        code, meta, raw, qfq, date(2026, 10, 30), date(2026, 10, 30), events=[], src=src
    )

    assert len(rows) == 1
    assert rows[0]["volume"] == expected_volume


def test_star_tencent_does_not_inflate_amount_and_turnover() -> None:
    raw = [
        _bar("2026-10-29", 101.0, 28_000_000),
        _bar("2026-10-30", 96.79, 29_998_989),
    ]
    qfq = [{"trade_date": row["trade_date"], "close": row["close"]} for row in raw]

    rows = _price_rows(
        "688981",
        _meta("中芯国际", 8_562_381_909, 2_000_593_909),
        raw,
        qfq,
        date(2026, 10, 30),
        date(2026, 10, 30),
        events=[],
        src="tencent",
    )

    assert rows[0]["turnover_rate"] == pytest.approx(1.4995, abs=1e-4)
    assert rows[0]["amount"] < 4_000_000_000
