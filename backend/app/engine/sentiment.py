"""
市场/板块情绪打分（方案 C5）
=============================
保留旧版 `services/sentiment.py` 的算法思路，但做了三项 V5 修正：

1. **所有函数都接受 `None` 并返回中性分**（旧版在 `margin_data` 为 None 时会 AttributeError）；
2. 新闻情绪改为从 `NewsArticle.sentiment_score`（LLM 标注的 -1..1）聚合，
   而不是旧版的"论坛热度"（论坛热度没有可持续数据源，V5 已取消采集）；
3. 提供 `combine_sentiment()`：缺子项时按权重**按比例分给可用项**，
   避免"北向长期无数据"把情绪分永久拖回 5.0 —— 这正是旧版的问题
   （四子项各 25%，北向恒为 5，相当于 25% 权重永远失效）。

子项口径：
  - 融资(margin)    ：融资余额日变化方向 + 融券占比 + 近 3 日连续趋势（旧算法保留）
  - 北向(northward) ：净流入线性映射 -50 亿→2 分、0→5 分、50 亿→8 分（无数据→中性）
  - 新闻(news)      ：5 + mean(sentiment_score) × 5；样本 < 3 条时向 5 分收缩 50%
  - 资金流(moneyflow)：主力净流入线性映射 ±10 亿→±3 分（单位：元）

输出统一 0–10、`round(..., 2)`；缺失用 `config.NEUTRAL_SCORE`。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Mapping, Sequence

from app.core.config import NEUTRAL_SCORE, SENTIMENT_WEIGHTS

logger = logging.getLogger(__name__)

# 板块情绪（不含新闻）三子项权重：新闻已升级为独立第三面，见 sector_analysis。
# 注意：这是**板块综合分口径**的权重，与 config.SENTIMENT_WEIGHTS（四子项旧口径）
# 保持同一风格、集中可调；当前三个板块情绪子项按 0.35/0.30/0.35 分配。
SECTOR_SENTIMENT_WEIGHTS: dict[str, float] = {
    "margin": 0.35,
    "northward": 0.30,
    "moneyflow": 0.35,
}

# 新闻样本少于该条数时向中性收缩（贝叶斯收缩，避免 1 条新闻决定板块情绪）
NEWS_MIN_SAMPLES = 3


# ============================================================
# 内部工具
# ============================================================

def _to_float(value: Any) -> float | None:
    """宽松转 float：None/空串/非法 → None，避免采集脏数据抛异常。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _clamp10(value: float) -> float:
    """裁剪到 [0, 10] 并保留两位小数。"""
    return round(float(min(10.0, max(0.0, value))), 2)


# ============================================================
# 子项 1：融资情绪
# ============================================================

def calc_margin_sentiment(
    margin_data: Mapping[str, Any] | None,
    recent_margin_changes: Sequence[float] | None = None,
) -> float:
    """融资情绪分（0–10）。

    经济含义：融资余额是杠杆资金的态度——余额增加说明投资者愿意借钱做多，
    连续增加是较强的看多信号；融券余额增加则代表做空力量增强。

    算法（保留旧版）：
      1. 当日融资变化：> +100 亿 +2.0；>0 +1.0；< -100 亿 -2.0；<0 -1.0；
      2. 融券占比（融券/融资）：<0.5% +0.5；>2% -1.0；
      3. 近 3 日融资变化同向：连续净买入 +1.5；连续净流出 -1.5；
      4. 从 5.0 起算，裁剪到 [0,10]。

    参数:
        margin_data: `MarginTrading` 当日数据（margin_change/margin_balance/short_balance），
                     允许为 None（返回中性分）
        recent_margin_changes: 最近若干日融资余额变化序列（亿元），可为 None

    返回:
        float: 0–10；无数据返回 5.0。
    """
    score = float(NEUTRAL_SCORE)
    if not margin_data:
        return _clamp10(score)

    change = _to_float(margin_data.get("margin_change"))
    if change is not None:
        if change > 100:
            score += 2.0
        elif change > 0:
            score += 1.0
        elif change < -100:
            score -= 2.0
        elif change < 0:
            score -= 1.0

    margin_balance = _to_float(margin_data.get("margin_balance")) or 0.0
    short_balance = _to_float(margin_data.get("short_balance")) or 0.0
    if margin_balance > 0:
        short_ratio = short_balance / margin_balance
        if short_ratio < 0.005:
            score += 0.5
        elif short_ratio > 0.02:
            score -= 1.0

    if recent_margin_changes:
        recent = [_to_float(x) for x in list(recent_margin_changes)[-3:]]
        valid = [x for x in recent if x is not None]
        if len(valid) >= 3:
            if all(x > 0 for x in valid):
                score += 1.5
            elif all(x < 0 for x in valid):
                score -= 1.5

    return _clamp10(score)


# ============================================================
# 子项 2：北向资金
# ============================================================

def calc_northward_sentiment(northward_net: float | None) -> float:
    """北向资金情绪分（0–10）。

    经济含义：北向资金被视作"聪明钱"，净流入代表外资风险偏好上升。
    线性映射：-50 亿→2 分、0→5 分、+50 亿→8 分、+100 亿→10 分（负向最多扣到 2）。

    **数据局限**：北向资金自 2024-08 起可能长期无披露，本函数对 None 返回中性 5.0，
    并建议调用方用 `combine_sentiment()` 重新分配权重，而不是让缺失项占着权重。

    参数:
        northward_net: 当日北向净流入（亿元），None 表示无数据

    返回:
        float: 0–10；无数据返回 5.0。
    """
    net = _to_float(northward_net)
    if net is None:
        return _clamp10(NEUTRAL_SCORE)
    if net >= 0:
        score = 5.0 + min(net / 50.0 * 3.0, 5.0)
    else:
        score = 5.0 + max(net / 50.0 * 3.0, -3.0)
    return _clamp10(score)


