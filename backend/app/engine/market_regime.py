"""市场状态机与配置建议引擎（V7 T1）
=====================================
输入 `fact_market_daily` 的原始因子，输出每日的：

1. 标准化因子与五组得分（T / M / B / V_q / Q / R）；
2. 五态判定（含 3 日确认、5 日最短驻留、20% 退出放宽）；
3. 把握度 confidence 与乐观度 optimism（两个独立字段）；
4. 防御/进攻/均衡三档配置（合计 100%、5% 整数倍、日变化 <=15pct）；
5. Top5 因子依据。

标准化口径：750 日滚动窗口、1%/99% 缩尾、百分位映射到 [-1,1]；
不足 250 日不输出状态（`data_quality="accumulating"`）。
所有窗口只使用 T 日及以前的数据。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    MARKET_PERCENTILE_MIN_PERIODS,
    MARKET_PERCENTILE_WINDOW,
    REGIME_ALLOCATION_RANGES,
    REGIME_CONFIRM_DAYS,
    REGIME_LABELS,
    REGIME_MIN_RESIDENCE_DAYS,
    REGIME_EXIT_RELAX,
    REGIME_THRESHOLDS,
    SECTOR_ROLE_DESC,
    SECTOR_ROLE_LABELS,
    SECTOR_TEMPLATES,
    SECTOR_WHITELIST,
)
from app.core.logging import get_logger
from app.db.models.fact import (
    FactMarketDaily,
    FactSectorDaily,
    MarketRegimeDaily,
)
from app.engine._db import session_ctx, to_jsonable

logger = get_logger(__name__)


# ============================================================
# 因子定义
# ============================================================

@dataclass(frozen=True)
class FactorSpec:
    key: str
    column: str
    group: str
    weight: float
    direction: int
    label: str
    kind: str = "continuous"      # continuous / discrete / bounded / derived
    unit: str = ""
    missing: bool = False


def _specs() -> dict[str, FactorSpec]:
    rows = [
        FactorSpec("T1", "ema_slope60", "T", 0.28, 1, "中期均线斜率"),
        FactorSpec("T2", "ma_alignment", "T", 0.22, 1, "均线排列", "discrete"),
        FactorSpec("T3", "adx14", "T", 0.20, 1, "趋势强度"),
        FactorSpec("T4", "efficiency_ratio", "T", 0.14, 1, "趋势效率"),
        FactorSpec("T5", "choppiness", "T", 0.08, -1, "震荡程度"),
        FactorSpec("T6", "price_vs_ma120", "T", 0.08, 1, "相对120日线"),
        FactorSpec("M1", "macd_hist", "M", 0.30, 1, "MACD 动量"),
        FactorSpec("M2", "rsi14", "M", 0.20, 1, "RSI"),
        FactorSpec("M3", "ret_20", "M", 0.25, 1, "20日涨跌幅", unit="%"),
        FactorSpec("M4", "above_ma30_ratio", "M", 0.25, 1, "站上30日线占比"),
        FactorSpec("B1", "above_ma30_ratio", "B", 0.30, 1, "站上30日线占比"),
        FactorSpec("B2", "new_high_60_ratio", "B", 0.25, 1, "60日新高占比"),
        FactorSpec("B3", "adv_dec_ratio", "B", 0.25, 1, "上涨家数占比"),
        FactorSpec("B4", "limit_ratio", "B", 0.20, 1, "涨跌停强弱", "derived"),
        FactorSpec("V1", "rv20", "V", 0.35, 1, "20日波动率", unit="%"),
        FactorSpec("V2", "rv60", "V", 0.25, 1, "60日波动率", unit="%"),
        FactorSpec("V3", "natr14", "V", 0.20, 1, "日均波幅", unit="%"),
        FactorSpec("V4", "boll_bandwidth", "V", 0.10, 1, "布林带宽", unit="%"),
        FactorSpec("V5", "max_drawdown_3m", "V", 0.10, 1, "近3月最大回撤", unit="%"),
        FactorSpec("Q1", "amount_vs_ma20", "Q", 0.25, 1, "成交额相对20日均", unit="倍"),
        FactorSpec("Q2", "turnover_ma5", "Q", 0.15, 1, "换手率", unit="%"),
        FactorSpec("Q3", "volume_price_match", "Q", 0.15, 1, "量价配合", "bounded"),
        FactorSpec("Q4", "industry_heat_concentration", "Q", 0.10, -1, "行业集中度"),
        FactorSpec("Q5", "capital_efficiency", "Q", 0.15, 1, "资金效率"),
        FactorSpec("Q6", "margin_buy_ratio", "Q", 0.10, 1, "融资买入占比"),
        FactorSpec("Q7", "fund_issue_amt", "Q", 0.10, 1, "基金新发", missing=True),
        FactorSpec("R1", "erp", "R", 0.20, 1, "股债性价比", unit="%"),
        FactorSpec("R2", "bond_equity_gap", "R", 0.10, 1, "股债收益差", unit="%"),
        FactorSpec("R3", "term_spread", "R", 0.10, 1, "期限利差", unit="%"),
        FactorSpec("R4", "defensive_rel_momentum", "R", 0.20, -1, "防御相对强度", unit="%"),
        FactorSpec("R5", "northbound_net", "R", 0.15, 1, "北向净流入", missing=True),
        FactorSpec("R6", "broken_net_ratio", "R", 0.10, 1, "破净股占比"),
        FactorSpec("R7", "sentiment_index", "R", 0.15, 1, "情绪合成", "derived"),
    ]
    return {item.key: item for item in rows}


FACTOR_SPECS = _specs()
GROUP_KEYS = ("T", "M", "B", "V", "Q", "R")


# ============================================================
# 标准化
# ============================================================

def rolling_percentile(
    series: pd.Series,
    window: int = MARKET_PERCENTILE_WINDOW,
    min_periods: int = MARKET_PERCENTILE_MIN_PERIODS,
) -> pd.Series:
    """滚动窗口内的百分位（1%/99% 缩尾后）。

    缺数据 → NaN（由调用方中性化为 0 并降低 completeness），
    不足 min_periods → NaN（数据积累中）。
    """
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    for index in range(len(values)):
        current = values[index]
        if not np.isfinite(current):
            continue
        start = max(0, index - window + 1)
        window_values = values[start:index + 1]
        valid = window_values[np.isfinite(window_values)]
        if len(valid) < min_periods:
            continue
        q_low, q_high = np.percentile(valid, [1, 99])
        clipped = np.clip(valid, q_low, q_high)
        if len(clipped) == 0 or np.nanmax(clipped) == np.nanmin(clipped):
            out[index] = 0.5
            continue
        current = min(max(current, q_low), q_high)
        out[index] = float(np.mean(clipped < current))
    return pd.Series(out, index=series.index)


def _prepare_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """补派生原始列（涨跌停强弱）。"""
    frame = raw.copy()
    limit_up = pd.to_numeric(frame.get("limit_up_cnt"), errors="coerce")
    limit_down = pd.to_numeric(frame.get("limit_down_cnt"), errors="coerce")
    frame["limit_ratio"] = (limit_up - limit_down) / (limit_up + limit_down + 1)
    return frame


def standardize(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (pct 表, x 表)。

    - continuous：750 日分位 → x=±(2p-1)；
    - discrete：ma_alignment 直接用 -1/0/1；
    - bounded：量价配合 0–1 → 2v-1；
    - derived：涨跌停强弱 / 情绪合成，直接用有界值。
    """
    frame = _prepare_raw(raw).sort_values("trade_date").reset_index(drop=True)
    pct = pd.DataFrame(index=frame.index)
    x = pd.DataFrame(index=frame.index)

    for key, spec in FACTOR_SPECS.items():
        if spec.missing:
            continue
        series = pd.to_numeric(frame.get(spec.column), errors="coerce")
        if spec.kind == "continuous":
            p = rolling_percentile(series)
            pct[key] = p
            x[key] = spec.direction * (2 * p - 1)
        elif spec.kind == "discrete":
            pct[key] = np.nan
            x[key] = series.clip(-1, 1)
        elif spec.kind == "bounded":
            pct[key] = np.nan
            x[key] = (2 * series - 1).clip(-1, 1)
        elif spec.kind == "derived":
            pct[key] = np.nan
            x[key] = series.clip(-1, 1)

    # ADX 乘方向符号（+DI - -DI）
    if "T3" in x:
        plus = pd.to_numeric(frame.get("di_plus"), errors="coerce")
        minus = pd.to_numeric(frame.get("di_minus"), errors="coerce")
        direction = np.sign((plus - minus).fillna(0.0))
        x["T3"] = x["T3"] * direction

    # 情绪合成：可用子项的 x 均值（Q1/Q2/Q6/M4/V1反转/R1）
    sentiment_parts = []
    for key in ("Q1", "Q2", "Q6", "M4"):
        if key in x:
            sentiment_parts.append(x[key])
    if "V1" in pct:
        sentiment_parts.append(-(2 * pct["V1"] - 1))
    if "R1" in x:
        sentiment_parts.append(x["R1"])
    if sentiment_parts:
        sentiment = pd.concat(sentiment_parts, axis=1).mean(axis=1, skipna=True)
        x["R7"] = sentiment.clip(-1, 1)
        pct["R7"] = rolling_percentile(x["R7"])

    return pct, x


