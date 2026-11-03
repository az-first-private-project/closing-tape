from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backfill_valuation as bv


class _FakeResult:
    def __init__(self, rows: int) -> None:
        self.rows = rows

    def summary(self) -> dict:
        return {"task": "fake", "rows": self.rows}


def _patch(monkeypatch, calls: list, *, fail: str | None = None) -> None:
    async def fake_financials(**kwargs):
        calls.append(("financials", kwargs))
        if fail == "financials":
            raise RuntimeError("boom")
        return _FakeResult(1)

    async def fake_dividends(**kwargs):
        calls.append(("dividends", kwargs))
        return _FakeResult(2)

    async def fake_revenue(**kwargs):
        calls.append(("revenue", kwargs))
        return _FakeResult(4)

    async def fake_valuation(start, end, codes):
        calls.append(("valuation", {"start": start, "end": end, "codes": codes}))
        return _FakeResult(3)

    monkeypatch.setattr(bv, "sync_financials", fake_financials)
    monkeypatch.setattr(bv, "sync_dividends", fake_dividends)
    monkeypatch.setattr(bv, "sync_revenue_bulk", fake_revenue)
    monkeypatch.setattr(bv, "compute_valuation", fake_valuation)


def test_run_stages_order_and_args(monkeypatch) -> None:
    calls: list = []
    _patch(monkeypatch, calls)

    summary = asyncio.run(bv.run_stages(
        start_year=2020, start=date(2024, 1, 2), end=date(2024, 2, 1),
        codes=["600519"], resume=False, only_missing=False,
    ))

    assert [name for name, _ in calls] == [
        "financials", "dividends", "revenue", "valuation",
    ]
    assert calls[0][1] == {
        "codes": ["600519"], "start_year": 2020,
        "only_missing": False, "resume": False,
    }
    assert calls[1][1] == {
        "codes": ["600519"], "only_missing": False, "resume": False,
    }
    assert calls[2][1] == {"years": 5, "force": True}
    assert calls[3][1] == {
        "start": date(2024, 1, 2), "end": date(2024, 2, 1), "codes": ["600519"],
    }
    assert summary["valuation"]["rows"] == 3


def test_stage_failure_does_not_abort(monkeypatch) -> None:
    calls: list = []
    _patch(monkeypatch, calls, fail="financials")

    summary = asyncio.run(bv.run_stages(start=date(2024, 1, 2), end=date(2024, 1, 3)))

    assert [name for name, _ in calls] == [
        "financials", "dividends", "revenue", "valuation",
    ]
    assert "boom" in summary["financials"]["error"]
    assert summary["dividends"]["rows"] == 2


def test_single_stage_filter(monkeypatch) -> None:
    calls: list = []
    _patch(monkeypatch, calls)

    asyncio.run(bv.run_stages(
        stages=("valuation",), start=date(2024, 1, 2), end=date(2024, 1, 3),
    ))

    assert [name for name, _ in calls] == ["valuation"]


def test_parse_codes() -> None:
    assert bv._parse_codes(None) is None
    assert bv._parse_codes("  ") is None
    assert bv._parse_codes("600519, 000001") == ["600519", "000001"]


def test_as_date_fallback() -> None:
    assert bv._as_date("2024-01-02", date(2022, 9, 1)) == date(2024, 1, 2)
    assert bv._as_date(None, date(2022, 9, 1)) == date(2022, 9, 1)
