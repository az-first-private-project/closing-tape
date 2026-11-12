"""宏观与两融回补（V7 T1）

两个数据集：

1. bond       中债 1Y/10Y 国债收益率，按年分段拉取，写 `macro_rates`；
2. margin_buy 沪深融资买入额，SSE 按年区间批量 + SZSE 逐日，写
              `margin_trading.margin_buy_amount`（元）。

断点与幂等：
  - 全部 upsert，可重复执行；
  - margin_buy 的 SZSE 逐日进度写 `backend/var/margin_buy_checkpoint.json`；
  - 每批先提交再写断点，避免“断点说完成、库里回滚”。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_macro.py --dataset bond
    ../.venv/Scripts/python.exe scripts/backfill_macro.py --dataset margin_buy --days 750
    ../.venv/Scripts/python.exe scripts/backfill_macro.py --dataset all --days 750
    ../.venv/Scripts/python.exe scripts/backfill_macro.py --dataset margin_buy --dry-run
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

from sqlalchemy import func, select  # noqa: E402

from app.core import config  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import MacroRate, MarginTrading  # noqa: E402
from app.db.models.meta import TradeCalendar  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.ingest.base import upsert_rows  # noqa: E402
from app.ingest.service import get_provider_chain  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE = Path(config.BACKEND_DIR) / "var" / "margin_buy_checkpoint.json"
_MARGIN_START = date(2023, 10, 1)


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


async def _db_margin_days() -> set[date]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(MarginTrading.trade_date).where(
                MarginTrading.margin_buy_amount.isnot(None)
            )
        )).scalars().all()
    return set(rows)


async def _backfill_bond(start: date, end: date) -> int:
    chain = get_provider_chain()
    total = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(end, date(cursor.year, 12, 31))
        fetched = await chain.fetch_bond_yields(cursor, chunk_end)
        frame = fetched.detail.get("data")
        rows = []
        if frame is not None and not getattr(frame, "empty", True):
            for record in frame.to_dict("records"):
                day = record.get("trade_date")
                if day is None:
                    continue
                rows.append({
                    "trade_date": day if isinstance(day, date) else date.fromisoformat(str(day)[:10]),
                    "cn_1y": record.get("cn_1y"),
                    "cn_10y": record.get("cn_10y"),
                    "term_spread": record.get("term_spread"),
                    "src": "chinabond",
                })
        if rows:
            async with AsyncSessionLocal() as session:
                written = await upsert_rows(session, MacroRate, rows, ["trade_date"])
                await session.commit()
            total += written
            logger.info("国债收益率 %s ~ %s：写入 %d 行", cursor, chunk_end, written)
        else:
            logger.warning("国债收益率 %s ~ %s：源返回空", cursor, chunk_end)
        cursor = chunk_end + timedelta(days=1)
    return total


async def _sse_buy_map(start: date, end: date) -> dict[date, float]:
    chain = get_provider_chain()
    result: dict[date, float] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(end, date(cursor.year, 12, 31))
        fetched = await chain.fetch_margin_buy_sse(cursor, chunk_end)
        frame = fetched.detail.get("data")
        if frame is not None and not getattr(frame, "empty", True):
            for record in frame.to_dict("records"):
                day = record.get("trade_date")
                value = record.get("margin_buy_amount")
                if day is None or value is None:
                    continue
                day = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
                result[day] = float(value)
        logger.info("SSE 融资买入额 %s ~ %s：累计 %d 天", cursor, chunk_end, len(result))
        cursor = chunk_end + timedelta(days=1)
    return result


async def _backfill_margin_buy(start: date, end: date, *, batch_days: int) -> int:
    state = _load_state()
    completed: set[str] = set(state.get("completed") or [])
    failed: dict[str, str] = dict(state.get("failed") or {})
    rows_written = int(state.get("rows_written") or 0)

    days = await _trade_days(start, end)
    if not days:
        logger.warning("区间内没有交易日：%s ~ %s", start, end)
        return 0

    db_days = await _db_margin_days()
    for key in list(completed):
        try:
            day = date.fromisoformat(key)
        except ValueError:
            completed.discard(key)
            continue
        if day in days and day not in db_days:
            logger.warning("断点显示 %s 已完成，但库中无数据，重新拉取", key)
            completed.discard(key)

    logger.info("拉取 SSE 区间融资买入额 %s ~ %s", start, end)
    sse_map = await _sse_buy_map(start, end)

    chain = get_provider_chain()
    pending = [d for d in days if d.isoformat() not in completed]
    logger.info(
        "融资买入额回补：交易日 %d，待拉取 %d，断点已完成 %d",
        len(days), len(pending), len(completed),
    )

    def persist() -> None:
        _save_state({
            "version": 1,
            "completed": sorted(completed),
            "failed": failed,
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()
    for offset in range(0, len(pending), batch_days):
        batch = pending[offset:offset + batch_days]
        rows: list[dict] = []
        for day in batch:
            key = day.isoformat()
            sse = sse_map.get(day)
            if sse is None:
                failed[key] = "sse_missing"
                continue
            try:
                fetched = await chain.fetch_margin_buy_szse(day)
            except Exception as exc:  # noqa: BLE001 —— 单日失败不阻断整批
                failed[key] = f"{type(exc).__name__}: {exc}"
                logger.warning("SZSE 融资买入额失败 %s：%s", key, exc)
                continue
            frame = fetched.detail.get("data")
            szse = None
            if frame is not None and not getattr(frame, "empty", True):
                szse = frame.to_dict("records")[0].get("margin_buy_amount")
            if szse is None:
                failed[key] = "szse_missing"
                continue
            rows.append({
                "trade_date": day,
                "margin_buy_amount": round(float(sse) + float(szse), 2),
            })
            failed.pop(key, None)

        if rows:
            async with AsyncSessionLocal() as session:
                written = await upsert_rows(
                    session, MarginTrading, rows, ["trade_date"]
                )
                await session.commit()
            rows_written += written
            completed.update(row["trade_date"].isoformat() for row in rows)
        persist()
        logger.info(
            "批次 %d-%d：写入 %d 天（累计完成 %d，失败 %d）",
            offset + 1, offset + len(batch), len(rows),
            len(completed), len(failed),
        )

    logger.info(
        "融资买入额回补结束：完成 %d/%d，失败 %d，写入 %d 行",
        len(completed), len(days), len(failed), rows_written,
    )
    return rows_written


async def _start_for_trading_days(end: date, count: int) -> date | None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.is_open.is_(True))
            .where(TradeCalendar.cal_date <= end)
            .order_by(TradeCalendar.cal_date.desc())
            .limit(count)
        )).scalars().all()
    return min(rows) if rows else None


async def run(args) -> int:
    end = args.end or await _latest_open_day()
    if end is None:
        raise SystemExit("交易日历为空，请先执行 calendar 任务")
    if args.start is not None:
        margin_start = bond_start = args.start
    else:
        margin_start = max(
            _MARGIN_START,
            await _start_for_trading_days(end, args.days) or end,
        )
        # 国债收益率用于 750 日分位，尽量回补到 2022-09
        bond_start = max(date(2022, 9, 1), end - timedelta(days=365 * 5))
    if margin_start > end or bond_start > end:
        raise SystemExit(
            f"--start {margin_start} / bond {bond_start} 晚于 --end {end}"
        )

    plan = {
        "dataset": args.dataset,
        "margin_start": margin_start.isoformat(),
        "bond_start": bond_start.isoformat(),
        "end": end.isoformat(),
    }
    if args.dry_run:
        print(json.dumps({**plan, "checkpoint": str(_STATE), "state": _load_state()},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    if args.dataset in {"bond", "all"}:
        total = await _backfill_bond(bond_start, end)
        logger.info("国债收益率完成：%d 行", total)
        if total:
            async with AsyncSessionLocal() as session:
                latest = (await session.execute(
                    select(func.max(MacroRate.trade_date))
                )).scalar()
                await mark_success(
                    session, "macro_rates",
                    rows=total, coverage=1.0, trade_date=latest,
                    detail={"start": bond_start.isoformat(), "end": end.isoformat()},
                )
                await session.commit()
    if args.dataset in {"margin_buy", "all"}:
        written = await _backfill_margin_buy(margin_start, end, batch_days=args.batch_days)
        if written:
            async with AsyncSessionLocal() as session:
                latest = (await session.execute(
                    select(func.max(MarginTrading.trade_date))
                )).scalar()
                await mark_success(
                    session, "margin",
                    rows=written, coverage=1.0, trade_date=latest,
                    detail={"start": margin_start.isoformat(), "end": end.isoformat()},
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
    parser = argparse.ArgumentParser(description="宏观利率与两融买入额回补")
    parser.add_argument("--dataset", choices=["bond", "margin_buy", "all"], default="all")
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--days", type=int, default=750, help="未指定 start 时回补的交易日数")
    parser.add_argument("--batch-days", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days 必须 >= 1")
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
