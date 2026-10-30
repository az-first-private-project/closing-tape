"""
条件树引擎（方案 D1）
=====================
纯本地 DataFrame 计算，**不拼接任何 SQL 字符串**，因此没有 SQL 注入面：
调用方先一次性把横截面数据取到内存，再在这里做 AND/OR 组合筛选。

设计要点：
  - `Condition` 是叶子（field / op / value），`ConditionGroup` 是枝干（AND/OR + children）；
  - 支持数值比较、区间、集合、排名分位（top_pct / bottom_pct）与行业约束；
  - 字段不存在或操作符非法一律抛 `InvalidParamError`（E1：错误要能说明原因）；
  - `describe()` 生成人类可读条件，写进 run 快照便于复盘。

行业约束的两种写法（等价）：
  1. 普通条件： ``{"field": "industry_l1", "op": "in", "value": ["电子", "计算机"]}``
  2. 简写糖：  ``{"logic": "AND", "children": [...], "industry": ["电子"],
                 "exclude_industry": ["银行"]}``
     其中 `industry` → industry_l1 in，`exclude_industry` → industry_l1 not_in。

排名分位的口径（D1 要求"排名分位"）：
  - ``top_pct: 20``     → 该字段升序排名百分位 > 0.8（即最高的 20%）
  - ``bottom_pct: 20``  → 排名百分位 <= 0.2（即最低的 20%）
  分位按当前 df 的横截面计算，空值不参与排名也不入选。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

import numpy as np
import pandas as pd

from app.core.errors import InvalidParamError

#: 支持的操作符集合（方案 D1 定稿）
OP = frozenset(
    {
        "gt", "ge", "lt", "le",
        "eq", "ne",
        "between",
        "in", "not_in",
        "top_pct", "bottom_pct",
    }
)

#: 比较类操作符 → 展示符号
_OP_SYMBOL = {
    "gt": ">",
    "ge": ">=",
    "lt": "<",
    "le": "<=",
    "eq": "=",
    "ne": "!=",
}

#: 字段别名（前端/配置可能用简称）
_FIELD_ALIASES: dict[str, str] = {
    "industry": "industry_l1",
    "industry1": "industry_l1",
    "industry_1": "industry_l1",
    "industry_l1": "industry_l1",
    "industry2": "industry_l2",
    "industry_2": "industry_l2",
}

#: 简写字段：表示"排除行业"
_EXCLUDE_INDUSTRY = "exclude_industry"


@dataclass(frozen=True)
class Condition:
    """单条件叶子节点： ``field op value``。

    - ``between`` 的 value 形如 ``[low, high]``（闭区间）；
    - ``in`` / ``not_in`` 的 value 为列表；
    - ``top_pct`` / ``bottom_pct`` 的 value 为百分数 (0, 100]；
    - 其余比较操作符 value 为标量。
    """

    field: str
    op: str
    value: Any = None


@dataclass
class ConditionGroup:
    """条件组：逻辑关系 + 子节点（子节点可以是 Condition 或 ConditionGroup）。"""

    logic: str = "AND"
    children: list["Condition | ConditionGroup"] = field(default_factory=list)


# =====================================================================
# 内部工具
# =====================================================================

def _fail(message: str, **detail: Any) -> None:
    """抛出统一的参数错误（集中构造，避免信息格式散落）。"""
    raise InvalidParamError(message, detail=detail or None)


def _normalize_op(op: Any) -> str:
    """校验操作符；非法抛 InvalidParamError。"""
    text = str(op or "").strip().lower()
    if text not in OP:
        _fail(
            f"非法操作符: {op!r}",
            op=op,
            allowed=sorted(OP),
        )
    return text


def _normalize_logic(logic: Any) -> str:
    """校验条件组逻辑（AND / OR）。"""
    text = str(logic or "AND").strip().upper()
    if text not in {"AND", "OR"}:
        _fail(f"条件组 logic 必须是 AND 或 OR，收到: {logic!r}", logic=logic)
    return text


def _as_list(value: Any, *, op: str) -> list[Any]:
    """把 value 规整为非空列表（in/not_in/between 用）。"""
    if isinstance(value, (list, tuple, set, frozenset, pd.Series, np.ndarray)):
        values = list(value)
    else:
        _fail(f"操作符 {op} 的 value 必须是列表，收到: {type(value).__name__}", op=op, value=value)
    if not values:
        _fail(f"操作符 {op} 的 value 不能为空列表", op=op)
    return values


def _as_scalar(value: Any, *, op: str) -> Any:
    """校验比较类操作符的标量取值。"""
    if value is None:
        _fail(f"操作符 {op} 的 value 不能为 None", op=op)
    return value


def _as_float(value: Any, *, op: str) -> float:
    """把标量转成 float；转换失败抛 InvalidParamError。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        _fail(f"操作符 {op} 需要数值，收到: {value!r}", op=op, value=value)
        raise  # pragma: no cover - _fail 一定抛异常


