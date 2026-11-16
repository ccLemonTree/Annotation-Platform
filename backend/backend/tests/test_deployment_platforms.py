"""Platform registration and multi-model plans use isolated SQLite and fake SSH."""
import hashlib
from uuid import uuid4
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def platform_setup(test_engine,monkeypatch):
    from models import Base
    from services import model_deployments
    async with test_engine.begin() as conn:
        for name in ('training_runs','model_repository_items','model_releases','deployment_targets','model_deployments','deployment_leases'):
            await conn.run_sync(lambda c:Base.metadata.tables[name].create(c,checkfirst=True))
    async def servers():return [dict(name='server-'+str(i),host='10.0.0.'+str(i+1),user='u',port=22) for i in range(20)]
    monkeypatch.setattr(model_deployments,'servers',servers)
    def bridge(server,payload):
        action=payload['payload']['action']
        if action=='platform_services':return dict(services=[dict(repository='/data/models',containers=['triton'],runtime='triton_plan')])
        if action=='platform_models':return dict(models=[dict(name=name,runtime='triton_plan') for name in ('detector','parcel')])
        if action=='platform_runtime':return dict(instances=[dict(name='triton',gpus=['Tesla T4'],tensorrt='10.14.1')])
        raise AssertionError(action)
    monkeypatch.setattr(model_deployments.training_runs,'bridge',bridge)


def register(client,headers,i=0):
    response=client.post('/api/model-deployments/platforms',headers=headers,json=dict(name='Platform '+str(i),control_server='server-'+str(i),environment='docker'))
    assert response.status_code==201,response.text
    platform=response.json()
    response=client.post('/api/model-deployments/platforms/'+platform['id']+'/discover',headers=headers)
    assert response.status_code==200,response.text
    return response.json()


async def model(db,name,version='v1',gpu='Tesla T4',trt='10.14.1'):
    from models.model_repository import ModelRepositoryItem
    row=ModelRepositoryItem(id=uuid4(),tenant_id=1,user_id=1,name=name,version=version,format='tensorrt',gpu_model=gpu,tensorrt_version=trt,
        entry_file='model.plan',file_size=1,files=[dict(path='model.plan',bytes=1,sha256=hashlib.sha256(b'x').hexdigest())],source={},status='ready')
    db.add(row);await db.commit();return row


@pytest.mark.asyncio
async def test_twenty_platforms_two_models_and_next_day_reuse(platform_setup,client,auth_headers,test_db):
    platforms=[register(client,auth_headers,i) for i in range(20)]
    assert all(len(p['models'])==2 for p in platforms)
    versions=[await model(test_db,name) for name in ('detector','parcel')]
    request=dict(platform_ids=[p['id'] for p in platforms],versions=[dict(repository_id=str(m.id)) for m in versions])
    preview=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request)
    assert preview.status_code==200 and len(preview.json()['items'])==40
    assert all(row['selected_key'] for row in preview.json()['items'])
    response=client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request)
    assert response.status_code==201,response.text
    assert len(response.json()['items'])==40 and len({r['batch_id'] for r in response.json()['items']})==1
    assert all(r['status']=='draft' for r in response.json()['items'])
    tomorrow=await model(test_db,'detector','v2')
    request['versions']=[dict(repository_id=str(tomorrow.id))]
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==201
    assert len(client.get('/api/model-deployments/platforms',headers=auth_headers).json()['items'])==20


@pytest.mark.asyncio
async def test_mapping_persists_and_missing_duplicate_or_foreign_targets_reject(platform_setup,client,auth_headers,test_db):
    platform=register(client,auth_headers)
    version=await model(test_db,'different-label')
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(version.id))])
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422
    request['assignments']=[dict(platform_id=platform['id'],version_key='r:'+str(version.id),model_key=platform['models'][0]['key'])]
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==201
    tomorrow=await model(test_db,'different-label','v2')
    request['versions']=[dict(repository_id=str(tomorrow.id))];request.pop('assignments')
    preview=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()
    assert preview['items'][0]['selected_key']==platform['models'][0]['key']
    request['versions'].append(dict(repository_id=str(version.id)))
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422
    request['platform_ids']=[str(uuid4())]
    assert client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).status_code==404


