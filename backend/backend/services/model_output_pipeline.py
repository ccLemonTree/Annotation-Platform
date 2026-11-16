"""Two-host export orchestration using the existing durable run and SSH bridge."""
import asyncio
import hashlib
import tempfile
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from weakref import WeakValueDictionary
from fastapi import HTTPException
from models import User
from services import training_runs
from services.training_config import build_script

_locks=WeakValueDictionary()
_advancing={}
_model_transfers=WeakValueDictionary()


async def merge_parent(db,run):
    from uuid import UUID
    return await training_runs.scoped(db,UUID(run.source['run_id']),run.tenant_id)


async def transfer_merge_inputs(db,run):
    from services import model_repository as repository
    parent=await merge_parent(db,run)
    for kind,path in run.source['merge_inputs'].items():
        key=(parent.id,path)
        lock=_model_transfers.setdefault(key,asyncio.Lock())
        async with lock:
            if kind=='base' and parent.source.get('repository_model_id'):
                from uuid import UUID
                model_id=UUID(parent.source['repository_model_id'])
            else:
                task=repository.schedule_automatic(parent,path,run.user_id)
                model_id=await asyncio.shield(task) if task else None
            if not model_id:raise HTTPException(409,'模型未能加入仓库，请检查来源文件')
            task=repository._tasks.get(str(model_id))
            if task:await asyncio.shield(task)
            row=await repository.scoped(db,model_id,run.tenant_id)
            if row.status!='ready':raise HTTPException(409,row.error or '模型尚未入库，请在模型仓库重试')
        await transfer_repository_input(db,run,row,kind)


async def transfer_repository_input(db,run,row,kind):
    from pathlib import PurePosixPath
    await training_runs.ensure_starting(db,run)
    prefix=str(PurePosixPath(row.entry_file).parent)
    prefix='' if prefix=='.' else prefix+'/'
    files=[{**item,'path':item['path'].removeprefix(prefix)} for item in row.files]
    files.sort(key=lambda item:item['path'])
    digest=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    options=dict(tenant_id=run.tenant_id,input_kind=kind,files=files,digest=digest)
    key=(run.server['id'],run.tenant_id,digest)
    lock=_model_transfers.setdefault(key,asyncio.Lock())
    async with lock:
        await upload_repository_input(db,run,row,prefix,options)


async def upload_repository_input(db,run,row,prefix,options):
    from services import model_repository as repository
    kind,files=options['input_kind'],options['files']
    await training_runs.ensure_starting(db,run)
    receipt=await training_runs.remote_direct(db,run,'merge_cache_probe',**options)
    if receipt.get('hit'):return
    upload_path=receipt.get('upload_path','')
    if not upload_path.startswith('/') or not upload_path.endswith('/'+str(run.id)+'/'+kind+'.zip') or '..' in upload_path.split('/'):
        raise HTTPException(502,'模型缓存上传路径无效')
    if run.config.get('execution_backend')!='k8s' and upload_path!=run.output_dir+'/'+kind+'.zip':
        raise HTTPException(502,'模型缓存上传路径与任务目录不符')
    root=repository.folder(row)/'files'
    with tempfile.TemporaryDirectory(prefix='merge-',dir=root.parent) as temp:
        archive=Path(temp)/'model.zip'
        repository.check_space(root,row.file_size)
        def pack():
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_STORED) as bundle:
                for item in files:
                    file=root/(prefix+item['path'])
                    if file.resolve()!=file or not file.is_file():raise ValueError('仓库模型文件无效')
                    bundle.write(file,item['path'])
        packing=asyncio.create_task(asyncio.to_thread(pack))
        try:await asyncio.shield(packing)
        except asyncio.CancelledError:
            await packing
            raise
        await training_runs.ensure_starting(db,run)
        conn=await training_runs.connection(db,run)
        upload=asyncio.create_task(asyncio.to_thread(training_runs.bridge,'platform_training' if conn else run.server['name'],
            dict(connection=conn,upload=dict(localPath=str(archive),remotePath=upload_path))))
        try:await asyncio.shield(upload)
        except asyncio.CancelledError:
            await upload
            raise
    await training_runs.ensure_starting(db,run)
    await training_runs.remote_direct(db,run,'merge_cache_publish',**options)


