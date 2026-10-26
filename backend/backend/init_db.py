"""
初始化脚本
创建默认租户和管理员用户
"""

import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from config.settings import settings
from models import Base, Tenant, User
from services.auth.jwt import get_password_hash


async def init_db():
    """初始化数据库"""
    engine = create_async_engine(settings.DATABASE_URL, echo=True)

    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 创建Session
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        # 创建默认租户
        default_tenant = Tenant(
            name="默认租户",
            code="default",
            is_active=True
        )
        session.add(default_tenant)
        await session.flush()

        # 创建管理员用户
        admin_user = User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash(os.environ["ANNOTATION_ADMIN_PASSWORD"]),
            full_name="系统管理员",
            is_active=True,
            is_superuser=True,
            tenant_id=default_tenant.id
        )
        session.add(admin_user)

        await session.commit()

        print("✅ 数据库初始化成功！")
        print(f"默认租户: {default_tenant.name} (ID: {default_tenant.id})")
        print(f"管理员账号: {admin_user.username}")
        print(f"管理员密码: 从环境变量读取，不回显")
        print("⚠️  请及时修改默认密码！")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
