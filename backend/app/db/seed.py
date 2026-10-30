"""
种子数据：策略元信息（A3/A4 + B1–B5）
=====================================
参数的**唯一定义处**：后端 `strategy/strategies.py` 的 SPECS 也引用同一份默认值，
前端则通过 `/algo/list` 拿 `params_schema` 渲染表单——避免三处各写一套默认值（V4 的坑）。

A1/A2 已按真实回测结果下线：A1 近三年收益 -16.4%、A2 -33.7%（最大回撤 -59%）。
"""
from __future__ import annotations

ALGO_SEED: list[dict] = [
    {
        "algo_code": "a3",
        "algo_name": "央企 × 综合评分TOP",
        "algo_type": "score",
        "short_desc": "央企属性 + 本地六因子综合评分（价值/质量/动量/波动/流动性/情绪）",
        "full_desc": (
            "筛选条件：① `dim_stock.ownership = 央企`（基于央企名录 + 名称规则，见 core/soe.py）；"
            "② 综合评分 ≥ min_score（0–10 分制，默认 6.0 分）。"
            "排序：综合评分降序。\n"
            "回测（2025-01-01 起）：收益 +22.9%、最大回撤 -10.4%、Sharpe 0.85。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "min_score", "label": "最低综合评分(0-10)", "type": "number",
             "default": 6.0, "min": 0, "max": 10, "step": 0.5},
        ],
        "sort_order": 1,
    },
    {
        "algo_code": "a4",
        "algo_name": "高股息 × 低估值",
        "algo_type": "value",
        "short_desc": "股息率 ≥ 阈值 且 0 < PE < 阈值 = 红利防守型价值股",
        "full_desc": (
            "筛选条件：① PE_TTM > 0；② PE_TTM < pe_max（默认 15）；"
            "③ 股息率 dv_ttm ≥ div_min（默认 4%）。"
            "排序：股息率降序。\n"
            "回测（2024-09-24 起）：收益 +37.8%、最大回撤 -18.2%、胜率 60%。"
            "需注意「股息陷阱」——一次性高分红会污染 TTM 口径。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "pe_max", "label": "PE 上限", "type": "number",
             "default": 15, "min": 0, "max": 100, "step": 1},
            {"key": "div_min", "label": "股息率下限(%)", "type": "number",
             "default": 4, "min": 0, "max": 20, "step": 0.5},
        ],
        "sort_order": 2,
    },
    {
        "algo_code": "b1",
        "algo_name": "双均线动量突破",
        "algo_type": "trend",
        "short_desc": "EMA5/EMA20 三日金叉 + 放量 + 站上 MA60，按 10 日动量降序",
        "full_desc": (
            "筛选条件：① 近 3 日 EMA5 上穿 EMA20；② 收盘 > EMA20 且 EMA20 上行；"
            "③ ROC10 > 0；④ 量能 ≥ 5 日均量 × min_vol_ratio（默认 1.0）；"
            "⑤ 收盘 > MA60；⑥ 20 日均成交额 ≥ min_amount（默认 2 亿）。"
            "排序：ROC10 降序。日线近似报告中的 60 分钟突破口径。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "min_vol_ratio", "label": "量能放大倍数", "type": "number",
             "default": 1.0, "min": 0.1, "max": 10, "step": 0.1},
            {"key": "min_amount", "label": "20日均额下限(元)", "type": "number",
             "default": 2.0e8, "min": 0, "max": 1.0e12, "step": 1.0e7},
        ],
        "sort_order": 3,
    },
    {
        "algo_code": "b2",
        "algo_name": "RSI6 超卖回归",
        "algo_type": "reversal",
        "short_desc": "RSI6 超卖 + 止跌 K 线 + 中期趋势未破，按 RSI6 升序",
        "full_desc": (
            "筛选条件：① RSI6 < rsi_max（默认 20）；② 当日收阳或收盘不低于昨收；"
            "③ 收盘 > MA120 × trend_dev（默认 0.9，剔除深熊）；"
            "④ 60 日振幅 > amp_min（默认 15%）；⑤ 20 日均成交额达标。"
            "排序：RSI6 升序（越超卖越靠前）。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "rsi_max", "label": "RSI6 上限", "type": "number",
             "default": 20, "min": 1, "max": 50, "step": 1},
            {"key": "amp_min", "label": "60日振幅下限(%)", "type": "number",
             "default": 15, "min": 0, "max": 200, "step": 1},
            {"key": "trend_dev", "label": "MA120 趋势容忍度", "type": "number",
             "default": 0.9, "min": 0.5, "max": 1.5, "step": 0.05},
            {"key": "min_amount", "label": "20日均额下限(元)", "type": "number",
             "default": 2.0e8, "min": 0, "max": 1.0e12, "step": 1.0e7},
        ],
        "sort_order": 4,
    },
    {
        "algo_code": "b3",
        "algo_name": "KDJ 低位金叉",
        "algo_type": "reversal",
        "short_desc": "J 值低位 + K 上穿 D + 站上 EMA60，按 J 值升序",
        "full_desc": (
            "筛选条件：① 今日或昨日 J < j_max（默认 10）；② 当日 K 上穿 D；"
            "③ K < k_max（默认 30）；④ 收盘 > EMA60；⑤ 收盘不低于昨收；"
            "⑥ 20 日均成交额达标。排序：J 值升序。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "k_max", "label": "K 值上限", "type": "number",
             "default": 30, "min": 0, "max": 100, "step": 1},
            {"key": "j_max", "label": "J 值上限", "type": "number",
             "default": 10, "min": -100, "max": 100, "step": 1},
            {"key": "min_amount", "label": "20日均额下限(元)", "type": "number",
             "default": 2.0e8, "min": 0, "max": 1.0e12, "step": 1.0e7},
        ],
        "sort_order": 5,
    },
    {
        "algo_code": "b4",
        "algo_name": "布林挤压突破",
        "algo_type": "breakout",
        "short_desc": "布林带宽 60 日低分位 + 突破上轨新高 + 放量，按带宽分位升序",
        "full_desc": (
            "筛选条件：① 前一日布林带宽处于 60 日最低 squeeze_pct%（默认 20）分位；"
            "② 收盘突破上轨且创 20 日新高；③ 量能 ≥ 5 日均量 × vol_mult（默认 1.5）；"
            "④ 20 日区间宽度 < range_max（默认 10%）；⑤ 20 日均成交额达标；"
            "⑥ 沪深300 收盘 ≥ MA20 × 0.995。排序：带宽分位升序。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "squeeze_pct", "label": "带宽分位上限(%)", "type": "number",
             "default": 20, "min": 1, "max": 100, "step": 1},
            {"key": "vol_mult", "label": "突破量能倍数", "type": "number",
             "default": 1.5, "min": 0.1, "max": 10, "step": 0.1},
            {"key": "range_max", "label": "20日区间宽度上限(%)", "type": "number",
             "default": 10, "min": 0, "max": 100, "step": 1},
            {"key": "min_amount", "label": "20日均额下限(元)", "type": "number",
             "default": 2.0e8, "min": 0, "max": 1.0e12, "step": 1.0e7},
        ],
        "sort_order": 6,
    },
    {
        "algo_code": "b5",
        "algo_name": "布林下轨 × KDJ 双确认",
        "algo_type": "reversal",
        "short_desc": "触及布林下轨 + J 值超卖 + 缩量止跌，按 J 值升序",
        "full_desc": (
            "筛选条件：① 最低价触及布林下轨；② 今日或昨日 J < j_max（默认 0）；"
            "③ 缩量（成交量 < 5 日均量）；④ 当日收阳或收盘不低于昨收；"
            "⑤ 收盘 > MA120 或偏离下轨不超过 dev_max%（默认 8%）；⑥ 20 日均成交额达标；"
            "⑦ 沪深300 当日跌幅 > -2%。排序：J 值升序。"
        ),
        "params_schema": [
            {"key": "limit", "label": "返回数量", "type": "select", "default": 20,
             "options": [10, 20, 50]},
            {"key": "j_max", "label": "J 值上限", "type": "number",
             "default": 0, "min": -100, "max": 100, "step": 1},
            {"key": "dev_max", "label": "下轨偏离容忍(%)", "type": "number",
             "default": 8, "min": 0.1, "max": 50, "step": 0.1},
            {"key": "min_amount", "label": "20日均额下限(元)", "type": "number",
             "default": 2.0e8, "min": 0, "max": 1.0e12, "step": 1.0e7},
        ],
        "sort_order": 7,
    },
]

__all__ = ["ALGO_SEED"]
