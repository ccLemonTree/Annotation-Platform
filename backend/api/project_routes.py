"""
项目管理API路由
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import uuid

from models import get_db, Project, Asset, ImportBatch, TaskType, ProjectStatus
from services.storage import get_storage

router = APIRouter()


# ===== Pydantic模型 =====

class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    project_name: str
    description: Optional[str] = None
    task_type: TaskType
    label_schema: dict


class ProjectResponse(BaseModel):
    """项目响应"""
    id: uuid.UUID
    project_name: str
    description: Optional[str]
    task_type: TaskType
    status: ProjectStatus
    total_assets: int
    annotated_count: int
    created_at: datetime

    class Config:
        from_attributes = True


# ===== 路由 =====

@router.post("/", response_model=ProjectResponse, summary="创建项目")
async def create_project(
    request: CreateProjectRequest,
    db: AsyncSession = Depends(get_db)
):
    """创建标注项目"""
    # 第一版：租户ID固定
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    project = Project(
        tenant_id=tenant_id,
        project_name=request.project_name,
        description=request.description,
        task_type=request.task_type,
        label_schema=request.label_schema,
        status=ProjectStatus.DRAFT
    )

    db.add(project)
    await db.commit()
    await db.refresh(project)

    return project


@router.get("/", summary="获取项目列表")
async def list_projects(
    status: Optional[ProjectStatus] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """获取项目列表"""
    query = select(Project)

    if status:
        query = query.where(Project.status == status)

    query = query.order_by(desc(Project.created_at))
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    projects = result.scalars().all()

    return {
        "total": len(projects),
        "projects": [
            {
                "id": str(p.id),
                "project_name": p.project_name,
                "description": p.description,
                "task_type": p.task_type,
                "status": p.status,
                "total_assets": p.total_assets,
                "annotated_count": p.annotated_count,
                "created_at": p.created_at
            }
            for p in projects
        ]
    }


@router.get("/{project_id}", summary="获取项目详情")
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """获取项目详细信息"""
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    return {
        "id": str(project.id),
        "project_name": project.project_name,
        "description": project.description,
        "task_type": project.task_type,
        "label_schema": project.label_schema,
        "status": project.status,
        "total_assets": project.total_assets,
        "annotated_count": project.annotated_count,
        "reviewed_count": project.reviewed_count,
        "created_at": project.created_at,
        "updated_at": project.updated_at
    }


@router.get("/{project_id}/assets", summary="获取项目图片")
async def list_project_assets(
    project_id: uuid.UUID,
    annotation_status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """获取项目的图片列表"""
    query = select(Asset).where(Asset.project_id == project_id)

    if annotation_status:
        query = query.where(Asset.annotation_status == annotation_status)

    query = query.order_by(desc(Asset.created_at))
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    assets = result.scalars().all()

    storage = get_storage()

    return {
        "total": len(assets),
        "assets": [
            {
                "id": str(a.id),
                "original_name": a.original_name,
                "file_size": a.file_size,
                "width": a.width,
                "height": a.height,
                "format": a.format,
                "annotation_status": a.annotation_status,
                "quality_score": a.quality_score,
                "capture_time": a.capture_time,
                "url": storage.get_public_url(a.blob_path),
                "source_type": a.source_type,
                "created_at": a.created_at
            }
            for a in assets
        ]
    }


@router.post("/{project_id}/upload", summary="上传图片")
async def upload_images(
    project_id: uuid.UUID,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    直接上传图片到项目

    与SmartEye导入不同：
    - SmartEye导入：零拷贝引用已下载的图片
    - 直接上传：从用户设备上传，保存到存储
    """
    # 验证项目存在
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 创建导入批次
    batch = ImportBatch(
        project_id=project_id,
        source_type="upload",
        source_name="直接上传",
        total_count=len(files)
    )
    db.add(batch)
    await db.flush()

    storage = get_storage()
    uploaded_assets = []

    for file in files:
        try:
            # 生成存储路径
            file_id = uuid.uuid4()
            blob_path = f"projects/{project_id}/uploads/{file_id}/{file.filename}"

            # 保存文件
            result = await storage.save_file(
                file.file,
                blob_path,
                metadata={
                    "original_name": file.filename,
                    "content_type": file.content_type
                }
            )

            # 创建Asset记录
            asset = Asset(
                project_id=project_id,
                batch_id=batch.id,
                source_type="upload",
                blob_path=result["path"],
                content_hash=result["hash"],
                original_name=file.filename,
                file_size=result["size"],
                annotation_status="pending"
            )
            db.add(asset)
            uploaded_assets.append(asset)

        except Exception as e:
            print(f"上传失败 {file.filename}: {e}")
            batch.failed_count += 1

    batch.success_count = len(uploaded_assets)
    project.total_assets += batch.success_count

    await db.commit()

    return {
        "batch_id": str(batch.id),
        "total": len(files),
        "success": batch.success_count,
        "failed": batch.failed_count,
        "message": f"成功上传 {batch.success_count}/{len(files)} 张图片"
    }


@router.delete("/{project_id}", summary="删除项目")
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """删除项目（级联删除所有资产）"""
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    await db.delete(project)
    await db.commit()

    return {"message": f"项目 {project.project_name} 已删除"}
