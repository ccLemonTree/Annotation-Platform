from datetime import timedelta
from time import monotonic
from types import SimpleNamespace
from uuid import UUID,uuid4
from typing import Literal
from fastapi import APIRouter,Depends,Query,Request,Response,HTTPException
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from config.settings import settings
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db_session
from api.deps import get_current_user,get_current_superuser
from models import User
from models.model_repository import ModelRepositoryItem
from models.training_run import TrainingRun
from schemas.common import PaginationParams
from schemas.model_repository import RepositoryMetadata,RepositoryImport,RepositoryUpdate,RepositoryBatchDownload,EXTENSIONS
from services import model_repository as service,training_runs
from services.auth import create_access_token,decode_access_token

router=APIRouter()
_batch_tickets:dict[str,tuple[float,list[UUID]]]={}


def _prune_batch_tickets(now:float)->None:
    expired=[k for k,(exp,_) in _batch_tickets.items() if exp<=now]
    for k in expired:_batch_tickets.pop(k,None)
    while len(_batch_tickets)>=500:
        _batch_tickets.pop(next(iter(_batch_tickets)),None)


@router.get('')
async def listing(pagination:PaginationParams=Depends(),q:str=Query('',max_length=100),format:Literal['pt','onnx','tensorrt','python','huggingface']|None=None,
        gpu_model:str=Query('',max_length=200),group_name:str|None=Query(None,max_length=100),run_id:UUID|None=None,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    query=service.query(user.tenant_id,q,format,gpu_model,group_name)
    if run_id:query=query.where(ModelRepositoryItem.run_id==run_id)
    total=await db.scalar(select(func.count()).select_from(query.subquery()))
    rows=(await db.scalars(query.order_by(ModelRepositoryItem.created_at.desc(),ModelRepositoryItem.id.desc()).offset(pagination.offset).limit(pagination.limit))).all()
    gpus=(await db.scalars(select(ModelRepositoryItem.gpu_model).where(ModelRepositoryItem.tenant_id==user.tenant_id,
        ModelRepositoryItem.format=='tensorrt',ModelRepositoryItem.gpu_model!='').distinct().order_by(ModelRepositoryItem.gpu_model))).all()
    return dict(items=[service.summary(row) for row in rows],total=total,page=pagination.page,page_size=pagination.page_size,gpu_models=gpus)


@router.get('/groups')
async def groups(pagination:PaginationParams=Depends(),q:str=Query('',max_length=100),format:Literal['pt','onnx','tensorrt','python','huggingface']|None=None,
        gpu_model:str=Query('',max_length=200),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await service.groups(db,user.tenant_id,pagination,q,format,gpu_model)


@router.get('/sources')
async def sources(pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    query=select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id,TrainingRun.status=='completed',TrainingRun.kind.in_(('yolo26','rfdetr_medium','rfdetr_2xlarge','model_export','model_merge','swift_sft','wemm')))
    total=await db.scalar(select(func.count()).select_from(query.subquery()))
    rows=(await db.scalars(query.order_by(TrainingRun.created_at.desc(),TrainingRun.id.desc()).offset(pagination.offset).limit(pagination.limit))).all()
    return dict(items=[{k:service.summary(row)[k] for k in ('id','name','kind','created_at')} for row in rows],total=total,page=pagination.page,page_size=pagination.page_size)


@router.post('/download-batch',response_class=Response,responses={200:{'content':{'application/zip':{'schema':{'type':'string','format':'binary'}}}}})
async def download_batch(request:RepositoryBatchDownload,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await service.download_batch(db,user.tenant_id,request)


@router.post('/download-batch/browser-download',status_code=204)
async def authorize_batch_download(payload:RepositoryBatchDownload,request:Request,
        db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    user_id,tenant_id=user.id,user.tenant_id
    rows=await service.resolve_batch_rows(db,tenant_id,payload)
    ids=[row.id for row in rows]
    await db.rollback()
    lifetime=timedelta(hours=2)
    now=monotonic();_prune_batch_tickets(now)
    batch_key=uuid4().hex
    _batch_tickets[batch_key]=(now+lifetime.total_seconds(),ids)
    ticket=create_access_token({'repository_batch_download':batch_key,'download_user_id':user_id,'tenant_id':tenant_id},lifetime)
    response=Response(status_code=204,headers={'Cache-Control':'no-store'})
    response.set_cookie('repository_batch_download',ticket,max_age=int(lifetime.total_seconds()),
        path=request.url.path,secure=request.url.scheme=='https',httponly=True,samesite='strict')
    return response


@router.get('/download-batch/browser-download',response_class=Response)
async def browser_batch_download(request:Request,db:AsyncSession=Depends(get_db_session)):
    cookie=request.cookies.get('repository_batch_download')
    ticket=decode_access_token(cookie) if cookie else None
    batch_key=ticket.get('repository_batch_download') if ticket else None
    now=monotonic();_prune_batch_tickets(now)
    entry=_batch_tickets.get(batch_key) if batch_key else None
    if not ticket or not entry or not ticket.get('download_user_id'):
        raise HTTPException(401,'下载授权已失效，请从模型仓库重新下载')
    user=await db.get(User,ticket['download_user_id'])
    if user is None or not user.is_active or user.role!='admin' or user.tenant_id!=ticket.get('tenant_id'):
        raise HTTPException(403,'当前账号无权下载模型仓库文件')
    return await service.download_batch(db,user.tenant_id,RepositoryBatchDownload(ids=entry[1]))


@router.get('/sources/{id}/artifacts')
async def artifacts(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    run=await service.source_run(db,id,user.tenant_id)
    result=await training_runs.list_artifacts(db,run);items=[]
    from pathlib import PurePosixPath
    if run.kind in ('swift_sft','wemm'):
        items.append(dict(path='base/config.json',bytes=0,format='huggingface',gpu_model='',tensorrt_version=''))
    for file in result['files']:
        model_entry=(run.kind=='model_merge' and file['path']=='merged/config.json') or (
            run.kind in ('swift_sft','wemm') and PurePosixPath(file['path']).name in ('config.json','adapter_config.json'))
        format='huggingface' if model_entry else next((
            kind for kind,suffixes in EXTENSIONS.items() if kind not in ('python','huggingface') and PurePosixPath(file['path']).suffix.lower() in suffixes),None)
        if format:items.append(dict(path=file['path'],bytes=file['bytes'],format=format,gpu_model='',tensorrt_version=''))
    # Read GPU provenance once; actual import rechecks the complete manifest and hashes.
    trt=next((item for item in items if item['format']=='tensorrt'),None)
    if trt:
        data=await service.metadata(db,run,trt['path'])
        for item in items:
            if item['format']=='tensorrt':item.update(gpu_model=data['gpu_model'],tensorrt_version=data['tensorrt_version'],batch_profile=data.get('batch_profile',''))
    return dict(items=items,truncated=result.get('truncated',False))




UPLOAD_SCHEMA=dict(type='object',required=['name','version','format','entry_file','files'],properties={
    **{key:{'type':'string'} for key in ('name','group_name','version','entry_file','gpu_model','tensorrt_version','batch_profile')},
    'format':{'type':'string','enum':['pt','onnx','tensorrt','python']},
    'files':{'type':'array','items':{'type':'string','format':'binary'}}})


@router.post('/upload',status_code=201,openapi_extra={'requestBody':{'required':True,'content':{'multipart/form-data':{'schema':UPLOAD_SCHEMA}}}})
async def upload(request:Request,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    # Authenticate before parsing/spooling large multipart bodies; cap chunked requests too.
    limit=settings.MAX_ARCHIVE_UPLOAD_SIZE+1024**2;received=0;message='模型上传请求超过大小限制'
    length=request.headers.get('content-length')
    if length is not None:
        if not length.isascii() or not length.isdigit():raise HTTPException(400,'Content-Length无效')
        if len(length)>20 or int(length)>limit:raise HTTPException(413,message)
    async def receive():
        nonlocal received
        chunk=await request.receive();received+=len(chunk.get('body',b''))
        if received>limit:raise MultiPartException(message)
        return chunk
    body=Request(request.scope,receive)
    try:
        async with body.form(max_files=500,max_fields=10,max_part_size=64*1024) as form:
            fields=('name','group_name','version','format','entry_file','gpu_model','tensorrt_version','batch_profile')
            if any(len(form.getlist(key))>1 for key in fields) or set(form)-set(fields)-{'files'}:raise HTTPException(422,'上传元数据字段无效或重复')
            try:metadata=RepositoryMetadata(**{key:form[key] for key in fields if key in form})
            except (ValidationError,KeyError,TypeError):raise HTTPException(422,'模型名称、版本、格式、主文件或GPU信息无效') from None
            files=form.getlist('files')
            if not files or any(not isinstance(file,UploadFile) for file in files):raise HTTPException(422,'请选择模型文件')
            return service.summary(await service.upload(db,user,metadata,files))
    except StarletteHTTPException as error:
        if error.detail==message:raise HTTPException(413,message) from None
        raise


@router.post('/from-training',status_code=201)
async def import_training(request:RepositoryImport,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.create_import(db,user,request))


@router.post('/sources/{id}/archive',status_code=202)
async def archive_training_model(id:UUID,request:RepositoryImport,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    if request.run_id!=id or request.format!='huggingface':raise HTTPException(422,'请选择此任务的模型目录')
    run=await service.source_run(db,id,user.tenant_id)
    task=service.schedule_automatic(run,request.artifact_path,user.id)
    await db.commit()
    import asyncio
    model_id=await asyncio.shield(task) if task else None
    if not model_id:raise HTTPException(409,'模型归档失败，请检查来源目录')
    return service.summary(await service.scoped(db,model_id,user.tenant_id))


@router.get('/{id}')
async def detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return service.summary(await service.scoped(db,id,user.tenant_id))


@router.get('/{id}/download')
async def download(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    row=await service.scoped(db,id,user.tenant_id)
    snap=SimpleNamespace(id=row.id,tenant_id=row.tenant_id,status=row.status,files=list(row.files),
        entry_file=row.entry_file,file_size=row.file_size)
    await db.rollback()
    return await service.download(snap)


@router.post('/{id}/browser-download',status_code=204)
async def authorize_model_download(id:UUID,request:Request,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    user_id,tenant_id=user.id,user.tenant_id
    row=await service.scoped(db,id,tenant_id)
    if row.status!='ready':raise HTTPException(409,'模型尚未入库完成')
    model_id=str(row.id)
    await db.rollback()
    lifetime=timedelta(hours=2)
    ticket=create_access_token({'repository_download':model_id,'download_user_id':user_id,'tenant_id':tenant_id},lifetime)
    response=Response(status_code=204,headers={'Cache-Control':'no-store'})
    response.set_cookie('repository_download',ticket,max_age=int(lifetime.total_seconds()),
        path=request.url.path,secure=request.url.scheme=='https',httponly=True,samesite='strict')
    return response


@router.get('/{id}/browser-download')
async def browser_model_download(id:UUID,request:Request,db:AsyncSession=Depends(get_db_session)):
    cookie=request.cookies.get('repository_download')
    ticket=decode_access_token(cookie) if cookie else None
    if not ticket or ticket.get('repository_download')!=str(id) or not ticket.get('download_user_id'):
        raise HTTPException(401,'下载授权已失效，请从模型仓库重新下载')
    user=await db.get(User,ticket['download_user_id'])
    if user is None or not user.is_active or user.role!='admin' or user.tenant_id!=ticket.get('tenant_id'):
        raise HTTPException(403,'当前账号无权下载此模型')
    row=await service.scoped(db,id,user.tenant_id)
    snap=SimpleNamespace(id=row.id,tenant_id=row.tenant_id,status=row.status,files=list(row.files),
        entry_file=row.entry_file,file_size=row.file_size)
    await db.rollback()
    return await service.download(snap)


@router.get('/{id}/config')
async def config(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await service.config(await service.scoped(db,id,user.tenant_id))


@router.patch('/{id}')
async def update(id:UUID,request:RepositoryUpdate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.update(db,await service.scoped(db,id,user.tenant_id,lock=True),request))


@router.post('/{id}/retry',status_code=202)
async def retry(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    return service.summary(await service.retry(db,user,await service.scoped(db,id,user.tenant_id,lock=True)))
