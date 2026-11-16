"""K8s contracts use isolated storage and never contact a cluster."""
from tests.test_training_runs import platform_payload
from uuid import uuid4
from types import SimpleNamespace
import pytest
import pytest_asyncio
from pydantic import ValidationError

ROOT='/adb-ssd-mount/ai/chen/training'


def values(**changes):
    return dict(dict(name='集群训练',kind='yolo26',execution_backend='k8s',
        k8s=dict(nodes=['gpu-worker.172.22.132.162','gpu-worker.172.22.132.164'],gpus_per_node=7),
        gpu_uuids=[],dataset_path=ROOT+'/datasets/test/data.yaml',model_path=ROOT+'/yolo.pt',
        output_root=ROOT+'/runs',parameters=dict(batch=42)),**changes)


@pytest_asyncio.fixture(autouse=True)
async def tables(test_engine):
    from models.training_run import TrainingRun,TrainingGpuHold
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c:TrainingRun.__table__.create(c,checkfirst=True))
        await conn.run_sync(lambda c:TrainingGpuHold.__table__.create(c,checkfirst=True))


@pytest.mark.parametrize('kind',['yolo26','rfdetr_medium','rfdetr_2xlarge','wemm'])
def test_k8s_accepts_nodes_without_physical_gpu_uuids(kind):
    from schemas.training_run import TrainingCreate
    request=TrainingCreate(**values(kind=kind,parameters=dict(batch=42) if kind=='yolo26' else {}))
    assert request.k8s.gpus_per_node==7
    assert len(request.k8s.nodes)==2
    assert not request.gpu_uuids


@pytest.mark.parametrize('change',[
    dict(k8s=None),dict(gpu_uuids=['GPU-test']),dict(dataset_path='/data/foreign'),
    dict(output_root='/adb-ssd-mount/ai/other'),dict(model_path='/etc/passwd'),
    dict(snapshot_id=str(uuid4())),dict(mounts=[dict(source='/data/a',target='/data/a')]),
    dict(k8s=dict(nodes=['gpu-worker.a','gpu-worker.a'],gpus_per_node=7)),
    dict(k8s=dict(nodes=['node;command'],gpus_per_node=7)),
    dict(k8s=dict(nodes=['gpu-worker.a'],gpus_per_node=0)),
    dict(parameters=dict(batch=48)),dict(environment=dict(NPROC_PER_NODE='2')),
    dict(dataset_path=ROOT+'/datasets//data.yaml'),dict(output_root=ROOT+'/runs/./other'),
    dict(kind='swift_sft',parameters={}),
])
def test_k8s_rejects_invalid_topology_or_unsafe_inputs(change):
    from schemas.training_run import TrainingCreate
    with pytest.raises(ValidationError):TrainingCreate(**values(**change))


def test_docker_still_requires_gpu_uuids_and_no_k8s_topology():
    from schemas.training_run import TrainingCreate
    with pytest.raises(ValidationError):TrainingCreate(**values(execution_backend='docker',k8s=None))
    with pytest.raises(ValidationError):TrainingCreate(**values(execution_backend='docker',gpu_uuids=['GPU-test']))


def test_wemm_accepts_existing_nfs_dataset_tree_but_other_trainers_do_not():
    from schemas.training_run import TrainingCreate
    dataset='/adb-ssd-mount/ai/chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl'
    assert TrainingCreate(**values(kind='wemm',parameters={},dataset_path=dataset)).dataset_path==dataset
    with pytest.raises(ValidationError):TrainingCreate(**values(dataset_path=dataset))
    with pytest.raises(ValidationError):TrainingCreate(**values(kind='wemm',parameters={},dataset_path='/adb-ssd-mount/ai/chen/foreign/data.jsonl'))


def test_k8s_wemm_api_defaults_match_the_reference_recipe():
    from schemas.training_run import TrainingCreate
    from services.training_config import effective_config
    config=effective_config(TrainingCreate(**values(kind='wemm',parameters={})))
    assert config['parameters']['learning_rate']==2e-5
    assert config['parameters']['save_steps']==1000
    assert config['environment']['IMAGE_MAX_TOKEN_NUM']=='768'


