"""
同花顺板块资金流兜底源测试（**不联网、不连库**）
================================================
被测算模块：``app.ingest.ths_sector``

覆盖点（对应任务验收项）：

    ① ``normalize_sector_fundflow``：用**与实测列名一致**的构造表验证
       亿元 → 元（×1e8）、百分数保留、数值类型、缺列容忍、空表 / None；
    ② ``build_sector_daily_rows``：有 / 无 code_map 的 sector_code 生成规则；
       ``up_count`` / ``down_count`` / ``volume`` / ``amount`` / ``turnover_rate``
       **必须为 None**（防止后人"好心"填市值或编造涨跌家数）；
    ③ ``sector_net_inflow_map``：同名去重求和、空值跳过；
    ④ 列名漂移：``净额`` → ``资金净额``、``流入资金`` → ``资金流入`` 仍可匹配；
       ``行业-涨跌幅`` 缺失时**不能**误用 ``领涨股-涨跌幅``（排除词生效）；
    ⑤ 联网函数：用 Fake ak 验证限频调用、双源合并、缓存命中、
       全部失败降级为 ``FetchResult(failed=2)``（不抛异常）；
    ⑥ ``collect_sector_fallback``：**跳过当日 src='akshare' 的板块**（不覆盖东财）。

DB 完全用替身（``_db_scope`` / ``_protected_sector_names`` / ``upsert_rows``），
保证在无 PostgreSQL、无外网的 CI 上也能全绿。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest

pd = pytest.importorskip("pandas")

from app.core.errors import ProviderError  # noqa: E402
from app.ingest import ths_sector  # noqa: E402
from app.ingest.ths_sector import (  # noqa: E402
    NORMALIZED_COLUMNS,
    build_sector_daily_rows,
    normalize_sector_fundflow,
    sector_net_inflow_map,
)

# 用"中国时区的今天"：collect 的即时日期守卫只接受今天 / 最近 10 天，
# 写死日期会让测试在若干个月后突然失效。
DAY = ths_sector._today_cn()


# ============================================================
# 构造"实测列名"原始表（列名与 akshare 1.19.1 线上返回完全一致）
# ============================================================
def _industry_raw() -> pd.DataFrame:
    """行业资金流原始表：3 行，金额是"亿元"字符串，涨跌幅含 %。"""
    return pd.DataFrame(
        {
            "序号": [1, 2, 3],
            "行业": ["半导体", "银行", "光伏设备"],
            "行业指数": ["1234.56", "2345.67", "3456.78"],
            "行业-涨跌幅": ["2.35%", "-1.20%", 0.5],
            "流入资金": ["123.45", "67.89", "1.23"],
            "流出资金": ["100.00", "80.00", 0.5],
            "净额": ["23.45", "-12.11", 0.73],
            "公司家数": [120, 45, 88],
            "领涨股": ["北方华创", "招商银行", "隆基绿能"],
            "领涨股-涨跌幅": ["10.01%", "3.20%", "-2.10%"],
            "当前价": ["350.12", "40.00", "20.00"],
        }
    )


def _concept_raw() -> pd.DataFrame:
    """概念资金流原始表：列名与行业表相同，但"行业"列装的是概念名。"""
    return pd.DataFrame(
        {
            "序号": [1, 2],
            "行业": ["半导体", "低空经济"],
            "行业指数": ["2100.00", "1500.00"],
            "行业-涨跌幅": ["3.10%", "-0.80%"],
            "流入资金": ["50.00", "30.00"],
            "流出资金": ["40.00", "35.00"],
            "净额": ["99.00", "-5.00"],
            "公司家数": [60, 40],
            "领涨股": ["中芯国际", "某概念股"],
            "领涨股-涨跌幅": ["8.80%", "-1.10%"],
            "当前价": ["90.00", "10.00"],
        }
    )


def _board_raw() -> pd.DataFrame:
    """行业代码表原始表（stock_board_industry_name_ths）。"""
    return pd.DataFrame({"name": ["半导体", "银行"], "code": ["881121", "881131"]})


@pytest.fixture(autouse=True)
def _reset_sync_cache():
    """同步缓存是进程级全局状态，用例之间必须清空，避免相互污染。"""
    ths_sector.clear_net_inflow_cache()
    yield
    ths_sector.clear_net_inflow_cache()


# ============================================================
# ① normalize_sector_fundflow
# ============================================================
def test_normalize_units_and_columns() -> None:
    """亿元 → 元（×1e8）、百分数原样、列顺序与类型正确。"""
    frame = normalize_sector_fundflow(_industry_raw(), "industry")

    assert list(frame.columns) == list(NORMALIZED_COLUMNS)
    assert frame["sector_type"].tolist() == ["industry"] * 3
    assert frame["name"].tolist() == ["半导体", "银行", "光伏设备"]

    # 亿元 → 元
    assert frame["net_inflow"].tolist() == pytest.approx([23.45e8, -12.11e8, 0.73e8])
    assert frame["inflow"].tolist() == pytest.approx([123.45e8, 67.89e8, 1.23e8])
    assert frame["outflow"].tolist() == pytest.approx([100.00e8, 80.00e8, 0.5e8])

    # 百分数不做二次换算（2.35 表示 2.35%）
    assert frame["pct_chg"].tolist() == pytest.approx([2.35, -1.20, 0.5])
    assert frame["leading_stock_pct"].tolist() == pytest.approx([10.01, 3.20, -2.10])

    assert frame["index_value"].tolist() == pytest.approx([1234.56, 2345.67, 3456.78])
    assert frame["company_count"].tolist() == [120, 45, 88]
    assert frame["leading_stock"].tolist() == ["北方华创", "招商银行", "隆基绿能"]

    # 数值列必须是数值 dtype（不是 object），否则下游 sum/比较会出错
    for column in ("net_inflow", "inflow", "outflow", "pct_chg",
                   "index_value", "company_count", "leading_stock_pct"):
        assert pd.api.types.is_numeric_dtype(frame[column]), f"{column} 应为数值列"


def test_normalize_concept_uses_concept_names() -> None:
    """概念表列名与行业表相同（都叫"行业"），但 sector_type 必须是 concept。"""
    frame = normalize_sector_fundflow(_concept_raw(), "concept")
    assert frame["sector_type"].tolist() == ["concept"] * 2
    assert frame["name"].tolist() == ["半导体", "低空经济"]
    assert frame["net_inflow"].tolist() == pytest.approx([99.00e8, -5.00e8])


def test_normalize_tolerates_missing_columns() -> None:
    """缺列不能抛错：缺的列给 NaN（DataFrame 空值），行数照常。"""
    raw = _industry_raw().drop(columns=["净额", "领涨股", "领涨股-涨跌幅", "公司家数"])
    frame = normalize_sector_fundflow(raw, "industry")

    assert list(frame.columns) == list(NORMALIZED_COLUMNS)
    assert len(frame) == 3
    assert frame["net_inflow"].isna().all()
    assert frame["leading_stock"].isna().all()
    assert frame["company_count"].isna().all()

    # 转成入库行时必须统一成 None（DB NULL），而不是 NaN
    rows = build_sector_daily_rows(frame, {}, DAY)
    assert all(row["leading_stock"] is None for row in rows)
    # 净额不上行，但 close/pct 仍在
    assert rows[0]["close"] == pytest.approx(1234.56)


def test_normalize_empty_and_none_input() -> None:
    """空表 / None：返回列齐全的空表，绝不抛错。"""
    for value in (pd.DataFrame(), None):
        frame = normalize_sector_fundflow(value, "industry")  # type: ignore[arg-type]
        assert frame.empty
        assert list(frame.columns) == list(NORMALIZED_COLUMNS)

    # 只有表头没有行也不能崩
    frame = normalize_sector_fundflow(_industry_raw().head(0), "concept")
    assert frame.empty and list(frame.columns) == list(NORMALIZED_COLUMNS)


def test_normalize_column_drift_net_amount() -> None:
    """列名漂移：``净额`` → ``资金净额``、``流入资金`` → ``资金流入`` 仍能匹配。"""
    raw = _industry_raw().rename(
        columns={
            "净额": "资金净额",
            "流入资金": "资金流入",
            "流出资金": "资金流出",
            "行业": "行业名称",          # 板块名列也漂移
            "行业指数": "行业指数(点)",   # 指数列加单位后缀
        }
    )
    frame = normalize_sector_fundflow(raw, "industry")

    assert frame["name"].tolist() == ["半导体", "银行", "光伏设备"]
    assert frame["net_inflow"].tolist() == pytest.approx([23.45e8, -12.11e8, 0.73e8])
    assert frame["inflow"].tolist() == pytest.approx([123.45e8, 67.89e8, 1.23e8])
    assert frame["index_value"].tolist() == pytest.approx([1234.56, 2345.67, 3456.78])


def test_normalize_does_not_confuse_leader_pct_with_sector_pct() -> None:
    """``行业-涨跌幅`` 缺失时，**不能**把 ``领涨股-涨跌幅`` 当成板块涨跌幅。"""
    raw = _industry_raw().drop(columns=["行业-涨跌幅"])
    frame = normalize_sector_fundflow(raw, "industry")
    assert frame["pct_chg"].isna().all()

    # 而领涨股涨跌幅自己仍然能被正确识别
    assert frame["leading_stock_pct"].tolist() == pytest.approx([10.01, 3.20, -2.10])


def test_normalize_suffix_units() -> None:
    """"3.5亿" / "35000万" 这种带单位后缀的脏值也要换算成正确的元。"""
    raw = pd.DataFrame({"行业": ["A", "B"], "净额": ["3.5亿", "35000万"]})
    frame = normalize_sector_fundflow(raw, "industry")
    assert frame["net_inflow"].tolist() == pytest.approx([3.5e8, 3.5e8])


# ============================================================
# ② build_sector_daily_rows
# ============================================================
def _normalized() -> pd.DataFrame:
    return normalize_sector_fundflow(_industry_raw(), "industry")


def test_build_rows_ignores_code_map() -> None:
    """code_map 仅为兼容保留：代码一律 THS_名称，避免代码表可用性波动造成双主键。"""
    rows = build_sector_daily_rows(_normalized(), {"半导体": "881121"}, DAY)
    by_name = {row["sector_name"]: row for row in rows}

    assert by_name["半导体"]["sector_code"] == "THS_半导体"
    assert by_name["银行"]["sector_code"] == "THS_银行"
    assert by_name["光伏设备"]["sector_code"] == "THS_光伏设备"

    assert all(row["trade_date"] == DAY for row in rows)
    assert all(row["src"] == "ths" for row in rows)
    assert all(row["sector_type"] == "industry" for row in rows)

    # 字段映射
    assert by_name["半导体"]["close"] == pytest.approx(1234.56)
    assert by_name["半导体"]["pct_chg"] == pytest.approx(2.35)
    assert by_name["半导体"]["leading_stock"] == "北方华创"
    assert by_name["半导体"]["leading_stock_pct"] == pytest.approx(10.01)


def test_build_rows_without_code_map() -> None:
    """无 code_map（None 或空 dict）→ 全部 THS_ 前缀，且两次调用完全可复现。"""
    first = build_sector_daily_rows(_normalized(), {}, DAY)
    second = build_sector_daily_rows(_normalized(), None, DAY)  # type: ignore[arg-type]
    assert [row["sector_code"] for row in first] == [
        "THS_半导体", "THS_银行", "THS_光伏设备",
    ]
    assert first == second


def test_build_rows_never_invents_missing_fields() -> None:
    """关键红线：涨跌家数/成交量/成交额/换手率/OHLC 必须为 None，绝不拿别的字段凑。"""
    rows = build_sector_daily_rows(_normalized(), {"半导体": "881121"}, DAY)
    assert len(rows) == 3
    for row in rows:
        assert row["up_count"] is None, "同花顺没有涨跌家数，不能编造"
        assert row["down_count"] is None, "同花顺没有涨跌家数，不能编造"
        assert row["volume"] is None, "该源不提供成交量，不能拿市值冒充"
        assert row["amount"] is None, "该源不提供成交额，不能拿市值冒充"
        assert row["turnover_rate"] is None
        assert row["open"] is None
        assert row["high"] is None
        assert row["low"] is None
        assert row["pre_close"] is None
        assert row["amplitude"] is None


def test_build_rows_accepts_string_date_and_rejects_garbage() -> None:
    """trade_date 容忍字符串；解析不了直接 ValueError（写错日期比失败更可怕）。"""
    rows = build_sector_daily_rows(_normalized(), {}, DAY.isoformat())
    assert rows[0]["trade_date"] == DAY
    with pytest.raises(ValueError):
        build_sector_daily_rows(_normalized(), {}, "不是日期")


def test_build_rows_dedupes_same_sector_code() -> None:
    """同一板块名出现多行时只留一行（PG upsert 同语句不能重复命中冲突键）。"""
    duplicated = pd.concat([_normalized(), _normalized()], ignore_index=True)
    rows = build_sector_daily_rows(duplicated, {}, DAY)
    codes = [row["sector_code"] for row in rows]
    assert len(codes) == len(set(codes))
    assert sorted(codes) == ["THS_光伏设备", "THS_半导体", "THS_银行"]


# ============================================================
# ③ sector_net_inflow_map
# ============================================================
def test_sector_net_inflow_map_dedupes_and_sums() -> None:
    """同名板块求和；净额缺失的板块跳过（不假装 0 元）。"""
    frame = pd.DataFrame(
        {
            "name": ["半导体", "半导体", "银行", "光伏设备"],
            "sector_type": ["industry"] * 4,
            "net_inflow": [10.0, 5.0, -2.5, None],
        }
    )
    mapping = sector_net_inflow_map(frame)
    assert mapping == {"半导体": 15.0, "银行": -2.5}
    assert "光伏设备" not in mapping


def test_sector_net_inflow_map_from_normalized() -> None:
    """真实归一化表：单位必须是元（与 engine 情绪分口径一致）。"""
    mapping = sector_net_inflow_map(_normalized())
    assert mapping["半导体"] == pytest.approx(23.45e8)
    assert mapping["银行"] == pytest.approx(-12.11e8)


def test_sector_net_inflow_map_empty() -> None:
    """空表 / None → {}，不抛错。"""
    assert sector_net_inflow_map(pd.DataFrame()) == {}
    assert sector_net_inflow_map(None) == {}  # type: ignore[arg-type]


# ============================================================
# 行业代码表
# ============================================================
def test_parse_industry_code_map() -> None:
    """name/code → {行业名: 881xxx}；代码统一成字符串（不能变 881121.0）。"""
    mapping = ths_sector._parse_industry_code_map(_board_raw())
    assert mapping == {"半导体": "881121", "银行": "881131"}

    # 列名漂移：name/code → 名称/代码
    drifted = _board_raw().rename(columns={"name": "名称", "code": "代码"})
    assert ths_sector._parse_industry_code_map(drifted) == mapping

    # 空表 / 缺列 → {}
    assert ths_sector._parse_industry_code_map(pd.DataFrame()) == {}
    assert ths_sector._parse_industry_code_map(None) == {}
    assert ths_sector._parse_industry_code_map(_industry_raw()) == {}


# ============================================================
# ④ 联网函数（Fake ak / Fake limiter，全程离线）
# ============================================================
class _FakeLimiter:
    """替身限频器：只计数不等待（真限频逻辑由 base.RateLimiter 自己测）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> float:
        self.calls += 1
        return 0.0


