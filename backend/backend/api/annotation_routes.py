"""Scoped annotation versions, review and business history."""
from uuid import UUID
from datetime import timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select, func, true
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session as get_db
from models import Annotation, Asset, Project, User, AnnotationEvent
from api.deps import get_current_user
from api.asset_routes import scoped_asset
from schemas.annotation import AnnotationCreate, AnnotationUpdate, AnnotationReview, AnnotationResponse
from schemas.common import PaginatedResponse, PaginationParams
from services.annotations import locked_annotation, save_annotation, review, remove
from services.annotation_scope import eligible_assets
from services.project_access import project_condition
from typing import Literal
from copy import deepcopy
import httpx
from PIL import Image
from fastapi import HTTPException
from schemas.annotation import CurrentImageAnalysis

router = APIRouter(dependencies=[Depends(get_current_user)])


async def analysis_context(db,asset_id,user_tenant):
    from api.asset_routes import scoped_project
    from services.annotation_scope import require_annotation_asset
    asset=await scoped_asset(db,asset_id,user_tenant)
    project=await scoped_project(db,asset.project_id,user_tenant)
    require_annotation_asset(project,asset)
    return asset,project


@router.get('/assets/{asset_id}/analysis-models')
async def analysis_models(asset_id:UUID,mode:Literal['attributes','description']='attributes',db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    from models import ModelService
    from services.model_inference import reject_offline_model,supports_prompt
    from schemas.model_service import ModelServiceConfig
    from schemas.prompts import ATTRIBUTES_PROMPT
    _,project=await analysis_context(db,asset_id,user.tenant_id)
    schema=(project.label_schema or {}).get('object_attributes') if mode=='attributes' else None
    protocols=('openai_chat',) if schema else ('openai_chat','model_service')
    rows=await db.scalars(select(ModelService).where(ModelService.tenant_id==user.tenant_id,
        ModelService.enabled.is_(True),ModelService.kind=='caption',ModelService.protocol.in_(protocols)).order_by(ModelService.name,ModelService.id))
    result=[]
    for row in rows:
        reason=None
        try:
            reject_offline_model(row)
        except HTTPException as exc:reason=str(exc.detail)
        editable=supports_prompt(row)
        result.append(dict(id=str(row.id),name=row.name,revision=row.revision,unavailable_reason=reason,
            supports_prompt=editable,prompt=(ATTRIBUTES_PROMPT if schema else ModelServiceConfig.model_validate(row).chat_prompts.caption) if editable else ''))
    return result


@router.post('/assets/{asset_id}/analyze')
async def analyze_current_image(asset_id:UUID,request:CurrentImageAnalysis,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    from config.settings import settings
    from services.image_semantics import model_snapshot,caption
    from services.project_work import read_content
    from services.object_attributes import analyze
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import supports_prompt
    tenant_id=user.tenant_id
    asset,project=await analysis_context(db,asset_id,tenant_id)
    snapshot=await model_snapshot(db,tenant_id,request.model_id,'caption')
    if snapshot['revision']!=request.model_revision:raise HTTPException(409,'模型配置已变更，请重新选择模型')
    schema=deepcopy((project.label_schema or {}).get('object_attributes')) if request.mode=='attributes' else None
    if request.attribute_prompt and not schema:raise HTTPException(400,'属性核心提示词仅适用于已配置项目属性的分析模式')
    if snapshot['config']['protocol'] not in (('openai_chat',) if schema else ('openai_chat','model_service')):
        raise HTTPException(400,'请选择支持当前分析内容的视觉模型；JEV请使用目标属性预标注以保留只读概率')
    config=ModelServiceConfig(**snapshot['config'])
    if request.prompt and not supports_prompt(config):raise HTTPException(400,'所选模型不支持自由提示词，请选择OpenAI视觉对话模型')
    if asset.file_size>settings.MAX_UPLOAD_SIZE:raise HTTPException(413,'图片超过单图分析大小限制')
    content_hash=asset.content_hash
    try:content=await read_content(asset)
    except (OSError,ValueError,Image.DecompressionBombError):raise HTTPException(400,'图片文件无法读取，请检查图片后重试') from None
    await db.rollback()
    async with httpx.AsyncClient(timeout=config.timeout_seconds,follow_redirects=False) as client:
        result=dict(attribute_schema=schema,attributes=await analyze(config,content,schema,client,
            prompt=request.prompt,attribute_prompt=request.attribute_prompt)) if schema else dict(description=await caption(config,content,request.prompt,client))
    asset,project=await analysis_context(db,asset_id,tenant_id)
    if asset.content_hash!=content_hash or request.mode=='attributes' and (project.label_schema or {}).get('object_attributes')!=schema:
        raise HTTPException(409,'分析期间图片或项目属性已变更，请重新分析')
    if await model_snapshot(db,tenant_id,request.model_id,'caption')!=snapshot:
        raise HTTPException(409,'分析期间模型配置已变更，请重新分析')
    return dict(asset_id=str(asset_id),model_id=snapshot['id'],model_revision=snapshot['revision'],**result)

async def scoped_annotation(db, annotation_id, tenant_id):
    from fastapi import HTTPException
    result = await db.scalar(select(Annotation).join(Project,Annotation.project_id==Project.id).where(
        Annotation.id == annotation_id, Project.tenant_id == tenant_id,project_condition(db)))
    if result is None:
        raise HTTPException(404, '标注不存在')
    return result

@router.get('', response_model=PaginatedResponse[AnnotationResponse])
async def list_annotations(asset_id: UUID | None = None, project_id: UUID | None = None,
    review_status: str | None = Query(None, pattern='^(pending|approved|rejected)$'),
    latest_only: bool = True, pagination: PaginationParams = Depends(),
    scope:Literal['all','annotation']='all',
    kind:Literal['detection','image','all']='all',
    ai_pending:bool=False,
    work_job_id:UUID|None=None,image_kind:Literal['all','originals','crops']='all',
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    conditions = [Project.tenant_id == current_user.tenant_id,project_condition(db)]
    project=None
    if project_id:
        from api.asset_routes import scoped_project
        project=await scoped_project(db,project_id,current_user.tenant_id)
    if asset_id:await scoped_asset(db,asset_id,current_user.tenant_id)
    if asset_id: conditions.append(Annotation.asset_id == asset_id)
    if project_id: conditions.append(Annotation.project_id == project_id)
    if project is not None and scope=='annotation':
        kind='detection' if project.task_type=='detection' else 'image'
        conditions.append(Annotation.has_project_format(project.task_type))
    elif kind!='all':conditions.append(Annotation.has_kind(kind))
    query=select(Annotation).join(Project,Annotation.project_id==Project.id)
    if work_job_id or image_kind!='all':
        if not project:raise HTTPException(422,'按批次或图片类型筛选时需要选择项目')
        from services.project_workflow import dataset_scope
        conditions.extend(await dataset_scope(db,project,work_job_id,image_kind))
    if project and (work_job_id or image_kind!='all' or scope=='annotation' and (project.label_schema or {}).get('annotation_scope')=='crops'):
        query=query.join(Asset,Annotation.asset_id==Asset.id)
        if scope=='annotation':conditions.append(eligible_assets(project))
    if review_status:
        state=Annotation.review_status if kind=='all' else Annotation.status_for_query(kind)
        conditions.append(state==review_status)
    if ai_pending and kind!='image':conditions.append(Annotation.ai_pending())
    if latest_only: conditions.append((Annotation.is_latest == true()))
    query=query.where(*conditions)
    total = await db.scalar(query.with_only_columns(func.count(Annotation.id)))
    result = await db.execute(query
        .order_by(Annotation.created_at.desc(), Annotation.version.desc(), Annotation.id).offset(pagination.skip).limit(pagination.limit))
    return PaginatedResponse(items=result.scalars().all(), total=total, page=pagination.page, page_size=pagination.page_size)

@router.get('/assets/{asset_id}/events')
async def annotation_events(asset_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await scoped_asset(db, asset_id, user.tenant_id)
    events = (await db.execute(select(AnnotationEvent).where(AnnotationEvent.asset_id == asset_id)
        .order_by(AnnotationEvent.created_at.desc(), AnnotationEvent.id))).scalars().all()
    return [dict(id=str(e.id), user_id=e.user_id, action=e.action, detail=e.detail, created_at=e.created_at.replace(tzinfo=timezone.utc)) for e in events]

@router.post('', response_model=AnnotationResponse, status_code=201)
async def create_annotation(annotation_data: AnnotationCreate, db: AsyncSession = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    asset = await scoped_asset(db, annotation_data.asset_id, current_user.tenant_id, for_update=True)
    data=annotation_data.annotation_data
    kind=annotation_data.kind or ('detection' if 'boxes' in data else 'image')
    annotation = await save_annotation(db, asset, data, annotation_data.base_version, current_user.id,kind=kind)
    await db.commit()
    await db.refresh(annotation)
    return annotation

@router.get('/{annotation_id}', response_model=AnnotationResponse)
async def get_annotation(annotation_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return await scoped_annotation(db, annotation_id, current_user.tenant_id)

@router.put('/{annotation_id}', response_model=AnnotationResponse)
async def update_annotation(annotation_id: UUID, annotation_data: AnnotationUpdate,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    _, asset = await locked_annotation(db, annotation_id, current_user.tenant_id)
    data=annotation_data.annotation_data
    kind=annotation_data.kind or ('detection' if 'boxes' in data else 'image')
    annotation = await save_annotation(db, asset, data, annotation_data.base_version, current_user.id,kind=kind)
    await db.commit()
    await db.refresh(annotation)
    return annotation

@router.delete('/{annotation_id}', status_code=204)
async def delete_annotation(annotation_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    annotation, asset = await locked_annotation(db, annotation_id, current_user.tenant_id)
    await remove(db, annotation, asset, current_user.id)
    await db.commit()

@router.post('/{annotation_id}/review', response_model=AnnotationResponse)
async def review_annotation(annotation_id: UUID, review_data: AnnotationReview,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    annotation, asset = await locked_annotation(db, annotation_id, current_user.tenant_id)
    await review(db, annotation, asset, review_data.approved, review_data.review_comment, current_user.id,kind=review_data.kind)
    await db.commit()
    await db.refresh(annotation)
    return annotation
