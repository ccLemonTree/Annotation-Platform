"""Durable run lifecycle; only explicit start can launch a training container."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path,PurePosixPath
import subprocess
import tempfile
import weakref
from datetime import datetime,timezone
from uuid import UUID,uuid4
from types import SimpleNamespace
from sqlalchemy import select,delete
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from pydantic import ValidationError
from config.settings import settings
from database import AsyncSessionLocal
from models import ExportSnapshot,Project,Tenant,User
from models.training_run import TrainingRun,TrainingGpuHold,TrainingTrainer
from services import gpu_servers
from services.storage import get_storage
from services.snapshot_manifests import read_manifest
from services.training_config import effective_config,validate_snapshot,build_script,configure_trainer,PARAMETERS
from schemas.training_run import TrainingCreate,default_output_root

ROOT=Path(__file__).resolve().parents[1]
ACTIVE=('starting','running','stopping','unknown')
K8S_SERVER=dict(id='jiashan-k8s',name='嘉善移动',revision=1)
_worker=None
_startups={}
_snapshot_locks=weakref.WeakValueDictionary()
logger=logging.getLogger(__name__)


async def scoped(db,id,tenant_id,lock=False):
    query=select(TrainingRun).where(TrainingRun.id==id,TrainingRun.tenant_id==tenant_id)
    if lock:query=query.with_for_update()
    run=await db.scalar(query.execution_options(populate_existing=True))
    if run is None:raise HTTPException(404,'训练任务不存在')
    return run


def summary(run):
    result={c.name:getattr(run,c.name) for c in run.__table__.columns}
    return {key:value.replace(tzinfo=timezone.utc) if isinstance(value,datetime) and value.tzinfo is None else value for key,value in result.items()}


async def server_snapshot(db,id,tenant_id,require_training=False):
    if id=='default':return dict(id='default',name=settings.GPU_SERVER_NAME,revision=0)
    try:server_id=UUID(id)
    except ValueError:raise HTTPException(400,'训练服务器ID格式无效') from None
    server=await gpu_servers.scoped_server(db,server_id,tenant_id)
    if require_training and not server.training_enabled:raise HTTPException(409,'此服务器未设为训练服务器，请在服务器管理中指定')
    if not server.enabled or not server.password_encrypted:raise HTTPException(409,'训练服务器已停用或未保存密码')
    return dict(id=str(server.id),name=server.name,revision=server.revision,connection_key=gpu_servers.connection_key(server))


async def connection(db,run):
    if getattr(run,'config',{}).get('execution_backend')=='k8s':return None
    if run.server['id']=='default':
        if run.server['name']!=settings.GPU_SERVER_NAME:raise HTTPException(409,'默认服务器配置已变化，请恢复原连接后核对任务')
        return None
    row=await gpu_servers.scoped_server(db,UUID(run.server['id']),run.tenant_id)
    if not gpu_servers.matches_connection(row,run.server):
        raise HTTPException(409,'服务器连接'+('目标' if run.server.get('connection_key') else '修订')+'已变化，不能操作旧任务')
    return gpu_servers.connection(row)


def bridge(server,input):
    entry=Path(settings.GPU_SSH_MANAGER_ENTRY)
    if not settings.GPU_SSH_MANAGER_ENTRY or not entry.is_file():raise HTTPException(503,'未配置ssh-manager MCP入口')
    args=[settings.GPU_SSH_NODE_BINARY,str(ROOT/'scripts/training_mcp_bridge.mjs'),str(entry),server]
    timeout=40 if input.get('executor')=='deployment_logs' else 330
    try:
        if input.get('upload') or input.get('download'):
            size=Path(input['upload']['localPath']).stat().st_size if input.get('upload') else input['download'].get('bytes',0)
            timeout=min(4*3600,max(330,size//(128*1024)+120))
        response=subprocess.run(args,input=json.dumps({**input,'timeout_seconds':timeout},ensure_ascii=False),capture_output=True,text=True,
            encoding='utf-8',timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0)
        value=json.loads(response.stdout)
    except subprocess.TimeoutExpired:
        if input.get('upload'):raise HTTPException(504,f'文件上传超时（{timeout}秒），请检查网络或缩小批次后重试') from None
        raise HTTPException(503,'SSH通信失败，执行状态待核对；GPU占用不会自动释放') from None
    except (OSError,ValueError):raise HTTPException(503,'SSH通信失败，执行状态待核对；GPU占用不会自动释放') from None
    if not value.get('success'):raise HTTPException(503,str(value.get('error','远端操作失败'))[-2000:])
    return value.get('result',value)


async def remote(db,run,action,**values):
    if run.config.get('onnx_stage'):
        from services import model_output_pipeline
        return await model_output_pipeline.remote(db,run,action,**values)
    return await remote_direct(db,run,action,**values)


async def _release_clean_db(db):
    if db is not None and not db.new and not db.dirty and not db.deleted and db.in_transaction():
        await db.commit()


async def remote_direct(db,run,action,**values):
    if run.config.get('execution_backend')=='k8s':
        from services.k8s_training_recipes import build_assets
        payload=dict(action=action,run_id=str(run.id),config=run.config,**values)
        if action in ('snapshot_cache','unpack','unpack_begin'):
            from services.k8s_training_snapshots import support_code
            payload['snapshot_code']=support_code()
        if action=='start':
            payload['assets']=build_assets(run.config)
            if run.config.get('snapshot_id'):payload['snapshot_hash']=run.source['sha256']
        await _release_clean_db(db)
        return await asyncio.to_thread(bridge,K8S_SERVER['name'],dict(executor='k8s_training',payload=payload))
    conn=await connection(db,run)
    server_name='platform_training' if conn else run.server['name']
    payload=dict(action=action,run_id=str(run.id),config=run.config,**values)
    if run.config.get('kind') in ('model_export','model_merge'):
        if action in ('precheck','start'):payload['output_code']=(ROOT/'services/model_output_runner.py').read_text(encoding='utf-8')
    elif action in ('dataset','precheck','start') and run.config.get('kind') not in ('swift_sft','wemm','custom'):
        payload['dataset_code']=(ROOT/'services/training_dataset.py').read_text(encoding='utf-8')
    await _release_clean_db(db)
    return await asyncio.to_thread(bridge,server_name,dict(connection=conn,payload=payload))


async def defaults(db,user,server_id,kind):
    filters=[TrainingRun.tenant_id==user.tenant_id,TrainingRun.server['id'].as_string()==server_id]
    if kind in PARAMETERS:
        filters.append(TrainingRun.kind==kind)
    else:
        try:trainer_id=UUID(kind)
        except ValueError:raise HTTPException(400,'未知训练器') from None
        filters.extend((TrainingRun.kind=='custom',TrainingRun.config['trainer_id'].as_string()==str(trainer_id)))
    await server_snapshot(db,server_id,user.tenant_id)
    run=await db.scalar(select(TrainingRun).where(*filters).order_by(TrainingRun.created_at.desc(),TrainingRun.id.desc()).limit(1))
    return dict(model_path=run.config['model_path'] if run else '',output_root=default_output_root())


async def scoped_trainer(db,user,id):
    row=await db.scalar(select(TrainingTrainer).where(TrainingTrainer.id==id,TrainingTrainer.tenant_id==user.tenant_id))
    if row is None:raise HTTPException(404,'训练器不存在或已移除')
    return row


async def builtin_defaults(db,tenant_id):
    return await db.scalar(select(Tenant.training_defaults).where(Tenant.id==tenant_id)) or {}


async def inspect_dataset(db,user,request):
    server=await server_snapshot(db,request.server_id,user.tenant_id)
    run=SimpleNamespace(id=uuid4(),tenant_id=user.tenant_id,server=server,config=request.model_dump())
    return await remote(db,run,'dataset')


async def configuration(db,user,request,saved_trainer=None,previous=None):
    if not isinstance(request,TrainingCreate):
        if previous and previous.kind==request.kind and previous.config.get('execution_backend','docker')==request.execution_backend:
            parameters=previous.config.get('parameters',{})
        else:
            parameters=(await builtin_defaults(db,user.tenant_id)).get(request.kind,{})
        try:
            request=TrainingCreate.model_validate(request.model_dump(),context={'parameters':parameters})
        except ValidationError as error:
            raise HTTPException(422,error.errors(include_url=False,include_context=False,include_input=False)) from None
    if not request.snapshot_id:raise HTTPException(422,'新建或修改训练配置仅支持平台冻结导出，请选择项目冻结版本')
    config=effective_config(request)
    snapshot=await db.scalar(select(ExportSnapshot).join(Project).where(ExportSnapshot.id==request.snapshot_id,Project.tenant_id==user.tenant_id).with_for_update(of=ExportSnapshot))
    if snapshot is None:raise HTTPException(404,'冻结导出不存在或不属于当前租户')
    manifest=await read_manifest(snapshot.manifest_path,snapshot.manifest)
    if request.kind=='custom':
        definition=saved_trainer if saved_trainer is not None else (await scoped_trainer(db,user,request.trainer_id)).definition
        config['runtime_dataset']=configure_trainer(config,definition,snapshot.export_format,manifest)
    else:
        validate_snapshot(request.kind,snapshot.export_format,manifest,request.parameters)
    training=manifest.get('training',{})
    if request.kind!='custom' and snapshot.export_format=='infonce' and training.get('negative_strategy')=='explicit':
        config['environment']['INFONCE_USE_BATCH']='false'
        config['environment'].pop('INFONCE_HARD_NEGATIVES',None)
    elif request.kind!='custom' and (
        training.get('source') in ('descriptions','attributes') and snapshot.export_format in ('infonce','online_contrastive')
        or training.get('source')=='single_attribute' and snapshot.export_format=='infonce'
    ):
        if training['samples']<2:
            raise HTTPException(400,'图片描述或属性配对至少需要两条样本才能组成有效训练批次')
        if snapshot.export_format=='infonce':
            config['environment']['INFONCE_USE_BATCH']='true'
            config['environment'].pop('INFONCE_HARD_NEGATIVES',None)
        gpu_count=(len(request.k8s.nodes)*request.k8s.gpus_per_node if request.k8s else len(request.gpu_uuids))
        if snapshot.export_format=='online_contrastive' or gpu_count==1:
            config['parameters']['per_device_train_batch_size']=max(2,config['parameters']['per_device_train_batch_size'])
    if request.selected_labels and set(request.selected_labels)-set(manifest.get('labels',[])):
        raise HTTPException(400,'所选训练标签不在冻结数据集中')
    source=dict(kind='snapshot',id=str(snapshot.id),blob_path=snapshot.blob_path,sha256=snapshot.content_hash,
        format=snapshot.export_format,project_id=str(snapshot.project_id),labels=manifest.get('labels',[]))
    config.pop('repository_model_id',None)
    if request.repository_model_id:
        from services import model_repository
        row=await model_repository.scoped(db,request.repository_model_id,user.tenant_id)
        parent=await scoped(db,row.run_id,user.tenant_id) if row.run_id else None
        expected=(parent.source.get('model_kind') or parent.kind) if parent else None
        if row.status!='ready' or row.format!='huggingface' or not row.entry_file.endswith('config.json') or row.entry_file.endswith('adapter_config.json') or expected!=request.kind:
            raise HTTPException(422,'请选择同类训练器可用的完整模型')
        source['repository_model_id']=str(row.id)
    if request.kind!='custom':
        config['runtime_dataset']=('/output/dataset/train.jsonl' if request.kind in ('swift_sft','wemm') else
            '/output/dataset/data.yaml' if request.kind=='yolo26' else '/output/dataset/rfdetr')
    if request.execution_backend=='k8s' and request.kind!='wemm':
        config['runtime_dataset']='/output/prepared-dataset/data.yaml' if request.kind=='yolo26' else '/output/prepared-dataset'
    server=dict(K8S_SERVER) if request.execution_backend=='k8s' else await server_snapshot(db,request.server_id,user.tenant_id,require_training=True)
    return config,source,server


async def create(db,user,request):
    config,source,server=await configuration(db,user,request)
    id=uuid4()
    if source.get('repository_model_id'):config['model_path']=f"{config['output_root']}/{id}/base"
    run=TrainingRun(id=id,tenant_id=user.tenant_id,user_id=user.id,name=request.name,kind=request.kind,
        config=config,source=source,server=server,output_dir=request.output_root+'/'+str(id),status='draft',inspection={})
    db.add(run);await db.commit();await db.refresh(run)
    return run


async def update(db,user,id,request):
    run=await scoped(db,id,user.tenant_id,lock=True)
    if run.kind in ('model_export','model_merge'):raise HTTPException(409,'导出/合并任务请从来源训练任务新建，不能改为训练任务')
    if run.status!='draft':raise HTTPException(409,'仅待启动任务可以修改配置；已提交任务请创建新任务')
    saved_trainer=run.config.get('trainer') if str(request.trainer_id)==run.config.get('trainer_id') else None
    config,source,server=await configuration(db,user,request,saved_trainer,previous=run)
    if source.get('repository_model_id'):config['model_path']=f"{config['output_root']}/{run.id}/base"
    run.name=request.name;run.kind=request.kind;run.config=config;run.source=source;run.server=server
    run.output_dir=request.output_root+'/'+str(run.id);run.inspection={};run.error=None
    await db.commit();await db.refresh(run)
    return run


async def remove(db,run):
    run=await scoped(db,run.id,run.tenant_id,lock=True)
    if run.status in ACTIVE:raise HTTPException(409,'请先停止训练并确认状态，再删除任务记录')
    if await db.scalar(select(TrainingGpuHold.gpu_uuid).where(TrainingGpuHold.run_id==run.id).limit(1)):
        raise HTTPException(409,'GPU占用尚未释放，请先核对并停止任务')
    from sqlalchemy import inspect
    from models.model_deployment import ModelDeployment,ModelRelease
    conn=await db.connection()
    if await conn.run_sync(lambda c:inspect(c).has_table('model_deployments')):
        if await db.scalar(select(ModelDeployment.id).where(ModelDeployment.run_id==run.id).limit(1)):
            raise HTTPException(409,'此任务已有模型下发记录，需保留来源以便追溯和恢复')
    if await conn.run_sync(lambda c:inspect(c).has_table('model_releases')):
        if await db.scalar(select(ModelRelease.id).where(ModelRelease.run_id==run.id).limit(1)):
            raise HTTPException(409,'此任务已有本地模型版本，需保留来源以便追溯')
    from models.model_repository import ModelRepositoryItem
    if await conn.run_sync(lambda c:inspect(c).has_table('model_repository_items')):
        if await db.scalar(select(ModelRepositoryItem.id).where(ModelRepositoryItem.run_id==run.id).limit(1)):
            raise HTTPException(409,'此任务已有模型仓库记录，需保留来源以便追溯')
    await db.delete(run);await db.commit()


async def list_artifacts(db,run):
    try:return await remote(db,run,'artifacts')
    except HTTPException as error:
        cached=run.inspection.get('runtime',{}).get('artifacts')
        if error.status_code<500 or run.status!='completed' or not cached or not cached.get('files'):raise
        return {**cached,'cached':True}


def validate_artifact_path(path: str) -> str:
    if not path or len(path)>1000 or '\\' in path or PurePosixPath(path).is_absolute() or any(p in ('','.', '..') for p in path.split('/')):
        raise HTTPException(422,'产物相对路径无效')
    return path


async def artifact_metadata(db,run,path):
    validate_artifact_path(path)
    metadata=await remote(db,run,'artifact',path=path)
    expected=str(PurePosixPath(run.output_dir)/run.config['output_subdir']/path)
    logical=metadata.get('logical_path') if run.config.get('execution_backend')=='k8s' else metadata.get('path')
    if logical!=expected or metadata.get('bytes',-1)<0 or len(metadata.get('sha256',''))!=64:
        raise HTTPException(502,'产物元数据无效')
    return metadata


async def fetch_artifact(db,run,metadata,local):
    conn=await connection(db,run)
    server_name='platform_training' if conn else run.server['name']
    await _release_clean_db(db)
    task=asyncio.create_task(asyncio.to_thread(bridge,server_name,
        dict(connection=conn,download=dict(remotePath=metadata['path'],localPath=str(local),bytes=metadata['bytes']))))
    try:await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    def verify():
        if local.is_symlink() or not local.is_file() or local.stat().st_size!=metadata['bytes']:
            return False
        digest=hashlib.sha256()
        with local.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024**2),b''):digest.update(chunk)
        return digest.hexdigest()==metadata['sha256']
    hashing=asyncio.create_task(asyncio.to_thread(verify))
    try:verified=await asyncio.shield(hashing)
    except asyncio.CancelledError:
        await hashing
        raise
    if not verified:raise HTTPException(409,'产物在下载时发生变化或传输不完整，SHA256校验失败')


async def download_artifact(db,run,path):
    from starlette.responses import FileResponse
    metadata=await artifact_metadata(db,run,path)
    folder=ROOT/'.backups/artifact-downloads';folder.mkdir(parents=True,exist_ok=True)
    temp=tempfile.TemporaryDirectory(prefix='artifact-',dir=folder);local=Path(temp.name)/'download'
    try:
        await fetch_artifact(db,run,metadata,local)
    except BaseException:
        temp.cleanup();raise
    if db is not None and db.in_transaction():
        await db.rollback()
    class DownloadResponse(FileResponse):
        async def __call__(self,*args,**kwargs):
            try:await super().__call__(*args,**kwargs)
            finally:await asyncio.to_thread(temp.cleanup)
    return DownloadResponse(local,filename=PurePosixPath(path).name,media_type='application/octet-stream')


async def check(db,run):
    run_id,tenant_id=run.id,run.tenant_id
    run=await scoped(db,run_id,tenant_id)
    checkpoint=run.source.get('checkpoint_artifact')
    pending={'pending_checkpoint':True} if checkpoint and not run.config.get('onnx_stage') else {}
    if run.source.get('merge_inputs'):pending['pending_merge']=True
    if run.source.get('repository_model_id'):pending['pending_base']=True
    await _release_clean_db(db)
    result=await remote(db,run,'precheck',**pending)
    if checkpoint:
        from services.model_output_pipeline import checkpoint_source
        try:
            parent,metadata=await checkpoint_source(db,run)
            source=dict(name='训练产物',ok=True,detail=f"{parent.server['name']} · {checkpoint} · {metadata['bytes']} 字节")
        except HTTPException as error:
            source=dict(name='训练产物',ok=False,detail=str(error.detail))
        result['checks'].append(source)
        result['ok']=result['ok'] and source['ok']
    await _release_clean_db(db)
    run=await scoped(db,run_id,tenant_id,lock=True)
    held=(await db.scalars(select(TrainingGpuHold.gpu_uuid).where(TrainingGpuHold.gpu_uuid.in_(reserved_gpus(run)),TrainingGpuHold.run_id!=run.id))).all()
    if held:
        result['checks'].append(dict(name='平台资源占用',ok=False,detail='所选GPU已分配给其他训练任务'))
        result['ok']=False
    run.inspection={**run.inspection,**result};await db.commit()
    return result


async def startup_progress(db,run,phase,message,state='running',**details):
    await db.refresh(run)
    if state=='running' and run.status!='starting':raise HTTPException(409,'任务已停止准备，不再继续启动')
    previous=run.inspection.get('startup',{})
    event=dict(phase=phase,message=message,time=datetime.now(timezone.utc).isoformat())
    run.inspection={**run.inspection,'startup':{**previous,**details,'state':state,'phase':phase,
        'events':(previous.get('events',[])+[event])[-40:],'updated_at':event['time']}}
    await db.commit()
    await db.refresh(run)
    await db.commit()


async def ensure_starting(db,run):
    await db.refresh(run)
    actor=await db.get(User,run.user_id,populate_existing=True)
    if run.status!='starting' or actor is None or not actor.is_active or actor.role!='admin':
        raise HTTPException(409,'任务已取消或管理员权限变化；停止启动')
    await db.commit()


async def transfer_snapshot(db,run):
    if run.source['kind']!='snapshot':return
    key=(run.server['id'],run.source['sha256'])
    lock=_snapshot_locks.setdefault(key,asyncio.Lock())
    async with lock:await transfer_cached_snapshot(db,run)


async def prepare_snapshot(db,run):
    sha=run.source['sha256']
    if getattr(run,'config',{}).get('execution_backend')!='k8s':
        await remote(db,run,'unpack',snapshot_hash=sha)
        return
    result=await remote(db,run,'unpack_begin',snapshot_hash=sha)
    while result.get('state')=='running':
        await asyncio.sleep(3)
        await ensure_starting(db,run)
        result=await remote(db,run,'unpack_status',snapshot_hash=sha)
    await ensure_starting(db,run)
    if result.get('state')!='ready':
        raise HTTPException(503,result.get('error') or '冻结数据准备未完成，请核对任务状态')


async def transfer_cached_snapshot(db,run):
    await ensure_starting(db,run)
    await startup_progress(db,run,'archive','正在按SHA256检查服务器冻结包缓存及已上传的历史任务')
    previous=(await db.scalars(select(TrainingRun.output_dir).where(TrainingRun.tenant_id==run.tenant_id,
        TrainingRun.id!=run.id,TrainingRun.source['sha256'].as_string()==run.source['sha256'],
        TrainingRun.server['id'].as_string()==run.server['id']).order_by(TrainingRun.created_at.desc()).limit(5))).all()
    await ensure_starting(db,run)
    cached=await remote(db,run,'snapshot_cache',snapshot_hash=run.source['sha256'],
        legacy_archives=[directory+'/input.zip' for directory in previous])
    if cached['hit']:
        steps=[step for step in run.inspection['startup']['steps'] if step!='upload']
        await startup_progress(db,run,'unpack',('已校验并缓存历史任务冻结包，跳过上传' if cached.get('imported') else
            '服务器冻结包SHA256缓存命中，跳过上传')+'；正在独立解包及逐张核验',cache_hit=True,steps=steps)
        await ensure_starting(db,run)
        await prepare_snapshot(db,run)
        return
    await startup_progress(db,run,'archive','缓存未命中，正在校验并暂存平台冻结包',cache_hit=False)
    folder=ROOT/'.backups/training-transfer';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='run-',dir=folder) as temp:
        local=Path(temp)/'input.zip'
        with await get_storage().get_file(run.source['blob_path']) as inp:
            def copy():
                digest=hashlib.sha256();size=0
                with local.open('wb') as out:
                    for chunk in iter(lambda:inp.read(1024*1024),b''):
                        size+=len(chunk)
                        if size>40*1024**3:raise HTTPException(400,'冻结包超过40GiB传输上限')
                        out.write(chunk);digest.update(chunk)
                if digest.hexdigest()!=run.source['sha256']:raise HTTPException(409,'平台冻结包SHA256已变化，禁止训练')
            await asyncio.to_thread(copy)
        conn=await connection(db,run)
        await ensure_starting(db,run)
        size=local.stat().st_size
        await startup_progress(db,run,'upload',f'正在传输冻结包（{size/1024**2:.1f} MiB）',archive_bytes=size)
        remote_path=run.output_dir+'/input.zip'
        if getattr(run,'config',{}).get('execution_backend')=='k8s':
            remote_path=cached.get('upload_path')
            suffix=str(PurePosixPath(run.output_dir).relative_to('/adb-ssd-mount/ai'))+'/input.zip'
            if not isinstance(remote_path,str) or not remote_path.startswith('/') or not remote_path.endswith('/'+suffix) or any(p in ('.','..') for p in remote_path.split('/')) or any(c in remote_path for c in ('\0','\r','\n','\\')):
                raise HTTPException(502,'集群返回的冻结包上传路径无效，禁止上传')
        await asyncio.to_thread(bridge,'platform_training' if conn else run.server['name'],
            dict(connection=conn,upload=dict(localPath=str(local),remotePath=remote_path)))
        await ensure_starting(db,run)
        await startup_progress(db,run,'unpack','传输完成，正在远端校验SHA256并写入缓存、独立解包及逐张核验')
        await prepare_snapshot(db,run)


async def release(db,run):
    await db.execute(delete(TrainingGpuHold).where(TrainingGpuHold.run_id==run.id))


def reserved_gpus(run):
    if run.config.get('kind')=='model_export' or run.config.get('execution_backend')=='k8s':return []
    return sorted(set(run.config['gpu_uuids']+run.config.get('onnx_stage',{}).get('config',{}).get('gpu_uuids',[])))


async def launch(db,run,user):
    run=await scoped(db,run.id,user.tenant_id,lock=True)
    if run.status!='draft':raise HTTPException(409,'任务已提交；请刷新状态，不要重复启动')
    if run.config.get('execution_backend')!='k8s' and run.server['id']!='default':
        server=await gpu_servers.scoped_server(db,UUID(run.server['id']),user.tenant_id,lock=True)
        if not server.enabled or not gpu_servers.matches_connection(server,run.server):raise HTTPException(409,'训练服务器已变化，请按新连接创建任务')
    from services.gpu_reservations import assert_free
    await assert_free(db,reserved_gpus(run))
    # Unique GPU UUID keys serialize competing starts, including different tenants/connections.
    for gpu in reserved_gpus(run):db.add(TrainingGpuHold(gpu_uuid=gpu,run_id=run.id))
    run.status='starting';run.error=None
    steps=['precheck','stage']+(['archive','upload','unpack'] if run.source['kind']=='snapshot' else [])+(
        ['checkpoint'] if any(run.source.get(key) for key in ('checkpoint_artifact','merge_inputs','repository_model_id')) else [])+['container']
    now=datetime.now(timezone.utc).isoformat()
    run.inspection={**run.inspection,'startup':dict(state='queued',phase='queued',steps=steps,submitted_at=now,updated_at=now,
        events=[dict(phase='queued',message='启动已提交，等待后台准备',time=now)])}
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'GPU已被其他平台训练任务占用') from None
    await db.refresh(run)
    return run


async def prepare_start(db,run):
    try:
        await ensure_starting(db,run)
        await startup_progress(db,run,'precheck','正在重新检查镜像、数据、权重和GPU')
        checked=await check(db,run)
        if not checked['ok']:
            run.status='draft';run.error='预检查未通过，请查看检查结果；未启动容器';await release(db,run);await db.commit()
            await startup_progress(db,run,'precheck',run.error,state='failed')
            raise HTTPException(409,'预检查未通过，请查看检查结果；未启动容器')
        await ensure_starting(db,run)
        await startup_progress(db,run,'stage','预检查通过，正在准备独立任务目录和配置')
        await remote(db,run,'stage',script=build_script(run.config))
        await ensure_starting(db,run)
        if run.source.get('checkpoint_artifact'):
            from services.model_output_pipeline import transfer_checkpoint
            await startup_progress(db,run,'checkpoint','正在下载训练权重并上传到 ONNX 导出服务器')
            await transfer_checkpoint(db,run)
        if run.source.get('merge_inputs'):
            from services.model_output_pipeline import transfer_merge_inputs
            await startup_progress(db,run,'checkpoint','正在归档模型并准备合并服务器的基础模型和 LoRA')
            await transfer_merge_inputs(db,run)
        if run.source.get('repository_model_id'):
            from services import model_repository
            from services.model_output_pipeline import transfer_repository_input
            row=await model_repository.scoped(db,UUID(run.source['repository_model_id']),run.tenant_id)
            if row.status!='ready':raise HTTPException(409,'模型仓库输入不可用')
            await startup_progress(db,run,'checkpoint','正在准备训练服务器的基础模型')
            await transfer_repository_input(db,run,row,'base')
        await transfer_snapshot(db,run)
        await ensure_starting(db,run)
        await startup_progress(db,run,'container','输入准备完成，正在复核资源并创建、启动独立容器')
        result=await remote(db,run,'start')
        await db.refresh(run)
        if run.status!='starting':return run
        if result['status']=='blocked':
            run.status='draft';run.inspection={**run.inspection,**result['inspection']};run.error='启动前资源复核未通过，请查看预检查结果';await release(db,run)
        else:run.status=result['status']
        if run.status=='canceled':await release(db,run)
        run.inspection={**run.inspection,'launched_image_digest':result.get('image_digest')}
        await db.commit()
        state='complete' if run.status=='running' else 'canceled' if run.status=='canceled' else 'failed'
        await startup_progress(db,run,'complete' if state=='complete' else 'container',
            '容器已启动，可切换训练日志查看运行情况' if state=='complete' else run.error or '启动已停止，请核对任务',state=state)
    except HTTPException as error:
        await db.refresh(run)
        if run.status=='starting':
            run.status='unknown';run.error=str(error.detail);await db.commit()
            await startup_progress(db,run,run.inspection.get('startup',{}).get('phase','precheck'),run.error,state='failed')
        raise
    except Exception:
        await db.rollback();await db.refresh(run)
        if run.status=='starting':
            run.status='unknown';run.error='准备或执行异常，请核对容器后停止释放占用';await db.commit()
            await startup_progress(db,run,run.inspection.get('startup',{}).get('phase','precheck'),run.error,state='failed')
        raise HTTPException(503,'训练准备或执行异常；请核对任务，GPU占用暂时保留') from None
    await db.refresh(run)
    return run


async def refresh(db,run):
    if run.status not in ACTIVE:return run
    if run.status=='starting' and run.inspection.get('startup',{}).get('state') in ('queued','running'):return run
    advance_output=False
    try:
        result=await remote(db,run,'status')
        await db.refresh(run)
        if run.status not in ACTIVE:return run
        if run.status=='starting' and run.inspection.get('startup',{}).get('state') in ('queued','running'):return run
        # A stop request wins over an older monitor response.
        if run.status=='stopping' and result['status']=='running':return run
        status=result['status']
        run.status=status if status not in ('missing','created') else 'unknown'
        startup=run.inspection.get('startup',{})
        if status in ('missing','created') and run.config.get('execution_backend')=='k8s':
            events=startup.get('events',[])
            reason=None
            if startup.get('state') in ('failed','interrupted'):
                reason=(events[-1].get('message') if events else None) or run.error
            run.error=reason or result.get('error') or '训练Job/Pod不存在或尚未启动，请点击停止核对'
        else:
            run.error=result.get('error') or ('容器不存在或尚未启动，请点击停止核对并释放占用' if status in ('missing','created') else None)
        if run.status in ('completed','failed','canceled'):await release(db,run)
        run.inspection={**run.inspection,'runtime':result};await db.commit()
        if run.status=='completed' and run.kind=='model_merge':
            from services.model_repository import schedule_automatic
            schedule_automatic(run,'merged/config.json')
        if run.status=='completed' and run.kind in ('swift_sft','wemm'):
            from pathlib import PurePosixPath
            from services.model_repository import schedule_automatic
            entry='adapter_config.json' if run.config['parameters'].get('tuner_type')=='lora' else 'config.json'
            paths=[file['path'] for file in result.get('artifacts',{}).get('files',[])
                if PurePosixPath(file['path']).name==entry]
            if paths:
                def checkpoint_order(path):
                    folder=PurePosixPath(path).parent.name
                    step=folder.removeprefix('checkpoint-')
                    return (int(step) if step.isdecimal() else -1,path)
                schedule_automatic(run,max(paths,key=checkpoint_order))
        if run.status=='completed' and run.kind=='model_export':
            from services.model_output import training_checkpoint
            from services.model_repository import schedule_automatic
            for artifact in result.get('artifacts',{}).get('files',[]):
                path=artifact['path']
                if path.lower().endswith(('.onnx','.engine','.plan')):
                    schedule_automatic(run,path)
            if run.source.get('run_id') and run.source.get('source_path'):
                parent=await db.scalar(select(TrainingRun).where(
                    TrainingRun.id==UUID(run.source['run_id']),TrainingRun.tenant_id==run.tenant_id))
                if parent and (checkpoint:=training_checkpoint(parent,run.source['source_path'])):
                    schedule_automatic(parent,checkpoint,run.user_id)
        if run.status=='running' and run.inspection.get('startup',{}).get('state')=='interrupted':
            await startup_progress(db,run,'complete','服务重启后已核对容器运行中',state='complete')
        advance_output=result.get('advance_output',False)
    except HTTPException as error:
        await db.refresh(run)
        if run.status=='starting' and run.inspection.get('startup',{}).get('state') in ('queued','running'):return run
        if run.status in ACTIVE:run.status='unknown';run.error=str(error.detail);await db.commit()
    await db.refresh(run)
    if advance_output and run.status in ACTIVE and run.status!='stopping':
        from services import model_output_pipeline
        model_output_pipeline.schedule(run)
    return run


async def stop(db,run):
    run=await scoped(db,run.id,run.tenant_id,lock=True)
    if run.status=='draft':run.status='canceled';await db.commit();await db.refresh(run);return run
    if run.status not in ACTIVE:return run
    if run.status=='starting' and run.inspection.get('startup',{}).get('state')=='queued':
        run.status='canceled';run.error=None;await release(db,run);await db.commit()
        await startup_progress(db,run,'queued','已取消排队，未启动远端准备',state='canceled')
        return run
    run.status='stopping';await db.commit()
    try:
        result=await remote(db,run,'stop')
        if result['status']!='canceled':raise HTTPException(503,'未确认容器停止')
        await db.refresh(run);run.status='canceled';run.error=None;await release(db,run);await db.commit()
        if run.inspection.get('startup',{}).get('state') in ('queued','running'):
            await startup_progress(db,run,run.inspection['startup']['phase'],'管理员已停止准备',state='canceled')
    except HTTPException as error:
        await db.refresh(run);run.status='unknown';run.error=str(error.detail);await db.commit();raise
    await db.refresh(run)
    return run


async def monitor():
    while True:
        try:
            async with AsyncSessionLocal() as db:
                ids=(await db.scalars(select(TrainingRun.id).where(TrainingRun.status.in_(ACTIVE)).order_by(TrainingRun.updated_at).limit(20))).all()
            for id in ids:
                async with AsyncSessionLocal() as db:
                    run=await db.get(TrainingRun,id)
                    if run and run.status=='starting' and run.inspection.get('startup',{}).get('state') in ('queued','running'):continue
                    if run and run.status in ('starting','stopping'):
                        age=(datetime.now(timezone.utc).replace(tzinfo=None)-run.updated_at.replace(tzinfo=None)).total_seconds()
                        if age<360:continue
                    if run and run.status in ACTIVE:await refresh(db,run)
        except asyncio.CancelledError:raise
        except Exception:logger.warning('Training status reconciliation failed; holds retained')
        await asyncio.sleep(15)


async def start_worker():
    global _worker
    if _worker is None:
        await recover_startups()
        _worker=asyncio.create_task(workers())


async def stop_worker():
    global _worker
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
        _worker=None


async def process_start(id,sessions=None):
    async with (sessions or AsyncSessionLocal)() as db:
        run=await db.scalar(select(TrainingRun).where(TrainingRun.id==id).with_for_update())
        if run is None or run.status!='starting' or run.inspection.get('startup',{}).get('state')!='queued':return
        run.inspection={**run.inspection,'startup':{**run.inspection['startup'],'state':'running'}}
        await db.commit()
        try:await prepare_start(db,run)
        except HTTPException:logger.warning('Background training preparation stopped: %s',id)


async def recover_startups(sessions=None):
    async with (sessions or AsyncSessionLocal)() as db:
        runs=(await db.scalars(select(TrainingRun).where(TrainingRun.status=='starting'))).all()
        for run in runs:
            if run.inspection.get('startup',{}).get('state')=='running':
                run.status='unknown';run.error='后台服务重启，启动准备中断；请核对容器后停止，GPU占用保留，不会自动重复启动';await db.commit()
                await startup_progress(db,run,run.inspection['startup']['phase'],run.error,state='interrupted')


async def preparation_worker():
    try:
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    runs=(await db.scalars(select(TrainingRun).where(TrainingRun.status=='starting'))).all()
                    for run in runs:
                        if run.id not in _startups and run.inspection.get('startup',{}).get('state')=='queued':
                            task=asyncio.create_task(process_start(run.id));_startups[run.id]=task
                            task.add_done_callback(lambda done,id=run.id:startup_finished(id,done))
            except asyncio.CancelledError:raise
            except Exception:logger.warning('Training startup queue unavailable; retrying')
            await asyncio.sleep(2)
    finally:
        for task in list(_startups.values()):task.cancel()
        await asyncio.gather(*list(_startups.values()),return_exceptions=True)
        _startups.clear()


def startup_finished(id,task):
    _startups.pop(id,None)
    if not task.cancelled() and task.exception():logger.error('Training startup worker failed: %s',id)


async def workers():
    # shortcut: 单后端进程；部署多实例前须以数据库租约替代进程内执行记录。
    async with asyncio.TaskGroup() as group:
        group.create_task(monitor());group.create_task(preparation_worker())
