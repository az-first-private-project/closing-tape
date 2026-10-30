"""
分析层纯函数单测（方案 F4 的一部分）
=====================================
原则（硬性）：
  - **不依赖数据库、不联网**：只 import 纯函数/模块级常量；
  - 若 pandas/numpy 未安装 → `pytest.importorskip` 整体跳过，而不是 import error；
  - 每个断言都对应一条"口径"，失败时能直接看出是哪条口径被改坏了。

覆盖：
  - `score_sector_technical`：上涨趋势分 > 下跌趋势分；历史不足返回中性 5.0；
  - `score_cross_section`：PE 最低者 value_score 最高、负 PE 视为最差、综合分在 0–10；
  - `industry_neutral_zscore`：同行业组内均值≈0、小样本行业回退全市场、组内同值返回 0；
  - `combine_sentiment`：缺北向时权重再分配、期望值仍为 8.0（不被拉回 5）；
  - `identify_leaders`：实力龙头取大市值中的成交额龙头、情绪龙头取 涨幅×换手 最高；
  - `compute_frames`：MA5 只依赖当日及以前（防未来函数），乱序输入会自动排序。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 允许直接 `pytest backend/tests/test_engine.py`（不依赖 conftest / 安装为包）
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pd = pytest.importorskip("pandas", reason="pandas 未安装，跳过分析层纯函数测试")
np = pytest.importorskip("numpy", reason="numpy 未安装，跳过分析层纯函数测试")
pytest.importorskip("sqlalchemy", reason="sqlalchemy 未安装，跳过分析层测试")

from app.engine.cross_section import industry_neutral_zscore  # noqa: E402
from app.engine.factors import composite_factor  # noqa: E402
from app.engine.features import compute_frames  # noqa: E402
from app.engine.leaders import (  # noqa: E402
    identify_leaders,
    identify_sentiment_leaders,
    identify_strength_leaders,
)
from app.engine.sentiment import (  # noqa: E402
    calc_margin_sentiment,
    calc_moneyflow_sentiment,
    calc_news_sentiment,
    calc_northward_sentiment,
    combine_sentiment,
)
from app.engine.stock_score import FACTOR_WEIGHTS, score_cross_section  # noqa: E402
from app.engine.technical_score import (  # noqa: E402
    score_sector_technical,
    score_sector_technical_detail,
)


# ============================================================
# 工具：构造测试数据
# ============================================================

def _linear_series(start: float, end: float, length: int = 60) -> pd.Series:
    """构造线性趋势序列（交易日升序）。"""
    return pd.Series(np.linspace(start, end, length), dtype="float64")


def _volume_series(length: int = 60, volume: float = 1_000_000.0) -> pd.Series:
    """构造成交量序列（平量）。"""
    return pd.Series(np.full(length, volume), dtype="float64")


def _cross_section() -> pd.DataFrame:
    """10 行构造横截面：PE/PB/PS 越低越好、动量/流动性越大越好（行 0 最优）。"""
    return pd.DataFrame({
        "code": [f"{i:06d}" for i in range(10)],
        "name": [f"股票{i}" for i in range(10)],
        "industry_l1": ["电子"] * 5 + ["银行"] * 5,
        "pe_ttm": [5.0, 12.0, 18.0, 22.0, 28.0, 35.0, 42.0, 55.0, 70.0, 90.0],
        "pb": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0],
        "ps_ttm": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0],
        "dv_ttm": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5, 0.3, 0.0, 0.0],
        "momentum_20": [15.0, 12.0, 9.0, 6.0, 3.0, 0.0, -3.0, -6.0, -9.0, -12.0],
        "momentum_60": [20.0, 15.0, 10.0, 5.0, 0.0, -5.0, -10.0, -15.0, -20.0, -25.0],
        "volatility_20": [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 25.0, 30.0, 35.0],
        "amount_ma20": [1e9, 8e8, 6e8, 5e8, 4e8, 3e8, 2e8, 1.5e8, 1e8, 5e7],
    })


# ============================================================
# 1. 板块技术分
# ============================================================

def test_score_sector_technical_rising_beats_falling() -> None:
    """上涨趋势的技术分必须显著高于下跌趋势；分数落在 0–10。"""
    rising = score_sector_technical(_linear_series(10.0, 20.0), _volume_series())
    falling = score_sector_technical(_linear_series(20.0, 10.0), _volume_series())

    assert 0.0 <= rising <= 10.0
    assert 0.0 <= falling <= 10.0
    assert rising > falling, f"上涨 {rising} 应高于下跌 {falling}"

    # 旧口径：趋势 40% + 动量 30% + 量能 30%；线性上涨趋势应拿到接近满分的趋势分
    score, detail = score_sector_technical_detail(
        _linear_series(10.0, 20.0), _volume_series()
    )
    assert score == rising
    assert detail["reason"] == "ok"
    assert detail["rows"] == 60
    assert detail["trend"] is not None and detail["trend"] > 5.0
    assert detail["weights"] == {"trend": 0.40, "momentum": 0.30, "volume": 0.30}


def test_score_sector_technical_insufficient_history_is_neutral() -> None:
    """历史不足 20 日 → 5.0（中性），且 detail 必须带 reason 供前端解释。"""
    assert score_sector_technical(
        _linear_series(10.0, 20.0, length=10), _volume_series(length=10)
    ) == pytest.approx(5.0)

    score, detail = score_sector_technical_detail(
        _linear_series(10.0, 20.0, length=10), _volume_series(length=10)
    )
    assert score == pytest.approx(5.0)
    assert detail["reason"] == "insufficient_history"
    assert detail["rows"] == 10
    assert detail["trend"] is None


# ============================================================
# 2. 个股横截面评分
# ============================================================

def test_score_cross_section_low_pe_gets_highest_value_score() -> None:
    """PE 最低者 value_score 最高；所有分数在 0–10；综合分加权正确。"""
    df = _cross_section()
    scores = score_cross_section(df)

    expected_columns = [
        "value_score", "quality_score", "momentum_score", "volatility_score",
        "liquidity_score", "sentiment_score", "composite_score",
    ]
    assert list(scores.columns) == expected_columns
    assert len(scores) == len(df)

    for column in expected_columns:
        assert scores[column].between(0.0, 10.0).all(), f"{column} 越界"

    # 行 0 的 PE/PB/PS 都最低 → 价值分必须满分 10，且为全表最高
    assert scores.loc[0, "value_score"] == pytest.approx(10.0)
    assert scores.loc[0, "value_score"] == scores["value_score"].max()
    # 情绪因子无个股级新闻数据 → 中性 5.0
    assert scores.loc[0, "sentiment_score"] == pytest.approx(5.0)

    # 综合分 = 0–10，且行 0 各因子几乎全优 → 综合分应为最高
    assert scores.loc[0, "composite_score"] == scores["composite_score"].max()
    assert set(FACTOR_WEIGHTS) == {
        "value", "quality", "momentum", "volatility", "liquidity", "sentiment",
    }
    assert sum(FACTOR_WEIGHTS.values()) == pytest.approx(1.0)


def test_score_cross_section_negative_pe_is_worst() -> None:
    """PE<=0（亏损）的 PE 子分必须按最差处理：value_score 被压到全表最低。"""
    df = _cross_section()
    df.loc[9, "pe_ttm"] = -5.0  # 行 9 原本 PE 最高，改成亏损后必须是最差
    scores = score_cross_section(df)

    # PE 子分被 fillna(0.0) → 价值分只能是全表最低，且低于同为高估值的行 8
    assert scores.loc[9, "value_score"] == scores["value_score"].min()
    assert scores.loc[9, "value_score"] < scores.loc[8, "value_score"]
    # 对照：不改成负值时，行 9 的价值分更高（说明是"负 PE 惩罚"而不是本来就最低）
    baseline = score_cross_section(_cross_section())
    assert baseline.loc[9, "value_score"] > scores.loc[9, "value_score"]


# ============================================================
# 3. 横截面行业中性化
# ============================================================

def test_industry_neutral_zscore_group_mean_zero() -> None:
    """同行业组内 z-score 均值≈0；小样本行业回退全市场；组内同值返回 0。"""
    df = pd.DataFrame({
        "industry_l1": ["电子"] * 10 + ["银行"] * 2 + ["煤炭"] * 8,
        "value": list(range(1, 11)) + [100.0, 200.0] + [5.0] * 8,
    })
    z = industry_neutral_zscore(df, "value", min_group=8)

    # 电子组（10 只 >= 8）走组内 z，总体标准差口径下组内均值严格为 0
    electronics = z[df["industry_l1"] == "电子"]
    assert abs(float(electronics.mean())) < 1e-9
    assert np.isfinite(electronics.to_numpy()).all()

    # 银行组只有 2 只 < min_group → 退化为全市场 z（数值有限、有区分度）
    banks = z[df["industry_l1"] == "银行"]
    assert np.isfinite(banks.to_numpy()).all()
    assert banks.nunique() == 2

    # 煤炭组 8 只但全组同值 → std==0，按约定返回 0（而不是 inf/NaN）
    coals = z[df["industry_l1"] == "煤炭"]
    assert (coals == 0.0).all()


def test_industry_neutral_zscore_keeps_nan() -> None:
    """输入 NaN 必须保留为 NaN，不能被当成 0 参与排名。"""
    df = pd.DataFrame({
        "industry_l1": ["电子"] * 8,
        "value": [1.0, 2.0, 3.0, 4.0, 6.0, 7.0, 8.0, np.nan],
    })
    z = industry_neutral_zscore(df, "value", min_group=8)
    assert np.isnan(z.iloc[-1])


def test_composite_factor_all_nan_factor_does_not_dilute() -> None:
    """整列为空（数据源无该字段）的因子不参与合成，也不会把综合分拉向 0。"""
    index = pd.RangeIndex(3)
    valid = pd.Series([1.0, 0.5, 0.0], index=index)
    empty = pd.Series([np.nan, np.nan, np.nan], index=index)

    result = composite_factor(
        {"value": valid, "quality": empty},
        {"value": 1.0, "quality": 1.0},
    )
    assert result.tolist() == pytest.approx(valid.tolist())


# ============================================================
# 4. 情绪合成（缺项权重再分配）
# ============================================================

def test_combine_sentiment_missing_part_reallocates_weights() -> None:
    """缺北向时权重按比例再分配：8/8 两子项 → 期望 8.0，而不是被拉回 5。"""
    parts = {"margin": 8.0, "northward": None, "moneyflow": 8.0}
    weights = {"margin": 0.35, "northward": 0.30, "moneyflow": 0.35}

    score, detail = combine_sentiment(parts, weights)

    # 旧口径（缺项固定 5 分参与加权）会得到 0.35*8 + 0.3*5 + 0.35*8 = 7.1；
    # 新口径只对可用项归一化 → (8*0.5 + 8*0.5) = 8.0，明示期望值
    assert score == pytest.approx(8.0)
    assert "northward" in detail["missing"]
    assert detail["effective_weights"]["margin"] == pytest.approx(0.5)
    assert detail["effective_weights"]["moneyflow"] == pytest.approx(0.5)
    assert sum(detail["effective_weights"].values()) == pytest.approx(1.0)

    # 全部缺失 → 中性 5.0（唯一允许的"缺数据=5分"场景）
    neutral, neutral_detail = combine_sentiment(
        {"margin": None, "northward": None, "moneyflow": None}, weights
    )
    assert neutral == pytest.approx(5.0)
    assert neutral_detail["effective_weights"] == {}


def test_sentiment_subitems_accept_none() -> None:
    """四个子项必须接受 None 并返回中性分，不能抛异常。"""
    assert calc_margin_sentiment(None) == pytest.approx(5.0)
    assert calc_margin_sentiment({}) == pytest.approx(5.0)
    assert calc_northward_sentiment(None) == pytest.approx(5.0)
    assert calc_news_sentiment(None) == pytest.approx(5.0)
    assert calc_news_sentiment([]) == pytest.approx(5.0)
    assert calc_moneyflow_sentiment(None) == pytest.approx(5.0)


def test_calc_news_sentiment_bayesian_shrinkage() -> None:
    """新闻情感：1 条样本向中性收缩 50%（期望 7.0）；>=3 条不收缩（期望 9.0）。"""
    # mean=0.8 → 原始分 5 + 0.8*5 = 9.0；1 条 < 3 → 收缩为 5 + (9-5)*0.5 = 7.0
    assert calc_news_sentiment([0.8]) == pytest.approx(7.0)
    # 3 条样本充足 → 不收缩
    assert calc_news_sentiment([0.8, 0.8, 0.8]) == pytest.approx(9.0)
    # 极端值被 clip 到 [-1, 1]
    assert calc_news_sentiment([5.0, 5.0, 5.0]) == pytest.approx(10.0)


# ============================================================
# 5. 龙头识别（单位：元）
# ============================================================

def _leader_stocks() -> list[dict]:
    """构造含大市值股、小市值股、超界股的板块成分。"""
    return [
        # 大市值组：600004 市值不是最大，但成交额最大 → 实力龙头
        {"code": "600001", "name": "大市值A", "market_cap": 3.0e11,
         "amount": 1.0e8, "pct_chg": 1.0, "turnover_rate": 1.0},
        {"code": "600002", "name": "大市值B", "market_cap": 2.5e11,
         "amount": 3.0e8, "pct_chg": 2.0, "turnover_rate": 2.0},
        {"code": "600003", "name": "大市值C", "market_cap": 2.2e11,
         "amount": 9.0e8, "pct_chg": 3.0, "turnover_rate": 3.0},
        {"code": "600004", "name": "大市值D", "market_cap": 1.2e11,
         "amount": 2.0e9, "pct_chg": 4.0, "turnover_rate": 4.0},
        # 情绪龙头候选：300001 涨幅×换手最高
        {"code": "300001", "name": "小盘A", "market_cap": 1.0e10,
         "amount": 5.0e8, "pct_chg": 8.0, "turnover_rate": 12.0},
        {"code": "300002", "name": "小盘B", "market_cap": 2.0e10,
         "amount": 4.0e8, "pct_chg": 5.0, "turnover_rate": 10.0},
        # 低于市值下界（50 亿）→ 情绪龙头应排除
        {"code": "300003", "name": "微盘", "market_cap": 1.0e9,
         "amount": 1.0e8, "pct_chg": 20.0, "turnover_rate": 30.0},
        # 高于市值上界（2000 亿）→ 情绪龙头应排除
        {"code": "300004", "name": "超大盘", "market_cap": 5.0e11,
         "amount": 1.0e9, "pct_chg": 30.0, "turnover_rate": 40.0},
    ]


def test_identify_leaders_strength_and_sentiment() -> None:
    """实力龙头=大市值池里成交额最高；情绪龙头=市值区间内 涨幅×换手 最高。"""
    stocks = _leader_stocks()
    leaders = identify_leaders(stocks)
    strength = leaders["strength_leaders"]
    sentiment = leaders["sentiment_leaders"]

    # 实力龙头：市值 Top10 内成交额最大的是 600004（2e9 元）
    assert strength[0]["code"] == "600004"
    assert strength[0]["type"] == "strength"
    # 单位必须是元（V5 硬约束），同时提供亿元展示字段
    assert strength[0]["market_cap"] == pytest.approx(1.2e11)
    assert strength[0]["market_cap_yi"] == pytest.approx(1200.0)
    assert "市值第" in strength[0]["rank_text"]

    # 情绪龙头：300001 热度 8% × 12% = 96 最高；超界股被排除
    picked = {leader["code"] for leader in sentiment}
    assert sentiment[0]["code"] == "300001"
    assert sentiment[0]["type"] == "sentiment"
    assert sentiment[0]["heat_score"] == pytest.approx(96.0)
    assert sentiment[0]["market_cap"] == pytest.approx(1.0e10)
    assert sentiment[0]["market_cap_yi"] == pytest.approx(100.0)
    assert "300003" not in picked and "300004" not in picked
    for leader in sentiment:
        assert 5e9 < leader["market_cap"] < 2e11


def test_identify_sentiment_leaders_all_down_fallback() -> None:
    """全板块下跌时退化为"最抗跌"，且标记 fallback=True。"""
    stocks = [
        {"code": "A1", "name": "抗跌股", "market_cap": 1.0e10,
         "pct_chg": -1.0, "turnover_rate": 10.0},
        {"code": "A2", "name": "深跌股", "market_cap": 1.2e10,
         "pct_chg": -5.0, "turnover_rate": 20.0},
    ]
    leaders = identify_sentiment_leaders(stocks, top_n=1)

    assert len(leaders) == 1
    assert leaders[0]["code"] == "A1"  # 跌幅最小 = 最抗跌
    assert leaders[0]["fallback"] is True
    assert "抗跌" in leaders[0]["rank_text"]


def test_identify_strength_leaders_empty_input() -> None:
    """空输入/无市值数据返回空列表，而不是抛异常。"""
    assert identify_strength_leaders([]) == []
    assert identify_strength_leaders([{"code": "X", "market_cap": None}]) == []


# ============================================================
# 6. 特征计算（防未来函数 / 乱序输入）
# ============================================================

def test_compute_frames_uses_only_past_data() -> None:
    """MA5 等滚动指标只能依赖当日及以前的数据（防未来函数）。"""
    length = 80
    close = np.arange(10.0, 10.0 + length, dtype="float64")
    daily = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=length, freq="D").date,
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.full(length, 1_000_000.0),
        "amount": close * 1_000_000.0,
    })

    result = compute_frames({"000001": daily})
    assert set(result) == {"000001"}
    features = result["000001"]
    assert "ma5" in features.columns and "momentum_20" in features.columns
    assert len(features) == length

    # 第 10 行的 MA5 必须等于第 6~10 行收盘价均值（不含未来第 11 行）
    assert features.loc[10, "ma5"] == pytest.approx(
        float(daily["close"].iloc[6:11].mean())
    )
    # 前 5 行没有足够历史 → MA5 为 NaN（不是用未来数据补齐）
    assert pd.isna(features.loc[0, "ma5"])
    assert pd.isna(features.loc[3, "ma5"])
    assert not pd.isna(features.loc[4, "ma5"])


def test_compute_frames_sorts_unsorted_input() -> None:
    """乱序输入会被排序，计算结果与升序输入一致。"""
    length = 70
    close = np.linspace(10.0, 30.0, length)
    daily = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=length, freq="D").date,
        "open": close, "high": close + 0.3, "low": close - 0.3,
        "close": close, "volume": np.full(length, 1e6),
        "amount": close * 1e6,
    })
    shuffled = daily.sample(frac=1.0, random_state=42)

    expected = compute_frames({"000001": daily})["000001"]
    actual = compute_frames({"000001": shuffled})["000001"]

    assert actual["trade_date"].is_monotonic_increasing
    assert actual["ma20"].iloc[-1] == pytest.approx(expected["ma20"].iloc[-1])
