"""
项目管理API路由
项目的增删改查和统计
"""
import logging
import shutil
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import inspect, select, update, delete, func, or_
from starlette.concurrency import run_in_threadpool
from typing import List, Optional
from uuid import UUID
from typing import Literal
from models import (
    Annotation,
    Asset,
    AutomationJob,
    ExportSnapshot,
    ExportSnapshotJob,
    ImageUpload,
    MatchQuery,
    ProjectWorkItem,
    ProjectWorkJob,
    TrainingTask,
    ValidationRun,
    ValidationSet,
)
from services.annotation_scope import eligible_assets

from database import get_db_session as get_db
from models import User, Project
from models.project import ProjectStatus, TaskType
from schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectStats, ProjectLabelRemovalPreview, ProjectLabelRemoval
from schemas.common import PaginatedResponse, PaginationParams
from api.deps import get_current_user, get_tenant_id, get_current_superuser
from services.project_labels import (
    sync_project_labels,
    label_removal_state,
    remove_project_label,
    map_yolo_classes,
    yolo_mapping_batches,
    project_used_labels,
)
from services.project_access import project_condition
from services.storage import LocalStorage, get_storage
from api.asset_routes import scoped_project
from models import ProjectMember, ModelService
from schemas.project import ProjectAssignments, ProjectLabelsAdd, YoloClassMapping


router = APIRouter()
logger = logging.getLogger(__name__)


@router.post('/{project_id}/labels',response_model=ProjectResponse)
async def add_labels(project_id:UUID,request:ProjectLabelsAdd,db:AsyncSession=Depends(get_db),
                     user:User=Depends(get_current_superuser)):
    project=await scoped_project(db,project_id,user.tenant_id,for_update=True)
    schema=project.label_schema or {}
    labels=list(dict.fromkeys([*(schema.get('labels') or []),*request.labels]))
    project.label_schema={**schema,'labels':labels}
    await db.commit()
    await db.refresh(project)
    return project


@router.get('/{project_id}/yolo-class-mapping')
async def list_yolo_mapping_batches(project_id:UUID,db:AsyncSession=Depends(get_db),
                                    user:User=Depends(get_current_superuser)):
    project=await scoped_project(db,project_id,user.tenant_id)
    return await yolo_mapping_batches(db,project)


@router.post('/{project_id}/yolo-class-mapping')
async def apply_yolo_class_mapping(project_id:UUID,request:YoloClassMapping,db:AsyncSession=Depends(get_db),
                                   user:User=Depends(get_current_superuser)):
    project=await scoped_project(db,project_id,user.tenant_id)
    result=await map_yolo_classes(db,project,request,user.id)
    await db.commit()
    return result


@router.get('/{project_id}/assignments',response_model=ProjectAssignments)
async def get_assignments(project_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    await scoped_project(db,project_id,user.tenant_id)
    ids=(await db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id==project_id).order_by(ProjectMember.user_id))).all()
    return {'user_ids':ids}


