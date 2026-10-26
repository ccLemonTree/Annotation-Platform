"""Single-instance durable preannotation and version-bound review worker."""
import asyncio
import hashlib
import logging
from datetime import datetime
from uuid import UUID
from sqlalchemy import select, func, exists, update
from fastapi import HTTPException
from models import Annotation, Asset, Project, User
from models.automation import AutomationJob, AutomationItem
from models.model_service import ModelService
from schemas.automation import AutomationConfig
from schemas.model_service import ModelServiceConfig
from services.annotations import save_annotation, review, record_event, detection_boxes
from services.model_inference import infer
from services.automation_review import audit_image, auto_pass
from services.storage import get_storage
from config.settings import settings

logger=logging.getLogger(__name__)
_worker=None
TERMINAL=('approved','needs_review','failed','skipped','canceled')


def review_state(annotation):
    if annotation is None:return {}
    return dict(status=annotation.review_status,reviewer_id=annotation.reviewer_id,
        reviewed_at=annotation.reviewed_at.isoformat() if annotation.reviewed_at else None)


async def model_snapshot(db, model_id, tenant_id, kind):
    model=await db.scalar(select(ModelService).where(ModelService.id==model_id,ModelService.tenant_id==tenant_id))
    if model is None or not model.enabled or model.kind!=kind or (kind=='caption' and model.protocol!='openai_chat'):
        raise HTTPException(400,'请选择已启用且用途正确的检测模型或OpenAI视觉审核模型')
    return dict(id=str(model.id),revision=model.revision,config=ModelServiceConfig.model_validate(model).model_dump())


async def validate_context(db, job, asset, item, lock=False):
    if asset is None or asset.project_id!=job.project_id or asset.content_hash!=item.content_hash:
        raise HTTPException(409,'图片已删除、移出项目或内容已变更，旧任务不会写入')
    query=select(Project).where(Project.id==job.project_id,Project.tenant_id==job.tenant_id)
    if lock:query=query.with_for_update()
    project=await db.scalar(query)
    if project is None or project.label_schema!=job.label_schema:
        raise HTTPException(409,'项目类别已更改，请按新类别重新创建任务')
    actor=await db.get(User,job.user_id)
    if actor is None or not actor.is_active or actor.role!='admin' or actor.tenant_id!=job.tenant_id:
        raise HTTPException(403,'任务创建人已失去管理员权限，停止写入')
    for snapshot in job.models.values():
        query=select(ModelService).where(ModelService.id==UUID(snapshot['id']),ModelService.tenant_id==job.tenant_id)
        if lock:query=query.with_for_update()
        model=await db.scalar(query)
        if model is None or not model.enabled or model.revision!=snapshot['revision']:
            raise HTTPException(409,'模型已停用、删除或修改，请按新配置重新创建任务')
    latest=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
    if (latest.version if latest else 0)!=item.annotation_version or (latest and latest.id!=item.annotation_id):
        raise HTTPException(409,'标注版本已变化，旧模型结果不会覆盖人工修改')
    if review_state(latest)!=item.review_baseline:
        raise HTTPException(409,'人工审核结论已变化，旧模型结果不会覆盖人工审核')
    return latest


async def recount(db, job):
    await db.flush()
    counts=dict((await db.execute(select(AutomationItem.status,func.count()).where(AutomationItem.job_id==job.id).group_by(AutomationItem.status))).all())
    job.processed=sum(counts.get(status,0) for status in TERMINAL)
    job.approved_count=counts.get('approved',0);job.needs_review_count=counts.get('needs_review',0);job.failed_count=counts.get('failed',0)
    if job.status!='canceled':
        job.status='running' if counts.get('running',0) else ('pending' if counts.get('pending',0) else ('failed' if job.failed_count else 'completed'))
    job.updated_at=datetime.utcnow()


