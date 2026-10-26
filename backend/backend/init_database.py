"""
数据库初始化脚本
创建数据库和必要的扩展
"""

import asyncio
import asyncpg
from config.settings import settings


async def create_database():
    """创建数据库"""
    # 连接到默认的postgres数据库
    conn = await asyncpg.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database='postgres'
    )

    try:
        # 检查数据库是否存在
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            settings.DB_NAME
        )

        if exists:
            print(f"✅ 数据库 {settings.DB_NAME} 已存在")
        else:
            # 创建数据库
            await conn.execute(f'CREATE DATABASE {settings.DB_NAME}')
            print(f"✅ 数据库 {settings.DB_NAME} 创建成功")

    finally:
        await conn.close()

    # 连接到新创建的数据库，安装扩展
    conn = await asyncpg.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )

    try:
        # 安装必要的扩展
        await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        print("✅ 扩展 uuid-ossp 已安装")

        # 创建测试数据库（用于测试）
        test_db_name = f"{settings.DB_NAME}_test"
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            test_db_name
        )

        if not exists:
            # 需要重新连接到postgres数据库才能创建数据库
            await conn.close()
            conn = await asyncpg.connect(
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
                database='postgres'
            )
            await conn.execute(f'CREATE DATABASE {test_db_name}')
            print(f"✅ 测试数据库 {test_db_name} 创建成功")
        else:
            print(f"✅ 测试数据库 {test_db_name} 已存在")

    finally:
        await conn.close()

    print("\n🎉 数据库初始化完成！")
    print(f"📊 主数据库: {settings.DB_NAME}")
    print(f"🧪 测试数据库: {settings.DB_NAME}_test")
    print(f"\n下一步：运行 alembic upgrade head 创建表结构")


if __name__ == "__main__":
    try:
        asyncio.run(create_database())
    except Exception as e:
        print(f"❌ 错误: {e}")
        exit(1)
