"""Repository deployment uses isolated storage/SQLite and never executes SSH."""
import asyncio
import hashlib
import json
from pathlib import Path
import threading
from uuid import UUID,uuid4
import zipfile
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func,select
from sqlalchemy.exc import IntegrityError


@pytest_asyncio.fixture(autouse=True)
async def distribution(test_engine,tmp_path,monkeypatch):
    from models import Base
    from config.settings import settings
    from services import model_deployments
    monkeypatch.setattr(settings,'MODEL_ARCHIVE_PATH',str(tmp_path/'archive'))
    async def servers():return [dict(name=name,host=name,user='u',port=22) for name in ('files','files2')]
    monkeypatch.setattr(model_deployments,'servers',servers)
    async with test_engine.begin() as conn:
        for name in ('training_runs','training_gpu_holds','model_repository_items','model_releases','deployment_targets','model_deployments','deployment_leases'):
            await conn.run_sync(lambda c:Base.metadata.tables[name].create(c,checkfirst=True))


async def repository_model(db,**values):
    from models.model_repository import ModelRepositoryItem
    from services import model_repository
    content=b'isolated engine';name='nested/model.engine'
    fields=dict(id=uuid4(),tenant_id=1,user_id=1,name='detector',version='v1',format='tensorrt',gpu_model='Tesla T4',
        tensorrt_version='10.14.1',entry_file=name,file_size=len(content),files=[dict(path=name,bytes=len(content),sha256=hashlib.sha256(content).hexdigest())],
        source={'kind':'upload'},status='ready')
    row=ModelRepositoryItem(**(fields|values));db.add(row);await db.commit()
    root=model_repository.folder(row)/'files';(root/'nested').mkdir(parents=True);(root/name).write_bytes(content)
    return row


@pytest.mark.asyncio
async def test_twenty_batch_targets_share_one_verified_package(test_db,test_user,monkeypatch):
    from services import model_deployments as service,distribution_center as center
    from types import SimpleNamespace
    model=await repository_model(test_db)
    job=SimpleNamespace(batch_id=uuid4(),tenant_id=1,repository_id=model.id)
    calls=[];pack=center.pack_repository
    def packing(model,path):
        calls.append(path);return pack(model,path)
    monkeypatch.setattr(center,'pack_repository',packing)
    bundles=await asyncio.gather(*(service.batch_repository_bundle(model,job) for _ in range(20)))
    assert len(calls)==1 and len({str(path) for path,receipt in bundles})==1
    path,receipt=bundles[0]
    assert center.sha(path)==receipt['sha256']
    assert await service.batch_repository_bundle(model,job)==bundles[0]
    other=await repository_model(test_db,name='parcel')
    other_job=SimpleNamespace(batch_id=job.batch_id,tenant_id=1,repository_id=other.id)
    other_bundles=await asyncio.gather(*(service.batch_repository_bundle(other,other_job) for _ in range(20)))
    assert len(calls)==2 and other_bundles[0][0]!=path
    path.write_bytes(b'corrupted cache')
    with pytest.raises(HTTPException,match='校验'):await service.batch_repository_bundle(model,job)


def target(client,headers,server='files',runtime='triton_plan'):
    response=client.post('/api/model-deployments/targets',headers=headers,json=dict(name=server,kind='model',target=dict(
        environment='docker',runtime=runtime,file_server=server,control_server=server,repository='/data/models',model_name='detector',containers=['triton'])))
    assert response.status_code==201,response.text
    return response.json()['id']


def batch(client,headers,model,targets):
    return client.post('/api/model-deployments/batches',headers=headers,json=dict(kind='model',repository_id=str(model.id),target_ids=targets))


@pytest.mark.asyncio
async def test_repository_batch_references_model_without_duplicate_release(client,auth_headers,test_db):
    from models.model_deployment import ModelRelease
    model=await repository_model(test_db)
    response=batch(client,auth_headers,model,[target(client,auth_headers),target(client,auth_headers,'files2')])
    assert response.status_code==201,response.text
    rows=response.json()['items'];assert len(rows)==2
    assert all(row['repository_id']==str(model.id) and row['release_id'] is None and row['source']['kind']=='local_repository' for row in rows)
    assert rows[0]['source']['gpu_model']=='Tesla T4' and rows[0]['source']['files']==model.files
    assert await test_db.scalar(select(func.count()).select_from(ModelRelease))==0
    other=await repository_model(test_db,gpu_model='NVIDIA A100')
    assert batch(client,auth_headers,other,[rows[0]['target_id']]).status_code==201


