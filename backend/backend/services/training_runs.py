"""Durable run lifecycle; only explicit start can launch a training container."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime,timezone
from uuid import UUID,uuid4
from types import SimpleNamespace
from sqlalchemy import select,delete
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from config.settings import settings
from database import AsyncSessionLocal
from models import ExportSnapshot,Project,User
from models.training_run import TrainingRun,TrainingGpuHold
from services import gpu_servers
from services.storage import get_storage
from services.training_config import effective_config,validate_snapshot,build_script,PARAMETERS
from schemas.training_run import default_output_root

ROOT=Path(__file__).resolve().parents[1]
ACTIVE=('starting','running','stopping','unknown')
_worker=None
_startups={}
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


async def server_snapshot(db,id,tenant_id):
    if id=='default':return dict(id='default',name=settings.GPU_SERVER_NAME,revision=0)
    try:server_id=UUID(id)
    except ValueError:raise HTTPException(400,'训练服务器ID格式无效') from None
    server=await gpu_servers.scoped_server(db,server_id,tenant_id)
    if not server.enabled or not server.password_encrypted:raise HTTPException(409,'训练服务器已停用或未保存密码')
    return dict(id=str(server.id),name=server.name,revision=server.revision)


async def connection(db,run):
    if run.server['id']=='default':
        if run.server['name']!=settings.GPU_SERVER_NAME:raise HTTPException(409,'默认服务器配置已变化，请恢复原连接后核对任务')
        return None
    row=await gpu_servers.scoped_server(db,UUID(run.server['id']),run.tenant_id)
    if row.revision!=run.server['revision']:raise HTTPException(409,'服务器连接修订已变化，不能操作旧任务')
    return dict(host=row.host,port=row.port,username=row.username,password=gpu_servers.decrypt_password(row.password_encrypted))


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


async def remote_direct(db,run,action,**values):
    conn=await connection(db,run)
    payload=dict(action=action,run_id=str(run.id),config=run.config,**values)
    if run.config.get('kind') in ('model_export','model_merge'):
        if action in ('precheck','start'):payload['output_code']=(ROOT/'services/model_output_runner.py').read_text(encoding='utf-8')
    elif action in ('dataset','precheck','start') and run.config.get('kind') not in ('swift_sft','wemm'):
        payload['dataset_code']=(ROOT/'services/training_dataset.py').read_text(encoding='utf-8')
    return await asyncio.to_thread(bridge,'platform_training' if conn else run.server['name'],dict(connection=conn,payload=payload))


async def defaults(db,user,server_id,kind):
    if kind not in PARAMETERS:raise HTTPException(400,'未知训练器')
    await server_snapshot(db,server_id,user.tenant_id)
    run=await db.scalar(select(TrainingRun).where(TrainingRun.tenant_id==user.tenant_id,TrainingRun.kind==kind,
        TrainingRun.server['id'].as_string()==server_id).order_by(TrainingRun.created_at.desc(),TrainingRun.id.desc()).limit(1))
    return dict(model_path=run.config['model_path'] if run else '',output_root=default_output_root())


async def inspect_dataset(db,user,request):
    server=await server_snapshot(db,request.server_id,user.tenant_id)
    run=SimpleNamespace(id=uuid4(),tenant_id=user.tenant_id,server=server,config=request.model_dump())
    return await remote(db,run,'dataset')


async def configuration(db,user,request):
    config=effective_config(request);source=dict(kind='remote',path=request.dataset_path)
    if request.snapshot_id:
        snapshot=await db.scalar(select(ExportSnapshot).join(Project).where(ExportSnapshot.id==request.snapshot_id,Project.tenant_id==user.tenant_id).with_for_update(of=ExportSnapshot))
        if snapshot is None:raise HTTPException(404,'冻结导出不存在或不属于当前租户')
        validate_snapshot(request.kind,snapshot.export_format,snapshot.manifest,request.parameters)
        if request.selected_labels and set(request.selected_labels)-set(snapshot.manifest.get('labels',[])):
            raise HTTPException(400,'所选训练标签不在冻结数据集中')
        source=dict(kind='snapshot',id=str(snapshot.id),blob_path=snapshot.blob_path,sha256=snapshot.content_hash,
            format=snapshot.export_format,project_id=str(snapshot.project_id),labels=snapshot.manifest.get('labels',[]))
        config['runtime_dataset']=('/output/dataset/train.jsonl' if request.kind in ('swift_sft','wemm') else
            '/output/dataset/data.yaml' if request.kind=='yolo26' else '/output/dataset/rfdetr')
    server=await server_snapshot(db,request.server_id,user.tenant_id)
    return config,source,server


async def create(db,user,request):
    config,source,server=await configuration(db,user,request)
    id=uuid4()
    run=TrainingRun(id=id,tenant_id=user.tenant_id,user_id=user.id,name=request.name,kind=request.kind,
        config=config,source=source,server=server,output_dir=request.output_root+'/'+str(id),status='draft',inspection={})
    db.add(run);await db.commit();await db.refresh(run)
    return run


async def update(db,user,id,request):
    run=await scoped(db,id,user.tenant_id,lock=True)
    if run.kind in ('model_export','model_merge'):raise HTTPException(409,'导出/合并任务请从来源训练任务新建，不能改为训练任务')
    if run.status!='draft':raise HTTPException(409,'仅待启动任务可以修改配置；已提交任务请创建新任务')
    config,source,server=await configuration(db,user,request)
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
    await db.delete(run);await db.commit()


async def download_artifact(db,run,path):
    from pathlib import PurePosixPath
    from starlette.responses import FileResponse
    if not path or len(path)>1000 or '\\' in path or PurePosixPath(path).is_absolute() or any(p in ('','.', '..') for p in path.split('/')):
        raise HTTPException(422,'产物相对路径无效')
    metadata=await remote(db,run,'artifact',path=path)
    expected=str(PurePosixPath(run.output_dir)/run.config['output_subdir']/path)
    if metadata.get('path')!=expected or metadata.get('bytes',-1)<0 or len(metadata.get('sha256',''))!=64:
        raise HTTPException(502,'产物元数据无效')
    folder=ROOT/'.backups/artifact-downloads';folder.mkdir(parents=True,exist_ok=True)
    temp=tempfile.TemporaryDirectory(prefix='artifact-',dir=folder);local=Path(temp.name)/'download'
    try:
        conn=await connection(db,run)
        await asyncio.to_thread(bridge,'platform_training' if conn else run.server['name'],
            dict(connection=conn,download=dict(remotePath=metadata['path'],localPath=str(local),bytes=metadata['bytes'])))
        def verify():
            digest=hashlib.sha256()
            with local.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024**2),b''):digest.update(chunk)
            return local.stat().st_size==metadata['bytes'] and digest.hexdigest()==metadata['sha256']
        if not await asyncio.to_thread(verify):raise HTTPException(409,'产物在下载时发生变化或传输不完整，请重试')
    except BaseException:
        temp.cleanup();raise
    class DownloadResponse(FileResponse):
        async def __call__(self,*args,**kwargs):
            try:await super().__call__(*args,**kwargs)
            finally:await asyncio.to_thread(temp.cleanup)
    return DownloadResponse(local,filename=PurePosixPath(path).name,media_type='application/octet-stream')


async def check(db,run):
    run=await scoped(db,run.id,run.tenant_id,lock=True)
    result=await remote(db,run,'precheck')
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
    await startup_progress(db,run,'archive','正在校验并暂存平台冻结包')
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
        await asyncio.to_thread(bridge,'platform_training' if conn else run.server['name'],
            dict(connection=conn,upload=dict(localPath=str(local),remotePath=run.output_dir+'/input.zip')))
        await ensure_starting(db,run)
        await startup_progress(db,run,'unpack','传输完成，正在远端校验SHA256、解包及逐张核验')
        await remote(db,run,'unpack',snapshot_hash=run.source['sha256'])


async def release(db,run):
    await db.execute(delete(TrainingGpuHold).where(TrainingGpuHold.run_id==run.id))


def reserved_gpus(run):
    return sorted(set(run.config['gpu_uuids']+run.config.get('onnx_stage',{}).get('config',{}).get('gpu_uuids',[])))


async def launch(db,run,user):
    run=await scoped(db,run.id,user.tenant_id,lock=True)
    if run.status!='draft':raise HTTPException(409,'任务已提交；请刷新状态，不要重复启动')
    if run.server['id']!='default':
        server=await gpu_servers.scoped_server(db,UUID(run.server['id']),user.tenant_id,lock=True)
        if not server.enabled or server.revision!=run.server['revision']:raise HTTPException(409,'训练服务器已变化，请按新连接创建任务')
    from services.gpu_reservations import assert_free
    await assert_free(db,reserved_gpus(run))
    # Unique GPU UUID keys serialize competing starts, including different tenants/connections.
    for gpu in reserved_gpus(run):db.add(TrainingGpuHold(gpu_uuid=gpu,run_id=run.id))
    run.status='starting';run.error=None
    steps=['precheck','stage']+(['archive','upload','unpack'] if run.source['kind']=='snapshot' else [])+['container']
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
        # A stop request wins over an older monitor response.
        if run.status=='stopping' and result['status']=='running':return run
        status=result['status']
        run.status=status if status not in ('missing','created') else 'unknown'
        run.error=result.get('error') or ('容器不存在或尚未启动，请点击停止核对并释放占用' if status in ('missing','created') else None)
        if run.status in ('completed','failed','canceled'):await release(db,run)
        run.inspection={**run.inspection,'runtime':result};await db.commit()
        if run.status=='running' and run.inspection.get('startup',{}).get('state')=='interrupted':
            await startup_progress(db,run,'complete','服务重启后已核对容器运行中',state='complete')
        advance_output=result.get('advance_output',False)
    except HTTPException as error:
        await db.refresh(run)
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
