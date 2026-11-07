"""
采集层测试（方案 F4：采集幂等 / 列名兼容 / 单位换算 / 质量校验）
=================================================================
**不联网**：AKShare 全部用构造的 DataFrame 替代，Provider 降级链用 FakeProvider。

覆盖点（对应任务验收项）：
    ① 重复 upsert 不产生重复行 —— 纯函数去重 + （可选）真实 DB 幂等用例；
    ② ``_pick`` 列名兼容（缺列/NaN/"--" 都不 KeyError）；
    ③ 成交量 "手" → "股" ×100；
    ④ 质量校验能发现越界 pct_chg、重复行、缺日、OHLC 非法；
    ⑤ ``adj_factor = 后复权收盘 / 不复权收盘``（raw 无效日跳过）。

DB 用例需要显式设置 ``GFNA_TEST_DB`` 环境变量（指向可连的 PostgreSQL 测试库），
默认跳过 —— 采集单测不应该依赖数据库。
"""
from __future__ import annotations

import asyncio
import math
import os
from datetime import date, timedelta

import pytest

pd = pytest.importorskip("pandas")

from app.core.errors import ProviderError
from app.ingest.akshare_provider import (  # noqa: E402
    INDEX_COLUMNS,
    SHARES_PER_LOT,
    AkshareProvider,
    _margin_effective_date,
    _margin_map_sse,
    _margin_map_szse,
    _normalize_calendar,
    _normalize_index,
    _normalize_moneyflow,
    _normalize_sector_list,
    _normalize_sector_members,
    _normalize_spot,
    _normalize_stock_history,
    _normalize_tencent_index,
    _num,
    _pick,
)
from app.ingest.base import (  # noqa: E402
    BaseProvider,
    FetchResult,
    ProviderChain,
    RateLimiter,
    dedupe_rows,
    retry_async,
)
from app.ingest.quality import check_daily_frame, check_snapshot  # noqa: E402

REQUIRE_DB = bool(os.getenv("GFNA_TEST_DB"))


# ============================================================
# ② 列名兼容（_pick / 数值清洗）
# ============================================================
def test_pick_column_alias() -> None:
    """候选列名逐个尝试：命中第一个可用列，缺失列返回默认值，绝不 KeyError。"""
    row = {"涨跌幅": 1.23, "最新价": None, "市盈率-动态": "--", "市净率": float("nan")}
    assert _pick(row, ("涨跌幅",)) == 1.23
    # 列存在但值为 None / "--" / NaN 时继续尝试下一个候选
    assert _pick(row, ("最新价", "涨跌幅")) == 1.23
    assert _pick(row, ("市盈率-动态", "市净率")) is None
    assert _pick(row, ("不存在的列",), default="fallback") == "fallback"
    # 完全空的 dict 也不抛异常
    assert _pick({}, ("代码", "名称")) is None


def test_num_parsing() -> None:
    """"--"/""/带逗号/带百分号的数值都要安全处理。"""
    from app.ingest.akshare_provider import _num

    assert _num("--") is None
    assert _num("") is None
    assert _num("1,234.5") == 1234.5
    assert _num("3.5%") == 3.5
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None


def test_normalize_spot_missing_columns_no_keyerror() -> None:
    """快照缺列（只有代码/名称/最新价）时其余字段给 None，不 KeyError。"""
    frame = pd.DataFrame(
        [{"代码": "600519", "名称": "贵州茅台", "最新价": 1500.0}]
    )
    normalized = _normalize_spot(frame)
    row = normalized.to_dict("records")[0]
    assert row["code"] == "600519"
    assert row["close"] == 1500.0
    assert row["volume"] is None
    assert row["market_cap"] is None


# ============================================================
# ③ 成交量单位：手 → 股（×100）
# ============================================================
def test_spot_volume_lots_to_shares() -> None:
    """东财快照成交量单位是手，必须 ×100 转股（硬约束 3）。"""
    frame = pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "最新价": 10.0,
                "昨收": 9.9,
                "成交量": 1234,  # 手
                "成交额": 1234000.0,
            }
        ]
    )
    row = _normalize_spot(frame).to_dict("records")[0]
    assert row["volume"] == 1234 * SHARES_PER_LOT == 123400
    assert row["amount"] == 1234000.0


