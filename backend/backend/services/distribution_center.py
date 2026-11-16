"""Persistent local model versions, explicit targets, and per-target batch results."""
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
from uuid import UUID,uuid4
import zipfile
from fastapi import HTTPException
from sqlalchemy import select,delete
from sqlalchemy.exc import IntegrityError
from config.settings import settings
from database import AsyncSessionLocal
from models.model_deployment import ModelRelease,DeploymentTarget,DeploymentLease,ModelDeployment
from models.training_run import TrainingRun
from services import training_runs,model_deployments as deployments
from services.model_deployment_executor import relative,sha

_archives={}
_archive_slots=asyncio.Semaphore(1)


def verify_bundle(path):
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        if len(infos)>20001 or len({i.filename for i in infos})!=len(infos):raise ValueError('归档清单过大或包含重复文件')
        for info in infos:
            relative(info.filename)
            if info.is_dir() or info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16):raise ValueError('归档文件类型无效')
        if archive.getinfo('bundle.json').file_size>8*1024**2:raise ValueError('模型清单过大')
        manifest=json.loads(archive.read('bundle.json'))
        rows=manifest['files']
        if not rows or len({r['path'] for r in rows})!=len(rows) or {r['path'] for r in rows}|{'bundle.json'}!={i.filename for i in infos}:raise ValueError('归档清单不一致')
        total=0
        for row in rows:
            relative(row['path']);size=row['bytes'];total+=size
            if not isinstance(size,int) or size<0 or total>512*1024**3 or archive.getinfo(row['path']).file_size!=size:raise ValueError('模型文件大小无效')
            digest=hashlib.sha256()
            with archive.open(row['path']) as stream:
                for chunk in iter(lambda:stream.read(1024**2),b''):digest.update(chunk)
            if digest.hexdigest()!=row['sha256']:raise ValueError('本地归档逐文件SHA256校验失败')
        if total!=manifest['bytes'] or not total:raise ValueError('归档总大小不一致')
    return {k:v for k,v in manifest.items() if k!='files'}|{'file_count':len(rows)}


async def scoped(db,model,id,tenant,lock=False):
    query=select(model).where(model.id==id,model.tenant_id==tenant)
    if lock:query=query.with_for_update()
    row=await db.scalar(query.execution_options(populate_existing=True))
    if row is None:raise HTTPException(404,'模型版本或服务器目标不存在')
    return row


def eligible(run,runtime):
    if run.status!='completed':return False
    if runtime=='triton_plan':return run.kind=='model_export' and run.config.get('output',{}).get('format')=='tensorrt'
    kind=run.source.get('model_kind') or run.kind
    return kind in ('swift_sft','wemm') and run.kind in ('model_merge','swift_sft','wemm') and (runtime=='vllm')==(kind=='wemm') and (run.kind=='model_merge' or run.config.get('parameters',{}).get('tuner_type')!='lora')


async def create_release(db,user,request):
    run=await training_runs.scoped(db,request.run_id,user.tenant_id)
    if not eligible(run,request.runtime):raise HTTPException(422,'请选择已完成的TensorRT导出或大模型/向量完整模型产物')
    row=ModelRelease(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,run_id=run.id,name=request.name,version=request.version,
        runtime=request.runtime,status='archiving',source=dict(root=run.output_dir+'/'+run.config['output_subdir'],artifact_path=request.artifact_path,runtime=request.runtime,server=run.server),archive={},logs=[])
    db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'同一模型的版本名已存在，请保留旧记录并使用新版本名') from None
    await db.refresh(row);schedule_archive(row)
    return row


def schedule_archive(row):
    _archives[str(row.id)]=asyncio.create_task(archive_worker(row.id,row.tenant_id))


