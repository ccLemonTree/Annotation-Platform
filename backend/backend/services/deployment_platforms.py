"""Reusable platform connections and discovered model slots; execution stays per model."""
import asyncio
from datetime import datetime,timezone,timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID,uuid4
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from models.model_deployment import DeploymentTarget,ModelDeployment,ModelRelease
from schemas.model_deployment import DeploymentCreate
from services import distribution_center as center,model_deployments as deployments

_discovery_slots=asyncio.Semaphore(3)


async def scoped(db,id,tenant,lock=False):
    row=await center.scoped(db,DeploymentTarget,id,tenant,lock)
    if row.kind!='platform':raise HTTPException(404,'平台不存在')
    return row


def summary(row):
    return dict(id=str(row.id),name=row.name,config={key:value for key,value in row.target.items() if key!='connections'},
        models=row.observation.get('models',[]),checked_at=row.observation.get('checked_at'),error=row.observation.get('error'),
        versions=row.observation.get('versions',{}),stale=row.observation.get('stale',False),warnings=row.observation.get('warnings',[]),
        needs_runtime_refresh=any(model['target']['runtime']=='triton_plan' and not valid_environment(model.get('environment',{})) for model in row.observation.get('models',[])))


def valid_environment(environment):
    try:fresh=datetime.fromisoformat(environment['expires_at'])>datetime.now(timezone.utc)
    except (KeyError,TypeError,ValueError):return False
    return fresh and not environment.get('error') and bool(environment.get('instances')) and all(row.get('gpus') and row.get('tensorrt') for row in environment['instances'])


def environment_check(model,source):
    if source['runtime']!='triton_plan':return 'not_required','此模型不使用TensorRT引擎'
    environment=model.get('environment',{})
    if not valid_environment(environment):return 'unknown','GPU/TensorRT环境未核验或已过期，请重新读取平台模型'+('：'+environment['error'] if environment.get('error') else '')
    gpu=source.get('gpu_model','');trt=source.get('tensorrt_version','')
    if not gpu or not trt:return 'unknown','模型版本缺少构建GPU或TensorRT信息'
    instances=environment['instances']
    if all(deployments.runtime_matches(row,gpu,trt) for row in instances):return 'compatible','GPU型号和TensorRT版本匹配'
    actual='；'.join(row.get('name','实例')+'：'+','.join(sorted(set(row['gpus'])))+' / TRT '+row['tensorrt'] for row in instances)
    return 'incompatible','所选构建需要 '+gpu+' / TRT '+trt+'；目标环境 '+actual


def identity(config):
    connections=config['connections']
    return hashlib.sha256(json.dumps([[connections[name][key] for key in ('host','port','user')] for name in (config['control_server'],config['file_server'])]).encode()).hexdigest()


async def save(db,user,request,id=None):
    config=await center.target_snapshot(request.model_dump(exclude={'name'}))
    row=await scoped(db,id,user.tenant_id,True) if id else DeploymentTarget(id=uuid4(),tenant_id=user.tenant_id,kind='platform',observation={})
    if id and await db.scalar(select(ModelDeployment.id).where(ModelDeployment.target_id==id,ModelDeployment.status.in_((*deployments.BUSY,'unknown'))).limit(1)):
        raise HTTPException(409,'平台正在更新或状态待核对，请稍后修改')
    if id and row.target!=config:row.observation={**row.observation,'stale':True}
    row.name=request.name;row.target=config;row.target_key=identity(config);db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'此服务器平台已经登记，请编辑已有平台') from None
    await db.refresh(row);return row


async def remote(server,action,target):
    async with _discovery_slots:
        return await asyncio.to_thread(deployments.training_runs.bridge,server,dict(executor='model_deployment',payload=dict(action=action,target=target)))


