"""
技术指标库（方案 C1）
=====================
纯函数 + 向量化，**不依赖除 pandas/numpy 之外的任何库**，输入输出都是 Series，
这样既能批量算全市场，也能在单元测试里用已知序列断言。

口径说明（写死在代码里，避免各处各算一套）：
  - MA      : 简单移动平均，窗口内等权
  - RSI     : Wilder 平滑（RMA），period=14。**不是** rolling.mean 的简化版
  - MACD    : DIF = EMA12 - EMA26；DEA = EMA9(DIF)；HIST = DIF - DEA
  - BOLL    : 20 日中轨 ± 2 倍总体标准差（ddof=0，与国内行情软件一致）
  - ATR     : Wilder 平滑的真实波幅，period=14
  - 动量    : 区间收益率 × 100（百分数）
  - 波动率  : 日收益率标准差 × sqrt(252) × 100（年化百分数）
  - 量比    : 近 5 日均量 / 近 20 日均量
  - 金叉    : DIF 当日上穿 DEA（前一日 DIF<=DEA 且当日 DIF>DEA）—— 用户定稿「当日金叉」
  - 多头排列: MA5 > MA10 > MA20 —— 用户定稿「三线多头」
  - KDJ     : RSV(9) → K/D 以 50 为种子做 1/3 平滑，J = 3K - 2D（通达信口径）
  - 带宽分位: 布林带宽在近 60 日的百分位（0–100，越低越"挤压"）
  - 区间宽度: 前 20 日收盘价区间宽度 / 高点（%），用于识别横盘收敛
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


# ============================================================
# 基础指标
# ============================================================

def ma(series: pd.Series, window: int) -> pd.Series:
    """简单移动平均。"""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """指数移动平均（adjust=False，与行情软件一致）。"""
    return series.ewm(span=span, adjust=False, min_periods=1).mean()


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑（RMA）的**标准实现**。

    标准定义（TA-Lib / StockCharts 一致）：
        第 period 个有效值 = 前 period 个观测的**简单平均**（种子）
        之后            = (prev × (period-1) + cur) / period

    注意：直接用 `ewm(alpha=1/period)` 会以第一个观测值做种子，早期值会系统性偏差
    （实测首值 50.66 vs 标准 70.53），这里通过「把种子塞进第 period 位、前面置 NaN」
    的方式，让 pandas 的递归从种子开始，既保持向量化又与标准一致。
    """
    values = series.astype("float64")
    n = len(values)
    if n <= period:
        return pd.Series(np.nan, index=series.index, dtype="float64")

    # 约定：输入序列的第 0 位是 NaN（例如 diff 生成的第一个值），有效观测从 1 开始
    seed = values.iloc[1:period + 1].mean()

    seeded = pd.Series(np.nan, index=series.index, dtype="float64")
    seeded.iloc[period] = seed
    seeded.iloc[period + 1:] = values.iloc[period + 1:]
    # adjust=False：y_t = (1-α)y_{t-1} + α·x_t，首个非空值即种子
    return seeded.ewm(alpha=1.0 / period, adjust=False, ignore_na=True).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指标（Wilder 平滑）。

    RSI = 100 - 100/(1+RS)，RS = 平均涨幅/平均跌幅，平均用 Wilder 平滑。
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = _wilder_smooth(gain, period)
    avg_loss = _wilder_smooth(loss, period)

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # 全涨（avg_loss=0）时 RSI 定义为 100；全跌为 0
    out = out.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return out


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD。返回 (DIF, DEA, HIST)。"""
    dif = ema(series, fast) - ema(series, slow)
    dea = ema(dif, signal)
    hist = dif - dea
    return dif, dea, hist


def boll(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带。返回 (中轨, 上轨, 下轨)。"""
    mid = ma(series, window)
    # ddof=0：总体标准差，与通达信/东财一致
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return mid, upper, lower


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """真实波幅 TR = max(H-L, |H-PC|, |L-PC|)。"""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """平均真实波幅（Wilder 平滑，与 TA-Lib 口径一致）。"""
    tr = true_range(high, low, close)
    return _wilder_smooth(tr, period)


def momentum(series: pd.Series, window: int) -> pd.Series:
    """N 日动量（%）：(今收/前收N - 1) × 100。"""
    return series.pct_change(periods=window) * 100.0