def test_sina_spot_volume_already_shares() -> None:
    """新浪快照成交量单位已是股（实测 amount/(volume*price)≈1），不能再 ×100。"""
    frame = pd.DataFrame(
        [
            {
                "代码": "sh600519",
                "名称": "贵州茅台",
                "最新价": 1309.69,
                "昨收": 1317.5,
                "成交量": 3_351_416,  # 股
                "成交额": 4_403_823_069.64,
            }
        ]
    )
    row = _normalize_spot(frame, volume_in_lots=False).to_dict("records")[0]
    assert row["code"] == "600519"
    assert row["volume"] == 3_351_416
    assert row["amount"] / (row["volume"] * row["close"]) == pytest.approx(1.0, rel=0.01)


def test_history_volume_lots_to_shares() -> None:
    """历史日线成交量同样是手，×100 转股。"""
    raw = pd.DataFrame(
        [
            {
                "日期": "2026-10-20",
                "开盘": 10.0,
                "收盘": 10.5,
                "最高": 10.6,
                "最低": 9.9,
                "成交量": 888,
                "成交额": 932400.0,
                "振幅": 7.0,
                "涨跌幅": 5.0,
                "换手率": 1.2,
            }
        ]
    )
    row = _normalize_stock_history(raw, None).to_dict("records")[0]
    assert row["volume"] == 88800
    # 有涨跌幅时反推 pre_close = close / (1 + pct/100)
    assert row["pre_close"] == pytest.approx(10.0, abs=1e-3)


# ============================================================
# ⑤ adj_factor = 后复权收盘 / 不复权收盘
# ============================================================
def _history_frame(close: float, volume: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": "2026-10-20",
                "开盘": close,
                "收盘": close,
                "最高": close,
                "最低": close,
                "成交量": volume,
                "成交额": close * volume * 100,
                "振幅": 0.0,
                "涨跌幅": 0.0,
                "换手率": 0.5,
            }
        ]
    )


def test_adj_factor_equals_hfq_over_raw() -> None:
    raw = _history_frame(close=10.0)
    hfq = _history_frame(close=20.0)
    row = _normalize_stock_history(raw, hfq).to_dict("records")[0]
    assert row["adj_factor"] == pytest.approx(2.0)


def test_adj_factor_skips_invalid_raw_close() -> None:
    """raw 收盘价为 0/空 → 跳过该日（不写脏行）。"""
    raw = pd.DataFrame(
        [
            {"日期": "2026-10-19", "收盘": 0.0, "成交量": 10},
            {"日期": "2026-10-20", "收盘": 10.0, "成交量": 10},
        ]
    )
    hfq = pd.DataFrame(
        [
            {"日期": "2026-10-19", "收盘": 0.0},
            {"日期": "2026-10-20", "收盘": 12.0},
        ]
    )
    frame = _normalize_stock_history(raw, hfq)
    assert len(frame) == 1
    assert frame.to_dict("records")[0]["trade_date"] == date(2026, 10, 20)
    assert frame.to_dict("records")[0]["adj_factor"] == pytest.approx(1.2)


def test_adj_factor_none_when_hfq_missing() -> None:
    """后复权缺失时 adj_factor 为 None（保留该日行情），不编造 1.0。"""
    raw = _history_frame(close=10.0)
    frame = _normalize_stock_history(raw, None)
    assert frame.to_dict("records")[0]["adj_factor"] is None


def test_history_empty_input() -> None:
    assert _normalize_stock_history(None, None).empty


# ============================================================
# ④ 质量校验
# ============================================================
def _snapshot_frame(rows: int = 4000) -> pd.DataFrame:
    """构造一份"基本健康"的快照（代码唯一、收盘价/昨收正常、成交量>0）。"""
    records = []
    for index in range(rows):
        code = f"{index:06d}"
        records.append(
            {
                "code": code,
                "name": f"股票{index}",
                "close": 10.0,
                "pre_close": 10.1,
                "pct_chg": -0.99,
                "volume": 100_000,
                "amount": 1_000_000.0,
            }
        )
    return pd.DataFrame(records)


