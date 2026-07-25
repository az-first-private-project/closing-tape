"""
Scrapy 爬虫设置文件
------------------
配置爬虫的行为：并发数、下载延迟、中间件、Pipeline 等。
"""
import sys
import os

# 将 backend 目录添加到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

# ==================== 爬虫名称 ====================
BOT_NAME = "finance_spider"

# ==================== 爬虫模块 ====================
SPIDER_MODULES = ["finance_spider.spiders"]
NEWSPIDER_MODULE = "finance_spider.spiders"

# ==================== 礼貌爬取 ====================
# User-Agent (会被 middlewares.py 中的轮换覆盖)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"

# 遵守 robots.txt (设为 True 表示遵守)
ROBOTSTXT_OBEY = False

# 并发请求数
CONCURRENT_REQUESTS = 8

# 下载延迟 (秒) — 避免给目标服务器造成压力
DOWNLOAD_DELAY = 2.0

# 每个域的并发请求数
CONCURRENT_REQUESTS_PER_DOMAIN = 4

# ==================== Cookie 与重试 ====================
# 禁用 Cookies (新闻网站通常不需要登录)
COOKIES_ENABLED = False

# 重试次数
RETRY_ENABLED = True
RETRY_TIMES = 3

# 重试的 HTTP 状态码
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# ==================== 下载超时 ====================
DOWNLOAD_TIMEOUT = 30  # 单个请求超时时间 (秒)

# ==================== 中间件 ====================
DOWNLOADER_MIDDLEWARES = {
    # 自定义中间件: User-Agent 轮换
    "finance_spider.middlewares.UserAgentRotationMiddleware": 400,
}

# ==================== Item Pipeline ====================
ITEM_PIPELINES = {
    # 去重 + 保存到 MySQL 数据库
    "finance_spider.pipelines.GfnaPipeline": 300,
}

# ==================== 其他设置 ====================
# 日志级别
LOG_LEVEL = "INFO"

# 输出编码
FEED_EXPORT_ENCODING = "utf-8"

# 最大爬取深度 (跟随链接的层数)
DEPTH_LIMIT = 2

# ==================== 自定义设置 ====================
# Bloom Filter 容量 (去重用)
BLOOM_FILTER_CAPACITY = 100000
BLOOM_FILTER_ERROR_RATE = 0.001  # 0.1% 误判率