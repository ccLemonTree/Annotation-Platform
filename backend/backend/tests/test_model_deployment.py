"""Distribution checks use local files and isolated databases, never production services."""
import hashlib
import importlib
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def deployment_tables(test_engine):
    from models.training_run import TrainingRun,TrainingGpuHold
    from models.model_deployment import ModelDeployment
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c:TrainingRun.__table__.create(c,checkfirst=True))
        await conn.run_sync(lambda c:TrainingGpuHold.__table__.create(c,checkfirst=True))
        from models.model_deployment import ModelRelease,DeploymentTarget,DeploymentLease
        for table in (ModelRelease.__table__,DeploymentTarget.__table__,DeploymentLease.__table__):
            await conn.run_sync(lambda c:table.create(c,checkfirst=True))
        await conn.run_sync(lambda c:ModelDeployment.__table__.create(c,checkfirst=True))


def executor():
    try:
        return importlib.import_module('services.model_deployment_executor')
    except ModuleNotFoundError:
        pytest.fail('模型下发执行器尚未实现')


def target(tmp_path, **values):
    repository=tmp_path/'models';repository.mkdir(exist_ok=True)
    return dict(dict(repository=str(repository),model_name='detector',runtime='triton_plan',
        environment='docker',file_server='files',control_server='control',containers=['triton'],
        namespace='ai',workload_kind='StatefulSet',workload='',container='triton-server',
        triton_config='',runtime_config={}),**values)


def source(tmp_path, **values):
    root=tmp_path/'output';root.mkdir(exist_ok=True)
    (root/'model.engine').write_bytes(b'engine')
    manifest={'format':'tensorrt','gpu_name':'Tesla T4','versions':{'tensorrt':'10.13.3'},
        'files':[{'path':'model.engine','bytes':6,'sha256':hashlib.sha256(b'engine').hexdigest()}]}
    (root/'result.json').write_text(json.dumps(manifest))
    return dict(dict(root=str(root),artifact_path='model.engine',runtime='triton_plan'),**values)


def test_missing_distribution_api(client,auth_headers):
    response=client.get('/api/model-deployments?run_id='+str(uuid4()),headers=auth_headers)
    assert response.status_code==200,response.text


@pytest.mark.asyncio
async def test_deployment_logs_are_scoped_read_only_and_redacted(client,auth_headers,local_flow,test_db,test_user,monkeypatch):
    from services import training_runs
    from models.model_deployment import DeploymentTarget
    service,row,calls,failures,live=local_flow
    row.logs=[{'at':'2026-10-26T00:00:00Z','message':'transfer phase password=private'}]
    target_row=DeploymentTarget(id=uuid4(),tenant_id=test_user.tenant_id,name='saved target',kind='model',target_key='saved',target=row.target)
    test_db.add(target_row);await test_db.commit()
    original=row.status;remote=[]
    def bridge(name,payload):
        remote.append((name,payload))
        return dict(content='ready Authorization: Bearer private',truncated=False,instances=[{'name':'triton','uid':'id'}],selected_instance='triton')
    monkeypatch.setattr(training_runs,'bridge',bridge)
    response=client.get(f'/api/model-deployments/{row.id}/logs',headers=auth_headers)
    assert response.status_code==200 and 'transfer phase' in response.json()['content']
    assert 'private' not in response.text and not remote
    response=client.get(f'/api/model-deployments/{row.id}/logs?source=service&instance=triton',headers=auth_headers)
    assert response.status_code==200 and 'private' not in response.text
    assert remote[0][1]['executor']=='deployment_logs' and remote[0][1]['payload']['instance']=='triton'
    assert client.get(f'/api/model-deployments/targets/{target_row.id}/logs?source=service',headers=auth_headers).status_code==200
    assert client.get(f'/api/model-deployments/{row.id}/logs?source=arbitrary',headers=auth_headers).status_code==422
    await test_db.refresh(row);assert row.status==original
    from models import Tenant
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush()
    row.tenant_id=2;await test_db.commit()
    assert client.get(f'/api/model-deployments/{row.id}/logs',headers=auth_headers).status_code==404
    test_user.role='annotator';await test_db.commit()
    assert client.get(f'/api/model-deployments/targets/{target_row.id}/logs?source=service',headers=auth_headers).status_code==403