def group_scores(
    pct: pd.DataFrame, x: pd.DataFrame
) -> pd.DataFrame:
    """五组得分 + V_q + completeness + 有效因子数。"""
    result = pd.DataFrame(index=x.index)
    weights_by_group: dict[str, list[tuple[FactorSpec, float]]] = {key: [] for key in GROUP_KEYS}
    for key, spec in FACTOR_SPECS.items():
        if spec.missing:
            continue
        weights_by_group[spec.group].append((spec, spec.weight))

    for group in ("T", "M", "B", "Q", "R"):
        numerator = pd.Series(0.0, index=x.index)
        denominator = pd.Series(0.0, index=x.index)
        for spec, weight in weights_by_group[group]:
            if spec.key not in x:
                continue
            values = x[spec.key]
            available = values.notna()
            numerator = numerator + values.fillna(0.0) * weight
            denominator = denominator + available.astype(float) * weight
        result[f"group_{group.lower()}"] = numerator / denominator.replace(0, np.nan)

    v_numerator = pd.Series(0.0, index=x.index)
    v_denominator = pd.Series(0.0, index=x.index)
    for spec, weight in weights_by_group["V"]:
        if spec.key not in pct:
            continue
        values = pct[spec.key]
        v_numerator = v_numerator + values.fillna(0.0) * weight
        v_denominator = v_denominator + values.notna().astype(float) * weight
    result["v_q"] = (v_numerator / v_denominator.replace(0, np.nan)).clip(0, 1)

    available = pd.Series(0.0, index=x.index)
    for key, spec in FACTOR_SPECS.items():
        if spec.missing:
            continue
        if spec.kind == "continuous":
            source = pct.get(key)
            if source is None:
                source = x.get(key)
        else:
            source = x.get(key)
        if source is not None:
            available = available + source.notna().astype(float)
    result["available_factors"] = available
    result["completeness"] = available / len(FACTOR_SPECS)

    # 状态机要求五组 + V_q 齐全，且至少 5 个因子有分位
    required = ["group_t", "group_m", "group_b", "group_q", "group_r", "v_q"]
    result["valid"] = result[required].notna().all(axis=1) & (available >= 5)
    return result


# ============================================================
# 判定辅助
# ============================================================

def _value(row: pd.Series, key: str) -> float:
    value = row.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) else np.nan


