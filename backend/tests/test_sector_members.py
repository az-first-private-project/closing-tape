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

from app.ingest import service
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
    """按预设序列返回成功/失败，用于驱动熔断逻辑。"""

    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.last_attempts: list[dict] = []

    async def fetch_sector_members(self, *args, **kwargs) -> FetchResult:
        ok = self.outcomes[self.calls]
        self.calls += 1
        if not ok:
            raise ConnectionError("connection reset by peer")
        frame = pd.DataFrame([{"stock_code": "600519"}])
        return FetchResult(rows=1, total=1, detail={"data": frame})


def _patch_service(monkeypatch, chain: _ScriptedChain, sector_count: int) -> None:
    @asynccontextmanager
    async def fake_lock(task):
        yield True

    @asynccontextmanager
    async def fake_scope(db):
        yield object()

    async def fake_trade_day(db, day):
        return True

    async def fake_sector_info(db, day):
        return {
            f"S{index:02d}": {"sector_name": f"板块{index}", "sector_type": "concept"}
            for index in range(sector_count)
        }

    async def fake_open(db):
        return {}

    async def fake_record_quality(*args, **kwargs) -> None:
        return None

    async def fake_upsert(session, model, rows, keys, **kwargs) -> int:
        return len(rows)

    monkeypatch.setattr(service, "_advisory_lock", fake_lock)
    monkeypatch.setattr(service, "_db_scope", fake_scope)
    monkeypatch.setattr(service, "_is_trade_day", fake_trade_day)
    monkeypatch.setattr(service, "_load_sector_info", fake_sector_info)
    monkeypatch.setattr(service, "_load_open_members", fake_open)
    monkeypatch.setattr(service, "get_provider_chain", lambda: chain)
    monkeypatch.setattr(service, "record_quality", fake_record_quality)
    monkeypatch.setattr(service, "_JobTracker", _FakeJob)
    monkeypatch.setattr(service, "HISTORY_LIMITER", _FastLimiter())
    monkeypatch.setattr(service, "upsert_rows", fake_upsert)


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
