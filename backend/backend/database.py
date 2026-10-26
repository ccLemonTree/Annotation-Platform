"""
数据库连接管理
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from config.settings import settings
from models import Base


# 创建异步引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # SQL绑定参数可能包含密码哈希及个人资料
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,  # 自动检测连接健康
    pool_recycle=3600,   # 1小时回收连接
)

# 创建会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db():
    """初始化数据库（创建所有表）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ 数据库表创建完成")


async def drop_db():
    """删除所有表（危险操作）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("⚠️  数据库表已删除")


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话（上下文管理器）

    使用示例:
        async with get_db() as db:
            result = await db.execute(select(Tenant))
            tenants = result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI依赖注入使用的数据库会话生成器

    使用示例:
        @router.get("/projects")
        async def get_projects(db: AsyncSession = Depends(get_db_session)):
            result = await db.execute(select(Project))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


if __name__ == "__main__":
    import asyncio

    async def test_connection():
        """测试数据库连接"""
        print(f"📡 测试数据库连接: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
        try:
            from sqlalchemy import text
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT version()"))
                version = result.scalar()
                print(f"✅ 连接成功！PostgreSQL版本: {version}")
        except Exception as e:
            print(f"❌ 连接失败: {e}")

    asyncio.run(test_connection())
