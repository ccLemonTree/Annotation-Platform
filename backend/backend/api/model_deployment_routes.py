from uuid import UUID
from typing import Literal
from types import SimpleNamespace
from fastapi import APIRouter,Depends,Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session
from api.deps import get_current_superuser
from models import User
from models.model_deployment import ModelDeployment,ModelRelease,DeploymentTarget
from models.training_run import TrainingRun
from schemas.model_deployment import DeploymentCreate,ReleaseCreate,TargetCreate,BatchCreate
from services import model_deployments as service
from services import distribution_center as center

router=APIRouter(dependencies=[Depends(get_current_superuser)])


@router.get('/servers')
async def servers():return await service.servers()


@router.get('/sources')
async def sources(page:int=Query(1,ge=1),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    query=select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id,TrainingRun.status=='completed',TrainingRun.kind.in_(('model_export','model_merge','swift_sft','wemm')))
    rows=(await db.scalars(query.order_by(TrainingRun.created_at.desc(),TrainingRun.id.desc()).offset((page-1)*20).limit(21))).all()
    return dict(items=[{**service.summary(row),'runtimes':[runtime for runtime in ('triton_plan','triton_vllm','vllm') if center.eligible(row,runtime)]} for row in rows[:20]],has_more=len(rows)>20)


@router.get('/releases')
async def releases(page:int=Query(1,ge=1),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    rows=(await db.scalars(select(ModelRelease).where(ModelRelease.tenant_id==user.tenant_id).order_by(ModelRelease.created_at.desc(),ModelRelease.id.desc()).offset((page-1)*20).limit(21))).all()
    return dict(items=[service.summary(row) for row in rows[:20]],has_more=len(rows)>20)


@router.post('/releases',status_code=201)
async def archive(request:ReleaseCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await center.create_release(db,user,request))


@router.post('/releases/{id}/retry',status_code=202)
async def retry_archive(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    from fastapi import HTTPException
    row=await center.scoped(db,ModelRelease,id,user.tenant_id,lock=True)
    if row.status not in ('failed','interrupted') or str(id) in center._archives:raise HTTPException(409,'此版本无需重新归档')
    row.user_id=user.id;row.status='archiving';row.error=None;await db.commit();await db.refresh(row);center.schedule_archive(row)
    return service.summary(row)


@router.get('/targets')
async def targets(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    rows=(await db.scalars(select(DeploymentTarget).where(DeploymentTarget.tenant_id==user.tenant_id).order_by(DeploymentTarget.name,DeploymentTarget.id))).all()
    return dict(items=[await center.target_summary(db,row) for row in rows])


@router.get('/targets/{id}/logs')
async def target_logs(id:UUID,source:Literal['updates','service']='updates',instance:str|None=Query(None,min_length=1,max_length=253),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    from services.deployment_logs import snapshot
    target=await center.scoped(db,DeploymentTarget,id,user.tenant_id)
    row=await db.scalar(select(ModelDeployment).where(ModelDeployment.target_id==id,ModelDeployment.tenant_id==user.tenant_id)
        .order_by(ModelDeployment.created_at.desc(),ModelDeployment.id.desc()).limit(1)) if source=='updates' else None
    if row is None:
        row=SimpleNamespace(id=target.id,target=target.target,kind='model',status='target',logs=[],inspection={})
    return await snapshot(db,row,source,instance)


@router.post('/targets',status_code=201)
async def add_target(request:TargetCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await center.target_summary(db,await center.create_target(db,user,request))


@router.post('/targets/{id}/inspect')
async def inspect_target(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await center.inspect_target(db,user,await center.scoped(db,DeploymentTarget,id,user.tenant_id,lock=True))


@router.put('/targets/{id}')
async def edit_target(id:UUID,request:TargetCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await center.target_summary(db,await center.update_target(db,user,id,request))


@router.post('/batches',status_code=201)
async def batch(request:BatchCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await center.create_batch(db,user,request)


@router.post('/batches/{id}/{operation}',status_code=202)
async def batch_action(id:UUID,operation:str,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await center.batch_action(db,user,id,operation)


@router.get('/batches/{id}')
async def batch_detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    from fastapi import HTTPException
    rows=(await db.scalars(select(ModelDeployment).where(ModelDeployment.tenant_id==user.tenant_id,ModelDeployment.batch_id==id).order_by(ModelDeployment.created_at,ModelDeployment.id))).all()
    if not rows:raise HTTPException(404,'批次不存在')
    return dict(batch_id=id,items=[service.summary(row) for row in rows])


@router.get('')
async def records(run_id:UUID|None=None,batch_id:UUID|None=None,page:int=Query(1,ge=1),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    query=select(ModelDeployment).where(ModelDeployment.tenant_id==user.tenant_id)
    if run_id:query=query.where(ModelDeployment.run_id==run_id)
    if batch_id:query=query.where(ModelDeployment.batch_id==batch_id)
    rows=(await db.scalars(query.order_by(ModelDeployment.created_at.desc(),ModelDeployment.id.desc()).offset((page-1)*20).limit(21))).all()
    return dict(items=[service.summary(row) for row in rows[:20]],has_more=len(rows)>20)


@router.post('',status_code=201)
async def create(request:DeploymentCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.create(db,user,request))


@router.get('/{id}')
async def detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.scoped(db,id,user.tenant_id))


@router.get('/{id}/logs')
async def record_logs(id:UUID,source:Literal['updates','service']='updates',instance:str|None=Query(None,min_length=1,max_length=253),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    from services.deployment_logs import snapshot
    return await snapshot(db,await service.scoped(db,id,user.tenant_id),source,instance)


@router.post('/{id}/refresh')
async def refresh(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.refresh(db,await service.scoped(db,id,user.tenant_id)))


@router.post('/{id}/precheck')
async def precheck(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.action(db,await service.scoped(db,id,user.tenant_id),'precheck',user))


@router.post('/{id}/{operation}',status_code=202)
async def execute(id:UUID,operation:str,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.action(db,await service.scoped(db,id,user.tenant_id),operation,user))
