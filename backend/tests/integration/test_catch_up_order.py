"""不变量 4：catch-up 的 margin 必须排在评分/日报之后，signal_perf 最后。"""
from __future__ import annotations

from datetime import date

import pytest

from app import catch_up as cu
from app import pipeline

pytestmark = pytest.mark.asyncio

D1 = date(2026, 11, 2)
D2 = date(2026, 11, 3)
TODAY = date(2026, 11, 4)


async def test_margin_after_daily_outputs_and_signal_perf_last(monkeypatch) -> None:
    calls: list[tuple[str, date | None]] = []

    async def fake_run_task(task: str, **kwargs):
        calls.append((task, kwargs.get("trade_date")))
        return {"status": "success"}

    async def fake_pipeline(day: date, db, steps=()):
        calls.append(("daily_pipeline", day))
        return {"steps": {}}

    monkeypatch.setattr(pipeline, "run_task", fake_run_task)
    monkeypatch.setattr(pipeline, "run_daily_pipeline", fake_pipeline)

    await cu._execute_catch_up(
        [D1, D2], object(), today=TODAY, snapshot_ok=False, stock_complete=False
    )

    names = [name for name, _ in calls]
    assert names[: len(cu.RANGE_STEPS)] == list(cu.RANGE_STEPS)
    assert "margin" not in cu.RANGE_STEPS

    offset = len(cu.RANGE_STEPS)
    for day in (D1, D2):
        day_steps = names[offset: offset + len(cu.DAY_STEPS)]
        assert day_steps == list(cu.DAY_STEPS)
        assert day_steps[-1] == "margin"
        for earlier in ("sector_scores", "temperature", "report", "algos"):
            assert day_steps.index("margin") > day_steps.index(earlier)
        assert day_steps.index("macro_rates") < day_steps.index("market_factors")
        assert day_steps.index("market_factors") < day_steps.index("temperature")
        assert day_steps.index("temperature") < day_steps.index("regime")
        assert day_steps.index("regime") < day_steps.index("report")
        offset += len(cu.DAY_STEPS)

    assert names[-1] == "signal_perf"


async def test_stock_complete_skips_history_fast(monkeypatch) -> None:
    """行情已完整时，只补下游，不再逐只重拉 5000+ 股票历史。"""
    calls: list[str] = []

    async def fake_run_task(task: str, **kwargs):
        calls.append(task)
        return {"status": "success"}

    async def fake_pipeline(day: date, db, steps=()):
        return {"steps": {}}

    monkeypatch.setattr(pipeline, "run_task", fake_run_task)
    monkeypatch.setattr(pipeline, "run_daily_pipeline", fake_pipeline)

    await cu._execute_catch_up(
        [D1], object(), today=TODAY, snapshot_ok=False, stock_complete=True
    )

    assert "history_fast" not in calls
    assert "valuation_fast" in calls
    assert "market_factors" in calls
    assert calls[-1] == "signal_perf"
