"""板块三面评分历史回填 / 覆盖修复。

管线只算目标交易日；历史日期从未跑过评分时，仪表盘历史日期会全部显示“未评分”。
本脚本按 `fact_sector_daily` 实际有数据的交易日逐日补算，并可选清理“当日没有
板块行情”的孤儿评分（早期演示数据残留）。

断点策略（与 backfill_temperature.py 一致）：

    逐日计算 + 提交 → 整批写断点（tmp + replace）

启动时用库内真实覆盖与断点对账，避免“断点说完成、库里其实没数据”。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_sector_scores.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_sector_scores.py --start 2026-10-01 --end 2026-10-30
    ../.venv/Scripts/python.exe scripts/backfill_sector_scores.py --prune-orphans --start 2026-10-21 --end 2026-10-21 --force
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

from sqlalchemy import delete, exists, func, select  # noqa: E402

from app.core import config  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import FactSectorDaily, SectorScore  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.sector_analysis import compute_and_save_sector_scores  # noqa: E402
from app.pipeline import mark_success  # noqa: E402

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE = Path(config.BACKEND_DIR) / "var" / "sector_score_backfill_progress.json"
DEFAULT_LOOKBACK_DAYS = 365


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD：{text}") from exc


def _save_state(state: dict) -> None:
    """原子写断点；Windows 上 os.replace 偶发被占用，短暂重试。"""
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


def _needs_compute(daily_count: int, scored_count: int, forced: bool) -> bool:
    """有板块行情但评分行数不足 → 需要补算。"""
    return forced or scored_count < daily_count


def _needs_prune(orphan_count: int, prune_orphans: bool) -> bool:
    """当日存在没有板块行情的评分 → 仅显式开启时清理。"""
    return prune_orphans and orphan_count > 0


async def _latest_daily_day(end: date | None) -> date | None:
    async with AsyncSessionLocal() as session:
        stmt = select(func.max(FactSectorDaily.trade_date))
        if end is not None:
            stmt = stmt.where(FactSectorDaily.trade_date <= end)
        return (await session.execute(stmt)).scalar()


async def _coverage_stats(start: date, end: date) -> dict[date, dict]:
    """逐日统计：板块日线数、已有有效评分、孤儿评分。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                FactSectorDaily.trade_date,
                func.count(FactSectorDaily.sector_code).label("daily_count"),
                func.count(SectorScore.sector_code).filter(
                    SectorScore.composite_score.isnot(None)
                ).label("scored_count"),
            )
            .outerjoin(
                SectorScore,
                (SectorScore.trade_date == FactSectorDaily.trade_date)
                & (SectorScore.sector_code == FactSectorDaily.sector_code),
            )
            .where(FactSectorDaily.trade_date >= start)
            .where(FactSectorDaily.trade_date <= end)
            .group_by(FactSectorDaily.trade_date)
            .order_by(FactSectorDaily.trade_date)
        )).all()

        orphan_rows = (await session.execute(
            select(SectorScore.trade_date, func.count(SectorScore.sector_code))
            .outerjoin(
                FactSectorDaily,
                (FactSectorDaily.trade_date == SectorScore.trade_date)
                & (FactSectorDaily.sector_code == SectorScore.sector_code),
            )
            .where(SectorScore.trade_date >= start)
            .where(SectorScore.trade_date <= end)
            .where(FactSectorDaily.sector_code.is_(None))
            .group_by(SectorScore.trade_date)
        )).all()

    stats = {
        row.trade_date: {
            "daily": int(row.daily_count),
            "scored": int(row.scored_count),
            "orphans": 0,
        }
        for row in rows
    }
    for trade_date, orphan_count in orphan_rows:
        if trade_date in stats:
            stats[trade_date]["orphans"] = int(orphan_count)
    return stats


async def _prune_orphan_scores(session, trade_date: date) -> int:
    """删除当日没有板块行情对应的评分行（演示数据/已消失板块残留）。"""
    orphan_exists = exists(
        select(1).select_from(FactSectorDaily).where(
            FactSectorDaily.trade_date == trade_date,
            FactSectorDaily.sector_code == SectorScore.sector_code,
        )
    )
    result = await session.execute(
        delete(SectorScore).where(
            SectorScore.trade_date == trade_date,
            ~orphan_exists,
        )
    )
    return int(result.rowcount or 0)


async def _latest_scored_day() -> date | None:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(func.max(SectorScore.trade_date)))).scalar()


