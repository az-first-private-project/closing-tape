"""不变量 5/6：checkpoint 只在提交后推进、失败码可重试清除、同日重跑幂等。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import FactStockDaily
from app.ingest import bulk_backfill
from app.ingest.base import upsert_rows as real_upsert_rows

pytestmark = pytest.mark.asyncio

START = date(2026, 10, 29)
END = date(2026, 10, 30)
CODE = "600519"


def _quote(day: date, close: float) -> dict:
    return {
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 10_000,
    }


def _fetch_pair(quotes: dict[date, float]):
    calls: list[str] = []

    async def fake(code: str, count: int | None = None):
        calls.append(code)
        raw = [_quote(day, close) for day, close in sorted(quotes.items())]
        return raw, list(raw), "tencent"

    return fake, calls


async def _no_dividends(start: date, end: date) -> dict:
    return {}


async def _row_count(db, code: str = CODE) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(FactStockDaily)
            .where(
                FactStockDaily.code == code,
                FactStockDaily.trade_date >= START,
                FactStockDaily.trade_date <= END,
            )
        )
        or 0
    )


async def _close_on(db, day: date, code: str = CODE) -> float:
    value = await db.scalar(
        select(FactStockDaily.close).where(
            FactStockDaily.code == code,
            FactStockDaily.trade_date == day,
        )
    )
    return float(value)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(bulk_backfill, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(bulk_backfill, "fetch_dividends", _no_dividends)


async def test_checkpoint_advances_only_after_commit(db_session, monkeypatch) -> None:
    quotes = {START - timedelta(days=1): 10.0, START: 10.1, END: 10.2}
    fetch, _ = _fetch_pair(quotes)
    monkeypatch.setattr(bulk_backfill, "fetch_price_pair", fetch)

    async def boom(*args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(bulk_backfill, "upsert_rows", boom)
    with pytest.raises(RuntimeError, match="write failed"):
        await bulk_backfill.backfill_prices(START, END, [CODE], db=db_session)

    assert bulk_backfill._load_state("prices", END) == set()
    assert await _row_count(db_session) == 0

    monkeypatch.setattr(bulk_backfill, "upsert_rows", real_upsert_rows)
    result = await bulk_backfill.backfill_prices(START, END, [CODE], db=db_session)

    assert result.failed == 0
    assert bulk_backfill._load_state("prices", END) == {CODE}
    assert await _row_count(db_session) == 2


async def test_transient_failure_is_retried_and_checkpoint_cleared(
    db_session, monkeypatch
) -> None:
    monkeypatch.setattr(bulk_backfill, "RETRY_BACKOFF_SEC", 0)
    quotes = {START - timedelta(days=1): 10.0, START: 10.1, END: 10.2}
    fetch, calls = _fetch_pair(quotes)
    failed_once = False

    async def flaky(code: str, count: int | None = None):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            calls.append(code)
            raise ConnectionResetError("temporary reset")
        return await fetch(code, count)

    monkeypatch.setattr(bulk_backfill, "fetch_price_pair", flaky)
    result = await bulk_backfill.backfill_prices(START, END, [CODE], db=db_session)

    assert result.failed == 0
    assert result.quality_status == "ok"
    assert result.detail["retry_recovered"] == 1
    assert calls == [CODE, CODE]
    assert bulk_backfill._load_state("prices", END) == {CODE}
    assert await _row_count(db_session) == 2


async def test_same_day_repeat_skips_and_force_rerun_stays_single_row(
    db_session, monkeypatch
) -> None:
    quotes = {START - timedelta(days=1): 10.0, START: 10.1, END: 10.2}
    fetch, calls = _fetch_pair(quotes)
    monkeypatch.setattr(bulk_backfill, "fetch_price_pair", fetch)

    first = await bulk_backfill.backfill_prices(START, END, [CODE], db=db_session)
    assert first.failed == 0
    assert calls == [CODE]

    second = await bulk_backfill.backfill_prices(START, END, [CODE], db=db_session)
    assert second.failed == 0
    assert calls == [CODE], "断点应跳过已完成代码，不再请求数据源"
    assert await _row_count(db_session) == 2

    quotes[END] = 11.0
    forced = await bulk_backfill.backfill_prices(
        START, END, [CODE], db=db_session, force=True
    )

    assert forced.failed == 0
    assert calls == [CODE, CODE], "force 重跑应重新抓取"
    assert await _row_count(db_session) == 2
    assert await _close_on(db_session, END) == pytest.approx(11.0)