def test_k8s_node_discovery_maps_fields_and_rejects_non_admin(client,auth_headers,test_user,test_db,monkeypatch):
    from services import training_runs
    calls=[]
    def bridge(server,input):
        calls.append((server,input))
        return [dict(name='gpu-worker.a',capacity=7,available=5,ready=True,unschedulable=False,taints=[])]
    monkeypatch.setattr(training_runs,'bridge',bridge)
    response=client.get('/api/training/k8s/nodes',headers=auth_headers)
    assert response.status_code==200
    result=response.json()
    assert result['nodes'][0]['gpu_available']==5
    assert result['output_root']==ROOT+'/runs'
    assert result['wemm_defaults']['environment']['IMAGE_MAX_TOKEN_NUM']=='768'
    assert calls[0][1]['executor']=='k8s_training'
    assert client.get('/api/training/k8s/nodes').status_code==401


@pytest.mark.asyncio
async def test_logs_rejects_node_outside_task(client,auth_headers,test_db,test_user,monkeypatch,platform_payload):
    from services import training_runs
    from schemas.training_run import TrainingCreate
    run=await training_runs.create(test_db,test_user,TrainingCreate(**values(dataset_path='',snapshot_id=platform_payload['snapshot_id'])))
    def no_bridge(*args,**kwargs):raise AssertionError('invalid node must not call SSH')
    monkeypatch.setattr(training_runs,'bridge',no_bridge)
    response=client.get('/api/training/runs/'+str(run.id)+'/logs?node=foreign',headers=auth_headers)
    assert response.status_code==422


@pytest.mark.asyncio
async def test_k8s_create_is_a_draft_and_uses_fixed_cluster(test_db,test_user,monkeypatch,platform_payload):
    from schemas.training_run import TrainingCreate
    from services import training_runs
    def no_bridge(*args,**kwargs):raise AssertionError('draft must not contact SSH')
    monkeypatch.setattr(training_runs,'bridge',no_bridge)
    run=await training_runs.create(test_db,test_user,TrainingCreate(**values(dataset_path='',snapshot_id=platform_payload['snapshot_id'])))
    assert run.status=='draft'
    assert run.server['id']=='jiashan-k8s' and run.server['name']=='嘉善移动'
    assert run.output_dir==ROOT+'/runs/'+str(run.id)
    assert training_runs.reserved_gpus(run)==[]
    await training_runs.launch(test_db,run,test_user)
    assert run.status=='starting'


@pytest.mark.asyncio
async def test_remote_routes_k8s_without_resolving_a_password_connection(monkeypatch):
    from services import training_runs
    seen=[]
    async def no_connection(*args):raise AssertionError('fixed K8s cluster uses ssh-manager')
    def bridge(server,input):seen.append((server,input));return dict(text='rank0')
    monkeypatch.setattr(training_runs,'connection',no_connection)
    monkeypatch.setattr(training_runs,'bridge',bridge)
    run=SimpleNamespace(id=uuid4(),config=values(),server=dict(id='jiashan-k8s',name='嘉善移动'))
    result=await training_runs.remote_direct(None,run,'logs',node='gpu-worker.172.22.132.162')
    assert result['text']=='rank0'
    assert seen[0][0]=='嘉善移动'
    assert seen[0][1]['executor']=='k8s_training'
    assert seen[0][1]['payload']['node']=='gpu-worker.172.22.132.162'


