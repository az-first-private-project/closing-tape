"""
爬虫调度服务（V5 修正）
======================
负责触发 Scrapy 爬虫、批量生成摘要与**新闻情感判定**。

V5 修复：
  - `cwd="backend/scraper"` 是相对路径，从 backend 目录启动时必然找不到 → 改为绝对路径
  - 摘要与情感判定分离：摘要失败不阻断情感判定（反之亦然）
  - 新增 `process_news_sentiment`：把 `NewsArticle.sentiment_score` 真正写进去，
    这是「情绪因子」唯一的新闻输入（V4 之前该列永远是 NULL）
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import BACKEND_DIR
from app.core.logging import get_logger
from app.db.models import CrawlLog, NewsArticle
from app.services.llm_service import generate_summary, judge_sentiment_batch

logger = get_logger(__name__)

SCRAPER_DIR = BACKEND_DIR / "scraper"
SPIDER_TIMEOUT_SEC = 900


def run_spider(source_name: str | None = None) -> dict:
    """运行 Scrapy 爬虫（同步子进程；Scrapy 的 Twisted reactor 不适合嵌进事件循环）。"""
    logger.info("开始运行爬虫，来源: %s", source_name or "全部")
    if not SCRAPER_DIR.exists():
        return {"status": "failed",
                "message": f"爬虫目录不存在: {SCRAPER_DIR}（新闻为可选链路，不影响量化主链）"}

    cmd = [sys.executable, "-m", "scrapy", "crawl", "global_news"]
    if source_name:
        cmd.extend(["-a", f"source={source_name}"])

    try:
        result = subprocess.run(
            cmd, cwd=str(SCRAPER_DIR), capture_output=True, text=True,
            timeout=SPIDER_TIMEOUT_SEC, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            logger.info("爬虫完成: %s", (result.stdout or "")[-300:])
            return {"status": "success", "message": "爬取完成"}
        logger.error("爬虫失败: %s", (result.stderr or "")[:500])
        return {"status": "failed", "message": (result.stderr or "")[:500]}
    except subprocess.TimeoutExpired:
        logger.error("爬虫超时 (%ds)", SPIDER_TIMEOUT_SEC)
        return {"status": "failed", "message": "爬虫运行超时"}
    except Exception as exc:                            # noqa: BLE001
        logger.error("爬虫异常: %s", exc)
        return {"status": "failed", "message": str(exc)}


async def process_unsummarized_articles(db: AsyncSession, limit: int = 10) -> int:
    """为未生成摘要的文章补摘要（is_processed: 0=未处理 1=完成 2=失败）。"""
    rows = (await db.execute(
        select(NewsArticle)
        .where(NewsArticle.is_processed == 0)
        .where(NewsArticle.content.isnot(None))
        .where(NewsArticle.content != "")
        .order_by(NewsArticle.crawl_time.desc())
        .limit(limit)
    )).scalars().all()

    if not rows:
        logger.info("没有待生成摘要的文章")
        return 0

    ok = 0
    for article in rows:
        try:
            summary = generate_summary(article.content)
            if summary:
                article.summary = summary
                article.is_processed = 1
                ok += 1
            else:
                article.is_processed = 2
        except Exception as exc:                        # noqa: BLE001
            article.is_processed = 2
            logger.error("文章 %s 摘要失败: %s", article.id, exc)

    await db.commit()
    logger.info("摘要完成 %d/%d 篇", ok, len(rows))
    return ok


async def process_news_sentiment(db: AsyncSession, limit: int = 30) -> int:
    """为已摘要但未判定情感的文章批量写 `sentiment_score`（-1..1）。

    这是「新闻 → 情绪因子」的唯一入口。失败不影响主链。
    """
    rows = (await db.execute(
        select(NewsArticle)
        .where(NewsArticle.is_processed == 1)
        .where(NewsArticle.sentiment_score.is_(None))
        .order_by(NewsArticle.crawl_time.desc())
        .limit(limit)
    )).scalars().all()

    if not rows:
        return 0

    payload = [{"title": a.title, "summary": a.summary} for a in rows]
    scores = judge_sentiment_batch(payload)

    updated = 0
    for article, score in zip(rows, scores):
        if score is None:
            continue
        article.sentiment_score = round(score, 3)
        updated += 1

    await db.commit()
    logger.info("新闻情感判定完成 %d/%d 篇", updated, len(rows))
    return updated


async def record_crawl_log(db: AsyncSession, status: str, found: int = 0,
                           new: int = 0, error: str | None = None) -> None:
    """写爬虫运行日志（crawl_logs）。"""
    db.add(CrawlLog(
        status="success" if status == "success" else "failed",
        articles_found=found,
        articles_new=new,
        error_message=error,
        finished_at=datetime.now(timezone.utc),
    ))
    await db.commit()


__all__ = ["run_spider", "process_unsummarized_articles", "process_news_sentiment",
           "record_crawl_log", "SCRAPER_DIR"]