@pytest.mark.parametrize('change',[
    {'repository':'/etc/models'},{'repository':'/'},{'model_name':'../model'},
    {'model_name':'model; reboot'},{'artifact_path':'../model.engine'},
    {'containers':['x; reboot']},{'environment':'k8s','workload':''},
])
def test_reject_invalid_deployment_input(change):
    try:
        schema=importlib.import_module('schemas.model_deployment')
    except ModuleNotFoundError:
        pytest.fail('模型下发输入校验尚未实现')
    values=dict(run_id=str(uuid4()),artifact_path='model.engine',repository='/data/models',
        model_name='detector',runtime='triton_plan',environment='docker',file_server='files',
        control_server='control',containers=['triton'])
    values.update(change)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):schema.DeploymentCreate(**values)


def test_tensor_source_requires_completed_export_manifest_and_stable_checksum(tmp_path):
    mod=executor();spec=source(tmp_path)
    result=mod.inspect_source(spec)
    assert result['bytes']==6 and result['gpu_name']=='Tesla T4'
    (Path(spec['root'])/'model.engine').write_bytes(b'broken')
    with pytest.raises(ValueError,match='校验'):mod.inspect_source(spec)


def test_full_model_rejects_adapter_missing_shards_and_symlinks(tmp_path):
    mod=executor();spec=source(tmp_path,runtime='vllm',artifact_path='merged')
    folder=Path(spec['root'])/'merged';folder.mkdir()
    (folder/'config.json').write_text('{}');(folder/'model.safetensors').write_bytes(b'weights')
    (folder/'tokenizer.json').write_text('{}')
    assert mod.inspect_source(spec)['bytes']>0
    (folder/'adapter_config.json').write_text('{}')
    with pytest.raises(ValueError,match='adapter'):mod.inspect_source(spec)
    (folder/'adapter_config.json').unlink()
    (folder/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'a':'missing.safetensors'}}))
    with pytest.raises(ValueError,match='分片'):mod.inspect_source(spec)


def stage_fixture(tmp_path,mod):
    spec=source(tmp_path);t=target(tmp_path);job=str(uuid4())
    receipt=mod.pack_source(spec,job)
    stage=mod.workspace(t,job);stage.mkdir(parents=True,exist_ok=True)
    import shutil
    shutil.copyfile(receipt['path'],stage/'input.zip')
    baseline=mod.target_state(t)
    mod.receive(t,job,receipt['sha256'])
    return t,job,baseline


def test_transfer_keeps_live_model_untouched_and_activation_can_restore(tmp_path):
    mod=executor();t=target(tmp_path);live=Path(t['repository'])/'detector'
    (live/'1').mkdir(parents=True);(live/'1/model.plan').write_bytes(b'previous')
    (live/'config.pbtxt').write_text('name: "detector"\nplatform: "tensorrt_plan"\n')
    t,job,baseline=stage_fixture(tmp_path,mod)
    assert (live/'1/model.plan').read_bytes()==b'previous'
    assert not (live/'.platform-deployment.json').exists()
    mod.promote(t,job,baseline)
    assert (live/'1/model.plan').read_bytes()==b'engine'
    assert mod.workspace(t,job).joinpath('previous/1/model.plan').read_bytes()==b'previous'
    mod.restore(t,job)
    assert (live/'1/model.plan').read_bytes()==b'previous'


def test_stale_staging_or_rollback_cannot_replace_a_newer_release(tmp_path):
    mod=executor();t,first,baseline=stage_fixture(tmp_path,mod)
    t,second,same=stage_fixture(tmp_path,mod)
    mod.promote(t,first,baseline)
    with pytest.raises(ValueError,match='变化'):mod.promote(t,second,same)
    live=Path(t['repository'])/'detector'
    (live/'.platform-deployment.json').write_text(json.dumps({'id':second}))
    with pytest.raises(ValueError,match='其他'):mod.restore(t,first)