@pytest.mark.asyncio
@pytest.mark.parametrize('format',['onnx','tensorrt'])
async def test_k8s_rfdetr_can_export_on_docker(client,auth_headers,test_db,test_user,monkeypatch,platform_payload,format):
    from schemas.training_run import TrainingCreate
    from services import training_runs
    parent=await training_runs.create(test_db,test_user,TrainingCreate(**values(
        kind='rfdetr_medium',parameters={},dataset_path='',snapshot_id=platform_payload['snapshot_id'])))
    parent_id=str(parent.id)
    await test_db.commit()
    source_id=str(uuid4())
    target_id=str(uuid4()) if format=='tensorrt' else source_id
    async def snapshot(db,id,tenant_id):
        assert tenant_id==test_user.tenant_id and id in (source_id,target_id)
        return dict(id=id,name='Docker '+id,revision=1)
    monkeypatch.setattr(training_runs,'server_snapshot',snapshot)
    source_path='/shared/train/checkpoint_best_total.pth'
    payload=dict(format=format,server_id=target_id,source_path=source_path)
    if format=='tensorrt':payload.update(onnx_server_id=source_id,gpu_uuid='GPU-target')
    response=client.post(f'/api/training/runs/{parent_id}/outputs',headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    job=response.json()
    assert job['status']=='draft' and job['server']['id']==target_id
    assert job['source']['run_id']==parent_id and job['source']['source_server_id']==source_id
    if format=='tensorrt':
        stage=job['config']['onnx_stage']
        assert stage['server']['id']==source_id and stage['config']['model_path']==source_path
        assert stage['config']['gpu_uuids']==[] and job['config']['gpu_uuids']==['GPU-target']
    else:
        assert job['config']['model_path']==source_path and job['config']['gpu_uuids']==[]


@pytest.mark.asyncio
async def test_k8s_analysis_reuses_local_metric_parser(monkeypatch):
    import base64
    from services import training_analysis,training_runs
    async def remote(db,run,action):
        assert action=='metric_files'
        return dict(files=[dict(path='results.csv',data=base64.b64encode(b'epoch,train/loss\n0,2.0\n1,1.0\n').decode())],truncated=False)
    monkeypatch.setattr(training_runs,'remote',remote)
    result=await training_analysis.read_metrics(None,SimpleNamespace(config=values()))
    assert result['metrics'][0]['last']==1.0


@pytest.mark.asyncio
async def test_k8s_analysis_rejects_remote_path_escape(monkeypatch):
    from services import training_analysis,training_runs
    from fastapi import HTTPException
    async def remote(*args):return dict(files=[dict(path='../escape',data='')],truncated=False)
    monkeypatch.setattr(training_runs,'remote',remote)
    with pytest.raises(HTTPException):await training_analysis.read_metrics(None,SimpleNamespace(config=values()))


@pytest.mark.parametrize('kind',['yolo26','rfdetr_medium','rfdetr_2xlarge','wemm'])
@pytest.mark.asyncio
async def test_k8s_platform_snapshot_config_is_tenant_scoped_and_uses_prepared_inputs(kind,test_db,test_user,test_project,monkeypatch):
    from models import ExportSnapshot
    from schemas.training_run import TrainingCreate
    from services import training_runs
    snapshot=ExportSnapshot(project_id=test_project.id,user_id=test_user.id,export_format='infonce' if kind=='wemm' else 'yolo',
        blob_path='isolated.zip',content_hash='a'*64,manifest=dict(labels=['fire','smoke'],approved_only=True,
            assets=[dict(split='train'),dict(split='val')],training=dict(format='infonce',samples=1)))
    test_db.add(snapshot);await test_db.commit()
    request=TrainingCreate(**values(kind=kind,snapshot_id=snapshot.id,dataset_path='',
        selected_labels=None if kind=='wemm' else ['smoke'],parameters=dict(batch=42) if kind=='yolo26' else {}))
    config,source,server=await training_runs.configuration(test_db,test_user,request)
    expected='/output/dataset/train.jsonl' if kind=='wemm' else '/output/prepared-dataset/data.yaml' if kind=='yolo26' else '/output/prepared-dataset'
    assert config['runtime_dataset']==expected
    assert source['sha256']=='a'*64 and source['id']==str(snapshot.id)
    assert server['id']=='jiashan-k8s'
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await training_runs.configuration(test_db,SimpleNamespace(tenant_id=987),request)
    assert error.value.status_code==404


@pytest.mark.asyncio
async def test_k8s_snapshot_bridge_attaches_small_helper_and_start_identity(monkeypatch):
    from services import training_runs
    from services.k8s_training_snapshots import support_code
    from schemas.training_run import TrainingCreate
    from services.training_config import effective_config
    config=effective_config(TrainingCreate(**values(snapshot_id=uuid4(),dataset_path='')))
    run=SimpleNamespace(id=uuid4(),config=config,source=dict(sha256='b'*64),server=dict(id='jiashan-k8s',name='嘉善移动'))
    calls=[]
    monkeypatch.setattr(training_runs,'bridge',lambda server,input:calls.append(input) or {})
    await training_runs.remote_direct(None,run,'snapshot_cache',snapshot_hash='b'*64)
    assert calls[-1]['payload']['snapshot_code']==support_code()
    await training_runs.remote_direct(None,run,'unpack_begin',snapshot_hash='b'*64)
    assert calls[-1]['payload']['snapshot_code']==support_code()
    assert calls[-1]['payload']['snapshot_hash']=='b'*64
    await training_runs.remote_direct(None,run,'start')
    assert calls[-1]['payload']['snapshot_hash']=='b'*64


@pytest.mark.parametrize('cache_hit',[False,True])
@pytest.mark.parametrize('kind',['yolo26','rfdetr_medium','rfdetr_2xlarge','wemm'])
@pytest.mark.asyncio
async def test_k8s_snapshot_start_uploads_to_controller_nfs_and_prepares_before_start(kind,cache_hit,test_db,test_user,test_project,test_engine,monkeypatch,tmp_path):
    import hashlib
    from io import BytesIO
    from pathlib import Path
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models import ExportSnapshot
    from services import training_runs
    from schemas.training_run import TrainingCreate
    raw=b'isolated snapshot fixture';digest=hashlib.sha256(raw).hexdigest()
    snapshot=ExportSnapshot(project_id=test_project.id,user_id=test_user.id,export_format='infonce' if kind=='wemm' else 'yolo',
        blob_path='isolated.zip',content_hash=digest,manifest=dict(labels=['smoke'],approved_only=True,
            assets=[dict(split='train'),dict(split='val')],training=dict(format='infonce',samples=1)))
    test_db.add(snapshot);await test_db.commit()
    request=TrainingCreate(**values(kind=kind,snapshot_id=snapshot.id,dataset_path='',parameters=dict(batch=42) if kind=='yolo26' else {},accept_platform_model_license=True))
    run=await training_runs.create(test_db,test_user,request)
    await training_runs.launch(test_db,run,test_user)
    await test_db.commit()
    calls=[]
    async def remote(db,run,action,**params):
        calls.append(action)
        if action=='precheck':return dict(ok=True,checks=[])
        if action=='snapshot_cache':return dict(hit=cache_hit,imported=False,upload_path='/controller-nfs'+run.output_dir.removeprefix('/adb-ssd-mount/ai')+'/input.zip')
        if action=='unpack_begin':return dict(state='ready')
        if action=='start':return dict(status='running')
        return dict(ok=True)
    def bridge(server,input):
        assert server=='嘉善移动'
        assert input['upload']['remotePath']=='/controller-nfs/chen/training/runs/'+str(run.id)+'/input.zip'
        assert Path(input['upload']['localPath']).read_bytes()==raw
        calls.append('upload');return {}
    class Storage:
        async def get_file(self,key):return BytesIO(raw)
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'bridge',bridge)
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    monkeypatch.setattr(training_runs,'get_storage',lambda:Storage())
    await training_runs.process_start(run.id,async_sessionmaker(test_engine,expire_on_commit=False))
    await test_db.refresh(run)
    assert run.status=='running',run.error
    assert calls==['precheck','stage','snapshot_cache']+([] if cache_hit else ['upload'])+['unpack_begin','start']