def _to_numeric(series: pd.Series, *, field_name: str) -> pd.Series:
    """把一列转成 float64（无法转换的值变 NaN）。

    Decimal（asyncpg 的 Numeric 返回值）显式转 float，避免 pandas 推断异常。
    """
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    converted = series.map(_to_float_quiet)
    return converted.astype("float64")


def _to_float_quiet(value: Any) -> float:
    """单值转 float，失败返回 NaN（供 map 使用）。"""
    if value is None or value is pd.NA:
        return float("nan")
    if isinstance(value, (bool, np.bool_)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _to_bool(series: pd.Series) -> pd.Series:
    """把一列转成 bool（None/NaN → False）。"""
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    return series.map(_to_bool_quiet).astype(bool)


def _to_bool_quiet(value: Any) -> bool:
    """单值转 bool（供 map 使用）。"""
    if value is None or value is pd.NA:
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating, Decimal)):
        try:
            return bool(float(value))
        except (TypeError, ValueError):
            return False
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "是"}


def _get_series(df: pd.DataFrame, condition: Condition) -> pd.Series:
    """取出条件对应的列；未知字段抛 InvalidParamError。"""
    field_name = _FIELD_ALIASES.get(str(condition.field).strip(), str(condition.field).strip())
    if field_name not in df.columns:
        _fail(
            f"未知字段: {field_name}",
            field=field_name,
            available=[str(c) for c in list(df.columns)[:80]],
        )
    return df[field_name]


# =====================================================================
# 求值
# =====================================================================

def evaluate(df: pd.DataFrame, node: "Condition | ConditionGroup") -> pd.Series:
    """对 DataFrame 求值，返回与 df 等长的布尔 Series。"""
    if not isinstance(df, pd.DataFrame):
        _fail(f"evaluate 需要 pandas.DataFrame，收到: {type(df).__name__}")

    if isinstance(node, ConditionGroup):
        return _eval_group(df, node)
    if isinstance(node, Condition):
        return _eval_condition(df, node)
    _fail(f"未支持的条件节点类型: {type(node).__name__}")


def _eval_group(df: pd.DataFrame, group: ConditionGroup) -> pd.Series:
    """条件组求值：AND / OR。"""
    logic = _normalize_logic(group.logic)
    children = list(group.children or [])
    if not children:
        _fail("条件组 children 不能为空", logic=logic)

    result = evaluate(df, children[0])
    for child in children[1:]:
        child_mask = evaluate(df, child)
        result = (result & child_mask) if logic == "AND" else (result | child_mask)
    return result.fillna(False).astype(bool)