@pytest.mark.parametrize('name',['../escape','/absolute','1/../../escape','1/model.plan'])
def test_invalid_or_unsigned_zip_is_never_extracted(tmp_path,name):
    mod=executor();t=target(tmp_path);job=str(uuid4());stage=mod.workspace(t,job)
    stage.mkdir(parents=True)
    with zipfile.ZipFile(stage/'input.zip','w') as out:out.writestr(name,b'bad')
    digest=hashlib.sha256((stage/'input.zip').read_bytes()).hexdigest()
    with pytest.raises(ValueError):mod.receive(t,job,digest)
    assert not (tmp_path/'escape').exists()
    assert not (Path(t['repository'])/'detector').exists()


def test_interrupted_promotion_can_restore_the_retained_directory(tmp_path):
    mod=executor();t,job,baseline=stage_fixture(tmp_path,mod)
    live=Path(t['repository'])/'detector';live.mkdir();(live/'old').write_bytes(b'old')
    stage=mod.workspace(t,job)
    receipt=json.loads((stage/'receipt.json').read_text());receipt.update(state='promoting',had_previous=True)
    (stage/'receipt.json').write_text(json.dumps(receipt));live.rename(stage/'previous')
    mod.restore(t,job)
    assert (live/'old').read_bytes()==b'old'


def test_workspace_rejects_symlink_parent(tmp_path,monkeypatch):
    mod=executor();t=target(tmp_path);outside=tmp_path/'outside';outside.mkdir()
    parent=tmp_path/'.models-platform-deployments'
    try:parent.symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('此Windows测试账户不能创建符号链接')
    with pytest.raises(ValueError,match='链接'):mod.workspace(t,str(uuid4()))


def test_retried_restore_cannot_claim_success_after_another_release(tmp_path):
    mod=executor();t=target(tmp_path);live=Path(t['repository'])/'detector';live.mkdir();(live/'old').write_bytes(b'old')
    t,job,baseline=stage_fixture(tmp_path,mod);mod.promote(t,job,baseline);mod.restore(t,job)
    (live/'old').write_bytes(b'a newer release')
    with pytest.raises(ValueError,match='变化'):mod.restore(t,job)


def test_interrupted_restore_can_finish_without_overwriting_a_new_release(tmp_path):
    mod=executor();t=target(tmp_path);live=Path(t['repository'])/'detector';live.mkdir();(live/'old').write_bytes(b'old')
    t,job,baseline=stage_fixture(tmp_path,mod);mod.promote(t,job,baseline)
    stage=mod.workspace(t,job);receipt=json.loads((stage/'receipt.json').read_text());receipt['state']='restoring'
    (stage/'receipt.json').write_text(json.dumps(receipt));live.rename(stage/'withdrawn')
    mod.restore(t,job)
    assert (live/'old').read_bytes()==b'old'


def test_failed_transfer_checksum_creates_no_release(tmp_path):
    mod=executor();t=target(tmp_path);job=str(uuid4());stage=mod.workspace(t,job)
    stage.mkdir(parents=True);(stage/'input.zip').write_bytes(b'not a bundle')
    with pytest.raises(ValueError,match='校验'):mod.receive(t,job,'0'*64)
    assert not (stage/'release').exists()


def test_docker_inventory_checks_every_container_mount_and_explicit_mode(tmp_path,monkeypatch):
    mod=executor();t=target(tmp_path,containers=['triton-1','triton-2'])
    def container(name,root):
        return {'Id':name+'-id','Name':'/'+name,'Image':'digest','State':{'Running':True},
            'Config':{'Image':'triton:25','Entrypoint':[],'Cmd':['tritonserver','--model-repository=/models','--model-control-mode=explicit']},
            'Mounts':[{'Type':'bind','Source':root,'Destination':'/models'}]}
    rows=[container('triton-1',t['repository']),container('triton-2',t['repository'])]
    monkeypatch.setattr(mod,'command',lambda args,timeout=60:json.dumps(rows))
    result=mod.inventory(t)
    assert len(result['instances'])==result['desired']==2
    rows[1]['Mounts'][0]['Source']='/different/models'
    with pytest.raises(ValueError,match='挂载'):mod.inventory(t)


