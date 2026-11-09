"""
选股与回测层单测（D1–D5）
==========================
**不依赖数据库、不依赖网络**：只测纯函数逻辑。

覆盖：
  - D1 条件树：AND / OR / between / top_pct / bottom_pct / 行业约束 / 非法操作符；
  - D3 参数指纹：不同参数不同指纹、key 顺序无关、策略隔离；
  - D2 rank_signals：A3 央企 + 评分阈值；A4 股息率/PE 过滤；
    B1 双均线动量、B2 RSI6 超卖、B3 KDJ 金叉、B4 布林挤压、B5 下轨双确认，
    以及 B4/B5 的沪深300 市场门槛；
  - 统一前置过滤：ST / 停牌 / 退市 / 次新股 / 无行情；
  - D5 绩效：已知净值序列的 total_return / max_drawdown 精确值、FIFO 胜率；
  - D4 回测：T+1 开盘成交、股数整百、涨停留空；未来函数静态检测（负向 shift）。

运行方式（backend 目录）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests/test_strategy.py -q
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

# 允许从任意 cwd 运行 pytest（避免 app 包导入失败）
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.errors import InvalidParamError  # noqa: E402
from app.strategy import runner as runner_module  # noqa: E402
from app.strategy.backtest import (  # noqa: E402
    BacktestParams,
    Bar,
    assert_no_lookahead,
    detect_lookahead_usage,
    simulate_portfolio,
)
from app.strategy.condition import (  # noqa: E402
    Condition,
    ConditionGroup,
    OP,
    apply as apply_condition,
    describe,
    evaluate,
    from_dict,
)
from app.strategy.metrics import compute_metrics  # noqa: E402
from app.strategy.runner import params_fingerprint  # noqa: E402
from app.strategy.strategies import (  # noqa: E402
    _assert_data_ready,
    _score_breakdown,
    _validate_params,
    apply_common_filters,
    market_gate_payload,
    rank_signals,
)


# =====================================================================
# 测试数据
# =====================================================================

def _condition_frame() -> pd.DataFrame:
    """条件树测试用小横截面（6 行）。"""
    return pd.DataFrame(
        {
            "code": ["600000", "600001", "600002", "600003", "600004", "600005"],
            "name": ["甲", "乙", "丙", "丁", "戊", "己"],
            "pe_ttm": [10.0, 20.0, 5.0, 60.0, -2.0, 30.0],
            "amount": [1.0e8, 2.0e7, 5.0e7, 3.0e8, 1.0e8, None],
            "dv_ttm": [5.0, 1.0, 6.0, 0.5, 2.0, 4.5],
            "industry_l1": ["银行", "电子", "银行", "食品", "计算机", "电子"],
            "macd_golden_cross": [True, False, True, True, False, True],
        }
    )


def _short_frame() -> pd.DataFrame:
    """6 行短线策略横截面（B1–B5 共用，按需取列）。"""
    return pd.DataFrame(
        {
            "code": ["600000", "600001", "600002", "600003", "600004", "600005"],
            "name": ["甲", "乙", "丙", "丁", "戊", "己"],
            "close": [11.0, 10.5, 10.0, 9.95, 10.2, 10.0],
            "open": [10.0, 10.6, 9.9, 9.8, 10.1, 10.1],
            "pre_close": [10.0, 10.2, 10.0, 9.9, 10.1, 10.0],
            "low": [10.0, 10.1, 9.0, 9.2, 9.8, 10.0],
            "volume": [1600.0, 900.0, 1520.0, 800.0, 500.0, 1000.0],
            "vol_ma5": [1000.0] * 6,
            "amount_ma20": [3.0e8] * 6,
            "ema5": [11.0, 10.6, 10.1, 9.95, 10.2, 10.0],
            "ema20": [10.5, 10.5, 9.95, 9.6, 10.0, 10.0],
            "ema20_up": [True] * 6,
            "ema_cross_3d": [True, False, True, True, True, True],
            "ema60": [10.0] * 6,
            "ma60": [10.0, 10.0, 9.8, 10.0, 10.0, 10.0],
            "ma120": [9.0, 9.0, 9.0, 12.0, 9.0, 12.0],
            "roc10": [5.0, -1.0, 3.0, 2.0, 4.0, 1.0],
            "rsi6": [15.0, 25.0, 10.0, 18.0, 19.0, 30.0],
            "amp60": [20.0] * 6,
            "kdj_k": [20.0, 20.0, 40.0, 25.0, 10.0, 20.0],
            "kdj_d": [15.0] * 6,
            "kdj_j": [-5.0, 40.0, 5.0, -2.0, -10.0, 5.0],
            "kdj_j_min2": [-5.0, 30.0, 5.0, -2.0, -10.0, 5.0],
            "kdj_golden_cross": [True, True, True, False, True, True],
            "boll_lower": [9.6, 9.0, 9.6, 9.7, 9.9, 9.6],
            "boll_upper": [10.8, 11.0, 9.9, 10.2, 10.5, 10.4],
            "boll_mid": [10.2] * 6,
            "boll_width": [0.12, 0.20, 0.08, 0.05, 0.06, 0.08],
            "boll_width_pct60": [10.0, 50.0, 15.0, 5.0, 80.0, 30.0],
            "high20_close": [10.6, 10.8, 9.95, 10.0, 10.4, 10.3],
            "range20_pct": [8.0, 12.0, 6.0, 4.0, 20.0, 5.0],
            "pct_chg": [1.0] * 6,
            "industry_l1": ["电子"] * 6,
        }
    )


def _strategy_frame() -> pd.DataFrame:
    """10 行策略横截面（A3/A4 共用，按需取列）。"""
    count = 10
    return pd.DataFrame(
        {
            "code": [f"{600000 + index:06d}" for index in range(count)],
            "name": [f"股票{index}" for index in range(count)],
            "pe_ttm": [10.0, -5.0, 20.0, 15.0, 0.0, 25.0, 12.0, 60.0, None, 8.0],
            "amount": [1.0e8] * 6 + [1.0e7] + [1.0e8] * 3,
            "macd_golden_cross": [True] * count,
            "macd_dif": [0.10] * count,
            "macd_dea": [0.05] * count,
            "macd_hist": [0.05] * count,
            "rsi14": [55.0] * count,
            "pb": [2.0] * count,
            "ps_ttm": [3.0] * count,
            "dv_ttm": [6.0, 3.0, 1.0, 5.0, 2.0, 9.0, 10.0, 0.5, 4.0, 4.0],
            "close": [10.0] * count,
            "pct_chg": [1.0] * count,
            "ma_bullish": [True, True, False, True, True, False, True, True, True, True],
            "ma5": [11.0] * count,
            "ma10": [10.5] * count,
            "ma20": [10.0] * count,
            "main_net_5d": [5.0e5, 2.0e6, 1.0e7, 3.0e6, -1.0e6, 8.0e6, 1.5e6, 0.0, 4.0e6, None],
            "main_net_t0": [1.0e5] * count,
            "main_net_t1": [1.0e5] * count,
            "main_net_t2": [1.0e5] * count,
            "main_net_t3": [1.0e5] * count,
            "main_net_t4": [1.0e5] * count,
            "composite_score": [7.0, 8.0, 9.0, 5.0, 6.5, 8.5, 7.2, 6.0, 9.5, 5.5],
            "value_score": [5.0] * count,
            "quality_score": [5.0] * count,
            "momentum_score": [5.0] * count,
            "volatility_score": [5.0] * count,
            "liquidity_score": [5.0] * count,
            "sentiment_score": [5.0] * count,
            "ownership": ["央企", "央企", "民企", "央企", "央企", "民企",
                          "央企", "央企", "央企", "央企"],
            "industry_l1": ["电子", "计算机", "银行", "电子", "食品",
                            "计算机", "电子", "银行", "食品", "电子"],
        }
    )


# =====================================================================
# D1 条件树
# =====================================================================

def test_condition_and_between() -> None:
    """AND + between：只保留 0 < pe_ttm <= 30 且成交额 >= 5000 万。"""
    frame = _condition_frame()
    node = from_dict(
        {
            "logic": "AND",
            "children": [
                {"field": "pe_ttm", "op": "between", "value": [0.0, 30.0]},
                {"field": "amount", "op": "ge", "value": 5.0e7},
            ],
        }
    )
    assert isinstance(node, ConditionGroup)
    mask = evaluate(frame, node)
    assert list(frame.loc[mask, "code"]) == ["600000", "600002"]
    assert apply_condition(frame, node).shape[0] == 2


def test_condition_or() -> None:
    """OR：pe_ttm < 0 或成交额 >= 1 亿。"""
    frame = _condition_frame()
    node = from_dict(
        {
            "logic": "OR",
            "children": [
                {"field": "pe_ttm", "op": "lt", "value": 0},
                {"field": "amount", "op": "ge", "value": 1.0e8},
            ],
        }
    )
    mask = evaluate(frame, node)
    assert list(frame.loc[mask, "code"]) == ["600000", "600003", "600004"]


def test_condition_top_bottom_pct() -> None:
    """排名分位：top_pct=20 取最高 20%，bottom_pct=20 取最低 20%。"""
    frame = pd.DataFrame(
        {
            "code": [f"{code:06d}" for code in range(10)],
            "amount": [float(value) for value in range(1, 11)],
        }
    )
    top = from_dict({"field": "amount", "op": "top_pct", "value": 20})
    bottom = from_dict({"field": "amount", "op": "bottom_pct", "value": 20})
    assert list(frame.loc[evaluate(frame, top), "amount"]) == [9.0, 10.0]
    assert list(frame.loc[evaluate(frame, bottom), "amount"]) == [1.0, 2.0]


def test_condition_industry_constraint() -> None:
    """行业约束：industry in / exclude_industry not_in。"""
    frame = _condition_frame()

    include_node = from_dict(
        {"field": "industry_l1", "op": "in", "value": ["电子", "计算机"]}
    )
    assert list(frame.loc[evaluate(frame, include_node), "code"]) == [
        "600001", "600004", "600005",
    ]

    exclude_node = from_dict(
        {
            "logic": "AND",
            "children": [{"field": "amount", "op": "ge", "value": 1.0e8}],
            "exclude_industry": ["银行"],
        }
    )
    assert list(frame.loc[evaluate(frame, exclude_node), "code"]) == ["600003", "600004"]


def test_condition_invalid_op_and_field() -> None:
    """未知操作符 / 未知字段必须抛 InvalidParamError。"""
    frame = _condition_frame()
    with pytest.raises(InvalidParamError):
        from_dict({"field": "pe_ttm", "op": "foo", "value": 1})
    node = from_dict({"field": "not_exists", "op": "gt", "value": 1})
    with pytest.raises(InvalidParamError):
        evaluate(frame, node)
    with pytest.raises(InvalidParamError):
        evaluate(frame, from_dict({"field": "pe_ttm", "op": "between", "value": [1]}))


def test_condition_describe_and_types() -> None:
    """describe 可读 + 节点类型正确 + OP 含全部操作符。"""
    node = from_dict(
        {
            "logic": "AND",
            "children": [
                {"field": "pe_ttm", "op": "lt", "value": 30},
                {"field": "amount", "op": "top_pct", "value": 20},
            ],
        }
    )
    text = describe(node)
    assert "pe_ttm" in text and "排名前" in text and "且" in text
    assert isinstance(node, ConditionGroup)
    assert isinstance(node.children[0], Condition)
    assert {"gt", "ge", "lt", "le", "eq", "ne", "between", "in", "not_in",
            "top_pct", "bottom_pct"} <= set(OP)


# =====================================================================
# D3 参数指纹 / 参数校验
# =====================================================================

def test_params_fingerprint_stable_and_sensitive() -> None:
    """key 顺序无关；参数值/策略不同则指纹不同。"""
    fingerprint_a = params_fingerprint("b1", {"min_vol_ratio": 1.5, "limit": 20})
    fingerprint_b = params_fingerprint("b1", {"limit": 20, "min_vol_ratio": 1.5})
    assert fingerprint_a == fingerprint_b

    assert params_fingerprint("b1", {"min_vol_ratio": 1.5}) != params_fingerprint(
        "b1", {"min_vol_ratio": 2.0}
    )
    assert params_fingerprint("b1", {"min_vol_ratio": 1.5}) != params_fingerprint(
        "b2", {"min_vol_ratio": 1.5}
    )
    assert len(fingerprint_a) == 16


def test_validate_params_defaults_and_bounds() -> None:
    """默认值补齐 + 越界/未知参数抛错。"""
    normalized = _validate_params("b1", {})
    assert normalized == {"limit": 20, "min_vol_ratio": 1.0, "min_amount": 2.0e8}

    normalized = _validate_params("b2", {"rsi_max": "18"})
    assert normalized["rsi_max"] == 18.0

    with pytest.raises(InvalidParamError):
        _validate_params("b1", {"min_vol_ratio": -1})
    with pytest.raises(InvalidParamError):
        _validate_params("b1", {"unknown_param": 1})
    with pytest.raises(InvalidParamError):
        _validate_params("b1", {"min_vol_ratio": "abc"})


def test_a3_min_score_is_zero_to_ten_with_legacy_fallback() -> None:
    """A3 min_score 是 0–10 分制：默认 6.0；旧调用方传 60 → 归一为 6.0 并回显。

    run.params 保存的是归一化后的参数，所以前端能看到 6.0 而不是 60，
    避免"传了 60 却静默空结果"。
    """
    default_params = _validate_params("a3", {})
    assert default_params == {"limit": 20, "min_score": 6.0}

    # 旧前端可能传 0–100 口径的 60 → 归一为 0–10 口径的 6.0
    legacy = _validate_params("a3", {"min_score": 60})
    assert legacy["min_score"] == pytest.approx(6.0)

    normal = _validate_params("a3", {"min_score": 7.5})
    assert normal["min_score"] == pytest.approx(7.5)

    # 归一后仍越界（如 600 → 60 > 10）必须报错，而不是静默通过
    with pytest.raises(InvalidParamError):
        _validate_params("a3", {"min_score": 600})
    with pytest.raises(InvalidParamError):
        _validate_params("a3", {"min_score": -1})


def test_list_runs_excludes_backtest_by_default() -> None:
    """`list_runs` 默认排除回测 run（Lead 契约 B），需显式 include_backtest=True。"""
    signature = inspect.signature(runner_module.list_runs)
    assert signature.parameters["include_backtest"].default is False
    # 注意：真正 SQL 过滤在 DB 集成测试验证（单测不连库）


# =====================================================================
# D2 四策略排序
# =====================================================================

def test_rank_signals_b1_dual_ema_momentum() -> None:
    """B1：EMA 近 3 日金叉 + 放量 + MA60 之上，按 ROC10 降序。"""
    frame = _short_frame()
    result = rank_signals("b1", frame, {"limit": 20})
    # 600000(roc=5) 与 600002(roc=3) 满足；600004 量能 500 < 1000 被剔除
    assert list(result["code"]) == ["600000", "600002"]
    assert result["roc10"].is_monotonic_decreasing
    assert list(result["score"]) == pytest.approx([5.0, 3.0])
    assert result["metrics"].iloc[0]["ema20"] == pytest.approx(10.5)

    # 参数真生效：量能倍数提高到 1.7 → 只剩 600000（1600/1000=1.6 也出局）
    strict = rank_signals("b1", frame, {"limit": 20, "min_vol_ratio": 1.7})
    assert len(strict) == 0


def test_rank_signals_b2_rsi_oversold_reversal() -> None:
    """B2：RSI6 超卖 + 止跌 K 线 + MA120 趋势，按 RSI6 升序。"""
    frame = _short_frame()
    result = rank_signals("b2", frame, {"limit": 20})
    # 600002(10) / 600000(15) / 600004(19)；600003 收盘跌破 MA120*0.9 被剔除
    assert list(result["code"]) == ["600002", "600000", "600004"]
    assert result["rsi6"].is_monotonic_increasing
    assert list(result["score"]) == pytest.approx([10.0, 15.0, 19.0])

    strict = rank_signals("b2", frame, {"limit": 20, "rsi_max": 12.0})
    assert list(strict["code"]) == ["600002"]


def test_rank_signals_b3_kdj_low_golden_cross() -> None:
    """B3：J 值低位 + K 上穿 D + EMA60 之上，按 J 值升序。"""
    frame = _short_frame()
    result = rank_signals("b3", frame, {"limit": 20})
    # 600004(J=-10) / 600000(J=-5)；600002 K=40 超上限
    assert list(result["code"]) == ["600004", "600000"]
    assert result["kdj_j"].is_monotonic_increasing

    strict = rank_signals("b3", frame, {"limit": 20, "j_max": -6.0})
    assert list(strict["code"]) == ["600004"]


def test_rank_signals_b4_boll_squeeze_breakout() -> None:
    """B4：带宽低分位 + 突破上轨新高 + 放量 + 区间收窄，按带宽分位升序。"""
    frame = _short_frame()
    result = rank_signals("b4", frame, {"limit": 20})
    assert list(result["code"]) == ["600000", "600002"]
    assert result["boll_width_pct60"].is_monotonic_increasing

    strict = rank_signals("b4", frame, {"limit": 20, "vol_mult": 1.55})
    assert list(strict["code"]) == ["600000"]


def test_rank_signals_b5_boll_lower_kdj_reversal() -> None:
    """B5：触及布林下轨 + J 超卖 + 缩量止跌，按 J 值升序。"""
    frame = _short_frame()
    result = rank_signals("b5", frame, {"limit": 20})
    # 600004(J=-10，收盘高于 MA120)；600003(J=-2，偏离下轨 2.6% ≤ 8% 放行)
    assert list(result["code"]) == ["600004", "600003"]
    assert result["kdj_j"].is_monotonic_increasing

    strict = rank_signals("b5", frame, {"limit": 20, "j_max": -5.0})
    assert list(strict["code"]) == ["600004"]


def test_b4_b5_market_gate_blocks_signals() -> None:
    """市场状态门槛：沪深300 不满足时 B4/B5 必须空结果（attrs 由 build_candidates 注入）。"""
    frame = _short_frame()
    blocked = frame.copy()
    blocked.attrs["market"] = {"b4_ok": False, "b5_ok": False}
    assert len(rank_signals("b4", blocked, {"limit": 20})) == 0
    assert len(rank_signals("b5", blocked, {"limit": 20})) == 0
    # 指数缺失（available=False）时放行，不因基准缺数据而永久空结果
    allowed = frame.copy()
    allowed.attrs["market"] = {"available": False, "b4_ok": True, "b5_ok": True}
    assert len(rank_signals("b4", allowed, {"limit": 20})) == 2
    assert len(rank_signals("b5", allowed, {"limit": 20})) == 2


def test_rank_signals_a4_dividend_and_pe_filters() -> None:
    """A4：PE>0 且 <pe_max 且 dv_ttm>=div_min，按股息率降序。"""
    frame = _strategy_frame()
    result = rank_signals("a4", frame, {"limit": 20, "pe_max": 15, "div_min": 4})
    # 命中：pe=10/dv=6, pe=12/dv=10, pe=8/dv=4（pe=15 不满足严格 <15）
    assert list(result["code"]) == ["600006", "600000", "600009"]
    assert (result["pe_ttm"] > 0).all()
    assert (result["dv_ttm"] >= 4).all()
    assert result["dv_ttm"].is_monotonic_decreasing


def test_rank_signals_a3_central_enterprise_and_score() -> None:
    """A3：央企 + composite_score >= min_score（0–10 分制），按评分降序。"""
    frame = _strategy_frame()
    result = rank_signals("a3", frame, {"limit": 20, "min_score": 7.0})
    assert list(result["code"]) == ["600008", "600001", "600006", "600000"]
    assert "600002" not in set(result["code"])  # 民企
    assert "600005" not in set(result["code"])  # 民企
    assert result["composite_score"].is_monotonic_decreasing
    assert (result["composite_score"] >= 7.0).all()
    assert result["metrics"].iloc[0]["ownership"] == "央企"
    # score = 综合评分原始值（0–10），不是百分位分
    assert list(result["score"]) == list(result["composite_score"])


def test_a3_score_breakdown_uses_persisted_detail() -> None:
    """A3 拆解直接复用 fact_stock_score_daily.detail，权重/分位不自造。"""
    record = {
        "composite_score": 8.15,
        "value_score": 5.0,
        "quality_score": 9.29,
        "momentum_score": 9.36,
        "volatility_score": 9.75,
        "liquidity_score": 9.71,
        "sentiment_score": 5.0,
        "detail": {
            "percentile": {
                "value": 0.5, "quality": 0.9293, "momentum": 0.9362,
                "volatility": 0.9753, "liquidity": 0.9712, "sentiment": 0.5,
            },
            "weights": {
                "value": 0.20, "quality": 0.15, "momentum": 0.25,
                "volatility": 0.15, "liquidity": 0.15, "sentiment": 0.10,
            },
            "raw": {"pe_ttm": None, "pb": None, "ps_ttm": None, "dv_ttm": 3.9358},
            "sentiment_source": "neutral",
        },
    }
    breakdown = _score_breakdown(record)
    assert breakdown is not None
    assert breakdown["composite"] == pytest.approx(8.15)
    assert [part["key"] for part in breakdown["parts"]] == [
        "value", "quality", "momentum", "volatility", "liquidity", "sentiment",
    ]
    assert [part["weight"] for part in breakdown["parts"]] == pytest.approx(
        [0.20, 0.15, 0.25, 0.15, 0.15, 0.10]
    )
    weighted = sum(part["score"] * part["weight"] for part in breakdown["parts"])
    assert weighted == pytest.approx(breakdown["composite"], abs=0.01)
    assert breakdown["weight_sum"] == pytest.approx(1.0)
    contributions = [part["contribution"] for part in breakdown["parts"]]
    assert all(value is not None for value in contributions)
    assert sum(contributions) == pytest.approx(breakdown["composite"], abs=0.01)
    assert breakdown["parts"][1]["raw"] == {"pb": None, "dv_ttm": 3.9358}
    json.dumps(breakdown, ensure_ascii=False, allow_nan=False)
    assert _score_breakdown({}) is None

    # 分位缺失：中性 5.0 分展示，但不计入权重与贡献；贡献之和仍等于综合分
    missing = dict(record)
    missing["detail"] = {
        **record["detail"],
        "percentile": {**record["detail"]["percentile"], "sentiment": None},
    }
    missing["composite_score"] = 8.5
    partial = _score_breakdown(missing)
    assert partial is not None
    assert partial["weight_sum"] == pytest.approx(0.9)
    sentiment = partial["parts"][-1]
    assert sentiment["score"] == pytest.approx(5.0)
    assert sentiment["contribution"] is None
    assert sum(
        part["contribution"] for part in partial["parts"] if part["contribution"] is not None
    ) == pytest.approx(partial["composite"], abs=0.01)
    json.dumps(partial, ensure_ascii=False, allow_nan=False)


def test_market_gate_payload_for_run_level_hint() -> None:
    """B4/B5 的 run 级门槛摘要：0 信号时前端可解释原因，缓存 run 也能现算。"""
    market = {
        "available": True,
        "index_code": "000300",
        "close": 4103.54,
        "ma20": 4275.67,
        "pct_chg": -0.8,
        "b4_ok": False,
        "b5_ok": True,
    }
    b4 = market_gate_payload(market, "b4")
    assert b4 is not None
    assert b4["passed"] is False
    assert b4["threshold"] == pytest.approx(4254.29165, abs=0.01)
    assert b4["rule"] == "沪深300 收盘 ≥ MA20 × 0.995"
    assert b4["close"] == pytest.approx(4103.54)

    b5 = market_gate_payload(market, "b5")
    assert b5 is not None
    assert b5["passed"] is True
    assert b5["threshold"] == -2.0

    missing = market_gate_payload(
        {"available": False, "index_code": "000300", "b4_ok": True, "b5_ok": True}, "b4"
    )
    assert missing is not None
    assert missing["available"] is False
    assert missing["passed"] is True
    assert "放行" in missing["rule"]
    assert market_gate_payload(None, "b4") is None
    json.dumps(b4, ensure_ascii=False, allow_nan=False)


# =====================================================================
# score 语义（Lead 定稿：score = 排序指标原始值；"得分"用 composite_score）
# =====================================================================

def test_score_semantics_is_raw_sort_metric() -> None:
    """score = 排序指标原始值：A3=综合分、A4=股息率、B1=ROC10、B2=RSI6、B3=J。"""
    frame = _strategy_frame()
    short = _short_frame()

    a4 = rank_signals("a4", frame, {"limit": 20, "pe_max": 15, "div_min": 4})
    assert list(a4["score"]) == pytest.approx(list(a4["dv_ttm"]))

    b1 = rank_signals("b1", short, {"limit": 20})
    assert list(b1["score"]) == pytest.approx(list(b1["roc10"]))

    b2 = rank_signals("b2", short, {"limit": 20})
    assert list(b2["score"]) == pytest.approx(list(b2["rsi6"]))

    b3 = rank_signals("b3", short, {"limit": 20})
    assert list(b3["score"]) == pytest.approx(list(b3["kdj_j"]))


def test_signal_items_have_composite_score_top_level() -> None:
    """契约 5.2 data[] 顶层必须带 composite_score（0–10）；metrics 覆盖策略字段。"""
    frame = _strategy_frame()
    signals = rank_signals("a4", frame, {"limit": 20, "pe_max": 15, "div_min": 4})
    items = runner_module._items_from_frame(signals)  # noqa: SLF001 - 契约序列化测试
    assert items, "示例数据应有信号"
    for item in items:
        assert set(item) >= {
            "code", "name", "rank", "score", "composite_score", "close", "pct_chg", "metrics",
        }
        assert item["composite_score"] is not None
        assert isinstance(item["metrics"], dict)
        assert "pe_ttm" in item["metrics"] and "dv_ttm" in item["metrics"]


def test_rank_signals_accepts_decimal_columns() -> None:
    """asyncpg Numeric 返回 Decimal：rank_signals 内部必须转 float 后再比较。"""
    frame = _short_frame()
    decimal_frame = frame.copy()
    for column in ("close", "volume", "vol_ma5", "amount_ma20", "ema20", "ema5", "ma60", "roc10"):
        decimal_frame[column] = [
            None if value is None else Decimal(str(value)) for value in decimal_frame[column]
        ]
    result = rank_signals("b1", decimal_frame, {"limit": 20})
    assert len(result) == 2
    assert result["roc10"].dtype == "float64"
    assert list(result["code"]) == ["600000", "600002"]


def test_rank_signals_empty_input() -> None:
    """空候选池返回结构完整的空表（API 不 500）。"""
    empty = pd.DataFrame(columns=["code", "name"])
    result = rank_signals("b1", empty, {})
    assert len(result) == 0
    assert {"code", "name", "rank", "score", "metrics"} <= set(result.columns)


# =====================================================================
# 统一前置过滤（四策略共用）
# =====================================================================

def test_apply_common_filters() -> None:
    """剔除 ST/停牌/退市/次新股/无行情。"""
    trade_date = date(2026, 2, 13)
    # 真实场景下 trade_calendar 覆盖整个历史区间（HISTORY_START 起），这里用长日历模拟
    open_dates = [item.date() for item in pd.bdate_range("2021-01-04", "2026-02-13")]
    frame = pd.DataFrame(
        {
            "code": ["000001", "000002", "000003", "000004", "000005", "000006"],
            "name": ["正常", "ST股", "停牌", "退市", "次新", "正常2"],
            "is_st": [False, True, False, False, False, False],
            "status": [1, 1, 2, 1, 1, 1],
            "delist_date": [None, None, None, pd.Timestamp("2026-02-01"), None, None],
            "list_date": [
                pd.Timestamp("2020-01-01"),
                pd.Timestamp("2020-01-01"),
                pd.Timestamp("2020-01-01"),
                pd.Timestamp("2020-01-01"),
                pd.Timestamp("2026-02-10"),
                pd.Timestamp("2015-06-01"),
            ],
            "is_suspended": [False, False, False, False, False, False],
            "close": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        }
    )
    filtered = apply_common_filters(frame, trade_date, open_dates)
    assert list(filtered["code"]) == ["000001", "000006"]

    # 当日无行情（close 为 NaN）也要剔除
    frame_missing_close = frame.copy()
    frame_missing_close.loc[0, "close"] = None
    filtered_missing = apply_common_filters(frame_missing_close, trade_date, open_dates)
    assert "000001" not in set(filtered_missing["code"])


def test_b_ready_requires_feature_coverage() -> None:
    """回填到一半（覆盖率 <50%）必须拒绝执行，而不是用少数股票生成信号并缓存。"""
    from app.core.errors import DataUnavailableError

    frame = pd.DataFrame(
        {
            "ema20": [1.0, None, None, None, None, None],
            "amount_ma20": [1.0] * 6,
        }
    )
    frame.attrs["trade_date"] = date(2026, 10, 26)
    with pytest.raises(DataUnavailableError, match="覆盖不足"):
        _assert_data_ready("b1", frame)

    frame.loc[1:2, "ema20"] = 1.0  # 3/6 = 50%，达到门槛
    _assert_data_ready("b1", frame)


# =====================================================================
# D5 绩效指标
# =====================================================================

def test_compute_metrics_known_nav() -> None:
    """已知净值序列：total_return / max_drawdown 精确值。"""
    nav = pd.Series(
        [1.0, 1.10, 0.99, 1.05],
        index=pd.date_range("2026-01-05", periods=4, freq="B"),
    )
    metrics = compute_metrics(nav, None, [], 1, 1_000_000.0)
    assert metrics["total_return"] == pytest.approx(0.05)
    assert metrics["max_drawdown"] == pytest.approx(0.99 / 1.10 - 1.0)
    assert metrics["trade_count"] == 0
    assert metrics["win_rate"] == 0.0
    assert metrics["benchmark_return"] is None


def test_compute_metrics_benchmark_and_fifo_win_rate() -> None:
    """基准超额 + FIFO 胜率/盈亏比 + 换手。"""
    nav = pd.Series(
        [1.0, 1.05, 1.10, 1.08],
        index=pd.date_range("2026-01-05", periods=4, freq="B"),
    )
    benchmark = pd.Series(
        [1.0, 1.01, 1.02, 1.03],
        index=nav.index,
    )
    trades = [
        # 第一回合：+200
        {"date": "2026-01-06", "code": "600000", "side": "buy",
         "price": 10.0, "shares": 100, "amount": 1000.0, "fee": 0.0},
        {"date": "2026-01-07", "code": "600000", "side": "sell",
         "price": 12.0, "shares": 100, "amount": 1200.0, "fee": 0.0},
        # 第二回合：-200
        {"date": "2026-01-08", "code": "600001", "side": "buy",
         "price": 20.0, "shares": 100, "amount": 2000.0, "fee": 0.0},
        {"date": "2026-01-09", "code": "600001", "side": "sell",
         "price": 18.0, "shares": 100, "amount": 1800.0, "fee": 0.0},
        # 失败单（shares=0）不计入成交
        {"date": "2026-01-09", "code": "600002", "side": "buy",
         "price": None, "shares": 0, "amount": 0.0, "fee": 0.0},
    ]
    metrics = compute_metrics(nav, benchmark, trades, 2, 1_000_000.0)
    assert metrics["total_return"] == pytest.approx(0.08)
    assert metrics["benchmark_return"] == pytest.approx(0.03)
    assert metrics["excess_return"] == pytest.approx(0.05)
    assert metrics["trade_count"] == 4
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert metrics["profit_loss_ratio"] == pytest.approx(1.0)
    assert metrics["turnover"] == pytest.approx((1000 + 1200 + 2000 + 1800) / 2 / 1_000_000.0)


def test_compute_metrics_empty_nav() -> None:
    """空净值不抛异常，返回零值结构。"""
    metrics = compute_metrics(pd.Series(dtype="float64"), None, [], 0, 1_000_000.0)
    assert metrics["total_return"] == 0.0
    assert metrics["max_drawdown"] == 0.0


# =====================================================================
# D4 回测：成交口径 / 资金流 / 未来函数
# =====================================================================

def _bar(open_price: float, close_price: float, *, limit_up: bool = False,
         limit_down: bool = False, suspended: bool = False,
         adj_factor: float | None = 1.0) -> Bar:
    """构造测试用 Bar。"""
    return Bar(
        open=open_price,
        close=close_price,
        adj_factor=adj_factor,
        is_limit_up=limit_up,
        is_limit_down=limit_down,
        is_suspended=suspended,
    )


def test_simulate_uses_next_open_and_lot_size() -> None:
    """T 日信号 → T+1 开盘（含滑点）成交，股数整百；T 日净值仍是 1.0。"""
    day1, day2, day3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    price_book = {
        "000001": {
            day1: _bar(9.0, 9.5),
            day2: _bar(10.0, 10.5),
            day3: _bar(10.5, 11.0),
        }
    }
    params = BacktestParams(
        rebalance_days=20,
        max_positions=1,
        commission_rate=0.0001,
        stamp_tax_rate=0.0005,
        slippage_rate=0.001,
        min_commission=5.0,
        initial_cash=100_000.0,
    )
    params.validate()

    curve, trades, adjusted_ok = simulate_portfolio(
        [day1, day2, day3],
        {day2: [{"code": "000001", "name": "测试股"}]},
        price_book,
        {},
        params,
    )

    # 信号日（day1）不建仓 → 净值 1.0，杜绝"拿到 T+1 价格再做 T 的决策"
    assert curve[0]["date"] == day1.isoformat()
    assert curve[0]["nav"] == pytest.approx(1.0)

    buys = [item for item in trades if item["side"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["date"] == day2.isoformat()
    assert buys[0]["price"] == pytest.approx(round(10.0 * 1.001 + 1e-9, 2))
    assert buys[0]["shares"] % 100 == 0
    assert buys[0]["shares"] > 0
    assert buys[0]["reason"] == "rebalance"
    assert adjusted_ok is True

    # 精确资金流：100000 / (10.01*100) 手 → 9900 股；佣金 9.91；净值 = (现金 + 9900*10.5)/100000
    expected_cash = 100_000.0 - round(10.01 * 9900, 2) - round(max(10.01 * 9900 * 0.0001, 5.0), 2)
    expected_nav = round((expected_cash + 9900 * 10.5) / 100_000.0, 6)
    assert curve[1]["nav"] == pytest.approx(expected_nav, abs=1e-6)


def test_simulate_skips_limit_up_buy() -> None:
    """涨停买不进：跳过并记录 reason=limit_up，净值保持 1.0。"""
    day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
    price_book = {"000001": {day1: _bar(9.0, 9.5), day2: _bar(11.0, 11.0, limit_up=True)}}
    params = BacktestParams(max_positions=1, initial_cash=100_000.0)
    params.validate()

    curve, trades, _ = simulate_portfolio(
        [day1, day2],
        {day2: [{"code": "000001", "name": "涨停股"}]},
        price_book,
        {},
        params,
    )
    failed = [item for item in trades if item["side"] == "buy"]
    assert len(failed) == 1
    assert failed[0]["reason"] == "limit_up"
    assert failed[0]["shares"] == 0
    assert curve[-1]["nav"] == pytest.approx(1.0)


def test_simulate_requires_adj_factor_flag() -> None:
    """adj_factor 缺失 → 退化原始价并标记 adjusted=False。"""
    day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
    price_book = {
        "000001": {day1: _bar(9.0, 9.5), day2: _bar(10.0, 10.5, adj_factor=None)}
    }
    params = BacktestParams(max_positions=1, initial_cash=100_000.0)
    params.validate()
    _, _, adjusted_ok = simulate_portfolio(
        [day1, day2],
        {day2: [{"code": "000001", "name": "无复权股"}]},
        price_book,
        {},
        params,
    )
    assert adjusted_ok is False


def test_detect_lookahead_usage_hits_negative_shift() -> None:
    """静态检测必须命中 shift(-1) 这种未来函数写法。"""
    bad_source = "df['next_close'] = df['close'].shift(-1)\n"
    issues = detect_lookahead_usage(bad_source)
    assert issues
    assert "疑似未来函数" in issues[0]

    good_source = "df['prev_close'] = df['close'].shift(1)\n"
    assert detect_lookahead_usage(good_source) == []


def test_assert_no_lookahead_package_clean_and_catches_bad_file(tmp_path: Path) -> None:
    """本包源码自检通过；被人为写入负向 shift 的文件必须报错。"""
    assert_no_lookahead()  # 不抛异常 = 本包没有未来函数

    bad_file = tmp_path / "bad_backtest.py"
    bad_file.write_text("value = df['close'].shift(-1)\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="未来函数"):
        assert_no_lookahead(bad_file)


def test_backtest_params_from_dict() -> None:
    """契约 6.1 短参数名 → dataclass，越界/未知参数抛错。"""
    params = BacktestParams.from_dict(
        {
            "rebalance_days": 10,
            "max_positions": 5,
            "benchmark": "sh000905",
            "commission": 0.0003,
            "slippage": 0.002,
        }
    )
    assert params.rebalance_days == 10
    assert params.max_positions == 5
    assert params.benchmark_code == "sh000905"
    assert params.commission_rate == 0.0003
    assert params.slippage_rate == 0.002

    contract = params.as_contract_dict({"pe_max": 30.0, "limit": 5})
    assert contract["benchmark"] == "sh000905"
    assert contract["strategy"] == {"pe_max": 30.0, "limit": 5}

    with pytest.raises(InvalidParamError):
        BacktestParams.from_dict({"max_positions": 0})
    with pytest.raises(InvalidParamError):
        BacktestParams.from_dict({"not_a_param": 1})
