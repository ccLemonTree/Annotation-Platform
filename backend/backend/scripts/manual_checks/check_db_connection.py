"""
测试数据库连接
"""

import asyncio
import os
from pathlib import Path
import sys

if __name__ == "__main__" and os.environ.get("RUN_MANUAL_CHECKS") != "1":
    raise SystemExit("安全拦截：此手工脚本会连接配置的 DATABASE_URL，严禁对业务库盲跑，请显式设置 RUN_MANUAL_CHECKS=1 后执行。")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from config.settings import settings


async def test_connection():
    """测试数据库连接"""
    try:
        # 创建引擎
        engine = create_async_engine(settings.DATABASE_URL, echo=True)

        # 测试连接
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            print("✅ 数据库连接成功！")
            print(f"数据库URL: {settings.DATABASE_URL}")

        await engine.dispose()

    except Exception as e:
        print(f"❌ 数据库连接失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_connection())
