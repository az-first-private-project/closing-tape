"""
定时任务（方案 A8 / F2）
========================
单进程 APScheduler + 数据库 advisory lock：
  - 15:36 盘后流水线（真实交易日历判断，节假日不跑）
  - 16:30 选股（等评分与日报就绪）
  - 17:10 信号前瞻统计（依赖 16:30 选股结果）
  - 02:00 历史补齐（断点续传，夜间执行）
  - 每 N 分钟新闻采集（作为情绪因子输入）

**为什么不用 Celery**：方案 F2 明确"先单进程 + DB 锁，量大再上队列"。
`pg_try_advisory_lock` 保证多 worker 部署时同一时刻只有一个实例在跑，定时任务因此幂等。
"""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import CRAWL_INTERVAL_MINUTES, TIMEZONE
from app.core.logging import bind_run, get_logger

logger = get_logger("worker")

_TZ = ZoneInfo(TIMEZONE)
scheduler: AsyncIOScheduler | None = None


# ============================================================
# 任务体
# ============================================================

async def _with_lock(name: str, job) -> None:
    """带 advisory lock 执行，拿不到锁就跳过（多实例部署安全）。"""
    from app.db.session import AsyncSessionLocal
    from app.pipeline import advisory_unlock, try_advisory_lock

    async with AsyncSessionLocal() as db:
        acquired = False
        try:
            acquired = await try_advisory_lock(db, f"gfna:{name}")
            if not acquired:
                logger.info("任务 %s 已在其他实例运行，跳过", name)
                return
            with bind_run(task=name):
                await job(db)
        finally:
            if acquired:
                await advisory_unlock(db, f"gfna:{name}")


async def run_daily_pipeline_job(db) -> None:
    """盘后全链路。仅在真实交易日执行（修 #17：不再用 weekday 判断）。"""
    from app.ingest.calendar import is_trade_day
    from app.pipeline import run_daily_pipeline

    today = date.today()
    if not await is_trade_day(db, today):
        logger.info("%s 非交易日，跳过盘后流水线", today)
        return

    result = await run_daily_pipeline(today, db)
    failed = [k for k, v in result["steps"].items()
              if isinstance(v, dict) and v.get("status") == "failed"]
    logger.info("盘后流水线结束 steps=%d failed=%s", len(result["steps"]), failed)


async def run_algo_job(db) -> None:
    """单独跑四个策略（结果先落库，前端打开即热）。"""
    from app.pipeline import run_task
    await run_task("algos", db=db)


async def run_signal_perf_job(db) -> None:
    """重算最近 25 个交易日的信号前瞻收益（依赖 16:30 选股结果）。"""
    from app.ingest.calendar import latest_trade_date
    from app.pipeline import run_task

    latest = await latest_trade_date(db, on_or_before=date.today())
    if latest is None:
        logger.info("无可用交易日，跳过信号前瞻统计")
        return
    await run_task("signal_perf", trade_date=latest, db=db)


async def run_news_crawl_job(db) -> None:
    """新闻采集（国内四源 JSON 快讯；失败不阻断主链）。"""
    from app.services.news_collector import collect_news

    try:
        await collect_news(db)
    except Exception:                                  # noqa: BLE001
        logger.exception("新闻采集失败（非阻断）")


async def run_backfill_job(db) -> None:
    """夜间补齐历史日线（断点续传，失败可继续）。"""
    from app.pipeline import run_task
    await run_task("backfill", db=db)


# ============================================================
# 调度器
# ============================================================

def start_scheduler() -> AsyncIOScheduler:
    """注册并启动定时任务。"""
    global scheduler
    if scheduler is not None:
        return scheduler

    scheduler = AsyncIOScheduler(timezone=_TZ)
    # 15:36 而不是 15:05：config.SNAPSHOT_READY_* 默认 15:30，采集层会拒绝"未收盘就绪"的数据，
    # 早于该时刻跑会让 snapshot/sector/moneyflow 全部被跳过（见 ingest/base 的 before_close_ready_time）
    scheduler.add_job(_with_lock, CronTrigger(hour=15, minute=36, timezone=_TZ),
                      args=["daily_pipeline", run_daily_pipeline_job],
                      id="daily_pipeline", name="盘后流水线", replace_existing=True)
    scheduler.add_job(_with_lock, CronTrigger(hour=16, minute=30, timezone=_TZ),
                      args=["algos", run_algo_job],
                      id="algos", name="策略选股", replace_existing=True)
    scheduler.add_job(_with_lock, CronTrigger(hour=17, minute=10, timezone=_TZ),
                      args=["signal_perf", run_signal_perf_job],
                      id="signal_perf", name="信号前瞻统计", replace_existing=True)
    scheduler.add_job(_with_lock, CronTrigger(hour=2, minute=0, timezone=_TZ),
                      args=["backfill", run_backfill_job],
                      id="backfill", name="历史补齐", replace_existing=True)
    scheduler.add_job(_with_lock, "interval", minutes=CRAWL_INTERVAL_MINUTES,
                      args=["news", run_news_crawl_job],
                      id="news_crawl", name="新闻采集", replace_existing=True)

    scheduler.start()
    logger.info("定时任务已启动：15:36 盘后 / 16:30 选股 / 17:10 信号统计 / 02:00 补齐 / 每 %d 分钟新闻",
                CRAWL_INTERVAL_MINUTES)
    return scheduler


def stop_scheduler() -> None:
    global scheduler
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
            logger.info("定时任务已停止")
        except Exception:                              # noqa: BLE001
            pass
        scheduler = None


def job_status() -> list[dict]:
    if scheduler is None:
        return []
    return [
        {"id": job.id, "name": job.name,
         "next_run": job.next_run_time.isoformat() if job.next_run_time else None}
        for job in scheduler.get_jobs()
    ]


__all__ = ["start_scheduler", "stop_scheduler", "job_status",
           "run_daily_pipeline_job", "run_algo_job", "run_signal_perf_job",
           "run_news_crawl_job", "run_backfill_job"]