@pytest.mark.parametrize('kind,fields',[
    ('model',{}),('model',{'repository_id':str(uuid4()),'release_id':str(uuid4())}),
    ('image',{'image':'service:v2','repository_id':str(uuid4())}),
    ('model',{'repository_id':str(uuid4()),'image':'service:v2'})])
def test_batch_requires_exactly_one_model_source(client,auth_headers,kind,fields):
    result=client.post('/api/model-deployments/batches',headers=auth_headers,json=dict(kind=kind,target_ids=[str(uuid4())],**fields))
    assert result.status_code==422


@pytest.mark.asyncio
@pytest.mark.parametrize('values,status',[
    ({'status':'importing'},409),({'status':'failed'},409),({'format':'pt'},422),({'format':'onnx'},422),
    ({'gpu_model':''},422),({'tensorrt_version':''},422),({'format':'python'},422)])
async def test_repository_batch_rejects_unready_or_unsupported_models(client,auth_headers,test_db,values,status):
    model=await repository_model(test_db,**values)
    response=batch(client,auth_headers,model,[target(client,auth_headers)])
    assert response.status_code==status,response.text


@pytest.mark.asyncio
async def test_repository_batch_rejects_wrong_runtime_and_other_tenant(client,auth_headers,test_db,test_user):
    model=await repository_model(test_db)
    assert batch(client,auth_headers,model,[target(client,auth_headers,runtime='triton_vllm')]).status_code==422
    from models import Tenant
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.commit()
    test_user.tenant_id=2;await test_db.commit()
    assert batch(client,auth_headers,model,[str(uuid4())]).status_code==404


async def transfer_fixture(client,headers,db,monkeypatch,tmp_path,with_config=False,**runtime):
    from services import model_deployments as service,training_runs
    model=await repository_model(db)
    if with_config:
        from services.model_repository import folder
        content=b'platform: "tensorrt_plan"\n'
        (folder(model)/'files/config.pbtxt').write_bytes(content)
        model.files=[*model.files,dict(path='config.pbtxt',bytes=len(content),sha256=hashlib.sha256(content).hexdigest())]
        model.file_size+=len(content);await db.commit()
    response=batch(client,headers,model,[target(client,headers)])
    assert response.status_code==201,response.text
    row=await service.scoped(db,UUID(response.json()['items'][0]['id']),1)
    row.batch_id=None  # Exercise legacy single-target temporary-package cleanup below.
    uploaded=[]
    async def remote(db,row,action,**kwargs):
        if action=='state':return dict(free_bytes=1024**4,addresses=[],exists=False,fingerprint='old')
        if action=='inventory':return dict(desired=1,instances=[dict(name='triton',uid='one',image_id='image',ready=True)],internal_model_path='/models',controller=None)
        if action=='compatibility':return dict(tensorrt='10.14.1',gpus=['Tesla T4'])|runtime
        if action=='prepare':return dict(path=str(tmp_path/'remote.zip'))
        if action=='receive':
            from services.distribution_center import verify_bundle
            info=verify_bundle(tmp_path/'remote.zip')
            assert hashlib.sha256((tmp_path/'remote.zip').read_bytes()).hexdigest()==kwargs['sha256']
            return info|dict(sha256=kwargs['sha256'])
        raise AssertionError(action)
    def bridge(server,payload):
        uploaded.append(Path(payload['upload']['localPath']))
        (tmp_path/'remote.zip').write_bytes(uploaded[-1].read_bytes())
    monkeypatch.setattr(service,'remote',remote);monkeypatch.setattr(training_runs,'bridge',bridge)
    return service,model,row,uploaded


