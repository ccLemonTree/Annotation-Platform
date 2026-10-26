"""
SmartEye数据下载服务
模拟从社交媒体平台下载图片数据
"""

import hashlib
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from models.smarteye import SmarteyeTask, SmarteeyeDownload, TaskStatus
from services.storage import get_storage


class SmartEyeService:
    """SmartEye下载服务"""

    def __init__(self):
        self.storage = get_storage()

    async def start_download_task(self, task_id: int, db: AsyncSession):
        """
        启动下载任务（异步执行）

        Args:
            task_id: 任务ID
            db: 数据库会话
        """
        # 获取任务
        task_result = await db.execute(
            select(SmarteyeTask).where(SmarteyeTask.id == task_id)
        )
        task = task_result.scalar_one_or_none()

        if not task:
            raise ValueError(f"任务 {task_id} 不存在")

        if task.status != TaskStatus.PENDING:
            raise ValueError(f"任务状态不正确: {task.status}")

        try:
            # 更新任务状态为运行中
            await db.execute(
                update(SmarteyeTask)
                .where(SmarteyeTask.id == task_id)
                .values(
                    status=TaskStatus.RUNNING,
                    started_at=datetime.utcnow()
                )
            )
            await db.commit()

            # 模拟下载过程
            downloaded = await self._download_images(
                task=task,
                db=db
            )

            # 更新任务状态为完成
            await db.execute(
                update(SmarteyeTask)
                .where(SmarteyeTask.id == task_id)
                .values(
                    status=TaskStatus.COMPLETED,
                    downloaded_count=downloaded,
                    completed_at=datetime.utcnow()
                )
            )
            await db.commit()

        except Exception as e:
            # 更新任务状态为失败
            await db.execute(
                update(SmarteyeTask)
                .where(SmarteyeTask.id == task_id)
                .values(
                    status=TaskStatus.FAILED,
                    error_message=str(e),
                    completed_at=datetime.utcnow()
                )
            )
            await db.commit()
            raise

    async def _download_images(
        self,
        task: SmarteyeTask,
        db: AsyncSession
    ) -> int:
        """
        执行图片下载

        Returns:
            下载成功的数量
        """
        downloaded_count = 0

        # 模拟从平台获取图片列表
        image_urls = self._mock_platform_search(
            platform=task.platform,
            keyword=task.keyword,
            count=task.target_count
        )

        for url in image_urls:
            try:
                # 模拟下载图片
                image_data = await self._mock_download_image(url)

                if not image_data:
                    continue

                # 计算哈希
                content_hash = hashlib.sha256(image_data).hexdigest()

                # 检查是否已下载（去重）
                existing = await db.execute(
                    select(SmarteeyeDownload).where(
                        SmarteeyeDownload.content_hash == content_hash
                    )
                )
                if existing.scalar_one_or_none():
                    continue  # 跳过重复

                # 保存到存储
                from io import BytesIO
                file_like = BytesIO(image_data)

                # 构建存储路径
                file_path = f"smarteye/{task.platform}/{task.id}/{content_hash[:2]}/{content_hash[2:4]}/{os.path.basename(url)}"

                # 保存文件
                result = await self.storage.save_file(file_like, file_path)
                blob_path = file_path  # 使用相对路径

                # 创建下载记录
                download = SmarteeyeDownload(
                    task_id=task.id,
                    blob_path=blob_path,
                    content_hash=content_hash,
                    file_name=os.path.basename(url),
                    file_size=len(image_data),
                    mime_type="image/jpeg",
                    width=1920,  # 模拟数据
                    height=1080,
                    source_url=url,
                    platform=task.platform,
                    reference_count=0
                )

                db.add(download)
                downloaded_count += 1

                # 每10条提交一次
                if downloaded_count % 10 == 0:
                    await db.commit()

            except Exception as e:
                print(f"下载失败: {url}, 错误: {e}")
                continue

        # 最终提交
        await db.commit()

        return downloaded_count

    def _mock_platform_search(
        self,
        platform: str,
        keyword: str,
        count: int
    ) -> List[str]:
        """
        模拟平台搜索
        实际应该调用各平台的API

        Returns:
            图片URL列表
        """
        # 模拟返回图片URL
        base_urls = {
            "xiaohongshu": "https://ci.xiaohongshu.com",
            "douyin": "https://p3.douyinpic.com",
            "weibo": "https://wx1.sinaimg.cn"
        }

        base_url = base_urls.get(platform, "https://example.com")
        urls = []

        for i in range(min(count, 100)):  # 限制最多100条
            # 生成模拟URL
            url = f"{base_url}/image_{keyword}_{i}.jpg"
            urls.append(url)

        return urls

    async def _mock_download_image(self, url: str) -> Optional[bytes]:
        """
        模拟下载图片
        实际应该使用 httpx 下载真实图片

        Returns:
            图片数据（字节）
        """
        # 模拟网络延迟
        await asyncio.sleep(0.1)

        # 返回模拟的图片数据（实际是随机字节）
        # 在生产环境中，这里应该是真实的图片下载
        import random
        mock_data = bytes([random.randint(0, 255) for _ in range(1024)])  # 1KB模拟图片

        return mock_data


# 全局服务实例
_smarteye_service: Optional[SmartEyeService] = None


def get_smarteye_service() -> SmartEyeService:
    """获取SmartEye服务单例"""
    global _smarteye_service
    if _smarteye_service is None:
        _smarteye_service = SmartEyeService()
    return _smarteye_service
