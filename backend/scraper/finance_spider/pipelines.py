"""
Scrapy Pipeline — 数据处理与入库 (PostgreSQL 版本)
-------------------------------------------------
负责:
1. 布隆过滤器去重
2. URL 哈希去重 (PostgreSQL UNIQUE 索引)
3. 数据清洗
4. 数据写入 PostgreSQL 数据库

变更: MySQL → PostgreSQL (使用 psycopg2 同步驱动)
"""
import hashlib
import logging
import re
import os
import math
from datetime import datetime, timezone
from dateutil import parser as date_parser
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from finance_spider.items import NewsItem

logger = logging.getLogger(__name__)


# ==================== 简易布隆过滤器 ====================
class SimpleBloomFilter:
    """
    简易布隆过滤器实现

    特点: 宁可误判 (说"见过"但其实没有)，绝不漏判
    """

    def __init__(self, capacity: int = 100000, error_rate: float = 0.001):
        self.size = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self.hash_count = int(self.size / capacity * math.log(2))
        self.bit_array = [0] * max(self.size, 1)

    def _hashes(self, value: str):
        for i in range(self.hash_count):
            data = f"{value}_{i}".encode("utf-8")
            h = hashlib.sha256(data).hexdigest()
            yield int(h, 16) % max(self.size, 1)

    def add(self, value: str):
        for pos in self._hashes(value):
            self.bit_array[pos] = 1

    def contains(self, value: str) -> bool:
        return all(self.bit_array[pos] == 1 for pos in self._hashes(value))


# ==================== 数据库处理管道 ====================
class GfnaPipeline:
    """GFNA 数据处理管道 (PostgreSQL 版)"""

    def __init__(self):
        self.bloom = None
        self.engine = None
        self.source_cache = {}

    def open_spider(self, spider):
        """爬虫启动时调用"""
        logger.info("Pipeline 初始化: 创建布隆过滤器和 PostgreSQL 连接")

        self.bloom = SimpleBloomFilter(capacity=100000, error_rate=0.001)

        from dotenv import load_dotenv
        load_dotenv()

        # PostgreSQL 同步连接 (Scrapy 是同步框架)
        db_url = (
            f"postgresql://{os.getenv('POSTGRES_USER', 'postgres')}:"
            f"{os.getenv('POSTGRES_PASSWORD', '')}@"
            f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
            f"{os.getenv('POSTGRES_PORT', '5432')}/"
            f"{os.getenv('POSTGRES_DATABASE', 'gfna_db')}"
        )
        self.engine = create_engine(db_url, echo=False, pool_pre_ping=True)

        # 预热布隆过滤器
        self._load_existing_urls()

    def close_spider(self, spider):
        """爬虫关闭时调用"""
        if self.engine:
            self.engine.dispose()
            logger.info("Pipeline 关闭: 数据库连接已释放")

    def _load_existing_urls(self):
        """加载已有的 URL 哈希到布隆过滤器"""
        try:
            with Session(self.engine) as session:
                result = session.execute(text("SELECT url_hash FROM news_articles"))
                count = 0
                for row in result:
                    self.bloom.add(row[0])
                    count += 1
                logger.info(f"布隆过滤器预热完成: 加载了 {count} 条已有记录")
        except Exception as e:
            logger.warning(f"布隆过滤器预热失败 (可能是首次运行): {e}")

    def process_item(self, item: NewsItem, spider):
        """
        处理单条新闻 Item:
        1. 计算 URL 哈希
        2. Bloom Filter 去重
        3. 数据清洗
        4. 写入 PostgreSQL (数据库级去重)
        """
        url = item.get("url", "")
        if not url:
            logger.warning("Item 缺少 URL，丢弃")
            return item

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        item["url_hash"] = url_hash

        # Bloom Filter 去重
        if self.bloom.contains(url_hash):
            logger.debug(f"Bloom Filter 去重: {url[:80]}...")
            return item

        # 数据清洗
        item["content"] = self._clean_content(item.get("content", ""))
        item["title"] = self._clean_title(item.get("title", ""))

        # 解析发布时间
        publish_time_str = item.get("publish_time")
        if publish_time_str:
            try:
                item["publish_time"] = date_parser.parse(str(publish_time_str))
            except Exception:
                item["publish_time"] = datetime.now(timezone.utc)

        # 写入数据库
        self._save_to_database(item)
        self.bloom.add(url_hash)
        return item

    def _clean_content(self, content: str) -> str:
        """清洗新闻正文"""
        if not content:
            return ""
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
        content = re.sub(r"\s+", " ", content).strip()
        if len(content) > 10000:
            content = content[:10000]
        return content

    def _clean_title(self, title: str) -> str:
        """清洗新闻标题"""
        if not title:
            return "无标题"
        title = re.sub(r"<[^>]+>", "", title)
        return re.sub(r"\s+", " ", title).strip()

    def _get_source_id(self, source_name: str) -> int:
        """根据来源名称获取来源 ID (带缓存)"""
        if source_name in self.source_cache:
            return self.source_cache[source_name]

        with Session(self.engine) as session:
            result = session.execute(
                text("SELECT id FROM news_sources WHERE name LIKE :name LIMIT 1"),
                {"name": f"%{source_name}%"}
            )
            row = result.fetchone()
            if row:
                self.source_cache[source_name] = row[0]
                return row[0]
        return 1

    def _save_to_database(self, item: NewsItem):
        """
        将清洗后的数据写入 PostgreSQL

        使用 INSERT ... ON CONFLICT DO NOTHING (PostgreSQL 的 UPSERT)
        替代 MySQL 的 INSERT IGNORE
        """
        try:
            with Session(self.engine) as session:
                source_name = item.get("source_name", "")
                source_id = item.get("source_id") or self._get_source_id(source_name)

                # PostgreSQL 语法: ON CONFLICT DO NOTHING
                insert_sql = text("""
                    INSERT INTO news_articles
                        (source_id, title, url, url_hash, content, author,
                         publish_time, crawl_time)
                    VALUES
                        (:source_id, :title, :url, :url_hash, :content,
                         :author, :publish_time, NOW())
                    ON CONFLICT (url_hash) DO NOTHING
                """)

                session.execute(insert_sql, {
                    "source_id": source_id,
                    "title": item.get("title", "无标题"),
                    "url": item.get("url", ""),
                    "url_hash": item.get("url_hash", ""),
                    "content": item.get("content", ""),
                    "author": item.get("author"),
                    "publish_time": item.get("publish_time", datetime.now(timezone.utc)),
                })
                session.commit()
                logger.debug(f"数据入库: {item.get('title', '')[:50]}...")

        except IntegrityError:
            logger.debug(f"重复数据跳过: {item.get('url', '')[:80]}")
        except Exception as e:
            logger.error(f"数据入库失败: {e}")