def test_distribution_requires_admin_and_never_exposes_other_tenants(client,auth_headers,test_user):
    assert client.get('/api/model-deployments').status_code==401
    assert client.get('/api/model-deployments/'+str(uuid4()),headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_create_is_draft_transfer_and_activation_are_separate(client,auth_headers,test_db,monkeypatch):
    from models.training_run import TrainingRun
    try:from services import model_deployments as service
    except ImportError:pytest.fail('模型下发服务尚未实现')
    parent=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='export',kind='model_export',status='completed',
        config={'output_subdir':'result','model_kind':'rfdetr_2xlarge','output':{'format':'tensorrt'}},
        source={},server={'id':'default','name':'source'},output_dir='/data/output',inspection={})
    test_db.add(parent);await test_db.commit()
    async def servers():return [{'name':'files','host':'10.0.0.1','user':'root','port':22}]
    monkeypatch.setattr(service,'servers',servers)
    scheduled=[];monkeypatch.setattr(service,'schedule',lambda row,operation:scheduled.append(operation))
    values=dict(run_id=str(parent.id),artifact_path='model.engine',repository='/data/models',model_name='detector',
        file_server='files',control_server='files',containers=['triton'])
    response=client.post('/api/model-deployments',headers=auth_headers,json=values)
    assert response.status_code==201,response.text
    row=response.json();assert row['status']=='draft' and not scheduled
    url='/api/model-deployments/'+row['id']
    assert client.post(url+'/activate',headers=auth_headers).status_code==409
    assert client.post(url+'/transfer',headers=auth_headers).status_code==202
    assert scheduled==['transfer']
    assert client.get(url,headers=auth_headers).json()['status']=='transferring'
    assert client.post(url+'/transfer',headers=auth_headers).status_code==409
    assert client.delete('/api/training/runs/'+str(parent.id),headers=auth_headers).status_code==409


@pytest.mark.asyncio
async def test_model_deployment_tenant_and_role_are_checked(client,auth_headers,test_db,test_user,monkeypatch):
    from models.model_deployment import ModelDeployment
    from models.training_run import TrainingRun
    row=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='export',kind='model_export',status='completed',
        config={},source={},server={},output_dir='/data/output',inspection={})
    test_db.add(row);await test_db.commit()
    deployment=ModelDeployment(id=uuid4(),tenant_id=1,user_id=1,run_id=row.id,status='draft',target_key='x',source={},target={},inspection={},logs=[])
    test_db.add(deployment);await test_db.commit()
    test_user.role='annotator';await test_db.commit()
    assert client.get('/api/model-deployments',headers=auth_headers).status_code==403
    test_user.role='admin';await test_db.commit()
    from models import Tenant
    test_db.add(Tenant(id=999,tenant_name='other'));await test_db.commit()
    deployment.tenant_id=999;await test_db.commit()
    assert client.get('/api/model-deployments/'+str(deployment.id),headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_action_response_has_no_implicit_database_load(test_db,test_user,monkeypatch):
    from sqlalchemy import inspect
    from models.model_deployment import ModelDeployment
    from models.training_run import TrainingRun
    from services import model_deployments as service
    parent=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='export',kind='model_export',status='completed',
        config={},source={},server={},output_dir='/data/output',inspection={})
    test_db.add(parent);await test_db.commit()
    row=ModelDeployment(id=uuid4(),tenant_id=1,user_id=1,run_id=parent.id,status='draft',target_key='target',source={},
        target=dict(environment='docker',control_server='files',containers=['triton'],connections={'files':dict(host='h',port=22)}),inspection={},logs=[])
    test_db.add(row);await test_db.commit()
    monkeypatch.setattr(service,'schedule',lambda row,op:None)
    result=await service.action(test_db,row,'transfer')
    assert not inspect(result).expired_attributes


