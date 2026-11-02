"""按库内真实覆盖补记 `meta_data_quality`（只写元数据，不联网、不碰业务表）。

手工回补/导入脚本绕过 `run_task` 时不会更新新鲜度，导致数据明明是新的，
前端却显示 stale/unknown。本脚本只处理可验证的两类：

    sector_daily   <- fact_sector_daily 的最新交易日与当日行数
    sector_members <- dim_sector_member 的当前开区间行数与活跃股票数

安全边界：板块日线最新日期超过 10 天、当日不足 100 行、或开区间成分不足
1000 条时拒绝补记，避免把陈旧/半截数据标成新鲜。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/reconcile_freshness.py --dry-run
    ../.venv/Scripts/python.exe scripts/reconcile_freshness.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import func, select  # noqa: E402

from app.db.models.dim import DimSectorMember  # noqa: E402
from app.db.models.fact import FactSectorDaily  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

MAX_DATA_AGE_DAYS = 10
MIN_SECTOR_ROWS = 100
MIN_MEMBER_ROWS = 1000
MIN_ACTIVE_STOCKS = 1000


def _ensure_recent(last: date, today: date | None = None) -> None:
    today = today or date.today()
    if last < today - timedelta(days=MAX_DATA_AGE_DAYS):
        raise SystemExit(
            f"fact_sector_daily 最新日期 {last} 已超过 {MAX_DATA_AGE_DAYS} 天，"
            "拒绝补记；请先运行板块回补"
        )


async def _sector_daily(session) -> dict:
    last = (await session.execute(select(func.max(FactSectorDaily.trade_date)))).scalar()
    if last is None:
        raise SystemExit("fact_sector_daily 为空，拒绝补记")
    _ensure_recent(last)
    rows = int((await session.execute(
        select(func.count()).select_from(FactSectorDaily)
        .where(FactSectorDaily.trade_date == last)
    )).scalar() or 0)
    if rows < MIN_SECTOR_ROWS:
        raise SystemExit(f"{last} 板块日线仅 {rows} 行，拒绝补记")
    total = int((await session.execute(
        select(func.count()).select_from(FactSectorDaily)
    )).scalar() or 0)
    return {
        "rows": rows,
        "trade_date": last,
        "detail": {
            "reconciled": True,
            "source": "fact_sector_daily",
            "last_data_date": last.isoformat(),
            "total_rows": total,
            "note": "手工回补绕过 run_task；按库内实际覆盖补记",
        },
    }


async def _sector_members(session) -> dict:
    total = int((await session.execute(
        select(func.count()).select_from(DimSectorMember)
    )).scalar() or 0)
    open_rows = int((await session.execute(
        select(func.count()).select_from(DimSectorMember)
        .where(DimSectorMember.effective_to.is_(None))
    )).scalar() or 0)
    if open_rows < MIN_MEMBER_ROWS:
        raise SystemExit(f"当前开区间成分仅 {open_rows} 条，拒绝补记")
    active_stocks = int((await session.execute(
        select(func.count(func.distinct(DimSectorMember.stock_code)))
        .where(DimSectorMember.effective_to.is_(None))
    )).scalar() or 0)
    if active_stocks < MIN_ACTIVE_STOCKS:
        raise SystemExit(f"当前开区间仅覆盖 {active_stocks} 只股票，拒绝补记")
    baseline = (await session.execute(
        select(func.max(DimSectorMember.effective_from))
    )).scalar()
    return {
        "rows": open_rows,
        "trade_date": None,
        "detail": {
            "reconciled": True,
            "source": "dim_sector_member",
            "open_rows": open_rows,
            "total_rows": total,
            "active_stocks": active_stocks,
            "baseline_effective_from": baseline.isoformat() if baseline else None,
            "note": "SCD2 effective_from 是回测基准日而非导入日；按当前开区间补记",
        },
    }


RECONCILERS = {"sector_daily": _sector_daily, "sector_members": _sector_members}


async def amain(datasets: list[str], dry_run: bool) -> int:
    try:
        async with AsyncSessionLocal() as session:
            for dataset in datasets:
                payload = await RECONCILERS[dataset](session)
                if dry_run:
                    print(json.dumps(
                        {"dataset": dataset, **payload},
                        ensure_ascii=False, default=str,
                    ), flush=True)
                    continue
                await mark_success(
                    session, dataset, rows=payload["rows"],
                    trade_date=payload["trade_date"], detail=payload["detail"],
                )
                print(
                    f"[reconcile] {dataset} rows={payload['rows']} "
                    f"trade_date={payload['trade_date']}",
                    flush=True,
                )
    finally:
        await dispose_engines()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按库内真实覆盖补记数据新鲜度")
    parser.add_argument(
        "--dataset", action="append", choices=sorted(RECONCILERS),
        help="可重复；默认两个都补",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(amain(args.dataset or list(RECONCILERS), args.dry_run)))
