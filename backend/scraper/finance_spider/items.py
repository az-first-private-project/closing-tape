"""
Scrapy Item 定义
----------------
定义爬虫抓取的数据结构。Item 就像"快递单"，
规定了每个抓取结果必须包含哪些字段。
"""
import scrapy


class NewsItem(scrapy.Item):
    """财经新闻数据项"""

    # 新闻标题 (必需)
    title = scrapy.Field()

    # 新闻原文 URL (必需，用于去重)
    url = scrapy.Field()

    # URL 的 SHA-256 哈希 (在 Pipeline 中计算)
    url_hash = scrapy.Field()

    # 新闻正文 (清洗后的纯文本)
    content = scrapy.Field()

    # 作者名称 (可选)
    author = scrapy.Field()

    # 新闻发布时间 (字符串格式，后续转换为 datetime)
    publish_time = scrapy.Field()

    # 来源名称 (如 "Reuters", "Bloomberg")
    source_name = scrapy.Field()

    # 来源 ID (对应 news_sources 表的主键，在 Pipeline 中查询)
    source_id = scrapy.Field()