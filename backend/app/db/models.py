"""
ORM 数据模型定义 (PostgreSQL 版本)
----------------------------------
使用 SQLAlchemy 2.0 异步风格定义数据库表的 ORM 映射。

变更 (MySQL → PostgreSQL):
  - DATETIME → TIMESTAMP WITH TIME ZONE (DateTime(timezone=True))
  - TINYINT → SMALLINT
  - DECIMAL → Numeric
  - ENUM → PostgreSQL 原生 ENUM 类型
"""
import hashlib
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Numeric,
    ForeignKey, Enum as SQLEnum, Index, SmallInteger,
)
from sqlalchemy.orm import relationship
from app.db.base import Base


# ==================== 新闻来源模型 ====================
class NewsSource(Base):
    """
    新闻来源表 (news_sources)
    存储每个财经媒体的基本信息。
    """
    __tablename__ = "news_sources"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="来源ID")
    name = Column(String(100), nullable=False, unique=True, comment="来源名称")
    base_url = Column(String(500), nullable=False, comment="基础URL")
    country = Column(String(50), default=None, comment="国家")
    language = Column(String(20), default="en", comment="语言")
    is_active = Column(SmallInteger, default=1, comment="1=启用, 0=停用")
    crawl_interval = Column(Integer, default=60, comment="抓取间隔(分钟)")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

    articles = relationship("NewsArticle", back_populates="source", lazy="dynamic")

    def __repr__(self):
        return f"<NewsSource(id={self.id}, name='{self.name}')>"


# ==================== 新闻文章模型 (核心) ====================
class NewsArticle(Base):
    """
    新闻文章表 (news_articles) — 项目核心表

    每篇爬取的新闻都存储在这个表中。
    去重机制: url_hash 字段设置唯一索引

    全文搜索: PostgreSQL tsvector + GIN 索引
      - 使用 to_tsvector('simple', title || ' ' || content) 进行搜索
      - 'simple' 配置不做语言分析，适合中英文混合场景
    """
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="文章ID")
    source_id = Column(
        Integer,
        ForeignKey("news_sources.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
        comment="来源ID (外键)",
    )
    title = Column(String(500), nullable=False, comment="新闻标题")
    url = Column(String(2048), nullable=False, comment="原文URL")
    url_hash = Column(String(64), nullable=False, unique=True, comment="URL哈希(SHA-256)")
    content = Column(Text, default=None, comment="正文内容")
    summary = Column(Text, default=None, comment="AI中文摘要")
    author = Column(String(200), default=None, comment="作者")
    publish_time = Column(
        DateTime(timezone=True), default=None, comment="新闻发布时间"
    )
    crawl_time = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="爬取时间",
    )
    sentiment_score = Column(
        Numeric(3, 2), default=None, comment="情感分数(预留)"
    )
    is_processed = Column(
        SmallInteger, default=0, comment="摘要状态: 0=未处理, 1=已完成, 2=失败"
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )

    source = relationship("NewsSource", back_populates="articles")

    __table_args__ = (
        Index("idx_articles_publish_time", "publish_time"),
        Index("idx_articles_crawl_time", "crawl_time"),
        Index("idx_articles_source_id", "source_id"),
        Index("idx_articles_is_processed", "is_processed"),
    )

    @staticmethod
    def hash_url(url: str) -> str:
        """计算 URL 的 SHA-256 哈希值"""
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def __repr__(self):
        return f"<NewsArticle(id={self.id}, title='{self.title[:30]}...')>"


# ==================== 爬虫日志模型 ====================
class CrawlLog(Base):
    """
    爬虫日志表 (crawl_logs)
    记录每次爬虫运行的情况，方便调试和监控。
    """
    __tablename__ = "crawl_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="日志ID")
    source_id = Column(Integer, default=None, comment="来源ID")
    status = Column(
        SQLEnum("running", "success", "failed", name="crawl_status"),
        default="running",
        comment="爬取状态",
    )
    articles_found = Column(Integer, default=0, comment="发现文章数")
    articles_new = Column(Integer, default=0, comment="新增文章数(去重后)")
    error_message = Column(Text, default=None, comment="错误信息")
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        comment="开始时间",
    )
    finished_at = Column(
        DateTime(timezone=True), default=None, comment="结束时间"
    )

    def __repr__(self):
        return f"<CrawlLog(id={self.id}, status='{self.status}')>"