def _confirmations(row: pd.Series, raw: pd.Series, index: int, raw_frame: pd.DataFrame) -> int:
    """筑底确认项（5 项中满足几项）。"""
    score = 0
    close = pd.to_numeric(raw_frame.get("close"), errors="coerce")
    low = pd.to_numeric(raw_frame.get("low"), errors="coerce") if "low" in raw_frame else None
    macd = pd.to_numeric(raw_frame.get("macd_hist"), errors="coerce")
    ema20 = pd.to_numeric(raw_frame.get("ema20"), errors="coerce")
    ema60 = pd.to_numeric(raw_frame.get("ema60"), errors="coerce")

    # ① 近 5 日低点高于前 5 日低点（没有 low 列时用 close 近似）
    series_low = low if low is not None else close
    if index >= 10 and series_low is not None:
        recent = series_low.iloc[index - 4:index + 1]
        prior = series_low.iloc[index - 9:index - 4]
        if recent.notna().all() and prior.notna().all() and recent.min() > prior.min():
            score += 1

    # ② MACD 底背离：价格接近前低而柱体抬高
    if index >= 20 and macd.notna().iloc[index - 20:index + 1].all():
        window_close = close.iloc[index - 20:index + 1]
        window_macd = macd.iloc[index - 20:index + 1]
        if window_close.notna().all() and close.iloc[index] <= window_close.min() * 1.02:
            if window_macd.iloc[-1] > window_macd.min():
                score += 1

    # ③ 破净率极高
    broken = _value(row, "pct_R6")
    if np.isfinite(broken) and broken > 0.85:
        score += 1

    # ④ 情绪由恐慌区回升
    sentiment = _value(row, "x_R7")
    if index >= 5:
        previous = row.get("sentiment_lag5")
        if previous is not None and np.isfinite(previous) and previous < -0.4 and sentiment > previous:
            score += 1

    # ⑤ 均线由空头转纠缠
    if index >= 10 and ema20 is not None and ema60 is not None:
        prior_align = raw_frame.get("ma_alignment")
        if prior_align is not None:
            prior = pd.to_numeric(prior_align, errors="coerce").iloc[index - 9:index]
            current_ratio = abs(ema20.iloc[index] / ema60.iloc[index] - 1) if ema60.iloc[index] else np.nan
            if (prior == -1).sum() >= 5 and np.isfinite(current_ratio) and current_ratio < 0.01:
                score += 1
    return score


def classify_state(row: pd.Series, index: int, raw_frame: pd.DataFrame) -> str:
    """五态分类（不含迟滞）。阈值集中在 REGIME_THRESHOLDS，便于历史校准。"""
    th = REGIME_THRESHOLDS
    T = _value(row, "group_t")
    M = _value(row, "group_m")
    B = _value(row, "group_b")
    Q = _value(row, "group_q")
    R = _value(row, "group_r")
    V = _value(row, "v_q")
    q1 = _value(row, "pct_Q1")
    q3 = _value(row, "x_Q3")
    q4 = _value(row, "pct_Q4")
    q5 = _value(row, "pct_Q5")
    dd = _value(row, "pct_V5")

    if not all(np.isfinite(item) for item in (T, M, B, Q, R, V)):
        return "accumulating"

    # 1) 避险下跌
    if (
        (R < th["risk_off_r"] and V > th["risk_off_v"] and M < th["risk_off_m"])
        or (T < th["risk_off_t"] and dd > th["risk_off_dd"])
        or (T < th["risk_off_t"] and V > th["risk_off_alt_v"] and M < th["risk_off_alt_m"])
    ):
        return "risk_off"

    # 2) 趋势上攻
    if (
        T > th["trend_t"] and M > th["trend_m"] and B > th["trend_b"]
        and Q > th["trend_q"] and q3 > 0
    ):
        return "trend_up"

    # 3) 震荡筑底
    if abs(T) <= th["bottom_t_abs"] and V < th["bottom_v"] and q1 < th["bottom_q1"]:
        confirmations = _confirmations(row, row, index, raw_frame)
        if confirmations >= th["bottom_confirm"]:
            if M < th["bottom_pull_m"]:
                return "pull_down"
            return "bottoming"

    # 4) 向上修复
    if abs(T) <= th["repair_t_abs"] and M > th["repair_m"] and T <= 0:
        return "repair_up"
    if T > 0 and not (
        T > th["trend_t"] and M > th["trend_m"] and B > th["trend_b"]
        and Q > th["trend_q"] and q3 > 0
    ):
        # 中期仍偏正但短期动量已转负：按「向下调整」处理，避免把走弱误标成修复
        return "pull_down" if M < 0 else "repair_up"

    # 5) 向下调整
    if abs(T) <= th["pull_t_abs"] and M < th["pull_m"]:
        return "pull_down"
    if q5 < th["pull_q5"] and q4 > th["pull_q4"]:
        previous = row.get("q4_lag5")
        if previous is not None and np.isfinite(previous) and q4 > previous:
            return "pull_down"

    # 兜底：按 M 方向
    return "repair_up" if M >= 0 else "pull_down"


def maintains_state(state: str, row: pd.Series) -> bool:
    """退出阈值放宽 20%：当前状态是否仍然成立。"""
    th = REGIME_THRESHOLDS
    relax = REGIME_EXIT_RELAX
    T = _value(row, "group_t")
    M = _value(row, "group_m")
    B = _value(row, "group_b")
    Q = _value(row, "group_q")
    R = _value(row, "group_r")
    V = _value(row, "v_q")
    q1 = _value(row, "pct_Q1")
    dd = _value(row, "pct_V5")
    q3 = _value(row, "x_Q3")
    if not all(np.isfinite(item) for item in (T, M, B, Q, R, V)):
        return False

    if state == "risk_off":
        return (
            (R < th["risk_off_r"] * relax and V > th["risk_off_v"] * relax
             and M < th["risk_off_m"] * relax)
            or (T < th["risk_off_t"] * relax and dd > th["risk_off_dd"])
            or (T < th["risk_off_t"] * relax and V > th["risk_off_alt_v"] * relax
                and M < th["risk_off_alt_m"] * relax)
        )
    if state == "trend_up":
        return (
            T > th["trend_t"] * relax and M > th["trend_m"] * relax
            and B > th["trend_b"] * relax and Q > th["trend_q"] * relax and q3 > 0
        )
    if state == "bottoming":
        return (
            abs(T) <= th["bottom_t_abs"] / relax
            and V < th["bottom_v"] / relax
            and q1 < th["bottom_q1"] / relax
        )
    if state == "repair_up":
        return (
            (abs(T) <= th["repair_t_abs"] / relax and M > th["repair_m"] * relax and T <= 0)
            or (T > 0 and not (
                T > th["trend_t"] * relax and M > th["trend_m"] * relax
                and B > th["trend_b"] * relax and Q > th["trend_q"] * relax and q3 > 0
            ))
        )
    if state == "pull_down":
        return abs(T) <= th["pull_t_abs"] / relax and M < th["pull_m"] * relax
    return False


# ============================================================
# 把握度 / 乐观度
# ============================================================

def _exceed(value: float, threshold: float, scale: float, *, below: bool = False) -> float:
    if not np.isfinite(value):
        return 0.0
    if below:
        return max(0.0, (threshold - value) / scale)
    return max(0.0, (value - threshold) / scale)


