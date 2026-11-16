"""Project-scoped recommendation scans and confirmation checkpoints."""
import logging
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from models import Project, ProjectWorkItem, ProjectWorkJob
from services.embedding_negative_recommendations import confirm, evidence, mismatch, original_id, recommend, recommendation_rows
from services.embedding_negatives import query_text
from services.project_workflow import dataset_scope, result_job
from services.swift_export import ATTRIBUTE_SOURCES, TARGET_FAMILIES

OPERATION='embedding_negatives'
BATCH_SIZE=256
logger=logging.getLogger(__name__)


def scope_key(request):
    key=f'{request.source}:{request.approved_only}:{request.image_kind}:{request.work_job_id or ""}'
    return key+':automatic' if request.automatic else key


async def latest(db,project,request):
    return await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project.id,
        ProjectWorkJob.operation==OPERATION,ProjectWorkJob.options['scope_key'].as_string()==scope_key(request))
        .order_by(ProjectWorkJob.created_at.desc(),ProjectWorkJob.id.desc()).limit(1))


async def checkpoint(db,job,lock=False):
    query=select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id,ProjectWorkItem.content_hash=='checkpoint')
    if lock:query=query.with_for_update()
    return await db.scalar(query)


async def status(db,job,include_sample=False):
    if job is None:return None
    item=await checkpoint(db,job)
    result=item.result
    state=item.status
    if state=='completed':state='ready' if result['phase']=='scan' else 'confirmed'
    value=dict(id=job.id,state=state,error=item.error,**{key:result[key] for key in (
        'phase','total','scanned','recommended','candidates','confirmed','skipped','reviewed')})
    if include_sample and state=='ready':
        preview=(await db.scalars(select(ProjectWorkItem.result).where(ProjectWorkItem.job_id==job.id,
            ProjectWorkItem.status=='suggested').order_by(ProjectWorkItem.asset_id).limit(12))).all()
        value['sample']=preview
    return value


async def workflow_template_families(db,project,work_job_id):
    options=ProjectWorkJob.options
    query=select(options['attribute_templates']).where(ProjectWorkJob.project_id==project.id,
        ProjectWorkJob.operation=='attributes',options['attribute_templates'].as_string().is_not(None),
        options['workflow_id'].as_string().is_not(None),options['workflow_hidden'].as_boolean().is_not(True))
    templates=None
    if work_job_id:
        source=await result_job(db,project,work_job_id)
        if workflow_id:=source.options.get('workflow_id'):
            templates=await db.scalar(query.where(options['workflow_id'].as_string()==workflow_id)
                .order_by(options['workflow_step'].as_integer().desc()).limit(1))
    if not templates:
        templates=await db.scalar(query.order_by(ProjectWorkJob.created_at.desc(),ProjectWorkJob.id.desc()).limit(1))
    families={}
    for template in templates or ():
        labels=template['config']['target_label']
        aliases={label.casefold().removeprefix('sam3_') for label in (labels if isinstance(labels,list) else [labels])}
        known={TARGET_FAMILIES[alias] for alias in aliases if alias in TARGET_FAMILIES}
        if len(known)<=1:
            family=next(iter(known),template['name'])
            for alias in aliases:
                families[alias]=family if families.get(alias,family)==family else None
    return families


async def start(db,project,request,user_id):
    if project.task_type!='caption':raise HTTPException(400,'自动推荐适用于描述与属性项目')
    job=await latest(db,project,request)
    if job:
        item=await checkpoint(db,job,lock=True)
        if item.status in ('pending','running'):return await status(db,job)
        await db.delete(job)
    await dataset_scope(db,project,request.work_job_id,request.image_kind)
    options={**request.model_dump(mode='json'),'scope_key':scope_key(request)}
    if request.automatic and request.source in ATTRIBUTE_SOURCES:
        options['template_families']=await workflow_template_families(db,project,request.work_job_id)
    job=ProjectWorkJob(project_id=project.id,user_id=user_id,operation=OPERATION,model={},options=options)
    db.add(job);await db.flush()
    db.add(ProjectWorkItem(job_id=job.id,content_hash='checkpoint',status='pending',
        result=dict(phase='scan',cursor=None,total=None,scanned=0,recommended=0,candidates=0,
            confirmed=0,skipped=0,reviewed=0)))
    await db.commit()
    return await status(db,job)


async def queue_confirmation(db,job,request,user_id):
    if job.options.get('automatic'):raise HTTPException(409,'自动生成的负样本可直接用于导出')
    item=await checkpoint(db,job,lock=True)
    result=item.result
    if result['phase']=='confirm':return await status(db,job)
    if item.status!='completed' or not result['candidates']:
        raise HTTPException(409,'请等待推荐完成后再统一确认')
    excluded={}
    for pair in request.excluded:
        excluded.setdefault(str(pair.asset_id),[]).append(str(pair.negative_id))
    item.result={**result,'phase':'confirm','cursor':None,'excluded':excluded,'confirmed_by':user_id}
    item.status='pending'
    await db.commit()
    return await status(db,job)


