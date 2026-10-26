"""
统计分析API路由
提供项目、资产、标注的统计数据
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timedelta

from database import get_db_session as get_db
from models.user import User
from models.project import Project
from models.asset import Asset
from models.image import LibraryImage
from models.annotation import Annotation
from api.deps import get_current_user, get_tenant_id
from pydantic import BaseModel
from collections import Counter
from api.asset_routes import scoped_project


router = APIRouter()

@router.get('/projects/{project_id}/distribution')
async def project_distribution(project_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await scoped_project(db, project_id, user.tenant_id)
    assets = (await db.execute(select(Asset).where(Asset.project_id == project_id))).scalars().all()
    annotations = (await db.execute(select(Annotation).join(Asset).where(
        Asset.project_id == project_id, Annotation.is_latest.is_(True)))).scalars().all()
    boxes = Counter(b.get('label', '未知') for a in annotations for b in a.annotation_data.get('boxes', []))
    return dict(boxes_by_label=dict(boxes), images_by_source=dict(Counter(a.source_type for a in assets)),
        images_by_status=dict(Counter(a.annotation_status for a in assets)),
        images_by_device=dict(Counter(str((a.device_info or {}).get('device_id') or (a.device_info or {}).get('deviceId') or '未知') for a in assets)),
        images_by_quality=dict(Counter(str(a.quality_score) if a.quality_score is not None else '未评分' for a in assets)),
        unannotated_images=len(assets)-len(annotations), negative_images=sum(not a.annotation_data.get('boxes') for a in annotations))


class ProjectStatsResponse(BaseModel):
    """项目统计响应"""
    total_assets: int
    annotated_count: int
    reviewed_count: int
    annotation_progress: float
    review_progress: float
    assets_by_status: Dict[str, int]
    annotations_by_review: Dict[str, int]
    recent_activity: Dict[str, int]


class TenantStatsResponse(BaseModel):
    """租户统计响应"""
    total_projects: int
    active_projects: int
    total_assets: int
    total_annotations: int
    storage_used: int
    projects_by_task_type: Dict[str, int]
    assets_by_source: Dict[str, int]


class DashboardStatsResponse(BaseModel):
    """Dashboard统计响应"""
    projects_overview: Dict[str, Any]
    assets_overview: Dict[str, Any]
    annotations_overview: Dict[str, Any]
    recent_trends: Dict[str, Any]


@router.get("/projects/{project_id}/stats", response_model=ProjectStatsResponse)
async def get_project_statistics(
    project_id: UUID,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取项目详细统计信息

    包括:
    - 资产总数和各状态分布
    - 标注进度和审核进度
    - 最近活动统计
    """
    # 验证项目
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 统计资产总数
    total_assets_stmt = select(func.count(Asset.id)).where(
        Asset.project_id == project_id
    )
    total_assets = await db.scalar(total_assets_stmt) or 0

    # 统计各状态的资产数
    assets_by_status = {}
    for status_value in ["pending", "annotating", "completed", "reviewed"]:
        count_stmt = select(func.count(Asset.id)).where(
            and_(
                Asset.project_id == project_id,
                Asset.annotation_status == status_value
            )
        )
        count = await db.scalar(count_stmt) or 0
        assets_by_status[status_value] = count

    # 统计已标注和已审核的资产
    annotated_count = assets_by_status.get("completed", 0) + assets_by_status.get("reviewed", 0)
    reviewed_count = assets_by_status.get("reviewed", 0)

    # 计算进度
    annotation_progress = (annotated_count / total_assets * 100) if total_assets > 0 else 0
    review_progress = (reviewed_count / total_assets * 100) if total_assets > 0 else 0

    # 统计标注的审核状态
    annotations_by_review = {}
    for review_status in ["pending", "approved", "rejected"]:
        count_stmt = (
            select(func.count(Annotation.id))
            .join(Asset)
            .where(
                and_(
                    Asset.project_id == project_id,
                    Annotation.review_status == review_status,
                    Annotation.is_latest.is_(True)
                )
            )
        )
        count = await db.scalar(count_stmt) or 0
        annotations_by_review[review_status] = count

    # 统计最近7天的活动
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    recent_assets_stmt = select(func.count(Asset.id)).where(
        and_(
            Asset.project_id == project_id,
            Asset.created_at >= seven_days_ago
        )
    )
    recent_assets = await db.scalar(recent_assets_stmt) or 0

    recent_annotations_stmt = (
        select(func.count(Annotation.id))
        .join(Asset)
        .where(
            and_(
                Asset.project_id == project_id,
                Annotation.created_at >= seven_days_ago
            )
        )
    )
    recent_annotations = await db.scalar(recent_annotations_stmt) or 0

    recent_activity = {
        "assets_added": recent_assets,
        "annotations_created": recent_annotations,
    }

    return ProjectStatsResponse(
        total_assets=total_assets,
        annotated_count=annotated_count,
        reviewed_count=reviewed_count,
        annotation_progress=round(annotation_progress, 2),
        review_progress=round(review_progress, 2),
        assets_by_status=assets_by_status,
        annotations_by_review=annotations_by_review,
        recent_activity=recent_activity
    )


