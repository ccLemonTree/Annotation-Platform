"""Durable, manually activated model distribution over the existing MCP bridge."""
import asyncio
from datetime import datetime,timezone
import hashlib
import json
import shutil
from pathlib import Path
import tempfile
from time import monotonic
from types import SimpleNamespace
from uuid import UUID,uuid4
from fastapi import HTTPException
from sqlalchemy import select,inspect
from sqlalchemy.exc import IntegrityError
from database import AsyncSessionLocal
from models import User
from models.model_deployment import ModelDeployment
from services import training_runs

BUSY=('transferring','activating','rolling_back')
_tasks={}
_slots=asyncio.Semaphore(2)


async def servers():
    return await asyncio.to_thread(training_runs.bridge,'',dict(list_servers=True))


async def scoped(db,id,tenant_id,lock=False):
    query=select(ModelDeployment).where(ModelDeployment.id==id,ModelDeployment.tenant_id==tenant_id)
    if lock:query=query.with_for_update()
    row=await db.scalar(query.execution_options(populate_existing=True))
    if row is None:raise HTTPException(404,'模型下发记录不存在')
    return row


def summary(row):
    result={c.name:getattr(row,c.name) for c in row.__table__.columns}
    return {key:value.replace(tzinfo=timezone.utc) if isinstance(value,datetime) and value.tzinfo is None else value for key,value in result.items()}


async def create(db,user,request):
    parent=await training_runs.scoped(db,request.run_id,user.tenant_id)
    if parent.status!='completed':raise HTTPException(409,'请先完成来源训练、导出或合并任务')
    if request.runtime=='triton_plan':
        if parent.kind!='model_export' or parent.config.get('output',{}).get('format')!='tensorrt':
            raise HTTPException(422,'Triton小模型请选择完成的TensorRT导出任务')
    else:
        kind=parent.source.get('model_kind') or parent.kind
        if kind not in ('swift_sft','wemm') or parent.kind not in ('model_merge','swift_sft','wemm'):
            raise HTTPException(422,'请选择大模型/向量的完整训练或合并产物')
        if (request.runtime=='vllm')!=(kind=='wemm'):raise HTTPException(422,'向量模型使用原生vLLM，大模型使用Triton vLLM backend')
        if parent.kind!='model_merge' and parent.config.get('parameters',{}).get('tuner_type')=='lora':
            raise HTTPException(422,'请先合并LoRA再下发完整模型')
    available={s['name']:s for s in await servers()}
    names=(request.file_server,request.control_server)
    if any(name not in available for name in names):raise HTTPException(422,'请选择ssh-manager配置中的服务器')
    target=request.model_dump(mode='json',exclude={'run_id','artifact_path'})
    target['connections']={name:available[name] for name in names}
    key=hashlib.sha256(json.dumps([available[request.file_server]['host'],available[request.file_server]['port'],request.repository,request.model_name]).encode()).hexdigest()
    row=ModelDeployment(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,run_id=parent.id,status='draft',target_key=key,
        source=dict(root=parent.output_dir+'/'+parent.config['output_subdir'],artifact_path=request.artifact_path,
            runtime=request.runtime,server=parent.server),target=target,inspection={},logs=[])
    db.add(row);await db.commit();await db.refresh(row)
    return row


async def authorized(db,row):
    actor=await db.get(User,row.user_id,populate_existing=True)
    if actor is None or not actor.is_active or actor.role!='admin' or actor.tenant_id!=row.tenant_id:
        raise HTTPException(403,'下发发起者已停用或不再拥有管理员权限')


async def validate_connections(row):
    current={s['name']:s for s in await servers()}
    for name,previous in row.target['connections'].items():
        if any(current.get(name,{}).get(k)!=previous.get(k) for k in ('name','host','user','port')):
            raise HTTPException(409,'SSH连接目标已变化，请新建下发记录')


async def remote(db,row,action,source=False,control=False,**values):
    await authorized(db,row)
    conn=None
    if source:
        view=SimpleNamespace(tenant_id=row.tenant_id,server=row.source['server'])
        conn=await training_runs.connection(db,view)
        name='platform_training' if conn else row.source['server']['name']
    else:name=row.target['control_server'] if control else row.target['file_server']
    payload=dict(action=action,target=row.target,source=row.source,job_id=str(row.id),**values)
    return await asyncio.to_thread(training_runs.bridge,name,dict(connection=conn,executor='model_deployment',payload=payload))


