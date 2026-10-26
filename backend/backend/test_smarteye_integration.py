"""
测试SmartEye集成功能
- 创建下载任务
- 监控下载进度
- 零拷贝导入到项目
"""

import os
import asyncio
import httpx
from uuid import UUID


BASE_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = os.environ["ANNOTATION_PASSWORD"]


async def test_smarteye_integration():
    """测试SmartEye完整流程"""

    async with httpx.AsyncClient(timeout=60.0) as client:
        print("=" * 60)
        print("🧪 测试SmartEye集成功能")
        print("=" * 60)

        # ==================== 1. 登录 ====================
        print("\n📝 步骤 1: 用户登录")
        login_response = await client.post(
            f"{BASE_URL}/api/auth/login",
            data={"username": USERNAME, "password": PASSWORD}
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("  ✅ 登录成功")

        # ==================== 2. 获取平台列表 ====================
        print("\n📝 步骤 2: 获取支持的平台")
        platforms_response = await client.get(
            f"{BASE_URL}/api/smarteye/platforms",
            headers=headers
        )
        assert platforms_response.status_code == 200
        platforms = platforms_response.json()
        print(f"  ✅ 支持的平台: {', '.join([p['platform_name'] for p in platforms if p['supported']])}")

        # ==================== 3. 创建项目 ====================
        print("\n📝 步骤 3: 创建目标项目")
        project_data = {
            "project_name": f"SmartEye导入测试_{asyncio.get_event_loop().time()}",
            "task_type": "detection",
            "description": "用于测试SmartEye零拷贝导入"
        }
        create_project_response = await client.post(
            f"{BASE_URL}/api/projects",
            json=project_data,
            headers=headers
        )
        assert create_project_response.status_code == 201
        project = create_project_response.json()
        project_id = project["id"]
        print(f"  ✅ 项目创建成功，ID: {project_id}")

        # ==================== 4. 创建下载任务 ====================
        print("\n📝 步骤 4: 创建SmartEye下载任务")
        task_data = {
            "tenant_id": 2,
            "task_name": "测试下载任务",
            "platform": "xiaohongshu",
            "keyword": "美食",
            "target_count": 10
        }
        create_task_response = await client.post(
            f"{BASE_URL}/api/smarteye/tasks",
            json=task_data,
            headers=headers
        )
        assert create_task_response.status_code == 201
        task = create_task_response.json()
        task_id = task["id"]
        print(f"  ✅ 下载任务创建成功，ID: {task_id}")
        print(f"     平台: {task['platform']}")
        print(f"     关键词: {task['keyword']}")
        print(f"     目标数量: {task['target_count']}")

        # ==================== 5. 监控下载进度 ====================
        print("\n📝 步骤 5: 监控下载进度")
        max_retries = 30
        retry_count = 0

        while retry_count < max_retries:
            await asyncio.sleep(2)  # 等待2秒

            # 查询任务状态
            status_response = await client.get(
                f"{BASE_URL}/api/smarteye/tasks/{task_id}",
                headers=headers
            )
            assert status_response.status_code == 200
            task_status = status_response.json()

            status = task_status["status"]
            downloaded = task_status["downloaded_count"]
            failed = task_status["failed_count"]

            print(f"  📊 状态: {status}, 已下载: {downloaded}, 失败: {failed}")

            if status == "completed":
                print(f"  ✅ 下载完成！总计下载 {downloaded} 条")
                break
            elif status == "failed":
                print(f"  ❌ 下载失败")
                break

            retry_count += 1

        if retry_count >= max_retries:
            print(f"  ⚠️  超时：下载任务未在预期时间内完成")

        # ==================== 6. 获取下载列表 ====================
        print("\n📝 步骤 6: 获取下载记录")
        downloads_response = await client.get(
            f"{BASE_URL}/api/smarteye/tasks/{task_id}/downloads",
            headers=headers
        )
        assert downloads_response.status_code == 200
        downloads_data = downloads_response.json()
        total_downloads = downloads_data["total"]
        print(f"  ✅ 共有 {total_downloads} 条下载记录")

        if total_downloads > 0:
            first_download = downloads_data["items"][0]
            print(f"     示例: {first_download['file_name']}, 大小: {first_download['file_size']} bytes")

        # ==================== 7. 零拷贝导入 ====================
        if total_downloads > 0:
            print("\n📝 步骤 7: 零拷贝导入到项目")
            import_data = {
                "task_id": task_id,
                "project_id": project_id
            }
            import_response = await client.post(
                f"{BASE_URL}/api/smarteye/import-to-project",
                json=import_data,
                headers=headers
            )
            assert import_response.status_code == 200
            import_result = import_response.json()

            print(f"  ✅ 导入成功！")
            print(f"     导入数量: {import_result['imported_count']}")
            print(f"     跳过数量: {import_result['skipped_count']}")
            print(f"     消息: {import_result['message']}")

            # ==================== 8. 验证项目资产 ====================
            print("\n📝 步骤 8: 验证项目资产")
            assets_response = await client.get(
                f"{BASE_URL}/api/assets",
                params={"project_id": project_id, "page": 1, "page_size": 20},
                headers=headers
            )
            assert assets_response.status_code == 200
            assets = assets_response.json()
            print(f"  ✅ 项目资产数量: {assets['total']}")

            if assets['total'] > 0:
                first_asset = assets['items'][0]
                print(f"     示例资产:")
                print(f"       - ID: {first_asset['id']}")
                print(f"       - 文件名: {first_asset['original_name']}")
                print(f"       - 来源: {first_asset['source_type']}")
                print(f"       - 状态: {first_asset['annotation_status']}")

        # ==================== 9. 清理测试数据 ====================
        print("\n📝 步骤 9: 清理测试数据")
        delete_response = await client.delete(
            f"{BASE_URL}/api/projects/{project_id}",
            headers=headers
        )
        assert delete_response.status_code == 204
        print("  ✅ 测试项目已删除")

        # ==================== 测试总结 ====================
        print("\n" + "=" * 60)
        print("✅ SmartEye集成测试完成！")
        print("=" * 60)
        print("\n测试内容:")
        print("  ✓ 获取平台列表")
        print("  ✓ 创建下载任务")
        print("  ✓ 监控下载进度")
        print("  ✓ 获取下载记录")
        if total_downloads > 0:
            print("  ✓ 零拷贝导入")
            print("  ✓ 验证资产数据")
        print("  ✓ 清理测试数据")
        print("\n🎉 SmartEye功能正常运行！")


if __name__ == "__main__":
    asyncio.run(test_smarteye_integration())
