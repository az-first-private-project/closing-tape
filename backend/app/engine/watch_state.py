"""
自选状态读写（V6 T8）
======================
单租户单行存储：同一后端下所有浏览器共享 `app_watchlist_state` 的 id=1 行。
`codes` 与 `seen_hits` 整份覆盖；PUT 时若只传一个字段，另一个保持原值。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParamError
from app.db.base import utcnow
from app.db.models.state import WatchlistState
from app.engine.watch_hits import MAX_HITS_CODES, normalize_hits_codes

MAX_SEEN_HITS = 500
SEEN_PATTERN = re.compile(r"^[a-z0-9_]{1,32}:(\d{4}-\d{2}-\d{2})$", re.IGNORECASE)


def normalize_seen_hits(raw_hits: Iterable[object]) -> list[str]:
    """校验已读命中键 `algo_code:YYYY-MM-DD`；去重保序，最多 500 条。"""
    hits: list[str] = []
    seen: set[str] = set()
    for value in raw_hits:
        if not isinstance(value, str):
            raise InvalidParamError(
                "seen_hits 必须是 algo_code:YYYY-MM-DD 字符串",
                detail={"value": value},
            )
        match = SEEN_PATTERN.fullmatch(value)
        if match is None:
            raise InvalidParamError(
                "seen_hits 必须是 algo_code:YYYY-MM-DD 字符串",
                detail={"value": value},
            )
        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            raise InvalidParamError(
                "seen_hits 的日期不是合法日期",
                detail={"value": value},
            ) from None
        if value in seen:
            continue
        seen.add(value)
        hits.append(value)
    if len(hits) > MAX_SEEN_HITS:
        raise InvalidParamError(
            f"seen_hits 去重后最多 {MAX_SEEN_HITS} 条，收到 {len(hits)} 条",
            detail={"limit": MAX_SEEN_HITS, "count": len(hits)},
        )
    return hits


def _payload(codes: list[str], seen_hits: list[str], updated_at: Any) -> dict[str, Any]:
    return {
        "codes": codes,
        "seen_hits": seen_hits,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


async def load_watchlist_state(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(WatchlistState, 1)
    if row is None:
        return _payload([], [], None)
    return _payload(list(row.codes or []), list(row.seen_hits or []), row.updated_at)


async def save_watchlist_state(
    session: AsyncSession,
    *,
    codes: list[str] | None = None,
    seen_hits: list[str] | None = None,
) -> dict[str, Any]:
    """整份覆盖（未提供的字段保持原值），单条 upsert 完成。"""
    row = await session.get(WatchlistState, 1)
    next_codes = codes if codes is not None else list(row.codes or []) if row else []
    next_seen = seen_hits if seen_hits is not None else list(row.seen_hits or []) if row else []
    now = utcnow()

    stmt = (
        pg_insert(WatchlistState)
        .values(id=1, codes=next_codes, seen_hits=next_seen, updated_at=now)
        .on_conflict_do_update(
            index_elements=[WatchlistState.id],
            set_={"codes": next_codes, "seen_hits": next_seen, "updated_at": now},
        )
    )
    await session.execute(stmt)
    await session.flush()
    return _payload(next_codes, next_seen, now)


__all__ = [
    "MAX_HITS_CODES",
    "MAX_SEEN_HITS",
    "SEEN_PATTERN",
    "load_watchlist_state",
    "normalize_hits_codes",
    "normalize_seen_hits",
    "save_watchlist_state",
]