# ============================================================
# 子项 3：新闻情绪
# ============================================================

def calc_news_sentiment(sentiment_scores: Iterable[float] | None) -> float:
    """新闻情绪分（0–10）。

    经济含义：把当日相关新闻的 LLM 情感分（-1 利空 … +1 利好）聚合成板块/市场情绪。
    映射：`score = 5 + mean(sentiment_score) × 5`。

    **小样本收缩（贝叶斯收缩）**：
      样本 < 3 条时，均值受单条新闻噪声影响极大（1 条利好就接近 10 分）。
      对偏离中性的部分乘 0.5：`score = 5 + (原始分 - 5) × 0.5`，
      相当于把观测值向先验（中性 5 分）收缩一半，样本越多越相信数据。

    参数:
        sentiment_scores: 情感分序列（-1..1）；None/空 → 中性 5.0

    返回:
        float: 0–10；无数据返回 5.0。
    """
    if sentiment_scores is None:
        return _clamp10(NEUTRAL_SCORE)
    valid = [
        x for x in (_to_float(v) for v in sentiment_scores) if x is not None
    ]
    if not valid:
        return _clamp10(NEUTRAL_SCORE)

    mean_score = sum(valid) / len(valid)
    mean_score = max(-1.0, min(1.0, mean_score))
    score = 5.0 + mean_score * 5.0
    if len(valid) < NEWS_MIN_SAMPLES:
        score = 5.0 + (score - 5.0) * 0.5
    return _clamp10(score)


# ============================================================
# 子项 4：板块资金流
# ============================================================

def calc_moneyflow_sentiment(net_flow: float | None) -> float:
    """主力资金流情绪分（0–10）。

    经济含义：主力（大单）净流入代表机构资金态度。
    线性映射（旧算法）：-10 亿→2 分、0→5 分、+10 亿→8 分，正负各封顶 ±3 分。

    参数:
        net_flow: 当日主力净流入（**元**），None 表示无数据

    返回:
        float: 0–10；无数据返回 5.0。
    """
    flow = _to_float(net_flow)
    if flow is None:
        return _clamp10(NEUTRAL_SCORE)
    flow_yi = flow / 1e8  # 元 → 亿元
    score = 5.0 + min(max(flow_yi / 10.0 * 3.0, -3.0), 3.0)
    return _clamp10(score)


# ============================================================
# 合成（缺项权重再分配）
# ============================================================

def combine_sentiment(
    parts: Mapping[str, float | None],
    weights: Mapping[str, float] | None = None,
) -> tuple[float, dict]:
    """把情绪子项加权成 0–10，并在子项缺失时**按比例重新分配权重**。

    为什么必须再分配：
      旧版四子项各 25%，北向长期无数据却固定给 5 分参与加权，
      等于情绪分被永久锚定在 5 附近，真实信号被稀释。
      V5 改为：只对**有数据的子项**按权重归一化，缺项不参与、也不拉回 5。

    例：margin=8 / northward=None / moneyflow=8，权重 0.35/0.30/0.35
      → 有效权重 margin=0.5、moneyflow=0.5 → 综合 = 8.0（而不是 6.75）

    参数:
        parts: {子项名: 0–10 分或 None}
        weights: {子项名: 权重}；缺省用 `config.SENTIMENT_WEIGHTS`。
                 只对 parts 中实际出现的子项计权（缺失项权重自动分给可用项）。

    返回:
        (score, detail)
        - score: 0–10，保留两位小数；全部子项缺失时返回 `NEUTRAL_SCORE`（5.0）
        - detail: `{"effective_weights": {...}, "available": [...], "missing": [...],
                    "raw_weights": {...}}`，可直接落 JSONB 供前端展示。
    """
    weight_map = dict(SENTIMENT_WEIGHTS if weights is None else weights)
    available: dict[str, float] = {}
    missing: list[str] = []
    for name, value in parts.items():
        numeric = _to_float(value)
        if numeric is None:
            missing.append(name)
        else:
            available[name] = _clamp10(numeric)

    detail: dict = {
        "raw_weights": {k: round(float(v), 6) for k, v in weight_map.items()},
        "available": sorted(available.keys()),
        "missing": sorted(missing),
        "effective_weights": {},
    }

    total_weight = sum(
        float(weight_map.get(name, 0.0)) for name in available
    )
    if not available or total_weight <= 0:
        detail["effective_weights"] = {}
        detail["score"] = _clamp10(NEUTRAL_SCORE)
        detail["reason"] = "no_available_sentiment_parts"
        return _clamp10(NEUTRAL_SCORE), detail

    effective = {
        name: float(weight_map.get(name, 0.0)) / total_weight
        for name in available
    }
    score = sum(available[name] * effective[name] for name in available)
    score = _clamp10(score)
    detail["effective_weights"] = {
        k: round(v, 6) for k, v in effective.items()
    }
    detail["score"] = score
    return score, detail


__all__ = [
    "SECTOR_SENTIMENT_WEIGHTS",
    "NEWS_MIN_SAMPLES",
    "calc_margin_sentiment",
    "calc_northward_sentiment",
    "calc_news_sentiment",
    "calc_moneyflow_sentiment",
    "combine_sentiment",
]
