"""
SmartEye数据导入服务
"""

from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from models import SmartEyeTask, SmartEyeDownload, Platform, Asset, ImportBatch
from services.storage import get_storage


class SmartEyeImportService:
    """SmartEye数据导入服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = get_storage()

    async def create_import_task(
        self,
        platform_id: uuid.UUID,
        task_name: str,
        query_params: Dict
    ) -> SmartEyeTask:
        """
        创建SmartEye导入任务

        Args:
            platform_id: 平台ID
            task_name: 任务名称
            query_params: 查询参数 {start_time, end_time, device_id, alarm_type}

        Returns:
            SmartEyeTask实例
        """
        task = SmartEyeTask(
            task_name=task_name,
            platform_id=platform_id,
            query_params=query_params,
            status="pending",
            storage_prefix=f"smarteye/task_{uuid.uuid4().hex[:8]}"
        )

        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)

        return task

    async def import_to_project(
        self,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
        filters: Optional[Dict] = None
    ) -> ImportBatch:
        """
        从SmartEye任务导入图片到标注项目（零拷贝）

        核心逻辑：
        1. 查询SmartEye已下载的记录
        2. 创建Asset记录，引用同一blob_path
        3. 无需复制文件，实现零拷贝

        Args:
            project_id: 标注项目ID
            task_id: SmartEye任务ID
            filters: 筛选条件 {device_ids, date_range, quality_min}

        Returns:
            ImportBatch实例
        """
        # 1. 创建导入批次
        batch = ImportBatch(
            project_id=project_id,
            source_type="smarteye",
            source_name=f"SmartEye任务: {task_id}",
            source_config={"task_id": str(task_id), "filters": filters or {}}
        )
        self.db.add(batch)
        await self.db.flush()

        # 2. 查询SmartEye下载记录
        query = select(SmartEyeDownload).where(
            SmartEyeDownload.task_id == task_id,
            SmartEyeDownload.download_status == "completed"
        )

        # 应用筛选条件
        if filters:
            if filters.get("device_ids"):
                query = query.where(SmartEyeDownload.device_id.in_(filters["device_ids"]))

        result = await self.db.execute(query)
        downloads = result.scalars().all()

        # 3. 零拷贝导入：创建Asset记录
        assets = []
        for download in downloads:
            asset = Asset(
                project_id=project_id,
                batch_id=batch.id,
                source_type="smarteye",
                source_record_id=download.id,  # ⭐ 关联SmartEye记录
                blob_path=download.blob_path,  # ⭐ 引用同一文件路径
                content_hash=download.content_hash,
                original_name=f"{download.device_name}_{download.timestamp}",
                file_size=download.file_size,
                width=download.width,
                height=download.height,
                format=download.format,
                capture_time=download.timestamp,
                device_info={
                    "device_name": download.device_name,
                    "device_id": download.device_id,
                    "platform": download.platform
                },
                annotation_status="pending"
            )
            assets.append(asset)

        self.db.add_all(assets)

        # 4. 更新批次统计
        batch.total_count = len(downloads)
        batch.success_count = len(assets)

        await self.db.commit()
        await self.db.refresh(batch)

        return batch

    async def get_download_records(
        self,
        task_id: uuid.UUID,
        filters: Optional[Dict] = None
    ) -> List[SmartEyeDownload]:
        """
        获取SmartEye下载记录（供前端预览）

        Args:
            task_id: 任务ID
            filters: 筛选条件

        Returns:
            下载记录列表
        """
        query = select(SmartEyeDownload).where(
            SmartEyeDownload.task_id == task_id,
            SmartEyeDownload.download_status == "completed"
        )

        # 应用筛选
        if filters:
            if filters.get("device_ids"):
                query = query.where(SmartEyeDownload.device_id.in_(filters["device_ids"]))
            if filters.get("target_types"):
                query = query.where(SmartEyeDownload.target_type.in_(filters["target_types"]))

        result = await self.db.execute(query)
        return result.scalars().all()
