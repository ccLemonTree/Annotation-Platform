"""Local archive and batch operations never connect to business infrastructure."""
import hashlib
import importlib
import json
from pathlib import Path
from uuid import uuid4
import zipfile

import pytest
import pytest_asyncio
from fastapi import HTTPException


def center():
    try:return importlib.import_module('services.distribution_center')
    except ModuleNotFoundError:pytest.fail('独立下发中心尚未实现')


def bundle(path,content=b'weights',name='1/model.plan'):
    manifest=dict(bytes=len(content),gpu_name='Tesla T4',versions={'tensorrt_runtime':'10.14.1'},
        files=[dict(path=name,bytes=len(content),sha256=hashlib.sha256(content).hexdigest())])
    with zipfile.ZipFile(path,'w') as out:
        out.writestr(name,content);out.writestr('bundle.json',json.dumps(manifest))
    return manifest


def test_local_archive_verifies_files_not_just_zip_hash(tmp_path):
    service=center();path=tmp_path/'model.zip';bundle(path)
    assert service.verify_bundle(path)['bytes']==7
    bundle(path,b'other')
    with zipfile.ZipFile(path,'a') as out:out.writestr('unlisted',b'bad')
    with pytest.raises(ValueError):service.verify_bundle(path)


@pytest.mark.parametrize('path',['../escape','/absolute','a/../../escape'])
def test_local_archive_rejects_unsafe_manifest(tmp_path,path):
    service=center();archive=tmp_path/'model.zip';bundle(archive,name=path)
    with pytest.raises(ValueError):service.verify_bundle(archive)


def test_independent_distribution_endpoints_require_admin(client):
    for path in ('releases','targets','sources'):
        assert client.get('/api/model-deployments/'+path).status_code==401


@pytest_asyncio.fixture
async def center_tables(test_engine):
    service=center()
    from models import Base
    async with test_engine.begin() as conn:
        for name in ('training_runs','training_gpu_holds','model_releases','deployment_targets','model_deployments','deployment_leases'):
            await conn.run_sync(lambda c:Base.metadata.tables[name].create(c,checkfirst=True))
    return service


def model_target(server='files'):
    return dict(environment='docker',runtime='triton_plan',file_server=server,control_server=server,
        repository='/data/models',model_name='detector',containers=['triton'])