class _FakeAk:
    """替身 akshare 模块：可配置成功 / 抛异常，并记录调用。"""

    def __init__(self, *, industry=None, concept=None, board=None,
                 fail_industry: bool = False, fail_concept: bool = False) -> None:
        self.industry = _industry_raw() if industry is None else industry
        self.concept = _concept_raw() if concept is None else concept
        self.board = _board_raw() if board is None else board
        self.fail_industry = fail_industry
        self.fail_concept = fail_concept
        self.calls: list[tuple[str, str | None]] = []

    def stock_fund_flow_industry(self, symbol: str = "即时") -> pd.DataFrame:
        self.calls.append(("industry", symbol))
        if self.fail_industry:
            raise ConnectionError("RemoteDisconnected: 10jqka 不可达")
        return self.industry

    def stock_fund_flow_concept(self, symbol: str = "即时") -> pd.DataFrame:
        self.calls.append(("concept", symbol))
        if self.fail_concept:
            raise ConnectionError("RemoteDisconnected: 10jqka 不可达")
        return self.concept

    def stock_board_industry_name_ths(self) -> pd.DataFrame:
        self.calls.append(("board", None))
        return self.board


@pytest.fixture()
def fake_env(monkeypatch):
    """把 ak / 限频 / 联网开关替换成离线替身，返回 (fake_ak, fake_limiter)。"""
    fake_ak = _FakeAk()
    limiter = _FakeLimiter()
    monkeypatch.setattr(ths_sector, "ak", fake_ak)
    monkeypatch.setattr(ths_sector, "HISTORY_LIMITER", limiter)
    monkeypatch.setattr(ths_sector.config, "INGEST_NETWORK_ENABLED", True)
    return fake_ak, limiter


