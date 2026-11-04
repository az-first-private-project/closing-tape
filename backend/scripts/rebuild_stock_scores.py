"""个股因子评分历史重建（逐日断点、失败隔离）。

为什么需要单独脚本：`run_task("scores", start, end)` 能一次跑完区间，但 700 个
交易日、300 多万行 upsert 中途被杀后无法知道哪些天已经落库；某一天异常会让整个
区间任务失败。这里按**交易日**逐日调用 `compute_stock_scores`（函数内部每 800 行
commit），返回后才推进断点：

    单日计算并写库（已提交） → 更新断点（tmp + replace）

断点键 = (start, end)：区间一变旧断点自动失效，换区间必须重新对账。

历史评分必须整段重算而不是只补缺失代码：分位是横截面概念，补回一批股票（例如
920 北交所）会改变全市场所有股票的分位，只补新代码会留下"新旧口径混用"。

`--prune-orphans` 清理"日期下没有任何行情"的评分行（历史 seed/演示残留），
执行前总是先打印待删日期与行数。

不写 meta_data_quality：新鲜度由日更管线负责，历史修复不应把 stock_scores 的
"最近成功/行数"覆盖成一次区间回填的数字。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/rebuild_stock_scores.py --dry-run
    ../.venv/Scripts/python.exe scripts/rebuild_stock_scores.py --start 2024-01-02 --end 2026-11-03
    ../.venv/Scripts/python.exe scripts/rebuild_stock_scores.py --prune-orphans
    ../.venv/Scripts/python.exe scripts/rebuild_stock_scores.py --force   # 忽略断点重跑
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import func, select, text  # noqa: E402

from app.core import config  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import FactStockDaily, FactStockScoreDaily  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.stock_score import compute_stock_scores  # noqa: E402

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_START = date(2024, 1, 2)
MAX_CONSECUTIVE_FAILURES = 3
_STATE = Path(config.BACKEND_DIR) / "var" / "stock_scores_progress.json"

ORPHAN_PREVIEW_SQL = text("""
    SELECT s.trade_date, count(*) AS rows
    FROM fact_stock_score_daily s
    WHERE NOT EXISTS (
        SELECT 1 FROM fact_stock_daily d WHERE d.trade_date = s.trade_date
    )
    GROUP BY s.trade_date ORDER BY s.trade_date
""")
ORPHAN_DELETE_SQL = text("""
    DELETE FROM fact_stock_score_daily s
    WHERE NOT EXISTS (
        SELECT 1 FROM fact_stock_daily d WHERE d.trade_date = s.trade_date
    )
