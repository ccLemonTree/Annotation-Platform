"""
统计分析API路由
提供项目、资产、标注的统计数据
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import String, case, cast, select, func, and_, true
from sqlalchemy.dialects.postgresql import JSONB
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
from api.asset_routes import scoped_project


router = APIRouter()

@router.get('/projects/{project_id}/distribution')
async def project_distribution(project_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await scoped_project(db, project_id, user.tenant_id)
    is_pg = db.get_bind().dialect.name == 'postgresql'
    images_by_source = dict((await db.execute(
        select(Asset.source_type, func.count()).where(Asset.project_id == project_id).group_by(Asset.source_type)
    )).all())
    images_by_status = dict((await db.execute(
        select(Asset.annotation_status, func.count()).where(Asset.project_id == project_id).group_by(Asset.annotation_status)
    )).all())
    total_assets = sum(images_by_status.values())
    dev_expr = func.coalesce(
        func.nullif(Asset.device_info['device_id'].as_string(), ''),
        func.nullif(Asset.device_info['deviceId'].as_string(), ''),
        '未知',
    )
    images_by_device = dict((await db.execute(
        select(dev_expr, func.count()).where(Asset.project_id == project_id).group_by(dev_expr)
    )).all())
    qual_expr = case((Asset.quality_score.is_not(None), cast(Asset.quality_score, String)), else_='未评分')
    images_by_quality = dict((await db.execute(
        select(qual_expr, func.count()).where(Asset.project_id == project_id).group_by(qual_expr)
    )).all())

    if is_pg:
        is_neg_expr = and_(
            func.jsonb_typeof(Annotation.annotation_data['boxes']) == 'array',
            func.jsonb_array_length(Annotation.annotation_data['boxes']) == 0,
        )
        boxes_arr = case(
            (func.jsonb_typeof(Annotation.annotation_data['boxes']) == 'array', Annotation.annotation_data['boxes']),
            else_=cast('[]', JSONB),
        )
        box_elems = func.jsonb_array_elements(boxes_arr).table_valued('value')
        label_expr = func.coalesce(cast(box_elems.c.value, JSONB)['label'].as_string(), '未知')
    else:
        is_neg_expr = and_(
            func.json_type(Annotation.annotation_data, '$.boxes') == 'array',
            func.json_array_length(Annotation.annotation_data, '$.boxes') == 0,
        )
        boxes_src = case(
            (func.json_type(Annotation.annotation_data, '$.boxes') == 'array', Annotation.annotation_data),
            else_='{"boxes":[]}',
        )
        box_elems = func.json_each(boxes_src, '$.boxes').table_valued('value')
        label_expr = func.coalesce(func.json_extract(box_elems.c.value, '$.label'), '未知')

    ann_total, neg_count = (await db.execute(
        select(func.count(Annotation.id), func.coalesce(func.sum(case((is_neg_expr, 1), else_=0)), 0))
        .where(Annotation.project_id == project_id, Annotation.is_latest.is_(True))
    )).one()
    boxes_by_label = dict((await db.execute(
        select(label_expr, func.count())
        .select_from(Annotation)
        .join(box_elems, true())
        .where(Annotation.project_id == project_id, Annotation.is_latest.is_(True))
        .group_by(label_expr)
    )).all())
    return dict(
        boxes_by_label=boxes_by_label,
        images_by_source=images_by_source,
        images_by_status=images_by_status,
        images_by_device=images_by_device,
        images_by_quality=images_by_quality,
        unannotated_images=total_assets - int(ann_total or 0),
        negative_images=int(neg_count or 0),
    )


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

    # 单条 GROUP BY 统计各状态的资产数与总数
    status_rows = dict((await db.execute(
        select(Asset.annotation_status, func.count(Asset.id))
        .where(Asset.project_id == project_id)
        .group_by(Asset.annotation_status)
    )).all())
    total_assets = sum(status_rows.values())
    assets_by_status = {
        status_value: status_rows.get(status_value, 0)
        for status_value in ["pending", "annotating", "completed", "reviewed"]
    }

    # 统计已标注和已审核的资产
    annotated_count = assets_by_status.get("completed", 0) + assets_by_status.get("reviewed", 0)
    reviewed_count = assets_by_status.get("reviewed", 0)

    # 计算进度
    annotation_progress = (annotated_count / total_assets * 100) if total_assets > 0 else 0
    review_progress = (reviewed_count / total_assets * 100) if total_assets > 0 else 0

    # 单条 GROUP BY 统计标注的审核状态（直接按 Annotation.project_id 过滤省去 Asset JOIN）
    review_rows = dict((await db.execute(
        select(Annotation.review_status, func.count(Annotation.id))
        .where(Annotation.project_id == project_id, Annotation.is_latest.is_(True))
        .group_by(Annotation.review_status)
    )).all())
    annotations_by_review = {
        review_status: review_rows.get(review_status, 0)
        for review_status in ["pending", "approved", "rejected"]
    }

    # 统计最近7天的活动
    seven_days_ago = datetime.utcnow() - timedelta(days=7)

    recent_assets = await db.scalar(select(func.count(Asset.id)).where(
        and_(
            Asset.project_id == project_id,
            Asset.created_at >= seven_days_ago
        )
    )) or 0

    recent_annotations = await db.scalar(
        select(func.count(Annotation.id)).where(
            Annotation.project_id == project_id,
            Annotation.created_at >= seven_days_ago,
        )
    ) or 0

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
    # 单条 GROUP BY 统计项目状态与类型分布
    proj_rows = (await db.execute(
        select(Project.status, Project.task_type, func.count(Project.id))
        .where(Project.tenant_id == tenant_id)
        .group_by(Project.status, Project.task_type)
    )).all()
    total_projects = sum(cnt for _, _, cnt in proj_rows)
    active_projects = sum(cnt for st, _, cnt in proj_rows if st == "active")
    projects_by_task_type = {t: 0 for t in ["detection", "classification", "segmentation", "caption", "vector"]}
    for _, t_type, cnt in proj_rows:
        t_key = t_type.value if hasattr(t_type, "value") else str(t_type)
        if t_key in projects_by_task_type:
            projects_by_task_type[t_key] += cnt

    # 单条 GROUP BY 统计图片库资产数、按来源分布与存储体积
    img_rows = (await db.execute(
        select(LibraryImage.source_type, func.count(LibraryImage.id), func.coalesce(func.sum(LibraryImage.file_size), 0))
        .where(LibraryImage.tenant_id == tenant_id)
        .group_by(LibraryImage.source_type)
    )).all()
    total_assets = sum(cnt for _, cnt, _ in img_rows)
    storage_used = sum(int(sz or 0) for _, _, sz in img_rows)
    assets_by_source = {src: 0 for src in ["upload", "smarteye", "import"]}
    for src, cnt, _ in img_rows:
        if src in assets_by_source:
            assets_by_source[src] += cnt

    # 统计标注总数（直接 JOIN Project 省去百万行 Asset JOIN）
    total_annotations = await db.scalar(
        select(func.count(Annotation.id))
        .join(Project, Annotation.project_id == Project.id)
        .where(Project.tenant_id == tenant_id, Annotation.is_latest.is_(True))
    ) or 0

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
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)

    # 项目概览与30天新增合并单条查询
    total_projects, active_projects, new_projects_30d = (await db.execute(
        select(
            func.count(Project.id),
            func.coalesce(func.sum(case((Project.status == "active", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Project.created_at >= thirty_days_ago, 1), else_=0)), 0),
        ).where(Project.tenant_id == tenant_id)
    )).one()

    # 资产概览与30天新增
    used_image = select(Asset.id).where(Asset.image_id == LibraryImage.id).exists()
    total_assets, pending_assets, new_assets_30d = (await db.execute(
        select(
            func.count(LibraryImage.id),
            func.coalesce(func.sum(case((~used_image, 1), else_=0)), 0),
            func.coalesce(func.sum(case((LibraryImage.created_at >= thirty_days_ago, 1), else_=0)), 0),
        ).where(LibraryImage.tenant_id == tenant_id)
    )).one()
    project_references = await db.scalar(
        select(func.count(Asset.id)).join(Project).where(Project.tenant_id == tenant_id)
    ) or 0

    # 标注概览（直接通过 Annotation.project_id JOIN Project 省去 Asset 表百万行 JOIN）
    total_annotations, approved_annotations = (await db.execute(
        select(
            func.count(Annotation.id),
            func.coalesce(func.sum(case((Annotation.review_status == "approved", 1), else_=0)), 0),
        )
        .join(Project, Annotation.project_id == Project.id)
        .where(Project.tenant_id == tenant_id, Annotation.is_latest.is_(True))
    )).one()

    new_annotations_30d = await db.scalar(
        select(func.count(Annotation.id))
        .join(Project, Annotation.project_id == Project.id)
        .where(Project.tenant_id == tenant_id, Annotation.created_at >= thirty_days_ago)
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
