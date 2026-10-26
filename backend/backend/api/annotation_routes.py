"""Scoped annotation versions, review and business history."""
from uuid import UUID
from datetime import timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session as get_db
from models import Annotation, Asset, Project, User, AnnotationEvent
from api.deps import get_current_user
from api.asset_routes import scoped_asset
from schemas.annotation import AnnotationCreate, AnnotationUpdate, AnnotationReview, AnnotationResponse
from schemas.common import PaginatedResponse, PaginationParams
from services.annotations import locked_annotation, save_annotation, review, remove
from services.annotation_scope import eligible_assets
from typing import Literal

router = APIRouter(dependencies=[Depends(get_current_user)])

async def scoped_annotation(db, annotation_id, tenant_id):
    from fastapi import HTTPException
    result = await db.scalar(select(Annotation).join(Asset).join(Project).where(
        Annotation.id == annotation_id, Project.tenant_id == tenant_id))
    if result is None:
        raise HTTPException(404, '标注不存在')
    return result

@router.get('', response_model=PaginatedResponse[AnnotationResponse])
async def list_annotations(asset_id: UUID | None = None, project_id: UUID | None = None,
    review_status: str | None = Query(None, pattern='^(pending|approved|rejected)$'),
    latest_only: bool = True, pagination: PaginationParams = Depends(),
    scope:Literal['all','annotation']='all',
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    conditions = [Project.tenant_id == current_user.tenant_id]
    if asset_id: conditions.append(Annotation.asset_id == asset_id)
    if project_id: conditions.append(Project.id == project_id)
    if scope=='annotation' and project_id:
        from api.asset_routes import scoped_project
        conditions.append(eligible_assets(await scoped_project(db,project_id,current_user.tenant_id)))
    if review_status: conditions.append(Annotation.review_status == review_status)
    if latest_only: conditions.append(Annotation.is_latest.is_(True))
    total = await db.scalar(select(func.count(Annotation.id)).join(Asset).join(Project).where(*conditions))
    result = await db.execute(select(Annotation).join(Asset).join(Project).where(*conditions)
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
    annotation = await save_annotation(db, asset, annotation_data.annotation_data, annotation_data.base_version, current_user.id)
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
    annotation = await save_annotation(db, asset, annotation_data.annotation_data, annotation_data.base_version, current_user.id)
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
    await review(db, annotation, asset, review_data.approved, review_data.review_comment, current_user.id)
    await db.commit()
    await db.refresh(annotation)
    return annotation