def _eval_condition(df: pd.DataFrame, cond: Condition) -> pd.Series:
    """叶子条件求值。"""
    op = _normalize_op(cond.op)
    field_name = _FIELD_ALIASES.get(str(cond.field).strip(), str(cond.field).strip())

    # ---- 行业排除糖：exclude_industry in [..] 等价于 industry_l1 not_in [..] ----
    if field_name == _EXCLUDE_INDUSTRY:
        if op not in {"in", "not_in"}:
            _fail(f"exclude_industry 仅支持 in/not_in，收到: {op}", op=op)
        industry = _FIELD_ALIASES["industry_l1"]
        if industry not in df.columns:
            _fail(f"未知字段: {industry}", field=industry)
        values = _as_list(cond.value, op=op)
        return (~df[industry].isin(values)).fillna(False).astype(bool)

    series = _get_series(df, cond)

    if op == "between":
        bounds = _as_list(cond.value, op=op)
        if len(bounds) != 2:
            _fail("between 的 value 必须是 [low, high] 两个元素", value=cond.value)
        low, high = _as_float(bounds[0], op=op), _as_float(bounds[1], op=op)
        numeric = _to_numeric(series, field_name=field_name)
        mask = numeric.between(low, high, inclusive="both")
        return mask.fillna(False).astype(bool)

    if op in {"gt", "ge", "lt", "le"}:
        target = _as_float(_as_scalar(cond.value, op=op), op=op)
        numeric = _to_numeric(series, field_name=field_name)
        if op == "gt":
            mask = numeric > target
        elif op == "ge":
            mask = numeric >= target
        elif op == "lt":
            mask = numeric < target
        else:
            mask = numeric <= target
        return mask.fillna(False).astype(bool)

    if op in {"in", "not_in"}:
        values = _as_list(cond.value, op=op)
        mask = series.isin(values)
        if op == "not_in":
            mask = (~mask) & series.notna()
        return mask.fillna(False).astype(bool)

    if op in {"eq", "ne"}:
        value = cond.value
        if value is None:
            mask = series.isna()
        elif isinstance(value, (bool, np.bool_)):
            mask = _to_bool(series) == bool(value)
        elif pd.api.types.is_numeric_dtype(series):
            numeric = _to_numeric(series, field_name=field_name)
            mask = numeric.eq(_as_float(value, op=op))
        else:
            mask = series.astype("object").eq(value)
        mask = mask.fillna(False).astype(bool)
        if op == "ne" and value is not None:
            # 空值不参与 "!="：语义上"没有数据"不等于"满足不等于"
            mask = mask & series.notna()
        return mask

    # top_pct / bottom_pct：按当前横截面排名百分位切
    pct = _as_float(_as_scalar(cond.value, op=op), op=op)
    if not (0.0 < pct <= 100.0):
        _fail(f"{op} 的百分数必须在 (0, 100] 区间，收到: {pct}", op=op, value=pct)
    numeric = _to_numeric(series, field_name=field_name)
    rank_pct = numeric.rank(pct=True, method="average")
    if op == "top_pct":
        mask = rank_pct > (1.0 - pct / 100.0)
    else:
        mask = rank_pct <= (pct / 100.0)
    return mask.fillna(False).astype(bool)


def apply(df: pd.DataFrame, node: "Condition | ConditionGroup") -> pd.DataFrame:
    """按条件树过滤 DataFrame（保留原列与 attrs）。"""
    mask = evaluate(df, node)
    filtered = df.loc[mask].copy()
    filtered.attrs.update(dict(df.attrs))
    return filtered


# =====================================================================
# 人类可读描述
# =====================================================================

def _format_value(value: Any) -> str:
    """把条件取值格式化成描述文本。"""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple, set, frozenset)):
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    return str(value)


def describe(node: "Condition | ConditionGroup") -> str:
    """生成人类可读的条件描述（写入 run 快照 / 前端展示）。"""
    if isinstance(node, ConditionGroup):
        logic = _normalize_logic(node.logic)
        children = list(node.children or [])
        if not children:
            return "（空条件组）"
        parts = [describe(child) for child in children]
        joiner = " 且 " if logic == "AND" else " 或 "
        text = joiner.join(parts)
        return f"({text})" if len(parts) > 1 else text

    if isinstance(node, Condition):
        field_name = _FIELD_ALIASES.get(str(node.field).strip(), str(node.field).strip())
        op = str(node.op or "").strip().lower()
        if field_name == _EXCLUDE_INDUSTRY:
            return f"行业排除 {_format_value(node.value)}"
        if op == "between":
            return f"{field_name} ∈ {_format_value(node.value)}"
        if op == "top_pct":
            return f"{field_name} 排名前 {_format_value(node.value)}%"
        if op == "bottom_pct":
            return f"{field_name} 排名后 {_format_value(node.value)}%"
        if op in {"in", "not_in"}:
            word = "属于" if op == "in" else "不属于"
            return f"{field_name} {word} {_format_value(node.value)}"
        return f"{field_name} {_OP_SYMBOL.get(op, op)} {_format_value(node.value)}"

    _fail(f"未支持的条件节点类型: {type(node).__name__}")