def _clip01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def state_scores(row: pd.Series, index: int, raw_frame: pd.DataFrame) -> dict[str, float]:
    th = REGIME_THRESHOLDS
    T = _value(row, "group_t")
    M = _value(row, "group_m")
    B = _value(row, "group_b")
    Q = _value(row, "group_q")
    R = _value(row, "group_r")
    V = _value(row, "v_q")
    q1 = _value(row, "pct_Q1")
    q3 = _value(row, "x_Q3")
    q4 = _value(row, "pct_Q4")
    q5 = _value(row, "pct_Q5")
    dd = _value(row, "pct_V5")

    def trend_score() -> float:
        parts = [
            (_exceed(T, th["trend_t"], 1 - th["trend_t"]), 0.25),
            (_exceed(M, th["trend_m"], 1 - th["trend_m"]), 0.20),
            (_exceed(B, th["trend_b"], 1 - th["trend_b"]), 0.20),
            (_exceed(Q, th["trend_q"], 1 - th["trend_q"]), 0.20),
            (_clip01((q3 + 1) / 2 if np.isfinite(q3) else 0), 0.15),
        ]
        return sum(value * weight for value, weight in parts)

    def risk_score() -> float:
        group_a = (
            _exceed(R, th["risk_off_r"], 1, below=True) * 0.35
            + _exceed(V, th["risk_off_v"], 1 - th["risk_off_v"]) * 0.25
            + _exceed(M, th["risk_off_m"], 1, below=True) * 0.20
        )
        group_b = (
            _exceed(T, th["risk_off_t"], 1, below=True) * 0.10
            + _exceed(dd, th["risk_off_dd"], 1 - th["risk_off_dd"]) * 0.10
        )
        group_c = (
            _exceed(T, th["risk_off_t"], 1, below=True) * 0.40
            + _exceed(V, th["risk_off_alt_v"], 1 - th["risk_off_alt_v"]) * 0.30
            + _exceed(M, th["risk_off_alt_m"], 1, below=True) * 0.30
        )
        return max(group_a / 0.8, group_b / 0.2, group_c)

    def bottom_score() -> float:
        confirmations = _confirmations(row, row, index, raw_frame) if index >= 0 else 0
        parts = [
            (_clip01(1 - abs(T) / th["bottom_t_abs"]), 0.20),
            (_exceed(V, th["bottom_v"], th["bottom_v"], below=True), 0.25),
            (_exceed(q1, th["bottom_q1"], th["bottom_q1"], below=True), 0.25),
            (confirmations / 5, 0.30),
        ]
        return sum(value * weight for value, weight in parts)

    def repair_score() -> float:
        parts = [
            (_clip01(1 - abs(T) / th["repair_t_abs"]), 0.20),
            (_exceed(M, th["repair_m"], 1 - th["repair_m"]), 0.35),
            (_clip01(1 - max(0.0, T) / th["repair_t_abs"]), 0.20),
            (_clip01((B + 1) / 2), 0.15),
            (_clip01((R + 1) / 2), 0.10),
        ]
        return sum(value * weight for value, weight in parts)

    def pull_score() -> float:
        parts = [
            (_clip01(1 - abs(T) / th["pull_t_abs"]), 0.20),
            (_exceed(M, th["pull_m"], 1, below=True), 0.35),
            (_exceed(q5, th["pull_q5"], th["pull_q5"], below=True), 0.25),
            (_exceed(q4, th["pull_q4"], 1 - th["pull_q4"]), 0.20),
        ]
        return sum(value * weight for value, weight in parts)

    return {
        "trend_up": _clip01(trend_score()),
        "risk_off": _clip01(risk_score()),
        "bottoming": _clip01(bottom_score()),
        "repair_up": _clip01(repair_score()),
        "pull_down": _clip01(pull_score()),
    }


def consistency(state: str, row: pd.Series) -> int:
    T = _value(row, "group_t")
    M = _value(row, "group_m")
    B = _value(row, "group_b")
    Q = _value(row, "group_q")
    R = _value(row, "group_r")
    V = _value(row, "v_q")
    if state == "trend_up":
        flags = [T > 0, M > 0, B > 0, Q > 0, R > 0]
    elif state == "risk_off":
        flags = [T < 0, M < 0, B < 0, Q < 0, R < 0]
    elif state == "bottoming":
        flags = [abs(T) < 0.33, M < 0.3, B < 0.5, Q < 0.5, V < 0.5]
    elif state == "repair_up":
        flags = [T <= 0.33, M > 0, B > 0, Q > 0, R > 0]
    else:
        flags = [T >= -0.33, M < 0, B < 0, Q < 0, R < 0]
    return int(sum(1 for flag in flags if flag))


def confidence_optimism(
    row: pd.Series, state: str, index: int, raw_frame: pd.DataFrame
) -> tuple[int, str, int, str]:
    scores = state_scores(row, index, raw_frame)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top1 = ordered[0][1]
    top2 = ordered[1][1] if len(ordered) > 1 else 0.0
    margin = max(0.0, top1 - top2)
    consistent = consistency(state, row)
    completeness = _value(row, "completeness")
    sigmoid = 1 / (1 + math.exp(-2.2 * margin))
    confidence = int(round(
        100 * sigmoid * (0.6 + 0.4 * consistent / 5)
        * (0.7 + 0.3 * (completeness if np.isfinite(completeness) else 0))
    ))
    confidence = max(0, min(100, confidence))
    level = "低" if confidence < 45 else ("中" if confidence <= 70 else "高")

    T = _value(row, "group_t")
    M = _value(row, "group_m")
    B = _value(row, "group_b")
    R = _value(row, "group_r")
    V = _value(row, "v_q")
    base = 50 + 38 * (0.30 * T + 0.30 * M + 0.20 * B + 0.20 * R) - 12 * (V - 0.5)
    adjustment = {
        "trend_up": 12, "repair_up": 5, "bottoming": 0,
        "pull_down": -8, "risk_off": -18,
    }.get(state, 0)
    optimism = int(round(max(0, min(100, base + adjustment))))
    if optimism <= 20:
        opt_level = "谨慎"
    elif optimism <= 40:
        opt_level = "偏谨慎"
    elif optimism <= 60:
        opt_level = "中性"
    elif optimism <= 80:
        opt_level = "偏乐观"
    else:
        opt_level = "乐观"
    return confidence, level, optimism, opt_level


# ============================================================
# 配置建议
# ============================================================

def _round5(value: float) -> int:
    return int(round(value / 5) * 5)


