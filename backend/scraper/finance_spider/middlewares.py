"""
Scrapy 中间件
------------
自定义下载中间件，在请求发出前进行处理。

当前实现:
- UserAgentRotationMiddleware: 每次请求随机选择一个 User-Agent
  这样做的好处:
  1. 降低被目标网站反爬虫机制识别的概率
  2. 模拟正常用户浏览行为
"""
import random
import logging

logger = logging.getLogger(__name__)

# User-Agent 轮换池
# 包含主流浏览器的最新 UA，模拟真实用户
USER_AGENTS = [
    # Chrome 120 on Windows 10
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome 120 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox 120 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Firefox 120 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Safari 17 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    # Edge 120 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class UserAgentRotationMiddleware:
    """
    User-Agent 轮换中间件

    每次 Scrapy 发出请求前，随机选择一个 User-Agent 设置到请求头中。
    这样目标网站看到的是来自不同浏览器的请求，降低被封禁的风险。

    工作原理:
    1. Scrapy 引擎准备发送请求
    2. 中间件拦截请求
    3. 随机选择一个 User-Agent
    4. 设置到请求头的 User-Agent 字段
    5. 继续发送请求
    """

    def process_request(self, request, spider):
        """
        在每次请求发出前，随机选择一个 User-Agent

        参数:
            request: Scrapy Request 对象
            spider: 当前运行的爬虫实例
        """
        # 随机选择
        ua = random.choice(USER_AGENTS)
        # 设置到请求头
        request.headers["User-Agent"] = ua
        logger.debug(f"使用 User-Agent: {ua[:60]}...")

        return None  # 返回 None 表示继续处理 (不拦截)