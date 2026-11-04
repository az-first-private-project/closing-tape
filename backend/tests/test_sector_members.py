"""板块成分抓取的熔断/预算与质量状态（不联网、不连库）。

背景：2026-11-03 东财板块成分接口整体不可用时，480 个板块逐个重试约 58 分钟，
拖死共享连接导致日更后续步骤全部失败。这里验证：

  ① 连续失败达到阈值后熔断，不再抓剩余板块（已有成分区间保持不变）；
  ② 中间成功会重置连续失败计数，偶发抖动不会误熔断；
  ③ 抓取总预算耗尽后跳过剩余板块；
  ④ 全部失败时 quality_status=stale，run_task 不会把质量记录洗成 ok。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date

import pandas as pd

from app.ingest import akshare_provider, service
from app.ingest.base import FetchResult


class _FakeJob:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def begin(self, *args, **kwargs) -> None:
        return None

    async def update(self, *args, **kwargs) -> None:
        return None

    async def finish(self, *args, **kwargs) -> None:
        return None


class _FastLimiter:
    async def acquire(self) -> None:
        return None


class _ScriptedChain:
    """按预设序列返回成功/失败/空名单/未匹配，用于驱动熔断与防误关逻辑。"""

    def __init__(self, outcomes: list[bool | str]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.last_attempts: list[dict] = []

    async def fetch_sector_members(self, *args, **kwargs) -> FetchResult:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if outcome is False or outcome == "fail":
            raise ConnectionError("connection reset by peer")
        if outcome == "skip":
            return FetchResult(
                rows=0,
                total=0,
                detail={
                    "data": pd.DataFrame(columns=["stock_code"]),
                    "skip_reason": "em_name_not_found",
                },
            )
        if outcome == "empty":
            frame = pd.DataFrame(columns=["stock_code"])
        else:
            frame = pd.DataFrame([{"stock_code": "600519"}])
        return FetchResult(rows=len(frame), total=len(frame), detail={"data": frame})


class _FakeSession:
    """记录 execute 调用，用于断言"没有执行关闭区间的 UPDATE"。"""

    def __init__(self) -> None:
        self.executed: list[object] = []

    async def execute(self, statement):
        self.executed.append(statement)
        return _FakeResult()


class _FakeResult:
    rowcount = 0


def _patch_service(
    monkeypatch,
    chain: _ScriptedChain,
    sector_count: int,
    *,
    open_members: dict[str, set[str]] | None = None,
    partial_members: dict[str, set[str]] | None = None,
    sector_info: dict[str, dict] | None = None,
    upserted: list[dict] | None = None,
):
    session = _FakeSession()
    quality_calls: list[tuple] = []

    @asynccontextmanager
    async def fake_lock(task):
        yield True

    @asynccontextmanager
    async def fake_scope(db):
        yield session

    async def fake_trade_day(db, day):
        return True

    async def fake_sector_info(db, day):
        if sector_info is not None:
            return sector_info
        return {
            f"S{index:02d}": {"sector_name": f"板块{index}", "sector_type": "concept"}
            for index in range(sector_count)
        }

    async def fake_open(db):
        return open_members or {}

    async def fake_partial(db):
        return partial_members or {}

    async def fake_record_quality(*args, **kwargs) -> None:
        quality_calls.append(args)

    async def fake_upsert(session, model, rows, keys, **kwargs) -> int:
        if upserted is not None:
            upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(service, "_advisory_lock", fake_lock)
    monkeypatch.setattr(service, "_db_scope", fake_scope)
    monkeypatch.setattr(service, "_is_trade_day", fake_trade_day)
    monkeypatch.setattr(service, "_load_sector_info", fake_sector_info)
    monkeypatch.setattr(service, "_load_open_members", fake_open)
    monkeypatch.setattr(service, "_load_partial_members", fake_partial)
    monkeypatch.setattr(service, "get_provider_chain", lambda: chain)
    monkeypatch.setattr(service, "record_quality", fake_record_quality)
    monkeypatch.setattr(service, "_JobTracker", _FakeJob)
    monkeypatch.setattr(service, "HISTORY_LIMITER", _FastLimiter())
    monkeypatch.setattr(service, "upsert_rows", fake_upsert)
    return session, quality_calls


def test_circuit_breaker_stops_after_consecutive_failures(monkeypatch) -> None:
    chain = _ScriptedChain([False] * 12)
    _patch_service(monkeypatch, chain, sector_count=12)

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == service._SECTOR_MEMBERS_MAX_CONSECUTIVE_FAILURES == 5
    assert result.failed == 5
    assert result.detail["skipped"] == 7
    assert result.detail["circuit_open"] is True
    assert result.quality_status == "stale"


def test_partial_success_with_breaker_is_stale(monkeypatch) -> None:
    """有部分板块成功但触发熔断时，本次只更新了一小部分，质量必须标 stale。"""
    outcomes = [True, False, False, False, False, False]
    chain = _ScriptedChain(outcomes)
    _patch_service(monkeypatch, chain, sector_count=len(outcomes))

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == len(outcomes)
    assert result.failed == 5
    assert result.detail["circuit_open"] is True
    assert result.rows > 0
    assert result.quality_status == "stale"


def test_success_resets_consecutive_failure_counter(monkeypatch) -> None:
    """4 次失败 + 1 次成功 + 4 次失败：不应熔断，也不会漏抓后续板块。"""
    outcomes = [False, False, False, False, True, False, False, False, False]
    chain = _ScriptedChain(outcomes)
    _patch_service(monkeypatch, chain, sector_count=len(outcomes))

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == len(outcomes)
    assert result.failed == 8
    assert result.detail["skipped"] == 0
    assert result.detail["circuit_open"] is False


def test_budget_exceeded_skips_remaining_without_fetching(monkeypatch) -> None:
    chain = _ScriptedChain([True] * 3)
    _patch_service(monkeypatch, chain, sector_count=3)
    monkeypatch.setattr(service, "_SECTOR_MEMBERS_BUDGET_SEC", 0.0)

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == 0
    assert result.detail["skipped"] == 3
    assert result.detail["budget_exceeded"] is True
    assert result.quality_status == "stale"


def test_empty_member_list_keeps_existing_intervals(monkeypatch) -> None:
    """源返回空名单（不是异常）时按抓取失败处理，不能拿空集合关掉全部区间。"""
    chain = _ScriptedChain(["empty"])
    session, quality_calls = _patch_service(
        monkeypatch, chain, sector_count=1,
        open_members={"S00": {"600519", "000001"}},
    )

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == 1
    assert result.failed == 1
    assert result.detail["closed"] == 0
    assert session.executed == []  # 没有执行任何关闭 UPDATE
    assert result.quality_status == "stale"
    report = quality_calls[-1][2]
    types = {item["type"] for item in report.anomalies}
    assert "partial_sector_fetch" in types


def test_incomplete_sector_list_does_not_close_missing_sectors(monkeypatch) -> None:
    """当日板块清单只覆盖 2/3 已有板块：缺失板块不关闭，并标质量不合格。"""
    open_members = {f"S{index:02d}": {"600519"} for index in range(30)}
    sector_info = {
        f"S{index:02d}": {"sector_name": f"板块{index}", "sector_type": "concept"}
        for index in range(20)
    }
    chain = _ScriptedChain(["ok"] * len(sector_info))
    session, quality_calls = _patch_service(
        monkeypatch, chain, sector_count=len(sector_info),
        open_members=open_members, sector_info=sector_info,
    )

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == 20
    assert result.detail["closed"] == 0
    assert result.detail["missing_sectors_skipped"] == 10
    assert session.executed == []  # 没有任何板块被当作"消失"关闭
    assert result.quality_status == "stale"
    report = quality_calls[-1][2]
    types = {item["type"] for item in report.anomalies}
    assert "sector_list_incomplete" in types
    assert report.ok is False


def test_complete_sector_list_closes_truly_missing_sector(monkeypatch) -> None:
    """清单覆盖 99%（>=98%）时，真正消失的板块仍按原逻辑关闭。"""
    open_members = {f"S{index:03d}": {"600519"} for index in range(100)}
    sector_info = {
        f"S{index:03d}": {"sector_name": f"板块{index}", "sector_type": "concept"}
        for index in range(99)
    }
    chain = _ScriptedChain(["ok"] * len(sector_info))
    session, _quality_calls = _patch_service(
        monkeypatch, chain, sector_count=len(sector_info),
        open_members=open_members, sector_info=sector_info,
    )

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert result.detail["missing_sectors_skipped"] == 0
    assert len(session.executed) == 1  # 恰好一条关闭缺失板块的 UPDATE
    assert result.quality_status == "ok"


def test_unmatched_sector_names_skipped_without_breaker(monkeypatch) -> None:
    """名称不在东财清单的板块：跳过、不计失败、不熔断、不动区间、质量仍 ok。"""
    open_members = {f"S{index:02d}": {"600519"} for index in range(6)}
    sector_info = {
        f"S{index:02d}": {"sector_name": f"板块{index}", "sector_type": "concept"}
        for index in range(6)
    }
    chain = _ScriptedChain(["skip", "ok", "skip", "ok", "skip", "ok"])
    session, quality_calls = _patch_service(
        monkeypatch, chain, sector_count=6,
        open_members=open_members, sector_info=sector_info,
    )

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == 6  # 未匹配板块不会触发熔断中断
    assert result.failed == 0
    assert result.detail["unmatched"] == 3
    assert result.detail["skipped"] == 0
    assert result.detail["circuit_open"] is False
    assert session.executed == []  # 未匹配板块不会被当作空名单关闭区间
    assert result.quality_status == "ok"
    report = quality_calls[-1][2]
    assert report.ok is True
    assert report.expected == 3  # 覆盖率只按可抓板块计算
    assert report.coverage == 1.0
    types = {item["type"] for item in report.anomalies}
    assert "sector_name_unmatched" in types


def test_all_unmatched_does_not_trip_breaker_but_stays_stale(monkeypatch) -> None:
    """整批板块名都无法匹配：不熔断、不误报失败；但零抓取仍须标 stale。"""
    chain = _ScriptedChain(["skip"] * 4)
    _patch_service(monkeypatch, chain, sector_count=4)

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert chain.calls == 4
    assert result.detail["unmatched"] == 4
    assert result.failed == 0
    assert result.detail["circuit_open"] is False
    assert result.quality_status == "stale"


def test_complete_list_closes_open_partial_intervals(monkeypatch) -> None:
    """拿到完整名单后，同板块残留的 partial 开区间必须一并结束。"""
    chain = _ScriptedChain(["ok"])
    session, _quality_calls = _patch_service(
        monkeypatch, chain, sector_count=1,
        open_members={"S00": {"600519"}},
        partial_members={"S00": {"600519"}},
    )

    result = asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert result.failed == 0
    assert len(session.executed) == 1  # 只有 partial 收尾这一条 UPDATE
    sql = str(session.executed[0])
    assert "is_partial" in sql and "effective_to" in sql
    assert result.quality_status == "ok"


def test_complete_list_replaces_partial_without_losing_overlap(monkeypatch) -> None:
    """完整名单取代 partial：重叠成分必须重新落完整开区间，partial 独有成分不补。"""
    chain = _ScriptedChain(["ok"])
    upserted: list[dict] = []
    session, _quality_calls = _patch_service(
        monkeypatch, chain, sector_count=1,
        open_members={"S00": {"600519", "000001"}},
        partial_members={"S00": {"600519", "000001"}},
        upserted=upserted,
    )

    asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    # 源只返回 600519：它必须作为完整成分重新插入（否则 partial 关闭后消失）；
    # 000001 只在 partial 名单里且不在完整名单中，不能补成完整成分。
    assert [row["stock_code"] for row in upserted] == ["600519"]
    assert upserted[0]["is_partial"] is False
    assert len(session.executed) == 1  # partial 区间关闭


def test_complete_list_keeps_complete_rows_not_in_partial(monkeypatch) -> None:
    """partial 与完整区间并存时：完整区间的退出成分关闭，未变的完整成分不重插。"""
    chain = _ScriptedChain(["ok"])
    upserted: list[dict] = []
    session, _quality_calls = _patch_service(
        monkeypatch, chain, sector_count=1,
        open_members={"S00": {"600519", "000001", "300750"}},
        partial_members={"S00": {"300750"}},
        upserted=upserted,
    )

    asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert upserted == []  # 600519 仍在名单里，无需重插
    assert len(session.executed) == 2
    close_params: set = set()
    for value in session.executed[0].compile().params.values():
        if isinstance(value, (list, tuple, set)):
            close_params.update(value)
        else:
            close_params.add(value)
    assert "000001" in close_params
    assert "600519" not in close_params
    assert "300750" not in close_params  # partial 行由第二条 UPDATE 负责
    assert "is_partial" in str(session.executed[1])


def test_boards_without_partial_rows_skip_extra_update(monkeypatch) -> None:
    """没有 partial 残留的板块不应多执行一条无用 UPDATE。"""
    chain = _ScriptedChain(["ok", "ok"])
    session, _quality_calls = _patch_service(
        monkeypatch, chain, sector_count=2,
        open_members={"S00": {"600519"}, "S01": {"600519"}},
    )

    asyncio.run(service.collect_sector_members(date(2026, 11, 3)))

    assert session.executed == []


def test_normalize_board_name_variants() -> None:
    cases = {
        "MR(混合现实)": "MR",
        "证券Ⅱ": "证券",
        "半导体 概念": "半导体",
        "银行行业": "银行",
        "新能源": "新能源",
        "  ": "",
    }
    for raw, expected in cases.items():
        assert akshare_provider.normalize_board_name(raw) == expected


def test_build_board_code_maps_drops_ambiguous_normalized_names() -> None:
    frame = pd.DataFrame(
        [
            {"板块名称": "证券Ⅱ", "板块代码": "BK0473"},
            {"板块名称": "证券", "板块代码": "BK0999"},
            {"板块名称": "MR(混合现实)", "板块代码": "BK1090"},
            {"板块名称": "银行行业", "板块代码": "BK0475"},
        ]
    )

    exact, normalized = akshare_provider.build_board_code_maps(frame)

    assert exact["证券Ⅱ"] == "BK0473"
    assert exact["证券"] == "BK0999"
    assert "证券" not in normalized  # 归一化后一对多，必须剔除
    assert normalized["MR"] == "BK1090"
    assert normalized["银行"] == "BK0475"
    assert akshare_provider.build_board_code_maps(None) == ({}, {})


def test_resolve_em_board_code_by_concept_alias() -> None:
    exact = {"新能源车": "BK0900"}
    normalized = {akshare_provider.normalize_board_name("新能源车"): "BK0900"}

    assert akshare_provider.resolve_em_board_code(
        exact, normalized, "新能源汽车", "concept"
    ) == "BK0900"


def test_resolve_em_board_code_prefers_exact_and_skips_alias_for_industry() -> None:
    exact = {"新能源汽车": "BK9999", "新能源车": "BK0900"}
    normalized = {
        akshare_provider.normalize_board_name("新能源车"): "BK0900",
    }

    assert akshare_provider.resolve_em_board_code(
        exact, normalized, "新能源汽车", "concept"
    ) == "BK9999"  # 精确名优先
    assert akshare_provider.resolve_em_board_code(
        exact, normalized, "新能源汽车", "industry"
    ) == "BK9999"  # 行业只走精确/归一化
    assert akshare_provider.resolve_em_board_code(
        {"新能源车": "BK0900"}, {}, "新能源汽车", "industry"
    ) is None  # 同义词不用于行业
    assert akshare_provider.resolve_em_board_code(
        {}, {}, "不存在的概念", "concept"
    ) is None
