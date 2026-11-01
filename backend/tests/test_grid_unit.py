"""参数敏感性矩阵纯逻辑单测（不需要数据库）。"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.core.errors import InvalidParamError
from app.strategy import grid as grid_module
from app.strategy.grid import (
    MAX_GRID_CELLS, _cell_snapshot, _params_match, normalize_axes,
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