@pytest.mark.asyncio
async def test_repository_transfer_builds_temporary_verified_bundle(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    await service.transfer(test_db,row)
    assert row.status=='staged' and uploaded and not uploaded[0].parent.exists()
    with zipfile.ZipFile(tmp_path/'remote.zip') as archive:
        assert set(archive.namelist())=={'1/model.plan','bundle.json'}
        assert archive.read('1/model.plan')==b'isolated engine'
        manifest=json.loads(archive.read('bundle.json'))
        assert manifest['gpu_name']=='Tesla T4' and manifest['versions']['tensorrt_runtime']=='10.14.1'
    from services.model_repository import folder
    assert (folder(model)/'files'/model.entry_file).read_bytes()==b'isolated engine'


@pytest.mark.asyncio
async def test_repository_with_config_keeps_target_config_policy(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path,with_config=True)
    await service.transfer(test_db,row)
    assert row.status=='staged' and uploaded
    with zipfile.ZipFile(tmp_path/'remote.zip') as archive:
        assert set(archive.namelist())=={'1/model.plan','bundle.json'}
    from services.model_repository import folder
    assert (folder(model)/'files/config.pbtxt').is_file()


@pytest.mark.asyncio
async def test_twenty_platform_batch_creation_and_transfers_pack_once(client,auth_headers,test_db,monkeypatch,tmp_path):
    from services import model_deployments as service,distribution_center as center
    async def servers():return [dict(name='platform-'+str(i),host='10.0.0.'+str(i+1),port=22,user='u') for i in range(20)]
    monkeypatch.setattr(service,'servers',servers)
    model=await repository_model(test_db)
    ids=[target(client,auth_headers,'platform-'+str(i)) for i in range(20)]
    response=batch(client,auth_headers,model,ids)
    assert response.status_code==201 and len(response.json()['items'])==20
    packed=[];uploads=[];pack=center.pack_repository
    def packing(model,path):packed.append(path);return pack(model,path)
    monkeypatch.setattr(center,'pack_repository',packing)
    async def remote(db,row,action,**kwargs):
        if action=='prepare':return dict(path='/isolated/'+str(row.id)+'.zip')
        if action=='receive':return dict(bytes=model.file_size,sha256=kwargs['sha256'],file_count=1)
        raise AssertionError(action)
    def bridge(server,payload):
        path=Path(payload['upload']['localPath']);assert center.verify_bundle(path)['bytes']==model.file_size
        uploads.append((server,path))
    monkeypatch.setattr(service,'remote',remote);monkeypatch.setattr(service.training_runs,'bridge',bridge)
    for item in response.json()['items']:
        row=await service.scoped(test_db,UUID(item['id']),1)
        await service.transfer_repository(test_db,row)
        assert row.status=='staged'
    assert len(packed)==1 and len(uploads)==20 and len({path for server,path in uploads})==1
    assert len({server for server,path in uploads})==20


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime',[{'gpus':['NVIDIA A100']},{'tensorrt':'10.8.0'}])
async def test_repository_transfer_rejects_gpu_or_trt_mismatch_before_upload(client,auth_headers,test_db,monkeypatch,tmp_path,runtime):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path,**runtime)
    with pytest.raises(HTTPException,match='不匹配'):await service.transfer(test_db,row)
    assert not uploaded


