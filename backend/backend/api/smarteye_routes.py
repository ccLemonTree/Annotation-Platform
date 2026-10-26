"""
SmartEye API路由
SmartEye数据拉取、零拷贝导入
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from api.deps import get_current_user, get_tenant_id
from models import User

from database import get_db_session
from models import SmarteyeTask, SmarteeyeDownload, Asset, Project, TaskStatus
from services.storage import get_storage
from services.smarteye_service import get_smarteye_service


router = APIRouter(dependencies=[Depends(get_current_user)])


async def scoped_task(db, task_id, tenant_id):
    task = await db.scalar(select(SmarteyeTask).where(
        SmarteyeTask.id == task_id, SmarteyeTask.tenant_id == tenant_id))
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


# ==================== Pydantic模型 ====================

class PlatformInfo(BaseModel):
    """平台信息"""
    platform_id: str
    platform_name: str
    supported: bool


class CreateTaskRequest(BaseModel):
    """创建下载任务请求"""
    tenant_id: int = Field(..., description="租户ID")
    task_name: str = Field(..., min_length=1, max_length=200, description="任务名称")
    platform: str = Field(..., description="平台名称: xiaohongshu/douyin/weibo")
    keyword: str = Field(..., min_length=1, description="搜索关键词")
    target_count: int = Field(100, ge=1, le=10000, description="目标下载数")


class TaskResponse(BaseModel):
    """任务响应"""
    id: int
    task_name: str
    platform: str
    keyword: str
    target_count: int
    status: str
    downloaded_count: int
    failed_count: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class ImportRequest(BaseModel):
    """零拷贝导入请求"""
    task_id: int = Field(..., description="SmartEye任务ID")
    project_id: UUID = Field(..., description="目标项目ID")
    download_ids: Optional[List[int]] = Field(None, description="指定下载记录ID（空则导入全部）")


class ImportResponse(BaseModel):
    """导入响应"""
    success: bool
    imported_count: int
    skipped_count: int
    message: str


# ==================== API端点 ====================

@router.get("/platforms", response_model=List[PlatformInfo])
async def get_platforms():
    """
    获取支持的平台列表
    """
    return [
        PlatformInfo(platform_id="xiaohongshu", platform_name="小红书", supported=True),
        PlatformInfo(platform_id="douyin", platform_name="抖音", supported=True),
        PlatformInfo(platform_id="weibo", platform_name="微博", supported=True),
        PlatformInfo(platform_id="zhihu", platform_name="知乎", supported=False),
    ]


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: CreateTaskRequest,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    创建SmartEye下载任务

    **说明**：
    - 创建任务后会立即开始异步下载
    - 可通过GET /tasks/{id}查询任务进度
    """
    if request.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="不能为其他租户创建任务")
    # 创建任务
    task = SmarteyeTask(
        tenant_id=request.tenant_id,
        task_name=request.task_name,
        platform=request.platform,
        keyword=request.keyword,
        target_count=request.target_count,
        status=TaskStatus.PENDING
    )

    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 启动后台下载任务
    import asyncio
    smarteye_service = get_smarteye_service()

    # 在后台异步执行下载
    asyncio.create_task(smarteye_service.start_download_task(task.id, db))

    return task


