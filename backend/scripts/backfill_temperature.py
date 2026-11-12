"""市场温度历史回补（V7 T1 新口径）

从 `fact_market_daily` 一次性重算并 upsert `market_temperature_daily`。
标准化需要 750 日窗口，前 250 个交易日没有温度分（不写空行）。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_temperature.py --start 2023-09-01
    ../.venv/Scripts/python.exe scripts/backfill_temperature.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import FactMarketDaily, MarketTemperatureDaily  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.market_temperature import (  # noqa: E402
    _RAW_COLUMNS,
    save_temperature,
    temperature_rows,
)
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


async def run(args) -> int:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(FactMarketDaily).order_by(FactMarketDaily.trade_date)
        )).scalars().all()
        if not rows:
            raise SystemExit("fact_market_daily 为空，请先运行 backfill_market_factors.py")
        raw = pd.DataFrame([{
            column: getattr(row, column) for column in _RAW_COLUMNS
        } for row in rows])

        all_rows = temperature_rows(raw)
        start = args.start or raw["trade_date"].min()
        end = args.end or raw["trade_date"].max()
        payload = [
            row for row in all_rows
            if row["temp_score"] is not None and start <= row["trade_date"] <= end
        ]
        if args.dry_run:
            print(json.dumps({
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows": len(payload),
                "first": payload[0]["trade_date"].isoformat() if payload else None,
                "last": payload[-1]["trade_date"].isoformat() if payload else None,
            }, ensure_ascii=False, indent=2))
            return 0

        written = 0
        for offset in range(0, len(payload), 120):
            batch = payload[offset:offset + 120]
            for row in batch:
                await save_temperature(session, row)
            await session.commit()
            written += len(batch)
            logger.info("温度回补批次 %d-%d：累计 %d", offset + 1, offset + len(batch), written)

        if payload:
            await mark_success(
                session, "market_temperature",
                rows=written,
                coverage=1.0,
                trade_date=payload[-1]["trade_date"],
                detail={"start": start.isoformat(), "end": end.isoformat()},
            )
    logger.info("温度回补结束：%d 行", written)
    return 0


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="市场温度历史回补（V7 新口径）")
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
