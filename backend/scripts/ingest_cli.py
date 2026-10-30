#!/usr/bin/env python
"""采集层命令行入口（方案 A8 的运维接口）
==========================================

在 ``backend`` 目录下运行（与项目 README 的启动方式一致）::

    python scripts/ingest_cli.py calendar
    python scripts/ingest_cli.py universe
    python scripts/ingest_cli.py snapshot [--date 2026-10-21] [--force]
    python scripts/ingest_cli.py backfill [--start 2021-01-01] [--end 2026-10-21]
                                        [--codes 600519,000001] [--no-resume]
    python scripts/ingest_cli.py sector|moneyflow|index|margin [--date ...]
    python scripts/ingest_cli.py all [--date ...] [--include snapshot,moneyflow]

设计说明：
    - 不依赖 ``python -m app.ingest``（用户习惯从 backend 目录直接跑脚本），
      这里手动把 backend 目录插进 ``sys.path``；
    - Windows 必须 ``WindowsSelectorEventLoopPolicy``（项目历史坑：默认
      ProactorEventLoop 与 asyncpg/libpq 的兼容问题会导致随机卡死）；
    - 进度：service 层每 50 个标的打一条 INFO 日志，这里配置 logging 输出到 stdout，
      最后再打印一段 JSON 统计摘要，方便脚本/CI 解析。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ---- 让 `python scripts/ingest_cli.py` 在 backend 目录下可 import app.* ----
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _install_windows_event_loop_policy() -> None:
    """Windows 下强制 SelectorEventLoop（项目历史坑，见模块 docstring）。"""
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover —— 非 Windows 不会走到
            pass


def _parse_date(value: str | None) -> date | None:
    """解析 ``YYYY-MM-DD`` / ``YYYYMMDD``。"""
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"无法解析日期参数: {value!r}（期望 YYYY-MM-DD）")


def _parse_codes(value: str | None) -> list[str] | None:
    """解析 ``--codes 600519,000001``。"""
    if not value:
        return None
    codes = [item.strip() for item in value.split(",") if item.strip()]
    return codes or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_cli",
        description="GFNA V5 采集层命令行（AKShare 主源 + Baostock 兜底）",
    )
    parser.add_argument(
        "command",
        choices=[
            "calendar", "universe", "snapshot", "backfill", "sector",
            "moneyflow", "index", "margin", "all",
        ],
        help="要执行的采集任务",
    )
    parser.add_argument("--date", help="目标交易日（默认最近一个交易日），YYYY-MM-DD")
    parser.add_argument("--start", help="起始日期（backfill / calendar 用），YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期（backfill / calendar 用），YYYY-MM-DD")
    parser.add_argument("--codes", help="指定股票代码，逗号分隔（backfill 用）")
    parser.add_argument("--no-resume", action="store_true",
                        help="backfill 不使用断点续传（从头开始）")
    parser.add_argument("--force", action="store_true",
                        help="snapshot 强制重新采集（仍遵守 60s 最小间隔）")
    parser.add_argument("--include", help="all 模式指定步骤，逗号分隔")
    parser.add_argument("--include-us", action="store_true",
                        help="index 任务同时采集美股指数（需要 yfinance）")
    return parser


def _print_summary(title: str, payload: Any) -> None:
    """打印简洁的 JSON 统计（datetime/date 用 str 兜底）。"""
    print(f"\n===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))


async def _resolve_trade_date(db: Any, requested: date | None) -> date | None:
    """把请求日期解析成可用交易日（默认"最近一个交易日"）。"""
    from app.ingest import calendar as cal

    if requested is not None:
        if not await cal.is_trade_day(db, requested):
            print(f"[warn] {requested} 不是交易日，任务将跳过")
            return None
        return requested
    today = date.today()
    if await cal.is_trade_day(db, today):
        return today
    latest = await cal.latest_trade_date(db, today)
    if latest is not None:
        print(f"[info] 今天非交易日，使用最近交易日 {latest}")
    return latest


async def _run(args: argparse.Namespace) -> int:
    from app.core.logging import setup_logging
    from app.db.session import AsyncSessionLocal, dispose_engines
    from app.ingest import calendar as cal
    from app.ingest import service

    setup_logging()
    requested_date = _parse_date(args.date)
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    command = args.command
    exit_code = 0

    async with AsyncSessionLocal() as db:
        try:
            if command == "calendar":
                range_start = start or (date.today() - timedelta(days=60))
                range_end = end or (date.today() + timedelta(days=7))
                result = await cal.sync_trade_calendar(db, range_start, range_end)
                _print_summary("交易日历同步", {**result.summary(), "detail": result.detail})
                return 0 if result.rows else 1

            if command == "backfill":
                codes = _parse_codes(args.codes)
                backfill_end = end or requested_date or await _resolve_trade_date(db, None)
                result = await service.backfill_stock_history(
                    codes=codes,
                    start=start,
                    end=backfill_end,
                    resume=not args.no_resume,
                )
                _print_summary("历史日线回补", result.summary())
                return 0 if result.failed == 0 else 2

            # 其余任务都需要目标交易日
            trade_date = await _resolve_trade_date(db, requested_date)
            if trade_date is None:
                print("[warn] 找不到可用交易日（交易日历为空且非交易日），跳过")
                return 0

            if command == "universe":
                result = await service.sync_stock_universe(db)
                _print_summary("股票池同步", result.summary())
                return 0 if result.ok else 2

            if command == "snapshot":
                result = await service.collect_daily_snapshot(
                    trade_date, db, force=args.force
                )
                _print_summary("全市场快照", result.summary())
                return 0 if (result.ok or result.is_skipped) else 2

            if command == "sector":
                # sector = 板块日线 + 板块成分（SCD2），与每日流水线的 sectors 步骤一致
                daily = await service.collect_sector_daily(trade_date, db)
                members = await service.collect_sector_members(trade_date, db)
                _print_summary(
                    "板块日线与成分",
                    {"daily": daily.summary(), "members": members.summary()},
                )
                return 0 if (daily.ok or daily.is_skipped) else 2

            if command == "moneyflow":
                result = await service.collect_moneyflow(trade_date, db)
                _print_summary("个股资金流", result.summary())
                return 0 if (result.ok or result.is_skipped) else 2

            if command == "index":
                result = await service.collect_indices(
                    trade_date, db, include_us=args.include_us
                )
                _print_summary("指数日线", result.summary())
                return 0 if (result.ok or result.is_skipped) else 2

            if command == "margin":
                result = await service.collect_margin(trade_date, db)
                _print_summary("两融与北向", result.summary())
                return 0 if (result.ok or result.is_skipped) else 2

            if command == "all":
                include = tuple(
                    item.strip() for item in (args.include or "").split(",") if item.strip()
                ) or (
                    "calendar", "universe", "indices", "sectors",
                    "snapshot", "moneyflow", "margin",
                )
                payload = await service.run_daily_ingest(
                    trade_date, db, include=include
                )
                _print_summary("每日采集编排", payload)
                if payload.get("status") == "skipped":
                    return 0
                return 0 if payload.get("status") == "success" else 2

            raise SystemExit(f"未知命令: {command}")
        finally:
            try:
                await dispose_engines()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    _install_windows_event_loop_policy()
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 首次运行前提示（CI/离线环境应把 INGEST_NETWORK_ENABLED 置 0）
    from app.core import config

    if not config.INGEST_NETWORK_ENABLED:
        print("[warn] INGEST_NETWORK_ENABLED=0：采集任务会因禁止联网而失败（CI 预期行为）")

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n[info] 已中断（历史回补已写 checkpoint，下次可直接续传）")
        return 130


if __name__ == "__main__":
    sys.exit(main())