async def discover(db,user,row):
    await deployments.validate_connections(SimpleNamespace(target=row.target))
    config=row.target
    try:
        services=config.get('services') or (await remote(config['control_server'],'platform_services',config))['services']
        if not services:raise HTTPException(422,'没有识别到模型服务，请检查启动参数和模型挂载，或填写高级服务配置')
        models={};warnings=[]
        for service in services:
            base={key:config[key] for key in ('environment','control_server','file_server','namespace')}
            base.update(service)
            if config.get('services') and base['environment']!='k8s':
                observed=(await remote(config['control_server'],'platform_services',{**config,'containers':base['containers']}))['services']
                matching={name for found in observed if found['repository'].rstrip('/')==base['repository'].rstrip('/') for name in found['containers']}
                excluded=[name for name in base['containers'] if name not in matching]
                base['containers']=[name for name in base['containers'] if name in matching]
                if not base['containers']:raise HTTPException(422,'指定容器的真实模型挂载不匹配仓库 '+base['repository']+'，请核对高级服务配置')
                if excluded:warnings.append('已排除挂载其他仓库的容器：'+'、'.join(excluded))
            result=await remote(config['file_server'],'platform_models',base)
            if result.get('repository'):base['repository']=result['repository']
            base.pop('only_model',None)
            warnings.extend(result.get('unsupported',[]))
            for found in result['models']:
                target=DeploymentCreate(run_id=UUID(int=0),artifact_path='model.plan',**{**base,'runtime':found['runtime'],'model_name':found['name']}).model_dump(mode='json',exclude={'run_id','artifact_path'})
                target['connections']=config['connections'];key=center.target_key(target,'model')
                if key in models:
                    old=models[key]['target']
                    if old['runtime']!=target['runtime'] or old['environment']=='k8s' and any(old[k]!=target[k] for k in ('namespace','workload','container')):
                        raise HTTPException(422,'同一模型目录对应多个不同服务，请在高级服务配置中明确更新范围')
                    old['containers']=sorted(set(old['containers']+target['containers']))
                else:models[key]=dict(key=key,name=found['name'],target=target)
        if not models:raise HTTPException(422,'模型服务已找到，但未读取到可更新的模型目录')
        environments={}
        for model in models.values():
            target=model['target']
            if target['runtime']!='triton_plan':continue
            service_key=json.dumps({key:target[key] for key in ('environment','repository','containers','namespace','workload_kind','workload','container')},sort_keys=True)
            if service_key not in environments:
                now=datetime.now(timezone.utc);environment=dict(checked_at=now.isoformat(),expires_at=(now+timedelta(minutes=15)).isoformat())
                try:environment.update(await remote(config['control_server'],'platform_runtime',target))
                except Exception as error:environment.update(error=str(error.detail) if isinstance(error,HTTPException) else str(error)[-1000:],instances=[])
                environments[service_key]=environment
            model['environment']=environments[service_key]
        row.observation={**row.observation,'models':list(models.values()),'checked_at':datetime.now(timezone.utc).isoformat(),'error':None,'stale':False,'warnings':warnings}
    except Exception as error:
        row.observation={**row.observation,'error':str(error.detail) if isinstance(error,HTTPException) else str(error)[-1000:],'stale':True}
    await db.commit();await db.refresh(row);return row


async def sources(db,user,request):
    result=[]
    for item in request.versions:
        if item.repository_id:
            row=await center.repository_model(db,item.repository_id,user.tenant_id)
            source=dict(kind='local_repository',repository_id=str(row.id),runtime='triton_plan',**{key:getattr(row,key) for key in center.REPOSITORY_FIELDS})
            result.append(dict(key='r:'+str(row.id),logical=json.dumps(['repository',row.name,row.gpu_model,row.tensorrt_version]),repository_id=row.id,release_id=None,run_id=row.run_id,source=source))
        else:
            row=await center.scoped(db,ModelRelease,item.release_id,user.tenant_id)
            if row.status!='ready':raise HTTPException(422,'完整模型目录尚未归档完成')
            source=dict(kind='local_release',release_id=str(row.id),name=row.name,version=row.version,runtime=row.runtime)
            if row.runtime=='triton_plan':
                info=row.archive.get('source_info',{});source.update(gpu_model=info.get('gpu_name',''),tensorrt_version=info.get('versions',{}).get('tensorrt_runtime') or info.get('versions',{}).get('tensorrt',''))
            result.append(dict(key='a:'+str(row.id),logical=row.runtime+':'+row.name,release_id=row.id,repository_id=None,run_id=row.run_id,source=source))
    return result


