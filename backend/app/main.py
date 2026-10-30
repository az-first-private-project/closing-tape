"""
GFNA V5 FastAPI 入口
====================
启动：
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
文档：
    /docs  /redoc
健康检查：
    /health  → 含数据库状态、最新交易日、数据是否就绪

V5 相对 V4 的变化：
  - 去掉了 V1 静态目录挂载（`frontend/css`、`frontend/js` 早就不存在，只制造 404）
  - 统一异常 → 统一错误响应体（方案 E1）
  - 建表改为"引导脚本 + Alembic"，启动时只做轻量检查（`AUTO_CREATE_TABLES=1` 时保留兜底）
  - 调度器只在 `SCHEDULER_ENABLED=1` 时启动
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager

# 允许 `python -m app.main` 与 `uvicorn app.main:app` 两种方式都能找到包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 【Windows】必须在任何 asyncio 相关导入前设置（项目历史坑：Proactor 下卡死）
if sys.platform == "win32":
    import asyncio as _asyncio
    _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.v1 import api_router  # noqa: E402
from app.core.config import (  # noqa: E402
    AUTO_CREATE_TABLES, CORS_ORIGINS, LOG_LEVEL, SCHEDULER_ENABLED, SERVER_HOST,
    SERVER_PORT,
)
from app.core.errors import AppError  # noqa: E402
from app.core.logging import bind_run, get_logger, setup_logging  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import dispose_engines, engine  # noqa: E402

setup_logging()
logger = get_logger("main")


# ============================================================
# 生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 56)
    logger.info("GFNA V5 启动中 …")
    logger.info("=" * 56)

    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version()"))).scalar()
            logger.info("数据库已连接：%s", str(version).split(",")[0])
    except Exception as exc:                          # noqa: BLE001
        logger.error("数据库连接失败（应用仍会启动，但接口将返回 DB_UNAVAILABLE）：%s", exc)

    if AUTO_CREATE_TABLES:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("已确保 %d 张表存在（AUTO_CREATE_TABLES=1）", len(Base.metadata.tables))
        except Exception as exc:                      # noqa: BLE001
            logger.warning("建表检查失败：%s", exc)

    scheduler = None
    if SCHEDULER_ENABLED:
        from app.worker import start_scheduler
        scheduler = start_scheduler()
    else:
        logger.info("SCHEDULER_ENABLED=0，定时任务未启动")

    logger.info("GFNA V5 启动完成 ✅")
    try:
        yield
    finally:
        logger.info("GFNA V5 关闭中 …")
        if scheduler is not None:
            from app.worker import stop_scheduler
            stop_scheduler()
        await dispose_engines()
        logger.info("已释放数据库连接")


app = FastAPI(
    title="GFNA V5 · A 股行情与量化选股",
    description=(
        "行情采集（AKShare 主 + Baostock 兜底）→ 本地 PostgreSQL 单一事实源 → "
        "指标/因子 → 条件树选股 → 回测。\n\n"
        "所有分析只读本地库，请求路径不访问外部数据源。\n"
        "接口契约见 `docs/V5-API-CONTRACT.md`。"
    ),
    version="5.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 中间件：请求 ID + 访问日志
# ============================================================

@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    with bind_run(request_id=request_id):
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ============================================================
# 异常处理：统一错误体（方案 E1）
# ============================================================

@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    request_id = request.headers.get("X-Request-ID")
    logger.warning("业务异常 [%s] %s: %s", exc.code, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code,
                        content=exc.to_payload(request_id=request_id))


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID")
    logger.exception("未处理异常: %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"},
            "request_id": request_id,
        },
    )


app.include_router(api_router)


# ============================================================
# 根路由与健康检查
# ============================================================

@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": "GFNA",
        "version": "5.0.0",
        "docs": "/docs",
        "frontend": "由 Nginx/Vite 提供，见 docker-compose.yml 的 frontend 服务",
    }


@app.get("/health", tags=["系统"])
async def health() -> dict:
    """健康检查：数据库 + 最新交易日 + 数据就绪度。"""
    db_status = "disconnected"
    latest_trade_date = None
    data_ready = False
    detail: dict = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_status = "connected"
            latest_trade_date = (await conn.execute(
                text("SELECT max(trade_date) FROM fact_stock_daily")
            )).scalar()
            critical = (await conn.execute(text(
                "SELECT count(*) FROM meta_data_quality WHERE status = 'ok'"
            ))).scalar()
            data_ready = bool(latest_trade_date) and bool(critical)
            detail = {"fresh_datasets": int(critical or 0)}
    except Exception as exc:                          # noqa: BLE001
        detail = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "status": "ok",
        "service": "GFNA",
        "version": "5.0.0",
        "database": db_status,
        "latest_trade_date": str(latest_trade_date) if latest_trade_date else None,
        "data_ready": data_ready,
        "detail": detail,
    }


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    logging.getLogger("uvicorn").setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.info("开发服务器：http://%s:%s", SERVER_HOST, SERVER_PORT)
    uvicorn.run("app.main:app", host=SERVER_HOST, port=SERVER_PORT,
                reload=True, log_level=LOG_LEVEL.lower())
