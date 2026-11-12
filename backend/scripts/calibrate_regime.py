"""市场状态机阈值校准（V7 T1）

只读数据库，不写任何业务表；输出不同阈值下的：
  - 五态天数占比
  - 平均/中位持续交易日
  - 每年切换次数
  - 最长单一状态占比

用法（backend 目录）：

    ../.venv/Scripts/python.exe scripts/calibrate_regime.py
    ../.venv/Scripts/python.exe scripts/calibrate_regime.py --set bottom_v=0.55 --set bottom_q1=0.35
    ../.venv/Scripts/python.exe scripts/calibrate_regime.py --grid
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select  # noqa: E402

from app.core.logging import setup_logging  # noqa: E402
from app.db.models.fact import FactMarketDaily  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engines  # noqa: E402
from app.engine import market_regime as regime  # noqa: E402


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_set(values: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in values:
        key, _, raw = item.partition("=")
        if not key or not raw:
            raise argparse.ArgumentTypeError(f"--set 需要 key=value：{item}")
        result[key.strip()] = float(raw)
    return result


def _stats(result: pd.DataFrame, years: float) -> dict:
    valid = result[result["regime_code"].notna()].copy()
    if valid.empty:
        return {"days": 0}
    counts = valid["regime_code"].value_counts()
    shares = (counts / counts.sum() * 100).round(2).to_dict()

    durations: dict[str, list[int]] = {}
    switches = 0
    current = None
    length = 0
    for code in valid["regime_code"]:
        if code == current:
            length += 1
        else:
            if current is not None:
                durations.setdefault(current, []).append(length)
                switches += 1
            current = code
            length = 1
    if current is not None:
        durations.setdefault(current, []).append(length)

    summary = {}
    for code, values in durations.items():
        summary[code] = {
            "episodes": len(values),
            "avg_days": round(sum(values) / len(values), 1),
            "median_days": int(pd.Series(values).median()),
            "max_days": max(values),
        }
    return {
        "days": int(len(valid)),
        "start": valid["trade_date"].min().isoformat(),
        "end": valid["trade_date"].max().isoformat(),
        "shares": shares,
        "episodes": summary,
        "switches": switches,
        "switches_per_year": round(switches / years, 1) if years > 0 else None,
        "max_share": max(shares.values()) if shares else None,
        "missing_states": [
            code for code in ("trend_up", "repair_up", "bottoming", "pull_down", "risk_off")
            if code not in counts
        ],
    }


async def _load_frame() -> pd.DataFrame:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(FactMarketDaily).order_by(FactMarketDaily.trade_date)
        )).scalars().all()
    if not rows:
        raise SystemExit("fact_market_daily 为空，请先运行 backfill_market_factors.py")
    raw = pd.DataFrame([
        {column: getattr(row, column) for column in regime._RAW_COLUMNS}
        for row in rows
    ])
    return regime._history_frame(raw)


def _run(frame: pd.DataFrame, thresholds: dict[str, float]) -> dict:
    regime.REGIME_THRESHOLDS.update(thresholds)
    result = regime.simulate_history(frame)
    years = (frame["trade_date"].max() - frame["trade_date"].min()).days / 365.25
    return _stats(result, years)


async def main(args) -> int:
    frame = await _load_frame()
    try:
        if args.sets:
            overrides = _parse_set(args.sets)
            print(json.dumps({
                "mode": "single",
                "overrides": overrides,
                "stats": _run(frame, overrides),
            }, ensure_ascii=False, indent=2))
            return 0

        base = dict(regime.REGIME_THRESHOLDS)
        if args.grid:
            grid = [
                {"bottom_v": v, "bottom_q1": q, "bottom_t_abs": t}
                for v in (0.45, 0.55, 0.65)
                for q in (0.25, 0.35)
                for t in (0.33, 0.40)
            ]
            rows = []
            for overrides in grid:
                regime.REGIME_THRESHOLDS = dict(base)
                stats = _run(frame, overrides)
                rows.append({
                    **overrides,
                    "shares": stats.get("shares"),
                    "episodes": {code: item["episodes"] for code, item in stats.get("episodes", {}).items()},
                    "avg_days": {code: item["avg_days"] for code, item in stats.get("episodes", {}).items()},
                    "switches_per_year": stats.get("switches_per_year"),
                    "max_share": stats.get("max_share"),
                    "missing": stats.get("missing_states"),
                })
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0

        print(json.dumps({
            "mode": "baseline",
            "thresholds": base,
            "stats": _run(frame, {}),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        await dispose_engines()


def cli() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="市场状态机阈值校准（只读）")
    parser.add_argument("--set", dest="sets", action="append", default=[],
                        help="覆盖阈值，如 bottom_v=0.55，可重复")
    parser.add_argument("--grid", action="store_true", help="跑筑底阈值网格")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args)))


if __name__ == "__main__":
    cli()