@pytest.mark.asyncio
async def test_platform_tenant_scope_and_reusable_exclusions(platform_setup,client,auth_headers,test_db,test_user):
    platform=register(client,auth_headers)
    versions=[await model(test_db,name) for name in ('detector','parcel')]
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(row.id)) for row in versions],assignments=[dict(platform_id=platform['id'],version_key='r:'+str(versions[1].id),model_key='skip')])
    result=client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request)
    assert result.status_code==201 and len(result.json()['items'])==1
    tomorrow=await model(test_db,'parcel','v2')
    request['versions']=[dict(repository_id=str(tomorrow.id))];request['assignments']=[]
    assert client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items'][0]['selected_key']=='skip'
    from models import Tenant
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.commit()
    test_user.tenant_id=2;await test_db.commit()
    assert client.get('/api/model-deployments/platforms',headers=auth_headers).json()['items']==[]
    assert client.post('/api/model-deployments/platforms/'+platform['id']+'/discover',headers=auth_headers).status_code==404
    test_user.role='annotator';await test_db.commit()
    assert client.get('/api/model-deployments/platforms',headers=auth_headers).status_code==403


@pytest.mark.asyncio
async def test_import_groups_models_on_same_server_and_is_idempotent(platform_setup,client,auth_headers):
    for name in ('detector','parcel'):
        response=client.post('/api/model-deployments/targets',headers=auth_headers,json=dict(name=name,kind='model',target=dict(environment='docker',runtime='triton_plan',file_server='server-0',control_server='server-0',repository='/data/models',model_name=name,containers=['triton'])))
        assert response.status_code==201,response.text
    assert client.post('/api/model-deployments/platforms/import-targets',headers=auth_headers).json()['imported']==1
    assert client.post('/api/model-deployments/platforms/import-targets',headers=auth_headers).json()['imported']==0
    assert len(client.get('/api/model-deployments/platforms',headers=auth_headers).json()['items'])==1
    assert len(client.get('/api/model-deployments/targets',headers=auth_headers).json()['items'])==2


@pytest.mark.asyncio
async def test_explicit_service_excludes_container_mounted_to_other_repository(platform_setup,client,auth_headers,monkeypatch):
    from services import model_deployments
    def bridge(server,payload):
        if payload['payload']['action']=='platform_services':return dict(services=[dict(repository=repo,containers=[name],runtime='triton_plan') for repo,name in [('/data/models','triton'),('/data/large','triton-large')]])
        return dict(models=[dict(name='detector',runtime='triton_plan')])
    monkeypatch.setattr(model_deployments.training_runs,'bridge',bridge)
    response=client.post('/api/model-deployments/platforms',headers=auth_headers,json=dict(name='Mixed',environment='docker',control_server='server-0',services=[dict(repository='/data/models',runtime='triton_plan',containers=['triton','triton-large'])]))
    assert response.status_code==201,response.text
    result=client.post('/api/model-deployments/platforms/'+response.json()['id']+'/discover',headers=auth_headers).json()
    assert result['models'][0]['target']['containers']==['triton']
    assert any('triton-large' in value for value in result['warnings'])


def test_platform_endpoints_require_admin(client):
    assert client.get('/api/model-deployments/platforms').status_code==401


@pytest.mark.asyncio
async def test_gpu_and_trt_variants_match_before_batch_creation(platform_setup,client,auth_headers,test_db,monkeypatch):
    from services import model_deployments
    bridge=model_deployments.training_runs.bridge;probes=[]
    def runtime_bridge(server,payload):
        if payload['payload']['action']=='platform_runtime':
            probes.append(server)
            return dict(instances=[dict(name='triton',gpus=['Tesla T4' if server=='server-0' else 'NVIDIA A10'],tensorrt='10.14.1')])
        return bridge(server,payload)
    monkeypatch.setattr(model_deployments.training_runs,'bridge',runtime_bridge)
    platforms=[register(client,auth_headers,i) for i in range(2)]
    assert len(probes)==2  # One service probe per platform, shared by both discovered models.
    versions=[await model(test_db,'detector','v2'),await model(test_db,'detector','v2',gpu='NVIDIA A10')]
    request=dict(platform_ids=[p['id'] for p in platforms],versions=[dict(repository_id=str(row.id)) for row in versions])
    preview=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items']
    assert sum(row['compatibility']=='compatible' for row in preview)==2
    assert sum(row.get('auto_skipped',False) for row in preview)==2
    result=client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request)
    assert result.status_code==201 and len(result.json()['items'])==2,result.text
    assert {row['target']['control_server']:row['source']['gpu_model'] for row in result.json()['items']}=={'server-0':'Tesla T4','server-1':'NVIDIA A10'}
    only_t4=dict(platform_ids=[platforms[1]['id']],versions=[dict(repository_id=str(versions[0].id))])
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=only_t4).status_code==422
    request['assignments']=[dict(platform_id=platforms[1]['id'],version_key='r:'+str(versions[0].id),model_key=platforms[1]['models'][0]['key'])]
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422
    wrong=await model(test_db,'detector','bad-trt',gpu='NVIDIA A10',trt='10.8.0')
    request=dict(platform_ids=[platforms[1]['id']],versions=[dict(repository_id=str(wrong.id))])
    preview=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items'][0]
    assert preview['compatibility']=='incompatible' and '10.8.0' in preview['error'] and '10.14.1' in preview['error']
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422


