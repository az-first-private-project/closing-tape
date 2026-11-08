"""参数敏感性矩阵纯逻辑单测（不需要数据库）。"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest

from app.core.errors import InvalidParamError, NotFoundError
from app.db.models.strategy import BacktestGrid, BacktestGridCell, BacktestRun
from app.strategy import grid as grid_module
from app.strategy.grid import (
    MAX_GRID_CELLS, _cell_snapshot, _params_match, get_grid, normalize_axes,
    requeue_failed_cells,
)


def test_normalize_axes_dedupes_and_sorts():
    rbs, mps = normalize_axes([40, 20, 40], [20, 5])
    assert rbs == [20, 40]
    assert mps == [5, 20]


@pytest.mark.parametrize("rb,mp", [([0], [5]), ([20], [0]), ([251], [5]), ([20], [101])])
def test_normalize_axes_rejects_out_of_range(rb, mp):
    with pytest.raises(InvalidParamError):
        normalize_axes(rb, mp)


def test_normalize_axes_caps_cells():
    with pytest.raises(InvalidParamError) as exc:
        normalize_axes([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert exc.value.detail["max_cells"] == MAX_GRID_CELLS
    assert "24" in exc.value.message


def test_cell_snapshot_binds_limit_to_positions():
    snapshot = _cell_snapshot("a3", 20, 50, None)
    assert snapshot["rebalance_days"] == 20
    assert snapshot["max_positions"] == 50
    assert snapshot["strategy"]["limit"] >= 50


def test_params_match_ignores_engine_extras():
    expected = _cell_snapshot("a3", 20, 20, {})
    assert _params_match({**expected, "calendar_source": "db"}, expected)
    assert not _params_match({**expected, "rebalance_days": 21}, expected)
    assert not _params_match(
        {**expected, "strategy": {**expected["strategy"], "limit": 1}}, expected
    )


def test_execute_grid_binds_limit_to_positions(monkeypatch):
    """执行时与快照同口径：limit 至少抬到该格 max_positions，保证后续复用可比。"""
    seen: dict[str, dict] = {}

    async def fake_run_backtest(*, algo_code, start, end, strategy_params, bt_params, bt_id=None):
        seen[bt_id] = dict(strategy_params)
        return {"bt_id": bt_id}

    monkeypatch.setattr(grid_module, "run_backtest", fake_run_backtest)
    asyncio.run(grid_module.execute_grid(
        algo_code="a3",
        start=date(2025, 1, 1),
        end=date(2025, 3, 1),
        strategy_params={"limit": 10, "min_score": 6.0},
        pending=[{"bt_id": "wide", "rebalance_days": 20, "max_positions": 50}],
    ))
    assert seen["wide"]["limit"] == 50
    assert seen["wide"]["min_score"] == 6.0


def test_execute_grid_isolates_single_cell_failure(monkeypatch):
    """单格抛异常不能中断同批其余格（编排层隔离）。"""
    calls: list[str] = []

    async def fake_run_backtest(*, algo_code, start, end, strategy_params, bt_params, bt_id=None):
        calls.append(bt_id)
        if bt_id == "bad-cell":
            raise RuntimeError("boom")
        return {"bt_id": bt_id}

    monkeypatch.setattr(grid_module, "run_backtest", fake_run_backtest)
    pending = [
        {"bt_id": "bad-cell", "rebalance_days": 5, "max_positions": 3},
        {"bt_id": "ok-1", "rebalance_days": 10, "max_positions": 3},
        {"bt_id": "ok-2", "rebalance_days": 15, "max_positions": 3},
    ]
    asyncio.run(grid_module.execute_grid(
        algo_code="a3",
        start=date(2025, 1, 1),
        end=date(2025, 3, 1),
        strategy_params={},
        pending=pending,
    ))
    assert sorted(calls) == ["bad-cell", "ok-1", "ok-2"]


def test_execute_grid_honors_per_cell_strategy_and_dates(monkeypatch):
    """重试格必须用自己的参数快照与起止日，不能被其他格的 limit 抬高。"""
    seen: dict[str, tuple] = {}

    async def fake_run_backtest(*, algo_code, start, end, strategy_params, bt_params, bt_id=None):
        seen[bt_id] = (start, end, dict(strategy_params))

    monkeypatch.setattr(grid_module, "run_backtest", fake_run_backtest)
    asyncio.run(grid_module.execute_grid(
        algo_code="a3",
        start=date(2025, 1, 1),
        end=date(2025, 3, 1),
        strategy_params=None,
        pending=[{
            "bt_id": "retry-cell",
            "rebalance_days": 10,
            "max_positions": 50,
            "start_date": date(2024, 1, 2),
            "end_date": date(2024, 6, 1),
            "strategy": {"limit": 50, "min_score": 7.0},
        }],
    ))
    start, end, strategy = seen["retry-cell"]
    assert start == date(2024, 1, 2)
    assert end == date(2024, 6, 1)
    assert strategy["limit"] == 50
    assert strategy["min_score"] == 7.0


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeGridSession:
    """按 execute 调用顺序返回预置结果（矩阵主表查询 → 关联查询）。"""

    def __init__(self, *results):
        self._results = list(results)
        self.committed = False

    async def execute(self, _stmt):
        return _FakeResult(self._results.pop(0))

    async def commit(self):
        self.committed = True


def _grid_row(algo: str = "a3") -> BacktestGrid:
    return BacktestGrid(
        grid_id="g1",
        algo_code=algo,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 6, 1),
        axes={"rebalance_days": [20, 40], "max_positions": [20, 50]},
        created_at=datetime(2025, 6, 2, 9, 0, 0),
    )


def _run_row(bt_id: str, status: str, rb: int = 20, mp: int = 20,
             strategy: dict | None = None) -> BacktestRun:
    return BacktestRun(
        bt_id=bt_id,
        algo_code="a3",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 6, 1),
        params={
            "rebalance_days": rb,
            "max_positions": mp,
            "strategy": strategy or {"limit": mp, "min_score": 6.0},
        },
        status=status,
        error="boom" if status == "failed" else None,
        metrics={"total_return": 0.1} if status == "success" else None,
    )


def _link(bt_id: str, rb: int = 20, mp: int = 20, reused: bool = False) -> BacktestGridCell:
    return BacktestGridCell(
        grid_id="g1", bt_id=bt_id,
        rebalance_days=rb, max_positions=mp, reused=reused,
    )


def test_requeue_failed_cells_only_resets_failed():
    failed = _run_row("bt-f", "failed", rb=40, mp=50,
                      strategy={"limit": 50, "min_score": 7.0})
    success = _run_row("bt-s", "success")
    session = _FakeGridSession(
        [_grid_row()],
        [(failed, _link("bt-f", rb=40, mp=50)), (success, _link("bt-s"))],
    )

    result = asyncio.run(requeue_failed_cells(session, "g1"))

    assert result["algo"] == "a3"
    assert result["start"] == date(2025, 1, 1)
    assert result["end"] == date(2025, 6, 1)
    assert result["pending"] == [{
        "bt_id": "bt-f",
        "rebalance_days": 40,
        "max_positions": 50,
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 6, 1),
        "strategy": {"limit": 50, "min_score": 7.0},
    }]
    assert failed.status == "queued"
    assert failed.error is None
    assert failed.elapsed_ms is None
    assert failed.metrics is None
    assert success.status == "success"
    assert success.metrics == {"total_return": 0.1}
    assert session.committed is True


def test_requeue_without_failed_does_not_commit():
    success = _run_row("bt-s", "success")
    session = _FakeGridSession([_grid_row()], [(success, _link("bt-s"))])

    result = asyncio.run(requeue_failed_cells(session, "g1"))

    assert result["pending"] == []
    assert success.status == "success"
    assert session.committed is False


def test_requeue_unknown_grid_raises_not_found():
    with pytest.raises(NotFoundError):
        asyncio.run(requeue_failed_cells(_FakeGridSession([]), "missing"))


def test_get_grid_reads_membership_from_association():
    """同一回测出现在两个矩阵时，各自链接都能读到完整格子与复用标记。"""
    success = _run_row("bt-s", "success")
    failed = _run_row("bt-f", "failed", rb=40, mp=50)
    session = _FakeGridSession(
        [_grid_row()],
        [(success, _link("bt-s", reused=True)), (failed, _link("bt-f", rb=40, mp=50))],
    )

    body = asyncio.run(get_grid(session, "g1"))

    assert body["grid_id"] == "g1"
    assert body["algo"] == "a3"
    assert body["axes"] == {"rebalance_days": [20, 40], "max_positions": [20, 50]}
    assert body["progress"] == {"done": 2, "total": 2, "failed": 1}
    by_key = {(c["rebalance_days"], c["max_positions"]): c for c in body["cells"]}
    assert by_key[(20, 20)]["reused"] is True
    assert by_key[(40, 50)]["reused"] is False
    assert by_key[(40, 50)]["error"] == "boom"
    assert by_key[(20, 20)]["metrics"]["total_return"] == 0.1
    assert body["created_at"] == "2025-06-02T09:00:00"


def test_get_grid_unknown_raises_not_found():
    with pytest.raises(NotFoundError):
        asyncio.run(get_grid(_FakeGridSession([]), "missing"))
