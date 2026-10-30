"""
历史估值源测试（不联网 / 不连库）
=================================
覆盖 ``app.ingest.valuation`` 的纯函数口径与"阶段 2 零网络"约束：

    ① ``normalize_financials``：中文列名容错（改版/缺列/全角括号）、脏值转数值、按报告期排序；
    ② ``eps_ttm_series``：季度累计 → TTM 的三期推导 + 年报（1231）特例 + 缺期返回 NaN；
    ③ ``build_valuation_frame``：**未来函数**（报告期 2024-03-31 + 90 天滞后，
       2024-06-01 之前拿不到，2024-06-29 起才可用）、``announced_date`` 优先、
       多报告期取"已可知的最新一期"、行序/列名契约；
    ④ ``pb`` / ``dv_ttm`` 边界：``bps <= 0``、``eps_ttm == 0``、``payout`` 缺失 → NaN；
       负 EPS 允许得到负 PE（由策略层过滤 PE<=0）；
    ⑤ ``dim_stock_financial`` 行形态直接入参（stage 2 不依赖 akshare 列名）；
    ⑥ ``compute_valuation`` **不发起任何网络请求**（把 akshare 换成会抛异常的桩，
       再把 DB 读写替换成内存桩，仍然成功返回）；
    ⑦ 更新语句只碰 ``pe_ttm / pb / dv_ttm``（不得覆盖 close/volume/amount）。

约定：所有用例都**不联网、不连库**（DB 相关用例用 monkeypatch 的内存桩），
与 ``backend/tests/test_ingest.py`` 保持同一风格（``pytest.importorskip("pandas")``）。
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest

pd = pytest.importorskip("pandas")

from app.ingest import valuation  # noqa: E402
from app.ingest.valuation import (  # noqa: E402
    REPORT_LAG_DAYS,
    build_valuation_frame,
    compute_valuation,
    dv_ttm_series,
    eps_ttm_series,
    fetch_financials,
    latest_knowable_report_period,
    normalize_dividends,
    normalize_financials,
)


# ============================================================
# 测试工具
# ============================================================
def _standard_raw() -> pd.DataFrame:
    """一份"实测列名"的新浪财报指标原始表（乱序 + 脏值）。"""
    return pd.DataFrame(
        {
            "日期": [date(2024, 3, 31), date(2023, 12, 31), date(2023, 9, 30)],
            "摊薄每股收益(元)": [0.40, 1.20, 0.90],
            "每股收益_调整后(元)": [0.41, 1.21, 0.91],
            "每股净资产_调整前(元)": [11.1, "--", 10.1],
            "每股净资产_调整后(元)": [11.0, 10.5, 10.0],
            "净资产收益率(%)": [3.5, 11.0, 8.0],
            "股息发放率(%)": [None, 30.0, None],
        }
    )


def _prices(*rows: tuple[date, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"trade_date": [row[0] for row in rows], "close": [row[1] for row in rows]}
    )


def _fins_from_db(*rows: dict[str, Any]) -> pd.DataFrame:
    """模拟 ``dim_stock_financial`` 的行（stage 2 的真实输入形态）。"""
    columns = [
        "code", "report_date", "announced_date", "eps_ttm", "bps", "payout_ratio",
        "eps", "roe",
    ]
    return pd.DataFrame([{column: row.get(column) for column in columns} for row in rows],
                        columns=columns)


# ============================================================
# ① normalize_financials
# ============================================================
def test_normalize_financials_standard_columns() -> None:
    """实测列名：取值优先级（eps 调整后 > 摊薄 / bps 调整前 > 调整后）+ 数值清洗 + 升序。"""
    fins = normalize_financials(_standard_raw())

    assert list(fins.columns) == [
        "report_date", "eps", "bps", "roe", "payout_ratio",
    ]
    # 升序
    assert list(fins["report_date"]) == [
        date(2023, 9, 30), date(2023, 12, 31), date(2024, 3, 31),
    ]
    # eps 优先"每股收益_调整后"（0.41 而不是摊薄的 0.40）
    assert fins["eps"].tolist() == pytest.approx([0.91, 1.21, 0.41])
    # bps 优先"每股净资产_调整前"（2023-12-31 那行调整前是 "--" → 逐行回落到调整后 10.5）
    assert fins["bps"].tolist() == pytest.approx([10.1, 10.5, 11.1])
    assert fins["roe"].tolist() == pytest.approx([8.0, 11.0, 3.5])
    assert fins["payout_ratio"].isna().tolist() == [True, False, True]

    # 全部是 float 数值列（下游算 PE 不用再转换）
    for column in ("eps", "bps", "roe", "payout_ratio"):
        assert finandtype_is_float(fins[column]), column


def finandtype_is_float(series: pd.Series) -> bool:
    """归一化后的数值列必须是 float64（NaN 而不是 object）。"""
    return bool(pd.api.types.is_float_dtype(series))


def test_normalize_financials_bps_prefers_adjusted_before() -> None:
    """BPS 必须用「调整前」：688981 实测 ``调整后=594.8588`` 是脏数据（真实约 25.7）。

    用"调整后"算 PB 会错 20 倍，A1 的低估值筛选会直接崩掉 —— 这条测试是防回归的关键。
    """
    raw = pd.DataFrame(
        {
            "日期": [date(2023, 12, 31), date(2024, 3, 31)],
            "每股净资产_调整后(元)": [None, 594.8588],
            "每股净资产_调整前(元)": [24.0, 25.7375],
            "摊薄每股收益(元)": [0.20, 0.50],
            "每股收益_调整后(元)": [None, 0.52],
        }
    )
    fins = normalize_financials(raw)
    assert fins["bps"].tolist() == pytest.approx([24.0, 25.7375])
    # eps：主列空 → 逐行回落到摊薄
    assert fins["eps"].tolist() == pytest.approx([0.20, 0.52])


def test_normalize_financials_tolerates_renamed_columns() -> None:
    """列名改版（去单位后缀 / 全角括号 / 缺 payout 列）仍然可用，缺列 → 全 NaN 不 KeyError。"""
    raw = pd.DataFrame(
        {
            # 全角括号 + 没有"(元)"后缀 → 只能靠正则前缀匹配命中
            "日期": ["20240331", "2023-12-31"],
            "每股收益_调整后（元）": [1.5, 1.2],
            "每股净资产_调整前": [9.8, 9.1],
            "净资产收益率": [15.0, 12.0],
            # 没有"股息发放率"列
        }
    )
    fins = normalize_financials(raw)

    assert fins["report_date"].tolist() == [date(2023, 12, 31), date(2024, 3, 31)]
    assert fins["eps"].tolist() == pytest.approx([1.2, 1.5])
    assert fins["bps"].tolist() == pytest.approx([9.1, 9.8])
    assert fins["roe"].tolist() == pytest.approx([12.0, 15.0])
    # 缺 payout 列：列仍然存在，但整列 NaN（"缺列返回 None 而不是 KeyError"）
    assert "payout_ratio" in fins.columns
    assert fins["payout_ratio"].isna().all()


def test_normalize_financials_empty_and_missing_date() -> None:
    """空输入 / 缺日期列 → 返回同结构空表（绝不抛异常）。"""
    for raw in (None, pd.DataFrame()):
        fins = normalize_financials(raw)
        assert fins.empty
        assert list(fins.columns) == list(valuation.FIN_COLUMNS)

    # 有数据但没有日期列 → 无法定位报告期，返回空表
    fins = normalize_financials(pd.DataFrame({"每股收益_调整后(元)": [1.0]}))
    assert fins.empty
    assert list(fins.columns) == list(valuation.FIN_COLUMNS)


# ============================================================
# ② eps_ttm_series
# ============================================================
def _cumulative_eps_frame() -> pd.DataFrame:
    """两个完整年度 + 2024Q1 的**累计** EPS（用于验证 TTM 推导）。"""
    rows = [
        (date(2022, 3, 31), 0.20),
        (date(2022, 6, 30), 0.50),
        (date(2022, 9, 30), 0.80),
        (date(2022, 12, 31), 1.10),
        (date(2023, 3, 31), 0.30),
        (date(2023, 6, 30), 0.60),
        (date(2023, 9, 30), 0.90),
        (date(2023, 12, 31), 1.20),
        (date(2024, 3, 31), 0.40),
    ]
    return pd.DataFrame(
        {"report_date": [r[0] for r in rows], "eps": [r[1] for r in rows]}
    )


def test_eps_ttm_series_three_period_formula_and_annual_special() -> None:
    """TTM = 本期累计 + 上年年报 − 上年同期；1231 特例 = 该期值（不用差分）。"""
    ttm = eps_ttm_series(_cumulative_eps_frame())
    by_period = dict(zip(ttm["report_date"], ttm["eps_ttm"]))
    basis = dict(zip(ttm["report_date"], ttm["eps_ttm_basis"]))

    # 年报特例：2023 年报 TTM 就是 1.20（12 个月）
    assert by_period[date(2023, 12, 31)] == pytest.approx(1.20)
    # 2024Q1：0.40 + 1.20 − 0.30
    assert by_period[date(2024, 3, 31)] == pytest.approx(1.30)
    # 2023Q3：0.90 + 1.10 − 0.80
    assert by_period[date(2023, 9, 30)] == pytest.approx(1.20)
    # 2022 年缺 2021 基期 → NaN（不外推）
    assert pd.isna(by_period[date(2022, 3, 31)])

    # basis 记录用了哪三期，便于追溯
    assert "2024-03-31+2023-12-31-2023-03-31" in str(basis[date(2024, 3, 31)])
    assert "annual" in str(basis[date(2023, 12, 31)])

    # 输出升序且列名固定
    assert list(ttm.columns) == ["report_date", "eps_ttm", "eps_ttm_basis"]
    assert list(ttm["report_date"]) == sorted(ttm["report_date"])


def test_eps_ttm_series_missing_prior_period_is_nan() -> None:
    """三期里缺任何一期 → NaN（宁缺勿错，不做线性外推）。"""
    fins = pd.DataFrame(
        {
            "report_date": [date(2023, 12, 31), date(2024, 3, 31)],
            "eps": [1.20, 0.40],  # 缺 2023-03-31 的同期基数
        }
    )
    ttm = eps_ttm_series(fins)
    by_period = dict(zip(ttm["report_date"], ttm["eps_ttm"]))
    assert by_period[date(2023, 12, 31)] == pytest.approx(1.20)
    assert pd.isna(by_period[date(2024, 3, 31)])

    # 空输入也有固定列
    empty = eps_ttm_series(pd.DataFrame())
    assert empty.empty
    assert list(empty.columns) == ["report_date", "eps_ttm", "eps_ttm_basis"]


# ============================================================
# ③ build_valuation_frame：未来函数防线（重点）
# ============================================================
def test_build_valuation_frame_blocks_future_report() -> None:
    """报告期 2024-03-31 + lag=90 → 2024-06-29 起才可用；2024-06-01 必须 NaN。

    这是整个模块最关键的测试：如果这条挂了，历史上每一天的 PE 都会用到"未来的财报"，
    A1/A4 回测会直接失真。
    """
    fins = _fins_from_db(
        {
            "report_date": date(2024, 3, 31),
            "eps_ttm": 2.0,
            "bps": 10.0,
            "payout_ratio": 30.0,
        }
    )
    prices = _prices(
        (date(2024, 6, 1), 100.0),   # 报告期 + 62 天：还不可知
        (date(2024, 6, 28), 100.0),  # + 89 天：还不可知
        (date(2024, 6, 29), 100.0),  # + 90 天：正好可知（allow_exact_matches）
        (date(2024, 7, 1), 100.0),
    )
    frame = build_valuation_frame(prices, fins)
    by_date = {row.trade_date: row for row in frame.itertuples(index=False)}

    # 反向断言：算一下 lag 的边界，防止偷偷改常量
    assert date(2024, 3, 31) + timedelta(days=REPORT_LAG_DAYS) == date(2024, 6, 29)

    assert pd.isna(by_date[date(2024, 6, 1)].pe_ttm)
    assert pd.isna(by_date[date(2024, 6, 28)].pe_ttm)
    assert by_date[date(2024, 6, 1)].report_date is None
    assert by_date[date(2024, 6, 28)].report_date is None

    assert by_date[date(2024, 6, 29)].pe_ttm == pytest.approx(50.0)  # 100 / 2.0
    assert by_date[date(2024, 7, 1)].pe_ttm == pytest.approx(50.0)
    assert by_date[date(2024, 7, 1)].report_date == date(2024, 3, 31)


def test_build_valuation_frame_uses_announced_date_when_present() -> None:
    """point-in-time 用 ``announced_date``：显式披露日晚于 report+lag 时以披露日为准。

    同时验证：``announced_date`` 为 NULL → 回落到 ``report_date + lag``；
    脏数据（披露日早于报告期）也不能让"未来财报"提前可用。
    """
    fins = _fins_from_db(
        # 8/30 才披露（远晚于 3/31+90=6/29）→ 8/29 之前不能有 PE
        {
            "report_date": date(2024, 3, 31),
            "announced_date": date(2024, 8, 30),
            "eps_ttm": 5.0, "bps": 20.0, "payout_ratio": 40.0,
        },
        # 无 announced_date → 回落到 2023-12-31 + 90 = 2024-03-30
        {
            "report_date": date(2023, 12, 31),
            "announced_date": None,
            "eps_ttm": 4.0, "bps": 18.0, "payout_ratio": None,
        },
    )
    prices = _prices(
        (date(2024, 3, 29), 100.0),
        (date(2024, 4, 1), 100.0),
        (date(2024, 8, 29), 100.0),
        (date(2024, 8, 30), 100.0),
    )
    frame = build_valuation_frame(prices, fins).set_index("trade_date")

    # 3/29：两个报告期都还不可知
    assert pd.isna(frame.loc[date(2024, 3, 29), "pe_ttm"])
    # 4/1：2023 年报（回落 3/30 可知）生效
    assert frame.loc[date(2024, 4, 1), "report_date"] == date(2023, 12, 31)
    assert frame.loc[date(2024, 4, 1), "pe_ttm"] == pytest.approx(25.0)  # 100 / 4
    # 8/29：2024 一季报还没披露（虽然 6/29 已过 report+lag）
    assert frame.loc[date(2024, 8, 29), "report_date"] == date(2023, 12, 31)
    # 8/30：一季报生效
    assert frame.loc[date(2024, 8, 30), "report_date"] == date(2024, 3, 31)
    assert frame.loc[date(2024, 8, 30), "pe_ttm"] == pytest.approx(20.0)  # 100 / 5
    assert frame.loc[date(2024, 8, 30), "dv_ttm"] == pytest.approx(2.0)


def test_build_valuation_frame_dirty_announced_date_falls_back() -> None:
    """announced_date 早于 report_date（脏数据）→ 回落到 report_date + lag，不提前可用。"""
    fins = _fins_from_db(
        {
            "report_date": date(2024, 3, 31),
            "announced_date": date(2024, 3, 1),  # 明显错误
            "eps_ttm": 2.0, "bps": 10.0, "payout_ratio": 30.0,
        }
    )
    prices = _prices((date(2024, 5, 1), 100.0), (date(2024, 7, 1), 100.0))
    frame = build_valuation_frame(prices, fins).set_index("trade_date")
    assert pd.isna(frame.loc[date(2024, 5, 1), "pe_ttm"])
    assert frame.loc[date(2024, 7, 1), "pe_ttm"] == pytest.approx(50.0)


def test_build_valuation_frame_picks_latest_known_period() -> None:
    """多个报告期：每个交易日取"已可知的最新一期"，而不是最新一期。"""
    fins = _fins_from_db(
        {"report_date": date(2023, 12, 31), "eps_ttm": 2.0, "bps": 10.0,
         "payout_ratio": 30.0},
        {"report_date": date(2024, 3, 31), "eps_ttm": 3.0, "bps": 12.0,
         "payout_ratio": 30.0},
    )
    prices = _prices(
        (date(2024, 3, 29), 120.0),  # 只有 2023 年报可用（2024-03-30 才可知）
        (date(2024, 4, 1), 120.0),   # 2023 年报
        (date(2024, 6, 28), 120.0),  # Q1 还不可知（6/29）
        (date(2024, 7, 1), 120.0),   # Q1 生效
    )
    frame = build_valuation_frame(prices, fins).set_index("trade_date")

    assert frame.loc[date(2024, 4, 1), "report_date"] == date(2023, 12, 31)
    assert frame.loc[date(2024, 4, 1), "pe_ttm"] == pytest.approx(60.0)   # 120 / 2
    assert frame.loc[date(2024, 6, 28), "report_date"] == date(2023, 12, 31)
    assert frame.loc[date(2024, 7, 1), "report_date"] == date(2024, 3, 31)
    assert frame.loc[date(2024, 7, 1), "pe_ttm"] == pytest.approx(40.0)   # 120 / 3


def test_build_valuation_frame_custom_lag() -> None:
    """lag_days 可调：lag=0 时报告期当天就可用（用于对账/别的披露口径）。"""
    fins = _fins_from_db(
        {"report_date": date(2024, 3, 31), "eps_ttm": 2.0, "bps": 10.0,
         "payout_ratio": 30.0}
    )
    prices = _prices((date(2024, 3, 31), 100.0))
    frame = build_valuation_frame(prices, fins, lag_days=0)
    assert frame.loc[0, "pe_ttm"] == pytest.approx(50.0)
    assert frame.loc[0, "report_date"] == date(2024, 3, 31)


# ============================================================
# ④ pb / dv_ttm / 负 PE 边界
# ============================================================
@pytest.mark.parametrize(
    ("eps_ttm", "bps", "payout_ratio", "close", "expect_pe", "expect_pb", "expect_dv"),
    [
        # 正常：100/5=20，100/20=5，40%*5/100*100=2.0
        (5.0, 20.0, 40.0, 100.0, 20.0, 5.0, 2.0),
        # 负 EPS：PE 允许负值（策略层过滤 PE<=0）；股息率为负 = 数学结果
        (-2.0, 20.0, 40.0, 100.0, -50.0, 5.0, -0.8),
        # BPS <= 0（资不抵债）→ PB = NaN
        (5.0, -1.0, 40.0, 100.0, 20.0, None, 2.0),
        (5.0, 0.0, 40.0, 100.0, 20.0, None, 2.0),
        # EPS_TTM = 0 → PE / 股息率都算不出来 → NaN
        (0.0, 20.0, 40.0, 100.0, None, 5.0, None),
        # payout 缺失 → 股息率 NaN
        (5.0, 20.0, None, 100.0, 20.0, 5.0, None),
    ],
)
def test_build_valuation_frame_edge_cases(
    eps_ttm: float,
    bps: float,
    payout_ratio: float | None,
    close: float,
    expect_pe: float | None,
    expect_pb: float | None,
    expect_dv: float | None,
) -> None:
    """pb / dv_ttm 的边界口径（bps<=0、eps=0、payout 缺失 → None；负 PE 保留）。"""
    fins = _fins_from_db(
        {
            "report_date": date(2023, 12, 31),
            "eps_ttm": eps_ttm,
            "bps": bps,
            "payout_ratio": payout_ratio,
        }
    )
    frame = build_valuation_frame(_prices((date(2024, 4, 1), close)), fins)
    row = frame.iloc[0]
    if expect_pe is None:
        assert pd.isna(row["pe_ttm"])
    else:
        assert row["pe_ttm"] == pytest.approx(expect_pe)
    if expect_pb is None:
        assert pd.isna(row["pb"])
    else:
        assert row["pb"] == pytest.approx(expect_pb)
    if expect_dv is None:
        assert pd.isna(row["dv_ttm"])
    else:
        assert row["dv_ttm"] == pytest.approx(expect_dv)


# ============================================================
# ③b 输出契约：列名 / 行序 / 空输入
# ============================================================
def test_build_valuation_frame_output_contract() -> None:
    """输出列固定为 5 列、行序与输入 prices 一致、空输入不炸。"""
    fins = _fins_from_db(
        # 2023-09-30 + 90 = 2023-12-29 → 2024 年 1 月起都可用
        {"report_date": date(2023, 9, 30), "eps_ttm": 2.0, "bps": 10.0,
         "payout_ratio": 30.0}
    )
    # 故意乱序输入（不同交易日），输出必须保持输入顺序
    prices = _prices(
        (date(2024, 3, 1), 100.0),
        (date(2024, 1, 2), 90.0),
        (date(2024, 2, 1), 95.0),
    )
    frame = build_valuation_frame(prices, fins)
    assert list(frame.columns) == ["trade_date", "pe_ttm", "pb", "dv_ttm", "report_date"]
    assert frame["trade_date"].tolist() == [
        date(2024, 3, 1), date(2024, 1, 2), date(2024, 2, 1),
    ]
    assert frame["pe_ttm"].tolist() == pytest.approx([50.0, 45.0, 47.5])

    # 空 prices / 空 fins
    empty = build_valuation_frame(pd.DataFrame(), fins)
    assert empty.empty
    assert list(empty.columns) == ["trade_date", "pe_ttm", "pb", "dv_ttm", "report_date"]

    no_fins = build_valuation_frame(prices, _fins_from_db())
    assert len(no_fins) == len(prices)
    assert no_fins["pe_ttm"].isna().all()
    assert no_fins["report_date"].isna().all()


def test_build_valuation_frame_accepts_normalized_fins_without_eps_ttm() -> None:
    """兼容 ``normalize_financials`` 的原始输出（没有 eps_ttm 列时现场推导 TTM）。"""
    fins = pd.DataFrame(
        {
            "report_date": [
                date(2022, 12, 31), date(2023, 3, 31), date(2023, 12, 31),
                date(2024, 3, 31),
            ],
            "eps": [1.10, 0.30, 1.20, 0.40],
            "bps": [10.0, 10.2, 10.8, 11.0],
            "payout_ratio": [30.0, 30.0, 30.0, 30.0],
        }
    )
    frame = build_valuation_frame(_prices((date(2024, 7, 1), 130.0)), fins)
    # 2024Q1 TTM = 0.40 + 1.20 − 0.30 = 1.30 → PE = 130 / 1.30
    assert frame.loc[0, "pe_ttm"] == pytest.approx(100.0)
    assert frame.loc[0, "report_date"] == date(2024, 3, 31)


# ============================================================
# ⑤b 巨潮分红：normalize_dividends / dv_ttm_series
# ============================================================
def test_normalize_dividends_per_10_to_per_share() -> None:
    """``派息比例`` 是每 10 股派 X 元：6.0 → dps=0.6；只送股/转增（派息=0）的行要过滤。"""
    raw = pd.DataFrame(
        {
            "实施方案公告日期": [date(2024, 5, 20), date(2024, 7, 10), date(2023, 5, 1)],
            "分红类型": ["年度分红", "中期分红", "年度分红"],
            "送股比例": [0, 0, 10],
            "转增比例": [0, 0, 0],
            "派息比例": [6.0, 10.0, 0.0],       # 第三行纯送股，没有现金
            "股权登记日": [date(2024, 6, 14), date(2024, 7, 14), date(2023, 5, 30)],
            "除权日": [date(2024, 6, 15), None, date(2023, 6, 1)],
            "派息日": [date(2024, 6, 16), date(2024, 7, 15), date(2023, 6, 2)],
            "报告时间": ["2023年报", "2024中报", "2022年报"],
        }
    )
    dividends = normalize_dividends(raw)

    assert list(dividends.columns) == ["ex_date", "announce_date", "dps", "report_period"]
    # 第一行 6.0/10=0.6；第二行除权日空 → 回落派息日 2024-07-15；第三行 dps=0 被过滤
    assert dividends["ex_date"].tolist() == [date(2024, 6, 15), date(2024, 7, 15)]
    assert dividends["dps"].tolist() == pytest.approx([0.6, 1.0])
    assert dividends["announce_date"].tolist() == [date(2024, 5, 20), date(2024, 7, 10)]
    assert dividends["report_period"].tolist() == ["2023年报", "2024中报"]

    # 空输入 / 缺列 → 同结构空表，不抛异常
    for raw_empty in (None, pd.DataFrame()):
        empty = normalize_dividends(raw_empty)
        assert empty.empty
        assert list(empty.columns) == ["ex_date", "announce_date", "dps", "report_period"]


def test_normalize_dividends_falls_back_to_announce_date() -> None:
    """除权日/派息日都缺失时退公告日（并记 warning），保证 still point-in-time 可用。"""
    raw = pd.DataFrame(
        {
            "实施方案公告日期": [date(2024, 4, 20)],
            "派息比例": [3.0],
            "除权日": [None],
            "派息日": [None],
        }
    )
    dividends = normalize_dividends(raw)
    assert dividends["ex_date"].tolist() == [date(2024, 4, 20)]
    assert dividends["dps"].tolist() == pytest.approx([0.3])


def test_dv_ttm_series_trailing_12m_vectorized() -> None:
    """近 12 个月现金分红 / 股价：3 笔分红（含 1 笔 400 天前应被排除）+ 2 个交易日。

    期望值手算：
        d1=2024-07-01 close=10：只有 2024-06-15 的 0.5 → 0.5/10*100 = 5.0
        d2=2024-08-01 close=20：2024-06-15 的 0.5 + 2024-07-15 的 1.0 = 1.5 → 7.5
        2023-06-01 的 0.9 距 d1 已 396 天（>365）→ 两个交易日都排除
        （2024-07-15 的分红在 d1 之后除权 → d1 不计入，验证 point-in-time）
    """
    prices = _prices((date(2024, 7, 1), 10.0), (date(2024, 8, 1), 20.0))
    dividends = pd.DataFrame(
        {
            "ex_date": [date(2024, 6, 15), date(2023, 6, 1), date(2024, 7, 15)],
            "dps": [0.5, 0.9, 1.0],
        }
    )
    frame = dv_ttm_series(prices, dividends)
    assert list(frame.columns) == ["trade_date", "dv_ttm"]
    assert frame["trade_date"].tolist() == [date(2024, 7, 1), date(2024, 8, 1)]
    assert frame["dv_ttm"].tolist() == pytest.approx([5.0, 7.5])


def test_dv_ttm_series_no_dividend_and_missing_price() -> None:
    """无分红 → 0.0（真实"无股息"）；无价格 → NaN；正好 365 天前的不计入。"""
    prices = _prices((date(2024, 7, 1), 10.0), (date(2024, 7, 2), float("nan")))

    no_dividend = dv_ttm_series(prices, pd.DataFrame())
    assert no_dividend["dv_ttm"].tolist()[0] == pytest.approx(0.0)
    assert pd.isna(no_dividend["dv_ttm"].tolist()[1])

    # 窗口是 (d-365, d]：ex_date 正好等于 d-365 不计入
    boundary = dv_ttm_series(
        _prices((date(2024, 7, 1), 10.0)),
        pd.DataFrame({"ex_date": [date(2023, 7, 2)], "dps": [0.5]}),
    )
    assert boundary["dv_ttm"].tolist()[0] == pytest.approx(0.0)

    # 除权日 = 交易日当天要计入（分红在除权日收盘后生效 → d >= ex_date）
    same_day = dv_ttm_series(
        _prices((date(2024, 7, 1), 10.0)),
        pd.DataFrame({"ex_date": [date(2024, 7, 1)], "dps": [0.5]}),
    )
    assert same_day["dv_ttm"].tolist()[0] == pytest.approx(5.0)

    # 空 prices → 空表（固定列）
    empty = dv_ttm_series(pd.DataFrame(), pd.DataFrame())
    assert empty.empty
    assert list(empty.columns) == ["trade_date", "dv_ttm"]


def test_dividend_rows_from_frame_precision() -> None:
    """阶段 1b 的行构造：dps 精度 Numeric(12,6)、ex_date 必须是 date、src 标记来源。"""
    frame = pd.DataFrame(
        {
            "ex_date": [date(2024, 6, 15), date(2024, 7, 15)],
            "announce_date": [date(2024, 5, 20), None],
            "dps": [0.6123456789, 0.0],   # 第二行 dps=0（脏值）应被丢弃
            "report_period": ["2023年报", None],
        }
    )
    rows = valuation._dividend_rows_from_frame("600519", frame)
    assert len(rows) == 1
    assert rows[0]["code"] == "600519"
    assert rows[0]["ex_date"] == date(2024, 6, 15)
    assert rows[0]["dps"] == pytest.approx(0.612346)       # Numeric(12,6)
    assert rows[0]["announce_date"] == date(2024, 5, 20)
    assert rows[0]["report_period"] == "2023年报"
    assert rows[0]["src"] == "cninfo"


# ============================================================
# ⑤ dim_stock_financial 行形态 / 阶段 1 落库行
# ============================================================
def test_financial_rows_from_frame_announced_date() -> None:
    """阶段 1 的行构造：announced_date = report_date + 90，精度按模型列定义。"""
    frame = pd.DataFrame(
        {
            "report_date": [date(2024, 3, 31), date(2024, 6, 30)],
            "eps": [0.41234567, None],
            "bps": [11.0, None],
            "roe": [3.51, None],
            "payout_ratio": [None, None],
            "eps_ttm": [1.31, None],
            "eps_ttm_basis": ["x", None],
            "revenue": [1.0e9, None],
        }
    )
    rows = valuation._financial_rows_from_frame("600519", frame)
    assert len(rows) == 1  # 第二行只有报告期、没有任何财务数 → 丢弃
    row = rows[0]
    assert row["code"] == "600519"
    assert row["report_date"] == date(2024, 3, 31)
    assert row["announced_date"] == date(2024, 3, 31) + timedelta(days=REPORT_LAG_DAYS)
    assert row["eps"] == pytest.approx(0.412346)   # Numeric(14,6)
    assert row["roe"] == pytest.approx(3.51)
    assert row["src"] == "sina"


def test_valuation_update_rows_nan_to_none_and_skip_all_null() -> None:
    """UPDATE 行：NaN→None、只更新三列、三列全空的行直接跳过。"""
    frame = pd.DataFrame(
        {
            "trade_date": [date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3)],
            "pe_ttm": [20.0, float("nan"), 30.0],
            "pb": [5.0, float("nan"), float("nan")],
            "dv_ttm": [float("nan"), float("nan"), float("nan")],
            "report_date": [date(2024, 3, 31)] * 3,
        }
    )
    rows = valuation._valuation_update_rows("600519", frame)
    assert [row["trade_date"] for row in rows] == [date(2024, 7, 1), date(2024, 7, 3)]
    assert rows[0]["pe_ttm"] == pytest.approx(20.0)
    assert rows[0]["dv_ttm"] is None           # NaN → None（写库写 NULL）
    assert rows[1]["pb"] is None
    assert set(rows[0]) == {"code", "trade_date", "pe_ttm", "pb", "dv_ttm"}


def test_valuation_update_statement_only_touches_three_columns() -> None:
    """UPDATE 语句只 SET 估值三列，绝不 INSERT、不覆盖 close/volume/amount。"""
    from sqlalchemy.dialects import postgresql

    statement = valuation._valuation_update_statement(
        [
            {"code": "600519", "trade_date": date(2024, 7, 1),
             "pe_ttm": 20.5, "pb": None, "dv_ttm": 2.0},
        ]
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).upper()
    assert sql.startswith("UPDATE FACT_STOCK_DAILY")
    assert "SET PE_TTM" in sql and "PB=" in sql.replace(" ", "") and "DV_TTM" in sql
    assert "INSERT" not in sql
    # 不能覆盖行情列（否则一次估值回补就把价格/成交额洗成 NULL）
    for column in ("CLOSE", "VOLUME", "AMOUNT", "OPEN", "HIGH", "LOW", "ADJ_FACTOR"):
        assert f"{column}=" not in sql.replace(" ", "")
    # onupdate 被显式压掉（自引用 = 保持原值），只更新那三列
    assert "UPDATED_AT=FACT_STOCK_DAILY.UPDATED_AT" in sql.replace(" ", "")


def test_latest_knowable_report_period() -> None:
    """only_missing 的判据：今天最多能知道到哪个报告期（报告期 + 90 天）。"""
    assert latest_knowable_report_period(date(2026, 10, 22)) == date(2026, 6, 30)
    assert latest_knowable_report_period(date(2026, 1, 5)) == date(2025, 9, 30)
    assert latest_knowable_report_period(date(2026, 4, 1)) == date(2025, 12, 31)
    # lag=0 时就是"最近一个已结束的季度末"
    assert latest_knowable_report_period(date(2026, 10, 22), lag_days=0) == date(
        2026, 9, 30
    )


# ============================================================
# ⑥ 阶段 2 零网络（重点）
# ============================================================
class _PoisonedAkshare:
    """任何属性访问都抛异常：用来证明 compute_valuation 完全不碰 akshare。"""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - 触发即失败
        raise AssertionError(f"compute_valuation 不该访问 akshare.{name}（阶段 2 必须零网络）")


async def _poisoned_async(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - 触发即失败
    raise AssertionError("compute_valuation 不该发起网络请求（阶段 2 必须零网络）")


def test_compute_valuation_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """阶段 2 零网络：把 akshare / fetch_financials / _ak_call 全部换成会炸的桩，仍然成功。

    这是「财报落库缓存」设计的关键保障：每日增量只做数据库计算，不再逐只打新浪接口。
    DB 读写也用内存桩替换（本测试不连库），最后断言更新行数/列集合。
    """
    financials = _fins_from_db(
        # 报告期 2024-03-31、没有 announced_date → 回落 2024-06-29 可用
        {"code": "000001", "report_date": date(2024, 3, 31), "eps_ttm": 1.0,
         "bps": 5.0, "payout_ratio": 20.0},
        {"code": "600519", "report_date": date(2024, 3, 31), "eps_ttm": 5.0,
         "bps": 20.0, "payout_ratio": 40.0},
    )
    dividends = pd.DataFrame(
        {
            "code": ["000001", "600519"],
            "ex_date": [date(2024, 6, 15), date(2024, 6, 15)],
            "dps": [0.5, 3.0],
        }
    )
    price_rows = pd.DataFrame(
        {
            "code": ["000001", "000001", "000001", "600519", "600519"],
            "trade_date": [
                date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3),
                date(2024, 7, 1), date(2024, 7, 2),
            ],
            "close": [10.0, 11.0, 12.0, 100.0, 100.0],
        }
    )
    recorded: dict[str, Any] = {"rows": [], "quality": []}

    async def fake_codes(db: Any, start: date, end: date, codes: Any = None) -> list[str]:
        return ["000001", "600519"]

    async def fake_load_financials(db: Any, codes: Any) -> pd.DataFrame:
        wanted = set(codes)
        return financials[financials["code"].isin(wanted)]

    async def fake_load_dividends(db: Any, codes: Any) -> pd.DataFrame:
        wanted = set(codes)
        return dividends[dividends["code"].isin(wanted)]

    async def fake_load_prices(db: Any, codes: Any, start: date, end: date) -> pd.DataFrame:
        wanted = set(codes)
        return price_rows[price_rows["code"].isin(wanted)]

    async def fake_bulk_update(db: Any, rows: Any) -> int:
        recorded["rows"].extend(rows)
        return len(rows)

    async def fake_record_quality(db: Any, report: Any, trade_date: Any, status: str) -> None:
        recorded["quality"].append((report, trade_date, status))

    monkeypatch.setattr(valuation, "_codes_with_prices", fake_codes)
    monkeypatch.setattr(valuation, "_load_financials_for_codes", fake_load_financials)
    monkeypatch.setattr(valuation, "_load_dividends_for_codes", fake_load_dividends)
    monkeypatch.setattr(valuation, "_load_price_rows", fake_load_prices)
    monkeypatch.setattr(valuation, "_bulk_update_valuation", fake_bulk_update)
    monkeypatch.setattr(valuation, "_record_quality", fake_record_quality)
    # 联网三件套全部毒化：一旦被调用立刻 AssertionError
    monkeypatch.setattr(valuation, "ak", _PoisonedAkshare())
    monkeypatch.setattr(valuation, "_ak_call", _poisoned_async)
    monkeypatch.setattr(valuation, "fetch_financials", _poisoned_async)

    result = asyncio.run(
        compute_valuation(date(2024, 7, 1), date(2024, 7, 3), db=None)
    )

    assert result.rows == 5          # 3 天 × 000001 + 2 天 × 600519
    assert result.total == 2         # 处理 2 只股票
    assert result.failed == 0
    assert len(recorded["rows"]) == 5
    for row in recorded["rows"]:
        assert set(row) == {"code", "trade_date", "pe_ttm", "pb", "dv_ttm"}
        assert row["pe_ttm"] is not None and row["pe_ttm"] > 0
    # dv_ttm 来自分红明细（0.5/10*100=5.0），证明阶段 2 用的是 dv_ttm_series 而不是 payout
    assert recorded["rows"][0]["dv_ttm"] == pytest.approx(5.0)
    assert recorded["quality"], "阶段 2 必须写 meta_data_quality(dataset='valuation')"
    report, _, status = recorded["quality"][0]
    assert report.dataset == "valuation"
    assert status == "ok"


def test_compute_valuation_skips_when_no_price_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """区间内没有日线 → skipped（不写质量记录、不炸）。"""
    async def fake_codes(db: Any, start: date, end: date, codes: Any = None) -> list[str]:
        return []

    async def boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - 触发即失败
        raise AssertionError("没有行情数据时不应发起任何网络/写库操作")

    monkeypatch.setattr(valuation, "_codes_with_prices", fake_codes)
    monkeypatch.setattr(valuation, "_load_financials_for_codes", boom)
    monkeypatch.setattr(valuation, "_load_dividends_for_codes", boom)
    monkeypatch.setattr(valuation, "_bulk_update_valuation", boom)
    monkeypatch.setattr(valuation, "_ak_call", _poisoned_async)

    result = asyncio.run(compute_valuation(date(2024, 7, 1), db=None))
    assert result.rows == 0
    assert result.total == 0
    assert result.is_skipped
    assert result.detail["skipped"] == "no_price_rows"


# ============================================================
# ⑦ 阶段 1 的离线降级
# ============================================================
def test_fetch_financials_returns_empty_without_akshare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """akshare 不可用（未安装/离线）→ 返回空表 + 记日志，绝不抛异常。"""
    monkeypatch.setattr(valuation, "ak", None)
    frame = asyncio.run(fetch_financials("600519"))
    assert frame.empty
    assert list(frame.columns) == list(valuation.FIN_COLUMNS) + [
        "eps_ttm", "eps_ttm_basis", "revenue",
    ]


def test_ak_call_returns_none_when_function_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """akshare 版本差异（函数被移除，如 stock_a_indicator_lg）→ 返回 None，不抛。"""
    class _EmptyAkshare:
        pass

    monkeypatch.setattr(valuation, "ak", _EmptyAkshare())
    assert asyncio.run(valuation._ak_call("stock_a_indicator_lg", symbol="600519")) is None


def test_normalize_codes_and_sina_symbol() -> None:
    """代码归一化 + 新浪交易所前缀（抓兜底股本时要用）。"""
    assert valuation._normalize_codes(["sh600519", "1", "600519", None, ""]) == [
        "600519", "000001",
    ]
    assert valuation._sina_symbol("600519") == "sh600519"
    assert valuation._sina_symbol("000001") == "sz000001"
    assert valuation._sina_symbol("830799") == "bj830799"
