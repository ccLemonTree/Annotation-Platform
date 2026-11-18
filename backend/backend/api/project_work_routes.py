from uuid import UUID
from datetime import timezone
from typing import Literal
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select,func,cast,String,or_
from sqlalchemy.orm import aliased
from database import get_db_session
from api.deps import get_current_user
from api.asset_routes import scoped_project
from models.project_work import ProjectWorkJob, ProjectWorkItem, MatchQuery
from schemas.project_work import (
    WorkCreate, WorkFollowUploads, WorkflowCreate, WorkflowStep,
    WorkflowDryRunRequest, WorkflowAiGenerateRequest, WorkflowAiScriptRequest,
    MatchSearch, MatchJudgment, MatchReview, MatchReviewBatch,
)
from schemas.object_attributes import AttributeFilters
from schemas.vector_smart_search import SmartSearch
from services import project_work as service
from services import project_workflow
from services import vector_smart_search as smart_search

router=APIRouter()


async def context(project_id:UUID,db=Depends(get_db_session),user=Depends(get_current_user)):
    return db,user,await scoped_project(db,project_id,user.tenant_id)


@router.get('/{project_id}/preannotation-examples')
async def preannotation_examples(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),q:str=Query('',max_length=255),ctx=Depends(context)):
    from models import Asset,Annotation
    from services.preannotation_examples import example_snapshot
    from fastapi import HTTPException
    db,_,project=ctx
    query=select(Asset,Annotation).join(Annotation,Annotation.asset_id==Asset.id).where(Asset.project_id==project.id,
        Annotation.is_latest.is_(True),Annotation.annotation_source=='manual',Annotation.annotator_id.is_not(None),
        Annotation.review_status!='rejected',Annotation.annotation_data['skipped'].as_boolean().is_not(True))
    if project.task_type!='detection':query=query.where(Annotation.annotation_data['attribute_schema']['target_label'].as_string().is_not(None))
    if q.strip():query=query.where(Asset.original_name.icontains(q.strip(),autoescape=True))
    total=await db.scalar(select(func.count()).select_from(query.subquery()))
    rows=(await db.execute(query.order_by(Asset.created_at.desc(),Asset.id).offset((page-1)*page_size).limit(page_size))).all()
    items=[]
    for asset,ann in rows:
        data=ann.annotation_data
        try:example_snapshot(project,asset,ann);compatible=True
        except HTTPException:compatible=False
        items.append(dict(asset_id=str(asset.id),original_name=asset.original_name,annotation_version=ann.version,
            attributes=data.get('attributes',{}),attribute_schema=data.get('attribute_schema'),boxes=data.get('boxes'),
            width=asset.width,height=asset.height,task_type=project.task_type,compatible=compatible))
    return dict(items=items,total=total,page=page,page_size=page_size)


@router.get('/{project_id}/attribute-options')
async def attribute_options(ctx=Depends(context)):
    from services.object_attributes import common_attribute_values
    db,_,project=ctx
    return await common_attribute_values(db,project)


@router.get('/{project_id}/crop-labels')
async def crop_labels(ctx=Depends(context)):
    from models import Asset
    db,_,project=ctx
    label=Asset.device_info['crop']['box']['label'].as_string()
    return list((await db.scalars(select(label).where(Asset.project_id==project.id,
        Asset.source_type=='model_crop',label.is_not(None),label!='').distinct().order_by(label))).all())


@router.get('/{project_id}/attributes')
async def list_attributes(filters:AttributeFilters=Depends(),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),ctx=Depends(context)):
    from services.object_attributes import attribute_query
    db,_,project=ctx
    query=attribute_query(project,**filters.model_dump())
    total=await db.scalar(select(func.count()).select_from(query.subquery()))
    from models import Asset
    rows=(await db.execute(query.order_by(Asset.created_at.desc(),Asset.id).offset((page-1)*page_size).limit(page_size))).all()
    return dict(total=total,page=page,page_size=page_size,items=[dict(asset_id=str(asset.id),original_name=asset.original_name,
        annotation_id=str(ann.id) if ann else None,version=ann.version if ann else 0,review_status=ann.status_for('image') if ann else None,
        annotation_source=ann.annotation_source if ann else None,
        attributes=ann.annotation_data.get('attributes',{}) if ann else {},attribute_schema=ann.annotation_data.get('attribute_schema') if ann else None,
        source=asset.device_info or {}) for asset,ann in rows])


@router.get('/{project_id}/attributes/export')
async def export_attributes(filters:AttributeFilters=Depends(),approved_only:bool=True,work_job_id:UUID|None=None,
                            image_kind:Literal['all','originals','crops']='all',ctx=Depends(context)):
    from services.annotation_export import training_package
    from api.export_routes import package_response
    from models import AnnotationEvent
    from services.project_workflow import dataset_scope
    db,user,project=ctx
    scope=await dataset_scope(db,project,work_job_id,image_kind)
    stream,manifest=await training_package(db,project,approved_only=approved_only,attribute_filters=filters.model_dump(exclude_none=True),scope=scope)
    db.add(AnnotationEvent(project_id=project.id,user_id=user.id,action='export',detail=manifest));await db.commit()
    return package_response(stream,f'project_{project.id}_attributes.zip')