def _normalize_100(values: dict[str, float]) -> dict[str, int]:
    rounded = {key: _round5(value) for key, value in values.items()}
    diff = 100 - sum(rounded.values())
    order = sorted(rounded, key=rounded.get, reverse=True)
    index = 0
    while diff != 0 and order:
        key = order[index % len(order)]
        step = 5 if diff > 0 else -5
        if rounded[key] + step >= 0:
            rounded[key] += step
            diff -= step
        index += 1
        if index > 100:
            break
    return rounded


def _normalize_with_cap(
    values: dict[str, float], prior: dict[str, int], cap: int = 15
) -> tuple[dict[str, int], bool]:
    """归一化到 100%，同时保证每档相对上一日变化 <= cap（5% 整数倍）。"""
    keys = list(values)
    lower = {key: max(0, prior.get(key, _round5(values[key])) - cap) for key in keys}
    upper = {key: min(100, prior.get(key, _round5(values[key])) + cap) for key in keys}
    result = {
        key: min(max(_round5(values[key]), lower[key]), upper[key])
        for key in keys
    }
    diff = 100 - sum(result.values())
    capped = diff != 0
    guard = 0
    while diff != 0 and guard < 200:
        guard += 1
        step = 5 if diff > 0 else -5
        candidates = [
            key for key in keys
            if (step > 0 and result[key] + step <= upper[key])
            or (step < 0 and result[key] + step >= lower[key])
        ]
        if not candidates:
            candidates = [max(keys, key=lambda key: result[key])]
        key = max(candidates, key=lambda item: result[item])
        result[key] += step
        diff -= step
    return result, capped


def build_allocation(
    regime: str,
    optimism: int,
    confidence: int,
    previous: dict[str, Any] | None,
    sector_picks: dict[str, list[str]],
) -> dict[str, Any]:
    ranges = REGIME_ALLOCATION_RANGES[regime]
    mid = {key: (lo + hi) / 2 for key, (lo, hi) in ranges.items()}
    values = dict(mid)

    shift = (optimism - 50) / 50 * 8
    values["offensive"] += shift
    delta = values["offensive"] - mid["offensive"]
    denominator = mid["defensive"] + mid["balanced"]
    if denominator > 0:
        values["defensive"] -= delta * mid["defensive"] / denominator
        values["balanced"] -= delta * mid["balanced"] / denominator

    if confidence < 45:
        move = 5.0 if values["offensive"] >= 5 and values["defensive"] >= 5 else 2.5
        values["defensive"] -= move
        values["offensive"] -= move
        values["balanced"] += move * 2

    for key in values:
        values[key] = max(0.0, values[key])
    rounded = _normalize_100(values)

    capped = False
    if previous and previous.get("buckets"):
        prior = {
            bucket["key"]: int(bucket.get("pct") or 0)
            for bucket in previous["buckets"]
        }
        rounded, capped = _normalize_with_cap(values, prior)

    prior_map = {
        bucket["key"]: int(bucket.get("pct") or 0)
        for bucket in (previous or {}).get("buckets", [])
    } if previous else {}
    vs_yesterday = {
        key: (rounded[key] - prior_map.get(key, rounded[key])) for key in rounded
    }

    reasons = {
        "defensive": {
            "risk_off": "波动放大、避险升温，先用稳定方向打底",
            "pull_down": "短期动能走弱，降低高弹性暴露",
            "bottoming": "方向未明，靠稳定方向控制回撤",
            "repair_up": "修复初期仍需底仓保护",
            "trend_up": "趋势偏强，防御只留底仓",
        }[regime],
        "offensive": {
            "risk_off": "风险释放期，进攻仓位压到最低",
            "pull_down": "动量转弱，暂不扩大高弹性方向",
            "bottoming": "仅保留少量弹性，等待确认",
            "repair_up": "短期动量转正，小幅参与修复",
            "trend_up": "趋势与广度共振，保留向上弹性",
        }[regime],
        "balanced": {
            "risk_off": "主体仓位收缩，等待风险释放",
            "pull_down": "中期趋势未破位，主体放均衡",
            "bottoming": "中期趋势未确认，主体放均衡",
            "repair_up": "方向正在修复，主体仓位承接",
            "trend_up": "趋势确认前，均衡仓跟随",
        }[regime],
    }

    buckets = []
    for key in ("defensive", "offensive", "balanced"):
        sectors = sector_picks.get(key) or list(SECTOR_TEMPLATES[key])
        buckets.append({
            "key": key,
            "label": SECTOR_ROLE_LABELS[key],
            "pct": rounded[key],
            "sectors": sectors[:4],
            "role": SECTOR_ROLE_DESC[key],
            "reason": reasons[key],
        })

    rebalance_note = (
        "为避免频繁调仓，本次调整幅度已截断；等趋势或广度进一步确认后再动。"
        if capped else {
            "trend_up": "若放量突破且广度继续回升，可再提高进攻档。",
            "repair_up": "若短期动量持续为正且成交放大，再考虑加进攻。",
            "bottoming": "若出现放量突破或广度明显回升，再考虑提高进攻档。",
            "pull_down": "若下跌缩量且广度止跌，再考虑把仓位挪回均衡。",
            "risk_off": "若波动回落且广度修复，再考虑降低防御档。",
        }[regime]
    )
    caveat = "把握偏低，建议偏向均衡。" if confidence < 45 else ""
    return {
        "total": 100,
        "buckets": buckets,
        "vs_yesterday": vs_yesterday,
        "rebalance_note": rebalance_note,
        "confidence_caveat": caveat,
    }


# ============================================================
# 板块动态补充
# ============================================================

async def load_sector_strength(
    session: AsyncSession, start: date, end: date
) -> pd.DataFrame:
    rows = (await session.execute(
        select(
            FactSectorDaily.trade_date,
            FactSectorDaily.sector_name,
            FactSectorDaily.pct_chg,
        )
        .where(FactSectorDaily.sector_type == "industry")
        .where(FactSectorDaily.trade_date >= start)
        .where(FactSectorDaily.trade_date <= end)
    )).all()
    frame = pd.DataFrame(rows, columns=["trade_date", "sector_name", "pct_chg"])
    if frame.empty:
        return frame
    frame["pct_chg"] = pd.to_numeric(frame["pct_chg"], errors="coerce")
    pivot = frame.pivot_table(
        index="trade_date", columns="sector_name", values="pct_chg", aggfunc="last"
    ).sort_index()
    return ((1 + pivot / 100).rolling(20, min_periods=15).apply(np.prod, raw=True) - 1) * 100


