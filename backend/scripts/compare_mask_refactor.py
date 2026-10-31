"""T7 掩码重构等价性对比（临时交付脚本）。

内嵌重构前的 strategy_mask（原样搬运），对最近 N 个交易日、全部 7 个策略比较：
  - 逐行布尔掩码是否完全一致；
  - 相同参数下 Top N 的 code 集合与 rank 顺序是否一致。

用法：
    backend/.venv/Scripts/python.exe scripts/compare_mask_refactor.py --days 20
有差异时退出码非 0（T7 的 PASS/FAIL 判据）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models.fact import FactStockDaily  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.strategy import strategies as strat  # noqa: E402

ALGOS = ("a3", "a4", "b1", "b2", "b3", "b4", "b5")


def _old_numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    return strat._to_float_series(df[column])  # noqa: SLF001


def _old_bool(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return strat._to_bool_series(df[column])  # noqa: SLF001


def _old_market_flag(df: pd.DataFrame, key: str) -> bool:
    market = df.attrs.get("market") or {}
    return bool(market.get(key, True))


def old_strategy_mask(algo_code: str, df: pd.DataFrame, params: dict) -> pd.Series:
    """重构前 strategy_mask 的逐字副本（仅用于等价性对比）。"""
    spec = strat.get_spec(algo_code)
    code = spec.code
    params = strat._validate_params(code, params)  # noqa: SLF001

    if code == "a3":
        composite = _old_numeric(df, "composite_score")
        if "ownership" in df.columns:
            ownership = df["ownership"].astype("object")
            central = ownership.eq("央企").fillna(False)
        else:
            central = pd.Series(False, index=df.index, dtype=bool)
        mask = central & composite.notna() & (composite >= float(params["min_score"]))
    elif code == "a4":
        pe = _old_numeric(df, "pe_ttm")
        dividend = _old_numeric(df, "dv_ttm")
        mask = (
            (pe > 0)
            & (pe < float(params["pe_max"]))
            & dividend.notna()
            & (dividend >= float(params["div_min"]))
        )
    elif code == "b1":
        close = _old_numeric(df, "close")
        volume = _old_numeric(df, "volume")
        vol_ma5 = _old_numeric(df, "vol_ma5")
        ma60 = _old_numeric(df, "ma60")
        amount_ma20 = _old_numeric(df, "amount_ma20")
        mask = (
            _old_bool(df, "ema_cross_3d")
            & (close > _old_numeric(df, "ema20"))
            & _old_bool(df, "ema20_up")
            & (_old_numeric(df, "roc10") > 0)
            & vol_ma5.notna()
            & (volume >= vol_ma5 * float(params["min_vol_ratio"]))
            & (close > ma60)
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
    elif code == "b2":
        close = _old_numeric(df, "close")
        open_price = _old_numeric(df, "open")
        pre_close = _old_numeric(df, "pre_close")
        ma120 = _old_numeric(df, "ma120")
        amplitude = _old_numeric(df, "amp60")
        amount_ma20 = _old_numeric(df, "amount_ma20")
        mask = (
            (_old_numeric(df, "rsi6") < float(params["rsi_max"]))
            & ((close > open_price) | (close >= pre_close))
            & ma120.notna()
            & (close > ma120 * float(params["trend_dev"]))
            & amplitude.notna()
            & (amplitude > float(params["amp_min"]))
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
    elif code == "b3":
        close = _old_numeric(df, "close")
        pre_close = _old_numeric(df, "pre_close")
        ema60 = _old_numeric(df, "ema60")
        amount_ma20 = _old_numeric(df, "amount_ma20")
        mask = (
            (_old_numeric(df, "kdj_j_min2") < float(params["j_max"]))
            & _old_bool(df, "kdj_golden_cross")
            & (_old_numeric(df, "kdj_k") < float(params["k_max"]))
            & ema60.notna()
            & (close > ema60)
            & (close >= pre_close)
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
    elif code == "b4":
        close = _old_numeric(df, "close")
        volume = _old_numeric(df, "volume")
        vol_ma5 = _old_numeric(df, "vol_ma5")
        amount_ma20 = _old_numeric(df, "amount_ma20")
        band_pct = _old_numeric(df, "boll_width_pct60")
        mask = (
            band_pct.notna()
            & (band_pct <= float(params["squeeze_pct"]))
            & (close > _old_numeric(df, "boll_upper"))
            & (close > _old_numeric(df, "high20_close"))
            & vol_ma5.notna()
            & (volume >= vol_ma5 * float(params["vol_mult"]))
            & _old_numeric(df, "range20_pct").notna()
            & (_old_numeric(df, "range20_pct") < float(params["range_max"]))
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
        if not _old_market_flag(df, "b4_ok"):
            mask &= False
    elif code == "b5":
        close = _old_numeric(df, "close")
        low = _old_numeric(df, "low")
        open_price = _old_numeric(df, "open")
        pre_close = _old_numeric(df, "pre_close")
        volume = _old_numeric(df, "volume")
        vol_ma5 = _old_numeric(df, "vol_ma5")
        lower = _old_numeric(df, "boll_lower")
        ma120 = _old_numeric(df, "ma120")
        amount_ma20 = _old_numeric(df, "amount_ma20")
        deviation = (lower - close) / lower.where(lower > 0) * 100.0
        mask = (
            lower.notna()
            & (low <= lower)
            & (_old_numeric(df, "kdj_j_min2") < float(params["j_max"]))
            & vol_ma5.notna()
            & (volume < vol_ma5)
            & ((close > open_price) | (close >= pre_close))
            & ((ma120.notna() & (close > ma120)) | (deviation <= float(params["dev_max"])))
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
        if not _old_market_flag(df, "b5_ok"):
            mask &= False
    else:  # pragma: no cover
        raise ValueError(algo_code)
    return mask.fillna(False).astype(bool)


def _top_codes(algo: str, frame: pd.DataFrame, mask: pd.Series, limit: int) -> list[str]:
    selected = frame.loc[mask]
    if len(selected) == 0:
        return []
    return strat._sort_candidates(algo, selected).head(limit)["code"].tolist()  # noqa: SLF001


async def _latest_dates(session, days: int, end: date | None) -> list[date]:
    stmt = select(FactStockDaily.trade_date).distinct()
    if end is not None:
        stmt = stmt.where(FactStockDaily.trade_date <= end)
    stmt = stmt.order_by(FactStockDaily.trade_date.desc()).limit(days)
    rows = (await session.execute(stmt)).scalars().all()
    return sorted(rows)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    end = date.fromisoformat(args.end) if args.end else None

    failures: list[dict] = []
    total_checks = 0
    async with AsyncSessionLocal() as session:
        dates = await _latest_dates(session, args.days, end)
        print(f"对比交易日: {dates[0]} ~ {dates[-1]}（{len(dates)} 天）")
        for trade_date in dates:
            raw = await strat._fetch_cross_section(session, trade_date)  # noqa: SLF001
            if len(raw) == 0:
                print(f"{trade_date} 无横截面，跳过")
                continue
            open_dates = await strat._load_open_dates(session, trade_date)  # noqa: SLF001
            eligible = strat.apply_common_filters(raw, trade_date, open_dates)
            market = await strat.load_market_context(session, trade_date)
            eligible.attrs["market"] = market
            line: list[str] = []
            for algo in ALGOS:
                params = strat._validate_params(algo, {})  # noqa: SLF001
                old_mask = old_strategy_mask(algo, eligible, params)
                new_mask = strat.strategy_mask(algo, eligible, params)
                diff = int((old_mask != new_mask).sum())
                old_top = _top_codes(algo, eligible, old_mask, args.limit)
                new_top = _top_codes(algo, eligible, new_mask, args.limit)
                total_checks += 1
                if diff or old_top != new_top:
                    failures.append(
                        {
                            "trade_date": trade_date.isoformat(),
                            "algo": algo,
                            "mask_diff": diff,
                            "old_count": int(old_mask.sum()),
                            "new_count": int(new_mask.sum()),
                            "old_top": old_top,
                            "new_top": new_top,
                        }
                    )
                line.append(f"{algo}:{int(new_mask.sum())}/{diff}")
            print(f"{trade_date} " + " ".join(line))
        print(
            f"市场状态 {market.get('index_code')} close={market.get('close')} "
            f"ma20={market.get('ma20')} pct={market.get('pct_chg')} "
            f"b4_ok={market.get('b4_ok')} b5_ok={market.get('b5_ok')}"
        )

    if failures:
        print("\nFAIL：发现差异")
        for item in failures:
            print(item)
        return 2
    print(f"\nPASS：{total_checks} 次策略对比全部零差异（掩码 + Top{args.limit} 顺序）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
