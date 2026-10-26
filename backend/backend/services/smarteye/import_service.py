"""
SmartEye数据导入服务
负责将SmartEye下载的数据导入到标注项目中
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from models import Asset, ImportBatch, SmartEyeDownload, Project
from services.storage import get_storage


class ImportService:
    """SmartEye数据导入服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = get_storage()

    async def create_import_batch(
        self,
        project_id: UUID,
        source_type: str,
        source_name: Optional[str] = None,
        source_config: Optional[Dict[str, Any]] = None
    ) -> ImportBatch:
        """
        创建导入批次

        Args:
            project_id: 目标项目ID
            source_type: 来源类型（smarteye/upload）
            source_name: 来源名称
            source_config: 来源配置（查询条件等）

        Returns:
            创建的导入批次
        """
        batch = ImportBatch(
            project_id=project_id,
            source_type=source_type,
            source_name=source_name,
            source_config=source_config or {}
        )
        self.db.add(batch)
        await self.db.commit()
        await self.db.refresh(batch)
        return batch

    async def import_from_smarteye_task(
        self,
        project_id: UUID,
        task_id: UUID,
        download_ids: Optional[List[UUID]] = None
    ) -> ImportBatch:
        """
        从SmartEye任务导入数据到项目

        Args:
            project_id: 目标项目ID
            task_id: SmartEye任务ID
            download_ids: 指定要导入的下载记录ID列表（None表示全部）

        Returns:
            导入批次记录
        """
        # 1. 创建导入批次
        batch = await self.create_import_batch(
            project_id=project_id,
            source_type="smarteye",
            source_name=f"SmartEye Task {task_id}",
            source_config={"task_id": str(task_id)}
        )

        # 2. 查询要导入的下载记录
        query = select(SmartEyeDownload).where(
            SmartEyeDownload.task_id == task_id,
            SmartEyeDownload.download_status == "completed"
        )
        if download_ids:
            query = query.where(SmartEyeDownload.id.in_(download_ids))

        result = await self.db.execute(query)
        downloads = result.scalars().all()

        # 3. 批量创建Asset记录（零拷贝引用）
        success_count = 0
        failed_count = 0

        for download in downloads:
            try:
                # 检查是否已经导入过（通过content_hash去重）
                existing = await self.db.execute(
                    select(Asset).where(
                        and_(
                            Asset.project_id == project_id,
                            Asset.content_hash == download.content_hash
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    print(f"⏭️  跳过重复图片: {download.blob_path}")
                    continue

                # 创建Asset记录（零拷贝：直接引用blob_path）
                asset = Asset(
                    project_id=project_id,
                    batch_id=batch.id,
                    source_type="smarteye",
                    source_record_id=download.id,
                    blob_path=download.blob_path,  # 零拷贝：不复制文件
                    content_hash=download.content_hash,
                    original_name=f"{download.source_id}.{download.format or 'jpg'}",
                    file_size=download.file_size,
                    width=download.width,
                    height=download.height,
                    format=download.format,
                    capture_time=download.timestamp,
                    device_info={
                        "platform": download.platform,
                        "device_name": download.device_name,
                        "device_id": download.device_id
                    } if download.device_name else None,
                    annotation_status="pending"
                )
                self.db.add(asset)
                success_count += 1

            except Exception as e:
                print(f"❌ 导入失败: {download.blob_path}, 错误: {e}")
                failed_count += 1

        # 4. 更新批次统计
        batch.total_count = len(downloads)
        batch.success_count = success_count
        batch.failed_count = failed_count

        # 5. 更新项目统计
        project = await self.db.get(Project, project_id)
        if project:
            project.total_assets += success_count

        await self.db.commit()
        await self.db.refresh(batch)

        return batch

    async def import_from_upload(
        self,
        project_id: UUID,
        files: List[Any],  # UploadFile对象
        batch_name: Optional[str] = None
    ) -> ImportBatch:
        """
        从用户上传导入文件

        Args:
            project_id: 目标项目ID
            files: 上传的文件列表
            batch_name: 批次名称

        Returns:
            导入批次记录
        """
        # 1. 创建导入批次
        batch = await self.create_import_batch(
            project_id=project_id,
            source_type="upload",
            source_name=batch_name or f"Upload {len(files)} files"
        )

        success_count = 0
        failed_count = 0

        for file in files:
            try:
                # 读取文件内容
                content = await file.read()

                # 上传到存储（会自动计算hash和去重）
                blob_info = await self.storage.upload_blob(
                    content=content,
                    content_type=file.content_type,
                    metadata={
                        "source": "upload",
                        "original_filename": file.filename
                    }
                )

                # 检查是否已存在
                existing = await self.db.execute(
                    select(Asset).where(
                        and_(
                            Asset.project_id == project_id,
                            Asset.content_hash == blob_info["content_hash"]
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    print(f"⏭️  跳过重复文件: {file.filename}")
                    continue

                # 创建Asset记录
                asset = Asset(
                    project_id=project_id,
                    batch_id=batch.id,
                    source_type="upload",
                    blob_path=blob_info["blob_path"],
                    content_hash=blob_info["content_hash"],
                    original_name=file.filename,
                    file_size=blob_info["file_size"],
                    width=blob_info.get("width"),
                    height=blob_info.get("height"),
                    format=blob_info.get("format"),
                    annotation_status="pending"
                )
                self.db.add(asset)
                success_count += 1

            except Exception as e:
                print(f"❌ 上传失败: {file.filename}, 错误: {e}")
                failed_count += 1

        # 更新批次统计
        batch.total_count = len(files)
        batch.success_count = success_count
        batch.failed_count = failed_count

        # 更新项目统计
        project = await self.db.get(Project, project_id)
        if project:
            project.total_assets += success_count

        await self.db.commit()
        await self.db.refresh(batch)

        return batch

    async def get_batch_details(self, batch_id: UUID) -> Optional[ImportBatch]:
        """获取导入批次详情"""
        return await self.db.get(ImportBatch, batch_id)

    async def list_batches(
        self,
        project_id: UUID,
        skip: int = 0,
        limit: int = 20
    ) -> List[ImportBatch]:
        """列出项目的导入批次"""
        result = await self.db.execute(
            select(ImportBatch)
            .where(ImportBatch.project_id == project_id)
            .order_by(ImportBatch.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
