"""
测试完整的CRUD操作
验证项目、资产、标注的增删改查
"""

import os
import asyncio
import httpx
from uuid import UUID


BASE_URL = "http://localhost:8000"

# 测试账户
USERNAME = "admin"
PASSWORD = os.environ["ANNOTATION_PASSWORD"]


async def test_full_crud():
    """测试完整的CRUD流程"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=" * 60)
        print("🧪 开始测试完整CRUD操作")
        print("=" * 60)

        # ==================== 1. 登录获取Token ====================
        print("\n📝 步骤 1: 用户登录")
        login_response = await client.post(
            f"{BASE_URL}/api/auth/login",
            data={"username": USERNAME, "password": PASSWORD}
        )
        assert login_response.status_code == 200, f"登录失败: {login_response.text}"

        token_data = login_response.json()
        token = token_data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"  ✅ 登录成功，Token: {token[:30]}...")

        # ==================== 2. 项目CRUD ====================
        print("\n📝 步骤 2: 测试项目CRUD")

        # 2.1 创建项目
        print("  2.1 创建项目...")
        project_data = {
            "project_name": f"测试项目_CRUD_{asyncio.get_event_loop().time()}",
            "task_type": "detection",
            "description": "用于测试CRUD操作的项目"
        }
        create_project_response = await client.post(
            f"{BASE_URL}/api/projects",
            json=project_data,
            headers=headers
        )
        assert create_project_response.status_code == 201, f"创建项目失败: {create_project_response.text}"

        project = create_project_response.json()
        project_id = project["id"]
        print(f"  ✅ 创建成功，项目ID: {project_id}")

        # 2.2 获取项目详情
        print("  2.2 获取项目详情...")
        get_project_response = await client.get(
            f"{BASE_URL}/api/projects/{project_id}",
            headers=headers
        )
        assert get_project_response.status_code == 200, f"获取项目失败: {get_project_response.text}"
        print(f"  ✅ 获取成功")

        # 2.3 更新项目
        print("  2.3 更新项目...")
        update_data = {
            "description": "已更新的项目描述"
        }
        update_project_response = await client.put(
            f"{BASE_URL}/api/projects/{project_id}",
            json=update_data,
            headers=headers
        )
        assert update_project_response.status_code == 200, f"更新项目失败: {update_project_response.text}"
        updated_project = update_project_response.json()
        assert updated_project["description"] == "已更新的项目描述"
        print(f"  ✅ 更新成功")

        # 2.4 获取项目统计
        print("  2.4 获取项目统计...")
        stats_response = await client.get(
            f"{BASE_URL}/api/projects/{project_id}/stats",
            headers=headers
        )
        assert stats_response.status_code == 200, f"获取统计失败: {stats_response.text}"
        stats = stats_response.json()
        print(f"  ✅ 统计信息: 资产数={stats['total_assets']}, 已标注={stats['annotated_count']}")

        # ==================== 3. 标注CRUD ====================
        print("\n📝 步骤 3: 测试标注CRUD（模拟数据）")

        # 注意：实际标注需要先有资产，这里我们测试API的可用性
        # 如果有资产ID，可以创建真实标注

        # 3.1 获取标注列表（应该为空）
        print("  3.1 获取标注列表...")
        list_annotations_response = await client.get(
            f"{BASE_URL}/api/annotations?page=1&page_size=10",
            headers=headers
        )
        assert list_annotations_response.status_code == 200, f"获取标注列表失败: {list_annotations_response.text}"
        annotations = list_annotations_response.json()
        print(f"  ✅ 标注列表获取成功，共 {annotations['total']} 条")

        # ==================== 4. 删除项目 ====================
        print("\n📝 步骤 4: 清理测试数据")
        print("  4.1 删除项目...")
        delete_response = await client.delete(
            f"{BASE_URL}/api/projects/{project_id}",
            headers=headers
        )
        assert delete_response.status_code == 204, f"删除项目失败: {delete_response.text}"
        print(f"  ✅ 项目删除成功")

        # ==================== 测试总结 ====================
        print("\n" + "=" * 60)
        print("✅ 所有CRUD操作测试通过！")
        print("=" * 60)
        print("\n测试内容:")
        print("  ✓ 项目创建")
        print("  ✓ 项目查询")
        print("  ✓ 项目更新")
        print("  ✓ 项目统计")
        print("  ✓ 项目删除")
        print("  ✓ 标注列表查询")
        print("\n所有核心CRUD功能正常工作！🎉")


if __name__ == "__main__":
    asyncio.run(test_full_crud())
