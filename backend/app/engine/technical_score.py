"""
板块技术面打分（方案 C5，自旧 `services/technical_analysis.py` 迁移并修正）
==========================================================================
为什么迁移到 `engine/`：
  - 旧实现自己写了一套 MA/RSI/MACD，与 `engine/indicators.py` 的口径迟早漂移；
  - 现在统一改为**复用 indicators**，保证板块技术分与个股技术指标同源同口径。

打分结构（沿用旧版可解释的三因子加权，权重 40/30/30）：
  1. 趋势分（40%）：收盘价相对 MA5 / MA10 / MA20 的偏离百分比之和
        trend_raw = Σ (last_close - MA_n) / MA_n * 100
        trend_score = clip(5 + trend_raw * 0.5, 0, 10)
     直观理解：价格站得越高、离均线越远，趋势越强；跌破所有均线时为 0 分附近。
  2. 动量分（30%）：RSI14 所处区间 + 当日 MACD 金叉加成
        RSI ∈ [40, 70]（健康区间）   → 7.0
        RSI ∈ [30, 80]（中性/一般）  → 5.0
        其它（超买 >80 或超卖 <30） → 3.0（追高/抄底都有风险，给低分）
        当日 DIF 上穿 DEA（金叉）    → +1.5，上限 10
  3. 量能分（30%）：5 日均量 / 20 日均量（量比）
        volume_score = clip(5 + (量比 - 1) * 5, 0, 10)
        放量（量比 2）→ 10 分，缩量（量比 0）→ 0 分，平量 → 5 分。

边界约定（硬性）：
  - 有效收盘价 < 20 个交易日 → 返回 `NEUTRAL_SCORE`（5.0），**不抛异常**，
    detail 里带 `reason="insufficient_history"` 供前端解释"为什么是 5 分"；
  - 所有分数最终 `round(..., 2)` 且裁剪到 0–10；
  - 本模块是**纯函数**（只依赖 pandas/numpy 与 indicators），可脱离数据库单测。
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from app.core.config import NEUTRAL_SCORE
from app.engine.indicators import golden_cross, ma, macd, rsi

logger = logging.getLogger(__name__)

# 三因子权重（可调；与旧版保持一致，改动请同步 docs/V5-API-CONTRACT.md 的 detail 口径）
TECH_WEIGHTS: dict[str, float] = {
    "trend": 0.40,
    "momentum": 0.30,
    "volume": 0.30,
}

# 计算技术分所需的最少历史交易日（少于此值只能给中性分）
MIN_HISTORY = 20


# ============================================================
# 内部工具
# ============================================================

def _as_float_series(values: Any) -> pd.Series:
    """把 list / ndarray / Series（可能含 Decimal/None）统一成 float64 Series。"""
    if isinstance(values, pd.Series):
        series = values
    else:
        series = pd.Series(values)
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _clip10(value: float) -> float:
    """裁剪到 [0, 10] 并保留两位小数。"""
    return round(float(min(10.0, max(0.0, value))), 2)


def _align_close_volume(close: Any, volume: Any) -> pd.DataFrame:
    """清洗并对齐收盘价/成交量。

    规则：
      - 收盘价无效（NaN）的行直接剔除，避免"用未来/占位值"参与指标；
      - 成交量缺失按 0 处理（停牌日成交量确实可能为 0）。
    """
    close_s = _as_float_series(close)
    volume_s = _as_float_series(volume)
    if len(volume_s) != len(close_s):
        # 长度不一致时按各自位置截断到较短长度，而不是抛异常（采集层偶发缺列）
        n = min(len(close_s), len(volume_s))
        close_s = close_s.iloc[:n].reset_index(drop=True)
        volume_s = volume_s.iloc[:n].reset_index(drop=True)
    else:
        close_s = close_s.reset_index(drop=True)
        volume_s = volume_s.reset_index(drop=True)
    frame = pd.DataFrame({"close": close_s, "volume": volume_s}).dropna(subset=["close"])
    frame["volume"] = frame["volume"].fillna(0.0)
    return frame.reset_index(drop=True)


def _empty_detail(rows: int) -> dict:
    """历史不足时的 detail（显式带 reason，前端据此展示"数据不足"）。"""
    return {
        "reason": "insufficient_history",
        "rows": int(rows),
        "min_rows": MIN_HISTORY,
        "trend": None,
        "momentum": None,
        "volume": None,
    }


# ============================================================
# 公开 API
# ============================================================

def score_sector_technical_detail(
    close: Any, volume: Any
) -> tuple[float, dict]:
    """板块技术面评分 + 可解释明细。

    参数:
        close: 收盘价序列（按交易日**升序**；建议长度 >= 20，lookback 60 更稳）
        volume: 成交量序列（股），与 close 等长同序

    返回:
        (score, detail)
        - score: 0–10，保留两位小数；历史不足返回 5.0
        - detail: `{"reason", "rows", "trend", "momentum", "volume", ...}`；
                  历史不足时 `reason="insufficient_history"` 且三子项为 None。
    """
    frame = _align_close_volume(close, volume)
    rows = len(frame)
    if rows < MIN_HISTORY:
        return _clip10(NEUTRAL_SCORE), _empty_detail(rows)

    close_s: pd.Series = frame["close"]
    volume_s: pd.Series = frame["volume"]
    last_close = float(close_s.iloc[-1])

    # ---------- 1. 趋势分（40%）----------
    deviations: list[float] = []
    for window in (5, 10, 20):
        line = ma(close_s, window).iloc[-1]
        if line is None or pd.isna(line) or float(line) <= 0:
            continue
        deviations.append((last_close - float(line)) / float(line) * 100.0)
    trend_raw = float(sum(deviations)) if deviations else 0.0
    trend_score = _clip10(5.0 + trend_raw * 0.5)

    # ---------- 2. 动量分（30%）----------
    rsi_last = rsi(close_s, 14).iloc[-1]
    rsi_value = 50.0 if rsi_last is None or pd.isna(rsi_last) else float(rsi_last)
    if 40.0 <= rsi_value <= 70.0:
        momentum_score = 7.0
    elif 30.0 <= rsi_value <= 80.0:
        momentum_score = 5.0
    else:
        momentum_score = 3.0

    dif, dea, _hist = macd(close_s)
    crossed = bool(golden_cross(dif, dea).iloc[-1])
    if crossed:
        momentum_score = min(10.0, momentum_score + 1.5)
    momentum_score = _clip10(momentum_score)

    # ---------- 3. 量能分（30%）----------
    vol_20d = float(volume_s.iloc[-20:].mean())
    if vol_20d > 0:
        volume_ratio = float(volume_s.iloc[-5:].mean()) / vol_20d
    else:
        volume_ratio = 1.0
    volume_score = _clip10(5.0 + (volume_ratio - 1.0) * 5.0)

    composite = _clip10(
        trend_score * TECH_WEIGHTS["trend"]
        + momentum_score * TECH_WEIGHTS["momentum"]
        + volume_score * TECH_WEIGHTS["volume"]
    )

    detail = {
        "reason": "ok",
        "rows": rows,
        "close": round(last_close, 3),
        "trend": trend_score,
        "momentum": momentum_score,
        "volume": volume_score,
        "rsi14": None if np.isnan(rsi_value) else round(rsi_value, 2),
        "macd_golden_cross": crossed,
        "volume_ratio": round(volume_ratio, 4),
        "weights": dict(TECH_WEIGHTS),
    }
    return composite, detail


def score_sector_technical(close: pd.Series, volume: pd.Series) -> float:
    """板块技术面评分（0–10，保留两位小数）。

    旧版 `services/technical_analysis.score_sector_technical` 的 V5 替代：
    内部改为复用 `engine.indicators` 的 MA/RSI/MACD，打分公式仍是
    **趋势 40% / 动量 30% / 量能 30%**，保证与历史看板数值连续可比。

    参数:
        close: 收盘价序列（交易日升序，建议 >= 20 个点，60 个点更稳）
        volume: 成交量序列（股）

    返回:
        float: 0.0–10.0；历史不足 20 日返回 5.0（中性）而不是抛异常。
    """
    return score_sector_technical_detail(close, volume)[0]


__all__ = [
    "TECH_WEIGHTS",
    "MIN_HISTORY",
    "score_sector_technical",
    "score_sector_technical_detail",
]