@pytest.mark.parametrize('location',['/etc/passwd','/controller/chen/training/runs/other/input.zip','/controller/../chen/training/runs/{id}/input.zip'])
@pytest.mark.asyncio
async def test_k8s_refuses_unsafe_upload_location_before_sftp(location,monkeypatch,tmp_path):
    import hashlib
    from io import BytesIO
    from fastapi import HTTPException
    from services import training_runs
    id=uuid4();raw=b'isolated archive'
    run=SimpleNamespace(id=id,tenant_id=1,config=values(),server=dict(id='jiashan-k8s',name='嘉善移动'),
        output_dir=ROOT+'/runs/'+str(id),source=dict(kind='snapshot',sha256=hashlib.sha256(raw).hexdigest(),blob_path='fixture.zip'))
    async def noop(*args,**kwargs):pass
    async def remote(*args,**kwargs):return dict(hit=False,upload_path=location.format(id=id))
    async def scalars(*args):return SimpleNamespace(all=lambda:[])
    def no_sftp(*args):raise AssertionError('unsafe upload must not contact SFTP')
    class Storage:
        async def get_file(self,key):return BytesIO(raw)
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    monkeypatch.setattr(training_runs,'ensure_starting',noop)
    monkeypatch.setattr(training_runs,'startup_progress',noop)
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'bridge',no_sftp)
    monkeypatch.setattr(training_runs,'get_storage',lambda:Storage())
    with pytest.raises(HTTPException) as error:await training_runs.transfer_cached_snapshot(SimpleNamespace(scalars=scalars),run)
    assert error.value.status_code==502
