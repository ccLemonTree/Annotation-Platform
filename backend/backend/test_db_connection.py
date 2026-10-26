"""
测试数据库连接
"""

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from config.settings import settings


async def test_connection():
    """测试数据库连接"""
    try:
        # 创建引擎
        engine = create_async_engine(settings.DATABASE_URL, echo=True)

        # 测试连接
        async with engine.connect() as conn:
            result = await conn.execute("SELECT 1")
            print("✅ 数据库连接成功！")
            print(f"数据库URL: {settings.DATABASE_URL}")

        await engine.dispose()

    except Exception as e:
        print(f"❌ 数据库连接失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_connection())
