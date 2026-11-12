"""市场原始因子历史回补（V7 T1）

按批计算 `fact_market_daily`：每批一个区间，先提交再写断点。
默认从 2022-09-01（广度数据可用起点）回补到最近开市日。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_market_factors.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_market_factors.py --start 2022-09-01
    ../.venv/Scripts/python.exe scripts/backfill_market_factors.py --batch-days 120 --force
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
from app.db.models.fact import FactMarketDaily  # noqa: E402
from app.db.models.meta import TradeCalendar  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.market_factors import compute_market_factors_range  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE = Path(config.BACKEND_DIR) / "var" / "market_factors_progress.json"
DEFAULT_START = date(2022, 9, 1)


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


def _save_state(state: dict) -> None:
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
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(FactMarketDaily.trade_date)
            .where(FactMarketDaily.trade_date >= start)
            .where(FactMarketDaily.trade_date <= end)
        )).scalars().all()
    return set(rows)


async def run(args) -> int:
    end = args.end or await _latest_open_day()
    if end is None:
        raise SystemExit("交易日历为空，请先执行 calendar 任务")
    start = args.start or DEFAULT_START
    if start > end:
        raise SystemExit(f"--start {start} 晚于 --end {end}")

    days = await _trade_days(start, end)
    if not days:
        print(json.dumps({"message": "区间内没有交易日"}, ensure_ascii=False))
        return 0

    state = {} if args.force else _load_state()
    completed: dict[str, dict] = dict(state.get("completed") or {})
    db_days = await _db_days(start, end)
    for key in list(completed):
        if date.fromisoformat(key) in days and date.fromisoformat(key) not in db_days:
            logger.warning("断点显示 %s 已完成，但库中无数据，重新计算", key)
            completed.pop(key, None)

    pending = [day for day in days
               if f"{day.isoformat()}" not in completed and day not in db_days]
    logger.info(
        "因子回补：区间 %s ~ %s，交易日 %d，需计算 %d，已完成 %d",
        start, end, len(days), len(pending), len(completed),
    )
    if args.dry_run:
        print(json.dumps({
            "start": start.isoformat(), "end": end.isoformat(),
            "trade_days": len(days), "pending": len(pending),
            "completed": len(completed), "database_days": len(db_days),
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    started_at = time.perf_counter()
    rows_written = int(state.get("rows_written") or 0)
    for offset in range(0, len(pending), args.batch_days):
        batch = pending[offset:offset + args.batch_days]
        batch_start = batch[0]
        batch_end = batch[-1]
        n = await compute_market_factors_range(batch_start, batch_end)
        rows_written += n
        for day in batch:
            completed[day.isoformat()] = {"rows": n}
        _save_state({
            "version": 1,
            "completed": completed,
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })
        logger.info(
            "批次 %s ~ %s 完成：写入 %d 行（累计 %d）",
            batch_start, batch_end, n, rows_written,
        )

    logger.info(
        "因子回补结束：%d 个交易日，用时 %.1f 秒",
        len(pending), time.perf_counter() - started_at,
    )
    if completed:
        latest = max(date.fromisoformat(key) for key in completed)
        async with AsyncSessionLocal() as session:
            await mark_success(
                session, "market_factors",
                rows=rows_written, coverage=1.0, trade_date=latest,
                detail={"start": start.isoformat(), "end": end.isoformat()},
            )
            await session.commit()
    return 0


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="市场原始因子历史回补（断点续传）")
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--batch-days", type=int, default=120)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.batch_days < 1:
        parser.error("--batch-days 必须 >= 1")
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
