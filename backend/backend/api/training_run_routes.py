from uuid import UUID
from fastapi import APIRouter,Depends,Query,Response,HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session
from api.deps import get_current_user,get_current_superuser
from models import User
from models.training_run import TrainingRun
from schemas.training_run import TrainingCreate,TrainingDatasetInput
from schemas.common import PaginationParams
from services import training_runs,gpu_servers
from services.training_config import catalog
from api.automation_routes import page_result
from services import training_analysis
from models.training_run import TrainingAnalysis
from schemas.model_output import ModelOutputCreate
from services import model_output

router=APIRouter(dependencies=[Depends(get_current_user)])


@router.get('/catalog')
async def templates():return catalog()


@router.get('/servers')
async def servers(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await gpu_servers.list_servers(db,user.tenant_id)


@router.get('/defaults')
async def defaults(kind:str,server_id:str='default',db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await training_runs.defaults(db,user,server_id,kind)


@router.post('/dataset')
async def dataset(request:TrainingDatasetInput,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return await training_runs.inspect_dataset(db,user,request)


@router.post('/runs',status_code=201)
async def create(request:TrainingCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.create(db,user,request))


@router.get('/runs')
async def list_runs(pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await page_result(db,select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id),TrainingRun,pagination)


@router.get('/runs/{id}')
async def detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return training_runs.summary(await training_runs.scoped(db,id,user.tenant_id))


@router.post('/runs/{id}/outputs',status_code=201)
async def output_job(id:UUID,request:ModelOutputCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await model_output.create(db,user,id,request))


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
async def update(id:UUID,request:TrainingCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return training_runs.summary(await training_runs.update(db,user,id,request))


@router.delete('/runs/{id}',status_code=204)
async def remove(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    await training_runs.remove(db,await training_runs.scoped(db,id,user.tenant_id))
    return Response(status_code=204)


@router.get('/runs/{id}/artifacts/download')
async def download(id:UUID,path:str=Query(min_length=1,max_length=1000),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await training_runs.download_artifact(db,await training_runs.scoped(db,id,user.tenant_id),path)


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
async def logs(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await training_runs.scoped(db,id,user.tenant_id)
    from services.training_logs import normalize_log
    result=await training_runs.remote(db,run,'logs')
    return {**result,'text':normalize_log(result['text'])}


@router.get('/runs/{id}/artifacts')
async def artifacts(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await training_runs.scoped(db,id,user.tenant_id)
    return await training_runs.remote(db,run,'artifacts')
