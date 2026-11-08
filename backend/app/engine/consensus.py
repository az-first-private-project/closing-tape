"""多策略共识榜（V6 T6）。

同一只票被多个策略同时命中时按命中策略数排序。**纯读查询**，不写库、不跑策略。

口径（改动必须同步任务书与 `docs/V5-API-CONTRACT.md` 5.5）：
  - 取目标交易日**全部成功且非回测**的 run（`status='success'`、`is_backtest IS FALSE`），
    同一策略的多次参数 run 合并统计，`hit_count = count(DISTINCT algo_code)`；
  - 同一策略多次 run 命中同一只票只算一次，取**名次最优**那次 run 的 rank/score/metrics
    （名次相同再取更晚创建的 run）；
  - 只统计当前 `SPECS` 里仍启用的策略，历史 a1/a2 run 不参与（否则下线策略会污染共识）；
  - 不返回跨策略平均分：各策略 score 量纲不同（见 `_SORT_SPEC`），混算会误导；
  - 股票名称/行业/板块/所有制取 `dim_stock`，当日涨跌与综合分取当日事实表；
  - `pct_chg`、`composite_score`、`metrics` 缺失一律保留 NULL / 空对象，**不补 0**；
  - `sector_type` 为可选的成分过滤（SCD2：`effective_from <= d AND (effective_to IS NULL OR effective_to > d)`），
    只影响榜单行，不影响 `total_algos_evaluated`。

性能：整个榜单由**一条 SQL** 取回（run ⋈ signal ⋈ dim/事实表），Python 只做分组与排序；
禁止按策略循环查询。7 个策略当日千级信号下响应稳定在 100ms 内。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DataUnavailableError
from app.db.models.dim import DimSectorMember, DimStock
from app.db.models.fact import FactStockDaily, FactStockScoreDaily
from app.db.models.strategy import StgRun, StgSignal

#: 每条命中策略附带多少个 metrics 键值，避免响应体积随策略数线性膨胀
METRICS_PREVIEW_KEYS = 3


def _to_float(value: Any) -> float | None:
    """Numeric/Decimal/None → float；NaN/Inf 视为缺失。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def metrics_preview(
    metrics: Mapping[str, Any] | None,
    limit: int = METRICS_PREVIEW_KEYS,
) -> dict[str, Any]:
    """取 metrics 的前 N 个键值（按键名排序，保证跨 run 稳定可复现）。"""
    if not isinstance(metrics, Mapping) or limit <= 0:
        return {}
    keys = sorted(metrics.keys(), key=str)[:limit]
    return {str(key): metrics[key] for key in keys}


@dataclass(frozen=True)
class ConsensusSignalRow:
    """一条「策略 × 股票」命中（同一策略同一股票在 SQL 层已去重）。"""

    algo_code: str
    algo_name: str | None
    code: str | None
    signal_name: str | None
    rank: int | None
    score: Any
    metrics: Mapping[str, Any] | None
    stock_name: str | None = None
    industry_l1: str | None = None
    board: str | None = None
    ownership: str | None = None
    pct_chg: Any = None
    composite_score: Any = None
    in_sector: bool = True


