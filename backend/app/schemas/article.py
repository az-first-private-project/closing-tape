"""
Pydantic 数据校验模型 (Schemas)
------------------------------
定义 API 请求/响应的数据结构。
FastAPI 使用这些模型自动:
  1. 校验请求数据 (如果格式不对会自动返回 422 错误)
  2. 生成 API 文档 (Swagger UI / ReDoc)
  3. 序列化响应数据 (Python 对象 → JSON)

比喻: Schema 是"快递单" — 规定了包裹里应该有什么，格式对不对
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


# ==================== 文章相关 Schema ====================

class ArticleBase(BaseModel):
    """文章的基础字段 (共享)"""
    title: str = Field(..., min_length=1, max_length=500, description="新闻标题")
    url: str = Field(..., max_length=2048, description="原文链接")
    content: Optional[str] = Field(None, description="正文内容")
    author: Optional[str] = Field(None, max_length=200, description="作者")
    publish_time: Optional[datetime] = Field(None, description="发布时间")


class ArticleCreate(ArticleBase):
    """创建文章时需要的字段 (爬虫入库用)"""
    source_id: int = Field(..., gt=0, description="来源ID (必须大于0)")
    url_hash: str = Field(..., min_length=64, max_length=64, description="URL的SHA-256哈希")


class ArticleUpdate(BaseModel):
    """更新文章时可能修改的字段 (所有字段可选)"""
    summary: Optional[str] = Field(None, description="AI生成的中文摘要")
    is_processed: Optional[int] = Field(None, ge=0, le=2, description="处理状态: 0/1/2")
    sentiment_score: Optional[float] = Field(None, ge=0, le=2, description="情感分数")


class ArticleResponse(ArticleBase):
    """返回给前端的文章数据 (列表页)"""
    id: int
    source_id: int
    source_name: Optional[str] = Field(None, description="来源名称 (如 Reuters)")
    summary: Optional[str] = Field(None, description="AI中文摘要")
    url_hash: str
    crawl_time: Optional[datetime] = None
    is_processed: int = 0
    sentiment_score: Optional[float] = None

    # 允许从 ORM 对象直接转换
    class Config:
        from_attributes = True


class ArticleDetailResponse(ArticleResponse):
    """返回给前端的文章详情 (详情页，包含完整正文)"""
    content: Optional[str] = Field(None, description="完整正文内容")


# ==================== 分页相关 Schema ====================

class PaginationParams(BaseModel):
    """分页查询参数"""
    page: int = Field(1, ge=1, description="页码 (从1开始)")
    page_size: int = Field(20, ge=1, le=100, description="每页数量 (最大100)")


class PaginatedResponse(BaseModel):
    """分页响应格式"""
    total: int = Field(..., description="总条数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    total_pages: int = Field(..., description="总页数")
    items: list = Field(..., description="当前页数据")


# ==================== 搜索相关 Schema ====================

class SearchParams(BaseModel):
    """搜索请求参数"""
    q: str = Field(..., min_length=1, max_length=200, description="搜索关键词")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


# ==================== 统计相关 Schema ====================

class StatsResponse(BaseModel):
    """系统统计信息"""
    total_articles: int = Field(..., description="文章总数")
    total_sources: int = Field(..., description="来源总数")
    articles_today: int = Field(..., description="今日新增文章数")
    processed_count: int = Field(..., description="已生成摘要的文章数")