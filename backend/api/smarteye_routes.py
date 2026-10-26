"""
SmartEye API路由
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import uuid

from models import get_db, SmartEyeTask, Platform
from services.smarteye.import_service import SmartEyeImportService

router = APIRouter()


# ===== Pydantic模型 =====

class CreateTaskRequest(BaseModel):
    """创建任务请求"""
    platform_id: uuid.UUID
    task_name: str
    start_time: datetime
    end_time: datetime
    device_id: Optional[str] = None
    alarm_type: Optional[str] = None


class ImportToProjectRequest(BaseModel):
    """导入到项目请求"""
    task_id: uuid.UUID
    project_id: uuid.UUID
    device_ids: Optional[List[str]] = None
    date_range: Optional[dict] = None
    quality_min: Optional[int] = None


class TaskResponse(BaseModel):
    """任务响应"""
    id: uuid.UUID
    task_name: str
    platform_id: uuid.UUID
    status: str
    progress: int
    total_count: int
    downloaded_count: int
    failed_count: int
    created_at: datetime

    class Config:
        from_attributes = True


# ===== 路由 =====

@router.get("/platforms", summary="获取平台列表")
async def list_platforms(db: AsyncSession = Depends(get_db)):
    """获取所有可用平台"""
    from sqlalchemy import select

    result = await db.execute(
        select(Platform).where(Platform.enabled == True)
    )
    platforms = result.scalars().all()

    return {
        "total": len(platforms),
        "platforms": [
            {
                "id": str(p.id),
                "platform_name": p.platform_name,
                "plat_id": p.plat_id,
                "description": p.description
            }
            for p in platforms
        ]
    }


@router.post("/tasks", response_model=TaskResponse, summary="创建下载任务")
async def create_task(
    request: CreateTaskRequest,
    db: AsyncSession = Depends(get_db)
):
    """创建SmartEye下载任务"""
    service = SmartEyeImportService(db)

    query_params = {
        "start_time": request.start_time.isoformat(),
        "end_time": request.end_time.isoformat(),
        "device_id": request.device_id,
        "alarm_type": request.alarm_type
    }

    task = await service.create_import_task(
        platform_id=request.platform_id,
        task_name=request.task_name,
        query_params=query_params
    )

    return task


@router.get("/tasks", summary="获取任务列表")
async def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """获取SmartEye任务列表"""
    from sqlalchemy import select, desc

    query = select(SmartEyeTask)

    if status:
        query = query.where(SmartEyeTask.status == status)

    query = query.order_by(desc(SmartEyeTask.created_at))
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    tasks = result.scalars().all()

    return {
        "total": len(tasks),
        "tasks": [
            {
                "id": str(t.id),
                "task_name": t.task_name,
                "status": t.status,
                "progress": t.progress,
                "total_count": t.total_count,
                "downloaded_count": t.downloaded_count,
                "created_at": t.created_at
            }
            for t in tasks
        ]
    }


@router.get("/tasks/{task_id}", summary="获取任务详情")
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """获取任务详细信息"""
    from sqlalchemy import select

    result = await db.execute(
        select(SmartEyeTask).where(SmartEyeTask.id == task_id)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "id": str(task.id),
        "task_name": task.task_name,
        "platform_id": str(task.platform_id) if task.platform_id else None,
        "query_params": task.query_params,
        "status": task.status,
        "progress": task.progress,
        "total_count": task.total_count,
        "downloaded_count": task.downloaded_count,
        "failed_count": task.failed_count,
        "storage_prefix": task.storage_prefix,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "error_message": task.error_message,
        "created_at": task.created_at
    }


@router.post("/import-to-project", summary="导入到标注项目")
async def import_to_project(
    request: ImportToProjectRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    从SmartEye任务导入图片到标注项目（零拷贝）

    核心功能：
    - 引用SmartEye已下载的图片，无需重复下载
    - 零拷贝，节省存储空间
    - 瞬间完成导入（1000张图片<5秒）
    """
    service = SmartEyeImportService(db)

    filters = {}
    if request.device_ids:
        filters["device_ids"] = request.device_ids
    if request.date_range:
        filters["date_range"] = request.date_range
    if request.quality_min:
        filters["quality_min"] = request.quality_min

    batch = await service.import_to_project(
        project_id=request.project_id,
        task_id=request.task_id,
        filters=filters
    )

    return {
        "batch_id": str(batch.id),
        "project_id": str(batch.project_id),
        "source_type": batch.source_type,
        "total_count": batch.total_count,
        "success_count": batch.success_count,
        "message": f"成功导入 {batch.success_count} 张图片（零拷贝）"
    }


@router.get("/tasks/{task_id}/downloads", summary="获取下载记录")
async def get_downloads(
    task_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """获取任务的下载记录（供预览）"""
    service = SmartEyeImportService(db)

    downloads = await service.get_download_records(task_id)

    # 分页
    total = len(downloads)
    downloads_page = downloads[offset:offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "downloads": [
            {
                "id": str(d.id),
                "device_name": d.device_name,
                "device_id": d.device_id,
                "target_type": d.target_type,
                "timestamp": d.timestamp,
                "blob_path": d.blob_path,
                "file_size": d.file_size,
                "width": d.width,
                "height": d.height,
                "download_status": d.download_status
            }
            for d in downloads_page
        ]
    }
