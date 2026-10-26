import asyncio
import asyncpg
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

async def create_database():
    """创建数据库"""
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = int(os.getenv('DB_PORT', '5432'))
    db_user = os.getenv('DB_USER', 'postgres')
    db_password = os.getenv('DB_PASSWORD', 'postgres')
    db_name = os.getenv('DB_NAME', 'data_platform')

    try:
        # 连接到默认postgres数据库
        conn = await asyncpg.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database='postgres'
        )

        # 检查数据库是否存在
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            db_name
        )

        if not exists:
            # 创建数据库
            await conn.execute(f'CREATE DATABASE {db_name}')
            print(f"✓ 数据库 {db_name} 创建成功")
        else:
            print(f"✓ 数据库 {db_name} 已存在")

        await conn.close()
        return True

    except Exception as e:
        print(f"✗ 创建数据库失败: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(create_database())