def test_fetch_async_success_and_rate_limited(fake_env) -> None:
    """异步抓取：两张表都归一化成功，且每次请求都过限频器（含代码表）。"""
    fake_ak, limiter = fake_env
    industry, concept = asyncio.run(ths_sector.fetch_sector_fundflow())

    assert list(industry.columns) == list(NORMALIZED_COLUMNS)
    assert list(concept.columns) == list(NORMALIZED_COLUMNS)
    assert industry["name"].tolist() == ["半导体", "银行", "光伏设备"]
    assert concept["name"].tolist() == ["半导体", "低空经济"]
    # 请求参数必须是 symbol="即时"（同花顺即时资金流）
    assert fake_ak.calls == [("industry", "即时"), ("concept", "即时")]
    assert limiter.calls == 2

    code_map = asyncio.run(ths_sector.fetch_industry_code_map())
    assert code_map == {"半导体": "881121", "银行": "881131"}
    assert limiter.calls == 3  # 代码表也要限频


def test_fetch_async_partial_failure_keeps_other_table(monkeypatch) -> None:
    """单张表失败 → 另一张照常返回，不抛异常。"""
    fake_ak = _FakeAk(fail_concept=True)
    monkeypatch.setattr(ths_sector, "ak", fake_ak)
    monkeypatch.setattr(ths_sector, "HISTORY_LIMITER", _FakeLimiter())
    monkeypatch.setattr(ths_sector.config, "INGEST_NETWORK_ENABLED", True)

    industry, concept = asyncio.run(ths_sector.fetch_sector_fundflow())
    assert not industry.empty
    assert concept.empty
    assert list(concept.columns) == list(NORMALIZED_COLUMNS)


