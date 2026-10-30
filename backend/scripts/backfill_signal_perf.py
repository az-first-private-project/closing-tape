"""信号前瞻收益历史回填（V6 T5）

把 `stg_signal_perf` 从 `config.HISTORY_START` 补齐到最近交易日。为什么需要脚本：
管线每天只重算最近 25 个交易日，历史信号（以及之后才走完的 5/10/20 日窗口）
必须靠一次回填补齐。

分段策略：

  - 按交易日切成 `--chunk-days` 大小的区间，每段调用同一个
    `recompute_signal_perf(start, end)`（先算后删再插，区间内幂等）；
  - 每段提交后写断点，并把断点里记录的行数和库里实际行数对账，
    不一致就视为未完成；
  - 中途被杀掉最多重跑一段，不会留下半段数据。

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/backfill_signal_perf.py --dry-run
    ../.venv/Scripts/python.exe scripts/backfill_signal_perf.py
    ../.venv/Scripts/python.exe scripts/backfill_signal_perf.py --start 2025-01-01 --end 2026-10-26
    ../.venv/Scripts/python.exe scripts/backfill_signal_perf.py --force
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

from sqlalchemy import func, select  # noqa: E402

from app.core import config  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models.meta import TradeCalendar  # noqa: E402
from app.db.models.strategy import StgRun, StgSignalPerf  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine.signal_perf import recompute_signal_perf  # noqa: E402
from app.pipeline import mark_success  # noqa: E402
from app.strategy.strategies import SPECS  # noqa: E402

logger = get_logger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE = Path(config.BACKEND_DIR) / "var" / "signal_perf_backfill_progress.json"


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


async def _latest_open_day() -> date | None:
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.is_open.is_(True))
            .where(TradeCalendar.cal_date <= date.today())
            .order_by(TradeCalendar.cal_date.desc())
            .limit(1)
        )).scalar()


async def _trade_days(start: date, end: date) -> list[date]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(TradeCalendar.cal_date)
            .where(TradeCalendar.is_open.is_(True))
            .where(TradeCalendar.cal_date >= start)
            .where(TradeCalendar.cal_date <= end)
            .order_by(TradeCalendar.cal_date)
        )).scalars().all()
    return list(rows)


async def _row_count(start: date, end: date, algos: list[str]) -> int:
    async with AsyncSessionLocal() as session:
        return int((await session.execute(
            select(func.count()).select_from(StgSignalPerf).where(
                StgSignalPerf.algo_code.in_(algos),
                StgSignalPerf.signal_date >= start,
                StgSignalPerf.signal_date <= end,
            )
        )).scalar_one())


async def _run_count(start: date, end: date, algos: list[str]) -> int:
    """断点对账用：区间内非回测成功 run 数变化 → 该段需要重算。"""
    async with AsyncSessionLocal() as session:
        return int((await session.execute(
            select(func.count()).select_from(StgRun).where(
                StgRun.algo_code.in_(algos),
                StgRun.trade_date >= start,
                StgRun.trade_date <= end,
                StgRun.is_backtest.is_(False),
                StgRun.status == "success",
            )
        )).scalar_one())


def _chunks(days: list[date], size: int) -> list[tuple[date, date]]:
    return [
        (block[0], block[-1])
        for offset in range(0, len(days), size)
        if (block := days[offset:offset + size])
    ]


async def run(args) -> int:
    end = args.end or await _latest_open_day()
    if end is None:
        raise SystemExit("交易日历为空，请先执行 calendar 任务")
    start = args.start or config.HISTORY_START
    if start > end:
        raise SystemExit(f"--start {start} 晚于 --end {end}")

    algos = (
        sorted({item.strip().lower() for item in args.algos.split(",") if item.strip()})
        if args.algos else sorted(SPECS)
    )
    fingerprint = ",".join(algos)

    days = await _trade_days(start, end)
    if not days:
        print(json.dumps({"message": "区间内没有交易日", "start": str(start), "end": str(end)},
                         ensure_ascii=False))
        return 0

    state = {} if args.force else _load_state()
    current_range = [start.isoformat(), end.isoformat()]
    if state.get("algos") != fingerprint or state.get("range") != current_range:
        state = {}
    completed: dict[str, dict] = dict(state.get("completed") or {})
    failed: dict[str, str] = dict(state.get("failed") or {})
    rows_written = int(state.get("rows_written") or 0)
    chunks = _chunks(days, args.chunk_days)

    logger.info(
        "信号前瞻回填：区间 %s ~ %s，交易日 %d，分段 %d，策略 %s",
        start, end, len(days), len(chunks), fingerprint,
    )
    if args.dry_run:
        print(json.dumps({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trade_days": len(days),
            "chunks": [f"{chunk_start}~{chunk_end}" for chunk_start, chunk_end in chunks],
            "completed": len(completed),
            "failed": failed,
            "rows_written": rows_written,
            "checkpoint": str(_STATE),
        }, ensure_ascii=False, indent=2))
        return 0

    def persist() -> None:
        _save_state({
            "version": 1,
            "algos": fingerprint,
            "range": current_range,
            "completed": completed,
            "failed": failed,
            "rows_written": rows_written,
            "updated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        })

    persist()
    started_at = time.perf_counter()

    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        key = f"{chunk_start.isoformat()}~{chunk_end.isoformat()}"
        before = await _row_count(chunk_start, chunk_end, algos)
        runs = await _run_count(chunk_start, chunk_end, algos)
        recorded = completed.get(key) or {}
        recorded_rows = recorded.get("rows")
        recorded_runs = recorded.get("runs")
        if (
            not args.force
            and recorded_rows is not None
            and recorded_runs is not None
            and int(recorded_rows) == before
            and int(recorded_runs) == runs
        ):
            continue

        chunk_started = time.perf_counter()
        try:
            result = await recompute_signal_perf(chunk_start, chunk_end, algos=algos)
            after = await _row_count(chunk_start, chunk_end, algos)
            if after != int(result.get("rows") or 0):
                raise RuntimeError(
                    f"行数对账失败：接口 rows={result.get('rows')}，库内={after}"
                )
        except Exception as exc:                      # noqa: BLE001 —— 单段失败不阻断后续
            failed[key] = f"{type(exc).__name__}: {exc}"
            logger.exception("信号前瞻回填失败 %s", key)
            persist()
            continue

        detail = result.get("detail") or {}
        completed[key] = {
            "rows": after,
            "runs": runs,
            "signals": int(detail.get("signals") or 0),
            "fillable": int(detail.get("fillable") or 0),
            "mature_20": int(detail.get("mature_20") or 0),
        }
        failed.pop(key, None)
        rows_written = sum(int(item.get("rows") or 0) for item in completed.values())
        persist()
        logger.info(
            "分段 %d/%d %s 完成：signals=%d rows=%d mature_20=%d，用时 %.1fs",
            index, len(chunks), key, completed[key]["signals"], after,
            completed[key]["mature_20"], time.perf_counter() - chunk_started,
        )

    if completed:
        last_day = max(_parse_date(key.split("~")[1]) for key in completed)
        async with AsyncSessionLocal() as session:
            await mark_success(
                session, "signal_perf",
                rows=rows_written,
                coverage=round(len(completed) / len(chunks), 4),
                trade_date=last_day,
                detail={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "chunks": len(completed),
                    "failed": len(failed),
                },
            )

    logger.info(
        "信号前瞻回填结束：完成 %d/%d 段，失败 %d，用时 %.1f 分钟",
        len(completed), len(chunks), len(failed),
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
    parser = argparse.ArgumentParser(description="信号前瞻收益历史回填（断点续传）")
    parser.add_argument("--start", type=_parse_date, default=None,
                        help=f"起始日（默认 config.HISTORY_START={config.HISTORY_START}）")
    parser.add_argument("--end", type=_parse_date, default=None,
                        help="截止日（默认最近一个开市日）")
    parser.add_argument("--chunk-days", type=int, default=60,
                        help="每段交易日数（默认 60，段末提交并写断点）")
    parser.add_argument("--algos", type=str, default="",
                        help="只回补指定策略（逗号分隔，默认全部 SPECS）")
    parser.add_argument("--force", action="store_true", help="忽略断点全量重算")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与断点状态")
    args = parser.parse_args()
    if args.chunk_days < 1:
        parser.error("--chunk-days 必须 >= 1")
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
