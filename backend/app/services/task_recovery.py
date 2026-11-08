"""僵尸任务启动清理（P1-4）。

``JobRecorder``、策略 run 与回测（含矩阵格）都先落 running/queued 记录再执行；
进程被杀或服务重启后这些行不会自行结束，监控页会一直显示“运行中”，矩阵进度
也永远差最后一格。服务启动时把超过 ``STALE_AFTER`` 的活跃行标为 failed，
让监控状态与真实进程一致；阈值用于避开仍在其他脚本进程内运行的长任务。

更精确的 DB 队列 + heartbeat 见计划 P1-4 中期方案，本模块只做短期兜底。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.status import TaskStatus
from app.db.models.meta import IngestJob
from app.db.models.strategy import BacktestRun, StgRun

logger = get_logger(__name__)

#: 超过该时长仍未结束的 running/queued 视为遗留任务
STALE_AFTER = timedelta(hours=2)

#: 启动清理写入的固定错误文案（可按前缀审计/筛出）
RESTART_ERROR = "进程重启遗留：服务启动时任务仍处于 running/queued 状态，已标记 failed"

_ACTIVE = (TaskStatus.RUNNING.value, "queued")


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _elapsed_ms(started_at: datetime | None, now: datetime) -> int | None:
    """按 UTC 计算耗时；历史行可能带 +08:00，也可能是不带时区的 naive 值。"""
    if started_at is None:
        return None
    delta = _as_utc(now) - _as_utc(started_at)
    return max(0, int(delta.total_seconds() * 1000))


def is_stale(
    started_at: datetime | None,
    *,
    now: datetime,
    max_age: timedelta = STALE_AFTER,
) -> bool:
    """开始时间早于 ``now - max_age`` 才算遗留；缺失开始时间不判遗留。"""
    if started_at is None:
        return False
    return _as_utc(started_at) < _as_utc(now) - max_age


async def recover_stale_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    max_age: timedelta = STALE_AFTER,
) -> dict[str, int]:
    """把启动前遗留的活跃任务标为 failed，返回各表实际处理行数。

    只改状态字段，不删除记录；调用方负责 commit。只处理超过 ``max_age`` 的行，
    避免把仍在其他进程写库的任务误判为僵尸。活跃行本身极少（终态占绝大多数），
    因此按状态取回后在 Python 侧按时间过滤，时区处理也更可控。
    """
    reference = now or datetime.now(timezone.utc)

    jobs = [job for job in (await session.execute(
        select(IngestJob).where(IngestJob.status.in_(_ACTIVE))
    )).scalars().all() if is_stale(job.started_at, now=reference, max_age=max_age)]
    for job in jobs:
        job.status = TaskStatus.FAILED.value
        job.finished_at = reference
        job.elapsed_ms = _elapsed_ms(job.started_at, reference)
        job.error = RESTART_ERROR

    runs = [run for run in (await session.execute(
        select(StgRun).where(StgRun.status == TaskStatus.RUNNING.value)
    )).scalars().all() if is_stale(run.created_at, now=reference, max_age=max_age)]
    for run in runs:
        run.status = TaskStatus.FAILED.value
        run.elapsed_ms = _elapsed_ms(run.created_at, reference)
        run.error = RESTART_ERROR

    backtests = [row for row in (await session.execute(
        select(BacktestRun).where(BacktestRun.status.in_(_ACTIVE))
    )).scalars().all() if is_stale(row.created_at, now=reference, max_age=max_age)]
    for row in backtests:
        row.status = TaskStatus.FAILED.value
        row.elapsed_ms = _elapsed_ms(row.created_at, reference)
        row.error = RESTART_ERROR

    counts = {
        "ingest_job": len(jobs),
        "stg_run": len(runs),
        "backtest_run": len(backtests),
    }
    if any(counts.values()):
        logger.warning("启动清理僵尸任务 %s（阈值 %s）", counts, max_age)
    return counts


__all__ = ["RESTART_ERROR", "STALE_AFTER", "is_stale", "recover_stale_tasks"]
