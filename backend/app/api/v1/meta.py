"""
元信息 API：数据新鲜度 / 交易日历 / 任务监控（方案 A10 / E2 / E5）
=================================================================
这些接口是"可信度"的载体：前端所有数据卡片都应能标注来源与新鲜度。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep
from app.core.config import INGEST_NETWORK_ENABLED, SNAPSHOT_READY_HOUR, SNAPSHOT_READY_MINUTE
from app.core.logging import get_logger
from app.db.models import IngestJob, TradeCalendar
from app.db.session import get_db
from app.services.freshness import get_freshness_items

router = APIRouter(prefix="/api/v1/meta", tags=["元信息"])
logger = get_logger(__name__)


@router.get("/freshness")
async def get_freshness(db: AsyncSession = Depends(get_db)) -> dict:
    """每个数据集一条新鲜度记录。**前端徽标的唯一数据源。**"""
    items = await get_freshness_items(db)
    return {
        "items": items,
        "network_enabled": INGEST_NETWORK_ENABLED,
        "snapshot_ready_after": f"{SNAPSHOT_READY_HOUR:02d}:{SNAPSHOT_READY_MINUTE:02d}",
    }


@router.get("/trade-calendar")
async def get_trade_calendar(
    start: Optional[date] = Query(None, description="起始日期，默认 30 天前"),
    end: Optional[date] = Query(None, description="结束日期，默认今天"),
    only_open: bool = Query(False, description="只返回交易日"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """交易日历。前端日期选择器与回测区间选择用它。"""
    end = end or date.today()
    start = start or (end - timedelta(days=30))
    stmt = select(TradeCalendar).where(
        TradeCalendar.cal_date >= start, TradeCalendar.cal_date <= end
    ).order_by(TradeCalendar.cal_date)
    if only_open:
        stmt = stmt.where(TradeCalendar.is_open.is_(True))

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": [{"cal_date": r.cal_date.isoformat(), "is_open": bool(r.is_open)} for r in rows],
    }


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=200),
    task: Optional[str] = Query(None, description="任务名过滤"),
    status: Optional[str] = Query(None, description="状态过滤"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """采集/选股任务运行记录（任务监控页）。"""
    stmt = select(IngestJob).order_by(IngestJob.started_at.desc()).limit(limit)
    if task:
        stmt = stmt.where(IngestJob.task == task)
    if status:
        stmt = stmt.where(IngestJob.status == status)

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "job_id": r.job_id,
                "task": r.task,
                "dataset": r.dataset,
                "trade_date": r.trade_date.isoformat() if r.trade_date else None,
                "status": r.status,
                "total": r.total,
                "done": r.done,
                "failed": r.failed,
                "rows_written": r.rows_written,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "elapsed_ms": r.elapsed_ms,
                "error": r.error,
            }
            for r in rows
        ]
    }


@router.get("/partitions")
async def get_partitions(db: AsyncSession = Depends(get_db)) -> dict:
    """分区概览（方案 B1：跨年自动建分区的可视化证明）。"""
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bound
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = 'fact_stock_daily'
        ORDER BY c.relname
    """))
    return {"table": "fact_stock_daily",
            "partitions": [{"partition": r[0], "bound": r[1]} for r in result]}


__all__ = ["router"]