async def save(db,row,message,status=None,**inspection):
    if status:row.status=status
    row.logs=[*row.logs,dict(at=datetime.now(timezone.utc).isoformat(),message=message)][-1000:]
    row.inspection={**row.inspection,**inspection}
    await db.commit();await db.refresh(row)


def complete_inventory(inventory):
    return len(inventory['instances'])==inventory['desired'] and all(i['ready'] for i in inventory['instances'])


async def inspect_job(db,row):
    await validate_connections(row)
    if row.source.get('kind')=='local_release':
        release=await local_release(db,row);data=release.archive['source_info']
    else:data=await remote(db,row,'source',source=True)
    state=await remote(db,row,'state')
    if not state.get('staging_same_device',True):raise HTTPException(409,'模型仓库是独立文件系统的挂载根目录，请使用同一文件系统内的仓库子目录以保留可恢复的目录切换')
    if state['free_bytes']<2*data['bytes']+64*1024**2:raise HTTPException(409,'目标剩余磁盘空间不足，需容纳传输包和展开模型')
    row.target={**row.target,'nfs4_roots':state.get('nfs4_roots',[]),
        'file_addresses':list(set(state['addresses']+[row.target['connections'][row.target['file_server']]['host']]))}
    inventory=await remote(db,row,'inventory',control=True)
    if not complete_inventory(inventory):raise HTTPException(409,'指定工作负载/容器未全部就绪，不能据此完成整组下发')
    row.target={**row.target,'internal_model_path':inventory['internal_model_path']}
    await check_compatibility(db,row,inventory,data)
    info={k:v for k,v in data.items() if k!='files'};info['file_count']=data.get('file_count',len(data.get('files',[])))
    await save(db,row,'预检查通过：来源校验、磁盘、挂载及全部实例已核对',baseline=state,inventory=inventory,source_info=info,check_ok=True)
    return inventory


async def check_compatibility(db,row,inventory,data):
    if row.target['runtime']=='triton_plan':
        expected=data['versions'].get('tensorrt_runtime') or data['versions']['tensorrt']
        expected='.'.join(expected.split('.')[:3])
        for instance in inventory['instances']:
            runtime=await remote(db,row,'compatibility',control=True,instance=instance)
            if runtime['tensorrt']!=expected or not runtime['gpus'] or any(g.strip()!=data['gpu_name'].strip() for g in runtime['gpus']):
                raise HTTPException(409,'TensorRT构建与推理环境不匹配：'+instance['name']+'；请在同型号GPU和TensorRT版本重新导出')


