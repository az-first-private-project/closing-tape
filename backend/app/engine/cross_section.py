"""
横截面标准化（方案 C3）
========================
为什么需要横截面标准化：
  同一个"PE 20 倍"在银行股（行业均值 6 倍）和半导体（行业均值 60 倍）里含义完全不同。
  直接拿全市场分位做比较，会把"整个行业便宜/贵"误判成个股便宜/贵，
  所以选股前必须先做**行业内中性化**，再做因子排序。

本模块提供三个纯函数（只依赖 pandas/numpy，可单测、不触库）：
  - `winsorize`           分位缩尾，削掉极端值对均值/标准差的污染；
  - `percentile_rank`     0–1 分位（越大越好），NaN 保留；
  - `industry_neutral_zscore`  行业内 z-score；行业样本太小时退化为全市场 z-score。

口径（写死在代码里）：
  - z-score 用**总体标准差**（ddof=0），与 `engine/indicators.boll` 的口径一致；
  - 行业样本 < `min_group`（默认 8）时退化为全市场 z-score；
  - 行业组内 std == 0（全组同值）时该组返回 0，避免除零；
  - 输入 NaN 原样保留，不参与均值/标准差计算。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 行业组内最少样本数：低于它时组内均值/标准差噪声太大，退化为全市场标准化
DEFAULT_MIN_GROUP = 8


def winsorize(
    series: pd.Series,
    lower: float = 0.01,
    upper: float = 0.99,
) -> pd.Series:
    """按分位缩尾（winsorize）：把低于 lower 分位/高于 upper 分位的值截断到边界。

    经济含义：市值、PE 等指标常有极端值（次新股、重组股），直接算 z-score 会把
    整组均值拉偏、把其它股票压到负无穷。缩尾保留极端值"方向"但限制其影响力，
    是横截面因子处理的标准第一步。

    参数:
        series: 原始数值列（可含 NaN）
        lower: 下分位（默认 1%）
        upper: 上分位（默认 99%）

    返回:
        缩尾后的 Series（与输入同索引，NaN 保留）。
    """
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    numeric = pd.to_numeric(series, errors="coerce").astype("float64")
    valid = numeric.dropna()
    if valid.empty:
        return numeric
    lo = float(valid.quantile(lower))
    hi = float(valid.quantile(upper))
    if not np.isfinite(lo) or not np.isfinite(hi) or lo > hi:
        # 数据退化（全同值/极端分位重叠）时不做缩尾，交给后续防除零逻辑
        return numeric
    return numeric.clip(lower=lo, upper=hi)


def percentile_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """0–1 分位排名（同 `rank(pct=True)`），NaN 保留。

    参数:
        series: 原始数值列
        ascending: True=数值越大分位越高；False=数值越小分位越高（如 PE）

    返回:
        0–1 的 Series，NaN 原样保留。
    """
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, ascending=ascending)


def industry_neutral_zscore(
    df: pd.DataFrame,
    value_col: str,
    industry_col: str = "industry_l1",
    min_group: int = DEFAULT_MIN_GROUP,
) -> pd.Series:
    """行业内 z-score 中性化（横截面标准化）。

    计算逻辑（逐行）：
      1. 全市场 z = (x - 全市场均值) / 全市场总体标准差（兜底）；
      2. 行业组 z = (x - 组内均值) / 组内总体标准差；
      3. 组内有效样本 >= min_group 且组内 std > 0 → 用组内 z；
         否则用全市场 z。

    **为什么小样本行业要退化到全市场 z-score？**
      一个行业只有 3–5 只股票时，组内均值/标准差的抽样误差极大：
      只要其中一只涨停，整组 z 值都会剧烈跳动，标准化反而制造噪声；
      退化为全市场 z-score 虽然牺牲了行业中性，但至少数值稳定、可比。
      同理，组内 std == 0（全组同值，如全部停牌）无法除，返回 0（等于行业中位）。

    经济含义：z=+1 表示"比同行业平均水平高一个行业标准差"，
    这样跨行业比较的是**个股在行业内的相对位置**，而不是绝对数值。

    参数:
        df: 横截面 DataFrame（一行一只股票）
        value_col: 要标准化的数值列名（缺失时返回全 NaN，不抛异常）
        industry_col: 行业列名（缺失时视为全市场一个组）
        min_group: 行业最少样本数，低于则退化全市场

    返回:
        与 df 同索引的 z-score Series；输入 NaN 处保留 NaN。
    """
    if value_col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    values = pd.to_numeric(df[value_col], errors="coerce").astype("float64")

    if industry_col in df.columns:
        industries = df[industry_col].astype("object")
        # 行业缺失的股票归到"未知"组，而不是丢弃（否则会凭空少样本）
        industries = industries.where(industries.notna(), "未知")
    else:
        industries = pd.Series("全市场", index=df.index, dtype="object")

    # ---------- 全市场兜底 z ----------
    global_mean = values.mean(skipna=True)
    global_std = values.std(ddof=0, skipna=True)
    if pd.isna(global_mean):
        global_z = pd.Series(np.nan, index=df.index, dtype="float64")
    elif pd.isna(global_std) or float(global_std) == 0.0:
        # 全市场同值：没有区分度，z 统一为 0（不给 NaN，避免下游丢样本）
        global_z = values.where(values.isna(), 0.0).astype("float64")
    else:
        global_z = (values - float(global_mean)) / float(global_std)

    # ---------- 行业组 z ----------
    grouped = values.groupby(industries, dropna=False)
    group_mean = grouped.transform("mean")
    group_std = grouped.transform("std", ddof=0)
    group_count = grouped.transform("count")

    zero_std = group_std.notna() & (group_std == 0.0)
    safe_std = group_std.replace(0.0, np.nan)
    group_z = (values - group_mean) / safe_std

    eligible = (group_count >= int(min_group)) & group_std.notna() & (group_std > 0.0)
    result = global_z.where(~eligible, group_z)
    # 组内样本充足但全组同值 → 0（在组内就是中位数）
    result = result.where(~(zero_std & (group_count >= int(min_group))), 0.0)
    # 输入缺失的行保持 NaN（不把缺失当 0 处理）
    result = result.where(values.notna())
    return result.astype("float64")


__all__ = [
    "DEFAULT_MIN_GROUP",
    "winsorize",
    "percentile_rank",
    "industry_neutral_zscore",
]
