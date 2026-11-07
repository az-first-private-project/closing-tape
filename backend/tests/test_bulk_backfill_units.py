"""成交量单位回归 + 失败代码自动重试。

成交量：腾讯科创板（68 开头）返回股，主板/创业板返回手；
重试：单只失败在整轮结束后按退避重试，恢复后不得继续算失败；
数据源整体不可用时跳过重试，避免放大请求量。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.ingest import bulk_backfill as bb
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


# ============================================================
# 失败代码自动重试
# ============================================================
def test_retry_failed_recovers_transient_codes(monkeypatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(bb.asyncio, "sleep", no_sleep)

    failed = {"600000": "boom", "600001": "boom"}
    calls: list[list[str]] = []

    async def consume(codes) -> None:
        calls.append(list(codes))
        if "600000" in codes:
            failed.pop("600000", None)

    recovered = asyncio.run(
        bb._retry_failed(failed, consume, attempted=10, label="日线回补")
    )

    assert recovered == 1
    assert calls == [["600000", "600001"], ["600001"]]
    assert failed == {"600001": "boom"}


def test_retry_failed_skips_when_source_wide_failure(monkeypatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(bb.asyncio, "sleep", no_sleep)

    failed = {f"{index:06d}": "down" for index in range(300)}
    calls: list[list[str]] = []

    async def consume(codes) -> None:
        calls.append(list(codes))

    recovered = asyncio.run(
        bb._retry_failed(failed, consume, attempted=500, label="日线回补")
    )

    assert recovered == 0
    assert calls == []
    assert len(failed) == 300


def test_backfill_prices_reports_quality_failed_after_retries(monkeypatch) -> None:
    """单只始终失败：重试 2 轮后必须返回 quality_failed，不能伪装成成功。"""
    calls = 0

    async def fake_fetch(code: str, *, count: int):
        nonlocal calls
        calls += 1
        raise RuntimeError("dns down")

    async def no_sleep(_seconds: float) -> None:
        return None

    async def fake_meta(codes, db):
        return {}

    async def fake_dividends(start, end):
        return {}

    async def fake_write(db, fn):
        return None

    monkeypatch.setattr(bb.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(bb, "fetch_price_pair", fake_fetch)
    monkeypatch.setattr(bb, "_load_meta", fake_meta)
    monkeypatch.setattr(bb, "fetch_dividends", fake_dividends)
    monkeypatch.setattr(bb, "_write", fake_write)
    monkeypatch.setattr(bb, "_load_state", lambda kind, end: set())
    monkeypatch.setattr(bb, "_save_state", lambda kind, end, completed: None)

    result = asyncio.run(
        bb.backfill_prices(
            start=date(2026, 11, 6), end=date(2026, 11, 6),
            codes=["000001"], db=object(),
        )
    )

    assert calls == 1 + bb.RETRY_ROUNDS
    assert result.failed == 1
    assert result.quality_status == "quality_failed"
    assert result.detail["retry_recovered"] == 0
    assert result.detail["failed_codes"] == ["000001"]


def test_backfill_prices_clears_failed_after_retry_success(monkeypatch) -> None:
    """首轮失败、重试成功：不再算失败，返回 ok 并记录恢复数。"""
    attempts = {"600000": 0}

    async def fake_fetch(code: str, *, count: int):
        if code == "600000":
            attempts[code] += 1
            if attempts[code] == 1:
                raise RuntimeError("transient")
        return [], [], "tencent"

    async def no_sleep(_seconds: float) -> None:
        return None

    async def fake_meta(codes, db):
        return {}

    async def fake_dividends(start, end):
        return {}

    async def fake_write(db, fn):
        return None

    monkeypatch.setattr(bb.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(bb, "fetch_price_pair", fake_fetch)
    monkeypatch.setattr(bb, "_load_meta", fake_meta)
    monkeypatch.setattr(bb, "fetch_dividends", fake_dividends)
    monkeypatch.setattr(bb, "_write", fake_write)
    monkeypatch.setattr(bb, "_load_state", lambda kind, end: set())
    monkeypatch.setattr(bb, "_save_state", lambda kind, end, completed: None)

    result = asyncio.run(
        bb.backfill_prices(
            start=date(2026, 11, 6), end=date(2026, 11, 6),
            codes=["600000"], db=object(),
        )
    )

    assert attempts["600000"] == 2
    assert result.failed == 0
    assert result.quality_status == "ok"
    assert result.detail["retry_recovered"] == 1
    assert result.detail["failed_codes"] == []