def stages(run):
    source=run.config['onnx_stage']
    def view(id,server,config):
        return SimpleNamespace(id=id,tenant_id=run.tenant_id,server=server,config=config,
            output_dir=config['output_root']+'/'+str(id))
    return (view(source['id'],source['server'],source['config']),
        view(run.id,run.server,{key:value for key,value in run.config.items() if key!='onnx_stage'}))


async def phase(db,run,value,**details):
    await db.refresh(run)
    run.inspection={**run.inspection,'output_phase':value,**details}
    await db.commit()


async def allowed(db,run):
    await db.refresh(run)
    actor=await db.get(User,run.user_id,populate_existing=True)
    return run.status in ('starting','running','unknown') and actor is not None and actor.is_active and actor.role=='admin'


async def checkpoint_source(db,run):
    from uuid import UUID
    parent=await training_runs.scoped(db,UUID(run.source['run_id']),run.tenant_id)
    return parent,await training_runs.artifact_metadata(db,parent,run.source['checkpoint_artifact'])


async def transfer_checkpoint(db,run):
    parent,metadata=await checkpoint_source(db,run)
    target=stages(run)[0] if run.config.get('onnx_stage') else run
    folder=training_runs.ROOT/'.backups/model-output-transfer'
    folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='checkpoint-',dir=folder) as temp:
        local=Path(temp)/'checkpoint'
        await training_runs.fetch_artifact(db,parent,metadata,local)
        await training_runs.ensure_starting(db,run)
        conn=await training_runs.connection(db,target)
        task=asyncio.create_task(asyncio.to_thread(training_runs.bridge,'platform_training' if conn else target.server['name'],
            dict(connection=conn,upload=dict(localPath=str(local),remotePath=target.config['model_path']+'.part'))))
        try:await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    await training_runs.ensure_starting(db,run)
    await training_runs.remote_direct(db,target,'receive_checkpoint',sha256=metadata['sha256'],size=metadata['bytes'])


async def transfer(db,run,source,target):
    receipt=await training_runs.remote_direct(db,source,'pack_onnx')
    if (receipt.get('path')!=source.output_dir+'/onnx.zip' or not 0<receipt.get('bytes',0)<=40*1024**3
            or len(receipt.get('sha256',''))!=64):
        raise HTTPException(502,'ONNX传输包元数据无效')
    folder=training_runs.ROOT/'.backups/model-output-transfer';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='onnx-',dir=folder) as temp:
        local=Path(temp)/'input.zip'
        conn=await training_runs.connection(db,source)
        await asyncio.to_thread(training_runs.bridge,'platform_training' if conn else source.server['name'],
            dict(connection=conn,download=dict(remotePath=receipt['path'],localPath=str(local),bytes=receipt['bytes'])))
        def verify():
            digest=hashlib.sha256()
            with local.open('rb') as inp:
                for chunk in iter(lambda:inp.read(1024**2),b''):digest.update(chunk)
            return local.stat().st_size==receipt['bytes'] and digest.hexdigest()==receipt['sha256']
        if not await asyncio.to_thread(verify):raise HTTPException(409,'ONNX下载校验失败，未启动TensorRT构建')
        if not await allowed(db,run):return False
        conn=await training_runs.connection(db,target)
        await asyncio.to_thread(training_runs.bridge,'platform_training' if conn else target.server['name'],
            dict(connection=conn,upload=dict(localPath=str(local),remotePath=target.output_dir+'/input.zip')))
        if not await allowed(db,run):return False
        await training_runs.remote_direct(db,target,'receive_onnx',sha256=receipt['sha256'])
    await phase(db,run,'transferring',onnx_transfer=dict(bytes=receipt['bytes'],sha256=receipt['sha256']))
    return True


