"""
项目管理API路由
项目的增删改查和统计
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from typing import List, Optional
from uuid import UUID
from typing import Literal
from models import Asset
from services.annotation_scope import eligible_assets

from database import get_db_session as get_db
from models import User, Project
from models.project import ProjectStatus, TaskType
from schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectStats, ProjectLabelRemovalPreview, ProjectLabelRemoval
from schemas.common import PaginatedResponse, PaginationParams
from api.deps import get_current_user, get_tenant_id, get_current_superuser
from services.project_labels import sync_project_labels, label_removal_state, remove_project_label


router = APIRouter()


@router.post('/{project_id}/annotations/remove-label/preview')
async def preview_label_removal(project_id:UUID,request:ProjectLabelRemovalPreview,
    db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    _,_,summary=await label_removal_state(db,project_id,user.tenant_id,request.label)
    return summary


@router.post('/{project_id}/annotations/remove-label')
async def apply_label_removal(project_id:UUID,request:ProjectLabelRemoval,
    db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    summary=await remove_project_label(db,project_id,user,request)
    await db.commit()
    return summary


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建新项目

    - **project_name**: 项目名称
    - **task_type**: 任务类型（detection/classification/segmentation/ocr）
    - **description**: 项目描述
    """
    # 检查项目名称是否已存在
    stmt = select(Project).where(
        Project.tenant_id == tenant_id,
        Project.project_name == project_data.project_name
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="项目名称已存在"
        )

    # 创建项目
    db_project = Project(
        tenant_id=tenant_id,
        project_name=project_data.project_name,
        task_type=project_data.task_type,
        description=project_data.description,
        label_schema=project_data.label_schema,
        status=ProjectStatus.ACTIVE,
        total_assets=0,
        annotated_count=0,
        reviewed_count=0
    )

    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)

    return db_project


@router.get("", response_model=PaginatedResponse[ProjectResponse])
async def list_projects(
    pagination: PaginationParams = Depends(),
    status_filter: Optional[ProjectStatus] = Query(None, alias="status", description="项目状态筛选"),
    task_type: Optional[TaskType] = Query(None),
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取项目列表（分页）

    - 仅返回当前租户下的项目
    - 支持按状态筛选
    """
    # 构建查询条件
    conditions = [Project.tenant_id == tenant_id]
    if status_filter:
        conditions.append(Project.status == status_filter)
    if task_type:
        conditions.append(Project.task_type == task_type)

    # 查询总数
    count_stmt = select(func.count(Project.id)).where(*conditions)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar()

    # 查询项目列表
    stmt = (
        select(Project)
        .where(*conditions)
        .offset(pagination.skip)
        .limit(pagination.limit)
        .order_by(Project.created_at.desc())
    )
    result = await db.execute(stmt)
    projects = result.scalars().all()

    return PaginatedResponse(
        items=projects,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取项目详情"""
    project = await db.get(Project, project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 验证租户权限
    if project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此项目"
        )

    return project


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新项目"""
    project = await db.get(Project, project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 验证租户权限
    if project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此项目"
        )

    if "label_schema" in project_data.model_fields_set:
        await sync_project_labels(db, project, project_data, current_user.id)
    elif project_data.label_renames:
        raise HTTPException(400, '类别重命名需要同时提交新类别配置')

    # 更新项目信息
    if project_data.project_name is not None:
        project.project_name = project_data.project_name
    if project_data.description is not None:
        project.description = project_data.description
    if project_data.status is not None:
        project.status = project_data.status
    await db.commit()
    await db.refresh(project)

    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除项目"""
    project = await db.get(Project, project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 验证租户权限
    if project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此项目"
        )

    await db.delete(project)
    await db.commit()

    return None


@router.get("/{project_id}/stats", response_model=ProjectStats)
async def get_project_stats(
    project_id: UUID,
    scope:Literal['all','annotation']='all',
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取项目统计信息"""
    project = await db.get(Project, project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 验证租户权限
    if project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此项目"
        )

    if scope=='annotation':
        statuses=(await db.execute(select(Asset.annotation_status,func.count()).where(
            Asset.project_id==project.id,eligible_assets(project)).group_by(Asset.annotation_status))).all()
        counts=dict(statuses);total=sum(counts.values());annotated=counts.get('completed',0)+counts.get('reviewed',0);reviewed=counts.get('reviewed',0)
        return ProjectStats(project_id=project.id,total_assets=total,annotated_count=annotated,reviewed_count=reviewed,
            annotation_progress=100*annotated/total if total else 0,review_progress=100*reviewed/total if total else 0)
    # 计算进度
    annotation_progress = (project.annotated_count / project.total_assets * 100) if project.total_assets > 0 else 0
    review_progress = (project.reviewed_count / project.total_assets * 100) if project.total_assets > 0 else 0

    return ProjectStats(
        project_id=project.id,
        total_assets=project.total_assets,
        annotated_count=project.annotated_count,
        reviewed_count=project.reviewed_count,
        annotation_progress=annotation_progress,
        review_progress=review_progress
    )