# =====================================================================
# 解析
# =====================================================================

def from_dict(payload: Any) -> "Condition | ConditionGroup":
    """把 dict / list 解析成条件树；结构非法抛 InvalidParamError。

    支持的 payload 形态：
      - 叶子：``{"field": "pe_ttm", "op": "lt", "value": 30}``
      - 条件组：``{"logic": "AND", "children": [叶子, 条件组, ...]}``
      - 列表：``[叶子, ...]``（等价 AND）
      - 行业简写（可与 children 同时出现）：
        ``{"logic": "AND", "children": [...], "industry": ["电子"],
           "exclude_industry": ["银行"]}``
    """
    if isinstance(payload, (Condition, ConditionGroup)):
        return payload

    if isinstance(payload, (list, tuple)):
        if not payload:
            _fail("条件列表不能为空")
        return ConditionGroup(logic="AND", children=[from_dict(item) for item in payload])

    if not isinstance(payload, dict):
        _fail(f"无法解析的条件节点: {type(payload).__name__}", payload=str(payload)[:200])

    # 叶子条件
    if "field" in payload:
        field_name = str(payload.get("field") or "").strip()
        if not field_name:
            _fail("条件 field 不能为空", payload=payload)
        op = _normalize_op(payload.get("op"))
        value = payload.get("value")
        if field_name == _EXCLUDE_INDUSTRY:
            if op not in {"in", "not_in"}:
                _fail(f"{_EXCLUDE_INDUSTRY} 仅支持 in/not_in，收到: {op}", op=op)
            _as_list(value, op=op)
        return Condition(field=field_name, op=op, value=value)

    # 条件组
    logic = payload.get("logic", payload.get("operator", "AND"))
    raw_children = payload.get("children", payload.get("conditions"))
    children: list["Condition | ConditionGroup"] = []
    if raw_children is not None:
        if not isinstance(raw_children, (list, tuple)):
            _fail("条件组的 children 必须是列表", children_type=type(raw_children).__name__)
        children = [from_dict(child) for child in raw_children]

    # 行业简写糖
    include_industries = payload.get("industry")
    if include_industries is not None:
        children.append(
            Condition(
                field="industry_l1",
                op="in",
                value=_as_list(include_industries, op="in"),
            )
        )
    exclude_industries = payload.get(_EXCLUDE_INDUSTRY)
    if exclude_industries is not None:
        children.append(
            Condition(
                field=_EXCLUDE_INDUSTRY,
                op="in",
                value=_as_list(exclude_industries, op="in"),
            )
        )

    if not children:
        _fail("无法识别的条件节点（既没有 field 也没有 children/industry）", payload=str(payload)[:200])
    return ConditionGroup(logic=_normalize_logic(logic), children=children)


def to_dict(node: "Condition | ConditionGroup") -> dict[str, Any]:
    """条件树 → 可 JSON 序列化的 dict（与 from_dict 互逆，便于存快照）。"""
    if isinstance(node, Condition):
        return {"field": node.field, "op": node.op, "value": node.value}
    if isinstance(node, ConditionGroup):
        return {
            "logic": _normalize_logic(node.logic),
            "children": [to_dict(child) for child in (node.children or [])],
        }
    _fail(f"未支持的条件节点类型: {type(node).__name__}")


def flatten_fields(node: "Condition | ConditionGroup") -> list[str]:
    """列出条件树引用到的字段（去重、保序；用于预取列自检）。"""
    seen: list[str] = []

    def _walk(item: "Condition | ConditionGroup") -> None:
        if isinstance(item, Condition):
            name = _FIELD_ALIASES.get(str(item.field).strip(), str(item.field).strip())
            if name == _EXCLUDE_INDUSTRY:
                name = _FIELD_ALIASES["industry_l1"]
            if name not in seen:
                seen.append(name)
            return
        if isinstance(item, ConditionGroup):
            for child in item.children or []:
                _walk(child)
            return
        _fail(f"未支持的条件节点类型: {type(item).__name__}")

    _walk(node)
    return seen


__all__ = [
    "OP",
    "Condition",
    "ConditionGroup",
    "evaluate",
    "apply",
    "describe",
    "from_dict",
    "to_dict",
    "flatten_fields",
]
