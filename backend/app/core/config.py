"""
GFNA V5 配置中心
=================
所有配置项集中在此，来源优先级：环境变量 > backend/.env > 默认值。

设计原则（V5）：
  1. **不 import 任何重依赖**（pandas/akshare），保证配置层可被任何模块安全引用；
  2. 所有可调参数（评分权重、回测成本、限频）都在这里，避免散落在业务代码里；
  3. 时区显式化：全系统统一 Asia/Shanghai，存储统一 TIMESTAMPTZ。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# ==================== 路径 ====================
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent

# 允许 backend/.env 与项目根 .env 同时存在，先加载根、再加载 backend（后者覆盖前者）
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env", override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = _env(key, "1" if default else "0").lower()
    return val in {"1", "true", "yes", "on"}


# ==================== 数据库 ====================
POSTGRES_HOST = _env("POSTGRES_HOST", "localhost")
POSTGRES_PORT = _env("POSTGRES_PORT", "5432")
POSTGRES_USER = _env("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD", "")
POSTGRES_DATABASE = _env("POSTGRES_DATABASE", "gfna_db")

DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DATABASE}"
)
DATABASE_URL_SYNC = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DATABASE}"
)
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 10)
DB_ECHO = _env_bool("DB_ECHO", False)


# ==================== 时区 / 交易日 ====================
TIMEZONE = "Asia/Shanghai"
# 历史深度：近 5 年（方案 10.1 定稿）。分区年份见 db/views.py
HISTORY_START = date.fromisoformat(_env("HISTORY_START", "2021-01-01"))
# 盘中快照的"收盘后"判定时刻：15:30 之后抓当日快照才算完整个交易日
SNAPSHOT_READY_HOUR = _env_int("SNAPSHOT_READY_HOUR", 15)
SNAPSHOT_READY_MINUTE = _env_int("SNAPSHOT_READY_MINUTE", 30)


# ==================== 数据源 / 合规限频 ====================
# 主源 akshare，兜底 baostock（方案 5：AKShare 主采集 + Baostock 兜底校验）
DATA_PRIMARY_PROVIDER = _env("DATA_PRIMARY_PROVIDER", "akshare")
DATA_FALLBACK_PROVIDER = _env("DATA_FALLBACK_PROVIDER", "baostock")
# 全市场快照 ≤ 1 次/分钟
SNAPSHOT_MIN_INTERVAL_SEC = _env_float("SNAPSHOT_MIN_INTERVAL_SEC", 60.0)
# 全市场快照/资金流是分页抓取（约 59 页），慢网下单次需 60–120s；
# 沿用个股请求的 30s 超时会把正常抓取误判为失败（实测 2026-11-02）
SNAPSHOT_FETCH_TIMEOUT_SEC = _env_float("SNAPSHOT_FETCH_TIMEOUT_SEC", 180.0)
# 单板块历史拉取的墙钟上限：akshare 内部 requests 没有 timeout，
# 一次挂死会让整条板块日更（乃至当日流水线）永久阻塞
SECTOR_BOARD_TIMEOUT_SEC = _env_float("SECTOR_BOARD_TIMEOUT_SEC", 60.0)
# 历史回补 ≤ 2 请求/秒
HISTORY_REQUESTS_PER_SEC = _env_float("HISTORY_REQUESTS_PER_SEC", 2.0)
# 单次 HTTP/接口调用超时与重试
FETCH_TIMEOUT_SEC = _env_float("FETCH_TIMEOUT_SEC", 30.0)
FETCH_MAX_RETRIES = _env_int("FETCH_MAX_RETRIES", 3)
FETCH_BACKOFF_BASE_SEC = _env_float("FETCH_BACKOFF_BASE_SEC", 1.5)
# 固定 UA（方案 5 合规边界：固定 UA、不实现反爬绕过）
HTTP_USER_AGENT = _env(
    "HTTP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 GFNA/5.0 (personal research)",
)
# 是否允许联网采集（离线开发/CI 置 0，全链路走 fixture）
INGEST_NETWORK_ENABLED = _env_bool("INGEST_NETWORK_ENABLED", True)


# ==================== 数据质量阈值（方案 A9）====================
QUALITY_MIN_UNIVERSE = _env_int("QUALITY_MIN_UNIVERSE", 4000)      # 全市场最少标的数
QUALITY_MIN_SNAPSHOT_ROWS = _env_int("QUALITY_MIN_SNAPSHOT_ROWS", 3500)
QUALITY_MIN_COVERAGE = _env_float("QUALITY_MIN_COVERAGE", 0.95)     # 覆盖率下限
# 估值覆盖率下限：低于此值说明财报缓存大面积缺失，PE/PB 因子不可用（记 stale）
QUALITY_MIN_VALUATION_COVERAGE = _env_float("QUALITY_MIN_VALUATION_COVERAGE", 0.80)
QUALITY_MAX_MISSING_RATIO = _env_float("QUALITY_MAX_MISSING_RATIO", 0.05)
QUALITY_MAX_ABS_PCT_CHG = _env_float("QUALITY_MAX_ABS_PCT_CHG", 30.0)  # 北交所 30% 涨跌停
QUALITY_PRICE_JUMP_RATIO = _env_float("QUALITY_PRICE_JUMP_RATIO", 0.5)  # 单日跳变 >50% 视为异常


# ==================== 评分权重（方案 C4，配置化）====================
WEIGHT_TECHNICAL = _env_float("WEIGHT_TECHNICAL", 0.40)
WEIGHT_SENTIMENT = _env_float("WEIGHT_SENTIMENT", 0.35)
WEIGHT_NEWS = _env_float("WEIGHT_NEWS", 0.25)

# 情绪面四子项权重（融资 / 北向 / 新闻情绪 / 资金流）
SENTIMENT_WEIGHTS = {
    "margin": _env_float("SENT_WEIGHT_MARGIN", 0.25),
    "northward": _env_float("SENT_WEIGHT_NORTH", 0.25),
    "news": _env_float("SENT_WEIGHT_NEWS", 0.25),
    "moneyflow": _env_float("SENT_WEIGHT_FLOW", 0.25),
}
# 无数据时的中性分（方案要求：缺数据不拉低也不抬高，但要能看出"没数据"）
NEUTRAL_SCORE = 5.0


# ==================== 回测默认参数（方案 D4/D5 + 用户定稿）====================
@dataclass(frozen=True)
class BacktestConfig:
    """回测口径。改这里即可改全系统默认值。"""

    rebalance_days: int = _env_int("BT_REBALANCE_DAYS", 20)   # 每 20 个交易日调仓
    max_positions: int = _env_int("BT_MAX_POSITIONS", 20)     # 持仓 20 只
    position_sizing: str = "equal"                            # 等权
    benchmark_code: str = _env("BT_BENCHMARK", "sh000300")    # 基准：沪深300
    commission_rate: float = _env_float("BT_COMMISSION", 0.0001)    # 佣金 万1（双边，全佣）
    stamp_tax_rate: float = _env_float("BT_STAMP_TAX", 0.0005)      # 印花税 万5（仅卖出）
    slippage_rate: float = _env_float("BT_SLIPPAGE", 0.001)         # 滑点 千1
    min_commission: float = _env_float("BT_MIN_COMMISSION", 5.0)    # 最低 5 元佣金
    initial_cash: float = _env_float("BT_INITIAL_CASH", 1_000_000.0)
    exclude_st: bool = True
    exclude_new_days: int = _env_int("BT_EXCLUDE_NEW_DAYS", 60)      # 上市 60 日内不买
    limit_up_pct: float = 0.095   # 判定涨停的容差（主板 10%，留 0.5% 余量）
    limit_down_pct: float = -0.095

    def as_dict(self) -> dict:
        return {
            "rebalance_days": self.rebalance_days,
            "max_positions": self.max_positions,
            "position_sizing": self.position_sizing,
            "benchmark_code": self.benchmark_code,
            "commission_rate": self.commission_rate,
            "stamp_tax_rate": self.stamp_tax_rate,
            "slippage_rate": self.slippage_rate,
            "initial_cash": self.initial_cash,
            "exclude_st": self.exclude_st,
        }


BACKTEST = BacktestConfig()

# ==================== 纸面组合（V6 T11）====================
# 空 = 全部现存策略；env 逗号分隔覆盖，例如 PAPER_ENABLED_ALGOS=a3,b1
PAPER_ENABLED_ALGOS: tuple[str, ...] = tuple(
    item.strip().lower()
    for item in _env("PAPER_ENABLED_ALGOS", "").split(",")
    if item.strip()
)

# 策略默认参数（A1/A2 已下线，B1–B5 为短线策略）
STRATEGY_DEFAULTS = {
    "a3": {"limit": 20, "min_score": 6.0},     # 0–10 分制（不是 0–100！）
    "a4": {"limit": 20, "pe_max": 15.0, "div_min": 4.0},
    "b1": {"limit": 20, "min_vol_ratio": 1.0, "min_amount": 2.0e8},
    "b2": {
        "limit": 20, "rsi_max": 20.0, "amp_min": 15.0,
        "trend_dev": 0.9, "min_amount": 2.0e8,
    },
    "b3": {"limit": 20, "k_max": 30.0, "j_max": 10.0, "min_amount": 2.0e8},
    "b4": {
        "limit": 20, "squeeze_pct": 20.0, "vol_mult": 1.5,
        "range_max": 10.0, "min_amount": 2.0e8,
    },
    "b5": {"limit": 20, "j_max": 0.0, "dev_max": 8.0, "min_amount": 2.0e8},
}


# ==================== LLM ====================
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat")
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 300)
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.3)
LLM_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", 3)
LLM_ENABLED = _env_bool("LLM_ENABLED", bool(DEEPSEEK_API_KEY))


# ==================== 爬虫（新闻链路，作为情绪因子输入）====================
CRAWL_INTERVAL_MINUTES = _env_int("CRAWL_INTERVAL_MINUTES", 60)
# 新闻入库上限：单轮最多新增 N 条，库内最多保留 M 条（超出淘汰最旧的）
NEWS_MAX_NEW_PER_RUN = _env_int("NEWS_MAX_NEW_PER_RUN", 50)
NEWS_MAX_ARTICLES = _env_int("NEWS_MAX_ARTICLES", 1000)
CONCURRENT_REQUESTS = _env_int("CONCURRENT_REQUESTS", 8)
DOWNLOAD_DELAY = _env_float("DOWNLOAD_DELAY", 2.0)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


# ==================== 服务 ====================
SERVER_PORT = _env_int("SERVER_PORT", 8000)
SERVER_HOST = _env("SERVER_HOST", "0.0.0.0")
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
LOG_JSON = _env_bool("LOG_JSON", False)
ADMIN_API_KEY = _env("ADMIN_API_KEY", "")
API_RATE_LIMIT = _env("API_RATE_LIMIT", "60/minute")
# 自动调度开关：CI/测试置 0，避免后台任务干扰
SCHEDULER_ENABLED = _env_bool("SCHEDULER_ENABLED", True)
AUTO_CREATE_TABLES = _env_bool("AUTO_CREATE_TABLES", True)
# 启动补跑：宿主机休眠/进程未运行导致日更整日跳过时，启动后按历史口径补齐缺口
CATCH_UP_ENABLED = _env_bool("CATCH_UP_ENABLED", True)
CATCH_UP_MAX_DAYS = _env_int("CATCH_UP_MAX_DAYS", 5)
# 每次启动回看最近 N 个开市日的行数，识别被后续完整日掩盖的残缺日
CATCH_UP_SCAN_DAYS = _env_int("CATCH_UP_SCAN_DAYS", 5)
CATCH_UP_DELAY_SEC = _env_float("CATCH_UP_DELAY_SEC", 15.0)

# 允许的 CORS 来源（默认放开给本地开发；生产用环境变量收紧）
CORS_ORIGINS = [
    o.strip() for o in _env("CORS_ORIGINS", "*").split(",") if o.strip()
]

__all__ = [
    "DATABASE_URL", "DATABASE_URL_SYNC", "TIMEZONE", "BACKTEST",
    "STRATEGY_DEFAULTS", "BACKEND_DIR", "PROJECT_DIR",
]