async def transfer(db,row):
    await inspect_job(db,row)
    if row.source.get('kind')=='local_release':
        release=await local_release(db,row);local=Path(release.archive['path'])
        from services.model_deployment_executor import sha
        if await asyncio.to_thread(sha,local)!=release.archive['sha256']:raise HTTPException(409,'本地归档包已变化，请重新归档新版本')
        destination=await remote(db,row,'prepare')
        await authorized(db,row);await validate_connections(row)
        await asyncio.to_thread(training_runs.bridge,row.target['file_server'],dict(upload=dict(localPath=str(local),remotePath=destination['path'])))
        result=await remote(db,row,'receive',sha256=release.archive['sha256'])
        await save(db,row,'本地归档已传输，逐文件校验通过，等待手动生效','staged',
            transfer=dict(bytes=result['bytes'],sha256=result['sha256'],file_count=result.get('file_count',len(result.get('files',[])))))
        return
    receipt=await remote(db,row,'pack',source=True)
    expected=str(Path(row.source['root']).parent).replace('\\','/')+'/.model-deployments/'+str(row.id)+'/bundle.zip'
    if receipt.get('path')!=expected or not 0<receipt.get('bytes',0)<=513*1024**3 or len(receipt.get('sha256',''))!=64:
        raise HTTPException(502,'来源模型包元数据无效')
    await save(db,row,'模型包已生成，正在中转并校验',bundle=receipt)
    folder=training_runs.ROOT/'.backups/model-deployment-transfer';folder.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(folder).free<receipt['bytes']+64*1024**2:raise HTTPException(409,'平台本地磁盘空间不足以中转模型包')
    with tempfile.TemporaryDirectory(prefix='deployment-',dir=folder) as temp:
        local=Path(temp)/'bundle.zip'
        conn=await training_runs.connection(db,SimpleNamespace(server=row.source['server'],tenant_id=row.tenant_id))
        await authorized(db,row)
        await asyncio.to_thread(training_runs.bridge,'platform_training' if conn else row.source['server']['name'],
            dict(connection=conn,download=dict(remotePath=receipt['path'],localPath=str(local),bytes=receipt['bytes'])))
        from services.model_deployment_executor import sha
        if local.stat().st_size!=receipt['bytes'] or await asyncio.to_thread(sha,local)!=receipt['sha256']:
            raise HTTPException(409,'中转模型包校验失败')
        destination=await remote(db,row,'prepare')
        await authorized(db,row);await validate_connections(row)
        await asyncio.to_thread(training_runs.bridge,row.target['file_server'],dict(upload=dict(localPath=str(local),remotePath=destination['path'])))
        result=await remote(db,row,'receive',sha256=receipt['sha256'])
    await save(db,row,'传输和逐文件SHA256校验完成，等待手动生效','staged',
        transfer=dict(bytes=result['bytes'],sha256=result['sha256'],file_count=result.get('file_count',len(result.get('files',[])))))


async def local_release(db,row):
    from services import distribution_center as center
    from models.model_deployment import ModelRelease
    release=await center.scoped(db,ModelRelease,row.release_id,row.tenant_id)
    if release.status!='ready':raise HTTPException(409,'本地模型版本尚未校验通过')
    path=Path(release.archive['path'])
    if not path.is_file() or path.stat().st_size!=release.archive['bytes']:raise HTTPException(409,'本地归档文件缺失或已变化')
    return release


async def verify_instances(db,row,inventory,absent=False):
    results=[]
    for instance in inventory['instances']:
        result=await remote(db,row,'probe',control=True,instance=instance,absent=absent)
        results.append(result)
    row.inspection={**row.inspection,'instances':results};await db.commit()
    return complete_inventory(inventory) and all(r['ok'] for r in results)