def pick_sectors(
    strength: pd.DataFrame, trade_date: date, role: str
) -> list[str]:
    base = list(SECTOR_TEMPLATES[role])
    if strength.empty or trade_date not in strength.index:
        return base
    row = strength.loc[trade_date].dropna()
    whitelist = [name for name in SECTOR_WHITELIST[role] if name in row.index]
    if not whitelist:
        return base
    ranked = row[whitelist].sort_values(ascending=False).index.tolist()
    picks = base[:]
    for name in ranked:
        if len(picks) >= 4:
            break
        if name not in picks:
            picks.append(name)
    return picks[:4]


# ============================================================
# 依据与文案
# ============================================================

def _fmt_raw(spec: FactorSpec, value: float) -> str:
    if not np.isfinite(value):
        return "—"
    if spec.key in ("T1", "T6"):
        return f"{value * 100:.2f}%"
    if spec.unit == "%":
        return f"{value:.1f}%"
    if spec.unit == "倍":
        return f"{value:.2f} 倍"
    if spec.key in ("T2", "B4"):
        return f"{value:+.2f}"
    if spec.key in ("M4", "B1", "B2", "B3", "Q3", "Q4", "Q6", "R6"):
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def _plain(
    spec: FactorSpec, value: float, pct_value: float, x_value: float, supports: bool
) -> str:
    low = np.isfinite(pct_value) and pct_value <= 0.3
    high = np.isfinite(pct_value) and pct_value >= 0.7
    positive = np.isfinite(x_value) and x_value > 0
    key = spec.key
    if key in ("T1", "T2", "T4", "T6"):
        return "中期趋势向上" if positive else "中期趋势仍偏弱"
    if key == "T3":
        return "上行趋势的强度在增强" if positive else "下行趋势的强度在增强，弱势明显"
    if key == "T5":
        return "走势较连贯，震荡有所收敛" if positive else "上下反复，震荡明显"
    if key in ("M1", "M2", "M3"):
        return "短期动量和人气在回升" if positive else "短期动量和人气偏弱"
    if key in ("M4", "B1"):
        return "站上 30 日线的股票在增多" if high else "多数股票仍在 30 日线下方"
    if key == "B2":
        return "创 60 日新高的股票偏多，赚钱效应扩散" if high else "创新高的股票很少，赚钱效应偏弱"
    if key == "B3":
        return "上涨家数占优，普涨特征明显" if high else "下跌家数偏多，个股承压"
    if key == "B4":
        return "涨停明显多于跌停，短线情绪偏强" if positive else "跌停增多，短线情绪偏弱"
    if key in ("V1", "V2", "V3"):
        return "波动偏大，持有体验不稳" if high else "波动温和，走势相对平稳"
    if key == "V4":
        return "布林带张开，方向正在选择" if high else "布林带收窄，市场在蓄势"
    if key == "V5":
        return "近三个月回撤偏深，风险仍需消化" if high else "近三个月回撤可控"
    if key == "Q1":
        return "成交明显萎缩，抛压在减弱" if low else "成交放大，分歧在增加"
    if key == "Q2":
        return "换手偏低，参与意愿不强" if low else "换手回升，参与意愿增强"
    if key == "Q3":
        return "上涨放量、回调缩量，量价配合较好" if positive else "量价配合一般，追高意愿不足"
    if key == "Q4":
        return "成交集中在少数行业，分歧集中" if high else "行业分布较均衡，没有单一主线"
    if key == "Q5":
        return "同样成交额能推动更多涨幅，进攻效率提升" if positive else "成交额推动涨幅的效率在下降"
    if key == "Q6":
        return "融资买入占比回升，杠杆资金偏积极" if high else "融资买入占比偏低，杠杆资金谨慎"
    if key == "R1":
        return "股债性价比处于历史偏高水平，估值不贵" if high else "股债性价比偏低，估值吸引力一般"
    if key == "R2":
        return "股息率相对国债更有吸引力" if high else "股息率相对国债的吸引力一般"
    if key == "R3":
        return "期限利差走阔，流动性环境偏松" if high else "期限利差偏窄"
    if key == "R4":
        return "防御方向相对走强，资金在避险" if high else "防御方向不再占优，风险偏好回升"
    if key == "R6":
        return "破净股占比处于高位，估值处于低位区" if high else "破净股占比不高"
    if key == "R7":
        return "综合情绪在回升" if positive else "综合情绪偏弱"
    return "该项对判断形成支撑" if supports else "该项对判断形成拖累"


def _supports_state(
    state: str, spec: FactorSpec, x_value: float, pct_value: float
) -> bool:
    """因子方向是否支撑当前状态（不同状态对同一因子的解读不同）。"""
    if state == "trend_up":
        if spec.group == "V":
            return np.isfinite(pct_value) and pct_value < 0.5
        return np.isfinite(x_value) and x_value > 0
    if state == "risk_off":
        if spec.group == "V":
            return np.isfinite(pct_value) and pct_value > 0.5
        return np.isfinite(x_value) and x_value < 0
    if state == "bottoming":
        if spec.group == "V":
            return np.isfinite(pct_value) and pct_value < 0.35
        if spec.key == "Q1":
            return np.isfinite(pct_value) and pct_value < 0.25
        if spec.key == "R6":
            return np.isfinite(pct_value) and pct_value > 0.85
        if spec.group in ("Q", "M", "B"):
            return np.isfinite(x_value) and x_value < 0
        return np.isfinite(x_value) and abs(x_value) < 0.33
    if state == "repair_up":
        if spec.group == "V":
            return np.isfinite(pct_value) and pct_value < 0.5
        return np.isfinite(x_value) and x_value > 0
    if state == "pull_down":
        if spec.group == "V":
            return np.isfinite(pct_value) and pct_value > 0.5
        return np.isfinite(x_value) and x_value < 0
    return False