def test_quality_snapshot_detects_duplicates_and_pct_out_of_range() -> None:
    frame = _snapshot_frame(4000)
    # 越界涨跌幅（首日上市等真实场景，记 warning）
    frame.loc[0, "pct_chg"] = 99.9
    # 重复代码（error，会破坏主键/统计口径）
    frame.loc[1, "code"] = frame.loc[0, "code"]
    report = check_snapshot(frame, expect_rows=4000)
    types = {item["type"] for item in report.anomalies}
    assert "pct_chg_out_of_range" in types
    assert "duplicate_rows" in types
    assert report.ok is False  # 重复行是 error


def test_quality_snapshot_blocks_small_frame() -> None:
    frame = _snapshot_frame(50)
    report = check_snapshot(frame, expect_rows=5000)
    types = {item["type"] for item in report.anomalies}
    assert "row_count_below_threshold" in types
    assert "coverage_below_threshold" in types
    assert report.ok is False


def test_quality_snapshot_empty() -> None:
    report = check_snapshot(None, expect_rows=5000)
    assert report.ok is False
    assert report.anomalies[0]["type"] == "empty_frame"


def _suspended_frame(rows: int) -> pd.DataFrame:
    """构造停牌/退市整理行：有昨收，但最新价/成交量/成交额全空。"""
    return pd.DataFrame(
        {
            "code": [f"9{index:05d}" for index in range(rows)],
            "name": [f"退市{index}" for index in range(rows)],
            "close": [None] * rows,
            "pre_close": [1.0] * rows,
            "pct_chg": [None] * rows,
            "volume": [None] * rows,
            "amount": [None] * rows,
        }
    )


def test_quality_snapshot_ignores_suspended_rows() -> None:
    """停牌/退市行不计入覆盖率与缺失率（东财榜单会混入 ~350 只）。"""
    frame = pd.concat(
        [_snapshot_frame(4000), _suspended_frame(300)], ignore_index=True
    )
    report = check_snapshot(frame, expect_rows=4000)
    assert report.ok is True
    assert report.rows == 4000
    assert report.coverage == 1.0
    assert any(item["type"] == "suspended_rows" for item in report.anomalies)


def test_quality_snapshot_blocks_missing_close_on_traded_rows() -> None:
    """有成交却缺收盘价仍要拦截：区别于停牌，属于数据源故障。"""
    broken = _snapshot_frame(300)
    broken["code"] = [f"8{index:05d}" for index in range(300)]
    broken["close"] = None
    frame = pd.concat([_snapshot_frame(4000), broken], ignore_index=True)
    report = check_snapshot(frame, expect_rows=4000)
    types = {item["type"] for item in report.anomalies}
    assert "missing_ratio_exceeded" in types
    assert report.ok is False


def test_snapshot_rows_skip_suspended_stock() -> None:
    """停牌/退市行不写入当日行情，与历史回补口径一致。"""
    from app.ingest.service import _snapshot_rows

    frame = pd.DataFrame(
        [
            {
                "code": "600519", "name": "贵州茅台", "close": 1500.0,
                "pre_close": 1490.0, "volume": 1000, "amount": 1_500_000.0,
            },
            {
                "code": "920680", "name": "广道退", "close": None,
                "pre_close": 0.86, "volume": None, "amount": None,
            },
        ]
    )
    rows = _snapshot_rows(frame, date(2026, 11, 4))
    assert [row["code"] for row in rows] == ["600519"]


def _daily_frame(dates: list[date]) -> pd.DataFrame:
    records = []
    price = 10.0
    for index, day in enumerate(dates):
        records.append(
            {
                "trade_date": day,
                "open": price,
                "high": price + 0.3,
                "low": price - 0.3,
                "close": price + 0.1,
                "pre_close": price,
                "pct_chg": 1.0,
                "amplitude": 6.0,
                "volume": 100_000 + index,
                "amount": 1_000_000.0,
            }
        )
        price += 0.1
    return pd.DataFrame(records)


