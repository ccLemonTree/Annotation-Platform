from uuid import UUID
from datetime import datetime,timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session as get_db
from api.deps import get_current_user, get_current_superuser
from api.asset_routes import scoped_project
from models import Asset, Annotation, User
from models.automation import AutomationJob, AutomationItem
from schemas.automation import AutomationCreate, ResolveReview
from schemas.common import PaginationParams
from services.automation import model_snapshot, recount, review_state
from services.annotations import review, record_event
from services.annotation_scope import eligible_assets

router=APIRouter(dependencies=[Depends(get_current_user)])


async def job_scope(db,id,tenant_id,lock=False):
    query=select(AutomationJob).where(AutomationJob.id==id,AutomationJob.tenant_id==tenant_id)
    if lock:query=query.with_for_update()
    row=await db.scalar(query)
    if row is None:raise HTTPException(404,'自动处理任务不存在')
    return row


def row_dict(row):
    result={column.name:getattr(row,column.name) for column in row.__table__.columns if column.name not in ('models',)}
    return {key:value.replace(tzinfo=timezone.utc) if isinstance(value,datetime) and value.tzinfo is None else value for key,value in result.items()}


async def page_result(db,query,model,pagination):
    total=await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows=(await db.scalars(query.order_by(model.created_at.desc(),model.id).offset(pagination.skip).limit(pagination.limit))).all()
    return dict(items=[row_dict(row) for row in rows],total=total,page=pagination.page,page_size=pagination.page_size)


