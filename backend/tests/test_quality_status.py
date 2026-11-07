"""质量状态传递测试（不联网、不连库）。

覆盖：
  ① ``FetchResult.summary`` 仅在非 ok 时携带 ``quality_status``；
  ② ``run_task``：采集层 stale 时不再写成功标记，返回 partial；
  ③ 正常结果仍写成功标记；
  ④ 定向 codes 回补只覆盖部分代码，不写数据集新鲜度（成功/失败都不写）；
  ⑤ 日更与启动补跑都包含 valuation（PE/PB 每日刷新）。
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


def test_run_task_skip_does_not_write_success(monkeypatch) -> None:
    """跳过（未到收盘/限频/锁占用）不是成功：不得把数据集写成 0 行 fresh。"""

    async def fake_dispatch(task, **kwargs):
        return FetchResult(
            rows=0, total=0, detail={"skipped": "before_close_ready_time"}
        )

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(
        pipeline.run_task("snapshot", trade_date=date(2026, 11, 6), db=_FakeDB())
    )
    assert out["status"] == "skipped"
    assert written == []


def test_run_task_already_collected_marks_existing_rows(monkeypatch) -> None:
    """已有数据跳过：数据本身完整，按存量行数与覆盖率记成功，不能记 0 行。"""

    async def fake_dispatch(task, **kwargs):
        return FetchResult(
            rows=0, total=0,
            detail={
                "skipped": "already_collected",
                "existing": 5031,
                "coverage": 0.9,
            },
        )

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(
        pipeline.run_task("snapshot", trade_date=date(2026, 11, 6), db=_FakeDB())
    )
    assert out["status"] == "skipped"
    assert written and written[0]["rows"] == 5031
    assert written[0]["coverage"] == 0.9


def test_run_task_dict_skip_does_not_write_success(monkeypatch) -> None:
    """引擎层用顶层 skipped 标记跳过（如日历为空），同样不能写成功。"""

    async def fake_dispatch(task, **kwargs):
        return {"rows": 0, "skipped": True, "reason": "交易日历为空"}

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(pipeline.run_task("signal_perf", db=_FakeDB()))
    assert out["status"] == "skipped"
    assert written == []


def test_run_task_scoped_backfill_skips_success_mark(monkeypatch) -> None:
    """定向回补的行数只是局部代码，不能覆盖全市场数据集的新鲜度。"""

    async def fake_dispatch(task, **kwargs):
        assert kwargs["codes"] == "920000"
        return FetchResult(rows=355, total=355)

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(
        pipeline.run_task(
            "history_fast", codes="920000",
            start=date(2026, 11, 3), end=date(2026, 11, 3), db=_FakeDB(),
        )
    )
    assert out["status"] == "success"
    assert written == []


def test_run_task_scoped_backfill_failure_keeps_freshness(monkeypatch) -> None:
    """定向回补失败不应把全市场数据集标成 error。"""

    async def fake_dispatch(task, **kwargs):
        raise RuntimeError("boom")

    recorded: list[str] = []

    async def fake_mark_failure(db, dataset, error, **kwargs) -> None:
        recorded.append(error)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_failure", fake_mark_failure)

    db = _BrokenDB()
    out = asyncio.run(pipeline.run_task("valuation_fast", codes="000001", db=db))
    assert out["status"] == "failed"
    assert db.rolled_back is True
    assert recorded == []


def test_run_task_full_history_fast_still_marks_success(monkeypatch) -> None:
    """全市场任务（codes 为空）仍要写新鲜度，守卫不能误伤。"""

    async def fake_dispatch(task, **kwargs):
        return FetchResult(rows=5590)

    written: list[dict] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        written.append(kwargs)

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)

    out = asyncio.run(pipeline.run_task("history_fast", db=_FakeDB()))
    assert out["status"] == "success"
    assert written and written[0]["rows"] == 5590


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


def test_run_task_bulk_failures_mark_quality_failed(monkeypatch) -> None:
    """全市场回补重试后仍有失败：写 quality_failed，返回 partial，不能洗成成功。"""

    async def fake_dispatch(task, **kwargs):
        return FetchResult(
            rows=5580, total=5582, failed=2,
            quality_status="quality_failed",
            detail={"failed_codes": ["000001", "600000"]},
        )

    successes: list[dict] = []
    failures: list[tuple[str, str, dict]] = []

    async def fake_mark_success(*args, **kwargs) -> None:
        successes.append(kwargs)

    async def fake_mark_failure(db, dataset, error, **kwargs) -> None:
        failures.append((dataset, error, kwargs))

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_success", fake_mark_success)
    monkeypatch.setattr(pipeline, "mark_failure", fake_mark_failure)

    out = asyncio.run(pipeline.run_task("history_fast", db=_FakeDB()))

    assert out["status"] == "partial"
    assert out["failed"] == 2
    assert successes == []
    assert failures and failures[0][0] == "stock_daily"
    assert failures[0][2]["status"] == "quality_failed"
    assert "2" in failures[0][1]
    assert "000001" in failures[0][1]


def test_run_task_scoped_bulk_partial_keeps_freshness(monkeypatch) -> None:
    """定向回补部分失败同样不得改写全市场数据集的新鲜度。"""

    async def fake_dispatch(task, **kwargs):
        return FetchResult(
            rows=1, total=2, failed=1,
            quality_status="quality_failed",
            detail={"failed_codes": ["600000"]},
        )

    failures: list[tuple[str, str, dict]] = []

    async def fake_mark_failure(db, dataset, error, **kwargs) -> None:
        failures.append((dataset, error, kwargs))

    monkeypatch.setattr(pipeline, "_dispatch", fake_dispatch)
    monkeypatch.setattr(pipeline, "mark_failure", fake_mark_failure)

    out = asyncio.run(
        pipeline.run_task("history_fast", codes="000001", db=_FakeDB())
    )

    assert out["status"] == "partial"
    assert failures == []