async def process_one(sessions):
    async with sessions() as db:
        job=await db.scalar(select(AutomationJob).where(AutomationJob.status.in_(('pending','running')),
            exists().where(AutomationItem.job_id==AutomationJob.id,AutomationItem.status=='pending'))
            .order_by(AutomationJob.created_at,AutomationJob.id).with_for_update(skip_locked=True).limit(1))
        if job is None:return False
        item=await db.scalar(select(AutomationItem).where(AutomationItem.job_id==job.id,AutomationItem.status=='pending')
            .order_by(AutomationItem.created_at,AutomationItem.id).with_for_update().limit(1))
        item.status='running';item.attempt+=1;job.status='running'
        job_id,item_id,attempt=job.id,item.id,item.attempt
        asset=await db.get(Asset,item.asset_id)
        try:
            annotation=await validate_context(db,job,asset,item)
        except HTTPException as error:
            item.status='skipped';item.reason=str(error.detail);await recount(db,job);await db.commit();return True
        path,width,height=asset.blob_path,asset.width,asset.height
        original_data=annotation.annotation_data if annotation else None
        await db.commit()
    result={};evidence={};failure=None;stage='detection'
    policy=AutomationConfig.model_validate(job.config)
    async def still_current():
        async with sessions() as check:
            return bool(await check.scalar(select(AutomationItem.id).join(AutomationJob).where(
                AutomationItem.id==item_id,AutomationItem.status=='running',AutomationItem.attempt==attempt,
                AutomationJob.status.in_(('running','pending')))))
    try:
        with await get_storage().get_file(path) as stream:content=stream.read(settings.MAX_UPLOAD_SIZE+1)
        if len(content)>settings.MAX_UPLOAD_SIZE or hashlib.sha256(content).hexdigest()!=item.content_hash:
            raise HTTPException(409,'原图内容或大小发生变化，请重新创建任务')
        labels=job.label_schema['labels']
        if job.kind=='preannotate':
            detector=ModelServiceConfig.model_validate(job.models['detector']['config']).model_copy(update={
                'confidence':policy.confidence,'iou':policy.iou})
            if policy.labels:detector.labels=policy.labels
            detector.label_mapping={**detector.label_mapping,**policy.label_mapping}
            result=await infer(detector,content,width,height)
            boxes=result['boxes']
        else:boxes=original_data.get('boxes',[]);result={'boxes':boxes,'warnings':[]}
        if not await still_current():return True
        unknown=any(box.get('label') not in labels for box in boxes)
        high=all(box.get('score',-1)>policy.auto_approve_confidence for box in boxes)
        if unknown:
            evidence={'boxes':[],'completeness':'uncertain','negative':'uncertain','reason':'模型类别未映射到项目类别，请人工修正映射或标注'}
        elif policy.auto_review and (job.kind=='review' or high or not boxes):
            reviewer=ModelServiceConfig.model_validate(job.models['reviewer']['config'])
            stage='audit'
            evidence=await audit_image(reviewer,content,boxes,labels,policy.crop_expansion,policy.prompt,should_continue=still_current)
        else:evidence={'boxes':[],'completeness':'uncertain','negative':'uncertain',
            'reason':'检测框置信度未全部严格超过自动通过阈值' if policy.auto_review else '此任务未启用大模型审核'}
        approved=not unknown and auto_pass(boxes,evidence,policy.auto_approve_confidence,job.kind=='preannotate')
        reason=evidence.get('reason','等待管理员审核')
        if not boxes:reason='候选负样本必须由管理员最终审核；'+reason
        if result.get('warnings'):
            approved=False;reason='检测结果含警告，请管理员核对；'+reason
    except asyncio.CancelledError:raise
    except Exception as error:
        failure=str(error.detail) if isinstance(error,HTTPException) else '图片读取或模型处理失败，请重试'
        approved=False;reason=failure
        if stage=='audit':
            evidence=dict(boxes=[],completeness='uncertain',negative='uncertain',reason='AI未能完成判断，转管理员；'+failure)
            failure=None
        logger.warning('Automation inference failed: %s',type(error).__name__)
    async with sessions() as db:
        current_job=await db.scalar(select(AutomationJob).where(AutomationJob.id==job_id).with_for_update())
        current=await db.scalar(select(AutomationItem).where(AutomationItem.id==item_id).with_for_update())
        if current_job is None or current is None or current_job.status=='canceled' or current.status!='running' or current.attempt!=attempt:return True
        asset=await db.scalar(select(Asset).where(Asset.id==current.asset_id).with_for_update())
        try:
            annotation=await validate_context(db,current_job,asset,current,lock=True)
        except HTTPException as error:
            current.status='skipped';current.reason=str(error.detail)
        else:
            evidence={**evidence,'provenance':dict(content_hash=current.content_hash,
                models={kind:dict(id=value['id'],revision=value['revision'],name=value['config']['name'],model_name=value['config']['model_name']) for kind,value in current_job.models.items()},
                project_labels=current_job.label_schema['labels'],crop_expansion=policy.crop_expansion,
                auto_approve_confidence=policy.auto_approve_confidence,reviewed_at=datetime.utcnow().isoformat()+'Z')}
            current.result=result;current.evidence=evidence;current.reason=reason[:1000]
            if failure:current.status='failed'
            else:
                boxes=result['boxes'];unknown=any(box.get('label') not in current_job.label_schema['labels'] for box in boxes)
                if current_job.kind=='preannotate' and not unknown:
                    annotation=await save_annotation(db,asset,{'boxes':boxes,'negative':not boxes},0,current_job.user_id,source='model')
                    current.annotation_id=annotation.id;current.annotation_version=annotation.version
                    current.review_baseline=review_state(annotation)
                if approved and annotation:
                    await review(db,annotation,asset,True,'AI审核通过：'+reason,current_job.user_id,source='ai')
                    current.status='approved'
                else:current.status='needs_review'
                record_event(db,asset,current_job.user_id,'ai_review',dict(job_id=str(job_id),item_id=str(item_id),
                    annotation_id=str(current.annotation_id) if current.annotation_id else None,version=current.annotation_version,
                    source='ai',decision=current.status,evidence=evidence))
        await recount(db,current_job);await db.commit()
    return True


async def run_worker(sessions):
    async def consume():
        while True:
            try:
                if not await process_one(sessions):await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception as error:
                logger.error('Automation worker failed: %s',type(error).__name__);await asyncio.sleep(1)
    async with asyncio.TaskGroup() as group:
        for _ in range(settings.AUTOMATION_CONCURRENCY):group.create_task(consume())


async def start_worker():
    global _worker
    from database import AsyncSessionLocal
    # shortcut: 单后端恢复；多实例执行前需引入租约，不能由另一实例回收活跃任务。
    async with AsyncSessionLocal() as db:
        await db.execute(update(AutomationItem).where(AutomationItem.status=='running').values(status='failed',reason='服务重启导致处理中断，请手动重试'))
        jobs=(await db.scalars(select(AutomationJob).where(AutomationJob.status.in_(('pending','running'))))).all()
        for job in jobs:await recount(db,job)
        await db.commit()
    _worker=asyncio.create_task(run_worker(AsyncSessionLocal))


async def stop_worker():
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