async def archive_model(db,row):
    await deployments.authorized(db,row)
    receipt=await deployments.remote(db,SimpleNamespace(id=row.id,tenant_id=row.tenant_id,user_id=row.user_id,target={},source=row.source),'pack',source=True)
    expected=str(Path(row.source['root']).parent).replace('\\','/')+'/.model-deployments/'+str(row.id)+'/bundle.zip'
    if receipt.get('path')!=expected or not 0<receipt.get('bytes',0)<=513*1024**3 or len(receipt.get('sha256',''))!=64:raise HTTPException(502,'来源模型包元数据无效')
    folder=Path(settings.MODEL_ARCHIVE_PATH).resolve()/str(row.tenant_id)/str(row.id)
    folder.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(folder).free<receipt['bytes']+64*1024**2:raise HTTPException(409,'平台本地磁盘空间不足以保存模型归档')
    partial=folder/'bundle.part';local=folder/'bundle.zip'
    conn=await training_runs.connection(db,SimpleNamespace(server=row.source['server'],tenant_id=row.tenant_id))
    download=asyncio.create_task(asyncio.to_thread(training_runs.bridge,'platform_training' if conn else row.source['server']['name'],dict(connection=conn,
        download=dict(remotePath=receipt['path'],localPath=str(partial),bytes=receipt['bytes']))))
    try:await asyncio.shield(download)
    except asyncio.CancelledError:
        await download;raise
    if partial.stat().st_size!=receipt['bytes'] or await asyncio.to_thread(sha,partial)!=receipt['sha256']:raise HTTPException(409,'本地模型包SHA256校验失败')
    info=await asyncio.to_thread(verify_bundle,partial)
    await deployments.authorized(db,row)
    partial.replace(local)
    row.archive=dict(path=str(local),bytes=receipt['bytes'],sha256=receipt['sha256'],source_info=info,archived_at=datetime.now(timezone.utc).isoformat())
    row.status='ready';row.error=None;row.logs=[*row.logs,dict(at=datetime.now(timezone.utc).isoformat(),message='模型已保存到平台本地，包和逐文件SHA256校验通过')]
    await db.commit()


async def archive_worker(id,tenant):
    try:
        async with _archive_slots:
            async with AsyncSessionLocal() as db:
                row=await scoped(db,ModelRelease,id,tenant)
                try:await archive_model(db,row)
                except BaseException as error:
                    await db.rollback();row=await scoped(db,ModelRelease,id,tenant)
                    row.status='interrupted' if isinstance(error,asyncio.CancelledError) else 'failed'
                    row.error=str(error.detail) if isinstance(error,HTTPException) else '归档被中断' if isinstance(error,asyncio.CancelledError) else str(error)[-1500:]
                    await db.commit()
                    if isinstance(error,asyncio.CancelledError):raise
    finally:_archives.pop(str(id),None)


async def target_snapshot(target):
    available={s['name']:s for s in await deployments.servers()}
    names={target['control_server'],target.get('file_server',target['control_server'])}
    if not names<=available.keys():raise HTTPException(422,'请选择ssh-manager配置中的服务器')
    return {**target,'connections':{name:available[name] for name in names}}


def target_key(target,kind):
    name=target.get('file_server') if kind=='model' else target['control_server']
    connection=target['connections'][name]
    identity=[connection['host'],connection['port']]
    if kind=='model':identity.extend([target['repository'],target['model_name']])
    elif target['environment']=='k8s':identity.extend([target['namespace'],target['workload_kind'],target['workload'],target['container']])
    else:identity.extend(sorted(target['containers']))
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


async def create_target(db,user,request):
    target=await target_snapshot(request.target)
    row=DeploymentTarget(id=uuid4(),tenant_id=user.tenant_id,name=request.name,kind=request.kind,
        target_key=target_key(target,request.kind),target=target,observation={})
    db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'此模型槽位或镜像服务目标已保存，请使用现有目标') from None
    await db.refresh(row);return row


async def update_target(db,user,id,request):
    row=await scoped(db,DeploymentTarget,id,user.tenant_id,lock=True)
    if await db.scalar(select(ModelDeployment.id).outerjoin(DeploymentLease,DeploymentLease.job_id==ModelDeployment.id).where(ModelDeployment.target_id==id,
        (ModelDeployment.status.in_((*deployments.BUSY,'unknown')))|(DeploymentLease.job_id.is_not(None))).limit(1)):
        raise HTTPException(409,'此目标有正在执行或待核对的操作，请完成核对或恢复后再修改')
    target=await target_snapshot(request.target)
    if row.target!=target or row.kind!=request.kind:row.observation={}
    row.name=request.name;row.kind=request.kind;row.target=target;row.target_key=target_key(target,request.kind)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'相同服务器目标已存在，请使用已有目标') from None
    await db.refresh(row);return row


async def create_batch(db,user,request):
    release=await scoped(db,ModelRelease,request.release_id,user.tenant_id) if request.release_id else None
    model=await repository_model(db,request.repository_id,user.tenant_id) if request.repository_id else None
    if release and release.status!='ready':raise HTTPException(409,'模型归档尚未完成或校验失败')
    batch=uuid4();rows=[]
    for id in request.target_ids:
        target=await scoped(db,DeploymentTarget,id,user.tenant_id)
        if target.kind!=request.kind or release and target.target['runtime']!=release.runtime or model and target.target['runtime']!='triton_plan':raise HTTPException(422,'所选服务器目标与模型/镜像类型不匹配')
        await deployments.validate_connections(SimpleNamespace(target=target.target))
        source=dict(kind='local_release',release_id=str(release.id),name=release.name,version=release.version,runtime=release.runtime) if release else dict(kind='image',image=request.image)
        if model:source=dict(kind='local_repository',repository_id=str(model.id),runtime='triton_plan',**{key:getattr(model,key) for key in REPOSITORY_FIELDS})
        rows.append(ModelDeployment(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,run_id=model.run_id if model else release.run_id if release else None,
            release_id=release.id if release else None,repository_id=model.id if model else None,target_id=target.id,batch_id=batch,kind=request.kind,status='draft',target_key=target.target_key,
            source=source,
            target=target.target,inspection={},logs=[]))
    db.add_all(rows);await db.commit()
    for row in rows:await db.refresh(row)
    return dict(batch_id=batch,items=[deployments.summary(row) for row in rows])


