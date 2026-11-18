from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID
from fastapi import APIRouter,Depends,Query,Request,Response,HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session
from api.deps import get_current_user,get_current_superuser
from models import Tenant,User
from models.training_run import TrainingRun,TrainingTrainer
from schemas.training_run import BuiltinTrainerDefaults,TrainingRunInput,TrainingDatasetInput,TrainingTrainerInput
from schemas.common import PaginationParams
from services import training_runs,gpu_servers
from services.auth import create_access_token,decode_access_token
from services.training_config import catalog,validate_defaults
from api.automation_routes import page_result
from services import training_analysis
from models.training_run import TrainingAnalysis
from schemas.model_output import ModelOutputCreate
from services import model_output
import asyncio

router=APIRouter()


@router.get('/catalog')
async def templates(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return catalog(await training_runs.builtin_defaults(db,user.tenant_id))


@router.put('/trainers/builtin/{kind}/defaults')
async def update_builtin_defaults(kind:str,request:BuiltinTrainerDefaults,db:AsyncSession=Depends(get_db_session),
                                  user:User=Depends(get_current_superuser)):
    parameters=validate_defaults(kind,request.parameters)
    tenant=await db.get(Tenant,user.tenant_id,with_for_update=True)
    defaults=dict(tenant.training_defaults)
    if parameters:
        defaults[kind]=parameters
    else:
        defaults.pop(kind,None)
    tenant.training_defaults=defaults
    await db.commit()
    return catalog(defaults)[kind]


@router.get('/trainers')
async def list_trainers(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    rows=await db.scalars(select(TrainingTrainer).where(TrainingTrainer.tenant_id==user.tenant_id)
                          .order_by(TrainingTrainer.definition['name'].as_string(),TrainingTrainer.id))
    return [dict(id=row.id,**row.definition) for row in rows]


@router.post('/trainers',status_code=201)
async def register_trainer(request:TrainingTrainerInput,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    row=TrainingTrainer(tenant_id=user.tenant_id,definition=request.model_dump())
    db.add(row)
    await db.flush()
    result=dict(id=row.id,**row.definition)
    await db.commit()
    return result


@router.put('/trainers/{id}')
async def update_trainer(id:UUID,request:TrainingTrainerInput,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    row=await training_runs.scoped_trainer(db,user,id)
    row.definition=request.model_dump()
    result=dict(id=row.id,**row.definition)
    await db.commit()
    return result


@router.delete('/trainers/{id}',status_code=204)
async def delete_trainer(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    await db.delete(await training_runs.scoped_trainer(db,user,id))
    await db.commit()
    return Response(status_code=204)


@router.get('/servers')
async def servers(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    rows=await gpu_servers.list_servers(db,user.tenant_id)
    default=next(row for row in rows if row['id']=='default')
    candidates=[row for row in rows if row['source']=='password' and row['training_enabled'] and row['enabled'] and
        row['has_password'] and row['name'].strip().casefold()==default['name'].strip().casefold()]
    if candidates:
        try:
            connections=await asyncio.wait_for(asyncio.to_thread(training_runs.bridge,'',dict(list_servers=True)),3)
        except (HTTPException,TimeoutError):
            return rows
        endpoint=next((row for row in connections if row['name']==default['name']),None)
        if endpoint:
            for row in candidates:
                if (row['host'],row['port'],row['username'])==(endpoint['host'],endpoint['port'],endpoint['user']):
                    row['duplicate_of_default']=True
    return rows


@router.get('/k8s/nodes')
async def k8s_nodes(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    from schemas.training_run import K8S_NFS_ROOT,DETECTION_IMAGE,SWIFT_IMAGE
    from services.k8s_training_recipes import recipe_defaults
    rows=await asyncio.to_thread(training_runs.bridge,training_runs.K8S_SERVER['name'],
        dict(executor='k8s_training',payload=dict(action='nodes')))
    nodes=[{**row,'gpu_capacity':row['capacity'],'gpu_available':row['available'],
        'ready':row['ready'] and not row.get('unschedulable',False)} for row in rows if row['capacity']>0]
    wemm=recipe_defaults('wemm')
    wemm['parameters'].update((await training_runs.builtin_defaults(db,user.tenant_id)).get('wemm',{}))
    return dict(nodes=nodes,namespace='aisfprod',pvc='nfs-pvc-ai',nfs_root=K8S_NFS_ROOT,
        output_root=K8S_NFS_ROOT+'/runs',images=dict(detection=DETECTION_IMAGE,wemm=SWIFT_IMAGE),
        wemm_defaults=wemm)


@router.get('/defaults')
async def defaults(kind:str,server_id:str='default',db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await training_runs.defaults(db,user,server_id,kind)


@router.post('/dataset')
async def dataset(request:TrainingDatasetInput,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await training_runs.inspect_dataset(db,user,request)


@router.post('/runs',status_code=201)
async def create(request:TrainingRunInput,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.create(db,user,request))


@router.get('/runs')
async def list_runs(project_id:UUID|None=None,pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    query=select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id)
    if project_id:
        query=query.where(TrainingRun.source['project_id'].as_string()==str(project_id))
    return await page_result(db,query,TrainingRun,pagination)


@router.get('/runs/{id}')
async def detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return training_runs.summary(await training_runs.scoped(db,id,user.tenant_id))


@router.post('/runs/{id}/outputs',status_code=201)
async def output_job(id:UUID,request:ModelOutputCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await model_output.create(db,user,id,request))


@router.put('/runs/{id}/outputs')
async def update_output_job(id:UUID,request:ModelOutputCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await model_output.update(db,user,id,request))


@router.get('/runs/{id}/comparison-candidates')
async def comparison_candidates(id:UUID,pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await training_runs.scoped(db,id,user.tenant_id)
    query=select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id,TrainingRun.id!=id,
        TrainingRun.created_at<run.created_at,TrainingRun.status=='completed').order_by(TrainingRun.created_at.desc())
    return await page_result(db,query,TrainingRun,pagination)


@router.post('/runs/{id}/analysis/evidence')
async def analysis_evidence(id:UUID,request:training_analysis.EvidenceRequest,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await training_analysis.evidence(db,user,id,request)


@router.get('/runs/{id}/analysis')
async def analysis_history(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    await training_runs.scoped(db,id,user.tenant_id)
    rows=(await db.scalars(select(TrainingAnalysis).where(TrainingAnalysis.run_id==id,TrainingAnalysis.tenant_id==user.tenant_id)
        .order_by(TrainingAnalysis.created_at.desc(),TrainingAnalysis.id.desc()).limit(10))).all()
    return [training_analysis.report_json(row,detail=False) for row in rows]


@router.get('/runs/{id}/analysis/{report_id}')
async def analysis_report(id:UUID,report_id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    await training_runs.scoped(db,id,user.tenant_id)
    row=await db.scalar(select(TrainingAnalysis).where(TrainingAnalysis.id==report_id,TrainingAnalysis.run_id==id,TrainingAnalysis.tenant_id==user.tenant_id))
    if row is None:raise HTTPException(404,'分析报告不存在')
    return training_analysis.report_json(row)


@router.post('/runs/{id}/analysis')
async def create_analysis(id:UUID,request:training_analysis.AnalysisRequest,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await training_analysis.analyze(db,user,id,request)


@router.put('/runs/{id}')
async def update(id:UUID,request:TrainingRunInput,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.update(db,user,id,request))


@router.delete('/runs/{id}',status_code=204)
async def remove(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    await training_runs.remove(db,await training_runs.scoped(db,id,user.tenant_id))
    return Response(status_code=204)


@router.get('/runs/{id}/artifacts/download')
async def download(id:UUID,path:str=Query(min_length=1,max_length=1000),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    user_id,tenant_id,is_admin=user.id,user.tenant_id,user.role=='admin'
    run=await training_runs.scoped(db,id,tenant_id)
    run_snap=SimpleNamespace(id=run.id,tenant_id=run.tenant_id,user_id=run.user_id,kind=run.kind,status=run.status)
    response=await training_runs.download_artifact(db,run,path)
    if is_admin:
        from services.model_repository import schedule_automatic
        schedule_automatic(run_snap,path,user_id)
    return response


@router.post('/runs/{id}/artifacts/browser-download',status_code=204)
async def authorize_artifact_download(id:UUID,request:Request,path:str=Query(min_length=1,max_length=1000),
        db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    training_runs.validate_artifact_path(path)
    user_id,tenant_id=user.id,user.tenant_id
    run=await training_runs.scoped(db,id,tenant_id)
    run_id_str=str(run.id)
    await db.rollback()
    lifetime=timedelta(hours=2)
    ticket=create_access_token({'training_artifact_download':run_id_str,'artifact_path':path,
        'download_user_id':user_id,'tenant_id':tenant_id},lifetime)
    response=Response(status_code=204,headers={'Cache-Control':'no-store'})
    response.set_cookie('training_artifact_download',ticket,max_age=int(lifetime.total_seconds()),
        path=request.url.path,secure=request.url.scheme=='https',httponly=True,samesite='strict')
    return response


@router.get('/runs/{id}/artifacts/browser-download')
async def browser_artifact_download(id:UUID,request:Request,path:str=Query(min_length=1,max_length=1000),
        db:AsyncSession=Depends(get_db_session)):
    training_runs.validate_artifact_path(path)
    cookie=request.cookies.get('training_artifact_download')
    ticket=decode_access_token(cookie) if cookie else None
    if (not ticket or ticket.get('training_artifact_download')!=str(id)
            or ticket.get('artifact_path')!=path or not ticket.get('download_user_id')):
        raise HTTPException(401,'下载授权已失效，请从训练任务列表重新下载')
    user=await db.get(User,ticket['download_user_id'])
    if user is None or not user.is_active or user.role!='admin' or user.tenant_id!=ticket.get('tenant_id'):
        raise HTTPException(403,'当前账号无权下载此训练产物')
    user_id,tenant_id,is_admin=user.id,user.tenant_id,user.role=='admin'
    run=await training_runs.scoped(db,id,tenant_id)
    run_snap=SimpleNamespace(id=run.id,tenant_id=run.tenant_id,user_id=run.user_id,kind=run.kind,status=run.status)
    response=await training_runs.download_artifact(db,run,path)
    if is_admin:
        from services.model_repository import schedule_automatic
        schedule_automatic(run_snap,path,user_id)
    return response


@router.post('/runs/{id}/precheck')
async def check(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await training_runs.check(db,await training_runs.scoped(db,id,user.tenant_id))


@router.post('/runs/{id}/start',status_code=202)
async def start(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.launch(db,await training_runs.scoped(db,id,user.tenant_id),user))


@router.post('/runs/{id}/stop')
async def stop(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.stop(db,await training_runs.scoped(db,id,user.tenant_id)))


@router.post('/runs/{id}/refresh')
async def refresh(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.refresh(db,await training_runs.scoped(db,id,user.tenant_id)))


@router.get('/runs/{id}/logs')
async def logs(id:UUID,node:str|None=Query(default=None,max_length=253),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await training_runs.scoped(db,id,user.tenant_id)
    if node is not None and (run.config.get('execution_backend')!='k8s' or node not in run.config['k8s']['nodes']):
        raise HTTPException(422,'请选择此训练任务的节点')
    from services.training_logs import normalize_log
    result=await training_runs.remote(db,run,'logs',**(dict(node=node) if node else {}))
    return {**result,'text':normalize_log(result['text'])}


@router.get('/runs/{id}/artifacts')
async def artifacts(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await training_runs.scoped(db,id,user.tenant_id)
    return await training_runs.list_artifacts(db,run)
