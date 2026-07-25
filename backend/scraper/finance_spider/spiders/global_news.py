"""
全球财经新闻爬虫 (global_news.py)
--------------------------------
抓取 Reuters、Bloomberg、CNBC、MarketWatch 的财经新闻。

核心逻辑:
1. 从多个新闻源同时抓取
2. 使用 CSS 选择器提取标题、正文、时间
3. 将数据交给 Pipeline 处理 (去重 + 入库)

每个新闻源定义了不同的 CSS 选择器规则，
因为不同网站的 HTML 结构不同。
"""
import scrapy
import logging
from finance_spider.items import NewsItem

logger = logging.getLogger(__name__)

# ==================== 新闻源配置 ====================
# 每个新闻源需要定义:
#   - name: 来源名称
#   - start_urls: 起始爬取 URL 列表
#   - selectors: CSS 选择器规则 (文章链接/标题/正文/时间)
SOURCES_CONFIG = [
    {
        "name": "Reuters",
        "start_urls": [
            "https://www.reuters.com/business/",
            "https://www.reuters.com/markets/",
        ],
        "selectors": {
            # 文章列表页的链接选择器
            "article_links": "a[href*='/article/'], a[href*='/business/'], a[href*='/markets/']",
            # 详情页的标题选择器
            "title": "h1.article-header__title, h1[class*='headline'], h1",
            # 详情页的正文选择器
            "content": "div.article-body, div[class*='article-body'], article p",
            # 详情页的时间选择器
            "time": "time, span[class*='date'], meta[property='article:published_time']",
            # 作者选择器
            "author": "a[class*='author'], span[class*='author'], meta[name='author']",
        },
    },
    {
        "name": "Bloomberg",
        "start_urls": [
            "https://www.bloomberg.com/markets",
            "https://www.bloomberg.com/economics",
        ],
        "selectors": {
            "article_links": "a[href*='/news/articles/'], a[href*='/news/']",
            "title": "h1, div[class*='headline'] h1, header h1",
            "content": "div.body-content, div[class*='body'], section[class*='body'] p",
            "time": "time, div[class*='timestamp'], meta[name='article:published_time']",
            "author": "a[class*='author'], span[class*='author']",
        },
    },
    {
        "name": "CNBC",
        "start_urls": [
            "https://www.cnbc.com/world/",
            "https://www.cnbc.com/economy/",
        ],
        "selectors": {
            "article_links": "a[href*='/202']",
            "title": "h1.ArticleHeader-headline, h1[class*='title'], h1",
            "content": "div.ArticleBody-articleBody, div[class*='ArticleBody'], div#article_body p",
            "time": "time, div[class*='time'], meta[itemprop='datePublished']",
            "author": "a[class*='author'], span[class*='author'], meta[name='author']",
        },
    },
    {
        "name": "MarketWatch",
        "start_urls": [
            "https://www.marketwatch.com/latest-news",
            "https://www.marketwatch.com/markets",
        ],
        "selectors": {
            "article_links": "a[href*='/story/']",
            "title": "h1.article__headline, h1[class*='headline'], h1",
            "content": "div.article__body, div[class*='article-body'], article p",
            "time": "time, span[class*='timestamp'], meta[name='article:published_time']",
            "author": "span[class*='author'], a[class*='author'], meta[name='author']",
        },
    },
]


