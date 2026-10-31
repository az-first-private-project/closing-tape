"""
ORM 模型聚合导出
=================
`Base.metadata` 只有在模型类被 import 后才包含表定义，
因此这里必须**显式**导入所有模型（顺序无关，但不能漏）。
"""
from app.db.models.dim import (
    DimSectorMember, DimStock, DimStockDividend, DimStockFinancial,
)
from app.db.models.fact import (
    DailyReport, FactFeatureDaily, FactMoneyflow, FactSectorDaily, FactStockDaily,
    FactStockScoreDaily, MarginTrading, MarketIndex, MarketTemperatureDaily,
    SectorScore,
)
from app.db.models.meta import AlgoMeta, IngestJob, MetaDataQuality, TradeCalendar
from app.db.models.news import CrawlLog, NewsArticle, NewsSource
from app.db.models.state import WatchlistState
from app.db.models.strategy import BacktestRun, StgRun, StgSignal, StgSignalPerf

__all__ = [
    # 新闻
    "NewsSource", "NewsArticle", "CrawlLog",
    # 维度
    "DimStock", "DimSectorMember", "DimStockFinancial", "DimStockDividend",
    # 事实
    "FactStockDaily", "FactSectorDaily", "FactFeatureDaily", "FactStockScoreDaily",
    "FactMoneyflow", "MarketIndex", "MarginTrading", "SectorScore",
    "MarketTemperatureDaily", "DailyReport",
    # 元数据
    "TradeCalendar", "MetaDataQuality", "IngestJob", "AlgoMeta",
    # 策略
    "StgRun", "StgSignal", "StgSignalPerf", "BacktestRun",
    # 应用状态
    "WatchlistState",
]
