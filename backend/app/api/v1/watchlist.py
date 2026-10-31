"""
自选状态 API（V6 T8）
=====================
GET /api/v1/watchlist        读取单租户自选名单与已读水位
PUT /api/v1/watchlist        整份覆盖（字段可选，未传保持原值）

无账号体系：后端只有一行状态（id=1），同一后端下所有浏览器共享。
前端 localStorage 仅作缓存，清站点数据/隐私模式后从本接口恢复。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.engine.watch_state import (
    load_watchlist_state,
    normalize_hits_codes,
    normalize_seen_hits,
    save_watchlist_state,
)

router = APIRouter(prefix="/api/v1/watchlist", tags=["自选"])


class WatchlistStateBody(BaseModel):
    """自选状态写入体；两个字段都可选，省略表示保持原值。"""

    codes: list[str] | None = Field(default=None, description="1–6 位数字代码，去重后 ≤100")
    seen_hits: list[str] | None = Field(
        default=None, description="algo_code:YYYY-MM-DD，去重后 ≤500"
    )


@router.get("")
async def get_watchlist_state(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await load_watchlist_state(db)


@router.put("")
async def put_watchlist_state(
    body: WatchlistStateBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    codes = normalize_hits_codes(body.codes) if body.codes is not None else None
    seen_hits = normalize_seen_hits(body.seen_hits) if body.seen_hits is not None else None
    return await save_watchlist_state(db, codes=codes, seen_hits=seen_hits)
