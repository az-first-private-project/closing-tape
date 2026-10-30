"""新闻链路 ORM 模型（保留，作为情绪因子输入）。"""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


class NewsSource(Base):
    """新闻来源表。"""

    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    country: Mapped[str | None] = mapped_column(String(50))
    language: Mapped[str] = mapped_column(String(20), default="en")
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1)
    crawl_interval: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    articles = relationship("NewsArticle", back_populates="source")

    def __repr__(self) -> str:
        return f"<NewsSource id={self.id} name={self.name!r}>"


class NewsArticle(Base):
    """新闻文章表（url_hash 唯一去重 + 全文检索）。"""

    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("news_sources.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(200))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawl_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # LLM 判定的情感倾向 -1..+1（V5 起真正写入，供情绪因子使用）
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    is_processed: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source = relationship("NewsSource", back_populates="articles")

    __table_args__ = (
        Index("ix_news_articles_publish_time", "publish_time"),
        Index("ix_news_articles_crawl_time", "crawl_time"),
        Index("ix_news_articles_source_id", "source_id"),
        Index("ix_news_articles_is_processed", "is_processed"),
    )

    @staticmethod
    def hash_url(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def __repr__(self) -> str:
        return f"<NewsArticle id={self.id} title={self.title[:24]!r}>"


class CrawlLog(Base):
    """爬虫运行日志。"""

    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="running")
    articles_found: Mapped[int] = mapped_column(Integer, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["NewsSource", "NewsArticle", "CrawlLog"]