@pytest.mark.asyncio
async def test_saved_targets_and_model_batch_share_one_immutable_local_release(center_tables,client,auth_headers,test_db,monkeypatch,tmp_path):
    service=center_tables
    from models.training_run import TrainingRun
    from models.model_deployment import ModelRelease
    from services import model_deployments
    async def servers():return [dict(name=n,host=n,user='u',port=22) for n in ('files','files2')]
    monkeypatch.setattr(model_deployments,'servers',servers)
    parent=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='export',kind='model_export',status='completed',
        config={'output_subdir':'result','output':{'format':'tensorrt'}},source={},server={'name':'source'},output_dir='/data/output',inspection={})
    test_db.add(parent);await test_db.commit()
    package=tmp_path/'bundle.zip';manifest=bundle(package)
    release=ModelRelease(id=uuid4(),tenant_id=1,user_id=1,run_id=parent.id,name='detector',version='v1',runtime='triton_plan',status='ready',
        source={},archive={'path':str(package),'bytes':package.stat().st_size,'sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'source_info':manifest},logs=[])
    test_db.add(release);await test_db.commit()
    targets=[]
    for server in ('files','files2'):
        response=client.post('/api/model-deployments/targets',headers=auth_headers,json={'name':server,'kind':'model','target':model_target(server)})
        assert response.status_code==201,response.text
        targets.append(response.json()['id'])
    response=client.post('/api/model-deployments/batches',headers=auth_headers,json={'kind':'model','release_id':str(release.id),'target_ids':targets})
    assert response.status_code==201,response.text
    rows=response.json()['items']
    assert len(rows)==2 and len({r['batch_id'] for r in rows})==1
    assert {r['release_id'] for r in rows}=={str(release.id)}
    assert all(r['status']=='draft' for r in rows)
    assert all(r['source']['kind']=='local_release' for r in rows)
    assert client.get('/api/model-deployments/targets',headers=auth_headers).json()['items'][0]['current']['state']=='unmanaged'


def test_batch_rejects_duplicate_target_ids(client,auth_headers):
    value=str(uuid4())
    response=client.post('/api/model-deployments/batches',headers=auth_headers,json={'kind':'image','image':'repo/service:v2','target_ids':[value,value]})
    assert response.status_code==422,response.text


@pytest.mark.asyncio
async def test_image_targets_do_not_require_model_repository(center_tables,client,auth_headers,monkeypatch):
    from services import model_deployments
    async def servers():return [dict(name='files',host='files',user='u',port=22)]
    monkeypatch.setattr(model_deployments,'servers',servers)
    response=client.post('/api/model-deployments/targets',headers=auth_headers,json={'name':'Kafka worker','kind':'image',
        'target':dict(environment='docker_kafka',control_server='files',containers=['nexusai-worker1','nexusai-worker2'])})
    assert response.status_code==201,response.text
    target=response.json()
    response=client.post('/api/model-deployments/batches',headers=auth_headers,json={'kind':'image','image':'repo/worker:v2','target_ids':[target['id']]})
    assert response.status_code==201,response.text
    assert response.json()['items'][0]['run_id'] is None
    update=client.put('/api/model-deployments/targets/'+target['id'],headers=auth_headers,json={'name':'corrected worker','kind':'image',
        'target':dict(environment='docker_kafka',control_server='files',containers=['nexusai-worker3'])})
    assert update.status_code==200,update.text
    assert update.json()['target']['containers']==['nexusai-worker3']


@pytest.mark.asyncio
async def test_service_leases_prevent_image_and_model_operations_overlapping(center_tables,test_db,test_user):
    service=center_tables
    first=type('Job',(),dict(id=uuid4(),target=dict(model_target(),connections={'files':dict(host='h',port=22)})))()
    second=type('Job',(),dict(id=uuid4(),target=dict(environment='docker',control_server='files',containers=['triton'],connections={'files':dict(host='h',port=22)})))()
    await service.acquire(test_db,first)
    await test_db.commit()
    with pytest.raises(HTTPException) as error:await service.acquire(test_db,second)
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_cross_tenant_target_ids_cannot_be_used_in_batch(center_tables,client,auth_headers):
    response=client.post('/api/model-deployments/batches',headers=auth_headers,json={'kind':'image','image':'repo/worker:v2','target_ids':[str(uuid4())]})
    assert response.status_code==404,response.text


@pytest.mark.asyncio
async def test_archive_is_persistent_and_cannot_be_overwritten_by_duplicate_version(center_tables,client,auth_headers,test_db,test_user,monkeypatch,tmp_path):
    import shutil
    from models.training_run import TrainingRun
    from models.model_deployment import ModelRelease
    from services import model_deployments,training_runs
    from config.settings import settings
    service=center_tables;monkeypatch.setattr(settings,'MODEL_ARCHIVE_PATH',str(tmp_path/'archive'))
    parent=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='export',kind='model_export',status='completed',
        config={'output_subdir':'result','output':{'format':'tensorrt'}},source={},server={'id':'default','name':'source'},output_dir='/data/output',inspection={})
    test_db.add(parent);await test_db.commit()
    package=tmp_path/'remote.zip';bundle(package);scheduled=[]
    monkeypatch.setattr(service,'schedule_archive',lambda row:scheduled.append(row.id))
    request=dict(run_id=str(parent.id),name='detector',version='v1',runtime='triton_plan',artifact_path='model.engine')
    response=client.post('/api/model-deployments/releases',headers=auth_headers,json=request)
    assert response.status_code==201,response.text
    assert response.json()['status']=='archiving' and len(scheduled)==1
    assert client.post('/api/model-deployments/releases',headers=auth_headers,json=request).status_code==409
    row=await service.scoped(test_db,ModelRelease,scheduled[0],1)
    async def remote(db,view,action,**kwargs):
        assert action=='pack'
        return dict(path='/data/output/.model-deployments/'+str(row.id)+'/bundle.zip',bytes=package.stat().st_size,sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    async def connection(*args):return None
    def bridge(server,payload):shutil.copyfile(package,payload['download']['localPath']);return {}
    monkeypatch.setattr(model_deployments,'remote',remote);monkeypatch.setattr(training_runs,'connection',connection);monkeypatch.setattr(training_runs,'bridge',bridge)
    await service.archive_model(test_db,row)
    assert row.status=='ready' and Path(row.archive['path']).is_file()
    assert service.verify_bundle(Path(row.archive['path']))['file_count']==1
    assert client.delete('/api/training/runs/'+str(parent.id),headers=auth_headers).status_code==409


@pytest.mark.asyncio
async def test_batch_partial_failure_does_not_cancel_other_targets(center_tables,test_db,test_user,monkeypatch):
    from models.model_deployment import ModelDeployment
    from services import model_deployments
    service=center_tables;batch=uuid4();rows=[]
    for index in range(3):
        row=ModelDeployment(id=uuid4(),tenant_id=1,user_id=1,kind='image',batch_id=batch,target_key=str(index),status='draft',source={'image':'service:v2'},target={},inspection={},logs=[])
        test_db.add(row);rows.append(row)
    await test_db.commit();calls=[];failed_id=rows[1].id
    async def action(db,row,operation,user):
        calls.append(row.id)
        if row.id==failed_id:raise HTTPException(409,'one target rejected')
        return row
    monkeypatch.setattr(model_deployments,'action',action)
    result=await service.batch_action(test_db,test_user,batch,'transfer')
    assert len(calls)==3 and sum('error' in row for row in result['results'])==1


@pytest.mark.asyncio
async def test_activation_rejected_before_any_mutation_does_not_lock_target_forever(center_tables,test_engine,test_db,test_user,monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models.model_deployment import ModelDeployment,DeploymentLease
    from services import model_deployments
    id=uuid4();row=ModelDeployment(id=id,tenant_id=1,user_id=1,kind='model',target_key='key',status='activating',source={},target={},inspection={},logs=[])
    test_db.add(row);test_db.add(DeploymentLease(resource='resource',job_id=id));await test_db.commit()
    async def activate(*args,**kwargs):raise HTTPException(409,'image baseline changed, no mutation attempted')
    monkeypatch.setattr(model_deployments,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    monkeypatch.setattr(model_deployments,'activate',activate)
    await model_deployments.execute_job(id,1,'activate')
    await test_db.refresh(row)
    assert row.status=='activation_failed'
    assert await test_db.get(DeploymentLease,'resource') is None