async def run(args) -> int:
    end = args.end or await _latest_daily_day(None)
    if end is None:
        raise SystemExit("fact_sector_daily 为空，请先补齐板块日线")
    start = args.start or (end - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    if start > end:
        raise SystemExit(f"--start {start} 晚于 --end {end}")

    stats = await _coverage_stats(start, end)
    if not stats:
        print(json.dumps({"message": "区间内没有板块日线", "start": str(start),
                          "end": str(end)}, ensure_ascii=False))
        return 0

    state = {} if args.force else _load_state()
    completed: dict[str, dict] = dict(state.get("completed") or {})
    failed: dict[str, str] = dict(state.get("failed") or {})
    rows_written = int(state.get("rows_written") or 0)
    pruned_total = int(state.get("pruned_total") or 0)

    # 断点对账：断点说完成不代表库里仍然完整
    for key in list(completed):
        try:
            day = _parse_date(key)
        except argparse.ArgumentTypeError:
            completed.pop(key, None)
            continue
        now = stats.get(day)
        if now is None:
            continue
        if _needs_compute(now["daily"], now["scored"], False) or _needs_prune(
            now["orphans"], args.prune_orphans
        ):
            logger.warning("断点显示 %s 已完成，但库内覆盖不完整，重新处理", key)
            completed.pop(key, None)

    plan: list[dict] = []
    for day, item in stats.items():
        need_compute = _needs_compute(item["daily"], item["scored"], args.force)
        need_prune = _needs_prune(item["orphans"], args.prune_orphans)
        if not need_compute and not need_prune:
            continue
        plan.append({
            "date": day,
            "daily": item["daily"],
            "scored": item["scored"],
            "orphans": item["orphans"],
            "compute": need_compute,
            "prune": need_prune,
        })

    logger.info(
        "板块评分回填：区间 %s ~ %s，有行情 %d 天，待处理 %d 天，已完成 %d，失败 %d",
        start, end, len(stats), len(plan), len(completed), len(failed),
    )

    if args.dry_run:
        print(json.dumps({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trade_days": len(stats),
            "pending_count": len(plan),
            "completed": len(completed),
            "failed": failed,
            "pending": [
                {**item, "date": item["date"].isoformat()} for item in plan
            ],
            "rows_written": rows_written,
            "pruned_total": pruned_total,
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    def persist() -> None:
        _save_state({
            "version": 1,
            "completed": completed,
            "failed": failed,
            "rows_written": rows_written,
            "pruned_total": pruned_total,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()
    started_at = time.perf_counter()

    for offset in range(0, len(plan), args.batch_days):
        batch = plan[offset:offset + args.batch_days]
        batch_started = time.perf_counter()
        done: dict[str, dict] = {}

        for item in batch:
            day: date = item["date"]
            key = day.isoformat()
            try:
                async with AsyncSessionLocal() as session:
                    written = 0
                    if item["compute"]:
                        written = await compute_and_save_sector_scores(day, db=session)
                    pruned = 0
                    if item["prune"]:
                        pruned = await _prune_orphan_scores(session, day)
                    # 先提交，再记断点：避免“断点说完成、库里其实回滚了”
                    await session.commit()
            except Exception as exc:                      # noqa: BLE001 —— 单日失败不阻断整批
                failed[key] = f"{type(exc).__name__}: {exc}"
                logger.exception("板块评分回填失败 %s", key)
                continue
            done[key] = {"written": written, "pruned": pruned}
            rows_written += written
            pruned_total += pruned
            failed.pop(key, None)

        completed.update(done)
        persist()
        logger.info(
            "批次 %d-%d 完成：处理 %d 天，用时 %.1fs（累计写 %d 行，清理 %d 行）",
            offset + 1, offset + len(batch), len(done),
            time.perf_counter() - batch_started, rows_written, pruned_total,
        )

    if completed:
        latest = await _latest_scored_day()
        async with AsyncSessionLocal() as session:
            await mark_success(
                session, "sector_scores",
                rows=rows_written,
                coverage=round(len(completed) / len(stats), 4),
                trade_date=latest,
                detail={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "days": len(completed),
                    "pruned_total": pruned_total,
                    "failed": len(failed),
                },
            )

    logger.info(
        "板块评分回填结束：完成 %d/%d，清理孤儿评分 %d，失败 %d，用时 %.1f 分钟",
        len(completed), len(plan), pruned_total, len(failed),
        (time.perf_counter() - started_at) / 60,
    )
    return 0 if not failed else 1


async def _amain(args) -> int:
    try:
        return await run(args)
    finally:
        await dispose_engines()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="板块三面评分历史回填 / 覆盖修复")
    parser.add_argument("--start", type=_parse_date, default=None,
                        help=f"起始日（默认 end - {DEFAULT_LOOKBACK_DAYS} 天）")
    parser.add_argument("--end", type=_parse_date, default=None,
                        help="截止日（默认 fact_sector_daily 最新一天）")
    parser.add_argument("--batch-days", type=int, default=5,
                        help="每批处理的交易日数（默认 5，批末写断点）")
    parser.add_argument("--force", action="store_true", help="忽略断点与覆盖，全量重算")
    parser.add_argument("--prune-orphans", action="store_true",
                        help="清理当日没有板块行情对应的评分行")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与覆盖差异")
    args = parser.parse_args()
    if args.batch_days < 1:
        parser.error("--batch-days 必须 >= 1")
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