def test_quality_daily_frame_detects_missing_days_and_ohlc() -> None:
    dates = [date(2026, 10, 12) + timedelta(days=i) for i in range(5)]
    frame = _daily_frame(dates)
    # 人为制造 OHLC 非法：最低价高于最高价
    frame.loc[2, "low"] = frame.loc[2, "high"] + 1
    # 人为删掉中间一天，制造"缺日"
    frame = frame.drop(index=1).reset_index(drop=True)
    report = check_daily_frame(frame, dates, code="600519")
    types = {item["type"] for item in report.anomalies}
    assert "ohlc_violation" in types
    assert "missing_trade_days" in types
    assert report.ok is False


def test_quality_daily_frame_ok() -> None:
    dates = [date(2026, 10, 12) + timedelta(days=i) for i in range(5)]
    report = check_daily_frame(_daily_frame(dates), dates, code="600519")
    assert report.ok is True
    assert report.coverage == pytest.approx(1.0)
    assert not report.anomalies


# ============================================================
# 单位：两融（上交所元 → 亿元；深交所已是亿元）
# ============================================================
def test_margin_sse_yuan_to_yi() -> None:
    frame = pd.DataFrame(
        [{"信用交易日期": "2026-10-20", "融资余额": 1.0e10, "融券余量金额": 5.0e8}]
    )
    result = _margin_map_sse(frame)
    item = result[date(2026, 10, 20)]
    assert item["margin_balance"] == pytest.approx(100.0)  # 1e10 元 = 100 亿
    assert item["short_balance"] == pytest.approx(5.0)


def test_margin_szse_already_yi_and_uses_default_date() -> None:
    """深交所返回值没有日期列，用请求日兜底；金额已是亿元不换算。"""
    frame = pd.DataFrame([{"融资余额": 6000.5, "融券余额": 12.3}])
    result = _margin_map_szse(frame, default_date=date(2026, 10, 20))
    item = result[date(2026, 10, 20)]
    assert item["margin_balance"] == pytest.approx(6000.5)
    assert item["short_balance"] == pytest.approx(12.3)


def test_margin_effective_date_picks_latest_within_lookback() -> None:
    sse_map = {
        date(2026, 10, 19): {"margin_balance": 1.0},
        date(2026, 10, 20): {"margin_balance": 2.0},
    }
    assert _margin_effective_date(sse_map, date(2026, 10, 21)) == date(2026, 10, 20)


def test_margin_effective_date_rejects_stale_history() -> None:
    """akshare 无参 SSE 默认区间截止 2023-09-22，不能当最新披露日。"""
    sse_map = {date(2023, 9, 22): {"margin_balance": 1.0}}
    assert _margin_effective_date(sse_map, date(2026, 10, 30)) is None


def test_margin_effective_date_ignores_future_rows() -> None:
    sse_map = {date(2026, 10, 31): {"margin_balance": 1.0}}
    assert _margin_effective_date(sse_map, date(2026, 10, 30)) is None


def test_fetch_margin_never_calls_endpoints_without_date(monkeypatch) -> None:
    """网络失败时不得走无日期参数兜底（会把 2023/2024 数据标成请求日）。"""
    provider = AkshareProvider()
    calls: list[tuple[str, dict]] = []

    def fake_fetch(frame, what, **kwargs):
        calls.append((what, kwargs))
        raise ProviderError("boom")

    monkeypatch.setattr(provider, "_fetch_one", fake_fetch)
    with pytest.raises(ProviderError, match="上交所"):
        provider.fetch_margin(date(2026, 10, 30))

    sse_calls = [kwargs for what, kwargs in calls if what == "stock_margin_sse"]
    szse_calls = [kwargs for what, kwargs in calls if what == "stock_margin_szse"]
    assert sse_calls and all({"start_date", "end_date"} <= set(kw) for kw in sse_calls)
    assert szse_calls and all("date" in kw for kw in szse_calls)


def test_fetch_margin_szse_failure_raises_instead_of_partial_row(monkeypatch) -> None:
    """库里存沪深合计；只有上交所数据时不能写半份余额。"""
    provider = AkshareProvider()
    sse_frame = pd.DataFrame(
        [{"信用交易日期": "2026-10-30", "融资余额": 1.0e12, "融券余量金额": 2.0e10}]
    )

    def fake_fetch(frame, what, **kwargs):
        if what == "stock_margin_sse":
            return sse_frame
        raise ProviderError("szse down")

    monkeypatch.setattr(provider, "_fetch_one", fake_fetch)
    with pytest.raises(ProviderError, match="深交所"):
        provider.fetch_margin(date(2026, 10, 30))