def test_fetch_async_both_failure_raises_provider_error(monkeypatch) -> None:
    """两张表都失败 → ProviderError（必须让上层知道"兜底源也不可用"）。"""
    fake_ak = _FakeAk(fail_industry=True, fail_concept=True)
    monkeypatch.setattr(ths_sector, "ak", fake_ak)
    monkeypatch.setattr(ths_sector, "HISTORY_LIMITER", _FakeLimiter())
    monkeypatch.setattr(ths_sector.config, "INGEST_NETWORK_ENABLED", True)

    with pytest.raises(ProviderError):
        asyncio.run(ths_sector.fetch_sector_fundflow())


def test_fetch_async_without_akshare_raises(monkeypatch) -> None:
    """akshare 未安装（ak=None）→ 立刻 ProviderError，不联网。"""
    monkeypatch.setattr(ths_sector, "ak", None)
    with pytest.raises(ProviderError):
        asyncio.run(ths_sector.fetch_sector_fundflow())


def test_fetch_async_network_disabled(monkeypatch) -> None:
    """INGEST_NETWORK_ENABLED=0 → ProviderError（离线环境绝不偷偷联网）。"""
    monkeypatch.setattr(ths_sector, "ak", _FakeAk())
    monkeypatch.setattr(ths_sector.config, "INGEST_NETWORK_ENABLED", False)
    with pytest.raises(ProviderError):
        asyncio.run(ths_sector.fetch_sector_fundflow())


