"""
GFNA (全球财经新闻聚合器) — FastAPI 主入口 (V2 PostgreSQL 异步版)
==============================================================
启动方式: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
API 文档: http://localhost:8000/docs (Swagger UI)
"""
import sys
import os

# 将 backend 目录添加到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 【Windows 修复】必须在任何 asyncio 相关导入之前设置
if sys.platform == "win32":
    import asyncio as _asyncio
    _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.api.v1.articles import router as articles_router
from app.api.v1.stats import router as stats_router
from app.db.base import Base
from app.db.session import engine
from app.core.config import LOG_LEVEL
from app.services.crawler_service import run_spider, process_unsummarized_articles
import logging

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


# ==================== 应用生命周期 (替代 on_event) ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 异步生命周期管理
    - 启动时: 创建数据库表、启动定时任务
    - 关闭时: 停止定时任务、关闭引擎
    """
    # ===== 启动 =====
    logger.info("=" * 50)
    logger.info("GFNA V2 应用正在启动...")
    logger.info("=" * 50)

    # 创建数据库表
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表已就绪 (已检查/创建)")
    except Exception as e:
        logger.error(f"数据库表创建失败: {e}")
        logger.warning("应用将继续启动，但数据库操作可能会失败")

    # 启动定时任务
    _start_scheduler()
    logger.info("GFNA V2 应用启动完成!")

    yield  # ← 应用运行期间暂停在这里

    # ===== 关闭 =====
    logger.info("GFNA 应用正在关闭...")
    _stop_scheduler()
    await engine.dispose()
    logger.info("数据库引擎已关闭")


# ==================== 创建 FastAPI 应用实例 ====================
app = FastAPI(
    title="GFNA V2 - 全球财经新闻聚合器",
    description=(
        "一个基于 Python 的全栈 Web 应用，自动抓取全球主流财经新闻，"
        "并利用 DeepSeek AI 生成中文摘要。\n\n"
        "**V2 升级:**\n"
        "- 前端: React 18 + Vite + TypeScript + Tailwind CSS + shadcn/ui\n"
        "- 后端: FastAPI (异步) + PostgreSQL 16 + asyncpg\n"
        "- 全文搜索: PostgreSQL tsvector + GIN 索引\n\n"
        "**技术栈:** Python 3.11 + FastAPI + PostgreSQL + Scrapy + DeepSeek"
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,  # 使用新的 lifespan 方式
)

# ==================== CORS 中间件 ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 注册路由 ====================
app.include_router(articles_router)
app.include_router(stats_router)

# ==================== 静态文件服务 ====================
FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend"
)

if os.path.exists(os.path.join(FRONTEND_DIR, "css")):
    app.mount("/static/css", StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")), name="css")
if os.path.exists(os.path.join(FRONTEND_DIR, "js")):
    app.mount("/static/js", StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")), name="js")


# ==================== 根路由 ====================
@app.get("/")
def read_root():
    """返回主页 HTML 文件"""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "GFNA V2 API 运行中", "docs": "/docs"}


# ==================== 健康检查 ====================
@app.get("/health")
async def health_check():
    """健康检查端点"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "ok",
        "service": "GFNA",
        "version": "2.0.0",
        "database": db_status,
    }


# ==================== 定时任务调度 ====================
_scheduler = None


def _start_scheduler():
    """启动 APScheduler 定时任务"""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from app.core.config import CRAWL_INTERVAL_MINUTES

        _scheduler = BackgroundScheduler()

        _scheduler.add_job(
            run_spider,
            trigger="interval",
            minutes=CRAWL_INTERVAL_MINUTES,
            id="crawl_job",
            name="定时爬取新闻",
            replace_existing=True,
        )

        _scheduler.add_job(
            _scheduled_summary_processing,
            trigger="interval",
            minutes=CRAWL_INTERVAL_MINUTES,
            id="summary_job",
            name="批量生成AI摘要",
            replace_existing=True,
        )

        _scheduler.start()
        logger.info(f"定时任务已启动 (爬虫间隔: {CRAWL_INTERVAL_MINUTES} 分钟)")

    except ImportError:
        logger.warning("APScheduler 未安装，定时任务未启用")
    except Exception as e:
        logger.error(f"定时任务启动失败: {e}")


def _stop_scheduler():
    """停止调度器"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("定时任务已停止")


def _scheduled_summary_processing():
    """定时任务: 批量处理未生成摘要的文章"""
    import asyncio
    from app.db.session import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                count = await process_unsummarized_articles(db, limit=20)
                logger.info(f"定时摘要任务完成: {count} 篇")
            except Exception as e:
                logger.error(f"定时摘要任务失败: {e}")

    asyncio.run(_run())


# ==================== 程序入口 ====================
if __name__ == "__main__":
    import uvicorn
    from app.core.config import SERVER_HOST, SERVER_PORT

    logger.info(f"启动开发服务器: http://{SERVER_HOST}:{SERVER_PORT}")
    uvicorn.run(
        "app.main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=True,
        log_level="info",
    )
