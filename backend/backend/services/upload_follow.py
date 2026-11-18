"""Attach freshly uploaded assets to the project's opted-in preannotation task."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from models import Annotation, AnnotationEvent, Asset, AutomationItem, AutomationJob, Project, ProjectWorkItem, ProjectWorkJob, User
from schemas.project_work import FOLLOW_UPLOAD_OPERATIONS
from services.annotation_scope import crops_only
from services.automation import automation_schema_matches, review_state
from services.image_semantics import validate_snapshots


def accepts_uploaded_crop_entry(job):
    return (job.options.get('workflow_streaming') and job.options.get('workflow_step')==0
        and job.options.get('workflow_can_follow',job.options.get('workflow_streaming'))
        and job.options.get('workflow_image_kind','originals')!='crops'
        and not job.options.get('workflow_cancelled'))


async def set_uploaded_crop_entry(db,job,destination_id):
    if not accepts_uploaded_crop_entry(job):
        raise HTTPException(409,'此工作流不能设置上传裁剪图入口')
    if destination_id is not None:
        destination=await db.get(ProjectWorkJob,destination_id)
        if (not destination or destination.project_id!=job.project_id
            or destination.options.get('workflow_id')!=str(job.id) or destination.options.get('workflow_cancelled')
            or destination.options.get('queue_deleted')):
            raise HTTPException(409,'请选择当前工作流中未取消的步骤')
    job.options={**job.options,'uploaded_crop_job_id':str(destination_id) if destination_id else None}


async def crop_accepts_originals(db, job):
    if 'crop_from_originals' in job.options:
        return job.options['crop_from_originals']
    sources = set((await db.scalars(select(Asset.source_type).select_from(ProjectWorkItem)
        .outerjoin(Asset, Asset.id == ProjectWorkItem.asset_id)
        .where(ProjectWorkItem.job_id == job.id).distinct())).all())
    return bool(sources) and not (sources & {None, 'model_crop'})


async def set_follow_uploads(db, job, enabled):
    if enabled:
        project = await db.scalar(select(Project).where(Project.id == job.project_id)
            .with_for_update().execution_options(populate_existing=True))
        if project is None:
            raise HTTPException(404, '项目不存在')
        actor = await db.get(User, job.user_id)
        if actor is None or not actor.is_active or actor.role != 'admin' or actor.tenant_id != project.tenant_id:
            raise HTTPException(409, '任务创建人已失去权限，请创建新的预标注任务')
        if isinstance(job, AutomationJob):
            if job.status == 'canceled' or project.task_type != 'detection':
                raise HTTPException(400, '请选择未取消的检测预标注或 AI 审核任务')
            if crops_only(project):
                raise HTTPException(409, '此项目仅标注裁剪图，上传原图不能自动加入此任务')
            snapshots = list(job.models.values())
        elif job.options.get('workflow_can_follow',job.options.get('workflow_streaming')) and job.options.get('workflow_step')==0:
            steps=(await db.scalars(select(ProjectWorkJob).where(ProjectWorkJob.project_id==job.project_id,
                ProjectWorkJob.options['workflow_id'].as_string()==str(job.id)))).all()
            snapshots=[step.model for step in steps]
        else:
            if job.operation not in FOLLOW_UPLOAD_OPERATIONS or job.options.get('workflow_id') or job.options.get('batch_root'):
                raise HTTPException(400, '此处理批次不支持接收新上传图片')
            if job.operation == 'crop' and not await crop_accepts_originals(db, job):
                raise HTTPException(409, '只有从原图开始的切图批次可以接收新上传图片')
            if job.operation not in ('crop', 'detect_attributes') and crops_only(project):
                raise HTTPException(409, '项目仅标注裁剪图，新上传的原图可加入切图批次')
            if job.operation == 'attributes' and await db.scalar(select(ProjectWorkItem.id).join(ProjectWorkJob).where(
                ProjectWorkJob.project_id == project.id,
                ProjectWorkItem.result['attribute_job_id'].as_string() == str(job.id)).limit(1)):
                raise HTTPException(400, '请在原图检测切图主批次开启接收新图')
            snapshots = [job.model, job.options.get('attribute_model')]
        if not matching_config(job, project):
            raise HTTPException(409, '项目配置已修改，请创建新的预标注任务')
        await validate_snapshots(db, project.tenant_id, snapshots, lock=False)
        for model in (AutomationJob, ProjectWorkJob):
            query = select(model.id).where(model.project_id == job.project_id, model.follow_uploads.is_(True))
            if isinstance(job, model):
                query = query.where(model.id != job.id)
            if await db.scalar(query.limit(1)):
                raise HTTPException(409, '已有任务在自动接收新图，请先关闭该任务的开关')
    job.follow_uploads = enabled
    return {'follow_uploads': enabled}


def matching_config(job, project):
    if isinstance(job, AutomationJob):
        return project.task_type == 'detection' and automation_schema_matches(job,project) and not crops_only(project)
    if job.options.get('workflow_streaming'):return True
    if job.operation == 'crop':
        return True
    if job.operation == 'quality':
        return job.options.get('quality_scope') == dict(task_type=project.task_type,
            annotation_scope=(project.label_schema or {}).get('annotation_scope', 'all')) and not crops_only(project)
    if job.operation == 'index':
        return project.task_type == 'vector' and not crops_only(project)
    return (project.task_type in ('caption', 'vector')
        and (job.operation == 'caption' or job.options.get('attribute_schema') == (project.label_schema or {}).get('object_attributes'))
        and (job.operation == 'detect_attributes' or not crops_only(project)))


async def lock_follow_job(db, project_id):
    # The detection worker also acquires its job lock before any project lock.
    job = await db.scalar(select(AutomationJob).where(AutomationJob.project_id == project_id,
        AutomationJob.follow_uploads.is_(True)).with_for_update())
    if job is not None:
        return job
    root=await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project_id,
        ProjectWorkJob.follow_uploads.is_(True)))
    if root is None:return None
    target=ProjectWorkJob.id==root.id
    if accepts_uploaded_crop_entry(root):
        target=or_(target,ProjectWorkJob.options['workflow_id'].as_string()==str(root.id))
    # Match workflow cancellation's job order, while allowing workers to hold key-share locks.
    locked=(await db.scalars(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project_id,target)
        .order_by(ProjectWorkJob.id).with_for_update(key_share=True).execution_options(populate_existing=True))).all()
    return next((job for job in locked if job.id==root.id and job.follow_uploads),None)


async def find_streaming_follow_job(db, project_id):
    return await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project_id,
        ProjectWorkJob.follow_uploads.is_(True),ProjectWorkJob.options['workflow_streaming'].as_boolean().is_(True)))


async def enqueue_uploads(db, job, new_assets, *, crop_entry_locked=True):
    if job is None or not new_assets:
        return 0, None
    project = await db.get(Project, job.project_id)
    if project is None or not matching_config(job, project):
        job.follow_uploads = False
        return 0, '项目配置已变更，已停止自动接收新图'
    if isinstance(job,ProjectWorkJob) and job.options.get('workflow_streaming'):
        from services.project_workflow import enqueue
        kind=job.options.get('workflow_image_kind','originals')
        entry_id=job.options.get('uploaded_crop_job_id')
        crops={asset.id for asset in new_assets if entry_id and asset.source_type=='model_crop'
            and (asset.device_info or {}).get('uploaded_crop')}
        ids=[asset.id for asset in new_assets if asset.id not in crops and
            (kind in ('all','uploads') or (asset.source_type=='model_crop')==(kind=='crops'))]
        entry_ids=[UUID(eid) for eid in (job.options.get('workflow_entry_job_ids') or [str(job.id)])]
        queued=0
        if ids:
            for eid in entry_ids:
                target_job=job if eid==job.id else await db.get(ProjectWorkJob,eid)
                if target_job and not target_job.options.get('workflow_cancelled'):
                    queued+=await enqueue(db,target_job,[Asset.id.in_(ids)],deduplicate=True)
        if not crops:return queued,None
        if not crop_entry_locked:return queued,'工作流在本次入库期间开启接收，本次裁剪图未自动入队'
        entry=await db.get(ProjectWorkJob,UUID(entry_id))
        if not entry or entry.options.get('workflow_cancelled') or entry.options.get('queue_deleted'):
            return queued,'上传裁剪图入口已取消，请重新选择工作流步骤'
        queued+=await enqueue(db,entry,[Asset.id.in_(crops)],deduplicate=True)
        return queued,None
    new_assets=[asset for asset in new_assets if asset.source_type!='model_crop']
    if not new_assets:return 0,None
    ids = [asset.id for asset in new_assets]
    latest = {annotation.asset_id: annotation for annotation in (await db.scalars(select(Annotation).where(
        Annotation.asset_id.in_(ids), Annotation.is_latest.is_(True)))).all()}
    if isinstance(job, AutomationJob):
        reviewing = job.kind == 'review'
        eligible = [asset for asset in new_assets if (asset.id in latest and 'boxes' in latest[asset.id].annotation_data) == reviewing]
        for asset in eligible:
            annotation = latest.get(asset.id)
            db.add(AutomationItem(job_id=job.id, asset_id=asset.id, content_hash=asset.content_hash,
                annotation_id=annotation.id if annotation else None, annotation_version=annotation.version if annotation else 0,
                review_baseline=review_state(annotation)))
        job.total += len(eligible)
        if eligible and job.status != 'running':
            job.status = 'pending'
    else:
        eligible = new_assets if job.operation in ('crop', 'index', 'quality') else [
            asset for asset in new_assets if asset.id not in latest]
        quality_events = dict((await db.execute(select(AnnotationEvent.asset_id, func.count())
            .where(AnnotationEvent.asset_id.in_(ids)).group_by(AnnotationEvent.asset_id))).all()) if job.operation == 'quality' else {}
        for asset in eligible:
            annotation = latest.get(asset.id)
            state = {'review_state': review_state(annotation)}
            if job.operation == 'quality':
                state['quality_event_count'] = quality_events.get(asset.id, 0)
            db.add(ProjectWorkItem(job_id=job.id, asset_id=asset.id, content_hash=asset.content_hash,
                base_version=annotation.version if annotation else 0, result=state))
    return len(eligible), None