# ============================================================
# ⑤ 同步版本（engine.sector_analysis 兜底用）：绝不抛异常 + 当日缓存
# ============================================================
def test_sync_map_merges_industry_first_and_caches(fake_env) -> None:
    """None = 两张表合并、同名以行业优先；第二次调用命中缓存，0 次新请求。"""
    fake_ak, _ = fake_env
    concept = _concept_raw()  # 概念表里也有"半导体"，净额 99 亿
    fake_ak.concept = concept

    mapping = ths_sector.fetch_net_inflow_map_sync(None)
    assert mapping["半导体"] == pytest.approx(23.45e8), "同名必须以行业（23.45 亿）优先"
    assert mapping["低空经济"] == pytest.approx(-5.00e8)
    assert len(fake_ak.calls) == 2

    first_calls = len(fake_ak.calls)
    again = ths_sector.fetch_net_inflow_map_sync(None)
    assert again == mapping
    assert len(fake_ak.calls) == first_calls, "命中缓存后不应产生新请求"


def test_sync_map_single_kind(fake_env) -> None:
    """指定 industry 只请求行业表，不碰概念表。"""
    fake_ak, _ = fake_env
    mapping = ths_sector.fetch_net_inflow_map_sync("industry")
    assert mapping["半导体"] == pytest.approx(23.45e8)
    assert "低空经济" not in mapping
    assert fake_ak.calls == [("industry", "即时")]


