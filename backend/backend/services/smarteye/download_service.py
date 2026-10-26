"""
SmartEye下载服务
处理从各平台拉取图片数据
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import SmarteyeTask, SmarteeyeDownload, TaskStatus
from services.storage import get_storage
from config.settings import settings

import asyncio
import aiohttp
import hashlib


class SmartEyeDownloadService:
    """SmartEye下载服务"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.storage = get_storage()

    async def create_task(
        self,
        tenant_id: int,
        task_name: str,
        platform: str,
        keyword: str,
        target_count: int = 100
    ) -> SmarteyeTask:
        """
        创建下载任务

        Args:
            tenant_id: 租户ID
            task_name: 任务名称
            platform: 平台名称
            keyword: 搜索关键词
            target_count: 目标数量

        Returns:
            创建的任务对象
        """
        task = SmarteyeTask(
            tenant_id=tenant_id,
            task_name=task_name,
            platform=platform,
            keyword=keyword,
            target_count=target_count,
            status=TaskStatus.PENDING
        )

        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)

        return task

    async def start_download(self, task_id: int) -> Dict[str, Any]:
        """
        启动下载任务

        Args:
            task_id: 任务ID

        Returns:
            下载结果统计
        """
        # 获取任务
        result = await self.db.execute(
            select(SmarteyeTask).where(SmarteyeTask.id == task_id)
        )
        task = result.scalar_one_or_none()

        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        if task.status != TaskStatus.PENDING:
            raise ValueError(f"任务状态不正确: {task.status}")

        # 更新任务状态
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        await self.db.commit()

        try:
            # TODO: 实际的爬虫逻辑（从SmartEye原有代码迁移）
            # 这里先用Mock数据演示流程
            image_urls = await self._fetch_image_urls(task.platform, task.keyword, task.target_count)

            # 并发下载
            downloaded = await self._download_images(task.id, image_urls)

            # 更新任务状态
            task.status = TaskStatus.COMPLETED
            task.downloaded_count = len(downloaded)
            task.completed_at = datetime.utcnow()
            await self.db.commit()

            return {
                "task_id": task.id,
                "success_count": len(downloaded),
                "failed_count": task.target_count - len(downloaded)
            }

        except Exception as e:
            # 更新任务失败状态
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            await self.db.commit()
            raise

    async def _fetch_image_urls(
        self,
        platform: str,
        keyword: str,
        count: int
    ) -> List[Dict[str, Any]]:
        """
        从平台获取图片URL列表

        TODO: 从SmartEye原有爬虫代码迁移
        """
        # Mock数据
        return [
            {
                "url": f"https://example.com/{platform}/{i}.jpg",
                "file_name": f"{keyword}_{i}.jpg"
            }
            for i in range(count)
        ]

    async def _download_images(
        self,
        task_id: int,
        image_urls: List[Dict[str, Any]]
    ) -> List[SmarteeyeDownload]:
        """
        并发下载图片

        Args:
            task_id: 任务ID
            image_urls: 图片URL列表

        Returns:
            下载记录列表
        """
        downloaded = []

        # 并发下载（最多10个并发）
        semaphore = asyncio.Semaphore(10)

        async def download_one(image_info: Dict[str, Any]) -> Optional[SmarteeyeDownload]:
            async with semaphore:
                try:
                    return await self._download_single_image(task_id, image_info)
                except Exception as e:
                    print(f"下载失败 {image_info['url']}: {e}")
                    return None

        tasks = [download_one(img) for img in image_urls]
        results = await asyncio.gather(*tasks)

        downloaded = [r for r in results if r is not None]
        return downloaded

    async def _download_single_image(
        self,
        task_id: int,
        image_info: Dict[str, Any]
    ) -> SmarteeyeDownload:
        """
        下载单张图片

        Args:
            task_id: 任务ID
            image_info: 图片信息（url, file_name）

        Returns:
            下载记录
        """
        url = image_info['url']
        file_name = image_info['file_name']

        # 下载图片
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=False) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}")

                image_data = await response.read()

        # 计算哈希
        content_hash = f"sha256:{hashlib.sha256(image_data).hexdigest()}"

        # 保存到存储（零拷贝架构的关键）
        blob_path = f"smarteye/task_{task_id}/{content_hash[:16]}/{file_name}"

        from io import BytesIO
        await self.storage.save_file(
            BytesIO(image_data),
            blob_path,
            content_hash
        )

        # 创建下载记录
        download = SmarteeyeDownload(
            task_id=task_id,
            blob_path=blob_path,
            content_hash=content_hash,
            file_name=file_name,
            file_size=len(image_data),
            mime_type=response.headers.get('Content-Type', 'image/jpeg'),
            source_url=url,
            platform="xiaohongshu",  # TODO: 从task获取
            reference_count=0
        )

        self.db.add(download)
        await self.db.commit()
        await self.db.refresh(download)

        return download

    async def get_task_downloads(
        self,
        task_id: int,
        limit: int = 100,
        offset: int = 0
    ) -> List[SmarteeyeDownload]:
        """
        获取任务的下载记录

        Args:
            task_id: 任务ID
            limit: 返回数量
            offset: 偏移量

        Returns:
            下载记录列表
        """
        result = await self.db.execute(
            select(SmarteeyeDownload)
            .where(SmarteeyeDownload.task_id == task_id)
            .limit(limit)
            .offset(offset)
        )

        return result.scalars().all()
