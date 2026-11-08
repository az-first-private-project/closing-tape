"""回测异步任务单测（P2-4，不需要数据库）。

覆盖：
  - POST 只落 queued 记录并登记后台任务，不在请求内执行回测；
  - 数据起点裁剪后的区间传给后台任务；
  - 后台执行失败只记日志，不向事件循环抛异常（失败状态由引擎落库）。
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from fastapi import BackgroundTasks

from app.api.v1 import backtest as backtest_api
from app.core.errors import DataUnavailableError, InvalidParamError


class _Request:
    def __init__(self, query: dict[str, str] | None = None) -> None:
        self.query_params = query or {}


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, row: object) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


class _ScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar(self):
        return self._value


class _ScalarDb:
    """只提供 max(trade_date) 查询结果的假会话。"""

    def __init__(self, value) -> None:
        self._value = value

    async def execute(self, statement):
        return _ScalarResult(self._value)


def _create(db: _FakeDb, background: BackgroundTasks, **overrides):
    kwargs = {
        "algo_code": "a3",
        "request": _Request({"limit": "5", "min_score": "7.5"}),
        "background_tasks": background,
        "start": date(2026, 1, 1),
        "end": date(2026, 6, 1),
        "rebalance_days": 20,
        "max_positions": 5,
        "benchmark": "sh000300",
        "commission": 0.00025,
        "stamp_tax": 0.001,
        "slippage": 0.001,
        "db": db,
    }
    kwargs.update(overrides)
    return asyncio.run(backtest_api.create_backtest(**kwargs))


def test_create_backtest_enqueues_without_running_inline(monkeypatch) -> None:
    async def fake_data_start(algo_code, db):
        return date(2026, 2, 1)

    async def fail_if_called(**kwargs):
        raise AssertionError("POST 不应同步执行回测")

    monkeypatch.setattr(backtest_api, "_data_start", fake_data_start)
    monkeypatch.setattr(backtest_api, "run_backtest", fail_if_called)
    db, background = _FakeDb(), BackgroundTasks()

    result = _create(db, background)

    assert db.commits == 1
    assert len(db.added) == 1
    record = db.added[0]
    assert record.status == "queued"
    assert record.algo_code == "a3"
    assert record.params["rebalance_days"] == 20
    assert record.params["strategy"] == {"limit": 5, "min_score": 7.5}

    assert result["status"] == "queued"
    assert result["bt_id"] == record.bt_id
    assert result["requested_start"] == "2026-01-01"
    assert result["start_date"] == result["effective_start"] == "2026-02-01"
    assert result["start_trimmed"] is True
    assert result["poll"] == f"/api/v1/backtest/{record.bt_id}"

    assert len(background.tasks) == 1
    task = background.tasks[0]
    assert task.func is backtest_api.execute_backtest
    assert task.kwargs["bt_id"] == record.bt_id
    assert task.kwargs["start"] == date(2026, 2, 1)
    assert task.kwargs["strategy_params"] == {"limit": 5, "min_score": 7.5}


def test_create_backtest_rejects_invalid_range_before_enqueue() -> None:
    db, background = _FakeDb(), BackgroundTasks()

    with pytest.raises(InvalidParamError):
        _create(db, background, start=date(2026, 6, 1), end=date(2026, 6, 1))

    assert db.added == []
    assert background.tasks == []


def test_default_end_date_follows_latest_market_data() -> None:
    latest = date(2026, 11, 6)
    assert asyncio.run(backtest_api._default_end_date(_ScalarDb(latest))) == latest

    future = date.today() + timedelta(days=3)
    assert asyncio.run(backtest_api._default_end_date(_ScalarDb(future))) == date.today()

    with pytest.raises(DataUnavailableError):
        asyncio.run(backtest_api._default_end_date(_ScalarDb(None)))


def test_create_backtest_defaults_end_to_latest_trade_date(monkeypatch) -> None:
    async def fake_data_start(algo_code, db):
        return None

    async def fake_default_end(db):
        return date(2026, 11, 6)

    monkeypatch.setattr(backtest_api, "_data_start", fake_data_start)
    monkeypatch.setattr(backtest_api, "_default_end_date", fake_default_end)
    db, background = _FakeDb(), BackgroundTasks()

    result = _create(db, background, start=None, end=None)

    record = db.added[0]
    assert record.end_date == date(2026, 11, 6)
    assert record.start_date == date(2025, 11, 6)
    assert result["end_date"] == "2026-11-06"
    assert result["start_date"] == "2025-11-06"


def test_execute_backtest_passes_through_and_swallows_failure(monkeypatch) -> None:
    calls: dict = {}

    async def fake_run(**kwargs):
        calls.update(kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(backtest_api, "run_backtest", fake_run)

    asyncio.run(backtest_api.execute_backtest(
        bt_id="bt-1",
        algo_code="a3",
        start=date(2026, 1, 1),
        end=date(2026, 6, 1),
        strategy_params={"limit": 5},
        bt_params={"rebalance_days": 20},
    ))

    assert calls["bt_id"] == "bt-1"
    assert calls["algo_code"] == "a3"
    assert calls["start"] == date(2026, 1, 1)
    assert calls["bt_params"] == {"rebalance_days": 20}
    assert "db" not in calls