def test_sync_map_never_raises_on_failure(fake_env) -> None:
    """同步版本失败 → {}，绝不抛异常（engine 评分不能被打断）。"""
    fake_ak, _ = fake_env
    fake_ak.fail_industry = True
    fake_ak.fail_concept = True

    assert ths_sector.fetch_net_inflow_map_sync(None) == {}
    # 失败结果也缓存，10 分钟内不重复打接口（避免 86 个板块各失败一次）
    calls = len(fake_ak.calls)
    assert ths_sector.fetch_net_inflow_map_sync(None) == {}
    assert len(fake_ak.calls) == calls


def test_sync_map_without_akshare_returns_empty(monkeypatch) -> None:
    """akshare 未安装 / 禁止联网 → {}，不抛异常、不联网。"""
    monkeypatch.setattr(ths_sector, "ak", None)
    assert ths_sector.fetch_net_inflow_map_sync("industry") == {}

    monkeypatch.setattr(ths_sector, "ak", _FakeAk())
    monkeypatch.setattr(ths_sector.config, "INGEST_NETWORK_ENABLED", False)
    assert ths_sector.fetch_net_inflow_map_sync(None) == {}


# ============================================================
# ⑥ collect_sector_fallback：不覆盖东财 + 失败降级
# ============================================================
def test_collect_skips_historical_date() -> None:
    """同花顺只有即时快照：历史日期必须跳过，绝不把今天数据写进历史（未来函数）。

    守卫如果失效，这个用例会真去联网/连库而失败 —— 正好当canary。
    """
    result = asyncio.run(ths_sector.collect_sector_fallback(date(2020, 1, 2)))
    assert result.rows == 0
    assert result.total == 0
    assert result.failed == 0
    assert result.detail["skipped"] == "not_realtime_date"
    assert result.detail["realtime_only"] is True
    assert result.is_skipped