def volatility(series: pd.Series, window: int = 20,
               annualize: bool = True) -> pd.Series:
    """滚动波动率（年化 %）。"""
    ret = series.pct_change()
    vol = ret.rolling(window=window, min_periods=max(5, window // 2)).std(ddof=0)
    if annualize:
        vol = vol * np.sqrt(TRADING_DAYS_PER_YEAR)
    return vol * 100.0


def volume_ratio(volume: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
    """量比 = 近 short 日均量 / 近 long 日均量。"""
    short_avg = volume.rolling(window=short, min_periods=short).mean()
    long_avg = volume.rolling(window=long, min_periods=long).mean()
    return short_avg / long_avg.replace(0.0, np.nan)


# ============================================================
# 短线指标（B1–B5）
# ============================================================

def _ewm_seeded(series: pd.Series, alpha: float, seed: float) -> pd.Series:
    """带固定种子的指数平滑：首个有效值 = (1-α)×seed + α×value。

    KDJ 的通达信口径要求 K/D 从 50 起算；直接 `ewm` 会以首个 RSV 为种子，
    前 20 个交易日内存在可感知偏差。
    """
    values = series.astype("float64")
    valid = values.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    smoothed = pd.Series(np.nan, index=series.index, dtype="float64")
    first = valid.index[0]
    smoothed.loc[first] = (1.0 - alpha) * seed + alpha * valid.loc[first]
    smoothed.loc[valid.index[1:]] = valid.iloc[1:]
    return smoothed.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()


def kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ(9,3,3)。返回 (K, D, J)。"""
    lowest = low.rolling(window=period, min_periods=period).min()
    highest = high.rolling(window=period, min_periods=period).max()
    span = (highest - lowest).replace(0.0, np.nan)
    rsv = (close - lowest) / span * 100.0
    k = _ewm_seeded(rsv, 1.0 / k_period, 50.0)
    d = _ewm_seeded(k, 1.0 / d_period, 50.0)
    j = 3.0 * k - 2.0 * d
    return k, d, j


def boll_bandwidth(mid: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """布林带宽 % = (上轨 − 下轨) / 中轨 × 100。"""
    return (upper - lower) / mid.replace(0.0, np.nan) * 100.0


def rolling_range_pct(high: pd.Series, low: pd.Series, window: int) -> pd.Series:
    """近 N 日振幅 % = (最高 − 最低) / 最低 × 100。"""
    highest = high.rolling(window=window, min_periods=window).max()
    lowest = low.rolling(window=window, min_periods=window).min()
    return (highest - lowest) / lowest.replace(0.0, np.nan) * 100.0


def previous_range_pct(close: pd.Series, window: int = 20) -> pd.Series:
    """突破前 N 日收盘区间宽度 %（不含当日），用于判断收敛是否干净。"""
    previous = close.shift(1)
    highest = previous.rolling(window=window, min_periods=window).max()
    lowest = previous.rolling(window=window, min_periods=window).min()
    return (highest - lowest) / highest.replace(0.0, np.nan) * 100.0


def previous_high(close: pd.Series, window: int = 20) -> pd.Series:
    """前 N 日最高收盘价（不含当日）。"""
    return close.shift(1).rolling(window=window, min_periods=window).max()


# ============================================================
# 形态判定
# ============================================================

def golden_cross(fast_line: pd.Series, slow_line: pd.Series) -> pd.Series:
    """当日金叉：前一日 fast<=slow 且当日 fast>slow。返回 bool Series。"""
    prev = fast_line.shift(1) <= slow_line.shift(1)
    now = fast_line > slow_line
    result = prev & now
    return result.fillna(False).astype(bool)


def above_all(price: pd.Series, *lines: pd.Series) -> pd.Series:
    """价格同时站上所有给定均线。"""
    result = price > lines[0]
    for line in lines[1:]:
        result &= price > line
    return result.fillna(False).astype(bool)


def ma_bullish(ma5: pd.Series, ma10: pd.Series, ma20: pd.Series) -> pd.Series:
    """三线多头排列：MA5 > MA10 > MA20。"""
    return ((ma5 > ma10) & (ma10 > ma20)).fillna(False).astype(bool)


# ============================================================
# 全量特征计算
# ============================================================

FEATURE_COLUMNS = [
    "ma5", "ma10", "ma20", "ma60",
    "rsi14",
    "macd_dif", "macd_dea", "macd_hist", "macd_golden_cross",
    "boll_mid", "boll_upper", "boll_lower", "atr14",
    "momentum_5", "momentum_20", "momentum_60",
    "volatility_20", "volume_ratio", "amount_ma20", "ma_bullish",
    # 短线策略（B1–B5）所需指标
    "ema5", "ema20", "ema60", "ma120",
    "ema20_up", "ema_golden_cross", "ema_cross_3d",
    "rsi6", "roc10",
    "kdj_k", "kdj_d", "kdj_j", "kdj_j_min2", "kdj_golden_cross",
    "boll_width", "boll_width_pct60",
    "high20_close", "range20_pct", "amp60", "vol_ma5",
]


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """对**单只股票**按时间升序的日线计算全部指标。

    参数 df 必须包含列：trade_date, open, high, low, close, volume, amount
    返回：在 df 上追加 FEATURE_COLUMNS 的新 DataFrame（行序不变）。

    注意：调用方必须保证 df 已按 trade_date 升序，且**至少 60 行**才有完整 MA60；
    不足时对应列为 NaN（不做前向填充，避免引入未来信息）。
    """
    required = {"trade_date", "open", "high", "low", "close", "volume", "amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_features 缺少必需列: {sorted(missing)}")

    out = df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    volume = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    amount = pd.to_numeric(out["amount"], errors="coerce")

    out["ma5"] = ma(close, 5)
    out["ma10"] = ma(close, 10)
    out["ma20"] = ma(close, 20)
    out["ma60"] = ma(close, 60)

    out["rsi14"] = rsi(close, 14)

    dif, dea, hist = macd(close)
    out["macd_dif"] = dif
    out["macd_dea"] = dea
    out["macd_hist"] = hist
    out["macd_golden_cross"] = golden_cross(dif, dea)

    mid, upper, lower = boll(close)
    out["boll_mid"] = mid
    out["boll_upper"] = upper
    out["boll_lower"] = lower

    out["atr14"] = atr(high, low, close, 14)

    out["momentum_5"] = momentum(close, 5)
    out["momentum_20"] = momentum(close, 20)
    out["momentum_60"] = momentum(close, 60)

    out["volatility_20"] = volatility(close, 20)
    out["volume_ratio"] = volume_ratio(volume)
    out["amount_ma20"] = amount.rolling(window=20, min_periods=5).mean()
    out["ma_bullish"] = ma_bullish(out["ma5"], out["ma10"], out["ma20"])

    # ---- 短线策略指标 ----
    out["ema5"] = ema(close, 5)
    out["ema20"] = ema(close, 20)
    out["ema60"] = ema(close, 60)
    out["ma120"] = ma(close, 120)
    out["ema20_up"] = (out["ema20"] > out["ema20"].shift(1)).fillna(False).astype(bool)
    out["ema_golden_cross"] = golden_cross(out["ema5"], out["ema20"])
    out["ema_cross_3d"] = (
        out["ema_golden_cross"].rolling(window=3, min_periods=1).max().astype(bool)
    )

    out["rsi6"] = rsi(close, 6)
    out["roc10"] = momentum(close, 10)

    k_line, d_line, j_line = kdj(high, low, close)
    out["kdj_k"] = k_line
    out["kdj_d"] = d_line
    out["kdj_j"] = j_line
    out["kdj_j_min2"] = pd.concat([j_line, j_line.shift(1)], axis=1).min(axis=1)
    out["kdj_golden_cross"] = golden_cross(k_line, d_line)

    width = boll_bandwidth(mid, upper, lower)
    out["boll_width"] = width
    # 用突破前一日（不含当日）的带宽分位，避免突破日放量扩带把挤压条件自己冲掉
    previous_width = width.shift(1)
    out["boll_width_pct60"] = (
        previous_width.rolling(window=60, min_periods=60).rank(pct=True) * 100.0
    )

    out["high20_close"] = previous_high(close, 20)
    out["range20_pct"] = previous_range_pct(close, 20)
    out["amp60"] = rolling_range_pct(high, low, 60)
    out["vol_ma5"] = volume.rolling(window=5, min_periods=5).mean()

    return out


def last_features(features: pd.DataFrame) -> dict:
    """取最后一行的特征字典（NaN → None），方便落库。"""
    if features.empty:
        return {}
    row = features.iloc[-1]
    result: dict = {}
    for col in FEATURE_COLUMNS:
        if col not in features.columns:
            continue
        value = row[col]
        if isinstance(value, (bool, np.bool_)):
            result[col] = bool(value)
        elif value is None or (isinstance(value, float) and np.isnan(value)):
            result[col] = None
        else:
            result[col] = float(value)
    return result


__all__ = [
    "ma", "ema", "rsi", "macd", "boll", "true_range", "atr", "momentum",
    "volatility", "volume_ratio", "golden_cross", "above_all", "ma_bullish",
    "kdj", "boll_bandwidth", "rolling_range_pct", "previous_range_pct", "previous_high",
    "compute_features", "last_features", "FEATURE_COLUMNS",
]
