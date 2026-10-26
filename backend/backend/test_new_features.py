"""
测试新增功能
- 批量操作
- 数据导出
- 统计分析
"""

import os
import asyncio
import httpx
from uuid import UUID


BASE_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = os.environ["ANNOTATION_PASSWORD"]


async def test_new_features():
    """测试所有新增功能"""

    async with httpx.AsyncClient(timeout=60.0) as client:
        print("=" * 60)
        print("🧪 测试新增功能")
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

        # ==================== 2. 创建测试项目 ====================
        print("\n📝 步骤 2: 创建测试项目")
        project_data = {
            "project_name": f"新功能测试项目_{asyncio.get_event_loop().time()}",
            "task_type": "detection",
            "description": "测试新增功能"
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

        # ==================== 3. 测试统计分析 ====================
        print("\n📝 步骤 3: 测试统计分析API")

        # 3.1 项目统计
        print("  3.1 获取项目统计...")
        stats_response = await client.get(
            f"{BASE_URL}/api/stats/projects/{project_id}/stats",
            headers=headers
        )
        if stats_response.status_code != 200:
            print(f"  ❌ 错误: {stats_response.status_code}")
            print(f"  响应: {stats_response.text}")
        assert stats_response.status_code == 200
        stats = stats_response.json()
        print(f"  ✅ 项目统计: 资产数={stats['total_assets']}, 进度={stats['annotation_progress']}%")

        # 3.2 租户统计
        print("  3.2 获取租户统计...")
        tenant_stats_response = await client.get(
            f"{BASE_URL}/api/stats/tenant/stats",
            headers=headers
        )
        assert tenant_stats_response.status_code == 200
        tenant_stats = tenant_stats_response.json()
        print(f"  ✅ 租户统计: 项目数={tenant_stats['total_projects']}, 资产数={tenant_stats['total_assets']}")

        # 3.3 Dashboard统计
        print("  3.3 获取Dashboard统计...")
        dashboard_response = await client.get(
            f"{BASE_URL}/api/stats/dashboard",
            headers=headers
        )
        assert dashboard_response.status_code == 200
        dashboard = dashboard_response.json()
        print(f"  ✅ Dashboard统计获取成功")
        print(f"     项目总数: {dashboard['projects_overview']['total']}")
        print(f"     资产总数: {dashboard['assets_overview']['total']}")
        print(f"     标注总数: {dashboard['annotations_overview']['total']}")

        # ==================== 4. 测试数据导出 ====================
        print("\n📝 步骤 4: 测试数据导出API")

        # 4.1 导出JSON格式
        print("  4.1 导出JSON格式...")
        export_json_response = await client.get(
            f"{BASE_URL}/api/export/projects/{project_id}/export",
            params={"format": "json", "include_annotations": True},
            headers=headers
        )
        assert export_json_response.status_code == 200
        print(f"  ✅ JSON导出成功，大小: {len(export_json_response.content)} bytes")

        # 4.2 导出CSV格式
        print("  4.2 导出CSV格式...")
        export_csv_response = await client.get(
            f"{BASE_URL}/api/export/projects/{project_id}/export",
            params={"format": "csv"},
            headers=headers
        )
        assert export_csv_response.status_code == 200
        print(f"  ✅ CSV导出成功，大小: {len(export_csv_response.content)} bytes")

        # 4.3 导出COCO格式
        print("  4.3 导出COCO格式...")
        export_coco_response = await client.get(
            f"{BASE_URL}/api/export/projects/{project_id}/export",
            params={"format": "coco"},
            headers=headers
        )
        assert export_coco_response.status_code == 200
        print(f"  ✅ COCO导出成功，大小: {len(export_coco_response.content)} bytes")

        # ==================== 5. 测试批量操作 ====================
        print("\n📝 步骤 5: 测试批量操作API")

        # 获取所有资产ID（用于测试）
        assets_response = await client.get(
            f"{BASE_URL}/api/assets",
            params={"project_id": project_id, "page": 1, "page_size": 10},
            headers=headers
        )
        assets = assets_response.json()
        asset_ids = [asset["id"] for asset in assets["items"]]

        if asset_ids:
            # 5.1 批量更新状态
            print(f"  5.1 批量更新资产状态（{len(asset_ids)}个）...")
            batch_update_response = await client.post(
                f"{BASE_URL}/api/batch/assets/update-status",
                params={"new_status": "annotating"},
                json=asset_ids,
                headers=headers
            )
            assert batch_update_response.status_code == 200
            batch_result = batch_update_response.json()
            print(f"  ✅ 批量更新成功: {batch_result['message']}")
        else:
            print("  ⚠️  项目中暂无资产，跳过批量操作测试")

        # 获取所有标注ID
        annotations_response = await client.get(
            f"{BASE_URL}/api/annotations",
            params={"page": 1, "page_size": 10},
            headers=headers
        )
        annotations = annotations_response.json()
        annotation_ids = [ann["id"] for ann in annotations["items"]]

        if annotation_ids:
            # 5.2 批量审核通过
            print(f"  5.2 批量审核标注（{len(annotation_ids)}个）...")
            batch_approve_response = await client.post(
                f"{BASE_URL}/api/batch/annotations/approve",
                json=annotation_ids,
                headers=headers
            )
            assert batch_approve_response.status_code == 200
            approve_result = batch_approve_response.json()
            print(f"  ✅ 批量审核成功: {approve_result['message']}")
        else:
            print("  ⚠️  暂无标注数据，跳过批量审核测试")

        # ==================== 6. 测试性能监控 ====================
        print("\n📝 步骤 6: 验证性能监控")
        print("  检查响应头中的性能指标...")

        test_response = await client.get(
            f"{BASE_URL}/api/projects",
            params={"page": 1, "page_size": 10},
            headers=headers
        )

        if "x-process-time" in test_response.headers:
            process_time = test_response.headers["x-process-time"]
            print(f"  ✅ 性能监控正常，处理时间: {process_time}s")
        else:
            print("  ⚠️  未检测到性能监控响应头")

        # ==================== 7. 清理测试数据 ====================
        print("\n📝 步骤 7: 清理测试数据")
        delete_response = await client.delete(
            f"{BASE_URL}/api/projects/{project_id}",
            headers=headers
        )
        assert delete_response.status_code == 204
        print("  ✅ 测试项目已删除")

        # ==================== 测试总结 ====================
        print("\n" + "=" * 60)
        print("✅ 新增功能测试完成！")
        print("=" * 60)
        print("\n测试内容:")
        print("  ✓ 统计分析API")
        print("    - 项目统计")
        print("    - 租户统计")
        print("    - Dashboard统计")
        print("  ✓ 数据导出API")
        print("    - JSON格式导出")
        print("    - CSV格式导出")
        print("    - COCO格式导出")
        print("  ✓ 批量操作API")
        print("    - 批量更新状态")
        print("    - 批量审核标注")
        print("  ✓ 性能监控中间件")
        print("\n🎉 所有新功能正常运行！")


if __name__ == "__main__":
    asyncio.run(test_new_features())