@pytest.mark.asyncio
async def test_expired_runtime_and_mixed_instance_gpus_are_not_compatible(platform_setup,client,auth_headers,test_db):
    from models.model_deployment import DeploymentTarget
    from uuid import UUID
    platform=register(client,auth_headers)
    version=await model(test_db,'detector')
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(version.id))])
    row=await test_db.get(DeploymentTarget,UUID(platform['id']))
    models=[{**model,'environment':{**model['environment'],'expires_at':'2000-01-01T00:00:00+00:00'}} for model in row.observation['models']]
    row.observation={**row.observation,'models':models};await test_db.commit()
    item=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items'][0]
    assert item['compatibility']=='unknown' and '过期' in item['error']
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422
    platform=client.post('/api/model-deployments/platforms/'+platform['id']+'/discover',headers=auth_headers).json()
    await test_db.refresh(row)
    models=[{**model,'environment':{**model['environment'],'instances':model['environment']['instances']+[dict(name='other',gpus=['NVIDIA A10'],tensorrt='10.14.1')]}} for model in row.observation['models']]
    row.observation={**row.observation,'models':models};await test_db.commit()
    assert client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items'][0]['compatibility']=='incompatible'


@pytest.mark.asyncio
async def test_unknown_or_expired_runtime_blocks_early(platform_setup,client,auth_headers,test_db,monkeypatch):
    from services import model_deployments
    bridge=model_deployments.training_runs.bridge
    def unavailable(server,payload):
        if payload['payload']['action']=='platform_runtime':raise RuntimeError('GPU probe unavailable')
        return bridge(server,payload)
    monkeypatch.setattr(model_deployments.training_runs,'bridge',unavailable)
    platform=register(client,auth_headers)
    version=await model(test_db,'detector')
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(version.id))])
    preview=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items'][0]
    assert preview['compatibility']=='unknown' and 'GPU probe unavailable' in preview['error']
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422


@pytest.mark.asyncio
async def test_unmapped_model_alias_reports_incompatible_environment(platform_setup,client,auth_headers,test_db):
    platform=register(client,auth_headers)
    version=await model(test_db,'friendly-label',gpu='NVIDIA A10')
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(version.id))])
    item=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items'][0]
    assert item['compatibility']=='incompatible'
    assert 'NVIDIA A10' in item['error'] and 'Tesla T4' in item['error']
    assert client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).status_code==422


@pytest.mark.asyncio
async def test_gpu_variant_reuses_existing_model_directory_alias(platform_setup,client,auth_headers,test_db):
    import json
    from uuid import UUID
    from models.model_deployment import DeploymentTarget
    platform=register(client,auth_headers)
    row=await test_db.get(DeploymentTarget,UUID(platform['id']))
    logical=json.dumps(['repository','friendly-label','NVIDIA A10','10.14.1'])
    row.observation={**row.observation,'mappings':{logical:platform['models'][0]['key']}};await test_db.commit()
    t4=await model(test_db,'friendly-label','v2')
    a10=await model(test_db,'friendly-label','v2',gpu='NVIDIA A10')
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(a10.id)),dict(repository_id=str(t4.id))])
    items=client.post('/api/model-deployments/platforms/preview',headers=auth_headers,json=request).json()['items']
    assert next(row for row in items if row['version_key']=='r:'+str(t4.id))['selected_key']==platform['models'][0]['key']
    assert next(row for row in items if row['version_key']=='r:'+str(a10.id))['auto_skipped']


