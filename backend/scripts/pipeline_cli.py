"""
流水线命令行入口（方案 8 / 阶段 5：一条命令跑出选股结果）
=======================================================
在 `backend/` 目录执行：

    python scripts/pipeline_cli.py calendar          # 同步交易日历
    python scripts/pipeline_cli.py universe          # 同步股票池
    python scripts/pipeline_cli.py snapshot          # 当日全市场快照
    python scripts/pipeline_cli.py backfill          # 历史日线回补（断点续传）
    python scripts/pipeline_cli.py backfill --start 2025-10-21 --codes 600519,000001
    python scripts/pipeline_cli.py history_fast --start 2023-10-01   # 腾讯并发回补日线
    python scripts/pipeline_cli.py moneyflow_history --start 2024-01-01  # 新浪资金流
    python scripts/pipeline_cli.py valuation_fast --start 2023-10-01     # 东财 PE/PB
    python scripts/pipeline_cli.py rebuild --start 2025-07-01 --end 2026-10-23  # 重算指标+评分
    python scripts/pipeline_cli.py features          # 计算技术指标
    python scripts/pipeline_cli.py scores            # 个股因子评分
    python scripts/pipeline_cli.py sector_scores     # 板块三面评分
    python scripts/pipeline_cli.py algos             # 跑 A3/A4 + B1–B5 选股
    python scripts/pipeline_cli.py report            # 生成 AI 日报
    python scripts/pipeline_cli.py daily             # 全链路（等价 admin/ingest/all）
    python scripts/pipeline_cli.py all               # 同 daily

    # 回测（不写入 run 之外的额外状态）
    python scripts/pipeline_cli.py backtest a3 --start 2025-10-21 --end 2026-10-21
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


async def _run_task(args) -> int:
    from app.core.logging import setup_logging
    from app.db.session import AsyncSessionLocal, dispose_engines
    from app.pipeline import run_task

    setup_logging()
    task = "all" if args.task in {"daily", "all"} else args.task
    trade_date = _parse_date(args.date)
    start = _parse_date(args.start)
    end = _parse_date(args.end)

    if task == "backfill" and start is None:
        start = date(date.today().year - 1, date.today().month, date.today().day)
    async with AsyncSessionLocal() as db:
        try:
            if task == "demo":
                # 离线演示数据：确定性生成，不联网
                from app.fixtures.demo_data import seed_demo_data
                result = await seed_demo_data(db)
            else:
                result = await run_task(task, trade_date=trade_date, start=start,
                                        end=end, codes=args.codes, db=db)
            await db.commit()
        finally:
            await dispose_engines()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") not in {"failed"} else 1


async def _run_backtest(args) -> int:
    from app.core.config import BACKTEST
    from app.core.logging import setup_logging
    from app.db.session import AsyncSessionLocal, dispose_engines
    from app.strategy.backtest import run_backtest

    setup_logging()
    end = _parse_date(args.end) or date.today()
    start = _parse_date(args.start) or (end.replace(year=end.year - 1))
    bt_params = {
        "rebalance_days": args.rebalance_days or BACKTEST.rebalance_days,
        "max_positions": args.max_positions or BACKTEST.max_positions,
        "benchmark_code": args.benchmark or BACKTEST.benchmark_code,
    }

    async with AsyncSessionLocal() as db:
        try:
            result = await run_backtest(args.algo, start, end, {}, bt_params, db=db)
            await db.commit()
        finally:
            await dispose_engines()

    brief = {
        "bt_id": result.get("bt_id"),
        "algo": result.get("algo"),
        "range": f"{result.get('start_date')} → {result.get('end_date')}",
        "metrics": result.get("metrics"),
        "trades": len(result.get("trades") or []),
        "curve_points": len(result.get("equity_curve") or []),
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="GFNA V5 流水线 CLI",
        epilog="回测用法：python scripts/pipeline_cli.py backtest a3 --start 2025-10-21 --end 2026-10-21",
    )
    parser.add_argument("task", help="任务名；回测时为 'backtest'")
    parser.add_argument("algo", nargs="?", help="回测策略代码 a3/a4/b1..b5（仅 backtest 需要）")
    parser.add_argument("--date", help="目标交易日 YYYY-MM-DD")
    parser.add_argument("--start", help="起始日期（backfill / backtest）")
    parser.add_argument("--end", help="结束日期（backfill / backtest）")
    parser.add_argument("--codes", help="逗号分隔的股票代码（backfill）")
    # 回测参数
    parser.add_argument("--rebalance-days", type=int, dest="rebalance_days")
    parser.add_argument("--max-positions", type=int, dest="max_positions")
    parser.add_argument("--benchmark")

    args = parser.parse_args()

    if args.task == "backtest":
        if not args.algo:
            parser.error("回测需要指定策略代码：pipeline_cli.py backtest a3 …")
        return asyncio.run(_run_backtest(args))

    if args.task in {"backfill", "history_fast", "moneyflow_history", "valuation_fast",
                     "rebuild", "snapshot", "daily", "all", "features", "scores",
                     "sector_scores", "algos", "report", "calendar", "universe",
                     "index", "sector", "sector_members", "sector_fallback",
                     "moneyflow", "valuation", "margin", "demo"}:
        return asyncio.run(_run_task(args))

    parser.error(f"未知任务: {args.task}（回测用 backtest <algo>）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
