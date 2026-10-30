"""
内置策略（A3/A4 价值守正 + B1–B5 短线进攻）与候选池构建
========================================================
**一个策略 = 一个候选池构建步骤 + 一个排序评分步骤**：

  - `build_candidates(algo_code, trade_date, params, db)`：
      一条 SQL 横截面取数（`fact_stock_daily` + `fact_feature_daily` +
      `fact_stock_score_daily` + `fact_moneyflow` + `dim_stock`，全部 LEFT JOIN），
      先在 SQL 里做基础可用性过滤，再在 pandas 里做统一的 A 股口径过滤与策略条件。
      **禁止 N+1 查询**：候选池只查一次库，一次取回当日全市场。
      B4/B5 额外读取沪深300 指数状态（`market_indices`），把市场门槛放进 `frame.attrs["market"]`。
  - `rank_signals(algo_code, df, params)`：纯函数（无 DB），把候选池按策略口径再筛一次、
      排序、截断到 `limit`，并生成 `rank / score / metrics`，供 API 直接序列化。
      其中 **score = 本策略排序指标的原始值**。

现存策略（A1/A2 已按回测结果下线：A1 三年 -16%、A2 -34% 且回撤 59%，不具备实用性）：
  A3 央企 × 综合评分
      ``dim_stock.ownership == '央企'`` 且 ``composite_score >= min_score(默认6)``，
      按 ``composite_score`` 降序。回测 2025-01 起 +22.9%、最大回撤 -10.4%、Sharpe 0.85。
  A4 高股息 × 低估值
      ``pe_ttm > 0`` 且 ``pe_ttm < pe_max(默认15)`` 且 ``dv_ttm >= div_min(默认4%)``，
      按 ``dv_ttm`` 降序。回测 2024-09 起 +37.8%、最大回撤 -18.2%、胜率 60%。

新增短线策略（来自《短线量化策略设计报告》，日线落地口径）：
  B1 双均线动量突破
      EMA5/EMA20 近 3 日金叉、收盘站上 EMA20 且 EMA20 上行、ROC10 > 0、
      量能 ≥ 5 日均量 × min_vol_ratio、收盘 > MA60、20 日均额 ≥ min_amount，
      按 ``roc10`` 降序。
  B2 RSI6 超卖回归
      ``rsi6 < rsi_max(默认20)`` 且当日出现止跌 K 线（收阳或收盘不低于昨收）、
      收盘 > MA120 × trend_dev(默认0.9，剔除深熊)、60 日振幅 > amp_min(默认15%)、
      20 日均额达标，按 ``rsi6`` 升序（越超卖越靠前）。
  B3 KDJ 低位金叉
      今日或昨日 J < j_max(默认10)、当日 K 上穿 D、K < k_max(默认30)、
      收盘 > EMA60、收盘不低于昨收、20 日均额达标，按 ``kdj_j`` 升序。
  B4 布林挤压突破
      前一日带宽处于 60 日最低 squeeze_pct% 分位、收盘突破上轨且创 20 日新高、
      量能 ≥ 5 日均量 × vol_mult、20 日区间宽度 < range_max、20 日均额达标，
      且沪深300 收盘 ≥ MA20 × 0.995（市场状态门槛），按带宽分位升序。
  B5 布林下轨 × KDJ 双确认
      最低价触及布林下轨、今日或昨日 J < j_max(默认0)、缩量、
      止跌 K 线、收盘 > MA120 或偏离下轨不超过 dev_max%、20 日均额达标，
      且沪深300 当日跌幅 > -2%（市场状态门槛），按 ``kdj_j`` 升序。

统一前置过滤（所有策略一致）：
  剔除 ST、status != 1（停牌/退市）、delist_date 已到、上市不足 60 个交易日、
  is_suspended、当日无行情（无 fact_stock_daily 行）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import HISTORY_START, STRATEGY_DEFAULTS
from app.core.errors import DataUnavailableError, InvalidParamError, UnknownAlgoError
from app.db.models.dim import DimStock
from app.db.models.fact import (
    FactFeatureDaily,
    FactMoneyflow,
    FactStockDaily,
    FactStockScoreDaily,
    MarketIndex,
)
from app.db.models.meta import TradeCalendar

logger = logging.getLogger(__name__)

#: 次新股过滤口径（用户定稿：上市不足 60 个交易日不入选）
NEW_STOCK_MIN_TRADING_DAYS = 60
#: 无交易日历时的近似：60 个交易日 ≈ 84 个自然日（60 × 7 / 5）
_FALLBACK_NEW_STOCK_CALENDAR_DAYS = 84

#: 市场状态门槛使用的基准指数
BENCHMARK_INDEX_CODE = "sh000300"

#: 前端展示用"通用字段"（契约 5.2 data[] 里的顶层列）
DISPLAY_FIELDS: tuple[str, ...] = (
    "close", "pct_chg", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
    "macd_dif", "macd_dea", "rsi14", "composite_score",
)


# =====================================================================
# 策略元信息（params_schema 供前端渲染）
# =====================================================================

def _param(
    key: str,
    label: str,
    default: Any,
    minimum: float,
    maximum: float,
    kind: str = "number",
) -> dict[str, Any]:
    """构造一个参数 schema 项（契约 5.1 的 key/label/type/default/min/max）。"""
    return {
        "key": key,
        "label": label,
        "type": kind,
        "default": default,
        "min": minimum,
        "max": maximum,
    }


@dataclass(frozen=True)
class StrategySpec:
    """策略元信息。`defaults` 里是**归一化后**的参数默认值（含 limit）。"""

    code: str
    name: str
    params_schema: list[dict]
    defaults: dict
    algo_type: str = "technical"
    short_desc: str = ""
    full_desc: str = ""
    sort_order: int = 0

    def as_algo_item(self) -> dict:
        """转成契约 5.1 的算法清单项。"""
        return {
            "algo_code": self.code,
            "algo_name": self.name,
            "algo_type": self.algo_type,
            "short_desc": self.short_desc,
            "full_desc": self.full_desc,
            "params_schema": [dict(item) for item in self.params_schema],
            "is_active": True,
            "sort_order": self.sort_order,
        }


SPECS: dict[str, StrategySpec] = {
    "a3": StrategySpec(
        code="a3",
        name="央企 × 综合评分TOP",
        algo_type="score",
        sort_order=1,
        short_desc="央企中综合评分达标者，按综合评分降序",
        full_desc=(
            "在 dim_stock.ownership = '央企' 的标的中，保留综合评分 >= min_score 者，"
            "按 composite_score 降序取前 limit 只。"
        ),
        defaults={**dict(STRATEGY_DEFAULTS["a3"]), "min_score": 6.0},  # 0–10 分制，自洽兜底
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("min_score", "最低综合评分(0-10)", 6.0, 0.0, 10.0),
        ],
    ),
    "a4": StrategySpec(
        code="a4",
        name="高股息 × 低估值",
        algo_type="value",
        sort_order=2,
        short_desc="PE 为正且低于上限、股息率达标的标的，按股息率降序",
        full_desc=(
            "剔除 PE<=0，保留 PE_TTM < pe_max 且股息率 dv_ttm >= div_min（单位 %）者，"
            "按股息率降序取前 limit 只。"
        ),
        defaults=dict(STRATEGY_DEFAULTS["a4"]),
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("pe_max", "PE_TTM 上限", 15.0, 0.01, 1000.0),
            _param("div_min", "股息率下限(%)", 4.0, 0.0, 100.0),
        ],
    ),
    "b1": StrategySpec(
        code="b1",
        name="双均线动量突破",
        algo_type="trend",
        sort_order=3,
        short_desc="EMA5/EMA20 三日金叉 + 放量 + 站上 MA60，按 10 日动量降序",
        full_desc=(
            "EMA5 近 3 日上穿 EMA20（日线近似报告的 60 分钟突破），且收盘站上 EMA20、"
            "EMA20 向上、ROC10 > 0、成交量 ≥ 5 日均量 × min_vol_ratio、"
            "收盘 > MA60、20 日均成交额达标，按 10 日动量降序取前 limit 只。"
        ),
        defaults=dict(STRATEGY_DEFAULTS["b1"]),
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("min_vol_ratio", "量能放大倍数", 1.0, 0.1, 10.0),
            _param("min_amount", "20日均额下限(元)", 2.0e8, 0.0, 1.0e12),
        ],
    ),
    "b2": StrategySpec(
        code="b2",
        name="RSI6 超卖回归",
        algo_type="reversal",
        sort_order=4,
        short_desc="RSI6 超卖 + 止跌 K 线 + 中期趋势未破，按 RSI6 升序",
        full_desc=(
            "RSI6 < rsi_max（默认 20）且当日收阳或收盘不低于昨收、收盘 > MA120 × trend_dev、"
            "60 日振幅 > amp_min、20 日均成交额达标，按 RSI6 升序取前 limit 只。"
        ),
        defaults=dict(STRATEGY_DEFAULTS["b2"]),
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("rsi_max", "RSI6 上限", 20.0, 1.0, 50.0),
            _param("amp_min", "60日振幅下限(%)", 15.0, 0.0, 200.0),
            _param("trend_dev", "MA120 趋势容忍度", 0.9, 0.5, 1.5),
            _param("min_amount", "20日均额下限(元)", 2.0e8, 0.0, 1.0e12),
        ],
    ),
    "b3": StrategySpec(
        code="b3",
        name="KDJ 低位金叉",
        algo_type="reversal",
        sort_order=5,
        short_desc="J 值低位 + K 上穿 D + 站上 EMA60，按 J 值升序",
        full_desc=(
            "今日或昨日 J < j_max（默认 10）、当日 K 上穿 D、K < k_max（默认 30）、"
            "收盘 > EMA60、收盘不低于昨收、20 日均成交额达标，按 J 值升序取前 limit 只。"
        ),
        defaults=dict(STRATEGY_DEFAULTS["b3"]),
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("k_max", "K 值上限", 30.0, 0.0, 100.0),
            _param("j_max", "J 值上限", 10.0, -100.0, 100.0),
            _param("min_amount", "20日均额下限(元)", 2.0e8, 0.0, 1.0e12),
        ],
    ),
    "b4": StrategySpec(
        code="b4",
        name="布林挤压突破",
        algo_type="breakout",
        sort_order=6,
        short_desc="布林带宽 60 日低分位 + 突破上轨新高 + 放量，按带宽分位升序",
        full_desc=(
            "前一日布林带宽处于 60 日最低 squeeze_pct%（默认 20）分位、收盘突破上轨且创 20 日新高、"
            "成交量 ≥ 5 日均量 × vol_mult、20 日区间宽度 < range_max、20 日均成交额达标；"
            "并要求沪深300 收盘 ≥ MA20 × 0.995。按带宽分位升序取前 limit 只。"
        ),
        defaults=dict(STRATEGY_DEFAULTS["b4"]),
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("squeeze_pct", "带宽分位上限(%)", 20.0, 1.0, 100.0),
            _param("vol_mult", "突破量能倍数", 1.5, 0.1, 10.0),
            _param("range_max", "20日区间宽度上限(%)", 10.0, 0.0, 100.0),
            _param("min_amount", "20日均额下限(元)", 2.0e8, 0.0, 1.0e12),
        ],
    ),
    "b5": StrategySpec(
        code="b5",
        name="布林下轨 × KDJ 双确认",
        algo_type="reversal",
        sort_order=7,
        short_desc="触及布林下轨 + J 值超卖 + 缩量止跌，按 J 值升序",
        full_desc=(
            "最低价触及布林下轨、今日或昨日 J < j_max（默认 0）、缩量、"
            "当日收阳或收盘不低于昨收、收盘 > MA120 或偏离下轨不超过 dev_max%、"
            "20 日均成交额达标；并要求沪深300 当日跌幅 > -2%。按 J 值升序取前 limit 只。"
        ),
        defaults=dict(STRATEGY_DEFAULTS["b5"]),
        params_schema=[
            _param("limit", "返回数量上限", 20, 1, 1000, kind="integer"),
            _param("j_max", "J 值上限", 0.0, -100.0, 100.0),
            _param("dev_max", "下轨偏离容忍(%)", 8.0, 0.1, 50.0),
            _param("min_amount", "20日均额下限(元)", 2.0e8, 0.0, 1.0e12),
        ],
    ),
}

#: 排序口径： (字段, 是否升序)
_SORT_SPEC: dict[str, tuple[str, bool]] = {
    "a3": ("composite_score", False),      # 综合评分降序
    "a4": ("dv_ttm", False),               # 股息率降序
    "b1": ("roc10", False),                # 10 日动量降序
    "b2": ("rsi6", True),                  # 越超卖越靠前
    "b3": ("kdj_j", True),                 # J 值升序
    "b4": ("boll_width_pct60", True),      # 挤压越极致越靠前
    "b5": ("kdj_j", True),                 # J 值升序
}

#: 各策略 metrics 字段（该策略依据的全部指标；前端表格列由它动态生成）
_METRIC_FIELDS: dict[str, tuple[str, ...]] = {
    "a3": (
        "composite_score", "value_score", "quality_score", "momentum_score",
        "volatility_score", "liquidity_score", "sentiment_score",
        "ownership", "industry_l1", "close", "pct_chg", "pe_ttm", "pb",
    ),
    "a4": (
        "pe_ttm", "dv_ttm", "pb", "ps_ttm", "close", "pct_chg", "industry_l1",
    ),
    "b1": (
        "ema5", "ema20", "ema_cross_3d", "ema20_up", "roc10",
        "close", "pct_chg", "volume", "vol_ma5", "ma60",
        "amount_ma20", "industry_l1",
    ),
    "b2": (
        "rsi6", "amp60", "ma120", "close", "open", "pre_close", "pct_chg",
        "amount_ma20", "industry_l1",
    ),
    "b3": (
        "kdj_k", "kdj_d", "kdj_j", "kdj_j_min2", "kdj_golden_cross",
        "ema60", "close", "pct_chg", "amount_ma20", "industry_l1",
    ),
    "b4": (
        "boll_width", "boll_width_pct60", "boll_upper", "boll_mid", "boll_lower",
        "high20_close", "range20_pct", "volume", "vol_ma5",
        "close", "pct_chg", "amount_ma20", "industry_l1",
    ),
    "b5": (
        "boll_lower", "kdj_j", "kdj_j_min2", "kdj_k", "kdj_d",
        "low", "volume", "vol_ma5", "ma120",
        "close", "open", "pre_close", "pct_chg", "amount_ma20", "industry_l1",
    ),
}


# =====================================================================
# 通用工具
# =====================================================================

def jsonable(value: Any) -> Any:
    """把 numpy / Decimal / NaN / date 等转成可 JSON 序列化的原生对象。"""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    if isinstance(value, Decimal):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return [jsonable(item) for item in value.tolist()]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def get_spec(algo_code: str) -> StrategySpec:
    """按代码取策略元信息；未知策略抛 UnknownAlgoError。"""
    key = str(algo_code or "").strip().lower()
    spec = SPECS.get(key)
    if spec is None:
        raise UnknownAlgoError(
            f"未知策略: {algo_code}",
            detail={"available": sorted(SPECS)},
        )
    return spec


def list_specs() -> list[StrategySpec]:
    """全部策略元信息（按 sort_order 排序）。"""
    return sorted(SPECS.values(), key=lambda item: item.sort_order)


def describe_specs() -> list[dict]:
    """转成契约 5.1 的算法清单（供 `GET /api/v1/algo/list` 直接用）。"""
    return [spec.as_algo_item() for spec in list_specs()]


def _check_range(
    key: str,
    value: float,
    minimum: Any,
    maximum: Any,
) -> None:
    """数值越界校验。"""
    if minimum is not None and value < float(minimum) - 1e-12:
        raise InvalidParamError(
            f"参数 {key} 不能小于 {minimum}，收到: {value}",
            detail={"param": key, "value": value, "min": minimum, "max": maximum},
        )
    if maximum is not None and value > float(maximum) + 1e-12:
        raise InvalidParamError(
            f"参数 {key} 不能大于 {maximum}，收到: {value}",
            detail={"param": key, "value": value, "min": minimum, "max": maximum},
        )


def _validate_params(algo_code: str, params: dict | None) -> dict:
    """参数白名单 + 类型转换 + 默认值补齐（**参数真生效的第一道关**）。

    - 未传的参数从 `SPECS[algo].defaults` 补齐；
    - 参数类型按 schema 归一（limit → int，其余 → float），保证缓存指纹稳定；
    - A3 的 `min_score` 是 0–10 分制：**兼容旧调用方传 0–100 分值**（如 60 → 6.0），
      归一后的值会回显在 run.params 里，避免"静默空结果"；
    - 未知参数 / 越界 / 非数值 → `InvalidParamError`。
    """
    spec = get_spec(algo_code)
    raw = dict(params or {})
    allowed = {item["key"] for item in spec.params_schema}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise InvalidParamError(
            f"策略 {spec.code} 不支持参数: {', '.join(unknown)}",
            detail={"algo": spec.code, "unknown": unknown, "allowed": sorted(allowed)},
        )

    normalized: dict[str, Any] = {}
    for item in spec.params_schema:
        key = item["key"]
        value = raw.get(key, spec.defaults.get(key, item.get("default")))
        if value is None:
            raise InvalidParamError(
                f"参数 {key} 不能为空", detail={"algo": spec.code, "param": key}
            )
        if item.get("type") == "integer":
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise InvalidParamError(
                    f"参数 {key} 必须是整数，收到: {value!r}",
                    detail={"algo": spec.code, "param": key, "value": str(value)},
                ) from None
            if isinstance(value, float) and not float(value).is_integer():
                raise InvalidParamError(
                    f"参数 {key} 必须是整数，收到: {value!r}",
                    detail={"algo": spec.code, "param": key, "value": str(value)},
                )
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise InvalidParamError(
                    f"参数 {key} 必须是数值，收到: {value!r}",
                    detail={"algo": spec.code, "param": key, "value": str(value)},
                ) from None
            if math.isnan(number) or math.isinf(number):
                raise InvalidParamError(
                    f"参数 {key} 必须是有限数值，收到: {value!r}",
                    detail={"algo": spec.code, "param": key, "value": str(value)},
                )
            # 旧调用方兼容：A3 最低综合评分传了 0–100 口径（如 60）→ 归一为 0–10（6.0）
            if spec.code == "a3" and key == "min_score" and number > 10.0:
                number = round(number / 10.0, 4)
        _check_range(key, float(number), item.get("min"), item.get("max"))
        normalized[key] = number

    return normalized


#: 公开别名（API 层可先校验再执行，避免重复实现）
validate_params = _validate_params


# =====================================================================
# 取数（一次 SQL 横截面，禁止 N+1）
# =====================================================================

#: 各表需要取回的列（显式声明，避免 SELECT * 带来的列名冲突/浪费）
_DIM_COLUMNS = (
    "code", "name", "exchange", "board",
    "industry_l1", "industry_l2", "ownership",
    "list_date", "delist_date", "is_st", "status",
)
_DAILY_COLUMNS = (
    "open", "high", "low", "close", "pre_close",
    "pct_chg", "volume", "amount", "turnover_rate", "adj_factor",
    "market_cap", "float_cap", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
    "is_limit_up", "is_limit_down", "is_suspended",
)
_FEATURE_COLUMNS = (
    "ma5", "ma10", "ma20", "ma60", "ma120", "rsi14", "rsi6", "roc10",
    "macd_dif", "macd_dea", "macd_hist", "macd_golden_cross",
    "ema5", "ema20", "ema60", "ema20_up", "ema_golden_cross", "ema_cross_3d",
    "kdj_k", "kdj_d", "kdj_j", "kdj_j_min2", "kdj_golden_cross",
    "boll_mid", "boll_upper", "boll_lower", "boll_width", "boll_width_pct60",
    "high20_close", "range20_pct", "amp60", "vol_ma5",
    "atr14", "momentum_5", "momentum_20", "momentum_60",
    "volatility_20", "volume_ratio", "amount_ma20", "ma_bullish",
)
_SCORE_COLUMNS = (
    "value_score", "quality_score", "momentum_score",
    "volatility_score", "liquidity_score", "sentiment_score", "composite_score",
)
_FLOW_COLUMNS = (
    "main_net_t0", "main_net_t1", "main_net_t2", "main_net_t3", "main_net_t4",
    "main_net_5d", "north_hold_ratio",
)

_NUMERIC_COLUMNS: tuple[str, ...] = (
    "market_cap", "float_cap", "turnover_rate", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
    "ma5", "ma10", "ma20", "ma60", "ma120", "rsi14", "rsi6", "roc10",
    "macd_dif", "macd_dea", "macd_hist",
    "ema5", "ema20", "ema60",
    "kdj_k", "kdj_d", "kdj_j", "kdj_j_min2",
    "boll_mid", "boll_upper", "boll_lower", "boll_width", "boll_width_pct60",
    "high20_close", "range20_pct", "amp60", "vol_ma5",
    "atr14",
    "momentum_5", "momentum_20", "momentum_60", "volatility_20",
    "volume_ratio", "amount_ma20",
    "value_score", "quality_score", "momentum_score",
    "volatility_score", "liquidity_score", "sentiment_score", "composite_score",
    "main_net_t0", "main_net_t1", "main_net_t2", "main_net_t3", "main_net_t4",
    "main_net_5d", "north_hold_ratio",
) + _DAILY_COLUMNS

_BOOL_COLUMNS: tuple[str, ...] = (
    "is_st", "is_suspended", "is_limit_up", "is_limit_down",
    "macd_golden_cross", "ma_bullish",
    "ema20_up", "ema_golden_cross", "ema_cross_3d", "kdj_golden_cross",
)


def _decimal_to_float(value: Any) -> Any:
    """asyncpg 的 Numeric 返回 Decimal，统一转 float 便于 pandas 计算。"""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _build_record(
    daily: FactStockDaily,
    dim: DimStock | None,
    feature: FactFeatureDaily | None,
    score: FactStockScoreDaily | None,
    flow: FactMoneyflow | None,
) -> dict:
    """把一行 ORM 对象拍平成 dict（Decimal → float）。"""
    record: dict[str, Any] = {"trade_date": daily.trade_date}
    for name in _DAILY_COLUMNS:
        record[name] = _decimal_to_float(getattr(daily, name, None))
    for name in _DIM_COLUMNS:
        record[name] = _decimal_to_float(getattr(dim, name, None)) if dim is not None else None
    for name in _FEATURE_COLUMNS:
        record[name] = _decimal_to_float(getattr(feature, name, None)) if feature is not None else None
    for name in _SCORE_COLUMNS:
        record[name] = _decimal_to_float(getattr(score, name, None)) if score is not None else None
    for name in _FLOW_COLUMNS:
        record[name] = _decimal_to_float(getattr(flow, name, None)) if flow is not None else None
    return record


async def _fetch_cross_section(db: AsyncSession, trade_date: date) -> pd.DataFrame:
    """当日全市场横截面：一条 SQL 取回五表 join 结果。

    基础可用性过滤直接下推到 SQL（ST/退市/停牌状态/退市日期），
    其余口径（次新股、当日无行情等）在 pandas 里做，便于纯函数测试。
    """
    daily = FactStockDaily
    statement = (
        select(daily, DimStock, FactFeatureDaily, FactStockScoreDaily, FactMoneyflow)
        .select_from(daily)
        .join(DimStock, DimStock.code == daily.code)
        .outerjoin(
            FactFeatureDaily,
            and_(
                FactFeatureDaily.code == daily.code,
                FactFeatureDaily.trade_date == daily.trade_date,
            ),
        )
        .outerjoin(
            FactStockScoreDaily,
            and_(
                FactStockScoreDaily.code == daily.code,
                FactStockScoreDaily.trade_date == daily.trade_date,
            ),
        )
        .outerjoin(
            FactMoneyflow,
            and_(
                FactMoneyflow.code == daily.code,
                FactMoneyflow.trade_date == daily.trade_date,
            ),
        )
        .where(daily.trade_date == trade_date)
        .where(DimStock.status == 1)
        .where(DimStock.is_st.is_(False))
        .where(or_(DimStock.delist_date.is_(None), DimStock.delist_date > trade_date))
    )

    try:
        result = await db.execute(statement)
        rows = result.all()
    except ProgrammingError as exc:  # 表/字段不存在：多半是数据库未初始化
        raise DataUnavailableError(
            "数据库表尚未初始化，无法读取选股横截面",
            next_action="执行数据库初始化（建表 + 分区 + 视图）后重试",
            detail={"error": str(exc)[:300]},
        ) from exc

    if not rows:
        return pd.DataFrame(columns=list(_DIM_COLUMNS) + list(_DAILY_COLUMNS)
                            + list(_FEATURE_COLUMNS) + list(_SCORE_COLUMNS)
                            + list(_FLOW_COLUMNS) + ["trade_date"])

    records = [_build_record(*row) for row in rows]
    frame = pd.DataFrame.from_records(records)
    _normalize_dtypes(frame)
    frame.attrs.update({"trade_date": trade_date, "source": "db"})

    # 便于排查的规模日志（不打印参数值，避免日志噪音）
    logger.info(
        "选股横截面取数完成 trade_date=%s rows=%d features=%d scores=%d moneyflow=%d",
        trade_date,
        len(frame),
        int(frame["macd_golden_cross"].notna().sum()) if "macd_golden_cross" in frame else 0,
        int(frame["composite_score"].notna().sum()) if "composite_score" in frame else 0,
        int(frame["main_net_5d"].notna().sum()) if "main_net_5d" in frame else 0,
    )
    return frame


async def load_market_context(
    db: AsyncSession,
    trade_date: date,
    index_code: str = BENCHMARK_INDEX_CODE,
) -> dict[str, Any]:
    """基准指数（默认沪深300）截至 T 日的状态，用于 B4/B5 的市场门槛。

    只使用 <= trade_date 的指数数据；指数数据缺失时返回 ``available=False``
    并放行（不因为基准缺失而让策略永远空结果）。
    """
    statement = (
        select(MarketIndex.trade_date, MarketIndex.close, MarketIndex.pct_chg)
        .where(MarketIndex.index_code == index_code)
        .where(MarketIndex.trade_date <= trade_date)
        .order_by(MarketIndex.trade_date.desc())
        .limit(30)
    )
    try:
        rows = (await db.execute(statement)).all()
    except ProgrammingError:
        logger.warning("market_indices 表不存在，市场状态门槛按放行处理")
        rows = []

    if not rows:
        logger.warning(
            "基准指数 %s 在 %s 前无数据，市场状态门槛按放行处理", index_code, trade_date
        )
        return {
            "available": False,
            "index_code": index_code,
            "b4_ok": True,
            "b5_ok": True,
        }

    ordered = list(reversed(rows))
    closes = [
        float(close) for _, close, _ in ordered
        if close is not None and float(close) > 0
    ]
    last_close = closes[-1] if closes else None
    ma20 = round(sum(closes[-20:]) / 20.0, 4) if len(closes) >= 20 else None
    raw_pct = ordered[-1][2]
    last_pct = float(raw_pct) if raw_pct is not None else None

    return {
        "available": True,
        "index_code": index_code,
        "close": round(last_close, 2) if last_close is not None else None,
        "ma20": ma20,
        "pct_chg": last_pct,
        "b4_ok": True if (last_close is None or ma20 is None) else last_close >= ma20 * 0.995,
        "b5_ok": True if last_pct is None else last_pct > -2.0,
    }


def _to_float_series(series: pd.Series) -> pd.Series:
    """列转 float64（Decimal/字符串/None → float/NaN），空列安全。

    asyncpg 读 `Numeric` 列返回 `Decimal`；`Decimal >= float` 会抛 TypeError，
    所以候选池/信号计算的每个数值列都必须先经过这里统一转 float。
    """
    if len(series) == 0:
        return pd.Series(dtype="float64", index=series.index)
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    converted = pd.to_numeric(series, errors="coerce")
    if converted.dtype == object:
        # pd.to_numeric 对 Decimal 对象可能不识别，退回逐值 float()
        converted = series.map(_to_float_quiet)
    return converted.astype("float64")


def coerce_numeric_frame(df: pd.DataFrame) -> pd.DataFrame:
    """把候选池 DataFrame 的数值列统一成 float64（Decimal 兜底，可重复调用）。

    方案 D2/D4 硬要求：所有金额/比率比较都在 float 上做，绝不直接比 Decimal。
    """
    if df is None or len(df) == 0:
        return df
    frame = df.copy()
    for column in _NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = _to_float_series(frame[column])
    for column in ("volume", "status"):
        if column in frame.columns:
            frame[column] = _to_float_series(frame[column])
    frame.attrs.update(dict(df.attrs))
    return frame


def _to_float_quiet(value: Any) -> float:
    """单值转 float，失败返回 NaN。"""
    if value is None or value is pd.NA:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _to_bool_quiet(value: Any) -> bool:
    """单值转 bool（None/NaN → False）。"""
    if value is None or value is pd.NA:
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating, Decimal)):
        try:
            return bool(float(value))
        except (TypeError, ValueError):
            return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "是"}


def _to_bool_series(series: pd.Series) -> pd.Series:
    """列转 bool（None/NaN → False），空列安全。"""
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    return series.map(_to_bool_quiet).astype(bool)


def _normalize_dtypes(frame: pd.DataFrame) -> None:
    """统一列类型：数值列 float64、布尔列 bool、日期列 datetime。"""
    for column in _NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = _to_float_series(frame[column])
    for column in ("volume", "status"):
        if column in frame.columns:
            frame[column] = _to_float_series(frame[column])
    for column in _BOOL_COLUMNS:
        if column in frame.columns:
            frame[column] = _to_bool_series(frame[column])
    for column in ("list_date", "delist_date"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.normalize()
    if "code" in frame.columns:
        frame["code"] = frame["code"].astype(str).str.zfill(6)


async def _load_open_dates(db: AsyncSession, trade_date: date) -> list[date] | None:
    """读取 trade_calendar 中 <= trade_date 的开市日（用于次新股过滤）。

    表不存在时返回 None（调用方退化到自然日近似并标记 calendar_source=fallback）。
    """
    statement = (
        select(TradeCalendar.cal_date)
        .where(TradeCalendar.is_open.is_(True))
        .where(TradeCalendar.cal_date <= trade_date)
        # 多取一年：确保覆盖"上市日在 HISTORY_START 附近"的次新股计数
        .where(TradeCalendar.cal_date >= HISTORY_START - timedelta(days=400))
        .order_by(TradeCalendar.cal_date)
    )
    try:
        rows = (await db.execute(statement)).scalars().all()
    except ProgrammingError:
        logger.warning("trade_calendar 不存在，次新股过滤退化为自然日近似")
        return None
    return list(rows) if rows else None


# =====================================================================
# 统一前置过滤（纯函数，可单测）
# =====================================================================

def listed_trading_days(
    list_dates: pd.Series,
    trade_date: date,
    open_dates: Sequence[date] | None = None,
) -> pd.Series:
    """计算每只标的截至 trade_date 的上市交易日数（含上市当日之后的天数）。

    - 有交易日历时：用 searchsorted 精确计数（O(n log m)，向量化）；
    - 无交易日历时：退化为自然日近似（84 自然日 ≈ 60 交易日）并标记 NaN 由调用方处理；
    - list_date 为空的标的不做次新股剔除（数据缺失不误杀）。
    """
    normalized_dates = pd.to_datetime(list_dates, errors="coerce")
    trade_ts = pd.Timestamp(trade_date).normalize()

    if open_dates:
        calendar = pd.DatetimeIndex(sorted({pd.Timestamp(item) for item in open_dates}))
        calendar = calendar[calendar <= trade_ts]
        if len(calendar) > 0:
            values = normalized_dates.to_numpy(dtype="datetime64[ns]")
            positions = np.searchsorted(calendar.to_numpy(dtype="datetime64[ns]"), values, side="right")
            days = pd.Series(len(calendar) - positions, index=normalized_dates.index, dtype="float64")
            days[normalized_dates.isna()] = float("nan")
            # 上市日早于日历最早一日：无法精确计数，但必然"早已上市"，不做次新股剔除
            # （否则回测起点贴近历史起点时会误杀全市场）
            too_old = normalized_dates.notna() & (normalized_dates < calendar[0])
            days[too_old] = 9999.0
            return days

    # 退化：自然日近似
    delta = (trade_ts - normalized_dates).dt.days.astype("float64")
    delta[normalized_dates.isna()] = float("nan")
    return delta / 7.0 * 5.0  # 自然日 → 约当交易日


def apply_common_filters(
    df: pd.DataFrame,
    trade_date: date,
    open_dates: Sequence[date] | None = None,
) -> pd.DataFrame:
    """所有策略统一的 A 股前置过滤（纯函数）。

    剔除：ST、status != 1、delist_date 已到、上市不足 60 个交易日、
    is_suspended、当日无行情（close 为空）。
    """
    if df is None or len(df) == 0:
        empty = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        if isinstance(df, pd.DataFrame):
            empty.attrs.update(dict(df.attrs))
        return empty

    trade_ts = pd.Timestamp(trade_date).normalize()
    mask = pd.Series(True, index=df.index)

    if "is_st" in df.columns:
        mask &= ~_to_bool_series(df["is_st"])
    if "status" in df.columns:
        mask &= _to_float_series(df["status"]).fillna(0.0) == 1.0
    if "delist_date" in df.columns:
        delist = pd.to_datetime(df["delist_date"], errors="coerce")
        mask &= delist.isna() | (delist.dt.normalize() > trade_ts)
    if "is_suspended" in df.columns:
        mask &= ~_to_bool_series(df["is_suspended"])
    if "close" in df.columns:
        mask &= _to_float_series(df["close"]) > 0
    if "list_date" in df.columns:
        days = listed_trading_days(df["list_date"], trade_date, open_dates)
        mask &= days.isna() | (days >= NEW_STOCK_MIN_TRADING_DAYS)

    filtered = df.loc[mask.fillna(False)].copy()
    filtered.attrs.update(dict(df.attrs))
    return filtered


# =====================================================================
# 策略条件（纯函数，与 build_candidates / rank_signals 共用同一口径）
# =====================================================================

def _numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    """取数值列；缺列时返回全 NaN，保证条件计算不炸。"""
    if column not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    return _to_float_series(df[column])


def _bool_column(df: pd.DataFrame, column: str) -> pd.Series:
    """取布尔列；缺列时返回全 False。"""
    if column not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return _to_bool_series(df[column])


def _market_flag(df: pd.DataFrame, key: str) -> bool:
    """读取市场状态门槛；无 attrs（单测/回测）或指数缺失时放行。"""
    market = df.attrs.get("market") or {}
    return bool(market.get(key, True))


def strategy_mask(algo_code: str, df: pd.DataFrame, params: dict) -> pd.Series:
    """返回策略条件命中的布尔 Series（口径见模块 docstring）。"""
    spec = get_spec(algo_code)
    code = spec.code
    params = _validate_params(code, params)

    if code == "a3":
        composite = _numeric_column(df, "composite_score")
        if "ownership" in df.columns:
            ownership = df["ownership"].astype("object")
            central = ownership.eq("央企").fillna(False)
        else:
            central = pd.Series(False, index=df.index, dtype=bool)
        mask = central & composite.notna() & (composite >= float(params["min_score"]))
    elif code == "a4":
        pe = _numeric_column(df, "pe_ttm")
        dividend = _numeric_column(df, "dv_ttm")
        mask = (
            (pe > 0)
            & (pe < float(params["pe_max"]))
            & dividend.notna()
            & (dividend >= float(params["div_min"]))
        )
    elif code == "b1":
        close = _numeric_column(df, "close")
        volume = _numeric_column(df, "volume")
        vol_ma5 = _numeric_column(df, "vol_ma5")
        ma60 = _numeric_column(df, "ma60")
        amount_ma20 = _numeric_column(df, "amount_ma20")
        mask = (
            _bool_column(df, "ema_cross_3d")
            & (close > _numeric_column(df, "ema20"))
            & _bool_column(df, "ema20_up")
            & (_numeric_column(df, "roc10") > 0)
            & vol_ma5.notna()
            & (volume >= vol_ma5 * float(params["min_vol_ratio"]))
            & (close > ma60)
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
    elif code == "b2":
        close = _numeric_column(df, "close")
        open_price = _numeric_column(df, "open")
        pre_close = _numeric_column(df, "pre_close")
        ma120 = _numeric_column(df, "ma120")
        amplitude = _numeric_column(df, "amp60")
        amount_ma20 = _numeric_column(df, "amount_ma20")
        mask = (
            (_numeric_column(df, "rsi6") < float(params["rsi_max"]))
            & ((close > open_price) | (close >= pre_close))
            & ma120.notna()
            & (close > ma120 * float(params["trend_dev"]))
            & amplitude.notna()
            & (amplitude > float(params["amp_min"]))
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
    elif code == "b3":
        close = _numeric_column(df, "close")
        pre_close = _numeric_column(df, "pre_close")
        ema60 = _numeric_column(df, "ema60")
        amount_ma20 = _numeric_column(df, "amount_ma20")
        mask = (
            (_numeric_column(df, "kdj_j_min2") < float(params["j_max"]))
            & _bool_column(df, "kdj_golden_cross")
            & (_numeric_column(df, "kdj_k") < float(params["k_max"]))
            & ema60.notna()
            & (close > ema60)
            & (close >= pre_close)
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
    elif code == "b4":
        close = _numeric_column(df, "close")
        volume = _numeric_column(df, "volume")
        vol_ma5 = _numeric_column(df, "vol_ma5")
        amount_ma20 = _numeric_column(df, "amount_ma20")
        band_pct = _numeric_column(df, "boll_width_pct60")
        mask = (
            band_pct.notna()
            & (band_pct <= float(params["squeeze_pct"]))
            & (close > _numeric_column(df, "boll_upper"))
            & (close > _numeric_column(df, "high20_close"))
            & vol_ma5.notna()
            & (volume >= vol_ma5 * float(params["vol_mult"]))
            & _numeric_column(df, "range20_pct").notna()
            & (_numeric_column(df, "range20_pct") < float(params["range_max"]))
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
        if not _market_flag(df, "b4_ok"):
            logger.info("B4 市场状态门槛未通过（沪深300 收盘低于 MA20 附近），本次不产生信号")
            mask &= False
    elif code == "b5":
        close = _numeric_column(df, "close")
        low = _numeric_column(df, "low")
        open_price = _numeric_column(df, "open")
        pre_close = _numeric_column(df, "pre_close")
        volume = _numeric_column(df, "volume")
        vol_ma5 = _numeric_column(df, "vol_ma5")
        lower = _numeric_column(df, "boll_lower")
        ma120 = _numeric_column(df, "ma120")
        amount_ma20 = _numeric_column(df, "amount_ma20")
        deviation = (lower - close) / lower.where(lower > 0) * 100.0
        mask = (
            lower.notna()
            & (low <= lower)
            & (_numeric_column(df, "kdj_j_min2") < float(params["j_max"]))
            & vol_ma5.notna()
            & (volume < vol_ma5)
            & ((close > open_price) | (close >= pre_close))
            & ((ma120.notna() & (close > ma120)) | (deviation <= float(params["dev_max"])))
            & amount_ma20.notna()
            & (amount_ma20 >= float(params["min_amount"]))
        )
        if not _market_flag(df, "b5_ok"):
            logger.info("B5 市场状态门槛未通过（沪深300 当日跌幅超过 2%），本次不产生信号")
            mask &= False
    else:  # pragma: no cover - get_spec 已保证策略存在
        raise UnknownAlgoError(f"未知策略: {algo_code}")

    return mask.fillna(False).astype(bool)


def filter_by_strategy(algo_code: str, df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """按策略条件过滤候选池（纯函数）。"""
    if df is None or len(df) == 0:
        empty = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        if isinstance(df, pd.DataFrame):
            empty.attrs.update(dict(df.attrs))
        return empty
    mask = strategy_mask(algo_code, df, params)
    filtered = df.loc[mask].copy()
    filtered.attrs.update(dict(df.attrs))
    return filtered


#: B 系列各自用于判断"指标是否已回填"的代表列
_B_READY_COLUMN: dict[str, str] = {
    "b1": "ema20",
    "b2": "rsi6",
    "b3": "kdj_j",
    "b4": "boll_width_pct60",
    "b5": "kdj_j",
}

# 短线指标在候选池中的最低覆盖率。只判断"是否全空"会让回填到一半时
# 用几百只股票跑出信号并写入 stg_run 缓存（特征表行数不变，data_version
# 也不会失效），必须按覆盖率拦截。
_MIN_FEATURE_COVERAGE = 0.5


def _assert_data_ready(algo_code: str, df: pd.DataFrame) -> None:
    """策略所需数据集为空时给出明确原因（E1：不要静默返回空结果）。"""
    spec = get_spec(algo_code)
    if df is None or len(df) == 0:
        raise DataUnavailableError(
            f"{str(df.attrs.get('trade_date') or '')} 候选池为空："
            "行情/上市天数/停牌过滤后没有可用标的",
            next_action="POST /api/v1/admin/ingest/history_fast?start=2023-01-01&background=true",
            detail={"dataset": "fact_stock_daily"},
        )

    def _all_null(column: str) -> bool:
        return column not in df.columns or _to_float_series(df[column]).notna().sum() == 0

    def _coverage(column: str) -> float:
        if column not in df.columns or len(df) == 0:
            return 0.0
        return float(_to_float_series(df[column]).notna().mean())

    if spec.code in _B_READY_COLUMN:
        ready_column = _B_READY_COLUMN[spec.code]
        for column in (ready_column, "amount_ma20"):
            coverage = _coverage(column)
            if coverage < _MIN_FEATURE_COVERAGE:
                raise DataUnavailableError(
                    f"{str(df.attrs.get('trade_date') or '')} 短线技术指标覆盖不足"
                    f"（{column} {coverage:.0%}，需 ≥{_MIN_FEATURE_COVERAGE:.0%}），"
                    "可能仍在回填",
                    next_action="执行短线特征回填（scripts/backfill_short_features.py）后重试",
                    detail={
                        "dataset": "fact_feature_daily",
                        "column": column,
                        "coverage": round(coverage, 4),
                    },
                )
        return

    if spec.code == "a3" and _all_null("composite_score"):
        raise DataUnavailableError(
            "个股因子评分尚未生成，无法执行综合评分策略",
            next_action="POST /api/v1/admin/ingest/scores",
            detail={"dataset": "fact_stock_score_daily"},
        )
    if spec.code == "a3":
        ownership = df["ownership"].fillna("") if "ownership" in df.columns else pd.Series(dtype=str)
        if not (ownership == "央企").any():
            raise DataUnavailableError(
                "股票池缺少央企分类，无法执行央企综合评分策略",
                next_action="POST /api/v1/admin/ingest/universe",
                detail={"dataset": "dim_stock.ownership"},
            )
    if spec.code == "a4":
        if _all_null("dv_ttm"):
            raise DataUnavailableError(
                "股息率字段为空，无法执行高股息策略",
                next_action="POST /api/v1/admin/ingest/history_fast?background=true",
                detail={"dataset": "fact_stock_daily.dv_ttm"},
            )
        if _all_null("pe_ttm"):
            raise DataUnavailableError(
                "PE 估值字段为空，无法执行高股息策略",
                next_action="POST /api/v1/admin/ingest/valuation_fast?background=true",
                detail={"dataset": "fact_stock_daily.pe_ttm"},
            )


# =====================================================================
# 排序 / 打分 / metrics
# =====================================================================

def _sort_candidates(algo_code: str, df: pd.DataFrame) -> pd.DataFrame:
    """按策略排序口径排序（同值时按 code 升序，保证结果确定性）。"""
    spec = get_spec(algo_code)
    field_name, ascending = _SORT_SPEC[spec.code]
    frame = df.copy()
    frame["_sort_value"] = _numeric_column(frame, field_name)
    frame = frame[frame["_sort_value"].notna()]
    if "code" in frame.columns:
        frame = frame.sort_values(
            ["_sort_value", "code"],
            ascending=[ascending, True],
            kind="mergesort",
        )
    else:
        frame = frame.sort_values("_sort_value", ascending=ascending, kind="mergesort")
    return frame.drop(columns=["_sort_value"])


def _score_series(algo_code: str, df: pd.DataFrame) -> pd.Series:
    """`score` = 本策略排序指标的原始值（A3=综合评分，A4=股息率，B*=各自排序指标）。"""
    spec = get_spec(algo_code).code
    field_name, _ = _SORT_SPEC[spec]
    return _numeric_column(df, field_name).astype("float64").round(4)


def _metrics_for_row(algo_code: str, record: dict) -> dict:
    """构造单条信号的 metrics（该策略依据的全部指标 + 前端展示字段）。"""
    spec = get_spec(algo_code)
    metrics: dict[str, Any] = {}
    for field_name in _METRIC_FIELDS[spec.code]:
        metrics[field_name] = jsonable(record.get(field_name))
    # 通用展示字段（契约 5.2 data[] 顶层列由 metrics 派生，保证缓存命中前后结构一致）
    for field_name in DISPLAY_FIELDS:
        if field_name in metrics:
            continue
        if field_name in record:
            metrics[field_name] = jsonable(record.get(field_name))
    return metrics


def _empty_signal_frame() -> pd.DataFrame:
    """空信号表（列齐全，便于 API 直接序列化成空数组）。"""
    return pd.DataFrame(
        columns=["code", "name", "rank", "score", "metrics", *DISPLAY_FIELDS]
    )


def rank_signals(algo_code: str, df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """按策略口径过滤 + 排序 + 截断，生成 rank/score/metrics。

    返回列：``code, name, rank, score, metrics`` + 契约 5.2 的通用展示列。
    **纯函数**：不访问数据库，便于单测与回测复用。
    """
    spec = get_spec(algo_code)
    normalized = _validate_params(spec.code, params)

    if df is None or len(df) == 0:
        return _empty_signal_frame()

    # Decimal（asyncpg Numeric）→ float64，禁止 Decimal 与 float 直接比较
    working = coerce_numeric_frame(df)
    filtered = filter_by_strategy(spec.code, working, normalized)
    if len(filtered) == 0:
        return _empty_signal_frame()

    ordered = _sort_candidates(spec.code, filtered)
    ordered = ordered.copy()
    ordered["score"] = _score_series(spec.code, ordered).reindex(ordered.index)

    limit = int(normalized["limit"])
    top = ordered.head(limit).copy()
    top["rank"] = list(range(1, len(top) + 1))
    top["metrics"] = [_metrics_for_row(spec.code, row) for row in top.to_dict("records")]
    top["code"] = top["code"].astype(str).str.zfill(6)
    if "name" not in top.columns:
        top["name"] = None

    columns = ["code", "name", "rank", "score", "metrics"]
    columns += [item for item in DISPLAY_FIELDS if item in top.columns]
    # 排序依据字段也带出来（便于测试/排查，前端不依赖它）
    sort_field, _ = _SORT_SPEC[spec.code]
    if sort_field in top.columns and sort_field not in columns:
        columns.append(sort_field)
    return top.loc[:, columns].reset_index(drop=True)


# =====================================================================
# 候选池构建（唯一一次 DB 取数）
# =====================================================================

async def build_candidates(
    algo_code: str,
    trade_date: date,
    params: dict | None = None,
    db: AsyncSession | None = None,
) -> pd.DataFrame:
    """构建某策略某日的候选池（**一次 SQL 横截面**，无 N+1 查询）。

    返回 DataFrame 的 attrs 中带：
      - ``universe_count``  当日全市场有行情的标的数（契约 5.2 的 run.universe_count）
      - ``eligible_count``  通过统一前置过滤后的标的数
      - ``calendar_source`` "db" / "fallback"
      - ``trade_date``      回测/详情页需要的日期
      - ``market``          B4/B5 的沪深300 市场状态（其他策略不取）
    """
    spec = get_spec(algo_code)
    normalized = _validate_params(spec.code, params)
    if db is None:
        raise InvalidParamError("build_candidates 需要传入 AsyncSession")
    if not isinstance(trade_date, date):
        raise InvalidParamError(f"trade_date 必须是 date，收到: {type(trade_date).__name__}")

    frame = await _fetch_cross_section(db, trade_date)
    if len(frame) == 0:
        raise DataUnavailableError(
            f"{trade_date.isoformat()} 无个股行情数据",
            next_action="POST /api/v1/admin/ingest/daily_snapshot",
            detail={"trade_date": trade_date.isoformat()},
        )

    universe_count = int(len(frame))
    open_dates = await _load_open_dates(db, trade_date)
    eligible = apply_common_filters(frame, trade_date, open_dates)
    _assert_data_ready(spec.code, eligible)

    market = None
    if spec.code in {"b4", "b5"}:
        market = await load_market_context(db, trade_date)

    # 最终出口再统一 coerce 一次：保证调用方拿到的数值列一定是 float64
    pool = coerce_numeric_frame(filter_by_strategy(spec.code, eligible, normalized))
    pool.attrs.update(
        {
            "trade_date": trade_date,
            "universe_count": universe_count,
            "eligible_count": int(len(eligible)),
            "calendar_source": "db" if open_dates else "fallback",
            "algo": spec.code,
            "params": dict(normalized),
        }
    )
    if market is not None:
        pool.attrs["market"] = market
    return pool


__all__ = [
    "SPECS",
    "StrategySpec",
    "DISPLAY_FIELDS",
    "BENCHMARK_INDEX_CODE",
    "NEW_STOCK_MIN_TRADING_DAYS",
    "get_spec",
    "list_specs",
    "describe_specs",
    "validate_params",
    "jsonable",
    "coerce_numeric_frame",
    "strategy_mask",
    "filter_by_strategy",
    "listed_trading_days",
    "apply_common_filters",
    "load_market_context",
    "rank_signals",
    "build_candidates",
]