def _better_hit(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """名次更小者优先；名次相同保留先到者（SQL 已按 run 创建时间排序）。"""
    if candidate["rank"] is None:
        return False
    if current["rank"] is None:
        return True
    return candidate["rank"] < current["rank"]


def _sort_key(item: dict[str, Any]) -> tuple:
    composite = item["composite_score"]
    return (
        -item["hit_count"],
        item["best_rank"] if item["best_rank"] is not None else 10**9,
        (0, -composite) if composite is not None else (1, 0.0),
        item["code"],
    )


def build_consensus(
    rows: Iterable[ConsensusSignalRow],
    *,
    min_count: int = 2,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """把扁平的命中行聚合成共识榜。

    - `total_algos_evaluated` 统计返回值里出现过的全部策略（含当日 0 信号的策略，
      SQL 用 LEFT JOIN 保证它们仍有一行 code=NULL）；
    - `min_count` 只过滤股票，不影响 `total_algos_evaluated`；
    - 空输入返回空榜单，不抛错。
    """
    row_list = list(rows)
    total_algos = len({row.algo_code for row in row_list})

    grouped: dict[str, dict[str, Any]] = {}
    for row in row_list:
        if row.code is None or not row.in_sector:
            continue
        group = grouped.get(row.code)
        if group is None:
            group = {
                "code": row.code,
                "name": row.stock_name or row.signal_name or row.code,
                "industry_l1": row.industry_l1,
                "board": row.board,
                "ownership": row.ownership,
                "pct_chg": _to_float(row.pct_chg),
                "composite_score": _to_float(row.composite_score),
                "_hits": {},
            }
            grouped[row.code] = group
        else:
            if not group["name"]:
                group["name"] = row.stock_name or row.signal_name or row.code
            for key, value in (
                ("industry_l1", row.industry_l1),
                ("board", row.board),
                ("ownership", row.ownership),
            ):
                if group[key] is None and value is not None:
                    group[key] = value
            if group["pct_chg"] is None:
                group["pct_chg"] = _to_float(row.pct_chg)
            if group["composite_score"] is None:
                group["composite_score"] = _to_float(row.composite_score)

        hit = {
            "algo_code": row.algo_code,
            "algo_name": row.algo_name or row.algo_code,
            "rank": row.rank,
            "score": _to_float(row.score),
            "metrics": metrics_preview(row.metrics),
        }
        existing = group["_hits"].get(row.algo_code)
        if existing is None or _better_hit(hit, existing):
            group["_hits"][row.algo_code] = hit

    items: list[dict[str, Any]] = []
    for group in grouped.values():
        hits = sorted(
            group.pop("_hits").values(),
            key=lambda hit: (
                hit["rank"] if hit["rank"] is not None else 10**9,
                hit["algo_code"],
            ),
        )
        hit_count = len(hits)
        if hit_count < min_count:
            continue
        ranks = [hit["rank"] for hit in hits if hit["rank"] is not None]
        items.append({
            **group,
            "hit_count": hit_count,
            "best_rank": min(ranks) if ranks else None,
            "algos": hits,
        })

    items.sort(key=_sort_key)
    total_matching = len(items)
    start = max(0, int(offset))
    size = max(0, int(limit))
    paged = items[start:start + size]
    return {
        "items": paged,
        "total_algos_evaluated": total_algos,
        # C1：total 为通过 min_count 的全量标的数（分页前），count 由前端按 items 长度计算
        "total": total_matching,
        "has_more": start + len(paged) < total_matching,
        "page": start // max(1, size) + 1,
        "page_size": size,
    }


async def _resolve_trade_date(session: AsyncSession, trade_date: date | None) -> date:
    if trade_date is not None:
        return trade_date
    result = await session.execute(select(func.max(FactStockDaily.trade_date)))
    value = result.scalar_one_or_none()
    if value is None:
        raise DataUnavailableError(
            "数据库中尚无个股行情数据",
            next_action="POST /api/v1/admin/ingest/daily_snapshot",
            detail={"dataset": "fact_stock_daily"},
        )
    return value


async def consensus_signals(
    session: AsyncSession,
    trade_date: date | None = None,
    *,
    min_count: int = 2,
    sector_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """读取某交易日的多策略共识榜（默认最新行情日）。"""
    # 延迟导入：避免 engine 层在 import 时就加载 pandas；同时只认当前启用的策略
    from app.strategy.strategies import SPECS

    target = await _resolve_trade_date(session, trade_date)
    algo_codes = sorted(SPECS)

    runs = (
        select(
            StgRun.algo_code,
            StgRun.algo_name,
            StgRun.run_id,
            StgRun.created_at,
        )
        .where(
            StgRun.trade_date == target,
            StgRun.status == "success",
            StgRun.is_backtest.is_(False),
            StgRun.algo_code.in_(algo_codes),
        )
        .subquery("runs")
    )

    # 同一策略多次参数 run 命中同一只票只保留一条：名次最优 → 更晚创建的 run
    signals = (
        select(
            runs.c.algo_code,
            runs.c.algo_name,
            StgSignal.code.label("code"),
            StgSignal.name.label("signal_name"),
            StgSignal.rank,
            StgSignal.score,
            StgSignal.metrics,
        )
        .select_from(runs)
        .outerjoin(StgSignal, StgSignal.run_id == runs.c.run_id)
        .distinct(runs.c.algo_code, StgSignal.code)
        .order_by(
            runs.c.algo_code,
            StgSignal.code,
            StgSignal.rank.asc().nullslast(),
            runs.c.created_at.desc().nullslast(),
            runs.c.run_id.desc(),
        )
        .subquery("signals")
    )

    if sector_type:
        in_sector = exists(
            select(1)
            .select_from(DimSectorMember)
            .where(
                DimSectorMember.stock_code == signals.c.code,
                DimSectorMember.sector_type == sector_type,
                DimSectorMember.effective_from <= target,
                or_(
                    DimSectorMember.effective_to.is_(None),
                    DimSectorMember.effective_to > target,
                ),
            )
        )
    else:
        in_sector = literal(True)

    stmt = (
        select(
            signals.c.algo_code,
            signals.c.algo_name,
            signals.c.code,
            signals.c.signal_name,
            signals.c.rank,
            signals.c.score,
            signals.c.metrics,
            DimStock.name.label("stock_name"),
            DimStock.industry_l1,
            DimStock.board,
            DimStock.ownership,
            FactStockDaily.pct_chg,
            FactStockScoreDaily.composite_score,
            in_sector.label("in_sector"),
        )
        .select_from(signals)
        .outerjoin(DimStock, DimStock.code == signals.c.code)
        .outerjoin(
            FactStockDaily,
            and_(
                FactStockDaily.code == signals.c.code,
                FactStockDaily.trade_date == target,
            ),
        )
        .outerjoin(
            FactStockScoreDaily,
            and_(
                FactStockScoreDaily.code == signals.c.code,
                FactStockScoreDaily.trade_date == target,
            ),
        )
    )

    result = await session.execute(stmt)
    rows = [ConsensusSignalRow(**mapping) for mapping in result.mappings()]
    payload = build_consensus(rows, min_count=min_count, limit=limit, offset=offset)
    return {"trade_date": target, **payload}


__all__ = [
    "METRICS_PREVIEW_KEYS",
    "ConsensusSignalRow",
    "build_consensus",
    "consensus_signals",
    "metrics_preview",
]