@router.get("/tasks", response_model=List[TaskResponse])
async def list_tasks(
    tenant_id: Optional[int] = None,
    status: Optional[TaskStatus] = None,
    platform: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    current_tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    获取任务列表

    **过滤条件**：
    - tenant_id: 租户ID
    - status: 任务状态
    - platform: 平台名称
    """
    if tenant_id is not None and tenant_id != current_tenant_id:
        raise HTTPException(status_code=403, detail="不能查询其他租户的任务")
    query = select(SmarteyeTask).where(SmarteyeTask.tenant_id == current_tenant_id)
    if status:
        query = query.where(SmarteyeTask.status == status)
    if platform:
        query = query.where(SmarteyeTask.platform == platform)

    query = query.order_by(SmarteyeTask.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    tasks = result.scalars().all()

    return tasks


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """获取任务详情"""
    return await scoped_task(db, task_id, tenant_id)


@router.post("/import-to-project", response_model=ImportResponse)
async def import_to_project(
    request: ImportRequest,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    零拷贝导入：SmartEye下载 → 标注项目

    **核心逻辑**：
    1. 不复制文件，只在assets表创建引用记录
    2. blob_path指向同一文件
    3. source_record_id关联源下载记录
    4. 性能：1000张图片 < 5秒

    **示例**：
    ```json
    {
      "task_id": 1,
      "project_id": 10,
      "download_ids": [1, 2, 3]  // 可选，默认导入全部
    }
    ```
    """
    storage = get_storage()

    # 1. 验证任务存在
    await scoped_task(db, request.task_id, tenant_id)

    # 2. 验证项目存在
    project_result = await db.execute(
        select(Project).where(Project.id == request.project_id, Project.tenant_id == tenant_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail=f"项目 {request.project_id} 不存在")

    # 3. 查询要导入的下载记录
    query = select(SmarteeyeDownload).where(SmarteeyeDownload.task_id == request.task_id)
    if request.download_ids:
        query = query.where(SmarteeyeDownload.id.in_(request.download_ids))

    result = await db.execute(query)
    downloads = result.scalars().all()

    if not downloads:
        return ImportResponse(
            success=False,
            imported_count=0,
            skipped_count=0,
            message="没有找到可导入的下载记录"
        )

    # 4. 零拷贝导入（批量创建Asset记录）
    imported_count = 0
    skipped_count = 0

    for download in downloads:
        # 检查是否已导入（防止重复）
        existing = await db.execute(
            select(Asset).where(
                Asset.project_id == request.project_id,
                Asset.blob_path == download.blob_path
            )
        )
        if existing.scalar_one_or_none():
            skipped_count += 1
            continue

        # 创建Asset记录（零拷贝：只引用路径）
        asset = Asset(
            project_id=request.project_id,
            blob_path=download.blob_path,  # 关键：指向同一路径
            content_hash=download.content_hash,
            source_type="smarteye",  # 来源类型：smarteye
            source_record_id=download.id,  # 关键：关联源记录
            original_name=download.file_name,
            file_size=download.file_size,
            width=download.width,
            height=download.height,
            annotation_status="pending"
        )
        db.add(asset)

        # 更新引用计数
        download.reference_count += 1
        imported_count += 1

    # 5. 更新项目统计
    project.total_assets += imported_count

    await db.commit()

    return ImportResponse(
        success=True,
        imported_count=imported_count,
        skipped_count=skipped_count,
        message=f"成功导入 {imported_count} 张图片（零拷贝）"
    )


@router.get("/tasks/{task_id}/downloads")
async def get_task_downloads(
    task_id: int,
    skip: int = 0,
    limit: int = 50,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db_session)
):
    """获取任务的下载记录列表"""
    await scoped_task(db, task_id, tenant_id)
    # 查询下载记录
    query = select(SmarteeyeDownload).where(
        SmarteeyeDownload.task_id == task_id
    ).order_by(
        SmarteeyeDownload.created_at.desc()
    ).offset(skip).limit(limit)

    result = await db.execute(query)
    downloads = result.scalars().all()

    # 查询总数
    count_query = select(func.count()).select_from(SmarteeyeDownload).where(
        SmarteeyeDownload.task_id == task_id
    )
    count_result = await db.execute(count_query)
    total = count_result.scalar()

    storage = get_storage()

    return {
        "total": total,
        "items": [
            {
                "id": d.id,
                "file_name": d.file_name,
                "file_size": d.file_size,
                "width": d.width,
                "height": d.height,
                "platform": d.platform,
                "reference_count": d.reference_count,
                "created_at": d.created_at,
                "preview_url": storage.get_public_url(d.blob_path, expires=3600)
            }
            for d in downloads
        ]
    }
