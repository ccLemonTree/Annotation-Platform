"""Mount at /api/image-semantics; startup helpers live in image_semantics."""
from uuid import UUID
import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user
from database import get_db_session
from models.user import User
from models.image_semantic import ImageSemanticJob
from schemas.image_semantic import SemanticJobCreate, SemanticAction, SemanticSearch
from services import image_semantics as service

router=APIRouter()


@router.post('/jobs',status_code=201)
async def create_job(request:SemanticJobCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    job=await service.create_job(db,user.tenant_id,request)
    return await service.job_summary(db,job)


@router.get('/jobs')
async def list_jobs(collection_id:UUID|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),
                    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    await service.image_scope(db,user.tenant_id,collection_id)
    query=select(ImageSemanticJob).where(ImageSemanticJob.tenant_id==user.tenant_id)
    if collection_id:query=query.where(ImageSemanticJob.collection_id==collection_id)
    jobs=(await db.scalars(query.order_by(ImageSemanticJob.created_at.desc(),ImageSemanticJob.id).offset((page-1)*page_size).limit(page_size))).all()
    return [await service.job_summary(db,job) for job in jobs]


@router.get('/jobs/{job_id}')
async def get_job(job_id:UUID,page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=100),
                  db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await service.job_summary(db,await service.scoped_job(db,user.tenant_id,job_id),True,page,page_size)


@router.post('/jobs/{job_id}/cancel')
async def cancel_job(job_id:UUID,request:SemanticAction=SemanticAction(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await service.action_job(db,user.tenant_id,job_id,'cancel',request.item_ids)


@router.post('/jobs/{job_id}/retry')
async def retry_job(job_id:UUID,request:SemanticAction=SemanticAction(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await service.action_job(db,user.tenant_id,job_id,'retry',request.item_ids)


@router.post('/search')
async def search(request:SemanticSearch,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    tenant_id=user.tenant_id
    async with httpx.AsyncClient(follow_redirects=False) as client:
        return await service.search(db,tenant_id,request,client)