def test_read_only_docker_discovery_maps_mount_and_recognizes_model_backends(tmp_path,monkeypatch):
    import json
    from services import model_deployment_executor as executor
    repo=tmp_path/'models';repo.mkdir()
    for name,config in [('detector','platform: "tensorrt_plan"'),('chat','backend: "vllm"')]:
        path=repo/name;path.mkdir();(path/'config.pbtxt').write_text(config,encoding='utf-8')
    calls=[]
    def command(args,timeout=60):
        calls.append(args)
        if args[:2]==['docker','ps']:return 'abc\n'
        if args[:2]==['docker','inspect']:return json.dumps([dict(Name='/triton',Config=dict(Entrypoint=['tritonserver'],Cmd=['--model-repository=/models','--model-control-mode=explicit']),Mounts=[dict(Type='bind',Source=str(repo),Destination='/models')])])
        raise AssertionError(args)
    monkeypatch.setattr(executor,'command',command)
    services=executor.platform_services(dict(environment='docker'))['services']
    assert len(services)==1 and services[0]['repository']==str(repo) and services[0]['containers']==['triton']
    assert {row['runtime'] for row in executor.platform_models(services[0])['models']}=={'triton_plan','triton_vllm'}
    assert all(args[1] in ('ps','inspect') for args in calls)


def test_k8s_discovery_resolves_nfs_claim_without_mutation(monkeypatch):
    import json
    from services import model_deployment_executor as executor
    workload=dict(kind='StatefulSet',metadata=dict(name='inference'),spec=dict(template=dict(spec=dict(containers=[dict(name='triton',command=['tritonserver'],args=['--model-repository=/models'],volumeMounts=[dict(name='models',mountPath='/models')])],volumes=[dict(name='models',persistentVolumeClaim=dict(claimName='models'))]))))
    calls=[]
    def command(args,timeout=60):
        calls.append(args)
        if args[2]=='deployments,statefulsets':return json.dumps(dict(items=[workload]))
        if args[2]=='pvc':return json.dumps(dict(spec=dict(volumeName='model-pv')))
        if args[2]=='pv':return json.dumps(dict(spec=dict(nfs=dict(server='10.0.0.2',path='/data/models'))))
        raise AssertionError(args)
    monkeypatch.setattr(executor,'command',command)
    result=executor.platform_services(dict(environment='k8s',namespace='ai',file_server='files',connections=dict(files=dict(host='10.0.0.2'))))
    assert result['services'][0]['repository']=='/data/models' and result['services'][0]['workload']=='inference'
    assert all(args[:2]==['kubectl','get'] for args in calls)


@pytest.mark.asyncio
async def test_shared_service_models_serialize_without_blocking_other_platform(monkeypatch):
    import asyncio
    from services import model_deployments as service
    active=set();seen=[]
    async def job(id,tenant,operation):
        key='shared' if id in (1,2) else 'other'
        assert key not in active
        active.add(key);seen.append(set(active));await asyncio.sleep(.02);active.remove(key)
    monkeypatch.setattr(service,'execute_job',job)
    monkeypatch.setattr(service,'_slots',asyncio.Semaphore(2))
    await asyncio.wait_for(asyncio.gather(service.execute(1,1,'transfer',['shared']),service.execute(2,1,'transfer',['shared']),service.execute(3,1,'transfer',['other'])),2)
    assert any(len(row)==2 for row in seen)


@pytest.mark.asyncio
async def test_platform_jobs_acquire_and_release_real_leases_when_executed(platform_setup,client,auth_headers,test_db,test_user,test_engine,monkeypatch):
    from uuid import UUID
    from sqlalchemy import select,func
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from services import model_deployments as service
    from models.model_deployment import DeploymentLease
    platform=register(client,auth_headers)
    versions=[await model(test_db,name) for name in ('detector','parcel')]
    request=dict(platform_ids=[platform['id']],versions=[dict(repository_id=str(row.id)) for row in versions])
    rows=client.post('/api/model-deployments/platforms/batches',headers=auth_headers,json=request).json()['items']
    monkeypatch.setattr(service,'schedule',lambda row,operation:None)
    for item in rows:
        row=await service.scoped(test_db,UUID(item['id']),1)
        assert (await service.action(test_db,row,'transfer',test_user)).status=='transferring'
    assert await test_db.scalar(select(func.count()).select_from(DeploymentLease))==0
    await test_db.rollback()
    async def transfer(db,row):
        assert await db.scalar(select(func.count()).select_from(DeploymentLease).where(DeploymentLease.job_id==row.id))==2
        await service.save(db,row,'isolated transfer','staged')
    monkeypatch.setattr(service,'transfer',transfer)
    monkeypatch.setattr(service,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    for item in rows:await service.execute_job(UUID(item['id']),1,'transfer')
    await test_db.rollback()
    assert await test_db.scalar(select(func.count()).select_from(DeploymentLease))==0
    for item in rows:assert (await service.scoped(test_db,UUID(item['id']),1)).status=='staged'
