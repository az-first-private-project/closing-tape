"""市场状态机历史回补（V7 T1）

一次性重算并 upsert `market_regime_daily`。标准化需要 750 日窗口，
前 250 个交易日没有状态（不写行）。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_regime.py --start 2023-09-01
    ../.venv/Scripts/python.exe scripts/backfill_regime.py --dry-run
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

from sqlalchemy import select  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import FactMarketDaily, MarketRegimeDaily  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.market_regime import compute_regime_range  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


async def run(args) -> int:
    async with AsyncSessionLocal() as session:
        bounds = (await session.execute(
            select(FactMarketDaily.trade_date)
            .order_by(FactMarketDaily.trade_date)
        )).scalars().all()
        if not bounds:
            raise SystemExit("fact_market_daily 为空，请先运行 backfill_market_factors.py")
        start = args.start or bounds[0]
        end = args.end or bounds[-1]
        existing = (await session.execute(
            select(MarketRegimeDaily.trade_date)
            .where(MarketRegimeDaily.trade_date >= start)
            .where(MarketRegimeDaily.trade_date <= end)
        )).scalars().all()
        if args.dry_run:
            print(json.dumps({
                "start": start.isoformat(),
                "end": end.isoformat(),
                "existing_rows": len(existing),
            }, ensure_ascii=False, indent=2))
            return 0

        written = await compute_regime_range(start, end, db=session)
        await session.commit()
        if written:
            last = (await session.execute(
                select(MarketRegimeDaily.trade_date)
                .order_by(MarketRegimeDaily.trade_date.desc())
                .limit(1)
            )).scalar()
            await mark_success(
                session, "market_regime",
                rows=written, coverage=1.0, trade_date=last,
                detail={"start": start.isoformat(), "end": end.isoformat()},
            )
            await session.commit()
    logger.info("状态机回补结束：%d 行", written)
    return 0


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="市场状态机历史回补（V7 T1）")
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