async def plan(db,user,request,lock=False):
    versions=await sources(db,user,request);items=[]
    assignments={(str(a.platform_id),a.version_key):a.model_key for a in request.assignments}
    platforms=[await scoped(db,id,user.tenant_id,lock) for id in sorted(request.platform_ids,key=str)]
    for platform in platforms:
        for version in versions:
            options=[model for model in platform.observation.get('models',[]) if model['target']['runtime']==version['source']['runtime']]
            selected=assignments.get((str(platform.id),version['key'])) or platform.observation.get('mappings',{}).get(version['logical'])
            if not selected and version['source']['runtime']=='triton_plan':
                candidates=set()
                for logical,key in platform.observation.get('mappings',{}).items():
                    try:identity=json.loads(logical)
                    except ValueError:continue
                    if isinstance(identity,list) and identity[:2]==['repository',version['source']['name']] and key!='skip':candidates.add(key)
                for peer in versions:
                    if peer['source']['name']==version['source']['name'] and peer['source']['runtime']=='triton_plan':
                        key=assignments.get((str(platform.id),peer['key']))
                        if key and key!='skip':candidates.add(key)
                if len(candidates)==1:selected=next(iter(candidates))
            if not selected:
                matched=[model for model in options if model['name']==version['source']['name']]
                if len(matched)==1:selected=matched[0]['key']
            if selected!='skip' and selected not in {model['key'] for model in options}:selected=None
            error='请先读取平台模型' if not platform.observation.get('checked_at') or platform.observation.get('stale') else None
            choices=[]
            for model in options:
                state,message=environment_check(model,version['source']);environment=model.get('environment',{})
                choices.append(dict(key=model['key'],name=model['name'],repository=model['target']['repository'],service=model['target']['workload'] or '、'.join(model['target']['containers']),
                    compatibility=state,message=message,disabled=state in ('unknown','incompatible'),gpus=sorted({gpu for row in environment.get('instances',[]) for gpu in row.get('gpus',[])}),
                    tensorrt_versions=sorted({row['tensorrt'] for row in environment.get('instances',[]) if row.get('tensorrt')}),checked_at=environment.get('checked_at')))
            chosen=next((choice for choice in choices if choice['key']==selected),None)
            state=chosen['compatibility'] if chosen else 'unknown' if version['source']['runtime']=='triton_plan' else 'not_required'
            message=chosen['message'] if chosen else '请先选择对应模型目录'
            if not chosen and choices:
                candidate=next((choice for choice in choices if not choice['disabled']),None)
                if candidate:state=candidate['compatibility'];message='存在兼容服务，请选择对应模型目录'
                else:
                    candidate=next((choice for choice in choices if choice['compatibility']=='unknown'),choices[0])
                    state=candidate['compatibility'];message=candidate['message']
            if selected=='skip':state='skipped';message='已按保存的对应关系或本次选择排除此项'
            elif state in ('unknown','incompatible'):error=error or message
            items.append(dict(platform_id=str(platform.id),platform_name=platform.name,version_key=version['key'],name=version['source']['name'],version=version['source']['version'],
                runtime=version['source']['runtime'],built_gpu=version['source'].get('gpu_model'),built_trt=version['source'].get('tensorrt_version'),options=choices,selected_key=selected,error=error,
                compatibility=state,compatibility_message=message,auto_skipped=False))
    for item in items:
        if item['compatibility']!='incompatible' or (item['platform_id'],item['version_key']) in assignments:continue
        alternatives=[row for row in items if row['platform_id']==item['platform_id'] and row['name']==item['name'] and row['runtime']==item['runtime'] and row['compatibility']=='compatible' and row['selected_key'] and not row['error']]
        if alternatives:
            item.update(selected_key='skip',compatibility='skipped',error=None,auto_skipped=True,
                compatibility_message='已自动跳过不兼容构建；此平台使用所选兼容版本 '+ '、'.join(row['version']+' / '+str(row['built_gpu'])+' / TRT '+str(row['built_trt']) for row in alternatives))
    destinations={}
    for item in items:
        if item['selected_key'] and item['selected_key']!='skip':destinations.setdefault(item['selected_key'],[]).append(item)
    for duplicates in destinations.values():
        if len(duplicates)>1:
            for item in duplicates:item['error']='同一模型目录选择了多个版本，请仅保留一个兼容版本，其余选择不更新'
    return items,platforms,versions


