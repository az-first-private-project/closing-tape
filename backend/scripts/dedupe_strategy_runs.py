"""把同一 (algo, trade_date, 参数指纹) 的旧成功 run 标记为 superseded。

默认 dry-run，只打印统计；加 ``--apply`` 才写库。只改 status，不删 run 与
signal；只处理当前启用策略的非回测 run，历史停用策略默认跳过。

    ..\\.venv\\Scripts\\python.exe scripts\\dedupe_strategy_runs.py
    ..\\.venv\\Scripts\\python.exe scripts\\dedupe_strategy_runs.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import date, datetime
from typing import Iterable

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def plan_supersede(
    rows: Iterable[tuple[str, str, date, dict | None, datetime | None]],
) -> tuple[dict[tuple[str, date, str], str], list[str], Counter[str]]:
    """按“已排序（最新在前）”的 run 列表，每组保留第一条、其余待取代。"""
    from app.strategy.runner import params_fingerprint

    keep: dict[tuple[str, date, str], str] = {}
    stale: list[str] = []
    by_algo: Counter[str] = Counter()
    for run_id, algo_code, trade_date, params, _ in rows:
        key = (algo_code, trade_date, params_fingerprint(algo_code, params or {}))
        if key in keep:
            stale.append(run_id)
            by_algo[algo_code] += 1
        else:
            keep[key] = run_id
    return keep, stale, by_algo


async def _run(apply: bool) -> int:
    from sqlalchemy import select, update

    from app.db.models.strategy import StgRun
    from app.db.session import AsyncSessionLocal, dispose_engines
    from app.strategy.strategies import SPECS

    async with AsyncSessionLocal() as db:
        try:
            rows = (await db.execute(
                select(
                    StgRun.run_id,
                    StgRun.algo_code,
                    StgRun.trade_date,
                    StgRun.params,
                    StgRun.created_at,
                )
                .where(StgRun.is_backtest.is_(False))
                .where(StgRun.status == "success")
                .where(StgRun.algo_code.in_(sorted(SPECS)))
                .order_by(
                    StgRun.algo_code,
                    StgRun.trade_date,
                    StgRun.created_at.desc().nullslast(),
                    StgRun.run_id.desc(),
                )
            )).all()

            keep, stale, by_algo = plan_supersede(rows)

            print(
                f"扫描成功非回测 run={len(rows)} 有效组={len(keep)} "
                f"待取代={len(stale)}"
            )
            for algo_code, count in sorted(by_algo.items()):
                print(f"  {algo_code}: {count}")

            if not stale:
                return 0
            if not apply:
                print("dry-run：未写库；确认后加 --apply")
                return 0

            for offset in range(0, len(stale), 500):
                chunk = stale[offset:offset + 500]
                await db.execute(
                    update(StgRun)
                    .where(StgRun.run_id.in_(chunk))
                    .values(status="superseded")
                )
            await db.commit()
            print(f"已标记 superseded={len(stale)}")
            return 0
        finally:
            await dispose_engines()


def main() -> int:
    parser = argparse.ArgumentParser(description="标记重复的策略 run（不删除数据）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="实际写库")
    mode.add_argument("--dry-run", action="store_true", help="只读统计（默认）")
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
