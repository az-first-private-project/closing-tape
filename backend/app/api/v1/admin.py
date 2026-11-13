"""
管理 API（手动触发采集/分析；方案 A8 的手动入口）
=================================================
所有写操作都需要 `X-Admin-Key`（未配置时仅允许本机访问，见 api/deps.py）。
演示数据接口只在 `ENV=demo` 下可用，已从生产路径移出（方案 P2）。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep
from app.core.errors import InvalidParamError
from app.core.logging import get_logger
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["管理"])
logger = get_logger(__name__)

INGEST_TASKS = {
    "calendar", "universe", "snapshot", "backfill", "history_fast",
    "moneyflow_history", "valuation_fast", "rebuild", "sector", "sector_members",
    "sector_fallback", "moneyflow", "valuation", "index", "broad_indices",
    "style_indices", "margin", "features", "scores", "sector_scores", "report",
    "algos", "signal_perf", "all",
}
# 这些任务耗时较长，默认建议后台跑
HEAVY_TASKS = {
    "backfill", "history_fast", "moneyflow_history", "valuation_fast", "rebuild",
    "all", "features", "scores", "valuation",
}


@router.post("/ingest/{task}")
async def trigger_ingest(
    task: str,
    background_tasks: BackgroundTasks,
    trade_date: Optional[date] = Query(None, description="目标交易日，默认最新交易日"),
    start: Optional[date] = Query(None, description="backfill 起始日期"),
    end: Optional[date] = Query(None, description="backfill 结束日期"),
    codes: Optional[str] = Query(None, description="backfill 指定代码，逗号分隔"),
    background: bool = Query(False, description="是否后台执行（重任务建议 true）"),
    _admin: AdminDep = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发采集或分析任务。"""
    if task not in INGEST_TASKS:
        raise InvalidParamError(f"未知任务: {task}",
                               detail={"available": sorted(INGEST_TASKS)})

    if background or task in HEAVY_TASKS:
        from app.pipeline import run_task_background
        background_tasks.add_task(run_task_background, task, trade_date, start, end, codes)
        return {
            "status": "accepted", "task": task, "mode": "background",
            "hint": "进度见 GET /api/v1/meta/jobs",
        }

    from app.pipeline import run_task
    return await run_task(task, trade_date=trade_date, start=start, end=end,
                          codes=codes, db=db)


@router.post("/catch-up")
async def trigger_catch_up(
    background_tasks: BackgroundTasks,
    _admin: AdminDep = None,
) -> dict:
    """手动触发「检查并补跑最近缺失交易日」。

    复用启动补跑同一套逻辑（advisory lock + 历史口径补区间 + 逐日重算），
    后台执行，不阻塞当前请求；进度见 GET /api/v1/meta/jobs。
    """
    from app.catch_up import run_catch_up_in_background

    background_tasks.add_task(run_catch_up_in_background, delay_sec=0.0)
    return {
        "status": "accepted",
        "mode": "background",
        "hint": "已提交补缺检查，进度见任务监控 / GET /api/v1/meta/jobs",
    }


@router.post("/seed-demo")
async def seed_demo(_admin: AdminDep = None, db: AsyncSession = Depends(get_db)) -> dict:
    """灌入确定性演示数据，仅 `ENV=demo` 可用。用于无网环境验收全链路。"""
    import os
    if os.getenv("ENV", "").lower() != "demo":
        raise InvalidParamError(
            "演示数据接口已下线；生产请用 POST /api/v1/admin/ingest/all",
            detail={"enable": "设置 ENV=demo 后可用"},
        )
    from app.fixtures.demo_data import seed_demo_data
    return await seed_demo_data(db)


__all__ = ["router", "INGEST_TASKS"]
