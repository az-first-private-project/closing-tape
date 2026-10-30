"""短线特征回填（B1–B5 所需列），带断点续传。

为什么单独一个脚本：新增的 EMA/KDJ/BOLL 带宽/振幅等列需要把 2024 年以来的
全市场特征表整体重算一遍（约 350 万行）。`compute_features_for_range` 虽然能跑，
但它是"一个调用跑完全市场"，中途被杀无法知道哪些股票已经落库。这里按**股票批**
调用它，每批返回（即逐块 upsert 已 commit）后才推进断点：

    计算并写库（内部每 800 行 commit） → 返回 → 更新断点（tmp + replace）

断点键 = (start, end)：区间一变，旧断点自动失效（换了结束日期必须重头对账，
否则会跳过未覆盖的新区间）。

为什么没有"empty"分类：本任务的数据源是库里已有的日线，候选股票就是从日线表
查出来的；不存在"源合法返回空"的情况。某批写入 0 行视为失败并保留重试，
避免把计算异常误记成"已确认无数据"而永久跳过。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_short_features.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_short_features.py
    ../.venv/Scripts/python.exe scripts/backfill_short_features.py --start 2024-01-02 --end 2026-10-26
    ../.venv/Scripts/python.exe scripts/backfill_short_features.py --force   # 忽略断点重跑
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

from sqlalchemy import func, select  # noqa: E402

from app.core import config  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.fact import FactFeatureDaily, FactStockDaily  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.features import compute_features_for_range  # noqa: E402

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_START = date(2024, 1, 2)
DEFAULT_BATCH = 40
MAX_CONSECUTIVE_FAILURES = 3
_STATE = Path(config.BACKEND_DIR) / "var" / "short_features_progress.json"
# 抽查覆盖度用的代表列（B4 最早可用列 + B3 的 KDJ）
_COVER_COLUMNS = (FactFeatureDaily.kdj_j, FactFeatureDaily.boll_width_pct60)


def _parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _load_state(start: date, end: date) -> dict:
    """读取断点；区间不匹配或文件损坏 → 视为无断点（宁可从零重跑）。"""
    try:
        payload = json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    saved_range = payload.get("range") or {}
    if saved_range.get("start") != start.isoformat() or saved_range.get("end") != end.isoformat():
        logger.warning(
            "断点区间 %s 与本次 %s~%s 不一致，忽略旧断点",
            saved_range, start, end,
        )
        return {}
    return payload


def _save_state(state: dict) -> None:
    """原子写：先写 .tmp 再 replace，半截文件不会冒充进度。

    Windows 上杀毒/索引器偶尔会短暂占用目标文件，os.replace 抛 WinError 5；
    短暂重试即可，不能因此把已提交的一批数据记成失败。
    """
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


async def _list_codes(start: date, end: date) -> list[str]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(FactStockDaily.code)
            .where(FactStockDaily.trade_date >= start, FactStockDaily.trade_date <= end)
            .distinct()
            .order_by(FactStockDaily.code)
        )
        return [str(code) for (code,) in result.all()]


async def _coverage(start: date, end: date) -> dict:
    """落库覆盖度抽查：非空代表列的行数 + 总行数。"""
    async with AsyncSessionLocal() as session:
        total = await session.scalar(
            select(func.count()).select_from(FactFeatureDaily).where(
                FactFeatureDaily.trade_date >= start, FactFeatureDaily.trade_date <= end
            )
        )
        values = {}
        for column in _COVER_COLUMNS:
            values[column.key] = await session.scalar(
                select(func.count()).select_from(FactFeatureDaily).where(
                    FactFeatureDaily.trade_date >= start,
                    FactFeatureDaily.trade_date <= end,
                    column.is_not(None),
                )
            )
    return {"rows": int(total or 0), **{key: int(val or 0) for key, val in values.items()}}


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[offset:offset + size] for offset in range(0, len(items), size)]


async def run(args) -> int:
    start = _parse_date(args.start) or DEFAULT_START
    end = await _resolve_end(_parse_date(args.end))
    if end < start:
        raise SystemExit(f"结束日期 {end} 早于开始日期 {start}")

    all_codes = await _list_codes(start, end)
    if not all_codes:
        raise SystemExit(f"区间 {start}~{end} 没有日线数据")

    state = {} if args.force else _load_state(start, end)
    completed = {str(code) for code in state.get("completed", [])}
    failed = {str(code) for code in state.get("failed", [])}
    rows_written = int(state.get("rows_written", 0))

    pending = [code for code in all_codes if code not in completed]
    logger.info(
        "短线特征回填：区间 %s~%s，股票 %d 只（断点跳过 %d，待处理 %d，上次失败 %d），每批 %d 只",
        start, end, len(all_codes), len(completed), len(pending), len(failed), args.batch_size,
    )
    if args.dry_run:
        print(json.dumps({
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "codes": len(all_codes),
            "completed": len(completed),
            "pending": len(pending),
            "failed": len(failed),
            "coverage": await _coverage(start, end),
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    def persist(extra_failed: set[str] | None = None) -> None:
        _save_state({
            "version": 1,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "completed": sorted(completed),
            "failed": sorted(failed | (extra_failed or set())),
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()  # 先落一份带区间的初始断点，避免意外退出后区间对不上

    consecutive_failures = 0
    started_at = time.perf_counter()
    batches = _chunks(pending, args.batch_size)
    for index, batch in enumerate(batches, start=1):
        batch_started = time.perf_counter()
        try:
            written = await compute_features_for_range(
                start, end, codes=batch, batch_size=len(batch)
            )
        except Exception as exc:  # noqa: BLE001 —— 单批失败不拖垮整轮，记失败后继续
            consecutive_failures += 1
            failed.update(batch)
            persist()
            logger.error(
                "批次 %d/%d 失败（%s: %s），已记入 failed，连续失败 %d 次",
                index, len(batches), type(exc).__name__, exc, consecutive_failures,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error("连续 %d 批失败，停止任务（断点已保存）", consecutive_failures)
                return 1
            continue

        consecutive_failures = 0
        if written <= 0:
            # 数据源是库内日线，0 行说明计算/落库异常，不能当作"确认无数据"跳过
            failed.update(batch)
            persist()
            logger.error("批次 %d/%d 写入 0 行，记入 failed 待重试", index, len(batches))
            continue

        rows_written += written
        completed.update(batch)
        failed.difference_update(batch)
        persist()
        logger.info(
            "批次 %d/%d 完成：%d 行，进度 %d/%d 只，累计 %d 行，用时 %.1fs",
            index, len(batches), written, len(completed), len(all_codes),
            rows_written, time.perf_counter() - batch_started,
        )

    elapsed = time.perf_counter() - started_at
    coverage = await _coverage(start, end)
    logger.info(
        "回填结束：区间 %s~%s，完成 %d/%d 只，失败 %d 只，累计写入 %d 行，用时 %.1f 分钟",
        start, end, len(completed), len(all_codes), len(failed), rows_written, elapsed / 60,
    )
    logger.info("落库覆盖度：%s", coverage)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="短线特征回填（B1–B5，断点续传）")
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=None, help="默认取日线表最新交易日")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--force", action="store_true", help="忽略断点，全量重跑")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与断点状态")
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size 必须为正整数")

    async def _main() -> int:
        try:
            return await run(args)
        finally:
            await dispose_engines()

    setup_logging()
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