def test_collect_skips_future_date() -> None:
    """未来日期同样不写库（skipped=future_date）。"""
    result = asyncio.run(
        ths_sector.collect_sector_fallback(DAY + timedelta(days=3))
    )
    assert result.rows == 0
    assert result.detail["skipped"] == "future_date"


def test_skip_protected_rows() -> None:
    """受保护（当日 src='akshare'）的同名板块必须被剔除。"""
    rows = [
        {"sector_code": "THS_半导体", "sector_name": "半导体"},
        {"sector_code": "THS_银行", "sector_name": "银行"},
    ]
    kept, skipped = ths_sector._skip_protected(rows, {"半导体"})
    assert [row["sector_code"] for row in kept] == ["THS_银行"]
    assert skipped == ["半导体"]

    # 没有受保护数据时原样保留
    kept_all, skipped_none = ths_sector._skip_protected(rows, set())
    assert kept_all == rows and skipped_none == []


def test_collect_degrades_when_fetch_fails(monkeypatch) -> None:
    """抓取全失败 → FetchResult(rows=0, failed=2)，**不抛异常、不碰 DB**。"""
    monkeypatch.setattr(ths_sector, "ak", None)
    result = asyncio.run(ths_sector.collect_sector_fallback(DAY))
    assert result.rows == 0
    assert result.total == 0
    assert result.failed == 2
    assert result.detail["source"] == "ths"
    assert result.detail["skipped"] == "fetch_failed"
    assert "akshare 未安装" in result.detail["error"]


def test_collect_skips_akshare_rows(monkeypatch) -> None:
    """端到端（DB 替身）：当日已有 src='akshare' 的板块被跳过，其余写入。

    这是本任务最关键的红线：兜底数据只有涨跌幅，绝不能覆盖东财的完整行情。
    """
    captured: dict[str, object] = {}

    async def fake_fetch():
        return (
            normalize_sector_fundflow(_industry_raw(), "industry"),
            normalize_sector_fundflow(_concept_raw(), "concept"),
        )

    async def fake_code_map():
        return {"半导体": "881121", "银行": "881131"}

    @asynccontextmanager
    async def fake_scope(db):
        yield object()  # 假会话：只需要能给 _protected_codes / upsert_rows 用

    async def fake_protected(session, trade_date):
        assert trade_date == DAY
        return {"半导体"}  # 半导体 当日已由东财写入

    async def fake_upsert(session, model, rows, index_elements):
        captured["rows"] = rows
        captured["model"] = model.__name__
        captured["index_elements"] = index_elements
        return len(rows)

    monkeypatch.setattr(ths_sector, "fetch_sector_fundflow", fake_fetch)
    monkeypatch.setattr(ths_sector, "fetch_industry_code_map", fake_code_map)
    monkeypatch.setattr(ths_sector, "_db_scope", fake_scope)
    monkeypatch.setattr(ths_sector, "_protected_sector_names", fake_protected)
    monkeypatch.setattr("app.ingest.base.upsert_rows", fake_upsert)

    result = asyncio.run(ths_sector.collect_sector_fallback(DAY))

    rows = captured["rows"]
    names = {row["sector_name"] for row in rows}
    assert "半导体" not in names, "已存在东财数据的板块绝不能写入兜底行"
    assert names == {"银行", "光伏设备", "低空经济"}
    assert captured["index_elements"] == ["sector_code", "trade_date"]
    assert captured["model"] == "FactSectorDaily"

    assert result.rows == 3          # 写入行数
    # 行业 3 行 + 概念 2 行，其中「半导体」行业/概念同名合并为 1 行 → 4 行
    assert result.total == 4
    assert result.failed == 0
    assert result.detail["skipped_akshare"] == 1
    assert result.detail["skipped_names"] == ["半导体"]
    assert result.detail["source"] == "ths"
    assert all(row["src"] == "ths" for row in rows)
