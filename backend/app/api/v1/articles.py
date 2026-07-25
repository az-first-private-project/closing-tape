"""
文章相关 API 接口 (v1) — 异步版本
-------------------------------
使用 SQLAlchemy 2.0 异步查询 + PostgreSQL 全文搜索。
"""
import logging
from datetime import datetime, timezone, date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, or_, select, text, desc
from sqlalchemy.orm import joinedload

from app.db.session import get_db
from app.db.models import NewsArticle, NewsSource, CrawlLog
from app.schemas.article import (
    ArticleResponse,
    ArticleDetailResponse,
    PaginatedResponse,
    StatsResponse,
)
from app.services.crawler_service import run_spider, process_unsummarized_articles
from app.core.security import verify_api_key

router = APIRouter(prefix="/api/v1", tags=["articles"])
logger = logging.getLogger(__name__)


# ==================== 新闻列表接口 ====================

@router.get("/articles", response_model=PaginatedResponse)
async def list_articles(
    page: int = Query(1, ge=1, description="页码 (从1开始)"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    source_id: Optional[int] = Query(None, description="按来源筛选"),
    db: AsyncSession = Depends(get_db),
):
    """
    分页获取新闻文章列表
    按发布时间倒序排列，最新的在前面。
    """
    # 构建基础查询
    query = (
        select(NewsArticle, NewsSource.name.label("source_name"))
        .join(NewsSource, NewsArticle.source_id == NewsSource.id)
        .where(NewsArticle.is_processed != 2)  # 排除处理失败的
    )

    if source_id is not None:
        query = query.where(NewsArticle.source_id == source_id)

    # 获取总条数
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页查询
    offset = (page - 1) * page_size
    query = (
        query
        .order_by(desc(NewsArticle.publish_time))
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    # 构建返回数据
    items = []
    for article, source_name in rows:
        items.append(
            ArticleResponse(
                id=article.id,
                title=article.title,
                url=article.url,
                content=article.content[:200] if article.content else None,
                author=article.author,
                publish_time=article.publish_time,
                source_id=article.source_id,
                source_name=source_name,
                summary=article.summary,
                url_hash=article.url_hash,
                crawl_time=article.crawl_time,
                is_processed=article.is_processed,
                sentiment_score=float(article.sentiment_score) if article.sentiment_score else None,
            )
        )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items,
    )


# ==================== 文章详情接口 ====================

@router.get("/articles/{article_id}", response_model=ArticleDetailResponse)
async def get_article_detail(
    article_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    获取单篇文章的完整详情 (包含全文和摘要)
    """
    query = (
        select(NewsArticle, NewsSource.name.label("source_name"))
        .join(NewsSource, NewsArticle.source_id == NewsSource.id)
        .where(NewsArticle.id == article_id)
    )
    result = await db.execute(query)
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail=f"文章 ID={article_id} 不存在")

    article, source_name = row

    return ArticleDetailResponse(
        id=article.id,
        title=article.title,
        url=article.url,
        content=article.content,
        author=article.author,
        publish_time=article.publish_time,
        source_id=article.source_id,
        source_name=source_name,
        summary=article.summary,
        url_hash=article.url_hash,
        crawl_time=article.crawl_time,
        is_processed=article.is_processed,
        sentiment_score=float(article.sentiment_score) if article.sentiment_score else None,
    )


# ==================== 搜索接口 (PostgreSQL 全文搜索) ====================

@router.get("/search", response_model=PaginatedResponse)
async def search_articles(
    q: str = Query(..., min_length=1, max_length=200, description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
):
    """
    搜索新闻 — 使用 PostgreSQL 全文搜索 (tsvector + GIN 索引)

    比 MySQL LIKE 更强大:
    - 支持中英文混合搜索
    - 自动分词匹配
    - GIN 索引加速，大数据量下依然快速

    示例:
        GET /api/v1/search?q=inflation&page=1&page_size=10
        GET /api/v1/search?q=美联储加息&page=1
    """
    # 使用 PostgreSQL 全文搜索
    # plainto_tsquery 会把搜索词转成查询语法
    # to_tsvector 把文本转成词条向量
    fts_condition = text(
        "to_tsvector('simple', COALESCE(news_articles.title, '') || ' ' || "
        "COALESCE(news_articles.content, '')) @@ plainto_tsquery('simple', :query)"
    )

    # 也保留 LIKE 搜索作为回退 (搜索摘要字段)
    like_term = f"%{q}%"
    like_condition = or_(
        NewsArticle.title.like(like_term),
        NewsArticle.content.like(like_term),
        NewsArticle.summary.like(like_term),
    )

    query = (
        select(NewsArticle, NewsSource.name.label("source_name"))
        .join(NewsSource, NewsArticle.source_id == NewsSource.id)
        .where(or_(fts_condition, like_condition))
    )

    # 获取总数
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query, {"query": q})
    total = total_result.scalar() or 0

    # 分页
    offset = (page - 1) * page_size
    query = (
        query
        .order_by(desc(NewsArticle.publish_time))
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query, {"query": q})
    rows = result.all()

    items = []
    for article, source_name in rows:
        items.append(
            ArticleResponse(
                id=article.id,
                title=article.title,
                url=article.url,
                content=article.content[:200] if article.content else None,
                author=article.author,
                publish_time=article.publish_time,
                source_id=article.source_id,
                source_name=source_name,
                summary=article.summary,
                url_hash=article.url_hash,
                crawl_time=article.crawl_time,
                is_processed=article.is_processed,
                sentiment_score=float(article.sentiment_score) if article.sentiment_score else None,
            )
        )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items,
    )


# ==================== 统计接口 ====================

@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """
    获取系统统计信息 — 用于仪表盘展示
    """
    total_articles_result = await db.execute(
        select(func.count(NewsArticle.id))
    )
    total_articles = total_articles_result.scalar() or 0

    total_sources_result = await db.execute(
        select(func.count(NewsSource.id))
    )
    total_sources = total_sources_result.scalar() or 0

    # 今日新增
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    articles_today_result = await db.execute(
        select(func.count(NewsArticle.id))
        .where(NewsArticle.crawl_time >= today_start)
    )
    articles_today = articles_today_result.scalar() or 0

    # 已处理
    processed_result = await db.execute(
        select(func.count(NewsArticle.id))
        .where(NewsArticle.is_processed == 1)
    )
    processed_count = processed_result.scalar() or 0

    return StatsResponse(
        total_articles=total_articles,
        total_sources=total_sources,
        articles_today=articles_today,
        processed_count=processed_count,
    )


# ==================== 爬虫触发接口 (管理员) ====================

@router.post("/crawl/trigger")
async def trigger_crawl(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """
    手动触发爬虫任务 (需要管理员 API Key)
    请求头: X-API-Key: <admin_api_key>
    """
    background_tasks.add_task(run_spider)

    log_entry = CrawlLog(
        source_id=None,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(log_entry)
    await db.commit()

    return {
        "message": "爬虫任务已触发，正在后台运行",
        "log_id": log_entry.id,
    }


@router.post("/crawl/process-summaries")
async def trigger_summary_processing(
    limit: int = Query(10, ge=1, le=100, description="处理文章数上限"),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """
    手动触发 AI 摘要生成 (处理未生成摘要的文章)
    请求头: X-API-Key: <admin_api_key>
    """
    count = await process_unsummarized_articles(db, limit=limit)
    return {
        "message": "AI 摘要处理完成",
        "processed": count,
    }