@router.post('/{project_id}/work/jobs',status_code=201)
async def create_job(request:WorkCreate,ctx=Depends(context)):
    db,user,project=ctx
    return await service.summary(db,await service.create_job(db,project,request,user.id))


@router.post('/{project_id}/work/workflows',status_code=201)
async def create_workflow(request:WorkflowCreate,ctx=Depends(context)):
    db,user,project=ctx
    steps=await project_workflow.create(db,project,request,user.id)
    return dict(id=str(steps[0].id))


@router.post('/{project_id}/work/workflows/dry-run')
async def dry_run_workflow(request:WorkflowDryRunRequest,ctx=Depends(context)):
    from services.workflow_dry_run import run_dry_run
    db,user,project=ctx
    if user.role!='admin':
        raise HTTPException(403,'仅管理员可执行工作流样本试运行')
    return await run_dry_run(db,project,request)


@router.post('/{project_id}/work/workflows/ai-generate')
async def ai_generate_workflow(request:WorkflowAiGenerateRequest,ctx=Depends(context)):
    from services.workflow_ai import generate_workflow
    db,user,project=ctx
    if user.role!='admin':
        raise HTTPException(403,'仅管理员可使用 AI 自动编排工作流')
    return await generate_workflow(db,project,request)


@router.post('/{project_id}/work/workflows/ai-script')
async def ai_generate_workflow_script(request:WorkflowAiScriptRequest,ctx=Depends(context)):
    from services.workflow_ai import generate_script
    db,user,project=ctx
    if user.role!='admin':
        raise HTTPException(403,'仅管理员可使用 AI 生成工作流脚本')
    return await generate_script(db,project,request)


@router.get('/{project_id}/work/workflows')
async def list_workflows(page:int=Query(1,ge=1),ctx=Depends(context)):
    db,_,project=ctx
    workflow=ProjectWorkJob.options['workflow_id'].as_string()
    step=ProjectWorkJob.options['workflow_step'].as_integer()
    roots=(await db.scalars(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project.id,step==0,
        ProjectWorkJob.options['workflow_hidden'].as_boolean().is_not(True))
        .order_by(ProjectWorkJob.created_at.desc(),ProjectWorkJob.id).offset((page-1)*10).limit(10))).all()
    result=[]
    for root in roots:
        jobs=(await db.scalars(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project.id,workflow==str(root.id))
            .order_by(step))).all()
        result.append(dict(id=str(root.id),name=root.options['workflow_name'],steps=[await service.summary(db,job) for job in jobs]))
    return result


@router.get('/{project_id}/work/workflows/{workflow_id}')
async def workflow_config(workflow_id:UUID,ctx=Depends(context)):
    db,_,project=ctx
    jobs=await project_workflow.history_steps(db,project,workflow_id)
    root=jobs[0]
    result=dict(name=root.options['workflow_name'],created_at=root.created_at.replace(tzinfo=timezone.utc)
        if root.created_at.tzinfo is None else root.created_at,image_kind=root.options.get('workflow_image_kind'),
        follow_uploads=root.options.get('workflow_can_follow'),uploaded_crop_job_id=root.options.get('uploaded_crop_job_id'),
        edges=root.options.get('workflow_edges'),steps=[])
    if 'workflow_source_job_id' in root.options:
        result['source_job_id']=root.options['workflow_source_job_id']
    fields=(WorkflowStep.model_fields.keys()-{'model_id','operation'})|{'attribute_schema'}
    for job in jobs:
        model=job.model.get('config',{})
        config={key:value for key,value in job.options.items() if key in fields}
        if job.operation in ('crop','detect','preannotate') and not config.get('labels'):
            config['labels']=model.get('labels',[])
        default_prompt=(model.get('chat_prompts') or {}).get('review' if job.operation=='ai_review' else job.operation)
        result['steps'].append(dict(id=str(job.id),operation=job.operation,model_name=model.get('name'),
            model_revision=job.model.get('revision'),default_prompt=default_prompt,**config))
    return result


@router.delete('/{project_id}/work/workflows/{workflow_id}',status_code=204)
async def delete_workflow(workflow_id:UUID,ctx=Depends(context)):
    db,_,project=ctx
    await project_workflow.delete_history(db,project,workflow_id)


@router.post('/{project_id}/work/workflows/{workflow_id}/cancel')
async def cancel_workflow(workflow_id:UUID,ctx=Depends(context)):
    db,_,project=ctx
    return await project_workflow.cancel(db,project,workflow_id)


