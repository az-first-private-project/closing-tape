"""
爬虫调度服务 (异步版本)
-----------------------
负责定时触发 Scrapy 爬虫，以及处理爬取后的 AI 摘要生成。
"""
import logging
import subprocess
import sys
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import NewsArticle, CrawlLog
from app.services.llm_service import generate_summary

logger = logging.getLogger(__name__)


def run_spider(source_name: str = None) -> dict:
    """
    运行 Scrapy 爬虫 (同步，单独进程运行)

    参数:
        source_name: 指定要爬取的来源名称 (None = 全部来源)

    返回:
        dict: {"status": "success"|"failed", "message": str}
    """
    logger.info(f"开始运行爬虫，来源: {source_name or '全部'}")

    try:
        cmd = [
            sys.executable, "-m", "scrapy", "crawl", "global_news",
        ]
        if source_name:
            cmd.extend(["-a", f"source={source_name}"])

        result = subprocess.run(
            cmd,
            cwd="backend/scraper",
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode == 0:
            logger.info(f"爬虫运行成功: {result.stdout[-500:]}")
            return {"status": "success", "message": "爬取完成"}
        else:
            logger.error(f"爬虫运行失败: {result.stderr}")
            return {"status": "failed", "message": result.stderr[:500]}

    except subprocess.TimeoutExpired:
        logger.error("爬虫运行超时 (10分钟)")
        return {"status": "failed", "message": "爬虫运行超时"}
    except Exception as e:
        logger.error(f"爬虫运行异常: {e}")
        return {"status": "failed", "message": str(e)}


async def process_unsummarized_articles(
    db: AsyncSession, limit: int = 10
) -> int:
    """
    处理数据库中尚未生成摘要的文章 (异步版本)

    参数:
        db: 异步数据库会话
        limit: 每次处理的文章数量上限

    返回:
        int: 成功生成摘要的文章数
    """
    # 查询未处理的文章
    result = await db.execute(
        select(NewsArticle)
        .where(NewsArticle.is_processed == 0)
        .where(NewsArticle.content.isnot(None))
        .where(NewsArticle.content != "")
        .order_by(NewsArticle.crawl_time.desc())
        .limit(limit)
    )
    articles = result.scalars().all()

    if not articles:
        logger.info("没有需要生成摘要的文章")
        return 0

    logger.info(f"找到 {len(articles)} 篇待处理的文章")
    success_count = 0

    for article in articles:
        try:
            summary = generate_summary(article.content)

            if summary and summary != "":
                article.summary = summary
                article.is_processed = 1
                success_count += 1
                logger.info(f"文章 {article.id} 摘要生成成功")
            else:
                article.is_processed = 2
                logger.warning(f"文章 {article.id} 摘要生成失败")

        except Exception as e:
            article.is_processed = 2
            logger.error(f"文章 {article.id} 处理异常: {e}")

    await db.commit()
    logger.info(f"批量处理完成: {success_count}/{len(articles)} 篇成功")

    return success_count