# ============================================================
# 其它归一化：指数 / 日历 / 板块 / 资金流
# ============================================================
def test_normalize_index_pre_close_and_pct() -> None:
    frame = pd.DataFrame(
        [
            {"date": date(2026, 10, 19), "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 10},
            {"date": date(2026, 10, 20), "open": 100.0, "high": 103.0,
             "low": 99.5, "close": 102.0, "volume": 20},
        ]
    )
    normalized = _normalize_index(frame, "sh000001", "上证指数")
    second = normalized.to_dict("records")[1]
    assert second["index_code"] == "sh000001"
    assert second["pre_close"] == pytest.approx(100.0)
    assert second["pct_chg"] == pytest.approx(2.0)
    # 新浪指数成交量单位已是股（实测与 baostock 股口径一致），不做 ×100
    assert second["volume"] == 20


def test_normalize_tencent_index_units_and_target_row() -> None:
    """腾讯指数兜底：成交量手→股（×100），前收/涨跌幅来自前一条记录。"""
    rows = [
        ["2026-11-03", "3624.580", "3608.100", "3624.580", "3580.380", "536848407.000"],
        ["2026-11-04", "3605.390", "3686.170", "3686.170", "3590.800", "599394361.000"],
    ]
    frame = _normalize_tencent_index(rows, "sh000001", "上证指数", date(2026, 11, 4))
    assert list(frame.columns) == list(INDEX_COLUMNS)
    assert len(frame) == 1
    row = frame.to_dict("records")[0]
    assert row["pre_close"] == pytest.approx(3608.100)
    assert row["pct_chg"] == pytest.approx(round((3686.170 / 3608.100 - 1) * 100, 3))
    assert row["volume"] == 599394361 * SHARES_PER_LOT
    assert row["amount"] is None
    # 目标日不在返回区间时不落行（不写错日期）
    assert _normalize_tencent_index(rows, "sh000001", "上证指数", date(2026, 11, 5)).empty


def test_fetch_index_daily_falls_back_to_tencent(monkeypatch) -> None:
    """新浪单指数失败时切腾讯；只有一个指数两者都失败才计 failed。"""
    from app.core import constants as consts

    monkeypatch.setattr(consts, "INDEX_MAP", {
        "sh000001": ("上证指数", True),
        "sz399001": ("深证成指", True),
        "sh000688": ("科创50", True),
    })
    provider = AkshareProvider()
    tencent_calls: list[str] = []

    def fake_sina(self, index_code, index_name, trade_date):
        if index_code == "sh000001":
            raise ProviderError("sina dns failed")
        if index_code == "sh000688":
            return pd.DataFrame(columns=list(INDEX_COLUMNS))  # 当日无行
        return pd.DataFrame([{
            "index_code": index_code, "index_name": index_name,
            "trade_date": trade_date, "open": 1.0, "high": 1.0, "low": 1.0,
            "close": 1.0, "pre_close": 1.0, "pct_chg": 0.0,
            "volume": 1, "amount": None,
        }], columns=list(INDEX_COLUMNS))

    def fake_tencent(self, index_code, index_name, trade_date):
        tencent_calls.append(index_code)
        if index_code == "sh000688":
            raise ProviderError("tencent blocked")
        return pd.DataFrame([{
            "index_code": index_code, "index_name": index_name,
            "trade_date": trade_date, "open": 2.0, "high": 2.0, "low": 2.0,
            "close": 2.0, "pre_close": 2.0, "pct_chg": 0.0,
            "volume": 2, "amount": None,
        }], columns=list(INDEX_COLUMNS))

    monkeypatch.setattr(AkshareProvider, "_fetch_sina_index", fake_sina)
    monkeypatch.setattr(AkshareProvider, "_fetch_tencent_index", fake_tencent)

    result = provider.fetch_index_daily(date(2026, 11, 4))
    assert set(tencent_calls) == {"sh000001", "sh000688"}
    assert result.rows == 2
    assert result.failed == 1
    assert result.detail["index_sources"] == {
        "sh000001": "tencent", "sz399001": "sina",
    }


def test_normalize_calendar_dedup_sorted() -> None:
    frame = pd.DataFrame(
        {"trade_date": ["2026-10-20", "2026-10-19", "2026-10-20"]}
    )
    normalized = _normalize_calendar(frame)
    assert [str(value) for value in normalized["trade_date"]] == [
        "2026-10-19",
        "2026-10-20",
    ]


def test_normalize_sector_list_does_not_fake_amount() -> None:
    """板块列表没有成交额，amount/volume 必须为 None（不能拿总市值冒充，历史 bug #18）。"""
    industry = pd.DataFrame(
        [
            {
                "板块代码": "BK0475",
                "板块名称": "银行",
                "最新价": 1000.0,
                "涨跌幅": 2.0,
                "换手率": 1.0,
                "上涨家数": 40,
                "下跌家数": 2,
                "领涨股票": "平安银行",
                "领涨股票-涨跌幅": 5.5,
                "总市值": 8.0e12,
            }
        ]
    )
    frame = _normalize_sector_list(industry, None)
    row = frame.to_dict("records")[0]
    assert row["sector_code"] == "BK0475"
    assert row["sector_type"] == "industry"
    assert row["amount"] is None
    assert row["volume"] is None
    assert row["up_count"] == 40
    assert row["pre_close"] == pytest.approx(980.392, rel=1e-3)


def test_normalize_sector_members_dedup() -> None:
    frame = pd.DataFrame(
        [
            {"代码": "600519", "名称": "贵州茅台"},
            {"代码": "600519", "名称": "贵州茅台"},
            {"代码": "000001", "名称": "平安银行"},
        ]
    )
    normalized = _normalize_sector_members(frame, "BK0475", "银行", "industry")
    assert len(normalized) == 2
    assert set(normalized["stock_code"]) == {"600519", "000001"}


def test_normalize_moneyflow_merge() -> None:
    today = pd.DataFrame(
        [{"代码": "600519", "今日主力净流入-净额": 1.0e8}]
    )
    five = pd.DataFrame(
        [{"代码": "600519", "5日主力净流入-净额": -3.0e8}]
    )
    row = _normalize_moneyflow(today, five).to_dict("records")[0]
    assert row["code"] == "600519"
    assert row["main_net_t0"] == pytest.approx(1.0e8)
    assert row["main_net_5d"] == pytest.approx(-3.0e8)


# ============================================================
# ① 幂等：去重纯函数 + 真实 DB（可选）
# ============================================================
def test_dedupe_rows_last_wins() -> None:
    """同键多行只保留最后一行（否则 PG ON CONFLICT 同语句会报错）。"""
    rows = [
        {"code": "600519", "trade_date": date(2026, 10, 20), "close": 1.0},
        {"code": "600519", "trade_date": date(2026, 10, 20), "close": 2.0},
        {"code": "000001", "trade_date": date(2026, 10, 20), "close": 3.0},
    ]
    deduped = dedupe_rows(rows, ["code", "trade_date"])
    assert len(deduped) == 2
    assert next(r for r in deduped if r["code"] == "600519")["close"] == 2.0


@pytest.mark.skipif(not REQUIRE_DB, reason="需要 GFNA_TEST_DB 指向可用的 PostgreSQL 测试库")
def test_upsert_is_idempotent_db() -> None:
    """同一批数据 upsert 两次，行数不变（PG 方言 ON CONFLICT DO UPDATE）。"""

    async def _run() -> None:
        from sqlalchemy import delete, func, select

        from app.db.models.meta import TradeCalendar
        from app.db.session import AsyncSessionLocal, dispose_engines
        from app.ingest.base import upsert_rows

        test_date = date(2100, 1, 1)
        rows = [{"cal_date": test_date, "is_open": True, "source": "pytest"}]
        try:
            async with AsyncSessionLocal() as session:
                await upsert_rows(session, TradeCalendar, rows, ["cal_date"])
                await session.commit()
                await upsert_rows(session, TradeCalendar, rows, ["cal_date"])
                await session.commit()
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(TradeCalendar)
                        .where(TradeCalendar.cal_date == test_date)
                    )
                ).scalar_one()
                assert count == 1
                await session.execute(
                    delete(TradeCalendar).where(TradeCalendar.cal_date == test_date)
                )
                await session.commit()
        finally:
            await dispose_engines()

    asyncio.run(_run())