@pytest.mark.asyncio
async def test_repository_corruption_prevents_transfer(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    from services.model_repository import folder
    (folder(model)/'files'/model.entry_file).write_bytes(b'corrupt')
    with pytest.raises(HTTPException,match='校验'):await service.transfer(test_db,row)
    assert not uploaded


@pytest.mark.asyncio
@pytest.mark.parametrize('correction',[{'gpu_model':'NVIDIA A100'},{'tensorrt_version':'10.15.0'}])
async def test_repository_snapshot_changes_cannot_relabel_existing_job(client,auth_headers,test_db,monkeypatch,tmp_path,correction):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    before=dict(row.source)
    await test_db.commit()
    response=client.patch('/api/model-repository/'+str(model.id),headers=auth_headers,
        json=dict(name=model.name,version=model.version)|correction)
    assert response.status_code==200,response.text
    await test_db.refresh(model)
    with pytest.raises(HTTPException,match='已变化'):await service.transfer(test_db,row)
    assert not uploaded and row.source==before


@pytest.mark.asyncio
async def test_repository_labels_can_change_without_invalidating_existing_job(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    before=dict(row.source)
    model.name='renamed';model.version='v2';model.group_name='sam3';model.description='updated note';await test_db.commit()
    await service.transfer(test_db,row)
    assert row.status=='staged' and uploaded and row.source==before


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy',[False,True])
async def test_batch_snapshot_is_enforced_without_breaking_legacy_jobs(client,auth_headers,test_db,monkeypatch,tmp_path,legacy):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    if legacy:row.source={k:v for k,v in row.source.items() if k!='batch_profile'}
    model.batch_profile='fixed:8';await test_db.commit()
    if legacy:
        await service.transfer(test_db,row);assert row.status=='staged' and uploaded
    else:
        with pytest.raises(HTTPException,match='已变化'):await service.transfer(test_db,row)
        assert not uploaded


@pytest.mark.asyncio
async def test_repository_reference_prevents_deleting_source(client,auth_headers,test_db):
    model=await repository_model(test_db)
    assert batch(client,auth_headers,model,[target(client,auth_headers)]).status_code==201
    await test_db.delete(model)
    with pytest.raises(IntegrityError):await test_db.commit()
    await test_db.rollback()


@pytest.mark.asyncio
async def test_repository_change_during_upload_is_rejected_and_temp_removed(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    from services import training_runs,model_repository
    def bridge(server,payload):
        uploaded.append(Path(payload['upload']['localPath']))
        (model_repository.folder(model)/'files'/model.entry_file).write_bytes(b'changed')
    monkeypatch.setattr(training_runs,'bridge',bridge)
    with pytest.raises(HTTPException,match='校验'):await service.transfer(test_db,row)
    assert uploaded and not uploaded[0].parent.exists()


@pytest.mark.asyncio
async def test_cancelled_packing_waits_before_temp_cleanup(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    from services import distribution_center
    pack=distribution_center.pack_repository;started=threading.Event();finish=threading.Event();paths=[]
    def packing(model,path):
        paths.append(path);started.set();assert finish.wait(5) and path.parent.is_dir()
        return pack(model,path)
    monkeypatch.setattr(distribution_center,'pack_repository',packing)
    task=asyncio.create_task(service.transfer(test_db,row))
    assert await asyncio.to_thread(started.wait,5)
    task.cancel();await asyncio.sleep(0);assert paths[0].parent.exists()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not paths[0].parent.exists() and not uploaded


@pytest.mark.asyncio
async def test_cancelled_upload_waits_for_thread_before_temp_cleanup(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    from services import training_runs
    started=threading.Event();finish=threading.Event()
    def bridge(server,payload):
        local=Path(payload['upload']['localPath']);uploaded.append(local);started.set()
        assert finish.wait(5) and local.is_file()
        (tmp_path/'remote.zip').write_bytes(local.read_bytes())
    monkeypatch.setattr(training_runs,'bridge',bridge)
    task=asyncio.create_task(service.transfer(test_db,row))
    assert await asyncio.to_thread(started.wait,5)
    task.cancel();await asyncio.sleep(0)
    assert uploaded[0].is_file()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not uploaded[0].parent.exists()


@pytest.mark.asyncio
async def test_cancelled_uploaded_package_hash_waits_before_temp_cleanup(client,auth_headers,test_db,monkeypatch,tmp_path):
    service,model,row,uploaded=await transfer_fixture(client,auth_headers,test_db,monkeypatch,tmp_path)
    from services import distribution_center
    sha=distribution_center.sha;started=threading.Event();finish=threading.Event();archives=[]
    def hashing(path):
        if path.name=='bundle.zip':
            archives.append(path)
            if len(archives)==2:
                with path.open('rb'):
                    started.set();assert finish.wait(5) and path.is_file()
        return sha(path)
    monkeypatch.setattr(distribution_center,'sha',hashing)
    task=asyncio.create_task(service.transfer(test_db,row))
    assert await asyncio.to_thread(started.wait,5)
    task.cancel();await asyncio.sleep(0);finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not archives[0].parent.exists()
