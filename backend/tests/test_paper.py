"""纸面组合纯逻辑测试（V6 T11，不需要数据库）。

覆盖：
  - 待执行清单与回测成交共用同一涨跌停/停牌判定；
  - 快照持仓字段与现金比；
  - 单组合失败隔离、非交易日跳过。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import pandas as pd
import pytest

from app.engine import paper as paper_module
from app.engine.paper import (
    _incremental_compatible,
    _same_params,
    _select_incremental,
    _series_metrics,
    _state_valid,
)
from app.strategy.backtest import (
    BacktestParams,
    Bar,
    build_pending_orders,
    phase_signal_dates,
    simulate_portfolio,
)
from app.strategy.metrics import compute_metrics
from app.worker import run_paper_job


def _bar(
    open_price: float | None,
    close_price: float | None,
    *,
    limit_up: bool = False,
    limit_down: bool = False,
    suspended: bool = False,
) -> Bar:
    return Bar(
        open=open_price,
        close=close_price,
        adj_factor=1.0,
        is_limit_up=limit_up,
        is_limit_down=limit_down,
        is_suspended=suspended,
    )


def _params(max_positions: int = 4) -> BacktestParams:
    params = BacktestParams(max_positions=max_positions, initial_cash=1_000_000.0)
    params.validate()
    return params


# =====================================================================
# 待执行清单：与回测同一成交规则
# =====================================================================

def test_pending_orders_block_reasons_match_trade_rules() -> None:
    holdings = [
        {"code": "000001", "name": "停牌股"},
        {"code": "000003", "name": "跌停股"},
    ]
    targets = [
        {"code": "000004", "name": "涨停股"},
        {"code": "000005", "name": "正常股"},
    ]
    bars = {
        "000001": _bar(None, 9.0, suspended=True),
        "000003": _bar(10.0, 10.0, limit_down=True),
        "000004": _bar(11.0, 11.0, limit_up=True),
        "000005": _bar(10.0, 10.2),
    }
    orders = build_pending_orders(
        holdings, targets, _params(), next_bar=lambda code: bars.get(code),
    )

    by_code = {item["code"]: item for item in orders}
    assert [item["side"] for item in orders] == ["sell", "sell", "buy", "buy"]
    assert by_code["000001"]["fillable"] is False
    assert by_code["000001"]["block_reason"] == "停牌"
    assert by_code["000003"]["fillable"] is False
    assert by_code["000003"]["block_reason"] == "一字跌停"
    assert by_code["000004"]["fillable"] is False
    assert by_code["000004"]["block_reason"] == "一字涨停"
    assert by_code["000005"]["fillable"] is True
    assert by_code["000005"]["block_reason"] is None
    assert all(item["reason"] == "T+1 开盘" for item in orders)


def test_pending_orders_unverified_without_next_bar() -> None:
    orders = build_pending_orders(
        [{"code": "000001", "name": "持仓"}],
        [{"code": "000002", "name": "目标"}],
        _params(max_positions=2),
    )
    assert {item["fillable"] for item in orders} == {None}
    assert all(item["block_reason"] is None for item in orders)


def test_pending_orders_rebalance_frees_slot() -> None:
    """卖出释放的名额要算进买入上限，否则换仓日只显示卖单不显示买单。"""
    holdings = [{"code": "000001", "name": "旧持仓"}]
    targets = [{"code": "000002", "name": "新目标"}]
    orders = build_pending_orders(holdings, targets, _params(max_positions=1))
    assert [(item["side"], item["code"]) for item in orders] == [
        ("sell", "000001"), ("buy", "000002"),
    ]


def test_pending_orders_blocked_sell_keeps_slot() -> None:
    """已知卖不出（跌停）时该持仓仍占名额，不能再计划买入。"""
    holdings = [{"code": "000001", "name": "跌停股"}]
    targets = [{"code": "000002", "name": "新目标"}]
    bars = {
        "000001": _bar(10.0, 10.0, limit_down=True),
        "000002": _bar(10.0, 10.2),
    }
    orders = build_pending_orders(
        holdings, targets, _params(max_positions=1), next_bar=lambda code: bars.get(code),
    )
    assert [(item["side"], item["code"]) for item in orders] == [("sell", "000001")]
    assert orders[0]["fillable"] is False
    assert orders[0]["block_reason"] == "一字跌停"


def test_pending_orders_skip_when_all_held() -> None:
    holdings = [{"code": "000001", "name": "已持有"}]
    targets = [{"code": "000001", "name": "仍是目标"}]
    assert build_pending_orders(holdings, targets, _params(max_positions=1)) == []


def test_pending_orders_truncate_to_max_positions() -> None:
    holdings = [{"code": "000001", "name": "旧持仓"}]
    targets = [
        {"code": "000002", "name": "目标2"},
        {"code": "000003", "name": "目标3"},
        {"code": "000004", "name": "目标4"},
    ]
    orders = build_pending_orders(holdings, targets, _params(max_positions=2))
    assert [(item["side"], item["code"]) for item in orders] == [
        ("sell", "000001"), ("buy", "000002"), ("buy", "000003"),
    ]


def test_snapshot_pending_matches_simulated_limit_up_reason() -> None:
    """回测里 T+1 一字涨停买不进，快照的待执行单必须同样标记不可成交。"""
    day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
    price_book = {
        "000001": {
            day1: _bar(9.0, 9.5),
            day2: _bar(11.0, 11.0, limit_up=True),
        }
    }
    snapshots: list[dict] = []
    curve, trades, _ = simulate_portfolio(
        [day1, day2],
        {day2: [{"code": "000001", "name": "涨停股"}]},
        price_book,
        {},
        _params(max_positions=1),
        snapshots_out=snapshots,
    )

    assert len(snapshots) == len(curve) == 2
    pending = snapshots[0]["pending_orders"]
    assert len(pending) == 1
    assert pending[0]["side"] == "buy"
    assert pending[0]["fillable"] is False
    assert pending[0]["block_reason"] == "一字涨停"
    assert pending[0]["reason"] == "T+1 开盘"

    trade = next(item for item in trades if item["side"] == "buy")
    assert trade["reason"] == "limit_up"
    assert trade["shares"] == 0


def _weekdays(count: int, start: date = date(2026, 1, 5)) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _synthetic_book(days: list[date]) -> dict[str, dict[date, Bar]]:
    codes = ["000001", "000002", "000003"]
    return {
        code: {
            day: _bar(10.0 + 0.5 * offset + 0.03 * index, 10.2 + 0.5 * offset + 0.05 * index)
            for index, day in enumerate(days)
        }
        for offset, code in enumerate(codes)
    }


def _synthetic_executions(days: list[date], rebalance_days: int = 5) -> dict[date, list[dict]]:
    codes = ["000001", "000002", "000003"]
    signal_days = phase_signal_dates(
        days, rebalance_days, signal_from=days[0], end=days[-1],
    )
    executions: dict[date, list[dict]] = {}
    for signal_day in signal_days:
        index = days.index(signal_day)
        targets = [
            {"code": codes[(index // rebalance_days + shift) % len(codes)], "name": "测试"}
            for shift in range(2)
        ]
        executions[days[index + 1]] = targets
    return executions


# =====================================================================
# 检查点续算：必须与全量重算逐日一致
# =====================================================================

def test_phase_signal_dates_keep_absolute_phase() -> None:
    days = _weekdays(60)
    assert phase_signal_dates(days, 20, signal_from=days[20], end=days[-1]) == [
        days[20], days[40],
    ]
    # 检查点落在两个信号日之间时，首个信号仍是绝对相位的 40
    assert phase_signal_dates(days, 20, signal_from=days[25], end=days[-1]) == [days[40]]
    # 结束日即使是信号日也没有 T+1，不生成信号
    assert phase_signal_dates(days, 20, signal_from=days[0], end=days[40]) == [days[0], days[20]]


@pytest.mark.parametrize("checkpoint_index", [25, 26, 30])
def test_resumed_simulation_matches_full_run(checkpoint_index: int) -> None:
    days = _weekdays(44)
    price_book = _synthetic_book(days)
    benchmark = {day: 3000.0 + 2.0 * index for index, day in enumerate(days)}
    executions = _synthetic_executions(days)
    params = _params(max_positions=2)
    checkpoint_day = days[checkpoint_index]

    full_snapshots: list[dict] = []
    state: dict = {}
    full_curve, _, _ = simulate_portfolio(
        days, executions, price_book, benchmark, params,
        snapshots_out=full_snapshots,
        capture_state_on=checkpoint_day,
        state_out=state,
    )
    assert state["date"] == checkpoint_day.isoformat()
    assert _state_valid(state)

    window_days = days[checkpoint_index + 1:]
    window_executions = {
        day: targets for day, targets in executions.items()
        if day >= window_days[0]
    }
    window_snapshots: list[dict] = []
    window_curve, _, _ = simulate_portfolio(
        window_days, window_executions, price_book, benchmark, params,
        snapshots_out=window_snapshots,
        initial_state=state,
    )

    # 净值、基准、持仓、现金比、待执行清单全部逐日相等
    assert window_curve == full_curve[checkpoint_index + 1:]
    assert window_snapshots == full_snapshots[checkpoint_index + 1:]


def test_resumed_simulation_without_state_starts_from_initial_cash() -> None:
    days = _weekdays(10)
    price_book = _synthetic_book(days)
    executions = _synthetic_executions(days, rebalance_days=5)
    params = _params(max_positions=2)
    snapshots: list[dict] = []
    curve, _, _ = simulate_portfolio(
        days, executions, price_book, {}, params, snapshots_out=snapshots,
    )
    assert curve[0]["nav"] == 1.0
    assert snapshots[0]["cash_ratio"] == 1.0


# =====================================================================
# 检查点/全量指标与参数兼容性
# =====================================================================

def test_series_metrics_matches_compute_metrics() -> None:
    navs = [1.0, 1.08, 0.96, 1.12, 1.05]
    benchmarks = [1.0, 1.02, 0.99, 1.04, 1.06]
    points = list(zip(navs, benchmarks))
    expected = compute_metrics(
        pd.Series(navs, dtype="float64"),
        pd.Series(benchmarks, dtype="float64"),
        [],
        1,
        1_000_000.0,
    )
    metrics = _series_metrics(points)
    for key in ("total_return", "benchmark_return", "excess_return", "max_drawdown"):
        assert metrics[key] == expected[key]


def test_incremental_compatibility_requires_same_params() -> None:
    bt = _params(max_positions=4)
    strategy = {"limit": 4, "k_max": 30.0}
    compatible = {
        "strategy": {"limit": 4.0, "k_max": 30},
        "backtest": bt.as_snapshot(),
    }
    assert _incremental_compatible(compatible, strategy, bt)
    assert not _incremental_compatible({}, strategy, bt)
    assert not _incremental_compatible(
        {**compatible, "strategy": {"limit": 3}}, strategy, bt,
    )
    assert not _incremental_compatible(
        {**compatible, "backtest": {**bt.as_snapshot(), "rebalance_days": 30}}, strategy, bt,
    )
    assert not _state_valid({"date": "2026-01-05", "positions": {}})
    assert _state_valid({"date": "2026-01-05", "cash": 0.0, "positions": {}})


def test_select_incremental_requires_valid_week_and_checkpoint() -> None:
    days = _weekdays(30)
    end = days[-1]
    checkpoint_index = len(days) - 5
    checkpoint_day = days[checkpoint_index]
    bt = _params(max_positions=2)
    strategy = {"limit": 2}
    state = {"date": checkpoint_day.isoformat(), "cash": 1000.0, "positions": {}}
    params = {
        "strategy": strategy,
        "backtest": bt.as_snapshot(),
        "incremental": {
            "checkpoint": {"date": checkpoint_day.isoformat(), "state": state},
            "calibrated_week": paper_module._week_key(end),
            "strategy": dict(strategy),
            "backtest": bt.as_snapshot(),
        },
    }

    use, selected_date, index, selected_state = _select_incremental(
        days, end=end, params=params, strategy_params=strategy, bt=bt, force_full=False,
    )
    assert use and selected_date == checkpoint_day and index == checkpoint_index
    assert selected_state == state

    def _reject(patched: dict, **kwargs) -> None:
        use, *_ = _select_incremental(
            days, end=end, params=patched, strategy_params=strategy, bt=bt,
            force_full=kwargs.get("force_full", False),
        )
        assert not use

    _reject(params, force_full=True)

    stale_week = {
        **params,
        "incremental": {**params["incremental"], "calibrated_week": "1999-W01"},
    }
    _reject(stale_week)

    changed_params = {
        **params,
        "incremental": {**params["incremental"], "strategy": {"limit": 3}},
    }
    _reject(changed_params)

    bad_state = {
        **params,
        "incremental": {
            **params["incremental"],
            "checkpoint": {"date": days[5].isoformat(), "state": {"date": days[5].isoformat()}},
        },
    }
    _reject(bad_state)

    weekend_checkpoint = {
        **params,
        "incremental": {
            **params["incremental"],
            "checkpoint": {"date": "2026-01-10", "state": state},
        },
    }
    _reject(weekend_checkpoint)

    for short_index in (len(days) - 1, len(days) - 2):
        short = {
            **params,
            "incremental": {
                **params["incremental"],
                "checkpoint": {
                    "date": days[short_index].isoformat(),
                    "state": {**state, "date": days[short_index].isoformat()},
                },
            },
        }
        _reject(short)


def test_snapshots_capture_holdings_cost_and_cash() -> None:
    day1, day2, day3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    price_book = {
        "000001": {
            day1: _bar(9.0, 9.5),
            day2: _bar(10.0, 10.5),
            day3: _bar(10.5, 11.0),
        }
    }
    snapshots: list[dict] = []
    simulate_portfolio(
        [day1, day2, day3],
        {day2: [{"code": "000001", "name": "测试股"}]},
        price_book,
        {},
        _params(max_positions=1),
        snapshots_out=snapshots,
    )

    first, second, third = snapshots
    assert first["holdings"] == []
    assert first["cash_ratio"] == pytest.approx(1.0)
    assert first["pending_orders"][0]["code"] == "000001"

    holding = second["holdings"][0]
    assert holding["code"] == "000001"
    assert holding["shares"] % 100 == 0
    assert holding["cost"] == pytest.approx(10.011)  # 含佣金
    assert holding["weight"] > 0.98
    assert second["cash_ratio"] < 0.02
    assert second["pending_orders"] == []
    assert third["holdings"][0]["pnl_pct"] > 0


# =====================================================================
# 编排：失败隔离与非交易日跳过
# =====================================================================

class _DummySession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def test_sync_all_isolates_single_failure(monkeypatch) -> None:
    calls: list[str] = []
    failed_marks: list[str] = []

    async def fake_sync(db, algo_code):
        calls.append(algo_code)
        if algo_code == "b3":
            raise RuntimeError("boom")
        return {"algo": algo_code, "status": "active"}

    async def fake_mark_failed(db, algo_code, error):
        failed_marks.append(algo_code)

    monkeypatch.setattr(paper_module, "enabled_algos", lambda: ["a3", "b3"])
    monkeypatch.setattr(paper_module, "sync_paper_portfolio", fake_sync)
    monkeypatch.setattr(paper_module, "_mark_failed", fake_mark_failed)
    db = _DummySession()

    results = asyncio.run(paper_module.sync_all_paper_portfolios(db))

    assert calls == ["a3", "b3"]
    assert [item["status"] for item in results] == ["active", "failed"]
    assert failed_marks == ["b3"]
    assert db.rollbacks == 1
    assert db.commits == 2  # a3 成功 + b3 失败状态落库


def test_run_paper_job_skips_non_trade_day(monkeypatch, caplog) -> None:
    called: list[int] = []

    async def fake_is_trade_day(db, day):
        return False

    async def fake_sync_all(db):
        called.append(1)
        return []

    monkeypatch.setattr("app.ingest.calendar.is_trade_day", fake_is_trade_day)
    monkeypatch.setattr(paper_module, "sync_all_paper_portfolios", fake_sync_all)
    caplog.set_level(logging.INFO, logger="worker")

    asyncio.run(run_paper_job(None))

    assert called == []
    assert any("非交易日" in record.getMessage() for record in caplog.records)


def test_enabled_algos_env_override_and_fallback(monkeypatch) -> None:
    monkeypatch.setattr(paper_module, "PAPER_ENABLED_ALGOS", ("b1", "nope"))
    assert paper_module.enabled_algos() == ["b1"]
    assert paper_module.is_enabled("b1")
    assert not paper_module.is_enabled("a3")

    monkeypatch.setattr(paper_module, "PAPER_ENABLED_ALGOS", ())
    assert paper_module.enabled_algos() == sorted(paper_module.SPECS)


# =====================================================================
# vs_backtest 只与同参数的回测比较
# =====================================================================

def test_same_params_requires_identical_snapshot() -> None:
    bt = _params(max_positions=4)
    strategy = {"limit": 4, "k_max": 30.0}
    row = {**bt.as_snapshot(), "strategy": {"limit": 4.0, "k_max": 30}}
    assert _same_params(bt.as_snapshot(), strategy, row)

    assert not _same_params(
        bt.as_snapshot(), strategy, {**row, "rebalance_days": 30},
    )
    assert not _same_params(
        bt.as_snapshot(), strategy, {**row, "strategy": {"limit": 4, "k_max": 20.0}},
    )
    assert not _same_params(bt.as_snapshot(), strategy, None)
    assert not _same_params(bt.as_snapshot(), strategy, {"strategy": strategy})