REPOSITORY_FIELDS=('name','version','format','gpu_model','tensorrt_version','batch_profile','entry_file','file_size','files')


async def repository_model(db,id,tenant):
    from services import model_repository as repository
    row=await repository.scoped(db,id,tenant)
    if row.status!='ready':raise HTTPException(409,'仓库模型尚未入库完成')
    if row.format!='tensorrt':raise HTTPException(422,'当前下发入口仅支持TensorRT engine；PT/ONNX请先导出，Python backend请按完整目录部署')
    if not row.gpu_model.strip() or not row.tensorrt_version.strip():raise HTTPException(422,'TensorRT模型缺少构建GPU型号或TensorRT版本，请补齐后使用新版本入库')
    if len(row.files) not in (1,2) or {f['path'] for f in row.files}!={row.entry_file}|({'config.pbtxt'} if len(row.files)==2 else set()) or row.entry_file.rsplit('.',1)[-1].lower() not in ('engine','plan'):
        raise HTTPException(422,'TensorRT更新需要一个engine或plan主文件，可附带config.pbtxt')
    return row


def repository_info(row):
    from services import model_repository as repository
    if repository.validate_files(row.files,row.entry_file)!=row.file_size:raise HTTPException(409,'仓库文件大小清单校验失败')
    root=repository.folder(row)/'files'
    for info in row.files:
        file=root/relative(info['path'])
        if any(p.is_symlink() for p in [file,*file.parents]) or not file.is_file() or file.stat().st_size!=info['bytes'] or sha(file)!=info['sha256']:raise HTTPException(409,'仓库文件SHA256校验失败')
    item=next(f for f in row.files if f['path']==row.entry_file)
    return dict(bytes=item['bytes'],gpu_name=row.gpu_model,versions={'tensorrt_runtime':row.tensorrt_version},batch_profile=row.batch_profile,
        files=[dict(path='1/model.plan',bytes=item['bytes'],sha256=item['sha256'])])


def pack_repository(row,archive):
    from services import model_repository as repository
    info=repository_info(row)
    if shutil.disk_usage(archive.parent).free<row.file_size+64*1024**2:raise HTTPException(409,'平台本地磁盘空间不足以生成临时部署包')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as out:
        out.write(repository.folder(row)/'files'/row.entry_file,'1/model.plan')
        out.writestr('bundle.json',json.dumps(info,ensure_ascii=False))
    verify_bundle(archive)
    return dict(bytes=archive.stat().st_size,sha256=sha(archive))


def resources(row):
    target=row.target;c=target['connections'][target['control_server']]
    base=[c['host'],c['port']]
    values=[base+['k8s',target['namespace'],target['workload_kind'],target['workload']]] if target['environment']=='k8s' else [base+['docker',name] for name in target['containers']]
    if target.get('repository'):
        c=target['connections'][target['file_server']];values.append([c['host'],c['port'],'model',target['repository'],target['model_name']])
    return sorted({hashlib.sha256(json.dumps(value).encode()).hexdigest() for value in values})


async def acquire(db,row):
    for key in resources(row):
        lease=await db.get(DeploymentLease,key)
        if lease and lease.job_id!=row.id:raise HTTPException(409,'此模型目录或服务正在执行其他更新，或仍处于待核对状态')
        if not lease:db.add(DeploymentLease(resource=key,job_id=row.id))
    try:await db.flush()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'其他操作已占用此推理服务，请稍后重试') from None


async def release_lease(db,row):
    await db.execute(delete(DeploymentLease).where(DeploymentLease.job_id==row.id));await db.commit()