@pytest_asyncio.fixture
async def local_flow(tmp_path,test_db,test_user,monkeypatch):
    import shutil
    from models.training_run import TrainingRun
    from models.model_deployment import ModelDeployment
    from services import model_deployments as service,training_runs
    mod=executor();spec=source(tmp_path);spec['root']=Path(spec['root']).as_posix()
    t=target(tmp_path,file_server='files',control_server='files');t['connections']={'files':{'name':'files','host':'10.0.0.1','port':22,'user':'root'}}
    live=Path(t['repository'])/'detector';(live/'1').mkdir(parents=True);(live/'1/model.plan').write_bytes(b'previous')
    parent=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='export',kind='model_export',status='completed',
        config={},source={},server={},output_dir=spec['root'],inspection={})
    test_db.add(parent);await test_db.commit()
    row=ModelDeployment(id=uuid4(),tenant_id=1,user_id=1,run_id=parent.id,status='draft',target_key='flow',
        source={**spec,'server':{'id':'default','name':'source'}},target=t,inspection={},logs=[])
    test_db.add(row);await test_db.commit()
    instances=[dict(name=name,uid=name,image_id='image',ready=True,internal_model_path='/models') for name in ('one','two')]
    calls=[];loaded={};failures=set()
    async def available():return list(t['connections'].values())
    async def connection(*args):return None
    def bridge(name,payload):
        transfer=payload.get('upload') or payload.get('download')
        assert transfer,'测试禁止真实MCP调用'
        a,b=(transfer['localPath'],transfer['remotePath']) if 'upload' in payload else (transfer['remotePath'],transfer['localPath'])
        shutil.copyfile(a,b);return {}
    async def remote(db,job,action,source=False,control=False,**values):
        calls.append((action,values.get('instance',{}).get('name'),values.get('operation')))
        if action=='source':return mod.dispatch(dict(action='source',source=job.source))
        if action=='pack':
            result=mod.pack_source(job.source,str(job.id));result['path']=result['path'].replace('\\','/');return result
        if action=='state':return dict(**mod.target_state(job.target),addresses=['10.0.0.1'])
        if action=='inventory':return dict(instances=instances,desired=2,controller=None,internal_model_path='/models')
        if action=='compatibility':return dict(gpus=['Tesla T4'],tensorrt='10.13.3')
        if action=='prepare':
            stage=mod.workspace(job.target,str(job.id));stage.mkdir(parents=True,exist_ok=True);return dict(path=str(stage/'input.zip'))
        if action=='receive':return mod.dispatch(dict(action='receive',target=job.target,job_id=str(job.id),sha256=values['sha256']))
        if action=='promote':return mod.promote(job.target,str(job.id),values['baseline'])
        if action=='restore':return mod.restore(job.target,str(job.id))
        if action=='probe':
            instance=values['instance'];name=instance['name'];operation=values.get('operation','ready')
            if operation=='load' and name not in failures:loaded[name]=(live/'1/model.plan').read_bytes()
            ok=name not in failures and loaded.get(name)==(live/'1/model.plan').read_bytes()
            return dict(name=name,uid=instance['uid'],ok=ok,detail='fixture')
        raise AssertionError(action)
    monkeypatch.setattr(service,'servers',available);monkeypatch.setattr(service,'remote',remote)
    monkeypatch.setattr(training_runs,'connection',connection);monkeypatch.setattr(training_runs,'bridge',bridge)
    return service,row,calls,failures,live


@pytest.mark.asyncio
async def test_end_to_end_transfer_waits_for_manual_activation_and_rollback(local_flow,test_db):
    service,row,calls,failures,live=local_flow
    await service.transfer(test_db,row)
    assert row.status=='staged' and (live/'1/model.plan').read_bytes()==b'previous'
    assert not any(action in ('probe','promote','restart') for action,_,_ in calls)
    await service.activate(test_db,row)
    assert row.status=='active' and (live/'1/model.plan').read_bytes()==b'engine'
    assert {r['name'] for r in row.inspection['instances'] if r['ok']}=={'one','two'}
    await service.activate(test_db,row,rollback=True)
    assert row.status=='rolled_back' and (live/'1/model.plan').read_bytes()==b'previous'


