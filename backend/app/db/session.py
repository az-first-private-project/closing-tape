"""
数据库会话管理 (异步)
--------------------
使用 SQLAlchemy 2.0 异步引擎 + asyncpg 驱动。
提供 async get_db 依赖注入函数。
"""
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from app.core.config import DATABASE_URL

# ==================== 创建异步数据库引擎 ====================
# echo=False: 不打印 SQL 日志 (开发时可设为 True)
# pool_size=10: 连接池最大连接数
# pool_recycle=3600: 连接回收时间 (秒)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    pool_recycle=3600,
)

# ==================== 创建异步会话工厂 ====================
# expire_on_commit=False: 提交后不使对象过期，方便在模板中继续使用
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """
    FastAPI 依赖注入函数 — 获取异步数据库会话

    用法:
    ```python
    @router.get("/articles")
    async def list_articles(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(NewsArticle))
        return result.scalars().all()
    ```

    每次请求创建新的数据库会话，请求结束后自动关闭。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