@router.put('/{project_id}/assignments',response_model=ProjectAssignments)
async def set_assignments(project_id:UUID,request:ProjectAssignments,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    project=await db.scalar(select(Project).where(Project.id==project_id,Project.tenant_id==user.tenant_id).with_for_update())
    if project is None:raise HTTPException(404,'项目不存在')
    ids=sorted(set(request.user_ids))
    found=(await db.scalars(select(User.id).where(User.id.in_(ids),User.tenant_id==user.tenant_id,
        User.is_active.is_(True),User.role!='admin').with_for_update())).all()
    if len(found)!=len(ids):raise HTTPException(400,'只能分配同租户内已启用的普通用户')
    await db.execute(delete(ProjectMember).where(ProjectMember.project_id==project_id))
    db.add_all([ProjectMember(project_id=project_id,user_id=id) for id in ids])
    await db.commit()
    return {'user_ids':ids}


@router.get('/{project_id}/annotation-models')
async def annotation_models(project_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    from models.project_work import ProjectWorkJob, ProjectWorkItem
    await scoped_project(db,project_id,user.tenant_id)
    indexed=set((await db.execute(select(ProjectWorkJob.model['id'].as_string(),ProjectWorkJob.model['revision'].as_integer()).where(
        ProjectWorkJob.project_id==project_id,ProjectWorkJob.operation=='index',select(ProjectWorkItem.id).where(
            ProjectWorkItem.job_id==ProjectWorkJob.id,ProjectWorkItem.status=='completed').exists()).distinct())).all())
    rows=(await db.scalars(select(ModelService).where(ModelService.tenant_id==user.tenant_id,
        ModelService.kind=='embedding',ModelService.enabled.is_(True)).order_by(ModelService.key))).all()
    return [dict(id=str(row.id),name=row.name,revision=row.revision,kind=row.kind,enabled=row.enabled,connection_check=row.connection_check)
        for row in rows if (str(row.id),row.revision) in indexed]


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
    conditions = [Project.tenant_id == tenant_id,project_condition(db)]
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


@router.get("/{project_id}/used-labels")
async def get_project_used_labels(
    project_id: UUID,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目最新标注中已使用的去重标签列表"""
    project = await db.scalar(select(Project).where(Project.id == project_id, project_condition(db)))
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )
    if project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此项目",
        )
    return {"labels": await project_used_labels(db, project)}


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取项目详情"""
    project = await db.scalar(select(Project).where(Project.id==project_id,project_condition(db)))

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
    project = await db.scalar(select(Project).where(Project.id==project_id,project_condition(db)))

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

    if project_data.task_type is not None and project_data.task_type != project.task_type:
        old_type = project.task_type
        project = await scoped_project(db, project_id, tenant_id, for_update=True)
        if project.task_type != old_type:
            raise HTTPException(409, '项目任务类型已变化，请重新打开编辑窗口')
        if project_data.task_type == TaskType.SEGMENTATION:
            raise HTTPException(400, '图像分割暂不支持')
        dependencies = [select(Annotation.id).join(Asset).where(Asset.project_id == project.id).exists(),
                        select(Asset.id).where(Asset.project_id == project.id, Asset.source_type == 'model_crop').exists()]
        dependencies.extend(select(model.id).where(model.project_id == project.id).exists() for model in
                            (AutomationJob, ProjectWorkJob, MatchQuery, ExportSnapshot, ValidationSet, TrainingTask))
        if await db.scalar(select(or_(*dependencies))):
            raise HTTPException(409, '项目已有标注、处理结果或关联任务，无法修改任务类型')
        if 'base_label_schema' in project_data.model_fields_set and project_data.base_label_schema != project.label_schema:
            raise HTTPException(409, '项目类别配置已更新，请重新打开编辑窗口')
        if project_data.label_renames:
            raise HTTPException(400, '切换任务类型时无需同步旧标注类别')
        schema = project_data.label_schema if 'label_schema' in project_data.model_fields_set else project.label_schema
        if schema is not None:
            schema = dict(schema)
            if project_data.task_type not in (TaskType.CAPTION, TaskType.VECTOR):
                schema.pop('object_attributes', None)
            if project_data.task_type == TaskType.DETECTION:
                schema.pop('classification_mode', None)
        project.label_schema = schema
        project.task_type = project_data.task_type
    elif "label_schema" in project_data.model_fields_set:
        if project_data.label_schema!=project.label_schema or project_data.label_renames:
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
    """删除项目并清理项目衍生裁剪图与快照文件"""
    project = await db.scalar(
        select(Project).where(Project.id == project_id, project_condition(db)).with_for_update()
    )

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

    conn = await db.connection()
    tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))

    active_checks = []
    if {"project_work_jobs", "project_work_items"} <= tables:
        active_checks.append(
            select(ProjectWorkItem.id)
            .join(ProjectWorkJob, ProjectWorkJob.id == ProjectWorkItem.job_id)
            .where(
                ProjectWorkJob.project_id == project_id,
                ProjectWorkJob.options["workflow_cancelled"].as_boolean().is_not(True),
                ProjectWorkJob.options["queue_deleted"].as_boolean().is_not(True),
                ProjectWorkItem.status.in_(("pending", "running", "retry_wait")),
            )
            .exists()
        )
    if "automation_jobs" in tables:
        active_checks.append(
            select(AutomationJob.id)
            .where(AutomationJob.project_id == project_id, AutomationJob.status.in_(("pending", "running")))
            .exists()
        )
    if "export_snapshot_jobs" in tables:
        active_checks.append(
            select(ExportSnapshotJob.id)
            .where(
                ExportSnapshotJob.project_id == project_id,
                ExportSnapshotJob.status.in_(("queued", "pending", "running", "saving")),
            )
            .exists()
        )
    if "image_uploads" in tables:
        active_checks.append(
            select(ImageUpload.id)
            .where(
                ImageUpload.project_id == project_id,
                ImageUpload.status.in_(("uploading", "queued", "pending", "processing", "running")),
            )
            .exists()
        )
    if {"validation_sets", "validation_runs"} <= tables:
        active_checks.append(
            select(ValidationRun.id)
            .join(ValidationSet, ValidationSet.id == ValidationRun.set_id)
            .where(ValidationSet.project_id == project_id, ValidationRun.status.in_(("pending", "running")))
            .exists()
        )
    if "training_tasks" in tables:
        active_checks.append(
            select(TrainingTask.id)
            .where(TrainingTask.project_id == project_id, TrainingTask.status.in_(("pending", "running")))
            .exists()
        )
    if active_checks and await db.scalar(select(or_(*active_checks))):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="项目仍有等待或运行中的任务，请先取消或等待完成后再删除",
        )

    snapshot_files: list[str] = []
    if "export_snapshots" in tables:
        rows = (
            await db.execute(
                select(ExportSnapshot.blob_path, ExportSnapshot.manifest_path).where(
                    ExportSnapshot.project_id == project_id
                )
            )
        ).all()
        for blob_path, manifest_path in rows:
            if blob_path:
                snapshot_files.append(blob_path)
            if manifest_path:
                snapshot_files.append(manifest_path)

    await db.delete(project)
    await db.commit()

    storage = get_storage()
    for file_path in snapshot_files:
        try:
            await storage.delete_file(file_path)
        except Exception:
            logger.warning("Failed to remove project snapshot file %s", file_path, exc_info=True)
    if isinstance(storage, LocalStorage):
        try:
            crop_dir = storage.resolve_path(f"project-crops/{project_id}")
            if crop_dir.exists():
                await run_in_threadpool(shutil.rmtree, crop_dir, True)
        except Exception:
            logger.warning("Failed to remove project crop directory for %s", project_id, exc_info=True)

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
    project = await db.scalar(select(Project).where(Project.id==project_id,project_condition(db)))

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
