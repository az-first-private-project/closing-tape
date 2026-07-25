"""
统计仪表盘 API (v1) — 新增
---------------------------
提供数据可视化所需的后端统计接口。
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, text, desc

from app.db.session import get_db
from app.db.models import NewsArticle, NewsSource

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])
logger = logging.getLogger(__name__)


@router.get("/daily-trend")
async def get_daily_trend(
    days: int = Query(30, ge=1, le=90, description="统计天数"),
    db: AsyncSession = Depends(get_db),
):
    """
    每日新增文章趋势

    返回最近 N 天每天新增的文章数，用于折线图/面积图展示。

    示例:
        GET /api/v1/stats/daily-trend?days=30

    返回:
    ```json
    {
      "days": 30,
      "data": [
        {"date": "2025-06-01", "count": 25},
        {"date": "2025-06-02", "count": 18},
        ...
      ]
    }
    ```
    """
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    # PostgreSQL 使用 date_trunc 按天分组
    query = (
        select(
            func.date_trunc("day", NewsArticle.crawl_time).label("day"),
            func.count(NewsArticle.id).label("count"),
        )
        .where(NewsArticle.crawl_time >= start_date)
        .group_by(text("day"))
        .order_by(text("day"))
    )

    result = await db.execute(query)
    rows = result.all()

    data = [
        {"date": str(row.day.date()), "count": row.count}
        for row in rows
    ]

    return {"days": days, "data": data}


@router.get("/source-distribution")
async def get_source_distribution(
    db: AsyncSession = Depends(get_db),
):
    """
    新闻来源分布

    返回各来源的文章数量占比，用于饼图展示。

    示例:
        GET /api/v1/stats/source-distribution

    返回:
    ```json
    {
      "data": [
        {"name": "Reuters", "count": 350, "percentage": 35.0},
        {"name": "Bloomberg", "count": 280, "percentage": 28.0},
        ...
      ],
      "total": 1000
    }
    ```
    """
    query = (
        select(
            NewsSource.name.label("name"),
            func.count(NewsArticle.id).label("count"),
        )
        .join(NewsArticle, NewsArticle.source_id == NewsSource.id)
        .group_by(NewsSource.name)
        .order_by(desc(text("count")))
    )

    result = await db.execute(query)
    rows = result.all()

    total = sum(row.count for row in rows)

    data = [
        {
            "name": row.name,
            "count": row.count,
            "percentage": round(row.count / total * 100, 1) if total > 0 else 0,
        }
        for row in rows
    ]

    return {"data": data, "total": total}


@router.get("/keywords")
async def get_keywords(
    limit: int = Query(20, ge=5, le=50, description="返回关键词数量"),
    db: AsyncSession = Depends(get_db),
):
    """
    热门关键词 (从文章标题中提取)

    使用 PostgreSQL ts_stat 函数从 tsvector 中统计词频。

    示例:
        GET /api/v1/stats/keywords?limit=20
    """
    # ts_stat 函数: 从 tsvector 列中统计每个词的出现次数
    query = text(
        """
        SELECT word, ndoc AS count
        FROM ts_stat(
            'SELECT to_tsvector(''simple'', title) FROM news_articles'
        )
        WHERE length(word) > 2
        ORDER BY ndoc DESC
        LIMIT :limit
        """
    )

    result = await db.execute(query, {"limit": limit})
    rows = result.all()

    keywords = [
        {"word": row.word, "count": row.count}
        for row in rows
    ]

    return {"keywords": keywords}


@router.get("/processing-status")
async def get_processing_status(
    db: AsyncSession = Depends(get_db),
):
    """
    AI 处理进度

    返回摘要生成的整体进度。

    返回:
    ```json
    {
      "total": 1000,
      "processed": 820,
      "unprocessed": 150,
      "failed": 30,
      "percentage": 82.0
    }
    ```
    """
    total_result = await db.execute(
        select(func.count(NewsArticle.id))
    )
    total = total_result.scalar() or 0

    processed_result = await db.execute(
        select(func.count(NewsArticle.id))
        .where(NewsArticle.is_processed == 1)
    )
    processed = processed_result.scalar() or 0

    unprocessed_result = await db.execute(
        select(func.count(NewsArticle.id))
        .where(NewsArticle.is_processed == 0)
    )
    unprocessed = unprocessed_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(NewsArticle.id))
        .where(NewsArticle.is_processed == 2)
    )
    failed = failed_result.scalar() or 0

    percentage = round(processed / total * 100, 1) if total > 0 else 0

    return {
        "total": total,
        "processed": processed,
        "unprocessed": unprocessed,
        "failed": failed,
        "percentage": percentage,
    }
