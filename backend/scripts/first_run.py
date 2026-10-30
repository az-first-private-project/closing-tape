"""
首次启动初始化（容器/新环境专用）
=================================
`bootstrap_db.py` 只建结构，不产生任何行情数据 —— 这就是"启动后页面空白"的真正原因
（与 Playwright 无关；Playwright 只影响新闻爬虫）。

本脚本做的是"让新环境一启动就有东西看"：
  1. 若数据库**完全没有行情**（fact_stock_daily 为空）：灌入确定性演示数据；
  2. 依次计算 指标 → 个股因子 → 板块评分 → 选股；
  3. 若已有真实数据则**完全跳过**（幂等、不会污染生产库）。

用法：
    python scripts/first_run.py                  # 空库才执行
    python scripts/first_run.py --force          # 强制重灌演示数据（危险：会覆盖同期行）
环境变量：
    SEED_DEMO_ON_EMPTY=1   允许空库自动灌演示数据（compose 默认开启）
    GFNA_DEMO_DAYS=250     演示数据天数
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import func, select  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402

logger = get_logger("first_run")


async def _has_market_data(db) -> bool:
    from app.db.models import FactStockDaily
    count = (await db.execute(select(func.count()).select_from(FactStockDaily))).scalar()
    return bool(count)


async def init_demo(db, days: int) -> dict:
    """灌演示数据 + 跑完整链路，返回每步行数。"""
    from app.fixtures.demo_data import seed_demo_data

    result: dict[str, object] = {}
    result["demo"] = await seed_demo_data(db, days=days)

    from app.db.models import FactStockDaily
    latest = (await db.execute(select(func.max(FactStockDaily.trade_date)))).scalar()
    if latest is None:
        return result

    from app.engine.features import compute_features_for_date
    from app.engine.sector_analysis import save_sector_scores, score_all_sectors
    from app.engine.stock_score import compute_stock_scores
    from app.strategy.runner import run_strategy
    from app.strategy.strategies import SPECS

    result["features"] = await compute_features_for_date(latest, db=db)
    result["scores"] = await compute_stock_scores(latest, db=db)
    rows = await score_all_sectors(latest, db=db)
    result["sector_scores"] = await save_sector_scores(latest, rows, db=db)

    signals = {}
    for algo_code in SPECS:
        try:
            run = await run_strategy(algo_code, latest, {}, force=True, db=db)
            signals[algo_code] = run.get("count", 0)
        except Exception as exc:                      # noqa: BLE001
            logger.warning("策略 %s 初始化失败（不阻断）: %s", algo_code, exc)
            signals[algo_code] = f"failed: {exc}"
    result["algos"] = signals
    await db.commit()
    result["trade_date"] = latest.isoformat()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="GFNA 首次启动初始化")
    parser.add_argument("--force", action="store_true", help="强制重灌演示数据")
    args = parser.parse_args()

    setup_logging()
    seed_allowed = os.getenv("SEED_DEMO_ON_EMPTY", "1").lower() in {"1", "true", "yes", "on"}
    days = int(os.getenv("GFNA_DEMO_DAYS", "250"))

    from app.db.session import AsyncSessionLocal, dispose_engines

    async def _run() -> int:
        try:
            async with AsyncSessionLocal() as db:
                has_data = await _has_market_data(db)
                if has_data and not args.force:
                    logger.info("已有行情数据，跳过首次初始化（不会覆盖）")
                    return 0
                if not seed_allowed and not args.force:
                    logger.info("SEED_DEMO_ON_EMPTY=0 且库为空：跳过灌数据（请手动采集）")
                    return 0
                logger.info("空库检测通过，开始灌入演示数据并计算指标/评分/选股 …")
                result = await init_demo(db, days)
                logger.info("首次初始化完成: %s", result)
                return 0
        finally:
            await dispose_engines()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
