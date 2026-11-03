"""日更错过自愈（P0-9）
======================
宿主机休眠、后端进程未运行时，APScheduler 内存作业在启动后只计算下一触发时间，
错过的 15:36 日更不会补跑（2026-11-02 整日缺数即此原因）。本模块在启动后对比
「已入库的最新交易日」与「最近完整交易日」：

  - 历史缺口日：先用 ``history_fast / valuation_fast / moneyflow_history`` 一次性
    覆盖整个缺口区间（历史口径，不依赖当日快照），再逐日重算
    ``index / sector / sector_agg / valuation / features / scores /
    sector_scores / temperature / report / algos``，最后统一重算 ``signal_perf``；
  - 缺口日就是今天且已收盘：直接走完整 ``run_daily_pipeline``（快照口径）。

并发安全：整个补跑持有 ``gfna:daily_pipeline`` advisory lock（与 15:36 定时任务同一
把锁），拿不到说明日更正在跑，直接跳过。所有写库都是 upsert / 覆盖式重算，
补跑可重复执行。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.logging import get_logger
from app.db.models import FactStockDaily, TradeCalendar

logger = get_logger("catch_up")

_CN_TZ = ZoneInfo(config.TIMEZONE)
CATCH_UP_LOCK = "gfna:daily_pipeline"

# 缺口区间用历史口径一次补齐
RANGE_STEPS = ("history_fast", "valuation_fast", "moneyflow_history")
# 逐日重算（依赖当日行情/资金流已落库）
DAY_STEPS = (
    "index", "sector", "sector_agg", "valuation", "features", "scores",
    "sector_scores", "temperature", "report", "algos",
)
# 全市场单日行数下限（约 5000+ 只；低于此值视为残缺日）
_MIN_DAILY_ROWS = 3000
# 残缺判定：不足上一交易日的 80%
_COMPLETENESS_RATIO = 0.8


# ============================================================
# 状态与计划（纯函数部分可离线单测）
# ============================================================

def plan_catch_up(
    *,
    last_completed: date | None,
    latest: date | None,
    open_days: list[date],
    latest_complete: bool = True,
    max_days: int = config.CATCH_UP_MAX_DAYS,
) -> list[date]:
    """返回需要补跑的交易日（升序）。

    - ``latest`` 为库里最新行情日期，``open_days`` 是 (latest, last_completed]
      之间的开市日；
    - ``latest`` 本身残缺时一并重跑（流水线中途失败的情况）；
    - 全库无行情（``latest is None``）不自动补：那是首次回补的事。
    """
    if last_completed is None or latest is None:
        return []
    days = [day for day in open_days if latest < day <= last_completed]
    if not latest_complete:
        days.insert(0, latest)
    if len(days) > max_days:
        logger.warning(
            "补跑缺口 %d 个交易日超过上限 %d，只补最近 %d 天（更早的用 pipeline_cli 回补）",
            len(days), max_days, max_days,
        )
        days = days[-max_days:]
    return days


def _today_cn() -> date:
    return datetime.now(_CN_TZ).date()


def _after_close() -> bool:
    now = datetime.now(_CN_TZ)
    return (now.hour, now.minute) >= (15, 30)


async def _day_row_count(db: AsyncSession, day: date) -> int:
    value = await db.scalar(
        select(func.count())
        .select_from(FactStockDaily)
        .where(FactStockDaily.trade_date == day)
    )
    return int(value or 0)


async def _latest_complete(db: AsyncSession, latest: date | None) -> bool:
    """最新行情日是否完整（行数绝对值 + 与前一交易日比例）。"""
    if latest is None:
        return True
    rows = await _day_row_count(db, latest)
    if rows < _MIN_DAILY_ROWS:
        return False
    previous = await db.scalar(
        select(func.max(FactStockDaily.trade_date))
        .where(FactStockDaily.trade_date < latest)
    )
    if previous is None:
        return True
    previous_rows = await _day_row_count(db, previous)
    return previous_rows == 0 or rows >= previous_rows * _COMPLETENESS_RATIO


async def _catch_up_state(db: AsyncSession) -> dict:
    latest = await db.scalar(select(func.max(FactStockDaily.trade_date)))
    cutoff = _today_cn() if _after_close() else _today_cn() - timedelta(days=1)
    last_completed = await db.scalar(
        select(func.max(TradeCalendar.cal_date)).where(
            TradeCalendar.is_open.is_(True),
            TradeCalendar.cal_date <= cutoff,
        )
    )
    open_days: list[date] = []
    if latest is not None and last_completed is not None:
        open_days = list(
            (
                await db.execute(
                    select(TradeCalendar.cal_date)
                    .where(
                        TradeCalendar.is_open.is_(True),
                        TradeCalendar.cal_date > latest,
                        TradeCalendar.cal_date <= last_completed,
                    )
                    .order_by(TradeCalendar.cal_date)
                )
            ).scalars()
        )
    return {
        "latest": latest,
        "last_completed": last_completed,
        "open_days": open_days,
        "latest_complete": await _latest_complete(db, latest),
    }


# ============================================================
# 执行
# ============================================================

async def _execute_catch_up(
    days: list[date], db: AsyncSession, *, today: date, snapshot_ok: bool
) -> dict:
    """按缺口逐日补跑；单步失败不中断后续（run_task 自身不抛异常）。"""
    from app.pipeline import run_daily_pipeline, run_task

    results: dict[str, dict] = {}
    first, last = days[0], days[-1]

    for step in RANGE_STEPS:
        results[step] = await run_task(step, start=first, end=last, db=db)
        logger.info("补跑区间 %s~%s：%s → %s", first, last, step,
                    results[step].get("status"))

    for day in days:
        if day == today and snapshot_ok:
            summary = await run_daily_pipeline(day, db, steps=(
                "calendar", "universe", "index", "sector", "sector_members",
                "snapshot", "moneyflow", "sector_agg", "margin", "valuation",
                "features", "scores", "sector_scores", "temperature", "report",
                "algos",
            ))
            failed = [
                key for key, value in summary["steps"].items()
                if isinstance(value, dict) and value.get("status") == "failed"
            ]
            results[f"{day}:daily_pipeline"] = {
                "status": "partial" if failed else "success", "failed": failed,
            }
            continue
        for step in DAY_STEPS:
            outcome = await run_task(step, trade_date=day, db=db)
            results[f"{day}:{step}"] = outcome
            if outcome.get("status") == "failed":
                logger.warning("补跑 %s %s 失败：%s", day, step, outcome.get("error"))

    # 前瞻统计依赖所有补跑日的选股结果，最后统一重算
    results["signal_perf"] = await run_task("signal_perf", trade_date=last, db=db)

    failed_steps = [
        key for key, value in results.items()
        if isinstance(value, dict) and value.get("status") == "failed"
    ]
    return {
        "status": "partial" if failed_steps else "success",
        "days": [day.isoformat() for day in days],
        "failed": failed_steps,
    }


async def run_catch_up(db: AsyncSession | None = None) -> dict:
    """检查并补跑日更缺口。返回摘要；无缺口时只做两次只读查询。"""
    from app.db.session import AsyncSessionLocal
    from app.pipeline import advisory_unlock, try_advisory_lock

    if db is None:
        async with AsyncSessionLocal() as own_db:
            return await run_catch_up(own_db)

    state = await _catch_up_state(db)
    days = plan_catch_up(**state)
    summary = {
        "latest": state["latest"].isoformat() if state["latest"] else None,
        "last_completed": (
            state["last_completed"].isoformat() if state["last_completed"] else None
        ),
        "latest_complete": state["latest_complete"],
    }
    if not days:
        return {"status": "up_to_date", **summary}

    acquired = False
    try:
        acquired = await try_advisory_lock(db, CATCH_UP_LOCK)
        if not acquired:
            logger.info("日更正在其他实例运行，跳过启动补跑")
            return {"status": "skipped", "reason": "lock_busy",
                    "days": [day.isoformat() for day in days], **summary}
        logger.info("启动补跑：%d 个交易日 %s", len(days), [d.isoformat() for d in days])
        result = await _execute_catch_up(
            days, db, today=_today_cn(), snapshot_ok=_after_close()
        )
        return {**result, **summary}
    finally:
        if acquired:
            await advisory_unlock(db, CATCH_UP_LOCK)


async def run_catch_up_in_background(*, delay_sec: float | None = None) -> None:
    """lifespan 启动任务入口：延迟少许，异常只记日志，绝不影响服务启动。"""
    await asyncio.sleep(config.CATCH_UP_DELAY_SEC if delay_sec is None else delay_sec)
    try:
        summary = await run_catch_up()
        logger.info("启动补跑结束：%s", summary)
    except asyncio.CancelledError:
        raise
    except Exception:                                   # noqa: BLE001
        logger.exception("启动补跑失败（不阻断服务）")


__all__ = [
    "plan_catch_up", "run_catch_up", "run_catch_up_in_background",
    "RANGE_STEPS", "DAY_STEPS", "CATCH_UP_LOCK",
]