async def automatic_pairs(db,project,job_id,rows,source,approved_only,export_scope):
    job=await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.id==job_id,
        ProjectWorkJob.project_id==project.id,ProjectWorkJob.operation==OPERATION))
    if not job or not job.options.get('automatic'):
        raise HTTPException(404,'自动负样本批次不存在')
    scope=export_scope or {}
    expected=dict(source=source,approved_only=approved_only,image_kind=scope.get('image_kind','all'),work_job_id=scope.get('work_job_id'))
    if any(job.options.get(key)!=value for key,value in expected.items()):
        raise HTTPException(409,'导出范围或数据来源已变化，请重新生成负样本')
    item=await checkpoint(db,job)
    if item.status!='completed':
        raise HTTPException(409,'请等待负样本生成完成后再导出')
    suggestions=(await db.scalars(select(ProjectWorkItem.result).where(ProjectWorkItem.job_id==job.id,
        ProjectWorkItem.status=='suggested').order_by(ProjectWorkItem.asset_id))).all()
    if not suggestions:return [],{},{}
    eligible={str(asset.id):(asset,ann,evidence(ann,source,allow_pending_attributes=not approved_only,
        template_families=job.options.get('template_families'))) for asset,ann in rows}
    selected=[];assets={};negatives={}
    for suggestion in suggestions:
        anchor=eligible.get(suggestion['asset_id'])
        if not anchor:continue
        asset,ann,signal=anchor
        if not signal or query_text(ann,source)!=suggestion['text']:continue
        valid=[];hashes={asset.content_hash};root=original_id(asset)
        for candidate in suggestion['candidates']:
            other=eligible.get(candidate['asset_id'])
            if not other:continue
            image,_,other_signal=other
            if image.content_hash in hashes or original_id(image)==root or not mismatch(signal,other_signal):continue
            hashes.add(image.content_hash)
            valid.append(str(image.id))
            assets[image.id]=image
        if valid:
            selected.append((asset,ann))
            negatives[str(asset.id)]=valid
    return selected,assets,negatives


async def process(sessions,work):
    async with sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id==work['item_id']).with_for_update())
        if not item or item.status!='running' or item.attempt!=work['attempt']:return True
        try:
            project=await db.get(Project,work['project_id'])
            options=work['options']
            job_scope=UUID(options['work_job_id']) if options['work_job_id'] else None
            scope=await dataset_scope(db,project,job_scope,options['image_kind'])
            source,approved=options['source'],options['approved_only']
            automatic=options.get('automatic',False)
            result=dict(item.result)
            cursor=UUID(result['cursor']) if result['cursor'] else None
            if result['phase']=='scan':
                if result['total'] is None:
                    base=recommendation_rows(project,source,approved,automatic=automatic).where(*scope)
                    result['total']=await db.scalar(select(func.count()).select_from(base.subquery()))
                usage=dict(result.get('negative_usage',{})) if automatic else None
                batch=await recommend(db,project,source,approved,scope,cursor,limit=BATCH_SIZE,
                    automatic=automatic,template_families=options.get('template_families'),usage=usage)
                db.add_all([ProjectWorkItem(job_id=item.job_id,asset_id=row['asset_id'],content_hash='suggestion',
                    status='suggested',result=jsonable_encoder(row)) for row in batch['items']])
                result['scanned']+=batch['scanned']
                result['recommended']+=len(batch['items'])
                result['candidates']+=sum(len(row['candidates']) for row in batch['items'])
                next_cursor=batch['next_cursor']
                if automatic and next_cursor:result['negative_usage']=usage
                else:result.pop('negative_usage',None)
            else:
                rows=(await db.scalars(select(ProjectWorkItem).where(ProjectWorkItem.job_id==item.job_id,
                    ProjectWorkItem.status=='suggested',*([ProjectWorkItem.id>cursor] if cursor else []))
                    .order_by(ProjectWorkItem.id).limit(BATCH_SIZE+1))).all()
                next_cursor=rows[BATCH_SIZE-1].id if len(rows)>BATCH_SIZE else None
                selections=[]
                for row in rows[:BATCH_SIZE]:
                    info=row.result
                    omitted=result['excluded'].get(info['asset_id'],[])
                    ids=[UUID(candidate['asset_id']) for candidate in info['candidates'] if candidate['asset_id'] not in omitted]
                    if ids and row.asset_id:
                        selections.append(SimpleNamespace(asset_id=row.asset_id,text=info['text'],version=info['version'],asset_ids=ids))
                    elif ids:result['skipped']+=len(ids)
                if selections:
                    saved=await confirm(db,project,source,approved,scope,selections,result['confirmed_by'],skip_stale=True)
                    result['confirmed']+=saved['confirmed_count']
                    result['skipped']+=saved['skipped_count']
                result['reviewed']+=min(len(rows),BATCH_SIZE)
            result['cursor']=str(next_cursor) if next_cursor else None
            item.result=result
            item.status='pending' if next_cursor else 'completed'
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception('Embedding recommendation job failed')
            failed=await db.get(ProjectWorkItem,work['item_id'])
            if failed and failed.attempt==work['attempt']:
                failed.status='failed'
                failed.error=str(exc.detail)[:500] if isinstance(exc,HTTPException) else '处理失败，请重新运行推荐'
                await db.commit()
    return True