""")


def _parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _load_state(start: date, end: date) -> dict:
    try:
        payload = json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    saved = payload.get("range") or {}
    if saved.get("start") != start.isoformat() or saved.get("end") != end.isoformat():
        logger.warning("断点区间 %s 与本次 %s~%s 不一致，忽略旧断点", saved, start, end)
        return {}
    return payload


def _save_state(state: dict) -> None:
    """原子写；Windows 上 replace 偶发 WinError 5，短暂重试即可。"""
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


async def _resolve_end(explicit: date | None) -> date:
    if explicit is not None:
        return explicit
    async with AsyncSessionLocal() as session:
        value = await session.scalar(select(func.max(FactStockDaily.trade_date)))
    if value is None:
        raise SystemExit("fact_stock_daily 没有任何日线数据，请先回补行情")
    return value


async def _list_dates(start: date, end: date) -> list[date]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(FactStockDaily.trade_date)
            .where(FactStockDaily.trade_date >= start, FactStockDaily.trade_date <= end)
            .distinct()
            .order_by(FactStockDaily.trade_date)
        )
        return [row[0] for row in result.all()]


async def _orphan_preview() -> list[tuple[date, int]]:
    async with AsyncSessionLocal() as session:
        return [(row[0], int(row[1])) for row in (await session.execute(ORPHAN_PREVIEW_SQL)).all()]


async def _prune_orphans() -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(ORPHAN_DELETE_SQL)
        await session.commit()
        return int(result.rowcount or 0)


async def _coverage(start: date, end: date) -> dict:
    async with AsyncSessionLocal() as session:
        dates = await session.scalar(
            select(func.count(func.distinct(FactStockScoreDaily.trade_date))).where(
                FactStockScoreDaily.trade_date >= start,
                FactStockScoreDaily.trade_date <= end,
            )
        )
        total = await session.scalar(
            select(func.count()).select_from(FactStockScoreDaily).where(
                FactStockScoreDaily.trade_date >= start,
                FactStockScoreDaily.trade_date <= end,
            )
        )
        by_year = (await session.execute(text("""
            SELECT date_part('year', trade_date)::int AS y, count(*) AS rows,
                   count(DISTINCT code) AS codes, count(DISTINCT trade_date) AS days,
                   count(*) FILTER (WHERE code LIKE '920%') AS bj_rows
            FROM fact_stock_score_daily
            WHERE trade_date BETWEEN :s AND :e
            GROUP BY 1 ORDER BY 1
        """), {"s": start, "e": end})).all()
    return {
        "dates": int(dates or 0),
        "rows": int(total or 0),
        "by_year": [list(map(int, row)) for row in by_year],
    }


async def run(args) -> int:
    start = _parse_date(args.start) or DEFAULT_START
    end = await _resolve_end(_parse_date(args.end))
    if end < start:
        raise SystemExit(f"结束日期 {end} 早于开始日期 {start}")

    dates = await _list_dates(start, end)
    if not dates:
        raise SystemExit(f"区间 {start}~{end} 没有日线数据")

    state = {} if args.force else _load_state(start, end)
    completed = {str(d) for d in state.get("completed", [])}
    failed = {str(d) for d in state.get("failed", [])}
    rows_written = int(state.get("rows_written", 0))

    pending = [d for d in dates if d.isoformat() not in completed]
    logger.info(
        "评分重建：区间 %s~%s，交易日 %d（断点跳过 %d，待处理 %d，上次失败 %d）",
        start, end, len(dates), len(completed), len(pending), len(failed),
    )

    orphans = await _orphan_preview()
    orphan_rows = sum(n for _, n in orphans)
    logger.info(
        "无行情日期的残留评分：%d 个日期 / %d 行%s",
        len(orphans), orphan_rows,
        "".join(f"\n  {d} -> {n}" for d, n in orphans[:30])
        + ("\n  ..." if len(orphans) > 30 else ""),
    )

    if args.dry_run:
        print(json.dumps({
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "trade_dates": len(dates),
            "completed": len(completed),
            "pending": len(pending),
            "failed": len(failed),
            "orphan_dates": [[d.isoformat(), n] for d, n in orphans],
            "coverage": await _coverage(start, end),
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.prune_orphans:
        deleted = await _prune_orphans()
        logger.info("已删除无行情日期的残留评分 %d 行（预览 %d 行）", deleted, orphan_rows)

    def persist(extra_failed: set[str] | None = None) -> None:
        _save_state({
            "version": 1,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "completed": sorted(completed),
            "failed": sorted(failed | (extra_failed or set())),
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()

    consecutive_failures = 0
    started_at = time.perf_counter()
    for index, day in enumerate(pending, start=1):
        day_started = time.perf_counter()
        try:
            written = await compute_stock_scores(day)
        except Exception as exc:  # noqa: BLE001 —— 单日失败不拖垮整轮
            consecutive_failures += 1
            failed.add(day.isoformat())
            persist()
            logger.error(
                "第 %d/%d 天 %s 失败（%s: %s），连续失败 %d 次",
                index, len(pending), day, type(exc).__name__, exc, consecutive_failures,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error("连续 %d 天失败，停止任务（断点已保存）", consecutive_failures)
                return 1
            continue

        consecutive_failures = 0
        rows_written += written
        completed.add(day.isoformat())
        failed.discard(day.isoformat())
        persist()
        logger.info(
            "第 %d/%d 天 %s 完成：%d 行，累计 %d 行，用时 %.1fs",
            index, len(pending), day, written, rows_written,
            time.perf_counter() - day_started,
        )

    elapsed = time.perf_counter() - started_at
    coverage = await _coverage(start, end)
    logger.info(
        "评分重建结束：区间 %s~%s，完成 %d/%d 天，失败 %d 天，累计写入 %d 行，用时 %.1f 分钟",
        start, end, len(completed), len(dates), len(failed), rows_written, elapsed / 60,
    )
    logger.info("落库覆盖：%s", coverage)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="个股因子评分历史重建（逐日断点）")
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None, help="默认取日线表最新交易日")
    parser.add_argument("--force", action="store_true", help="忽略断点，全量重跑")
    parser.add_argument("--prune-orphans", action="store_true",
                        help="删除日期下没有任何行情的评分行（执行前打印预览）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划、残留与覆盖")
    args = parser.parse_args()

    async def _main() -> int:
        try:
            return await run(args)
        finally:
            await dispose_engines()

    setup_logging()
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