async def create_batch(db,user,request):
    items,platforms,versions=await plan(db,user,request,True)
    invalid=[row for row in items if row['error'] or not row['selected_key']]
    if invalid:raise HTTPException(422,'；'.join(row['platform_name']+' / '+row['name']+'：'+(row['error'] or '请选择对应模型目录') for row in invalid[:10]))
    destinations=[row['selected_key'] for row in items if row['selected_key']!='skip']
    if not destinations:raise HTTPException(422,'请至少选择一项需要更新的模型')
    if len(set(destinations))!=len(destinations):raise HTTPException(422,'同一个模型目录不能在同批次更新为多个版本，请调整模型对应关系或取消重复平台')
    for platform in platforms:await deployments.validate_connections(SimpleNamespace(target=platform.target))
    batch=uuid4();rows=[]
    for platform in platforms:
        mappings=dict(platform.observation.get('mappings',{}))
        for version in versions:
            item=next(row for row in items if row['platform_id']==str(platform.id) and row['version_key']==version['key'])
            if item['selected_key']=='skip':
                if not item['auto_skipped']:mappings[version['logical']]='skip'
                continue
            model=next(model for model in platform.observation['models'] if model['key']==item['selected_key'])
            mappings[version['logical']]=model['key']
            rows.append(ModelDeployment(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,target_id=platform.id,batch_id=batch,kind='model',status='draft',target_key=model['key'],
                run_id=version['run_id'],repository_id=version['repository_id'],release_id=version['release_id'],source={**version['source'],'platform_name':platform.name},
                target={**model['target'],'platform_id':str(platform.id)},inspection={},logs=[]))
        platform.observation={**platform.observation,'mappings':mappings}
    db.add_all(rows);await db.commit()
    for row in rows:await db.refresh(row)
    return dict(batch_id=str(batch),items=[deployments.summary(row) for row in rows])


async def import_legacy(db,tenant):
    existing=(await db.scalars(select(DeploymentTarget).where(DeploymentTarget.tenant_id==tenant,DeploymentTarget.kind=='platform'))).all()
    keys={row.target_key for row in existing};groups={}
    legacy=(await db.scalars(select(DeploymentTarget).where(DeploymentTarget.tenant_id==tenant,DeploymentTarget.kind=='model'))).all()
    for old in legacy:
        target=old.target
        config={key:target[key] for key in ('environment','control_server','file_server','connections')}
        config['namespace']=target.get('namespace','ai');config['services']=[];key=identity(config)
        if key in keys:continue
        if key not in groups:groups[key]=DeploymentTarget(id=uuid4(),tenant_id=tenant,kind='platform',name=target['control_server'],target_key=key,target=config,observation={})
        service={key:value for key,value in target.items() if key in ('environment','repository','runtime','containers','namespace','workload_kind','workload','container')}
        if service not in groups[key].target['services']:groups[key].target['services'].append(service)
    db.add_all(groups.values());await db.commit()
    return len(groups)
