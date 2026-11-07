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

    await cu._execute_catch_up([D1, D2], object(), today=TODAY, snapshot_ok=False)

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
        offset += len(cu.DAY_STEPS)

    assert names[-1] == "signal_perf"
