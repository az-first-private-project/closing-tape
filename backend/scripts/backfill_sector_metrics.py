"""板块聚合指标历史回填（V6 T4）

按交易日逐日重算 `fact_sector_daily` 的 market_cap / float_cap /
main_net_inflow / rs_20d / member_count。管线只算当天，treemap 与历史回看
依赖这些派生列，所以需要把历史补齐。

断点策略与 backfill_temperature.py 一致：

    逐批计算 + 提交 → 整批成功后才写断点（tmp + os.replace）

重跑幂等（同一日重算覆盖），断点只记录已落库日期，并会与库内实际
`member_count IS NOT NULL` 的日期对账。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_sector_metrics.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_sector_metrics.py --start 2025-01-01 --end 2026-10-26
    ../.venv/Scripts/python.exe scripts/backfill_sector_metrics.py --batch-days 20 --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402

from app.core import config  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import FactSectorDaily  # noqa: E402
from app.db.models.meta import TradeCalendar  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.sector_agg import aggregate_sector_metrics  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE = Path(config.BACKEND_DIR) / "var" / "sector_metrics_backfill_progress.json"
DEFAULT_LOOKBACK_DAYS = 365


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


def _save_state(state: dict) -> None:
    """原子写断点；Windows 上 os.replace 偶发被占用，短暂重试。"""
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(tmp, _STATE)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def _load_state() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


async def _latest_open_day() -> date | None:
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.is_open.is_(True))
            .where(TradeCalendar.cal_date <= date.today())
            .order_by(TradeCalendar.cal_date.desc())
            .limit(1)
        )).scalar()


async def _trade_days(start: date, end: date) -> list[date]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.is_open.is_(True))
            .where(TradeCalendar.cal_date >= start)
            .where(TradeCalendar.cal_date <= end)
            .order_by(TradeCalendar.cal_date)
        )).scalars().all()
    return list(rows)


async def _db_days(start: date, end: date) -> set[date]:
    """断点对账：库里 member_count 已写入的日期才算完成。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(FactSectorDaily.trade_date)
            .where(FactSectorDaily.trade_date >= start)
            .where(FactSectorDaily.trade_date <= end)
            .where(FactSectorDaily.member_count.isnot(None))
            .distinct()
        )).scalars().all()
    return set(rows)


async def _has_sector_rows(session, day: date) -> bool:
    return (await session.execute(
        select(FactSectorDaily.sector_code)
        .where(FactSectorDaily.trade_date == day)
        .limit(1)
    )).scalar() is not None


async def run(args) -> int:
    end = args.end or await _latest_open_day()
    if end is None:
        raise SystemExit("交易日历为空，请先执行 calendar 任务")
    start = args.start or (end - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    if start > end:
        raise SystemExit(f"--start {start} 晚于 --end {end}")

    days = await _trade_days(start, end)
    if not days:
        print(json.dumps({"message": "区间内没有交易日", "start": str(start), "end": str(end)},
                         ensure_ascii=False))
        return 0

    state = {} if args.force else _load_state()
    completed: dict[str, dict] = dict(state.get("completed") or {})
    skipped: dict[str, str] = dict(state.get("skipped") or {})
    failed: dict[str, str] = dict(state.get("failed") or {})
    rows_written = int(state.get("rows_written") or 0)

    db_days = await _db_days(start, end)
    for key in list(completed):
        if _parse_date(key) in days and _parse_date(key) not in db_days:
            logger.warning("断点显示 %s 已完成，但库中无指标数据，重新计算", key)
            completed.pop(key, None)

    pending = [
        day for day in days
        if day.isoformat() not in completed
        and (args.force or day.isoformat() not in skipped)
    ]
    logger.info(
        "板块指标回填：区间 %s ~ %s，交易日 %d，需计算 %d，已完成 %d，跳过 %d，失败 %d",
        start, end, len(days), len(pending), len(completed), len(skipped), len(failed),
    )

    if args.dry_run:
        print(json.dumps({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trade_days": len(days),
            "pending": [d.isoformat() for d in pending],
            "completed": len(completed),
            "skipped": len(skipped),
            "failed": failed,
            "rows_written": rows_written,
            "database_days": len(db_days),
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    def persist() -> None:
        _save_state({
            "version": 1,
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()
    started_at = time.perf_counter()

    for offset in range(0, len(pending), args.batch_days):
        batch = pending[offset:offset + args.batch_days]
        batch_started = time.perf_counter()
        done: dict[str, dict] = {}

        async with AsyncSessionLocal() as session:
            for day in batch:
                key = day.isoformat()
                try:
                    if not await _has_sector_rows(session, day):
                        skipped[key] = "no_sector_daily"
                        failed.pop(key, None)
                        continue
                    summary = await aggregate_sector_metrics(session, day)
                except Exception as exc:                  # noqa: BLE001 —— 单日失败不阻断整批
                    failed[key] = f"{type(exc).__name__}: {exc}"
                    logger.exception("板块指标回填失败 %s", key)
                    continue
                done[key] = {
                    "rows": summary["rows"],
                    "with_cap": summary["with_cap"],
                    "with_rs": summary["with_rs"],
                }
                failed.pop(key, None)
                skipped.pop(key, None)
            # 先提交，再写断点：避免"断点说完成、库里其实回滚了"
            await session.commit()

        completed.update(done)
        rows_written += sum(item["rows"] for item in done.values())
        persist()
        logger.info(
            "批次 %d-%d 完成：%d 天，用时 %.1fs（累计 %d 行）",
            offset + 1, offset + len(batch), len(done),
            time.perf_counter() - batch_started, rows_written,
        )

    if completed:
        last_day = max(_parse_date(key) for key in completed)
        async with AsyncSessionLocal() as session:
            await mark_success(
                session, "sector_metrics",
                rows=rows_written,
                coverage=round(len(completed) / len(days), 4),
                trade_date=last_day,
                detail={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "days": len(completed),
                    "skipped": len(skipped),
                    "failed": len(failed),
                },
            )
            await session.commit()

    logger.info(
        "板块指标回填结束：完成 %d/%d，跳过 %d，失败 %d，用时 %.1f 分钟",
        len(completed), len(days), len(skipped), len(failed),
        (time.perf_counter() - started_at) / 60,
    )
    return 0 if not failed else 1


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="板块聚合指标历史回填（断点续传）")
    parser.add_argument("--start", type=_parse_date, default=None,
                        help=f"起始日（默认 end - {DEFAULT_LOOKBACK_DAYS} 天）")
    parser.add_argument("--end", type=_parse_date, default=None,
                        help="截止日（默认最近一个开市日）")
    parser.add_argument("--batch-days", type=int, default=20,
                        help="每批计算的交易日数（默认 20，批末提交并写断点）")
    parser.add_argument("--force", action="store_true", help="忽略断点全量重算")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与断点状态")
    args = parser.parse_args()
    if args.batch_days < 1:
        parser.error("--batch-days 必须 >= 1")
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
