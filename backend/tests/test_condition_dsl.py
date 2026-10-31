"""T7 条件 DSL 扩展与逐条解释单测。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from app.core.errors import InvalidParamError
from app.strategy.condition import (
    Condition,
    ConditionGroup,
    FieldRef,
    describe,
    evaluate,
    explain,
    flatten_fields,
    from_dict,
    to_dict,
    triggered_row,
)
from app.strategy.strategies import CONDITION_SPECS, _evaluate_strategy, rank_signals


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["600000", "600001", "600002"],
            "name": ["甲", "乙", "丙"],
            "close": [10.0, 9.0, 11.0],
            "open": [9.5, 9.6, 10.0],
            "pre_close": [9.8, 9.2, 10.5],
            "ema20": [9.0, 10.0, 10.0],
            "ma60": [9.5, 9.5, 10.5],
            "volume": [160.0, 140.0, 90.0],
            "vol_ma5": [100.0, 100.0, 100.0],
            "amount_ma20": [3.0e8, None, 2.0e8],
            "roc10": [5.0, -1.0, 2.0],
            "ema_cross_3d": [True, False, True],
            "ema20_up": [True, True, False],
        }
    )


def test_field_ref_comparisons_and_multiplier() -> None:
    frame = _frame()
    above_ema = Condition("close", "gt", FieldRef("ema20"))
    assert list(evaluate(frame, above_ema)) == [True, False, True]

    volume_expansion = Condition("volume", "ge", FieldRef("vol_ma5", 1.5))
    assert list(evaluate(frame, volume_expansion)) == [True, False, False]

    below_ma = Condition("close", "le", FieldRef("ma60"))
    assert list(evaluate(frame, below_ma)) == [False, True, False]


def test_field_ref_missing_column_raises() -> None:
    frame = _frame()
    node = Condition("close", "gt", FieldRef("not_exists"))
    with pytest.raises(InvalidParamError):
        evaluate(frame, node)


def test_notna_isna_and_describe() -> None:
    frame = _frame()
    assert list(evaluate(frame, Condition("amount_ma20", "notna"))) == [True, False, True]
    assert list(evaluate(frame, Condition("amount_ma20", "isna"))) == [False, True, False]
    assert describe(Condition("amount_ma20", "notna")) == "amount_ma20 非空"
    fields = flatten_fields(
        Condition("volume", "ge", FieldRef("vol_ma5", 1.5))
    )
    assert fields == ["volume", "vol_ma5"]


def test_field_ref_roundtrip_with_metadata() -> None:
    node = Condition(
        "close",
        "gt",
        FieldRef("ema20", 1.2),
        key="close_above_ema20",
        label="收盘站上 EMA20",
        detail_fields=("close", "ema20"),
    )
    payload = to_dict(node)
    assert payload["value"] == {"$field": "ema20", "multiplier": 1.2}
    restored = from_dict(payload)
    assert isinstance(restored, Condition)
    assert isinstance(restored.value, FieldRef)
    assert restored.value.multiplier == pytest.approx(1.2)
    assert restored.key == "close_above_ema20"


def test_explain_and_triggered_row_json_safe() -> None:
    frame = _frame()
    group = ConditionGroup(
        children=[
            Condition(
                "volume", "ge", FieldRef("vol_ma5", 1.5),
                key="volume_expansion", label="放量",
                detail_fields=("volume", "vol_ma5"),
            ),
            ConditionGroup(
                logic="OR", key="bullish_bar", label="收阳或收盘不低于昨收",
                detail_fields=("open", "close", "pre_close"),
                children=[
                    Condition("close", "gt", FieldRef("open")),
                    Condition("close", "ge", FieldRef("pre_close")),
                ],
            ),
        ]
    )
    details = explain(frame, group)
    assert [item.key for item in details] == ["volume_expansion", "bullish_bar"]

    triggered = triggered_row(details, frame, 0)
    assert triggered[0]["actual"] == pytest.approx(160.0)
    assert triggered[0]["op"] == "ge"
    assert triggered[0]["threshold"] == pytest.approx(150.0)
    assert triggered[0]["detail"] == "volume=160 vol_ma5=100"
    assert triggered[1]["actual"] is True
    assert triggered[1]["detail"] == "open=9.5 close=10 pre_close=9.8"
    json.dumps(triggered, ensure_ascii=False, allow_nan=False)


def _b1_frame() -> pd.DataFrame:
    frame = _frame()
    frame["amount_ma20"] = [3.0e8, 2.0e8, None]
    frame["vol_ma5"] = [100.0, 100.0, 100.0]
    frame["volume"] = [160.0, 90.0, 100.0]
    frame["ema20_up"] = [True, True, True]
    frame["ema_cross_3d"] = [True, False, True]
    frame["roc10"] = [5.0, -1.0, 2.0]
    frame["close"] = [11.0, 10.0, 10.0]
    frame["ema20"] = [10.0, 10.0, 10.0]
    frame["ma60"] = [10.0, 10.0, 10.0]
    return frame


def test_b1_triggered_matches_condition_specs_count() -> None:
    frame = _b1_frame()
    result = rank_signals("b1", frame, {"limit": 20})
    assert len(result) == 1
    triggered = result["metrics"].iloc[0]["triggered"]
    assert len(triggered) == len(CONDITION_SPECS["b1"].children) == 9
    assert all(item["scope"] == "stock" for item in triggered)
    assert all(item["passed"] for item in triggered)
    assert all(item["label"] and item["detail"] for item in triggered)
    json.dumps(result["metrics"].iloc[0], ensure_ascii=False, allow_nan=False)


def test_b4_market_condition_is_separate_scope() -> None:
    frame = pd.DataFrame(
        {
            "code": ["600000"],
            "name": ["甲"],
            "close": [11.0],
            "open": [10.0],
            "pre_close": [10.0],
            "boll_width_pct60": [10.0],
            "boll_upper": [10.8],
            "high20_close": [10.6],
            "volume": [1600.0],
            "vol_ma5": [1000.0],
            "range20_pct": [8.0],
            "amount_ma20": [3.0e8],
            "roc10": [5.0],
        }
    )
    frame.attrs["market"] = {"available": True, "b4_ok": True, "close": 4000.0, "ma20": 3900.0}
    evaluation = _evaluate_strategy("b4", frame, {})
    assert bool(evaluation.mask.iloc[0]) is True
    market_items = [item for item in evaluation.details if item.scope == "market"]
    assert len(market_items) == 1
    assert market_items[0].key == "b4_market"

    blocked = frame.copy()
    blocked.attrs["market"] = {"available": True, "b4_ok": False}
    assert bool(_evaluate_strategy("b4", blocked, {}).mask.iloc[0]) is False
