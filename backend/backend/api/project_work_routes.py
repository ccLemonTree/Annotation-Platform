from uuid import UUID
import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from database import get_db_session
from api.deps import get_current_user
from api.asset_routes import scoped_project
from models.project_work import ProjectWorkJob, MatchQuery
from schemas.project_work import WorkCreate, MatchSearch, MatchJudgment, MatchReview, MatchReviewBatch
from schemas.vector_smart_search import SmartSearch
from services import project_work as service
from services import vector_smart_search as smart_search

router=APIRouter()


async def context(project_id:UUID,db=Depends(get_db_session),user=Depends(get_current_user)):
    return db,user,await scoped_project(db,project_id,user.tenant_id)


@router.post('/{project_id}/work/jobs',status_code=201)
async def create_job(request:WorkCreate,ctx=Depends(context)):
    db,user,project=ctx
    return await service.summary(db,await service.create_job(db,project,request,user.id))


@router.get('/{project_id}/work/jobs')
async def list_jobs(page:int=Query(1,ge=1),ctx=Depends(context)):
    db,_,project=ctx
    jobs=(await db.scalars(select(ProjectWorkJob).where(ProjectWorkJob.project_id==project.id)
        .order_by(ProjectWorkJob.created_at.desc(),ProjectWorkJob.id).offset((page-1)*20).limit(20))).all()
    return [await service.summary(db,job) for job in jobs]


@router.get('/{project_id}/work/jobs/{job_id}')
async def get_job(job_id:UUID,page:int=Query(1,ge=1),ctx=Depends(context)):
    db,_,project=ctx
    return await service.summary(db,await service.scoped_job(db,project,job_id),True,page)


@router.post('/{project_id}/work/jobs/{job_id}/{action}')
async def action_job(job_id:UUID,action:str,ctx=Depends(context)):
    from fastapi import HTTPException
    if action not in ('cancel','retry'):raise HTTPException(404,'操作不存在')
    db,_,project=ctx
    return await service.action_job(db,project,job_id,action)


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
    rows=(await db.scalars(select(MatchQuery).where(MatchQuery.project_id==project.id)
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
