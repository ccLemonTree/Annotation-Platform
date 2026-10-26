"""Two-host export orchestration using the existing durable run and SSH bridge."""
import asyncio
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from weakref import WeakValueDictionary
from fastapi import HTTPException
from models import User
from services import training_runs
from services.training_config import build_script

_locks=WeakValueDictionary()
_advancing={}


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
        first=await call(db,source,'precheck')
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