async def batch_action(db,user,id,operation):
    if operation not in ('transfer','activate','rollback','refresh'):raise HTTPException(400,'未知批次操作')
    tenant=user.tenant_id;actor=SimpleNamespace(id=user.id)
    ids=(await db.scalars(select(ModelDeployment.id).where(ModelDeployment.tenant_id==tenant,ModelDeployment.batch_id==id).order_by(ModelDeployment.created_at,ModelDeployment.id))).all()
    if not ids:raise HTTPException(404,'批次不存在')
    result=[]
    for row_id in ids:
        row=await deployments.scoped(db,row_id,tenant)
        done={'transfer':('staged','active','rolled_back'),'activate':('active',),'rollback':('rolled_back',)}
        if operation!='refresh' and (row.status in done[operation] or row.status in deployments.BUSY):
            result.append(dict(id=row_id,status=row.status,skipped=True));continue
        try:
            row=await deployments.refresh(db,row) if operation=='refresh' else await deployments.action(db,row,operation,actor)
            result.append(dict(id=row_id,status=row.status))
        except HTTPException as error:
            await db.rollback();result.append(dict(id=row_id,error=str(error.detail)))
    return dict(batch_id=id,results=result)


async def target_summary(db,row):
    result=deployments.summary(row)
    result['current']=row.observation or dict(state='unmanaged')
    latest=await db.scalar(select(ModelDeployment).where(ModelDeployment.tenant_id==row.tenant_id,ModelDeployment.target_key==row.target_key,ModelDeployment.kind==row.kind).order_by(ModelDeployment.updated_at.desc(),ModelDeployment.id.desc()).limit(1))
    if latest:result['latest']=deployments.summary(latest)
    if latest and (latest.status in ('activating','rolling_back','activation_failed','rollback_failed') or latest.status=='unknown' and (latest.inspection.get('promotion') or latest.inspection.get('apply_started'))):
        result['current']={**result['current'],'state':'unknown'}
    return result


async def observe(db,row):
    if row.target_id is None:return
    target=await scoped(db,DeploymentTarget,row.target_id,row.tenant_id,lock=True)
    if target.kind=='platform':
        state=await deployments.remote(db,row,'audit');marker=state.get('deployment_id')
        current=await db.scalar(select(ModelDeployment).where(ModelDeployment.id==UUID(marker),ModelDeployment.tenant_id==row.tenant_id,ModelDeployment.target_key==row.target_key)) if marker else None
        versions=dict(target.observation.get('versions',{}))
        versions[row.target_key]=dict(state='verified' if row.status in ('active','rolled_back') and current and current.status=='active' else 'unknown',
            version=current.source.get('version') if current else None,name=current.source.get('name') if current else None,checked_at=datetime.now(timezone.utc).isoformat())
        target.observation={**target.observation,'versions':versions};await db.commit();return
    if target.target_key!=row.target_key or target.kind!=row.kind:return
    if row.kind=='image':
        target.observation=dict(state='verified' if row.status in ('active','rolled_back') else 'unknown',job_id=str(row.id),
            image=row.source['image'] if row.status=='active' else '、'.join(sorted({i['image'] for i in row.inspection['inventory']['instances']})),instances=row.inspection.get('instances',[]),checked_at=datetime.now(timezone.utc).isoformat())
    else:
        state=await deployments.remote(db,row,'audit')
        marker=state.get('deployment_id')
        current=await db.scalar(select(ModelDeployment).where(ModelDeployment.id==UUID(marker),ModelDeployment.tenant_id==row.tenant_id,ModelDeployment.target_key==row.target_key)) if marker else None
        target.observation=dict(state='verified' if row.status in ('active','rolled_back') and current and current.status=='active' else 'unknown' if marker else 'unmanaged',
            job_id=marker,release_id=str(current.release_id) if current and current.release_id else None,repository_id=str(current.repository_id) if current and current.repository_id else None,version=current.source.get('version') if current else None,
            name=current.source.get('name') if current else None,checked_at=datetime.now(timezone.utc).isoformat(),instances=row.inspection.get('instances',[]))
    await db.commit()


async def inspect_target(db,user,row):
    await deployments.validate_connections(SimpleNamespace(target=row.target))
    view=SimpleNamespace(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,target=row.target,source={})
    if row.kind=='image':
        from services import image_updates
        inventory=await image_updates.remote(db,view,'inventory')
        row.observation=dict(state='observed',instances=inventory['instances'],checked_at=datetime.now(timezone.utc).isoformat())
    else:
        state=await deployments.remote(db,view,'audit')
        marker=state.get('deployment_id')
        current=await db.scalar(select(ModelDeployment).where(ModelDeployment.id==UUID(marker),ModelDeployment.tenant_id==user.tenant_id,ModelDeployment.target_key==row.target_key)) if marker else None
        row.observation=dict(state='recorded' if current else 'unmanaged',job_id=marker,version=current.source.get('version') if current else None,
            release_id=str(current.release_id) if current and current.release_id else None,repository_id=str(current.repository_id) if current and current.repository_id else None,name=current.source.get('name') if current else None,checked_at=datetime.now(timezone.utc).isoformat())
    await db.commit();return await target_summary(db,row)
