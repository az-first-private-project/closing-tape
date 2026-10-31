"""自选股命中查询（V6 T8）。

**纯读查询**，不跑策略、不写库。接口用 POST 是因为自选代码列表可能很长，
语义上等价一次 GET。

口径：
  - 只认当前 `SPECS` 内启用策略、`status='success'` 且 `is_backtest IS FALSE`
    的 run（与共识榜一致，历史 a1/a2 run 不提示）；
  - 近 N 个交易日以 `trade_calendar` 为准，锚定最新成功 run 的交易日；
  - 同一策略同一天多次参数 run 命中同一只票只保留一条（名次最优 → 更晚创建）；
  - 整个查询是一条 SQL，`codes` 走参数绑定，不拼字符串。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParamError

MAX_HITS_CODES = 100
MAX_HITS_DAYS = 60
CODE_PATTERN = re.compile(r"^\d{1,6}$")


def normalize_hits_codes(raw_codes: Iterable[object]) -> list[str]:
    """校验并去重自选代码；任何非法项直接 422。"""
    codes: list[str] = []
    seen: set[str] = set()
    for value in raw_codes:
        if not isinstance(value, str) or CODE_PATTERN.fullmatch(value) is None:
            raise InvalidParamError(
                "codes 必须是 1–6 位数字串",
                detail={"value": value},
            )
        if value in seen:
            continue
        seen.add(value)
        codes.append(value)
    if len(codes) > MAX_HITS_CODES:
        raise InvalidParamError(
            f"codes 去重后最多 {MAX_HITS_CODES} 个，收到 {len(codes)} 个",
            detail={"limit": MAX_HITS_CODES, "count": len(codes)},
        )
    return codes


_HITS_SQL = text(
    """
    WITH recent_days AS (
        SELECT d.cal_date
        FROM (
            SELECT cal_date
            FROM trade_calendar
            WHERE is_open IS TRUE
              AND cal_date <= COALESCE(
                  (SELECT max(trade_date) FROM stg_run
                   WHERE is_backtest IS FALSE AND status = 'success'),
                  CURRENT_DATE
              )
            ORDER BY cal_date DESC
            LIMIT :days
        ) d
    ),
    ranked AS (
        SELECT s.code,
               COALESCE(s.name, s.code) AS name,
               r.algo_code,
               COALESCE(r.algo_name, r.algo_code) AS algo_name,
               r.trade_date AS signal_date,
               s.rank,
               ROW_NUMBER() OVER (
                   PARTITION BY r.algo_code, r.trade_date, s.code
                   ORDER BY s.rank ASC NULLS LAST,
                            r.created_at DESC NULLS LAST,
                            r.run_id DESC
               ) AS rn
        FROM stg_signal s
        JOIN stg_run r ON r.run_id = s.run_id
        WHERE s.code = ANY(CAST(:codes AS text[]))
          AND r.algo_code = ANY(CAST(:algo_codes AS text[]))
          AND r.is_backtest IS FALSE
          AND r.status = 'success'
          AND r.trade_date >= (SELECT min(cal_date) FROM recent_days)
    )
    SELECT code, name, algo_code, algo_name, signal_date, rank
    FROM ranked
    WHERE rn = 1
    ORDER BY signal_date DESC, algo_code ASC, rank ASC NULLS LAST, code ASC
    """
)


async def watchlist_hits(
    session: AsyncSession,
    codes: Sequence[str],
    days: int = 10,
) -> dict[str, Any]:
    """返回一批自选代码近 N 个交易日的策略命中。"""
    if days < 1 or days > MAX_HITS_DAYS:
        raise InvalidParamError(
            f"days 必须在 1–{MAX_HITS_DAYS} 之间，收到 {days}",
            detail={"field": "days", "value": days},
        )
    if not codes:
        return {"items": [], "latest_signal_date": None}

    from app.strategy.strategies import SPECS

    result = await session.execute(
        _HITS_SQL,
        {
            "codes": list(codes),
            "algo_codes": sorted(SPECS),
            "days": days,
        },
    )

    items: list[dict[str, Any]] = []
    latest: date | None = None
    for row in result.mappings():
        signal_date: date = row["signal_date"]
        if latest is None or signal_date > latest:
            latest = signal_date
        items.append(
            {
                "code": row["code"],
                "name": row["name"],
                "algo_code": row["algo_code"],
                "algo_name": row["algo_name"],
                "signal_date": signal_date.isoformat(),
                "rank": row["rank"],
                "hit_id": f"{row['algo_code']}:{signal_date.isoformat()}",
            }
        )

    return {
        "items": items,
        "latest_signal_date": latest.isoformat() if latest is not None else None,
    }


__all__ = [
    "CODE_PATTERN",
    "MAX_HITS_CODES",
    "MAX_HITS_DAYS",
    "normalize_hits_codes",
    "watchlist_hits",
]
