"""参数敏感性矩阵（V6 T10）
============================
一次矩阵 = 一个 ``grid_id``，每格 = 一条独立 ``backtest_run``（复用既有回测引擎）。
本模块只负责：轴校验、格数上限、同参数断点复用、后台逐格执行与矩阵汇总。

并发与事务：
  - 全局信号量上限 2，逐格独立会话/事务；单格失败只标记该格 failed；
  - 复用判定只看 ``status='success'`` 且 (algo, start, end, 参数) 完全一致，
    命中后只在 ``backtest_grid_cell`` 新增关联，不重算、不改写回测行；
  - 矩阵身份在 ``backtest_grid`` 主表，同一回测可同时属于多个矩阵。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParamError, NotFoundError
from app.db.models.strategy import BacktestGrid, BacktestGridCell, BacktestRun
from app.strategy import strategies as strat
from app.strategy.backtest import BacktestParams, run_backtest

logger = logging.getLogger(__name__)

#: 单次矩阵格数硬上限（回测单格可达分钟级，防止拖垮连接池）
MAX_GRID_CELLS = 24

#: 后台执行并发上限
GRID_CONCURRENCY = 2

_GRID_SEMAPHORE: asyncio.Semaphore | None = None
_GRID_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None


def _grid_semaphore() -> asyncio.Semaphore:
    """信号量按事件循环惰性绑定（测试/多进程环境下每个循环各一份）。"""
    global _GRID_SEMAPHORE, _GRID_SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _GRID_SEMAPHORE is None or _GRID_SEMAPHORE_LOOP is not loop:
        _GRID_SEMAPHORE = asyncio.Semaphore(GRID_CONCURRENCY)
        _GRID_SEMAPHORE_LOOP = loop
    return _GRID_SEMAPHORE

_METRIC_KEYS = (
    "total_return", "excess_return", "max_drawdown", "sharpe", "win_rate", "trade_count",
)


def _unique_axis(values: list[int], *, name: str, minimum: int, maximum: int) -> list[int]:
    result: list[int] = []
    for raw in values:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise InvalidParamError(f"{name} 必须是整数，收到: {raw!r}") from None
        if value < minimum or value > maximum:
            raise InvalidParamError(
                f"{name} 超出范围 [{minimum}, {maximum}]，收到: {value}",
                detail={"axis": name, "value": value},
            )
        if value not in result:
            result.append(value)
    if not result:
        raise InvalidParamError(f"{name} 至少需要一个取值")
    return sorted(result)


def normalize_axes(
    rebalance_days: list[int], max_positions: list[int]
) -> tuple[list[int], list[int]]:
    """去重、排序并校验两轴；格数超过 24 直接拒绝。"""
    rbs = _unique_axis(rebalance_days, name="rebalance_days", minimum=1, maximum=250)
    mps = _unique_axis(max_positions, name="max_positions", minimum=1, maximum=100)
    if len(rbs) * len(mps) > MAX_GRID_CELLS:
        raise InvalidParamError(
            f"矩阵格数 {len(rbs)}×{len(mps)} 超过上限 {MAX_GRID_CELLS}；"
            "回测单格可达分钟级，请减少轴取值",
            detail={"cells": len(rbs) * len(mps), "max_cells": MAX_GRID_CELLS},
        )
    return rbs, mps


def _cell_snapshot(
    algo_code: str, rebalance_days: int, max_positions: int, strategy_params: dict | None
) -> dict[str, Any]:
    """该格落库的参数快照（与单次回测完全同构，供复用比对）。"""
    bt_params = BacktestParams.from_dict(
        {"rebalance_days": rebalance_days, "max_positions": max_positions}
    )
    normalized = strat._validate_params(algo_code, strategy_params)  # noqa: SLF001
    normalized["limit"] = max(int(normalized["limit"]), int(max_positions))
    return {**bt_params.as_snapshot(), "strategy": dict(normalized)}


def _params_match(existing: dict | None, expected: dict) -> bool:
    current = existing or {}
    for key, value in expected.items():
        if key == "strategy":
            if dict(current.get("strategy") or {}) != dict(value):
                return False
        elif current.get(key) != value:
            return False
    return True


async def _find_reusable(
    db: AsyncSession,
    algo_code: str,
    start: date,
    end: date,
    expected_params: dict,
) -> BacktestRun | None:
    rows = (await db.execute(
        select(BacktestRun)
        .where(BacktestRun.algo_code == algo_code)
        .where(BacktestRun.start_date == start)
        .where(BacktestRun.end_date == end)
        .where(BacktestRun.status == "success")
        .order_by(BacktestRun.created_at.desc())
    )).scalars().all()
    for row in rows:
        if _params_match(row.params, expected_params):
            return row
    return None


async def create_grid(
    db: AsyncSession,
    *,
    algo_code: str,
    start: date,
    end: date,
    rebalance_days: list[int],
    max_positions: list[int],
    strategy_params: dict | None = None,
) -> dict[str, Any]:
    """建立矩阵：命中成功回测直接复用，其余预建 queued 行并返回待执行清单。"""
    rbs, mps = normalize_axes(rebalance_days, max_positions)
    grid_id = str(uuid.uuid4())
    cells: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    links: list[BacktestGridCell] = []
    reused = 0

    db.add(BacktestGrid(
        grid_id=grid_id,
        algo_code=algo_code,
        start_date=start,
        end_date=end,
        axes={"rebalance_days": rbs, "max_positions": mps},
        strategy_params=dict(strategy_params or {}),
    ))

    for rb in rbs:
        for mp in mps:
            expected = _cell_snapshot(algo_code, rb, mp, strategy_params)
            existing = await _find_reusable(db, algo_code, start, end, expected)
            if existing is not None:
                links.append(BacktestGridCell(
                    grid_id=grid_id,
                    bt_id=existing.bt_id,
                    rebalance_days=rb,
                    max_positions=mp,
                    reused=True,
                ))
                reused += 1
                cells.append({
                    "bt_id": existing.bt_id,
                    "params": {"rebalance_days": rb, "max_positions": mp},
                    "rebalance_days": rb,
                    "max_positions": mp,
                    "status": existing.status,
                    "reused": True,
                })
                continue

            bt_id = str(uuid.uuid4())
            db.add(BacktestRun(
                bt_id=bt_id,
                algo_code=algo_code,
                start_date=start,
                end_date=end,
                params={**expected},
                status="queued",
            ))
            links.append(BacktestGridCell(
                grid_id=grid_id,
                bt_id=bt_id,
                rebalance_days=rb,
                max_positions=mp,
                reused=False,
            ))
            pending.append({"bt_id": bt_id, "rebalance_days": rb, "max_positions": mp})
            cells.append({
                "bt_id": bt_id,
                "params": {"rebalance_days": rb, "max_positions": mp},
                "rebalance_days": rb,
                "max_positions": mp,
                "status": "queued",
                "reused": False,
            })

    await db.flush()  # 先落主表与新回测行，再写关联，保证外键可见
    db.add_all(links)
    await db.commit()  # 后台任务在响应后启动，排队行必须已可见
    cells.sort(key=lambda item: (item["rebalance_days"], item["max_positions"]))
    logger.info(
        "参数矩阵已建立 grid=%s algo=%s cells=%d 复用=%d 待执行=%d",
        grid_id, algo_code, len(cells), reused, len(pending),
    )
    return {
        "grid_id": grid_id,
        "axes": {"rebalance_days": rbs, "max_positions": mps},
        "cells": cells,
        "pending": pending,
        "reused": reused,
    }


async def execute_grid(
    *,
    algo_code: str,
    start: date,
    end: date,
    strategy_params: dict | None,
    pending: list[dict[str, Any]],
) -> None:
    """后台逐格执行；单格异常只记录日志（失败状态由回测引擎落库）。

    ``pending`` 每项可带 ``strategy`` / ``start_date`` / ``end_date`` 覆盖整批默认值，
    重试失败格时用它还原该格自己的参数快照，避免 limit 被其他格抬高。
    """
    semaphore = _grid_semaphore()
    # 与 _cell_snapshot 同口径：limit 至少等于该格 max_positions，否则落库参数与复用比对不一致
    base_strategy = strat._validate_params(algo_code, strategy_params or {})  # noqa: SLF001

    async def run_cell(cell: dict[str, Any]) -> None:
        cell_base = (
            strat._validate_params(algo_code, cell["strategy"])  # noqa: SLF001
            if cell.get("strategy") else base_strategy
        )
        cell_strategy = {
            **cell_base,
            "limit": max(int(cell_base["limit"]), int(cell["max_positions"])),
        }
        async with semaphore:
            try:
                await run_backtest(
                    algo_code=algo_code,
                    start=cell.get("start_date") or start,
                    end=cell.get("end_date") or end,
                    strategy_params=cell_strategy,
                    bt_params={
                        "rebalance_days": cell["rebalance_days"],
                        "max_positions": cell["max_positions"],
                    },
                    bt_id=cell["bt_id"],
                )
            except Exception:  # noqa: BLE001 - 单格失败不能中断整张矩阵
                logger.exception(
                    "参数矩阵单格失败 algo=%s bt_id=%s rb=%s mp=%s",
                    algo_code, cell["bt_id"], cell["rebalance_days"], cell["max_positions"],
                )

    if not pending:
        return
    logger.info("参数矩阵开始执行 algo=%s cells=%d", algo_code, len(pending))
    await asyncio.gather(*(run_cell(cell) for cell in pending))
    logger.info("参数矩阵执行结束 algo=%s cells=%d", algo_code, len(pending))


async def requeue_failed_cells(
    db: AsyncSession, grid_id: str
) -> dict[str, Any]:
    """把矩阵内 failed 格重置为 queued 并返回待执行清单（T10 重试）。

    只重置 failed：成功格保持原记录，queued/running 说明已有任务在跑，
    重复调用不会造成同格并发执行。每格带自己的参数快照与起止日，
    避免用其他格的 ``limit`` 覆盖本格。
    """
    grid = (await db.execute(
        select(BacktestGrid).where(BacktestGrid.grid_id == grid_id)
    )).scalar_one_or_none()
    if grid is None:
        raise NotFoundError(f"参数矩阵不存在: {grid_id}", detail={"grid_id": grid_id})

    rows = (await db.execute(
        select(BacktestRun, BacktestGridCell)
        .join(BacktestGridCell, BacktestGridCell.bt_id == BacktestRun.bt_id)
        .where(BacktestGridCell.grid_id == grid_id)
        .with_for_update()
    )).all()

    pending: list[dict[str, Any]] = []
    for row, link in rows:
        if row.status != "failed":
            continue
        row.status = "queued"
        row.error = None
        row.elapsed_ms = None
        row.metrics = None
        row.equity_curve = None
        row.trades = None
        pending.append({
            "bt_id": row.bt_id,
            "rebalance_days": link.rebalance_days,
            "max_positions": link.max_positions,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "strategy": dict((row.params or {}).get("strategy") or {}),
        })

    if pending:
        await db.commit()  # 后台任务在响应后启动，排队状态必须已可见

    return {
        "grid_id": grid_id,
        "algo": grid.algo_code,
        "start": grid.start_date,
        "end": grid.end_date,
        "pending": pending,
    }


async def get_grid(db: AsyncSession, grid_id: str) -> dict[str, Any]:
    """矩阵状态 + 每格指标摘要 + 进度；不重算任何回测。"""
    grid = (await db.execute(
        select(BacktestGrid).where(BacktestGrid.grid_id == grid_id)
    )).scalar_one_or_none()
    if grid is None:
        raise NotFoundError(f"参数矩阵不存在: {grid_id}", detail={"grid_id": grid_id})

    rows = (await db.execute(
        select(BacktestRun, BacktestGridCell)
        .join(BacktestGridCell, BacktestGridCell.bt_id == BacktestRun.bt_id)
        .where(BacktestGridCell.grid_id == grid_id)
    )).all()
    runs = [row for row, _ in rows]

    cells: list[dict[str, Any]] = []
    for row, link in rows:
        metrics = row.metrics if row.status == "success" else None
        summary = {
            key: (metrics or {}).get(key) for key in _METRIC_KEYS
        } if metrics else None
        cells.append({
            "bt_id": row.bt_id,
            "params": {
                "rebalance_days": link.rebalance_days,
                "max_positions": link.max_positions,
            },
            "rebalance_days": link.rebalance_days,
            "max_positions": link.max_positions,
            "status": row.status,
            "reused": bool(link.reused),
            "metrics": summary,
            "elapsed_ms": row.elapsed_ms,
            "error": (row.error or "")[:300] or None,
            "_start_date": row.start_date.isoformat(),
            "_end_date": row.end_date.isoformat(),
        })
    cells.sort(key=lambda item: (item["rebalance_days"], item["max_positions"]))

    start_counter = Counter(item["_start_date"] for item in cells)
    end_counter = Counter(item["_end_date"] for item in cells)
    canonical_start = start_counter.most_common(1)[0][0] if cells else grid.start_date.isoformat()
    canonical_end = end_counter.most_common(1)[0][0] if cells else grid.end_date.isoformat()
    calendar_by_bt = {
        row.bt_id: str((row.params or {}).get("calendar_source") or "db")
        for row in runs if row.status == "success"
    }
    calendar_counter = Counter(calendar_by_bt.values())
    canonical_calendar = calendar_counter.most_common(1)[0][0] if calendar_counter else None

    for cell in cells:
        cell["aligned"] = (
            cell["_start_date"] == canonical_start
            and cell["_end_date"] == canonical_end
            and (
                canonical_calendar is None
                or calendar_by_bt.get(cell["bt_id"], canonical_calendar) == canonical_calendar
            )
        )
        cell.pop("_start_date", None)
        cell.pop("_end_date", None)
    cells.sort(key=lambda item: (item["rebalance_days"], item["max_positions"]))

    total = len(cells)
    failed = sum(1 for item in cells if item["status"] == "failed")
    done = sum(1 for item in cells if item["status"] in ("success", "failed"))
    elapsed = [item["elapsed_ms"] for item in cells if item["elapsed_ms"] is not None]
    axes = grid.axes or {}
    created = grid.created_at

    return {
        "grid_id": grid_id,
        "algo": grid.algo_code,
        "start_date": canonical_start,
        "end_date": canonical_end,
        "axes": {
            "rebalance_days": sorted(
                int(value) for value in axes.get("rebalance_days")
                or {item["rebalance_days"] for item in cells}
            ),
            "max_positions": sorted(
                int(value) for value in axes.get("max_positions")
                or {item["max_positions"] for item in cells}
            ),
        },
        "cells": cells,
        "progress": {"done": done, "total": total, "failed": failed},
        "total_elapsed_ms": sum(elapsed),
        "created_at": created.isoformat() if isinstance(created, datetime) else None,
    }


__all__ = [
    "MAX_GRID_CELLS", "GRID_CONCURRENCY", "normalize_axes", "create_grid",
    "execute_grid", "get_grid", "requeue_failed_cells",
]
