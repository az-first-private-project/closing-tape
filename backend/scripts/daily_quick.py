"""
当日快速分析（Daily Quick）
===========================
回答一个很实际的问题：**收盘后想马上看到"今天哪些股票值得看"，最短路径是什么？**

全市场 5 年回补要 1–2 小时，不适合盘中/收盘后即时看。本脚本走"活跃股子集"路线：

    1. 从当日快照按成交额取 Top-N（默认 400）作为**观察池**
    2. 回补它们近 1 年日线（baostock/新浪，断点续传）
    3. 同步财报与分红 → 本地算 PE/PB/股息率（point-in-time）
    4. 算技术指标 → 个股因子评分 → 板块评分 → A1–A4 选股
    5. 打印结果摘要（可直接在终端看，也可以去前端 /algo-center 看同一份 run）

用法（backend 目录）：
    python scripts/daily_quick.py                 # 默认 Top 400、近 1 年
    python scripts/daily_quick.py --top 200
    python scripts/daily_quick.py --date 2026-10-22 --top 400
    python scripts/daily_quick.py --skip-backfill  # 已有历史时跳过采集

为什么是"子集"而不是全市场：
  - 技术指标需要 ≥60 个交易日历史；全市场回补约 1.5–2 小时（2 req/s 合规限频）
  - 观察池按成交额取前 N 只，覆盖了绝大多数流动性足够的标的，
    对"今天看什么"这个问题已经足够；夜间再跑全市场 `pipeline_cli.py all`
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import date, timedelta

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402

logger = get_logger("daily_quick")


async def _resolve_date(db, requested: date | None) -> date | None:
    from app.pipeline import resolve_trade_date
    resolved, _ = await resolve_trade_date(db, requested)
    return resolved


async def _top_codes(db, trade_date: date, limit: int) -> list[str]:
    """按成交额取当日最活跃的 N 只（观察池）。"""
    from app.db.models import FactStockDaily
    rows = (await db.execute(
        select(FactStockDaily.code)
        .where(FactStockDaily.trade_date == trade_date)
        .where(FactStockDaily.amount.isnot(None))
        .order_by(FactStockDaily.amount.desc())
        .limit(limit)
    )).all()
    return [r[0] for r in rows]


async def main() -> int:
    parser = argparse.ArgumentParser(description="当日快速分析（活跃股子集）")
    parser.add_argument("--date", help="交易日 YYYY-MM-DD，默认最新有快照的交易日")
    parser.add_argument("--top", type=int, default=400, help="观察池大小（按成交额）")
    parser.add_argument("--history-days", type=int, default=365, help="回补历史自然日数")
    parser.add_argument("--skip-backfill", action="store_true", help="跳过日线回补")
    parser.add_argument("--skip-valuation", action="store_true", help="跳过估值同步")
    args = parser.parse_args()

    setup_logging()
    started = time.perf_counter()
    summary: dict = {}

    from app.db.session import AsyncSessionLocal, dispose_engines

    try:
        async with AsyncSessionLocal() as db:
            trade_date = await _resolve_date(
                db, date.fromisoformat(args.date) if args.date else None
            )
            if trade_date is None:
                logger.error("无可用交易日（先跑 pipeline_cli.py calendar / snapshot）")
                return 1
            summary["trade_date"] = trade_date.isoformat()

            codes = await _top_codes(db, trade_date, args.top)
            if not codes:
                logger.error("%s 没有行情快照 → 先执行 pipeline_cli.py snapshot", trade_date)
                return 1
            summary["pool"] = len(codes)
            logger.info("观察池：%d 只（按 %s 成交额）", len(codes), trade_date)

        # ---- 1) 回补活跃股历史 ----
        if not args.skip_backfill:
            from app.ingest.service import backfill_stock_history
            start = trade_date - timedelta(days=args.history_days)
            t0 = time.perf_counter()
            result = await backfill_stock_history(codes=codes, start=start, end=trade_date)
            summary["backfill"] = {"rows": result.rows, "failed": result.failed,
                                   "seconds": round(time.perf_counter() - t0, 1)}
            logger.info("回补完成：%s 行，失败 %s 只，%.0fs",
                        result.rows, result.failed, time.perf_counter() - t0)

        # ---- 2) 估值（财报/分红缓存 + 本地计算）----
        if not args.skip_valuation:
            from app.ingest.valuation import (
                compute_valuation, sync_dividends, sync_financials,
            )
            t0 = time.perf_counter()
            fin = await sync_financials(codes=codes)
            div = await sync_dividends(codes=codes)
            val = await compute_valuation(
                trade_date - timedelta(days=args.history_days), trade_date, codes=codes
            )
            summary["valuation"] = {"financials": fin.rows, "dividends": div.rows,
                                    "updated": val.rows,
                                    "seconds": round(time.perf_counter() - t0, 1)}
            logger.info("估值完成：财报 %s / 分红 %s / 更新 %s 行，%.0fs",
                        fin.rows, div.rows, val.rows, time.perf_counter() - t0)

        # ---- 3) 指标 + 因子 + 板块 + 选股 ----
        from app.engine.features import compute_features_for_range
        from app.engine.sector_analysis import save_sector_scores, score_all_sectors
        from app.engine.stock_score import compute_stock_scores_range
        from app.pipeline import run_task
        from app.strategy.runner import run_strategy
        from app.strategy.strategies import SPECS

        start = trade_date - timedelta(days=args.history_days)
        t0 = time.perf_counter()
        summary["features"] = await compute_features_for_range(start, trade_date, codes=codes)
        summary["scores"] = await compute_stock_scores_range(start, trade_date)
        logger.info("指标 %s 行 / 因子 %s 行，%.0fs",
                    summary["features"], summary["scores"], time.perf_counter() - t0)

        # 板块评分（东财板块抓不到时用已有 THS 板块）
        try:
            rows = await score_all_sectors(trade_date)
            summary["sector_scores"] = await save_sector_scores(trade_date, rows)
        except Exception as exc:                       # noqa: BLE001
            logger.warning("板块评分失败（不阻断）：%s", exc)
            summary["sector_scores"] = f"failed: {exc}"

        signals: dict = {}
        for algo_code in SPECS:
            try:
                run = await run_strategy(algo_code, trade_date, {}, force=True)
                signals[algo_code] = {
                    "count": run.get("count", 0),
                    "run_id": (run.get("run") or {}).get("run_id"),
                    "top": [
                        {
                            "code": d["code"], "name": d["name"],
                            "score": d.get("score"),
                            "pe": (d.get("metrics") or {}).get("pe_ttm"),
                            "dv": (d.get("metrics") or {}).get("dv_ttm"),
                        }
                        for d in (run.get("data") or [])[:5]
                    ],
                }
            except Exception as exc:                   # noqa: BLE001
                signals[algo_code] = {"count": 0, "error": str(exc)[:160]}
        summary["signals"] = signals
    finally:
        await dispose_engines()

    summary["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    print("\n" + "=" * 60)
    print(f"当日快速分析完成：{summary.get('trade_date')}  用时 {summary['elapsed_seconds']}s")
    print("=" * 60)
    import json
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