def build_evidence(
    row: pd.Series, state: str
) -> list[dict[str, Any]]:
    """按 |x| × 组内权重 取贡献最大的 5 个因子。"""
    items = []
    for key, spec in FACTOR_SPECS.items():
        if spec.missing:
            continue
        x_value = _value(row, f"x_{key}")
        pct_value = _value(row, f"pct_{key}")
        raw_value = _value(row, spec.column) if spec.column in row.index else np.nan
        if not np.isfinite(x_value) and not np.isfinite(pct_value):
            continue
        strength = abs(x_value) if np.isfinite(x_value) else abs(2 * pct_value - 1)
        if state == "bottoming":
            relevant = 1.6 if spec.group in ("V", "Q") and (x_value < 0) else 1.0
        elif state == "trend_up":
            relevant = 1.6 if x_value > 0 else 0.8
        elif state == "repair_up":
            relevant = 1.6 if (spec.group in ("M", "B") and x_value > 0) else 1.0
        elif state == "risk_off":
            relevant = 1.6 if x_value < 0 else 0.8
        else:
            relevant = 1.6 if x_value < 0 else 0.8
        contribution = strength * spec.weight * relevant
        supports = _supports_state(state, spec, x_value, pct_value)
        items.append({
            "code": key,
            "name": spec.label,
            "value": _fmt_raw(spec, raw_value if np.isfinite(raw_value) else x_value),
            "percentile": round(pct_value, 4) if np.isfinite(pct_value) else None,
            "direction": f"{'支撑' if supports else '削弱'}『{REGIME_LABELS[state]}』",
            "plain": _plain(spec, raw_value, pct_value, x_value, supports),
            "contribution": contribution,
        })
    items.sort(key=lambda item: item["contribution"], reverse=True)
    selected = items[:5]
    selected_codes = {item["code"] for item in selected}

    def ensure(prefix: str, slot: int) -> None:
        if any(code.startswith(prefix) for code in selected_codes) or slot >= len(selected):
            return
        for candidate in items:
            if candidate["code"].startswith(prefix) and candidate["code"] not in selected_codes:
                selected_codes.discard(selected[slot]["code"])
                selected[slot] = candidate
                selected_codes.add(candidate["code"])
                return

    # 让依据覆盖结论里提到的维度：避嫌/筑底要能看到波动，趋势要能看到广度
    if state in ("risk_off", "bottoming"):
        ensure("V", 4)
    if state == "bottoming":
        ensure("Q", 3)
    if state in ("trend_up", "repair_up"):
        ensure("B", 4)
    return [
        {key: value for key, value in item.items() if key != "contribution"}
        for item in selected
    ]


HEADLINES = {
    "trend_up": "中期趋势向上，短期动量与广度同步走强",
    "repair_up": "中期仍弱，但短期动量和人气正在修复",
    "bottoming": "跌势放缓、成交缩量，市场在低位反复筑底",
    "pull_down": "中期未破位，但短期动量和资金正在走弱",
    "risk_off": "避险情绪升温，波动放大、广度走弱",
}


# ============================================================
# 历史计算
# ============================================================

def _history_frame(raw: pd.DataFrame) -> pd.DataFrame:
    prepared = _prepare_raw(raw)
    pct, x = standardize(raw)
    groups = group_scores(pct, x)
    frame = pd.concat([
        prepared.reset_index(drop=True),
        pct.add_prefix("pct_"),
        x.add_prefix("x_"),
        groups,
    ], axis=1)
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    # 标准化窗口长度：不足 750 日为扩展窗口，data_quality 标 partial
    frame["window_days"] = [min(index + 1, MARKET_PERCENTILE_WINDOW) for index in range(len(frame))]
    # 滞后列（只用于判定，不参与评分）
    for source, target in (("x_R7", "sentiment_lag5"), ("pct_Q4", "q4_lag5")):
        if source in frame:
            frame[target] = frame[source].shift(5)
    return frame


def simulate_history(frame: pd.DataFrame) -> pd.DataFrame:
    """逐日跑状态机，返回带 regime_* 与配置的 DataFrame。"""
    state: str | None = None
    since: date | None = None
    days = 0
    pending: str | None = None
    pending_days = 0
    previous_allocation: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []

    for index, row in frame.iterrows():
        trade_date = row["trade_date"]
        if isinstance(trade_date, pd.Timestamp):
            trade_date = trade_date.date()
        candidate = classify_state(row, index, frame)
        valid = bool(row.get("valid", False))

        if not valid or candidate == "accumulating":
            records.append({
                "trade_date": trade_date,
                "regime_code": None,
                "data_quality": "accumulating",
                "note": "数据积累中，暂不输出阶段判断",
            })
            continue

        if state is None:
            state = candidate
            since = trade_date
            days = 1
            pending = None
            pending_days = 0
        else:
            days += 1
            if candidate == state:
                pending = None
                pending_days = 0
            else:
                if candidate == "risk_off":
                    state = "risk_off"
                    since = trade_date
                    days = 1
                    pending = None
                    pending_days = 0
                else:
                    if pending == candidate:
                        pending_days += 1
                    else:
                        pending = candidate
                        pending_days = 1
                    if (pending_days >= REGIME_CONFIRM_DAYS
                            and days >= REGIME_MIN_RESIDENCE_DAYS
                            and not maintains_state(state, row)):
                        state = candidate
                        since = trade_date
                        days = 1
                        pending = None
                        pending_days = 0

        confidence, conf_level, optimism, opt_level = confidence_optimism(
            row, state, index, frame
        )
        regime_label = REGIME_LABELS[state]
        note = ""
        if state == "trend_up" and _value(row, "v_q") > 0.90:
            note = "上攻但波动过大"
        elif state == "repair_up" and _value(row, "pct_Q5") < 0.3:
            note = "修复动能衰减"
        elif pending and pending != state:
            note = f"观察是否转向『{REGIME_LABELS[pending]}』"
        headline = HEADLINES[state]
        stale_fields = [
            spec.label for key, spec in FACTOR_SPECS.items()
            if not spec.missing
            and not np.isfinite(_value(row, f"pct_{key}"))
            and not np.isfinite(_value(row, f"x_{key}"))
        ]

        records.append({
            "trade_date": trade_date,
            "regime_code": state,
            "regime_label": regime_label,
            "regime_since": since,
            "regime_days": days,
            "note": note,
            "headline": headline,
            "confidence_score": confidence,
            "confidence_level": conf_level,
            "optimism_score": optimism,
            "optimism_level": opt_level,
            "group_t": _value(row, "group_t"),
            "group_m": _value(row, "group_m"),
            "group_b": _value(row, "group_b"),
            "group_v": _value(row, "v_q"),
            "group_q": _value(row, "group_q"),
            "group_r": _value(row, "group_r"),
            "completeness": _value(row, "completeness"),
            "data_quality": (
                "full"
                if _value(row, "completeness") >= 0.85
                and _value(row, "window_days") >= MARKET_PERCENTILE_WINDOW
                else "partial"
            ),
            "stale_fields": stale_fields,
            "factors": _factor_payload(row),
            "evidence": build_evidence(row, state),
            "_confidence": confidence,
            "_optimism": optimism,
        })

    result = pd.DataFrame(records)
    if result.empty:
        return result

    # 配置需要顺序依赖：逐日生成，保存上一日配置
    allocations = []
    for _, row in result.iterrows():
        if row["regime_code"] is None:
            allocations.append(None)
            continue
        previous = previous_allocation
        allocation = build_allocation(
            row["regime_code"],
            int(row["_optimism"]),
            int(row["_confidence"]),
            previous,
            {},
        )
        allocations.append(allocation)
        previous_allocation = allocation
    result["allocation"] = allocations
    return result