@router.post('/jobs',status_code=201)
async def create_job(request:AutomationCreate,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    project=await scoped_project(db,request.project_id,user.tenant_id)
    labels=(project.label_schema or {}).get('labels')
    if project.task_type!='detection' or not labels:raise HTTPException(400,'请选择已配置类别的检测项目')
    if request.kind=='review' and any(label not in labels for label in request.config.labels):
        raise HTTPException(400,'审核类别必须属于项目类别')
    models={}
    if request.kind=='preannotate':models['detector']=await model_snapshot(db,request.detector_id,user.tenant_id,'detection')
    if request.config.auto_review or request.kind=='review':models['reviewer']=await model_snapshot(db,request.reviewer_id,user.tenant_id,'caption')
    if any(value not in labels for value in request.config.label_mapping.values()):raise HTTPException(400,'映射目标必须属于项目类别')
    query=select(Asset,Annotation).outerjoin(Annotation,(Annotation.asset_id==Asset.id)&Annotation.is_latest.is_(True)).where(Asset.project_id==project.id,eligible_assets(project))
    if request.asset_ids:
        query=query.where(Asset.id.in_(request.asset_ids))
        found=await db.scalar(select(func.count()).select_from(Asset).where(Asset.project_id==project.id,Asset.id.in_(request.asset_ids)))
        if found!=len(set(request.asset_ids)):raise HTTPException(404,'部分图片不属于当前项目')
    query=query.where(Annotation.id.is_(None) if request.kind=='preannotate' else Annotation.id.is_not(None))
    values=(await db.execute(query.order_by(Asset.id).limit(10001))).all()
    if not values:raise HTTPException(400,'当前范围没有符合条件的图片；预标注只处理未标注图片')
    if len(values)>10000:raise HTTPException(400,'单任务最多10000张，请分批选择图片')
    job=AutomationJob(tenant_id=user.tenant_id,project_id=project.id,user_id=user.id,kind=request.kind,
        config=request.config.model_dump(),models=models,label_schema=project.label_schema,total=len(values))
    db.add(job);await db.flush()
    for asset,annotation in values:
        db.add(AutomationItem(job_id=job.id,asset_id=asset.id,content_hash=asset.content_hash,
            annotation_id=annotation.id if annotation else None,annotation_version=annotation.version if annotation else 0,
            review_baseline=review_state(annotation)))
    await db.commit();await db.refresh(job)
    return row_dict(job)


@router.get('/jobs')
async def list_jobs(project_id:UUID|None=None,pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    query=select(AutomationJob).where(AutomationJob.tenant_id==user.tenant_id)
    if project_id:
        await scoped_project(db,project_id,user.tenant_id);query=query.where(AutomationJob.project_id==project_id)
    return await page_result(db,query,AutomationJob,pagination)


@router.get('/jobs/{job_id}')
async def get_job(job_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    return row_dict(await job_scope(db,job_id,user.tenant_id))


@router.get('/jobs/{job_id}/items')
async def list_items(job_id:UUID,status:str|None=None,pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    await job_scope(db,job_id,user.tenant_id)
    query=select(AutomationItem).where(AutomationItem.job_id==job_id)
    if status:query=query.where(AutomationItem.status==status)
    return await page_result(db,query,AutomationItem,pagination)


@router.post('/jobs/{job_id}/cancel')
async def cancel_job(job_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    job=await job_scope(db,job_id,user.tenant_id,True)
    if job.status in ('pending','running'):
        job.status='canceled'
        items=(await db.scalars(select(AutomationItem).where(AutomationItem.job_id==job.id,AutomationItem.status.in_(('pending','running'))).with_for_update())).all()
        for item in items:item.status='canceled';item.attempt+=1;item.reason='管理员取消任务'
        await recount(db,job);await db.commit()
    return row_dict(job)


@router.post('/jobs/{job_id}/retry')
async def retry_job(job_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    job=await job_scope(db,job_id,user.tenant_id,True)
    if job.status in ('running','pending'):raise HTTPException(409,'任务仍在运行，请等待结束或先取消')
    for snapshot in job.models.values():
        current=await model_snapshot(db,UUID(snapshot['id']),user.tenant_id,snapshot['config']['kind'])
        if current['revision']!=snapshot['revision']:raise HTTPException(409,'模型配置已变化，请新建任务')
    items=(await db.scalars(select(AutomationItem).where(AutomationItem.job_id==job.id,AutomationItem.status.in_(('failed','canceled'))).with_for_update())).all()
    if not items:raise HTTPException(400,'没有失败或取消的图片可重试')
    for item in items:item.status='pending';item.reason=None;item.attempt+=1
    job.status='pending';job.error=None;await recount(db,job);await db.commit()
    return row_dict(job)


@router.get('/review-queue')
async def review_queue(project_id:UUID|None=None,pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    query=select(AutomationItem).join(AutomationJob).where(AutomationJob.tenant_id==user.tenant_id,AutomationItem.status=='needs_review').order_by(AutomationItem.updated_at.desc())
    if project_id:
        await scoped_project(db,project_id,user.tenant_id);query=query.where(AutomationJob.project_id==project_id)
    return await page_result(db,query,AutomationItem,pagination)


@router.post('/items/{item_id}/resolve')
async def resolve_review(item_id:UUID,request:ResolveReview,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    job_id=await db.scalar(select(AutomationItem.job_id).join(AutomationJob).where(AutomationItem.id==item_id,AutomationJob.tenant_id==user.tenant_id))
    if job_id is None:raise HTTPException(404,'审核条目不存在')
    job=await job_scope(db,job_id,user.tenant_id,True)
    item=await db.scalar(select(AutomationItem).where(AutomationItem.id==item_id).with_for_update())
    if item.status!='needs_review':raise HTTPException(409,'条目已处理，请刷新队列')
    asset=await db.scalar(select(Asset).where(Asset.id==item.asset_id).with_for_update())
    annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==item.asset_id,Annotation.is_latest.is_(True)))
    project=await scoped_project(db,job.project_id,user.tenant_id)
    valid=asset is not None and asset.project_id==job.project_id and asset.content_hash==item.content_hash and project.label_schema==job.label_schema
    valid=valid and annotation is not None and annotation.id==item.annotation_id and annotation.version==item.annotation_version and review_state(annotation)==item.review_baseline
    if not valid and request.approved:
        raise HTTPException(409,'图片、类别或标注版本已变化，或候选框尚未保存；请到工作台重新核对，旧条目可驳回移出队列')
    if valid:await review(db,annotation,asset,request.approved,request.comment,user.id)
    item.status='approved' if request.approved else 'skipped';item.reason=request.comment or ('管理员通过' if request.approved else '管理员不通过')
    item.result={**item.result,'admin_decision':dict(approved=request.approved,user_id=user.id,comment=request.comment)}
    await recount(db,job);await db.commit();await db.refresh(item)
    return row_dict(item)