async def advance(id,tenant):
    try:
        async with training_runs.AsyncSessionLocal() as db:
            run=await training_runs.scoped(db,id,tenant)
            source,target=stages(run)
            if not await allowed(db,run):return
            if not await transfer(db,run,source,target):return
            if not await allowed(db,run):return
            await phase(db,run,'tensorrt')
            result=await training_runs.remote_direct(db,target,'start')
            await db.refresh(run)
            if run.status not in training_runs.ACTIVE or run.status=='stopping':return
            if result['status']=='blocked':
                run.status='unknown';run.error='目标服务器预检查未通过，请查看GPU和导出镜像'
                run.inspection={**run.inspection,'output_phase':'transferring','target_checks':result.get('inspection')}
            else:
                run.status=result['status'];run.error=None
                run.inspection={**run.inspection,'launched_image_digest':result.get('image_digest')}
            await db.commit()
    except Exception as error:
        async with training_runs.AsyncSessionLocal() as db:
            run=await training_runs.scoped(db,id,tenant)
            if run.status in training_runs.ACTIVE and run.status!='stopping':
                run.status='unknown';run.error=str(error.detail) if isinstance(error,HTTPException) else 'ONNX传输或TensorRT启动异常，请核对任务后重试状态或停止'
                await db.commit()
    finally:_advancing.pop(str(id),None)


def schedule(run):
    # File transfer must not hold up HTTP status requests or other jobs' monitoring.
    if str(run.id) not in _advancing:_advancing[str(run.id)]=asyncio.create_task(advance(run.id,run.tenant_id))


async def remote(db,run,action,**values):
    source,target=stages(run)
    call=training_runs.remote_direct
    if action=='precheck':
        first=await call(db,source,'precheck',**({'pending_checkpoint':True} if run.source.get('checkpoint_artifact') else {}))
        second=await call(db,target,'precheck',pending_onnx=True)
        checks=[{**row,'name':prefix+' · '+row['name']} for prefix,result in
            [('ONNX / '+source.server['name'],first),('TensorRT / '+target.server['name'],second)] for row in result['checks']]
        return dict(ok=first['ok'] and second['ok'],checks=checks)
    if action=='logs':
        first=await call(db,source,'logs');second=await call(db,target,'logs')
        return dict(text='[ONNX · '+source.server['name']+']\n'+first['text']+'\n[TensorRT · '+target.server['name']+']\n'+second['text'])
    if action=='stop':
        results=await asyncio.gather(call(db,source,'stop'),call(db,target,'stop'),return_exceptions=True)
        if any(isinstance(row,BaseException) or row.get('status')!='canceled' for row in results):raise HTTPException(503,'尚未确认两阶段容器停止，保留GPU占用')
        return dict(status='canceled')
    if action=='status' and str(run.id) in _advancing:return dict(status='running')
    if action not in ('stage','start','status','stop'):return await call(db,target,action,**values)
    lock=_locks.setdefault(str(run.id),asyncio.Lock())
    async with lock:
        await db.refresh(run)
        if action=='stage':
            await call(db,source,'stage',script=build_script(source.config))
            return await call(db,target,'stage',script=build_script(target.config))
        if action=='start':
            if not await allowed(db,run):return dict(status='canceled')
            result=await call(db,source,'start')
            await phase(db,run,'onnx',onnx_image_digest=result.get('image_digest'))
            return result
        current=run.inspection.get('output_phase','onnx')
        if current=='onnx':
            result=await call(db,source,'status')
            if result['status']!='completed':
                if result['status']=='failed':result={**result,'error':'ONNX导出失败：'+result.get('error','请查看来源服务器日志')}
                return result
            await phase(db,run,'transferring')
            current='transferring'
        if current=='transferring':
            return dict(status='running',advance_output=await allowed(db,run))
        result=await call(db,target,'status')
        if result['status']=='missing' and await allowed(db,run):
            # Recover a restart between persisting the phase and submitting the target container.
            result=await call(db,target,'start')
            if result['status']=='blocked':return dict(status='unknown',error='TensorRT目标服务器预检查未通过')
        return result
