from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session as get_db
from api.deps import get_current_user, get_current_superuser
from models import User
from models.validation import ValidationSet, ValidationRun, ValidationItem
from schemas.common import PaginationParams
from schemas.validation import ValidationSetCreate, ValidationRunCreate, ValidationView, CaptionReview
from services import validation

router = APIRouter()


async def page_result(db, query, model, pagination, serialize):
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = (await db.scalars(query.order_by(model.created_at.desc(), model.id)
        .offset(pagination.skip).limit(pagination.limit))).all()
    return dict(items=[serialize(row) for row in rows], total=total,
        page=pagination.page, page_size=pagination.page_size)


@router.post('/sets', status_code=201)
async def create_set(request: ValidationSetCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    return validation.set_dict(await validation.create_set(db, request, user))


@router.get('/sets')
async def list_sets(project_id: UUID | None = None, pagination: PaginationParams = Depends(),
                    task_type: Literal['detection','caption','embedding']='detection',
                    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(ValidationSet).where(ValidationSet.tenant_id == user.tenant_id,or_(ValidationSet.source['kind'].as_string().is_(None),ValidationSet.source['kind'].as_string()!='upload_preview'))
    if project_id: query = query.where(ValidationSet.project_id == project_id)
    query=query.where(validation.multimodal.kind_filter(ValidationSet.source,task_type))
    return await page_result(db, query, ValidationSet, pagination, validation.set_dict)


@router.get('/sets/{set_id}')
async def get_set(set_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return validation.set_dict(await validation.set_scope(db, set_id, user.tenant_id))


@router.post('/sets/{set_id}/refresh',status_code=201)
async def refresh_set(set_id: UUID,db: AsyncSession=Depends(get_db),user: User=Depends(get_current_superuser)):
    return validation.set_dict(await validation.refresh_set(db,set_id,user))


@router.delete('/sets/{set_id}',status_code=204)
async def delete_set(set_id: UUID,db: AsyncSession=Depends(get_db),user: User=Depends(get_current_superuser)):
    await validation.remove_set(db,set_id,user)
    return Response(status_code=204)


@router.post('/runs', status_code=201)
async def create_run(request: ValidationRunCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    return validation.run_dict(await validation.create_run(db, request, user))


@router.get('/runs')
async def list_runs(set_id: UUID | None = None, pagination: PaginationParams = Depends(),
                    task_type: Literal['detection','caption','embedding']='detection',
                    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(ValidationRun).where(ValidationRun.tenant_id == user.tenant_id,ValidationRun.model_snapshot['preview'].as_boolean().is_not(True))
    if set_id: query = query.where(ValidationRun.set_id == set_id)
    query=query.where(validation.multimodal.kind_filter(ValidationRun.model_snapshot,task_type))
    return await page_result(db, query, ValidationRun, pagination, validation.run_dict)


@router.get('/runs/{run_id}')
async def get_run(run_id: UUID, view: ValidationView = Depends(), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    run = await validation.run_scope(db, run_id, user.tenant_id)
    result={**validation.run_dict(run), 'metrics': await validation.calculate_metrics(db, run),
        'labels':(await validation.set_scope(db,run.set_id,user.tenant_id)).labels}
    if view.mode=='target' and validation.multimodal.task_type(run)=='detection':
        threshold=view.target_overlap or run.config.get('target_overlap',.2)
        result['target_metrics'],_=await validation.evaluated_results(db,run,threshold)
        result['evaluation_view']=dict(mode=view.mode,target_overlap=threshold)
    return result


@router.post('/runs/{run_id}/cancel')
async def cancel_run(run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    run = await validation.run_scope(db, run_id, user.tenant_id, lock=True)
    return validation.run_dict(await validation.cancel_run(db, run))


@router.post('/runs/{run_id}/retry')
async def retry_run(run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    run = await validation.run_scope(db, run_id, user.tenant_id, lock=True)
    return validation.run_dict(await validation.retry_run(db, run))


@router.delete('/runs/{run_id}',status_code=204)
async def delete_run(run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    run=await validation.run_scope(db,run_id,user.tenant_id,lock=True)
    await validation.remove_run(db,run)
    return Response(status_code=204)


@router.get('/runs/{run_id}/items')
async def list_items(run_id: UUID,
    error_type: Literal['false_positive', 'false_negative', 'class_confusion', 'failed', 'correct'] | None = None,
    label: str | None = Query(None,min_length=1,max_length=100),
    pagination: PaginationParams = Depends(), view: ValidationView = Depends(), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    run=await validation.run_scope(db, run_id, user.tenant_id)
    if validation.multimodal.task_type(run)!='detection':
        query=select(ValidationItem).where(ValidationItem.run_id==run_id)
        if error_type=='failed':query=query.where(ValidationItem.status=='failed')
        return await page_result(db,query,ValidationItem,pagination,validation.item_dict)
    if view.mode=='target' or label is not None:
        _,rows=await validation.evaluated_results(db,run,(view.target_overlap or run.config.get('target_overlap',.2)) if view.mode=='target' else run.config['match_iou'],merge_duplicates=view.mode=='target')
        if error_type=='failed':rows=[row for row in rows if row['status']=='failed']
        elif error_type:
            rows=[row for row in rows if row['status']=='completed']
            if error_type=='correct':rows=[row for row in rows if row['evaluation']['fp']==row['evaluation']['fn']==0]
            elif error_type in ('false_positive','false_negative'):
                key='fp' if error_type=='false_positive' else 'fn'
                rows=[row for row in rows if row['evaluation'][key]>0]
            else:rows=[row for row in rows if 'class_confusion' in row['evaluation']['errors']]
        if label is not None:
            def matches_label(row):
                if error_type in ('false_negative','false_positive'):
                    truth=error_type=='false_negative'
                    boxes=row['truth' if truth else 'predictions']
                    indices=row['evaluation']['unmatched_truth' if truth else 'unmatched_predictions']
                    return any(boxes[index]['label']==label for index in indices)
                return any(box['label']==label for box in row['truth']+row['predictions'])
            rows=[row for row in rows if matches_label(row)]
        return dict(items=rows[pagination.skip:pagination.skip+pagination.limit],total=len(rows),page=pagination.page,page_size=pagination.page_size)
    query = select(ValidationItem).where(ValidationItem.run_id == run_id)
    if error_type == 'failed': query = query.where(ValidationItem.status == 'failed')
    elif error_type:
        query = query.where(ValidationItem.status == 'completed')
        if error_type == 'correct':
            query = query.where(ValidationItem.evaluation['fp'].as_integer() == 0,
                ValidationItem.evaluation['fn'].as_integer() == 0)
        elif error_type in ('false_positive', 'false_negative'):
            query = query.where(ValidationItem.evaluation['fp' if error_type == 'false_positive' else 'fn'].as_integer() > 0)
        else:
            # JSON arrays compare portably through their serialized scalar representation.
            query = query.where(ValidationItem.evaluation['errors'].as_string().contains('class_confusion'))
    return await page_result(db, query, ValidationItem, pagination, validation.item_dict)


@router.get('/runs/{run_id}/report')
async def report(run_id: UUID, view: ValidationView = Depends(), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    run = await validation.run_scope(db, run_id, user.tenant_id)
    dataset = await validation.set_scope(db, run.set_id, user.tenant_id)
    items = (await db.scalars(select(ValidationItem).where(ValidationItem.run_id == run_id)
        .order_by(ValidationItem.created_at, ValidationItem.id))).all()
    metrics = await validation.calculate_metrics(db, run, items=items)
    document = dict(run={**validation.run_dict(run), 'metrics': metrics}, set=validation.set_dict(dataset),
        items=[validation.item_dict(item) for item in items])
    if view.mode=='target' and validation.multimodal.task_type(run)=='detection':
        threshold=view.target_overlap or run.config.get('target_overlap',.2)
        document['run']['target_metrics'],document['items']=await validation.evaluated_results(db,run,threshold,items)
        document['run']['evaluation_view']=dict(mode=view.mode,target_overlap=threshold)
    return JSONResponse(jsonable_encoder(document), headers={
        'Content-Disposition': f'attachment; filename="validation_{run.id}.json"'})


@router.post('/runs/{run_id}/items/{item_id}/review')
async def review_caption(run_id:UUID,item_id:UUID,request:CaptionReview,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    run=await validation.run_scope(db,run_id,user.tenant_id,lock=True)
    if validation.multimodal.task_type(run)!='caption':raise HTTPException(400,'仅图片描述验证支持语义复核')
    item=await db.scalar(select(ValidationItem).where(ValidationItem.id==item_id,ValidationItem.run_id==run_id).with_for_update())
    if item is None:raise HTTPException(404,'验证图片不存在')
    if item.status!='completed':raise HTTPException(409,'请等待此图描述生成成功后复核')
    from datetime import datetime,timezone
    item.evaluation={**item.evaluation,'review':dict(**request.model_dump(),user_id=user.id,reviewed_at=datetime.now(timezone.utc).isoformat())}
    await db.flush();run.metrics=await validation.calculate_metrics(db,run)
    await db.commit()
    return validation.item_dict(item)


@router.get('/runs/{run_id}/items/{item_id}/image')
async def item_image(run_id: UUID, item_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    run = await validation.run_scope(db, run_id, user.tenant_id)
    item = await db.scalar(select(ValidationItem).where(ValidationItem.id == item_id, ValidationItem.run_id == run_id))
    if item is None: raise HTTPException(404, '验证图片不存在')
    dataset = await validation.set_scope(db, run.set_id, user.tenant_id)
    sample = next((sample for sample in dataset.samples if sample['asset_id'] == str(item.asset_id)), None)
    if sample is None: raise HTTPException(404, '冻结图片不存在')
    content, media_type = await validation.read_sample(sample)
    return Response(content, media_type=media_type, headers={'Cache-Control': 'private, no-store'})
