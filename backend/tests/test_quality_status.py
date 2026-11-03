"""质量状态传递测试（不联网、不连库）。

覆盖：
  ① ``FetchResult.summary`` 仅在非 ok 时携带 ``quality_status``；
  ② ``run_task``：采集层 stale 时不再写成功标记，返回 partial；
  ③ 正常结果仍写成功标记；
  ④ 日更与启动补跑都包含 valuation（PE/PB 每日刷新）。
"""
from __future__ import annotations

import asyncio
from datetime import date

from app import catch_up, pipeline
from app.ingest.base import FetchResult


class _FakeDB:
    async def commit(self) -> None:
        return None


def test_fetch_result_summary_hides_ok_status() -> None:
    assert "quality_status" not in FetchResult(rows=1).summary()
    payload = FetchResult(rows=1, quality_status="stale").summary()
    assert payload["quality_status"] == "stale"


def test_run_task_keeps_stale_and_skips_success_mark(monkeypatch) -> None:
    async def fake_dispatch(task, **kwargs):
        return FetchResult(rows=6, total=7, failed=1, quality_status="stale")

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(
        pipeline.run_task("index", trade_date=date(2026, 11, 2), db=_FakeDB())
    )
    assert out["status"] == "partial"
    assert out["quality_status"] == "stale"
    assert written == []


def test_run_task_writes_success_for_ok(monkeypatch) -> None:
    async def fake_dispatch(task, **kwargs):
        return FetchResult(rows=7, total=7)

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(
        pipeline.run_task("index", trade_date=date(2026, 11, 2), db=_FakeDB())
    )
    assert out["status"] == "success"
    assert "quality_status" not in out
    assert written and written[0]["rows"] == 7


def test_daily_pipeline_and_catch_up_include_valuation() -> None:
    assert "valuation" in pipeline.DEFAULT_DAILY_STEPS
    assert "valuation" in catch_up.DAY_STEPS
    assert (
        pipeline.DEFAULT_DAILY_STEPS.index("valuation")
        < pipeline.DEFAULT_DAILY_STEPS.index("scores")
    )
    assert (
        catch_up.DAY_STEPS.index("valuation")
        < catch_up.DAY_STEPS.index("scores")
    )
