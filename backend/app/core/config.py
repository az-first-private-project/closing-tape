"""
GFNA 配置文件
-----------
从环境变量 (.env) 读取所有配置项，提供默认值。
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()


# ==================== 数据库配置 (PostgreSQL) ====================
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DATABASE = os.getenv("POSTGRES_DATABASE", "gfna_db")

# SQLAlchemy 异步数据库连接 URL
# 格式: postgresql+asyncpg://用户名:密码@主机:端口/数据库名
DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DATABASE}"
)

# 同步连接 URL (给 Scrapy 爬虫用)
DATABASE_URL_SYNC = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DATABASE}"
)


# ==================== DeepSeek API 配置 ====================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# API 调用参数
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "300"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))


# ==================== 爬虫配置 ====================
CRAWL_INTERVAL_MINUTES = int(os.getenv("CRAWL_INTERVAL_MINUTES", "60"))
CONCURRENT_REQUESTS = int(os.getenv("CONCURRENT_REQUESTS", "8"))
DOWNLOAD_DELAY = float(os.getenv("DOWNLOAD_DELAY", "2.0"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


# ==================== 服务器配置 ====================
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "60/minute")