async def activate(db,row,rollback=False):
    await validate_connections(row)
    state=await remote(db,row,'state')
    row.target={**row.target,'nfs4_roots':state.get('nfs4_roots',[]),
        'file_addresses':list(set(state['addresses']+[row.target['connections'][row.target['file_server']]['host']]))}
    inventory=await remote(db,row,'inventory',control=True)
    previous=row.inspection['inventory']
    if inventory['controller']!=previous['controller'] or {i['image_id'] for i in inventory['instances']}!={i['image_id'] for i in previous['instances']}:
        raise HTTPException(409,'工作负载或推理镜像已变化，请先人工核对目标')
    if not rollback:await check_compatibility(db,row,inventory,row.inspection['source_info'])
    await save(db,row,'即将切换模型目录，开始保留恢复现场',mutation_started=True)
    result=await remote(db,row,'restore' if rollback else 'promote',**({} if rollback else dict(baseline=row.inspection['baseline'])))
    operation='rollback' if rollback else 'activate'
    applied=row.inspection.get('applied',[]) if row.inspection.get('runtime_operation')==operation else []
    await save(db,row,'旧目录已恢复，正在核对推理实例' if rollback else '模型目录已切换，旧版本已保留，正在使各实例生效',promotion=result,applied=applied,instances=[],runtime_operation=operation)
    absent=rollback and not result['had_previous']
    if row.target['runtime']=='triton_plan':
        for instance in inventory['instances']:
            if any(r['uid']==instance['uid'] and r['ok'] for r in row.inspection['applied']):continue
            applied=await remote(db,row,'probe',control=True,instance=instance,operation='unload' if absent else 'load')
            await save(db,row,instance['name']+'：'+('卸载' if absent else '加载')+('成功' if applied['ok'] else '失败'),applied=[*[r for r in row.inspection['applied'] if r['uid']!=instance['uid']],applied])
            if not applied['ok']:raise HTTPException(409,'部分实例加载失败；旧版本仍保留，可继续生效或恢复旧版本')
    elif row.target['environment']=='k8s':
        token=row.inspection.get('restart_token') if row.inspection.get('restart_operation')==operation else None
        if not token or inventory.get('rollout_token')!=token:
            token=str(row.id)+('-restore-' if rollback else '-apply-')+uuid4().hex
            await save(db,row,'正在滚动重启指定vLLM工作负载',restart_token=token,restart_operation=operation)
            await remote(db,row,'restart',control=True,controller=inventory['controller'],token=token)
    else:
        for instance in inventory['instances']:
            if any(r['uid']==instance['uid'] and r['ok'] for r in row.inspection['applied']):continue
            await remote(db,row,'restart',control=True,instance=instance)
            await save(db,row,instance['name']+'：已重启',applied=[*row.inspection['applied'],dict(name=instance['name'],uid=instance['uid'],ok=True)])
    # shortcut: one backend process owns a run; use distributed leases before multi-instance workers.
    deadline=monotonic()+max(1200,inventory['desired']*600 if row.target['environment']=='k8s' else 1200)
    while monotonic()<deadline:
        inventory=await remote(db,row,'inventory',control=True)
        if row.target['runtime']=='triton_plan':
            for instance in inventory['instances']:
                if instance['ready'] and not any(r['uid']==instance['uid'] and r['ok'] for r in row.inspection['applied']):
                    if instance['image_id'] not in {i['image_id'] for i in previous['instances']}:raise HTTPException(409,'新实例的推理镜像已变化，请人工核对')
                    if not rollback:await check_compatibility(db,row,dict(instances=[instance]),row.inspection['source_info'])
                    applied=await remote(db,row,'probe',control=True,instance=instance,operation='unload' if absent else 'load')
                    await save(db,row,instance['name']+'：核对新实例加载',applied=[*[r for r in row.inspection['applied'] if r['uid']!=instance['uid']],applied])
                    if not applied['ok']:raise HTTPException(409,'新替换实例加载失败，请核对后重试')
        same={i['uid'] for i in inventory['instances']}<= {i['uid'] for i in row.inspection.get('applied',[]) if i['ok']}
        if row.target['runtime']!='triton_plan' and row.target['environment']=='k8s':same=inventory.get('rollout_token')==row.inspection['restart_token'] and inventory.get('rollout_complete',False)
        if complete_inventory(inventory) and same and await verify_instances(db,row,inventory,absent):
            await save(db,row,'全部指定实例已核验'+('，旧版本恢复完成' if rollback else '，模型生效完成'),'rolled_back' if rollback else 'active')
            return
        await asyncio.sleep(10)
    raise HTTPException(504,'等待全部实例就绪超时，请核对状态；不能认定整组生效成功')


async def execute(id,tenant,operation):
    try:
        async with _slots:await execute_job(id,tenant,operation)
    finally:_tasks.pop(str(id),None)


async def execute_job(id,tenant,operation):
    async with AsyncSessionLocal() as db:
        row=await scoped(db,id,tenant)
        try:
            if row.kind=='image':
                from services import image_updates
                if operation=='transfer':await image_updates.prepare(db,row)
                else:await image_updates.activate(db,row,rollback=operation=='rollback')
            elif operation=='transfer':await transfer(db,row)
            else:await activate(db,row,rollback=operation=='rollback')
            if operation!='transfer':
                from services import distribution_center as center
                await center.observe(db,row)
        except asyncio.CancelledError:
            row.error='后台执行被中断，远端状态待核对';await save(db,row,row.error,'unknown');raise
        except Exception as error:
            await db.rollback();row=await scoped(db,id,tenant)
            row.error=str(error.detail) if isinstance(error,HTTPException) else str(error)[-2000:]
            status='transfer_failed' if operation=='transfer' else 'rollback_failed' if operation=='rollback' else 'activation_failed'
            if operation!='transfer' and (not isinstance(error,HTTPException) or error.status_code>=500):status='unknown'
            await save(db,row,row.error,status)
        finally:
            if row.status in ('staged','active','rolled_back','transfer_failed') or row.status in ('activation_failed','rollback_failed') and not (row.inspection.get('mutation_started') or row.inspection.get('apply_started')):
                from services import distribution_center as center
                await center.release_lease(db,row)
            _tasks.pop(str(id),None)