class GlobalNewsSpider(scrapy.Spider):
    """
    全球财经新闻爬虫

    同时爬取多个新闻源，自动提取标题、正文、时间等信息。
    """
    name = "global_news"
    allowed_domains = ["reuters.com", "bloomberg.com", "cnbc.com", "marketwatch.com"]

    # 自定义设置
    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS": 8,
    }

    def __init__(self, source=None, *args, **kwargs):
        """
        初始化爬虫

        参数:
            source: 指定爬取来源 (None = 全部来源)
        """
        super().__init__(*args, **kwargs)
        # 如果指定了来源，只爬取对应来源
        if source:
            self.sources_config = [s for s in SOURCES_CONFIG if s["name"].lower() == source.lower()]
            if not self.sources_config:
                logger.warning(f"未找到来源 '{source}'，将爬取全部来源")
                self.sources_config = SOURCES_CONFIG
        else:
            self.sources_config = SOURCES_CONFIG

        logger.info(f"爬虫初始化完成，将爬取 {len(self.sources_config)} 个来源")

    def start_requests(self):
        """
        生成初始请求

        为每个新闻源生成抓取请求，并传递该源的配置信息。
        """
        for source_config in self.sources_config:
            for url in source_config["start_urls"]:
                logger.info(f"开始爬取: {source_config['name']} - {url}")
                yield scrapy.Request(
                    url=url,
                    callback=self.parse_list,
                    meta={"source_config": source_config},
                    errback=self.handle_error,
                )

    def parse_list(self, response):
        """
        解析新闻列表页

        从列表页提取文章链接，然后跳转到详情页抓取全文。
        同时翻页继续抓取更多列表。
        """
        source_config = response.meta["source_config"]
        selectors = source_config["selectors"]
        source_name = source_config["name"]

        # 提取当前页面所有文章链接
        links = response.css(selectors["article_links"])
        logger.info(f"[{source_name}] 列表页发现 {len(links)} 个链接: {response.url}")

        # 限制每页最多处理 20 个链接，避免爬取过多
        processed = 0
        for link in links:
            if processed >= 20:
                break

            # 提取 href 属性
            href = link.css("::attr(href)").get()
            if not href:
                continue

            # 拼接完整 URL (处理相对路径)
            full_url = response.urljoin(href)

            # 过滤非文章链接 (排除搜索、登录、关于等页面)
            if self._is_article_url(full_url):
                processed += 1
                yield scrapy.Request(
                    url=full_url,
                    callback=self.parse_article,
                    meta={
                        "source_config": source_config,
                        "source_name": source_name,
                    },
                    errback=self.handle_error,
                )

        logger.info(f"[{source_name}] 列表页处理完成，提取 {processed} 篇文章链接")

    def parse_article(self, response):
        """
        解析文章详情页

        从详情页提取标题、正文、发布时间、作者等信息。
        """
        source_config = response.meta["source_config"]
        selectors = source_config["selectors"]
        source_name = response.meta["source_name"]

        # ===== 提取标题 =====
        title = self._extract_text(response, selectors["title"])

        # ===== 提取正文 =====
        content = self._extract_content(response, selectors["content"])

        # ===== 提取发布时间 =====
        publish_time = self._extract_text(response, selectors["time"])

        # ===== 提取作者 =====
        author = self._extract_text(response, selectors["author"])

        # 如果没有提取到足够内容，跳过这篇
        if not title or not content or len(content) < 100:
            logger.debug(f"[{source_name}] 内容不足，跳过: {response.url}")
            return

        # 创建 NewsItem
        item = NewsItem(
            title=title,
            url=response.url,
            content=content,
            author=author if author else None,
            publish_time=publish_time if publish_time else None,
            source_name=source_name,
        )

        logger.info(f"[{source_name}] 提取新闻: {title[:60]}...")
        yield item

    def _extract_text(self, response, selector: str) -> str:
        """
        使用 CSS 选择器提取文本内容

        参数:
            response: Scrapy Response 对象
            selector: CSS 选择器字符串

        返回:
            提取的文本 (多个匹配用空格连接)
        """
        try:
            # 尝试多个选择器 (用逗号分隔)
            parts = [s.strip() for s in selector.split(",")]
            for part in parts:
                result = response.css(part + "::text").getall()
                if result:
                    text = " ".join([t.strip() for t in result if t.strip()])
                    if len(text) > 10:  # 至少 10 个有效字符
                        return text
            return ""
        except Exception as e:
            logger.debug(f"提取文本失败 ({selector}): {e}")
            return ""

    def _extract_content(self, response, selector: str) -> str:
        """
        提取文章正文 (多段落拼接)

        参数:
            response: Scrapy Response 对象
            selector: 正文的 CSS 选择器

        返回:
            拼接后的完整正文
        """
        try:
            parts = [s.strip() for s in selector.split(",")]
            for part in parts:
                # 提取所有 <p> 标签内的文本
                paragraphs = response.css(f"{part}::text").getall()
                if not paragraphs:
                    # 尝试提取段落内的文本
                    paragraphs = response.css(f"{part} p::text").getall()

                if paragraphs:
                    content = " ".join([p.strip() for p in paragraphs if p.strip()])
                    if len(content) > 100:
                        return content

            # 如果上述选择器都没提取到，提取页面上所有 <p> 标签
            all_paragraphs = response.css("article p::text, main p::text, div p::text").getall()
            if all_paragraphs:
                content = " ".join([p.strip() for p in all_paragraphs if p.strip()])
                return content

            return ""
        except Exception as e:
            logger.debug(f"提取正文失败: {e}")
            return ""

    def _is_article_url(self, url: str) -> bool:
        """
        判断 URL 是否可能是文章链接

        排除以下类型的链接:
        - 搜索页面
        - 登录页面
        - 关于页面
        - 视频/音频页面
        - 锚点链接
        """
        exclude_keywords = [
            "search", "login", "signup", "about", "contact",
            "video", "audio", "podcast", "newsletter",
            "subscribe", "account", "settings", "help",
            "#", "javascript:", "mailto:",
        ]
        url_lower = url.lower()
        return not any(kw in url_lower for kw in exclude_keywords)

    def handle_error(self, failure):
        """处理请求错误"""
        logger.error(f"请求失败: {failure.request.url}")
        logger.error(f"错误信息: {failure.value}")