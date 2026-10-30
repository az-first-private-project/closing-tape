"""API 路由聚合。新增模块只需在这里注册一次。"""
from fastapi import APIRouter

from app.api.v1 import admin, algo, articles, backtest, market, meta, stats, stocks

api_router = APIRouter()
api_router.include_router(articles.router)
api_router.include_router(stats.router)
api_router.include_router(market.router)
api_router.include_router(stocks.router)
api_router.include_router(algo.router)
api_router.include_router(backtest.router)
api_router.include_router(meta.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
