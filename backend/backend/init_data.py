"""
初始化数据库默认数据
创建默认租户和管理员账户
"""
import os
import asyncio
from sqlalchemy import select
from database import get_db_session
from models import Tenant, User
from services.auth import get_password_hash
from datetime import datetime


async def init_default_data():
    """初始化默认数据"""
    async for db in get_db_session():
        try:
            # 1. 检查是否已有租户
            result = await db.execute(select(Tenant))
            existing_tenant = result.scalar_one_or_none()

            if existing_tenant:
                print("✓ 租户已存在，跳过初始化")
                return

            # 2. 创建默认租户
            tenant = Tenant(
                tenant_name="默认租户",
                contact_email="admin@example.com",
                is_active=True
            )
            db.add(tenant)
            await db.flush()  # 获取租户ID
            print(f"✓ 创建默认租户: {tenant.tenant_name} (ID: {tenant.id})")

            # 3. 创建管理员用户
            admin_user = User(
                tenant_id=tenant.id,
                username="admin",
                email="admin@example.com",
                full_name="系统管理员",
                password_hash=get_password_hash(os.environ["ANNOTATION_ADMIN_PASSWORD"]),
                role="admin",
                is_active=True
            )
            db.add(admin_user)
            print(f"✓ 创建管理员用户: {admin_user.username}")

            # 4. 创建普通测试用户
            test_user = User(
                tenant_id=tenant.id,
                username="user1",
                email="user1@example.com",
                full_name="测试用户",
                password_hash=get_password_hash(os.environ["ANNOTATION_TEST_USER_PASSWORD"]),
                role="user",
                is_active=True
            )
            db.add(test_user)
            print(f"✓ 创建测试用户: {test_user.username}")

            # 5. 提交事务
            await db.commit()

            print("\n" + "="*50)
            print("✅ 数据初始化完成！")
            print("="*50)
            print("\n默认账户信息:")
            print(f"  管理员:")
            print(f"    用户名: admin")
            print(f"    密码: 从环境变量读取，不回显")
            print(f"  测试用户:")
            print(f"    用户名: user1")
            print(f"    密码: 从环境变量读取，不回显")
            print("\n请访问 http://localhost:8000/docs 测试API")

        except Exception as e:
            await db.rollback()
            print(f"❌ 初始化失败: {e}")
            raise
        finally:
            break


if __name__ == "__main__":
    asyncio.run(init_default_data())