@router.get("/tenant/stats", response_model=TenantStatsResponse)
async def get_tenant_statistics(
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取租户级别统计信息

    包括:
    - 项目总数和活跃项目
    - 资产和标注总数
    - 存储使用情况
    - 各类型分布
    """
    # 统计项目
    total_projects_stmt = select(func.count(Project.id)).where(
        Project.tenant_id == tenant_id
    )
    total_projects = await db.scalar(total_projects_stmt) or 0

    active_projects_stmt = select(func.count(Project.id)).where(
        and_(
            Project.tenant_id == tenant_id,
            Project.status == "active"
        )
    )
    active_projects = await db.scalar(active_projects_stmt) or 0

    # 统计资产总数
    total_assets_stmt = (
        select(func.count(LibraryImage.id)).where(LibraryImage.tenant_id == tenant_id)
    )
    total_assets = await db.scalar(total_assets_stmt) or 0

    # 统计标注总数
    total_annotations_stmt = (
        select(func.count(Annotation.id))
        .join(Asset)
        .join(Project)
        .where(Project.tenant_id == tenant_id, Annotation.is_latest.is_(True))
    )
    total_annotations = await db.scalar(total_annotations_stmt) or 0

    # 统计存储使用（字节）
    storage_used_stmt = (
        select(func.sum(LibraryImage.file_size)).where(LibraryImage.tenant_id == tenant_id)
    )
    storage_used = await db.scalar(storage_used_stmt) or 0

    # 按任务类型统计项目
    projects_by_task_type = {}
    for task_type in ["detection", "classification", "segmentation", "caption", "vector"]:
        count_stmt = select(func.count(Project.id)).where(
            and_(
                Project.tenant_id == tenant_id,
                Project.task_type == task_type
            )
        )
        count = await db.scalar(count_stmt) or 0
        projects_by_task_type[task_type] = count

    # 按来源统计资产
    assets_by_source = {}
    for source_type in ["upload", "smarteye", "import"]:
        count_stmt = (
            select(func.count(LibraryImage.id)).where(LibraryImage.tenant_id == tenant_id,
                LibraryImage.source_type == source_type)
        )
        count = await db.scalar(count_stmt) or 0
        assets_by_source[source_type] = count

    return TenantStatsResponse(
        total_projects=total_projects,
        active_projects=active_projects,
        total_assets=total_assets,
        total_annotations=total_annotations,
        storage_used=storage_used,
        projects_by_task_type=projects_by_task_type,
        assets_by_source=assets_by_source
    )


@router.get("/dashboard", response_model=DashboardStatsResponse)
async def get_dashboard_statistics(
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取Dashboard概览统计

    综合展示所有关键指标和趋势
    """
    # 项目概览
    total_projects = await db.scalar(
        select(func.count(Project.id)).where(Project.tenant_id == tenant_id)
    ) or 0

    active_projects = await db.scalar(
        select(func.count(Project.id)).where(
            and_(Project.tenant_id == tenant_id, Project.status == "active")
        )
    ) or 0

    # 资产概览
    total_assets = await db.scalar(
        select(func.count(LibraryImage.id)).where(LibraryImage.tenant_id == tenant_id)
    ) or 0

    used_image = select(Asset.id).where(Asset.image_id == LibraryImage.id).exists()
    pending_assets = await db.scalar(select(func.count(LibraryImage.id)).where(
        LibraryImage.tenant_id == tenant_id, ~used_image)) or 0
    project_references = await db.scalar(select(func.count(Asset.id)).join(Project)
        .where(Project.tenant_id == tenant_id)) or 0

    # 标注概览
    total_annotations = await db.scalar(
        select(func.count(Annotation.id))
        .join(Asset)
        .join(Project)
        .where(Project.tenant_id == tenant_id, Annotation.is_latest.is_(True))
    ) or 0

    approved_annotations = await db.scalar(
        select(func.count(Annotation.id))
        .join(Asset)
        .join(Project)
        .where(
            and_(
                Project.tenant_id == tenant_id,
                Annotation.review_status == "approved",
                Annotation.is_latest.is_(True)
            )
        )
    ) or 0

    # 最近趋势（30天）
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)

    new_projects_30d = await db.scalar(
        select(func.count(Project.id)).where(
            and_(
                Project.tenant_id == tenant_id,
                Project.created_at >= thirty_days_ago
            )
        )
    ) or 0

    new_assets_30d = await db.scalar(
        select(func.count(LibraryImage.id)).where(LibraryImage.tenant_id == tenant_id,
            LibraryImage.created_at >= thirty_days_ago)
    ) or 0

    new_annotations_30d = await db.scalar(
        select(func.count(Annotation.id))
        .join(Asset)
        .join(Project)
        .where(
            and_(
                Project.tenant_id == tenant_id,
                Annotation.created_at >= thirty_days_ago
            )
        )
    ) or 0

    return DashboardStatsResponse(
        projects_overview={
            "total": total_projects,
            "active": active_projects,
            "inactive": total_projects - active_projects,
        },
        assets_overview={
            "total": total_assets,
            "pending": pending_assets,
            "in_progress": total_assets - pending_assets,
            "project_references": project_references,
        },
        annotations_overview={
            "total": total_annotations,
            "approved": approved_annotations,
            "pending_review": total_annotations - approved_annotations,
        },
        recent_trends={
            "new_projects_30d": new_projects_30d,
            "new_assets_30d": new_assets_30d,
            "new_annotations_30d": new_annotations_30d,
        }
    )
