"""日更错过自愈测试（不联网、不连库）。

覆盖：
  ① ``plan_catch_up``：无缺口 / 跨日缺口 / 最新日残缺 / 残日合并 / 缺口超上限；
      ``find_incomplete_days``：绝对下限 / 缩水比例 / 空心日 / 基准不被拉低；
  ② ``_execute_catch_up``：区间步骤 → 逐日重算 → signal_perf 的顺序与参数，
     当天缺口走完整 run_daily_pipeline（快照口径），单步失败不中断；
  ③ ``run_catch_up``：无缺口不取锁；锁被占用直接跳过。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app import catch_up as cu

D1 = date(2026, 11, 2)
D2 = date(2026, 11, 3)
D3 = date(2026, 11, 4)


# ============================================================
# ① plan_catch_up
# ============================================================
def test_plan_no_calendar_returns_empty() -> None:
    assert cu.plan_catch_up(
        last_completed=None, latest=D2, open_days=[D1], latest_complete=True
    ) == []


def test_plan_no_data_returns_empty() -> None:
    """全库无行情 → 不自动补（首次回补不是启动补跑的职责）。"""
    assert cu.plan_catch_up(
        last_completed=D2, latest=None, open_days=[D1], latest_complete=True
    ) == []


def test_plan_up_to_date_returns_empty() -> None:
    assert cu.plan_catch_up(
        last_completed=D2, latest=D2, open_days=[], latest_complete=True
    ) == []


def test_plan_gap_days() -> None:
    assert cu.plan_catch_up(
        last_completed=D2, latest=D1, open_days=[D2], latest_complete=True
    ) == [D2]


def test_plan_includes_incomplete_latest() -> None:
    """库里最新一天残缺（流水线中途失败）→ 与之后的缺口一起补。"""
    assert cu.plan_catch_up(
        last_completed=D2, latest=D1, open_days=[D2], latest_complete=False
    ) == [D1, D2]


def test_plan_latest_incomplete_when_up_to_date() -> None:
    assert cu.plan_catch_up(
        last_completed=D2, latest=D2, open_days=[], latest_complete=False
    ) == [D2]


def test_plan_caps_to_most_recent_days() -> None:
    days = [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    assert cu.plan_catch_up(
        last_completed=D2, latest=date(2026, 10, 19), open_days=days,
        latest_complete=True, max_days=2,
    ) == days[-2:]


def test_plan_includes_incomplete_history_days() -> None:
    """被后续完整日盖住的残日也要补：latest 完整不代表更早的日完整。"""
    assert cu.plan_catch_up(
        last_completed=D2, latest=D2, open_days=[], latest_complete=True,
        incomplete_days=[D1],
    ) == [D1]


def test_plan_merges_incomplete_and_open_days() -> None:
    assert cu.plan_catch_up(
        last_completed=D3, latest=D2, open_days=[D3], latest_complete=True,
        incomplete_days=[D1, D3],
    ) == [D1, D3]


# ============================================================
# ①b find_incomplete_days
# ============================================================
def test_find_incomplete_days_flags_absolute_low() -> None:
    assert cu.find_incomplete_days(
        [D1, D2], {D1: 280, D2: 5287}
    ) == [D1]


def test_find_incomplete_days_flags_ratio_drop() -> None:
    assert cu.find_incomplete_days(
        [D1, D2], {D1: 5568, D2: 4000}
    ) == [D2]


def test_find_incomplete_days_flags_missing_day() -> None:
    assert cu.find_incomplete_days(
        [D1, D2, D3], {D1: 5568, D3: 5571}
    ) == [D2]


def test_find_incomplete_days_normal_growth_is_complete() -> None:
    assert cu.find_incomplete_days(
        [D1, D2, D3], {D1: 5510, D2: 5568, D3: 5571}
    ) == []


def test_find_incomplete_days_baseline_not_dragged_down() -> None:
    """截断日不能把基准拉低，否则紧随其后的另一残日会被漏判。"""
    assert cu.find_incomplete_days(
        [D1, D2, D3], {D1: 5568, D2: 4000, D3: 3400}
    ) == [D2, D3]


# ============================================================
# ② _execute_catch_up
# ============================================================
def _patch_pipeline(monkeypatch, *, fail_step: str | None = None,
                    partial_step: str | None = None):
    from app import pipeline

    calls: list[dict] = []

    async def fake_run_task(task, *, trade_date=None, start=None, end=None,
                            codes=None, db=None):
        calls.append({"task": task, "trade_date": trade_date,
                      "start": start, "end": end})
        if task == fail_step:
            return {"task": task, "status": "failed", "error": "boom"}
        if task == partial_step:
            return {"task": task, "status": "partial",
                    "quality_status": "quality_failed", "failed": 1}
        return {"task": task, "status": "success", "rows": 1}

    async def fake_pipeline(day, db=None, steps=None):
        calls.append({"task": "daily_pipeline", "trade_date": day,
                      "steps": list(steps or [])})
        return {"trade_date": day.isoformat(), "steps": {}}

    monkeypatch.setattr(pipeline, "run_task", fake_run_task)
    monkeypatch.setattr(pipeline, "run_daily_pipeline", fake_pipeline)
    return calls


def test_execute_history_day_order_and_params(monkeypatch) -> None:
    calls = _patch_pipeline(monkeypatch)

    summary = asyncio.run(
        cu._execute_catch_up([D1], object(), today=D2, snapshot_ok=True)
    )

    assert [c["task"] for c in calls] == [
        *cu.RANGE_STEPS, *cu.DAY_STEPS, "signal_perf",
    ]
    for call in calls[: len(cu.RANGE_STEPS)]:
        assert call["start"] == D1 and call["end"] == D1
    for call in calls[len(cu.RANGE_STEPS): len(cu.RANGE_STEPS) + len(cu.DAY_STEPS)]:
        assert call["trade_date"] == D1
    assert calls[-1]["trade_date"] == D1
    assert summary["status"] == "success"
    assert summary["days"] == [D1.isoformat()]


def test_history_day_steps_backfill_margin_after_same_day_outputs() -> None:
    """两融 T+1 披露：漏跑日必须补采，但不得参与当日评分/日报（未来函数）。"""
    assert "margin" in cu.DAY_STEPS
    assert cu.DAY_STEPS.index("margin") > cu.DAY_STEPS.index("sector_scores")
    assert cu.DAY_STEPS.index("margin") > cu.DAY_STEPS.index("report")


def test_execute_today_uses_snapshot_pipeline(monkeypatch) -> None:
    """缺口日就是今天且已收盘 → 快照口径的完整流水线，不逐日跑历史步骤。"""
    calls = _patch_pipeline(monkeypatch)

    summary = asyncio.run(
        cu._execute_catch_up([D2], object(), today=D2, snapshot_ok=True)
    )
    tasks = [c["task"] for c in calls]
    assert tasks == [*cu.RANGE_STEPS, "daily_pipeline", "signal_perf"]
    assert summary["status"] == "success"


def test_execute_today_before_close_uses_history(monkeypatch) -> None:
    """today 但尚未收盘（snapshot_ok=False）→ 仍按历史口径，不用半截快照。"""
    calls = _patch_pipeline(monkeypatch)

    asyncio.run(cu._execute_catch_up([D2], object(), today=D2, snapshot_ok=False))
    assert "daily_pipeline" not in [c["task"] for c in calls]


def test_execute_failure_does_not_abort(monkeypatch) -> None:
    calls = _patch_pipeline(monkeypatch, fail_step="sector")

    summary = asyncio.run(
        cu._execute_catch_up([D1], object(), today=D2, snapshot_ok=True)
    )
    assert summary["status"] == "partial"
    assert f"{D1}:sector" in summary["failed"]
    # 失败之后后续步骤仍然执行
    assert calls[-1]["task"] == "signal_perf"


def test_execute_range_partial_is_not_reported_as_success(monkeypatch) -> None:
    """区间回补有代码失败（partial）时，补跑摘要不能写 success。"""
    _patch_pipeline(monkeypatch, partial_step="history_fast")

    summary = asyncio.run(
        cu._execute_catch_up([D1], object(), today=D2, snapshot_ok=True)
    )

    assert summary["status"] == "partial"
    assert "history_fast" in summary["partial"]
    assert summary["failed"] == []


# ============================================================
# ③ run_catch_up 取锁行为
# ============================================================
def test_run_catch_up_up_to_date_skips_lock(monkeypatch) -> None:
    state = {"last_completed": D2, "latest": D2,
             "open_days": [], "latest_complete": True}

    async def fake_state(db):
        return state

    locks: list[str] = []

    async def fake_lock(db, key):
        locks.append(key)
        return True

    monkeypatch.setattr(cu, "_catch_up_state", fake_state)
    monkeypatch.setattr("app.pipeline.try_advisory_lock", fake_lock)

    summary = asyncio.run(cu.run_catch_up(db=object()))
    assert summary["status"] == "up_to_date"
    assert locks == []


def test_run_catch_up_lock_busy(monkeypatch) -> None:
    state = {"last_completed": D2, "latest": D1,
             "open_days": [D2], "latest_complete": True}

    async def fake_state(db):
        return state

    async def fake_lock(db, key):
        return False

    executed: list = []

    async def fake_execute(*args, **kwargs):
        executed.append(True)
        return {"status": "success"}

    monkeypatch.setattr(cu, "_catch_up_state", fake_state)
    monkeypatch.setattr("app.pipeline.try_advisory_lock", fake_lock)
    monkeypatch.setattr(cu, "_execute_catch_up", fake_execute)

    summary = asyncio.run(cu.run_catch_up(db=object()))
    assert summary["status"] == "skipped"
    assert summary["reason"] == "lock_busy"
    assert executed == []
