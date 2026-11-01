"""
纸面组合 API（V6 T11）
=======================
GET  /api/v1/paper/portfolios                   组合列表（净值/超额/回撤/跟踪天数）
GET  /api/v1/paper/portfolio/{algo_code}        净值序列 + 持仓 + 明日待执行 + freshness
POST /api/v1/paper/portfolio/{algo_code}/sync   手动重算（完整区间，转后台）

组合由后台同步自动创建；未初始化的启用策略在列表里以 ``status=pending`` 出现。
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParamError, UnknownAlgoError
from app.db.session import get_db
from app.engine.paper import (
    get_paper_detail,
    is_enabled,
    list_paper_portfolios,
    sync_paper_background,
)
from app.strategy.strategies import SPECS

router = APIRouter(prefix="/api/v1/paper", tags=["纸面组合"])


def _ensure_algo(algo_code: str) -> str:
    code = algo_code.strip().lower()
    if code not in SPECS:
        raise UnknownAlgoError(
            f"未知策略: {algo_code}", detail={"available": sorted(SPECS)}
        )
    if not is_enabled(code):
        raise InvalidParamError(
            f"策略 {code} 未启用纸面跟踪",
            detail={"enable": "把该策略加入 PAPER_ENABLED_ALGOS 后重启"},
        )
    return code


@router.get("/portfolios")
async def get_paper_portfolios(db: AsyncSession = Depends(get_db)) -> dict:
    items = await list_paper_portfolios(db)
    return {"items": items, "total": len(items)}


@router.get("/portfolio/{algo_code}")
async def get_paper_portfolio(
    algo_code: str,
    days: int = Query(250, ge=1, le=2000, description="返回最近多少个交易日"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await get_paper_detail(db, _ensure_algo(algo_code), days=days)


@router.post("/portfolio/{algo_code}/sync")
async def sync_paper_portfolio_api(
    algo_code: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """手动触发完整区间重算；耗时与区间长度成正比，统一转后台。"""
    code = _ensure_algo(algo_code)
    background_tasks.add_task(sync_paper_background, code)
    return {
        "status": "accepted",
        "algo": code,
        "mode": "background",
        "hint": f"轮询 GET /api/v1/paper/portfolio/{code} 查看结果",
    }


__all__ = ["router"]
