"""
因子库（方案 C2）
==================
输入：**某个交易日的横截面 DataFrame**（一行一只股票，列是原始指标，如 pe_ttm/pb/
momentum_20/volatility_20/amount_ma20）。
输出：与输入同索引的 `pd.Series`，取值 **0–1 分位（越大越好）**，NaN 原样保留。

统一约定（避免每个因子各写一套分位逻辑）：
  - 分位用 `Series.rank(pct=True)`：1 只股票时为 1.0；有并列时取平均名次；
  - `ascending=True` 表示"数值越大越靠前"，`ascending=False` 表示"数值越小越靠前"；
  - 缺失值不参与排名，保持 NaN（**不 fillna(0)**，否则会把缺数据当成最差）。

经济含义与数据局限（每只因子都必须在 docstring 里写清）：
  价值(value)     低 PE / PB / PS 更好；
  质量(quality)   高 ROE、低负债更好，但本系统当前**没有财报数据**，用 1/PB 与股息率代理；
  动量(momentum)  过去 20/60 日涨幅越大越好；
  波动(volatility) 年化波动越低越好（同样收益下波动小 = 风险调整后收益高）；
  流动性(liquidity) 20 日均成交额越大越好（可交易性、冲击成本低）；
  情绪(sentiment) 新闻情感；当前无个股级新闻数据时返回中性 0.5。

局限说明（诚实标注，避免误用）：
  - 所有因子都是**相对分位**，不是绝对好坏：全市场都高估时，最低 PE 也可能很贵；
  - PB/PS/PE 为负（亏损、资不抵债）在价值因子中按"最差"处理（fillna(0)），
    但这会把"亏损成长股"与"造假股"混在一起，A 股策略层需再叠加质量过滤；
  - 行业中性化不在这里做，见 `engine/cross_section.industry_neutral_zscore`；
  - 单日横截面只反映一天，因子有效性（IC/IR）需在回测层检验（方案阶段 6）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# 基础工具
# ============================================================

def _percentile(series: pd.Series, ascending: bool = True) -> pd.Series:
    """统一的分位函数：`rank(pct=True)`，NaN 保留为 NaN。

    参数:
        series: 原始数值列（可含 NaN）
        ascending: True=数值越大分位越高；False=数值越小分位越高

    返回:
        0–1 的分位 Series（与输入同索引）；全 NaN 时返回全 NaN。
    """
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, ascending=ascending)


def _column(df: pd.DataFrame, *names: str) -> pd.Series:
    """按候选列名取第一列存在的数值列；都不存在时返回全 NaN。"""
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _neutral(index: pd.Index) -> pd.Series:
    """中性分位：无数据时返回 0.5（不奖励也不惩罚）。"""
    return pd.Series(0.5, index=index, dtype="float64")


def _combine(components: list[tuple[pd.Series, float]], index: pd.Index) -> pd.Series:
    """按权重合成多个分位分量。

    - 某分量**整列为空**（说明数据源根本没有该字段）时跳过并重新归一化权重，
      避免"没数据 = 最差"的系统性偏差；
    - 行级缺失（个别股票缺字段）用 0 填充，体现"该维度无优势"；
    - 所有分量都不可用时返回中性 0.5。
    """
    total_weight = 0.0
    acc: pd.Series | None = None
    for series, weight in components:
        if weight <= 0 or series.isna().all():
            continue
        filled = series.fillna(0.0)
        acc = filled * weight if acc is None else acc + filled * weight
        total_weight += weight
    if acc is None or total_weight <= 0:
        return _neutral(index)
    return (acc / total_weight).clip(0.0, 1.0)


# ============================================================
# 六大因子
# ============================================================

def value_factor(df: pd.DataFrame) -> pd.Series:
    """价值因子：低 PE / 低 PB / 低 PS 更好（0–1 分位）。

    经济含义：估值越低，买入后回归合理估值的空间越大（格雷厄姆式安全边际）。
    PE<=0（亏损）或 PB/PS<=0（资不抵债/无营收）视为**最差**：先置 NaN 再 fillna(0)。

    数据局限：
      - 只看估值不看盈利质量，低估可能是"价值陷阱"（行业衰退、财务造假）；
      - PE/PB 受会计口径、周期股盈利波动影响大（周期顶部 PE 低但风险高）；
      - ST/退市股、次新股估值失真，需要上游股票池过滤。

    权重：PE 0.40 / PB 0.30 / PS 0.30（可调；PB 对银行地产更适用，PS 对成长股更适用）。
    """
    pe = _column(df, "pe_ttm", "pe")
    pb = _column(df, "pb", "pb_mrq")
    ps = _column(df, "ps_ttm", "ps")

    # 负值/零值视为无意义估值 → NaN → 在合成时 fillna(0.0) 即最差分位
    pe_pct = _percentile(pe.where(pe > 0), ascending=False).fillna(0.0)
    pb_pct = _percentile(pb.where(pb > 0), ascending=False).fillna(0.0)
    ps_pct = _percentile(ps.where(ps > 0), ascending=False).fillna(0.0)

    # 整列都无有效值时不纳入合成（_combine 会跳过）
    pe_pct = pe_pct if (pe > 0).any() else pd.Series(np.nan, index=df.index)
    pb_pct = pb_pct if (pb > 0).any() else pd.Series(np.nan, index=df.index)
    ps_pct = ps_pct if (ps > 0).any() else pd.Series(np.nan, index=df.index)

    return _combine([(pe_pct, 0.40), (pb_pct, 0.30), (ps_pct, 0.30)], df.index)


def quality_factor(df: pd.DataFrame) -> pd.Series:
    """质量因子：高 ROE、低负债更好（0–1 分位）。

    **数据局限（重要）**：本系统当前没有财报数据（ROE/负债率/现金流），
    只能用行情数据做代理：
      - 1 / PB 代理 ROE：PB = 市值/净资产，在 ROE 稳定时 PB 越低 ROE 越高（0.60 权重）；
      - 股息率 dv_ttm 代理现金质量：能持续分红说明盈利真实、现金流健康（0.40 权重）。
    真实 ROE、资产负债率接入后应替换本函数（届时 PB 权重应下调）。

    经济含义：同样便宜的公司，赚钱能力更强、财务更稳的更有长期价值。
    """
    pb = _column(df, "pb", "pb_mrq")
    dv = _column(df, "dv_ttm", "dividend_yield")

    roe_proxy = _percentile((1.0 / pb.where(pb > 0)), ascending=True).fillna(0.0)
    dv_pct = _percentile(dv.where(dv >= 0), ascending=True).fillna(0.0)

    roe_proxy = roe_proxy if (pb > 0).any() else pd.Series(np.nan, index=df.index)
    dv_pct = dv_pct if (dv >= 0).any() else pd.Series(np.nan, index=df.index)

    return _combine([(roe_proxy, 0.60), (dv_pct, 0.40)], df.index)


def momentum_factor(df: pd.DataFrame) -> pd.Series:
    """动量因子：过去 20 日 + 60 日涨幅越大越好（0–1 分位）。

    原始动量 = momentum_20 + 0.5 × momentum_60（20 日权重更高，兼顾短期强度；
    60 日项用于过滤"一日游"脉冲）。两者都缺失（次新股）时给中性 0.5，而不是最差。

    经济含义：趋势延续（A 股散户结构下动量效应显著，但反转也常见）。
    数据局限：
      - 未做复权校验时送转会造成假动量（上游必须保证 adj_factor 正确）；
      - 涨停板/停牌会扭曲短期动量，策略层需用可成交性再过滤；
      - 动量因子在震荡市容易失效，需与波动/价值因子组合。
    """
    mom20 = _column(df, "momentum_20", "mom_20")
    mom60 = _column(df, "momentum_60", "mom_60")
    raw = mom20 + 0.5 * mom60
    # 只有两项都无数据时才是 NaN；此时给中性，不惩罚次新股
    return _percentile(raw, ascending=True).fillna(0.5)


def volatility_factor(df: pd.DataFrame) -> pd.Series:
    """波动因子：低波动更好（0–1 分位，对波动率取反向排名）。

    经济含义：低波动异象——同等收益下波动小的股票风险调整后收益更高，
    且在回撤期更抗跌。这里直接用 `volatility_20`（20 日年化波动 %）反向分位。

    数据局限：
      - 历史波动不代表未来风险，低波动也可能是流动性差导致的价格黏滞；
      - 次新股/停牌恢复期波动异常，需上游过滤；
      - 与动量因子高度相关（高动量常伴随高波动），合成时避免重复惩罚。
    """
    vol = _column(df, "volatility_20", "volatility")
    return _percentile(vol, ascending=False).fillna(0.5)


def liquidity_factor(df: pd.DataFrame) -> pd.Series:
    """流动性因子：20 日均成交额越大越好（0–1 分位）。

    经济含义：成交额大 = 买卖容易、冲击成本低、机构容量大；小票虽然弹性大，
    但真实交易时滑点会吃掉大部分超额收益。

    数据局限：
      - 成交额大也可能是**放量出货/情绪高潮**的顶部信号（本因子默认"越大越好"，
        若要做"适中最好"的倒 U 型，可在策略层对极端分位再打折）；
      - 成交额单位是元；本函数优先取 `amount_ma20`，没有时退回单日 `amount`；
      - 停牌/一字板日成交额失真，需上游剔除。
    """
    amount = _column(df, "amount_ma20", "amount")
    return _percentile(amount, ascending=True).fillna(0.5)


def sentiment_factor(df: pd.DataFrame) -> pd.Series:
    """情绪因子：新闻情感越好分位越高；**无个股新闻数据时中性 0.5**。

    输入约定：若横截面里存在 `news_sentiment` 列（取值 -1..1，来自
    `NewsArticle.sentiment_score` 聚合），按 `0.5 + 0.5 × clip(x, -1, 1)` 线性映射到 0–1；
    没有该列时返回全 0.5（中性），**绝不因为缺新闻而给 0**。

    经济含义：利好消息短期推升风险偏好，利空压制股价。
    数据局限：
      - 新闻只能按标题/正文关键词匹配到个股，覆盖率低且滞后；
      - LLM 情感判断有噪声（反讽、政策中性措辞），样本少时向中性收缩；
      - 新闻情绪容易被"已兑现"行情反向交易，策略层只作辅助因子。
    """
    if "news_sentiment" not in df.columns:
        return _neutral(df.index)
    raw = pd.to_numeric(df["news_sentiment"], errors="coerce").clip(-1.0, 1.0)
    return (0.5 + 0.5 * raw).fillna(0.5)


# ============================================================
# 因子合成
# ============================================================

def composite_factor(
    factors: dict[str, pd.Series],
    weights: dict[str, float] | None = None,
) -> pd.Series:
    """把多个因子分位加权成 0–1 的综合因子分。

    参数:
        factors: {因子名: 0–1 分位 Series}
        weights: {因子名: 权重}；缺省等权。权重会自动归一化；
                 只对 `factors` 中实际存在的因子计权（缺失因子不占权重）。

    返回:
        0–1 的加权综合分；某行所有因子都缺失时返回 0.5（中性）。
        不做 round——保留浮点精度，由调用方决定展示精度。
    """
    if not factors:
        return pd.Series(dtype="float64")
    index = next(iter(factors.values())).index
    names = list(factors.keys())
    if weights is None:
        weight_map = {name: 1.0 for name in names}
    else:
        weight_map = {name: float(weights.get(name, 0.0)) for name in names}

    total_weight = 0.0
    weighted_sum: pd.Series | None = None
    available_weight: pd.Series | None = None
    for name, factor in factors.items():
        weight = weight_map.get(name, 0.0)
        if weight <= 0:
            continue
        factor = factor.reindex(index)
        valid = factor.notna()
        total_weight += weight
        weighted_sum = (factor.fillna(0.0) * weight) if weighted_sum is None \
            else weighted_sum + factor.fillna(0.0) * weight
        row_weight = valid.astype("float64") * weight
        available_weight = row_weight if available_weight is None else available_weight + row_weight

    if weighted_sum is None or total_weight <= 0:
        return _neutral(index)
    # 逐行按"该行实际可用的因子权重"归一化，避免个别因子缺失把综合分拉向 0
    result = weighted_sum / available_weight.replace(0.0, np.nan)
    return result.fillna(0.5).clip(0.0, 1.0)


__all__ = [
    "value_factor",
    "quality_factor",
    "momentum_factor",
    "volatility_factor",
    "liquidity_factor",
    "sentiment_factor",
    "composite_factor",
]