@pytest.mark.asyncio
async def test_retry_only_loads_failed_instances_and_never_claims_partial_success(local_flow,test_db):
    from fastapi import HTTPException
    service,row,calls,failures,live=local_flow
    await service.transfer(test_db,row);failures.add('two')
    with pytest.raises(HTTPException,match='部分'):await service.activate(test_db,row)
    assert row.status!='active'
    failures.clear();await service.activate(test_db,row)
    assert row.status=='active'
    assert sum(action=='probe' and name=='one' and operation=='load' for action,name,operation in calls)==1
    assert sum(action=='probe' and name=='two' and operation=='load' for action,name,operation in calls)==2


def test_k8s_inventory_uses_owner_references_not_similar_labels(tmp_path,monkeypatch):
    mod=executor();t=target(tmp_path,environment='k8s',containers=[],workload='triton',file_addresses=['10.0.0.1'])
    controller={'metadata':{'uid':'controller','generation':1},'spec':{'replicas':2,'template':{'metadata':{}}},'status':{}}
    def pod(name,owner):return {'metadata':{'name':name,'uid':name,'ownerReferences':[{'uid':owner}]},
        'spec':{'containers':[{'name':'triton-server','image':'triton','command':['tritonserver','--model-repository=/models','--model-control-mode=explicit'],
            'volumeMounts':[{'name':'data','mountPath':'/models'}]}],'volumes':[{'name':'data','nfs':{'server':'10.0.0.1','path':t['repository']}}]},
        'status':{'phase':'Running','containerStatuses':[{'name':'triton-server','imageID':'digest','ready':True}]}}
    def command(args,timeout=60):return json.dumps({'items':[pod('one','controller'),pod('two','controller'),pod('rogue','another')]} if 'pods' in args else controller)
    monkeypatch.setattr(mod,'command',command)
    result=mod.inventory(t)
    assert {i['name'] for i in result['instances']}=={'one','two'}
    assert result['desired']==2


def test_vllm_probe_requires_the_configured_served_model(tmp_path,monkeypatch):
    mod=executor();t=target(tmp_path,runtime='vllm')
    monkeypatch.setattr(mod,'execution',lambda *args:[])
    monkeypatch.setattr(mod,'command',lambda *args,**kwargs:json.dumps(dict(ok=True,body=json.dumps({'data':[{'id':'different-model'}]}))))
    assert not mod.probe(t,dict(name='vector',uid='uid',port=8000,served_model='embedding'))['ok']


def test_nfs4_pseudoroot_mapping_requires_the_actual_nfs4_mount(tmp_path):
    mod=executor();t=target(tmp_path,repository='/data/ai/models',nfs4_roots=['/data'])
    args=['tritonserver','--model-repository=/models','--model-control-mode=explicit']
    mounts=[{'source':'/ai/models','destination':'/models','nfs4':True}]
    assert mod.model_mount(t,args,mounts)['port']==8000
    mounts[0]['nfs4']=False
    with pytest.raises(ValueError,match='挂载'):mod.model_mount(t,args,mounts)


def test_tensorrt_version_comes_from_the_inference_library(monkeypatch):
    import ctypes,ctypes.util
    from types import SimpleNamespace
    mod=importlib.import_module('services.model_output_runner')
    monkeypatch.setattr(ctypes.util,'find_library',lambda name:'libnvinfer.so.10')
    monkeypatch.setattr(ctypes,'CDLL',lambda name:SimpleNamespace(getInferLibVersion=lambda:101401))
    assert mod.tensorrt_version()=='10.14.1'


