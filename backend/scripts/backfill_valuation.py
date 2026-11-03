"""全市场财报/分红回补 + 历史估值重算（P0-11）。

三个阶段按顺序执行，相互独立（单阶段失败不阻断后续）：

  1. ``financials`` —— 新浪财报指标 → ``dim_stock_financial``（有网络、限频、断点续传）；
  2. ``dividends``  —— 巨潮现金分红明细 → ``dim_stock_dividend``（有网络、断点续传）；
  3. ``valuation``  —— 零网络重算 ``fact_stock_daily.pe_ttm/pb/dv_ttm``。

用法::

    python scripts/backfill_valuation.py                     # 全流程
    python scripts/backfill_valuation.py --stage valuation --start 2024-01-02
    python scripts/backfill_valuation.py --codes 600519,000001 --stage financials
    python scripts/backfill_valuation.py --force             # 强制刷新（忽略已有最新报告期）

阶段摘要以 ``=== {json}`` 打印；``--summary-out`` 可落盘供运维留档。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.ingest.valuation import (  # noqa: E402
    compute_valuation,
    sync_dividends,
    sync_financials,
)

STAGES = ("financials", "dividends", "valuation")
DEFAULT_START_YEAR = 2019
DEFAULT_VALUATION_START = date(2022, 9, 1)


def _today_cn() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def _as_date(value: str | None, fallback: date) -> date:
    return date.fromisoformat(value) if value else fallback


def _parse_codes(value: str | None) -> list[str] | None:
    if not value:
        return None
    codes = [item.strip() for item in value.split(",") if item.strip()]
    return codes or None


def _stage_plan(
    *,
    stages: Sequence[str],
    start_year: int,
    start: date,
    end: date,
    codes: list[str] | None,
    resume: bool,
    only_missing: bool,
) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    """构建阶段执行计划（纯函数，单测可断言参数与顺序）。"""
    plan: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
    for name in stages:
        if name == "financials":
            plan.append((
                name,
                lambda: sync_financials(
                    codes=codes, start_year=start_year,
                    only_missing=only_missing, resume=resume,
                ),
            ))
        elif name == "dividends":
            plan.append((
                name,
                lambda: sync_dividends(
                    codes=codes, only_missing=only_missing, resume=resume,
                ),
            ))
        elif name == "valuation":
            plan.append((name, lambda: compute_valuation(start, end, codes)))
        else:  # pragma: no cover —— argparse choices 已拦截
            raise ValueError(f"未知阶段：{name}")
    return plan


async def run_stages(
    *,
    stages: Sequence[str] = STAGES,
    start_year: int = DEFAULT_START_YEAR,
    start: date = DEFAULT_VALUATION_START,
    end: date | None = None,
    codes: list[str] | None = None,
    resume: bool = True,
    only_missing: bool = True,
) -> dict[str, dict]:
    """顺序执行阶段，返回每阶段摘要；单阶段异常只记录不中断。"""
    end = end or _today_cn()
    summary: dict[str, dict] = {}
    for name, factory in _stage_plan(
        stages=stages, start_year=start_year, start=start, end=end,
        codes=codes, resume=resume, only_missing=only_missing,
    ):
        started = time.perf_counter()
        try:
            result = await factory()
            payload = result.summary() if hasattr(result, "summary") else {"result": result}
            summary[name] = payload
        except Exception as exc:  # noqa: BLE001 —— 单阶段失败不阻断后续
            summary[name] = {"error": f"{type(exc).__name__}: {exc}"}
        record = {
            "stage": name,
            "elapsed_sec": round(time.perf_counter() - started, 1),
            **summary[name],
        }
        print("=== " + json.dumps(record, ensure_ascii=False, default=str), flush=True)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="财报/分红回补 + 历史估值重算")
    parser.add_argument("--stage", action="append", choices=[*STAGES, "all"],
                        help="执行阶段，可重复；默认全部")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR,
                        help="财报抓取起始年份（默认 2019）")
    parser.add_argument("--start", help="估值重算起始日 YYYY-MM-DD（默认 2022-09-01）")
    parser.add_argument("--end", help="估值重算结束日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--codes", help="限定代码，逗号分隔（默认全部在市股票）")
    parser.add_argument("--no-resume", action="store_true", help="忽略断点重新检查")
    parser.add_argument("--force", action="store_true",
                        help="忽略 only_missing，强制重抓财报/分红")
    parser.add_argument("--summary-out", help="阶段摘要写入的 JSON 文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_parser().parse_args(argv)
    stages = STAGES if (not args.stage or "all" in args.stage) else tuple(dict.fromkeys(args.stage))
    summary = asyncio.run(run_stages(
        stages=stages,
        start_year=args.start_year,
        start=_as_date(args.start, DEFAULT_VALUATION_START),
        end=_as_date(args.end, _today_cn()),
        codes=_parse_codes(args.codes),
        resume=not args.no_resume,
        only_missing=not args.force,
    ))
    if args.summary_out:
        Path(args.summary_out).write_text(
            json.dumps(summary, ensure_ascii=False, default=str), encoding="utf-8"
        )
    return 1 if any("error" in item for item in summary.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
