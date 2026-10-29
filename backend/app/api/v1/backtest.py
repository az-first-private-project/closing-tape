"""
回测 API（方案 D4/D5）
======================
POST /api/v1/backtest/{algo_code}          发起回测（同步执行，1 年内约 3–10 秒）
GET  /api/v1/backtest/{bt_id}              查询结果
GET  /api/v1/backtest/{bt_id}/export.csv   净值曲线导出
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BACKTEST
from app.core.errors import InvalidParamError, NotFoundError, UnknownAlgoError
from app.db.models import (
    BacktestRun, FactFeatureDaily, FactStockDaily, FactStockScoreDaily,
)
from app.db.session import get_db
from app.strategy.backtest import run_backtest
from app.strategy.strategies import SPECS

router = APIRouter(prefix="/api/v1/backtest", tags=["回测"])

#: B1–B5 用于判断特征回填起点的代表列（列名不能参数化，这里显式映射）
_B_START_COLUMNS = {
    "b1": FactFeatureDaily.ema20,
    "b2": FactFeatureDaily.rsi6,
    "b3": FactFeatureDaily.kdj_j,
    "b4": FactFeatureDaily.boll_width_pct60,
    "b5": FactFeatureDaily.kdj_j,
}


async def _data_start(algo_code: str, db: AsyncSession) -> date | None:
    """该策略所需数据的最早可用日期。

    回测如果从"没有特征/评分/资金流"的日期开始，第一个调仓日就会因
    `DataUnavailableError` 整体失败。与其让用户猜，不如**自动裁剪起点**并在
    响应里写明 `effective_start`（诚实、可解释）。
    """
    if algo_code in _B_START_COLUMNS:
        column = _B_START_COLUMNS[algo_code]
        return (await db.execute(
            select(func.min(FactFeatureDaily.trade_date)).where(column.isnot(None))
        )).scalar()
    if algo_code == "a3":
        return (await db.execute(select(func.min(FactStockScoreDaily.trade_date)))).scalar()
    if algo_code == "a4":
        return (await db.execute(
            select(func.min(FactStockDaily.trade_date)).where(FactStockDaily.pe_ttm.isnot(None))
        )).scalar()
    return None


def _collect_strategy_params(algo_code: str, query: dict) -> dict:
    """按策略 schema 从 query string 收集策略参数（不传则用策略默认值）。"""
    params: dict = {}
    for field in SPECS[algo_code].params_schema:
        key = field["key"]
        raw = query.get(key)
        if raw is None or raw == "":
            continue
        if field.get("type") == "integer":
            try:
                params[key] = int(raw)
            except (TypeError, ValueError):
                raise InvalidParamError(f"参数 {key} 必须是整数，收到 {raw!r}") from None
        else:
            try:
                params[key] = float(raw)
            except (TypeError, ValueError):
                raise InvalidParamError(f"参数 {key} 必须是数值，收到 {raw!r}") from None
    return params


def _serialize(row: BacktestRun) -> dict:
    return {
        "bt_id": row.bt_id,
        "algo": row.algo_code,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "params": row.params,
        "metrics": row.metrics,
        "equity_curve": row.equity_curve,
        "trades": row.trades,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/{algo_code}")
async def create_backtest(
    algo_code: str,
    request: Request,
    start: Optional[date] = Query(None, description="默认近 1 年"),
    end: Optional[date] = Query(None, description="默认最新交易日"),
    rebalance_days: int = Query(BACKTEST.rebalance_days, ge=1, le=250),
    max_positions: int = Query(BACKTEST.max_positions, ge=1, le=100),
    benchmark: str = Query(BACKTEST.benchmark_code),
    commission: float = Query(BACKTEST.commission_rate, ge=0, le=0.01),
    stamp_tax: float = Query(BACKTEST.stamp_tax_rate, ge=0, le=0.01),
    slippage: float = Query(BACKTEST.slippage_rate, ge=0, le=0.02),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """跑一次回测并落库。**同步返回**，前端展示 loading。

    策略参数从 query string 按该策略的 `params_schema` 收集（B1–B5 参数各异）。
    """
    if algo_code not in SPECS:
        raise UnknownAlgoError(f"未知策略: {algo_code}",
                               detail={"available": sorted(SPECS.keys())})

    end = end or date.today()
    start = start or (end - timedelta(days=365))
    if start >= end:
        raise InvalidParamError("start 必须早于 end")
    if (end - start).days > 365 * 6:
        raise InvalidParamError("回测区间不能超过 6 年（本系统历史深度为近 5 年）")

    # 数据可用起点裁剪：避免"第一个调仓日没有特征/评分"导致整体 409
    requested_start = start
    data_start = await _data_start(algo_code, db)
    trimmed = False
    if data_start and data_start > start:
        start = data_start
        trimmed = True
        if start >= end:
            raise InvalidParamError(
                f"数据只从 {data_start} 开始，回测区间内没有可用数据",
                detail={"effective_start": data_start.isoformat()},
            )

    strategy_params = _collect_strategy_params(algo_code, dict(request.query_params))

    bt_params = {
        "rebalance_days": rebalance_days,
        "max_positions": max_positions,
        "benchmark_code": benchmark,
        "commission_rate": commission,
        "stamp_tax_rate": stamp_tax,
        "slippage_rate": slippage,
    }
    result = await run_backtest(
        algo_code=algo_code,
        start=start,
        end=end,
        strategy_params=strategy_params,
        bt_params=bt_params,
        db=db,
    )
    # 区间裁剪信息放在响应顶层（不进 bt_params：回测模块对参数做严格白名单校验）
    result["requested_start"] = requested_start.isoformat()
    result["effective_start"] = start.isoformat()
    result["start_trimmed"] = trimmed
    return result


@router.get("/{bt_id}")
async def get_backtest(bt_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = (await db.execute(
        select(BacktestRun).where(BacktestRun.bt_id == bt_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"回测不存在: {bt_id}", detail={"bt_id": bt_id})
    return _serialize(row)


@router.get("/{bt_id}/trades")
async def get_backtest_trades(
    bt_id: str,
    code: Optional[str] = Query(None, min_length=1, max_length=6, pattern=r"^\d{1,6}$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """回测成交明细（可只取某只股票），用于 K 线买卖点标记。

    直接读 `backtest_run.trades`，**不重新计算回测**；`shares=0` 的记录是被拒单
    （涨跌停/停牌/无数据），前端需与真实成交区分展示。
    """
    row = (await db.execute(
        select(BacktestRun).where(BacktestRun.bt_id == bt_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"回测不存在: {bt_id}", detail={"bt_id": bt_id})

    all_trades = list(row.trades or [])
    target = code.zfill(6) if code else None
    items = [t for t in all_trades if target is None or str(t.get("code", "")).zfill(6) == target]
    return {
        "bt_id": row.bt_id,
        "algo_code": row.algo_code,
        "code": target,
        "total": len(all_trades),
        "count": len(items),
        "items": items,
    }


@router.get("/{bt_id}/export.csv")
async def export_backtest_csv(bt_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    row = (await db.execute(
        select(BacktestRun).where(BacktestRun.bt_id == bt_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"回测不存在: {bt_id}", detail={"bt_id": bt_id})

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "nav", "benchmark"])
    for point in (row.equity_curve or []):
        writer.writerow([point.get("date"), point.get("nav"), point.get("benchmark")])
    writer.writerow([])
    writer.writerow(["# trades"])
    writer.writerow(["date", "code", "name", "side", "price", "shares", "amount", "fee", "reason"])
    for t in (row.trades or []):
        writer.writerow([t.get("date"), t.get("code"), t.get("name"), t.get("side"),
                         t.get("price"), t.get("shares"), t.get("amount"),
                         t.get("fee"), t.get("reason")])

    return Response(
        content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="backtest_{row.algo_code}_{bt_id[:8]}.csv"'},
    )


__all__ = ["router"]