def schedule(row,operation):
    _tasks[str(row.id)]=asyncio.create_task(execute(row.id,row.tenant_id,operation))


async def action(db,row,operation,actor=None):
    row=await scoped(db,row.id,row.tenant_id,lock=True)
    if str(row.id) in _tasks or row.status in BUSY:raise HTTPException(409,'此下发任务正在执行，请等待完成')
    if operation=='precheck':
        if row.status not in ('draft','checked','transfer_failed'):raise HTTPException(409,'已传输的任务不能重新指定检查基线，请新建记录')
        if actor:row.user_id=actor.id
        try:
            if row.kind=='image':
                from services import image_updates
                await image_updates.inspect_job(db,row)
            else:await inspect_job(db,row)
            row.error=None;row.status='checked';await db.commit();await db.refresh(row)
        except HTTPException as error:
            row.error=str(error.detail);row.inspection={**row.inspection,'check_ok':False};await db.commit();raise
        return row
    allowed={'transfer':('draft','checked','transfer_failed'),'activate':('staged','activation_failed','unknown'),
        'rollback':('active','activation_failed','rollback_failed','unknown')}
    if row.status not in allowed.get(operation,()):raise HTTPException(409,'当前状态不能执行此操作')
    if actor:row.user_id=actor.id
    if operation!='transfer' and not row.inspection.get('inventory'):raise HTTPException(409,'尚未完成传输预检查，不能使服务生效')
    if row.kind!='image' and operation!='transfer' and not row.inspection.get('promotion') and operation=='rollback':
        state=await remote(db,row,'state')
        if not state.get('receipt') or state['receipt'].get('state') not in ('promoted','promoting','restored'):
            raise HTTPException(409,'尚未切换在线目录，无需恢复旧版本')
    await authorized(db,row)
    from services import distribution_center as center
    await center.acquire(db,row)
    row.status={'transfer':'transferring','activate':'activating','rollback':'rolling_back'}[operation];row.error=None
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'同一模型目标有其他正在执行或待核对的下发任务') from None
    await db.refresh(row)
    schedule(row,operation)
    return row


async def refresh(db,row):
    row=await scoped(db,row.id,row.tenant_id,lock=True)
    if str(row.id) in _tasks:return row
    await validate_connections(row)
    if row.kind=='image':
        from services import image_updates
        return await image_updates.refresh(db,row)
    state=await remote(db,row,'state');receipt=state.get('receipt')
    if row.status in BUSY:row.status='unknown'
    if row.status=='unknown':
        if not receipt:row.status='transfer_failed';row.error='未找到完成的暂存收据，可重新传输'
        elif receipt['state']=='staged':row.status='staged';row.error=None
        else:row.error='在线目录已操作，需继续生效或恢复旧版本并重新核验全部实例'
    await save(db,row,'已读取远端目录及暂存收据，未执行加载/重启',remote_state=state)
    if row.status in ('staged','transfer_failed'):
        from services import distribution_center as center
        await center.release_lease(db,row)
    return row


async def start_worker():
    async with AsyncSessionLocal() as db:
        conn=await db.connection()
        if not await conn.run_sync(lambda c:inspect(c).has_table('model_deployments')):return
        rows=(await db.scalars(select(ModelDeployment).where(ModelDeployment.status.in_(BUSY)))).all()
        for row in rows:row.status='unknown';row.error='应用重启中断了执行，请先核对远端状态'
        from models.model_deployment import ModelRelease
        if await conn.run_sync(lambda c:inspect(c).has_table('model_releases')):
            releases=(await db.scalars(select(ModelRelease).where(ModelRelease.status=='archiving'))).all()
            for row in releases:row.status='interrupted';row.error='应用重启中断归档，可手动重试'
        await db.commit()


async def stop_worker():
    from services import distribution_center as center
    tasks=[*list(_tasks.values()),*list(center._archives.values())]
    for task in tasks:task.cancel()
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
