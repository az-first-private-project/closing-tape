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


class _BrokenDB(_FakeDB):
    """模拟连接断开后的共享会话：失败路径必须回滚，否则后续步骤全部复用死连接。"""

    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


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


def test_run_task_rolls_back_on_failure(monkeypatch) -> None:
    """步骤异常后必须回滚共享会话，否则后续步骤会被同一条死连接带崩。"""

    async def fake_dispatch(task, **kwargs):
        raise RuntimeError("connection is closed")

    recorded: list[str] = []

    async def fake_mark_failure(db, dataset, error, **kwargs) -> None:
        recorded.append(error)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_failure", fake_mark_failure)

    db = _BrokenDB()
    out = asyncio.run(
        pipeline.run_task("features", trade_date=date(2026, 11, 3), db=db)
    )
    assert out["status"] == "failed"
    assert db.rolled_back is True
    assert recorded and "connection is closed" in recorded[0]


def test_run_task_failure_survives_broken_metadata_write(monkeypatch) -> None:
    """DB 已不可用时，写失败状态本身再失败也只能记日志，不能改变返回结果。"""

    async def fake_dispatch(task, **kwargs):
        raise RuntimeError("boom")

    async def fake_mark_failure(*args, **kwargs) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_failure", fake_mark_failure)

    db = _BrokenDB()
    out = asyncio.run(
        pipeline.run_task("features", trade_date=date(2026, 11, 3), db=db)
    )
    assert out["status"] == "failed"
    assert db.rolled_back is True


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