def _factor_payload(row: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, spec in FACTOR_SPECS.items():
        raw_value = _value(row, spec.column) if spec.column in row.index else np.nan
        pct_value = _value(row, f"pct_{key}")
        x_value = _value(row, f"x_{key}")
        if not np.isfinite(raw_value) and not np.isfinite(pct_value) and not np.isfinite(x_value):
            continue
        payload[key] = {
            "label": spec.label,
            "group": spec.group,
            "raw": raw_value if np.isfinite(raw_value) else None,
            "pct": pct_value if np.isfinite(pct_value) else None,
            "x": x_value if np.isfinite(x_value) else None,
        }
    return payload


# ============================================================
# 落库入口
# ============================================================

def _row_to_db(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": record["trade_date"],
        "regime_code": record["regime_code"],
        "regime_label": record["regime_label"],
        "regime_since": record["regime_since"],
        "regime_days": int(record["regime_days"]),
        "note": record.get("note") or None,
        "headline": record.get("headline"),
        "confidence_score": int(record["confidence_score"]),
        "confidence_level": record["confidence_level"],
        "optimism_score": int(record["optimism_score"]),
        "optimism_level": record["optimism_level"],
        "group_t": record.get("group_t"),
        "group_m": record.get("group_m"),
        "group_b": record.get("group_b"),
        "group_v": record.get("group_v"),
        "group_q": record.get("group_q"),
        "group_r": record.get("group_r"),
        "factors": to_jsonable(record.get("factors")),
        "allocation": to_jsonable(record.get("allocation")),
        "evidence": to_jsonable(record.get("evidence")),
        "completeness": record.get("completeness"),
        "data_quality": record.get("data_quality") or "partial",
        "stale_fields": to_jsonable(record.get("stale_fields") or []),
        "src": "engine",
    }


async def compute_regime_range(
    start: date, end: date, db: AsyncSession | None = None
) -> int:
    """计算并 upsert [start, end] 的状态与配置；返回写入行数。"""
    async with session_ctx(db) as session:
        rows = (await session.execute(
            select(FactMarketDaily)
            .where(FactMarketDaily.trade_date <= end)
            .order_by(FactMarketDaily.trade_date)
        )).scalars().all()
        if not rows:
            logger.warning("状态机 %s ~ %s：fact_market_daily 为空", start, end)
            return 0
        raw = pd.DataFrame([{
            column: getattr(row, column)
            for column in _RAW_COLUMNS
        } for row in rows])
        frame = _history_frame(raw)

        # 动态板块：只加载一次，避免逐日查库
        strength = await load_sector_strength(
            session, start - timedelta(days=60), end
        )

        result = simulate_history(frame)
        if result.empty:
            return 0
        result = result[
            (result["trade_date"] >= start)
            & (result["trade_date"] <= end)
            & (result["regime_code"].notna())
        ]
        if result.empty:
            return 0

        # 配置里的动态板块补充（按日；跨档去重，避免同一行业同时出现在两档）
        for index, record in result.iterrows():
            if record["regime_code"] is None:
                continue
            raw_picks = {
                role: pick_sectors(strength, record["trade_date"], role)
                for role in ("defensive", "offensive", "balanced")
            }
            used: set[str] = set()
            picks: dict[str, list[str]] = {}
            for role in ("defensive", "offensive", "balanced"):
                names = [name for name in raw_picks[role] if name not in used]
                if len(names) < 2:
                    for name in raw_picks[role]:
                        if name not in names:
                            names.append(name)
                picks[role] = names[:4]
                used.update(names)
            allocation = record.get("allocation") or {}
            if allocation:
                for bucket in allocation.get("buckets", []):
                    bucket["sectors"] = picks.get(bucket["key"], bucket["sectors"])[:4]

        payload = [_row_to_db(record) for _, record in result.iterrows()]
        stmt = pg_insert(MarketRegimeDaily).values(payload)
        update_columns = {
            key: value for key, value in payload[0].items() if key != "trade_date"
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[MarketRegimeDaily.trade_date],
            set_={key: getattr(stmt.excluded, key) for key in update_columns},
        )
        await session.execute(stmt)
        logger.info("状态机 %s ~ %s：写入 %d 行", start, end, len(payload))
        return len(payload)


async def compute_and_save_regime(
    trade_date: date, db: AsyncSession | None = None
) -> dict:
    rows = await compute_regime_range(trade_date, trade_date, db=db)
    return {"rows": rows, "trade_date": trade_date.isoformat()}


_RAW_COLUMNS = (
    "trade_date", "close", "high", "low", "ema20", "ema60", "ema120",
    "ema_slope60", "ma_alignment", "adx14", "di_plus", "di_minus",
    "efficiency_ratio", "choppiness", "macd_hist", "rsi14", "price_vs_ma120",
    "ret_20",
    "amount", "amount_ma20", "amount_vs_ma20", "market_turnover", "turnover_ma5",
    "volume_price_match", "industry_heat_concentration", "capital_efficiency",
    "up_count", "down_count", "flat_count", "total_count",
    "limit_up_cnt", "limit_down_cnt", "adv_dec_ratio", "above_ma30_ratio",
    "new_high_60_ratio", "broken_net_ratio", "dividend_yield", "earnings_yield",
    "cn_1y", "cn_10y", "erp", "bond_equity_gap", "term_spread",
    "defensive_rel_momentum", "margin_buy_amount", "margin_buy_ratio",
    "rv20", "rv60", "natr14", "boll_bandwidth", "max_drawdown_3m",
)


__all__ = [
    "FACTOR_SPECS",
    "build_allocation",
    "build_evidence",
    "classify_state",
    "compute_and_save_regime",
    "compute_regime_range",
    "confidence_optimism",
    "group_scores",
    "maintains_state",
    "pick_sectors",
    "rolling_percentile",
    "simulate_history",
    "standardize",
]