# ============================================================
# 限频 / 重试 / 降级链
# ============================================================
def test_rate_limiter_spacing() -> None:
    """两次 acquire 之间至少间隔 min_interval（异步限频器）。"""

    async def _run() -> None:
        limiter = RateLimiter(0.05, name="test")
        await limiter.acquire()
        started = asyncio.get_running_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed >= 0.04  # 容忍调度误差
        assert limiter.try_acquire() is False  # 刚用过令牌，立即再取应失败

    asyncio.run(_run())


def test_retry_async_retries_then_succeeds() -> None:
    """同步 callable 抛网络异常时重试，最终成功。"""
    attempts = {"count": 0}

    def flaky() -> int:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("模拟网络错误")
        return 42

    async def _run() -> int:
        return await retry_async(
            flaky, retries=3, base_delay=0.001, jitter=0.0
        )

    assert asyncio.run(_run()) == 42
    assert attempts["count"] == 3


class _FakeProvider(BaseProvider):
    """降级链测试用 Provider（不联网）。"""

    def __init__(self, name: str, *, mode: str = "ok", rows: int = 3) -> None:
        self.name = name
        self.mode = mode
        self.rows = rows
        self.calls = 0

    def fetch_trade_calendar(self, start: date, end: date) -> FetchResult:
        self.calls += 1
        if self.mode == "raise":
            raise ProviderError(f"{self.name} 模拟失败")
        if self.mode == "not_implemented":
            raise NotImplementedError(f"{self.name} 不支持交易日历")
        if self.mode == "empty":
            return FetchResult(rows=0, total=0)
        frame = pd.DataFrame({"trade_date": [start, end]})
        return FetchResult(rows=self.rows, total=self.rows,
                           detail={"data": frame, "source": self.name})