def test_inference_compatibility_works_without_python_tensorrt_bindings(monkeypatch,tmp_path):
    import ctypes,ctypes.util,subprocess,io
    from contextlib import redirect_stdout
    from types import SimpleNamespace
    mod=executor()
    monkeypatch.setattr(ctypes.util,'find_library',lambda name:'libnvinfer.so.10')
    monkeypatch.setattr(ctypes,'CDLL',lambda name:SimpleNamespace(getInferLibVersion=lambda:101401))
    monkeypatch.setattr(subprocess,'check_output',lambda args:b'Tesla T4\n')
    monkeypatch.setattr(mod,'execution',lambda *args:[])
    def command(args,timeout=60):
        output=io.StringIO()
        with redirect_stdout(output):exec(args[-1],{})
        return output.getvalue()
    monkeypatch.setattr(mod,'command',command)
    assert mod.compatibility(target(tmp_path),{})=={'tensorrt':'10.14.1','gpus':['Tesla T4']}


@pytest.mark.asyncio
async def test_restart_marks_interrupted_jobs_unknown_without_replaying_remote_actions(local_flow,test_db,test_engine,monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    service,row,calls,failures,live=local_flow
    row.status='activating';await test_db.commit()
    monkeypatch.setattr(service,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    await service.start_worker();await test_db.refresh(row)
    assert row.status=='unknown' and not calls
    assert (live/'1/model.plan').read_bytes()==b'previous'


@pytest.mark.asyncio
async def test_another_job_cannot_operate_on_a_busy_or_unknown_model_target(local_flow,test_db,monkeypatch):
    from models.model_deployment import ModelDeployment
    from fastapi import HTTPException
    service,row,calls,failures,live=local_flow
    row.status='unknown';await test_db.commit()
    other=ModelDeployment(id=uuid4(),tenant_id=1,user_id=1,run_id=row.run_id,status='draft',target_key=row.target_key,
        source=row.source,target=row.target,inspection={},logs=[])
    test_db.add(other);await test_db.commit()
    monkeypatch.setattr(service,'schedule',lambda *args:pytest.fail('不能调度重复目标任务'))
    with pytest.raises(HTTPException,match='其他'):await service.action(test_db,other,'transfer')
    await test_db.refresh(other)
    assert other.status=='draft' and not calls


def test_rollback_of_a_new_model_verifies_it_is_absent_from_ready_models(tmp_path,monkeypatch):
    mod=executor();t=target(tmp_path);models=[{'name':'detector','state':'READY'}]
    monkeypatch.setattr(mod,'execution',lambda *args:[])
    def command(args,timeout=60):
        assert '/v2/repository/index' in args[-1]
        return json.dumps(dict(ok=True,body=json.dumps(models)))
    monkeypatch.setattr(mod,'command',command)
    instance=dict(name='triton',uid='uid',port=8000)
    assert not mod.probe(t,instance,absent=True)['ok']
    models.clear();assert mod.probe(t,instance,absent=True)['ok']


@pytest.mark.asyncio
async def test_large_triton_models_restart_and_wait_for_rollout_instead_of_double_loading(local_flow,test_db,monkeypatch):
    service,row,calls,failures,live=local_flow
    await service.transfer(test_db,row)
    row.target={**row.target,'runtime':'triton_vllm','environment':'k8s'}
    controller=dict(uid='workload',kind='StatefulSet',name='big-model')
    row.inspection={**row.inspection,'inventory':{**row.inspection['inventory'],'controller':controller}}
    original=service.remote;token=None;restarts=[]
    async def remote(db,job,action,**values):
        nonlocal token
        if action=='restart':token=values['token'];restarts.append(token);return dict(ok=True)
        if action=='inventory':
            data=await original(db,job,action,**values)
            return {**data,'controller':controller,'rollout_token':token,'rollout_complete':bool(token)}
        if action=='probe':
            assert values.get('operation','ready')=='ready','大模型不能在旧显存占用期间双份加载'
            return dict(name=values['instance']['name'],uid=values['instance']['uid'],ok=True)
        return await original(db,job,action,**values)
    monkeypatch.setattr(service,'remote',remote)
    await service.activate(test_db,row)
    assert row.status=='active' and len(restarts)==1