@router.get('/{project_id}/work/jobs')
async def list_jobs(page:int=Query(1,ge=1),roots_only:bool=False,crops_only:bool=False,ctx=Depends(context)):
    db,_,project=ctx
    batch_root=ProjectWorkJob.options['batch_root'].as_string()
    query=select(ProjectWorkJob).where(ProjectWorkJob.project_id==project.id,
        ProjectWorkJob.operation!='embedding_negatives',
        or_(batch_root.is_(None),func.replace(batch_root,'-','')==func.replace(cast(ProjectWorkJob.id,String),'-','')),
        ProjectWorkJob.options['queue_deleted'].as_boolean().is_not(True))
    if crops_only:
        query=query.where(ProjectWorkJob.operation.in_(('crop','detect_attributes')))
    else:
        query=query.where(ProjectWorkJob.options['workflow_id'].as_string().is_(None))
    if roots_only:
        parent_item,parent_job=aliased(ProjectWorkItem),aliased(ProjectWorkJob)
        parent=select(parent_item.id).join(parent_job,parent_job.id==parent_item.job_id).where(parent_job.project_id==project.id,
            func.replace(parent_item.result['attribute_job_id'].as_string(),'-','')==func.replace(cast(ProjectWorkJob.id,String),'-',''))
        query=query.where(~parent.exists())
    jobs=(await db.scalars(query
        .order_by(ProjectWorkJob.created_at.desc(),ProjectWorkJob.id).offset((page-1)*20).limit(20))).all()
    return [await service.summary(db,job) for job in jobs]


@router.get('/{project_id}/work/jobs/{job_id}')
async def get_job(job_id:UUID,page:int=Query(1,ge=1),ctx=Depends(context)):
    db,_,project=ctx
    return await service.summary(db,await service.scoped_job(db,project,job_id,lock=False),True,page)


@router.delete('/{project_id}/work/jobs/{job_id}',status_code=204)
async def delete_job(job_id:UUID,ctx=Depends(context)):
    db,_,project=ctx
    await service.delete_job(db,project,job_id)


@router.post('/{project_id}/work/jobs/{job_id}/{action}')
async def action_job(job_id:UUID,action:str,ctx=Depends(context)):
    from fastapi import HTTPException
    if action not in ('cancel','retry'):raise HTTPException(404,'操作不存在')
    db,_,project=ctx
    return await service.action_job(db,project,job_id,action)


@router.put('/{project_id}/work/jobs/{job_id}/follow-uploads')
async def follow_uploads(job_id:UUID,request:WorkFollowUploads,ctx=Depends(context)):
    from services.upload_follow import set_follow_uploads,set_uploaded_crop_entry
    db,_,project=ctx
    job=await service.scoped_job(db,project,job_id)
    if job.options.get('workflow_hidden'):raise HTTPException(409,'工作流已从历史任务删除')
    if 'uploaded_crop_job_id' in request.model_fields_set:
        await set_uploaded_crop_entry(db,job,request.uploaded_crop_job_id)
    result=await set_follow_uploads(db,job,request.enabled)
    await db.commit()
    return result


@router.post('/{project_id}/matches/search',status_code=201)
async def search(request:MatchSearch,ctx=Depends(context)):
    db,_,project=ctx
    async with httpx.AsyncClient(follow_redirects=False) as client:
        return await service.search(db,project,request,client)


@router.get('/{project_id}/matches/smart-search/platforms')
async def smart_search_platforms(ctx=Depends(context)):
    return await smart_search.platforms()


@router.post('/{project_id}/matches/smart-search',status_code=201)
async def search_external(request:SmartSearch,ctx=Depends(context)):
    db,_,project=ctx
    async with httpx.AsyncClient(follow_redirects=False,trust_env=False) as client:
        return await smart_search.search(db,project,request,client)


@router.get('/{project_id}/matches/{query_id}/source/{asset_id}')
async def smart_search_original(query_id:UUID,asset_id:UUID,ctx=Depends(context)):
    from io import BytesIO
    from PIL import Image
    from fastapi import Response
    db,_,project=ctx
    content,_=await smart_search.original(db,project,query_id,asset_id)
    with Image.open(BytesIO(content)) as image:media_type=Image.MIME.get(image.format,'application/octet-stream')
    return Response(content,media_type=media_type,headers={'Cache-Control':'private, max-age=3600'})


@router.get('/{project_id}/matches')
async def queries(page:int=Query(1,ge=1),ctx=Depends(context)):
    db,_,project=ctx
    rows=(await db.scalars(select(MatchQuery).where(MatchQuery.project_id==project.id,MatchQuery.is_retrieval_query())
        .order_by(MatchQuery.created_at.desc(),MatchQuery.id).offset((page-1)*20).limit(20))).all()
    return [await service.query_with_content(db,q) for q in rows]


@router.put('/{project_id}/matches/{query_id}')
async def judgment(query_id:UUID,request:MatchJudgment,ctx=Depends(context)):
    db,user,project=ctx
    return await service.judge(db,project,query_id,request.asset_id,request.judgment,request.base_version,user.id)


@router.post('/{project_id}/matches/{query_id}/review')
async def review_match(query_id:UUID,request:MatchReview,ctx=Depends(context)):
    db,user,project=ctx
    return await service.judge(db,project,query_id,request.asset_id,None,request.base_version,user.id,request.approved)


@router.post('/{project_id}/matches/review-jobs',status_code=201)
async def review_matches(request:MatchReviewBatch,ctx=Depends(context)):
    from services.match_review import create_review_jobs
    db,user,project=ctx
    return [await service.summary(db,job) for job in await create_review_jobs(db,project,request,user.id)]
