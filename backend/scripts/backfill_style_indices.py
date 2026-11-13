"""风格轮动指数历史回补（V7 T3）

创业板50（sz399673，新浪）+ 红利低波50（H30269，中证指数）历史日线，
写入 `market_indices`。两个源独立降级，单源失败不阻断另一个。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_style_indices.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_style_indices.py --start 2014-06-01
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import func, select  # noqa: E402

from app.core.constants import STYLE_INDEX_MAP  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import MarketIndex  # noqa: E402
from app.db.models.meta import TradeCalendar  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.ingest.base import upsert_rows  # noqa: E402
from app.ingest.service import get_provider_chain  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)
DEFAULT_START = date(2014, 6, 1)


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


async def _latest_open_day() -> date | None:
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.is_open.is_(True))
            .where(TradeCalendar.cal_date <= date.today())
            .order_by(TradeCalendar.cal_date.desc())
            .limit(1)
        )).scalar()


async def run(args) -> int:
    end = args.end or await _latest_open_day() or date.today()
    start = args.start or DEFAULT_START
    if start > end:
        raise SystemExit(f"--start {start} 晚于 --end {end}")

    chain = get_provider_chain()
    fetched = await chain.fetch_style_indices(start, end)
    frame = fetched.detail.get("data")
    rows = []
    if frame is not None and not getattr(frame, "empty", True):
        for record in frame.to_dict("records"):
            code = str(record.get("index_code") or "")
            if code not in STYLE_INDEX_MAP:
                continue
            day = record.get("trade_date")
            close = record.get("close")
            if day is None or close is None:
                continue
            rows.append({
                "index_code": code,
                "index_name": record.get("index_name") or STYLE_INDEX_MAP[code],
                "trade_date": day if isinstance(day, date) else date.fromisoformat(str(day)[:10]),
                "open": record.get("open"),
                "high": record.get("high"),
                "low": record.get("low"),
                "close": close,
                "pre_close": record.get("pre_close"),
                "pct_chg": record.get("pct_chg"),
                "volume": int(record["volume"]) if record.get("volume") is not None else None,
                "amount": record.get("amount"),
                "src": str(record.get("src") or "akshare")[:16],
            })

    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": len(rows),
        "sources": fetched.detail.get("sources"),
        "failed": fetched.failed,
        "codes": sorted({row["index_code"] for row in rows}),
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        raise SystemExit("两个指数都没有取到数据")

    async with AsyncSessionLocal() as session:
        written = await upsert_rows(
            session, MarketIndex, rows, ["index_code", "trade_date"]
        )
        await session.commit()
        latest = (await session.execute(
            select(func.max(MarketIndex.trade_date)).where(
                MarketIndex.index_code.in_(tuple(STYLE_INDEX_MAP))
            )
        )).scalar()
        await mark_success(
            session, "style_indices",
            rows=written, coverage=1.0, trade_date=latest,
            detail=summary,
        )
        await session.commit()
    logger.info("风格指数回补完成：%s", summary)
    return 0 if fetched.failed == 0 else 1


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="创业板50 / 红利低波50 历史回补")
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