def test_provider_chain_fallback() -> None:
    """主源失败自动降级到兜底源，并在 detail["attempts"] 记录逐源成败。"""
    primary = _FakeProvider("primary", mode="raise")
    fallback = _FakeProvider("fallback", mode="ok")
    chain = ProviderChain([primary, fallback], max_retries=0)

    async def _run() -> FetchResult:
        return await chain.fetch_trade_calendar(date(2026, 10, 19), date(2026, 10, 20))

    result = asyncio.run(_run())
    assert result.rows == 3
    assert result.source == "fallback"
    attempts = result.detail["attempts"]
    assert attempts[0]["status"] == "failed"
    assert attempts[-1]["status"] == "ok"


def test_provider_chain_skips_not_implemented_and_empty() -> None:
    """NotImplementedError 视为"能力不支持"跳过；空结果继续尝试兜底源。"""
    empty = _FakeProvider("empty", mode="empty")
    not_impl = _FakeProvider("baostock", mode="not_implemented")
    good = _FakeProvider("good", mode="ok", rows=5)
    chain = ProviderChain([empty, not_impl, good], max_retries=0)

    async def _run() -> FetchResult:
        return await chain.fetch_trade_calendar(date(2026, 10, 19), date(2026, 10, 20))

    result = asyncio.run(_run())
    assert result.rows == 5
    statuses = [item["status"] for item in result.detail["attempts"]]
    assert "empty" in statuses
    assert "not_implemented" in statuses


def test_provider_chain_all_failed_raises() -> None:
    chain = ProviderChain(
        [_FakeProvider("a", mode="raise"), _FakeProvider("b", mode="raise")],
        max_retries=0,
    )

    async def _run() -> FetchResult:
        return await chain.fetch_stock_universe()

    with pytest.raises(ProviderError):
        asyncio.run(_run())


def test_fetch_result_summary_is_json_safe() -> None:
    """FetchResult.summary() 必须去掉 DataFrame，避免 API 序列化 500。"""
    result = FetchResult(
        rows=10,
        total=10,
        elapsed_ms=5,
        detail={"data": pd.DataFrame({"a": [1]}), "source": "akshare", "skipped": None},
    )
    summary = result.summary()
    assert "data" not in summary["detail"]
    assert summary["detail"]["source"] == "akshare"
    assert math.isfinite(summary["rows"])
