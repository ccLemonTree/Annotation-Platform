import hashlib
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID,uuid4
import zipfile
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture(autouse=True)
async def repository_tables(test_engine,tmp_path,monkeypatch):
    from models import Base
    from config.settings import settings
    from services import model_repository as service
    monkeypatch.setattr(settings,'MODEL_ARCHIVE_PATH',str(tmp_path/'models'))
    monkeypatch.setattr(service,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    async with test_engine.begin() as connection:
        for name in ('training_runs','training_gpu_holds','model_repository_items'):
            await connection.run_sync(lambda c:Base.metadata.tables[name].create(c,checkfirst=True))


def send(client,headers,format='pt',files=None,**values):
    name={'pt':'best.pt','onnx':'model.onnx','tensorrt':'model.engine'}[format]
    data=dict(name='detector',version='v1',format=format,entry_file=name);data.update(values)
    return client.post('/api/model-repository/upload',headers=headers,data=data,files=files or [('files',(name,b'weights'))])


def test_curl_download_ticket_only_authorizes_selected_model(client,auth_headers):
    first=send(client,auth_headers)
    second=send(client,auth_headers,version='v2')
    assert first.status_code==201 and second.status_code==201
    root='/api/model-repository/'
    model=root+first.json()['id']
    other=root+second.json()['id']
    assert client.post(model+'/curl-download').status_code==401
    issued=client.post(model+'/curl-download',headers=auth_headers)
    assert issued.status_code==200
    assert issued.headers['Cache-Control']=='no-store'
    ticket=issued.json()['ticket']
    cookie={'Cookie':f'repository_download={ticket}'}
    response=client.get(model+'/browser-download',headers=cookie)
    assert response.status_code==200 and response.content==b'weights'
    assert client.get(other+'/browser-download',headers=cookie).status_code==401
    assert client.get(root.rstrip('/'),headers={'Authorization':f'Bearer {ticket}'}).status_code==401


async def run_record(db,kind='yolo26',tenant=1):
    from models.training_run import TrainingRun
    run=TrainingRun(id=uuid4(),tenant_id=tenant,user_id=1,name='training',kind=kind,status='completed',
        config={'output_subdir':'result'},source={},server={'name':'fixture'},output_dir='/isolated/run',inspection={})
    db.add(run);await db.commit();return run


@pytest.mark.asyncio
async def test_base_model_import_uses_its_own_group_and_can_train_in_another_project(client,auth_headers,test_db,test_project,monkeypatch):
    from services import model_repository,training_runs
    from models import ExportSnapshot,Project,TaskType
    from tests.test_training_runs import spec
    run=await run_record(test_db,'wemm')
    run.config={**run.config,'model_path':'/models/WeMM-Embedding-2B'}
    run.source={'project_id':str(test_project.id)}
    project=Project(project_name='另一个项目',tenant_id=test_project.tenant_id,task_type=TaskType.VECTOR)
    test_db.add(project);await test_db.flush()
    snapshot=ExportSnapshot(project_id=project.id,user_id=1,export_format='infonce',blob_path='fixture.zip',
        content_hash='a'*64,manifest={'training':{'format':'infonce','samples':2}})
    test_db.add(snapshot)
    await test_db.commit()
    async def remote(db,run,action,**options):
        assert action=='model_directory' and options['input_kind']=='base'
        return {'files':[dict(path='config.json',bytes=2,sha256=hashlib.sha256(b'{}').hexdigest())]}
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(model_repository,'schedule',lambda row:None)
    response=client.post('/api/model-repository/from-training',headers=auth_headers,json=dict(
        name='测试 · 基础模型',group_name='测试',version='v1',format='huggingface',
        run_id=str(run.id),artifact_path='base/config.json'))
    assert response.status_code==201,response.text
    assert response.json()['name']=='WeMM-Embedding-2B'
    assert response.json()['group_name']=='基础模型 · WeMM-Embedding-2B'
    row=await model_repository.scoped(test_db,UUID(response.json()['id']),test_project.tenant_id)
    row.status='ready';await test_db.commit()
    payload=spec('wemm',model_path='',dataset_path='',snapshot_id=snapshot.id,repository_model_id=row.id)
    response=client.post('/api/training/runs',headers=auth_headers,json=payload.model_dump(mode='json'))
    assert response.status_code==201,response.text
    assert response.json()['source']['project_id']==str(project.id)
    assert response.json()['source']['repository_model_id']==str(row.id)


def source_fixture(monkeypatch,tmp_path,files=None,format='pt',gpu='',**metadata):
    from services import training_runs,model_repository as service
    files=files or {'best.pt':b'checkpoint'}
    rows=[dict(path=name,bytes=len(content),sha256=hashlib.sha256(content).hexdigest()) for name,content in files.items()]
    entry=next(iter(files));scheduled=[]
    async def remote(db,run,action,**values):
        if action=='repository_metadata':return dict(format=format,gpu_model=gpu,tensorrt_version='10.14.1' if gpu else '',entry_file=entry,files=rows,**metadata)
        if action=='artifacts':return dict(files=rows,truncated=False)
        if action=='artifact':return dict(path=run.output_dir+'/result/'+values['path'],**{k:v for k,v in next(r for r in rows if r['path']==values['path']).items() if k!='path'})
        raise AssertionError(action)
    async def connection(*args):return None
    def bridge(server,payload):
        record=payload['download'];name=record['remotePath'].split('/result/')[1]
        Path(record['localPath']).write_bytes(files[name]);return {}
    monkeypatch.setattr(training_runs,'remote',remote);monkeypatch.setattr(training_runs,'connection',connection)
    monkeypatch.setattr(training_runs,'bridge',bridge);monkeypatch.setattr(service,'schedule',lambda row:scheduled.append(row.id))
    return scheduled

def test_repository_requires_authentication(client):
    assert client.get('/api/model-repository').status_code == 401


@pytest.mark.asyncio
async def test_automatic_k8s_checkpoint_import_is_idempotent(test_db,test_user,monkeypatch,tmp_path):
    from sqlalchemy import select
    from models.model_repository import ModelRepositoryItem
    from services import model_repository as service,training_runs
    run=await run_record(test_db,kind='rfdetr_medium')
    run.config={**run.config,'execution_backend':'k8s'}
    await test_db.commit()
    body=b'checkpoint'
    async def remote(db,run,action,**values):
        assert action=='artifact' and values['path']=='best.pth'
        return dict(path='/mounted/result/best.pth',logical_path='/isolated/run/result/best.pth',
            bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    def bridge(server,payload):
        assert payload['download']['remotePath']=='/mounted/result/best.pth'
        Path(payload['download']['localPath']).write_bytes(body)
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'bridge',bridge)
    for _ in range(2):
        service.schedule_automatic(run,'best.pth',test_user.id)
        await asyncio.gather(*list(service._auto_tasks.values()))
        await asyncio.gather(*list(service._tasks.values()))
    rows=(await test_db.scalars(select(ModelRepositoryItem))).all()
    assert len(rows)==1 and rows[0].status=='ready' and rows[0].format=='pt'
    assert (service.folder(rows[0])/'files'/'best.pth').read_bytes()==body


def test_edit_metadata_preserves_files_source_and_download(client,auth_headers):
    original=send(client,auth_headers).json();url='/api/model-repository/'+original['id']
    response=client.patch(url,headers=auth_headers,json=dict(name='  烟火检测  ',version=' v2 ',description='适用于园区摄像头\n测试版本'))
    assert response.status_code==200,response.text
    edited=response.json();assert (edited['name'],edited['version'],edited['description'])==('烟火检测','v2','适用于园区摄像头\n测试版本')
    for key in ('id','tenant_id','user_id','run_id','format','gpu_model','tensorrt_version','entry_file','files','file_size','source','status','created_at'):
        assert edited[key]==original[key]
    assert client.get(url,headers=auth_headers).json()['description']==edited['description']
    assert client.get(url+'/download',headers=auth_headers).content==b'weights'
    assert client.patch(url,headers=auth_headers,json=dict(name='烟火检测',version='v2',description='')).json()['description']==''


@pytest.mark.parametrize('change',[{'name':' '},{'version':' '},{'description':'x'*2001},{'files':[]},{'gpu_model':'A100'},{'format':'onnx'},{'name':'bad\x00name'}])
def test_edit_rejects_invalid_or_immutable_fields(client,auth_headers,change):
    row=send(client,auth_headers).json();url='/api/model-repository/'+row['id']
    assert client.patch(url,headers=auth_headers,json=dict(name='detector',version='v1',description='')|change).status_code==422
    assert client.get(url,headers=auth_headers).json()['name']=='detector'


def test_edit_conflicting_variant_is_atomic(client,auth_headers):
    first=send(client,auth_headers).json();second=send(client,auth_headers,version='v2').json()
    url='/api/model-repository/'+second['id']
    assert client.patch(url,headers=auth_headers,json=dict(name=first['name'],version=first['version'],description='must rollback')).status_code==409
    kept=client.get(url,headers=auth_headers).json();assert kept['version']=='v2' and kept['description']==''


@pytest.mark.asyncio
async def test_edit_auth_tenant_and_importing_guards(client,auth_headers,test_db,test_user):
    from models import Tenant
    from models.model_repository import ModelRepositoryItem
    row=send(client,auth_headers).json();url='/api/model-repository/'+row['id'];values=dict(name='edited',version='v2',description='')
    assert client.patch(url,json=values).status_code==401
    test_user.role='annotator';await test_db.commit()
    assert client.patch(url,headers=auth_headers,json=values).status_code==403
    test_db.add(Tenant(id=2,tenant_name='other'));test_user.role='admin';test_user.tenant_id=2;await test_db.commit()
    assert client.patch(url,headers=auth_headers,json=values).status_code==404
    test_user.tenant_id=1
    record=await test_db.get(ModelRepositoryItem,UUID(row['id']));record.status='importing';await test_db.commit()
    assert client.patch(url,headers=auth_headers,json=values).status_code==409


@pytest.mark.parametrize('format,name',[('pt','best.pt'),('pt','model.pth'),('tensorrt','model.engine'),('tensorrt','model.plan')])
def test_upload_download_and_gpu_variants(client,auth_headers,format,name):
    extra={'gpu_model':'Tesla T4','tensorrt_version':'10.14.1'} if format=='tensorrt' else {}
    response=send(client,auth_headers,format,files=[('files',(name,b'weights'))],entry_file=name,**extra)
    assert response.status_code==201,response.text
    row=response.json();assert row['status']=='ready' and row['file_size']==7 and row['files'][0]['sha256']==hashlib.sha256(b'weights').hexdigest()
    assert 'path' not in row['source'] and row['source']=={'kind':'upload'}
    url='/api/model-repository/'+row['id']
    result=client.get(url+'/download',headers=auth_headers)
    assert result.status_code==200 and result.content==b'weights' and name in result.headers['content-disposition']
    assert client.get(url+'/download').status_code==401
    assert send(client,auth_headers,format,files=[('files',(name,b'other'))],entry_file=name,**extra).status_code==409
    listing=client.get('/api/model-repository',headers=auth_headers).json()
    assert listing['total']==1 and listing['gpu_models']==(['Tesla T4'] if extra else [])
    if extra:
        assert send(client,auth_headers,format,gpu_model='NVIDIA A100').status_code==201
        listing=client.get('/api/model-repository?gpu_model=Tesla%20T4',headers=auth_headers).json()
        assert listing['total']==1 and len(listing['gpu_models'])==2


def test_onnx_companion_paths_roundtrip_in_zip(client,auth_headers,tmp_path):
    response=send(client,auth_headers,'onnx',entry_file='nested/model.onnx',files=[('files',('nested/model.onnx',b'graph')),('files',('nested/model.data',b'tensors'))])
    assert response.status_code==201,response.text
    row=response.json();response=client.get('/api/model-repository/'+row['id']+'/download',headers=auth_headers)
    assert response.status_code==200 and response.headers['content-type']=='application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read('nested/model.onnx')==b'graph' and archive.read('nested/model.data')==b'tensors'
    assert not list((tmp_path/'models').rglob('download-*'))


@pytest.mark.parametrize('name',['../escape.pt','/absolute.pt','a\\bad.pt','C:bad.pt','AUX.pt','model.pt.','bad?.pt','bad\x7f.pt'])
def test_upload_rejects_unsafe_paths_and_leaves_no_files(client,auth_headers,tmp_path,name):
    response=send(client,auth_headers,files=[('files',(name,b'weights'))])
    assert response.status_code==422,response.text
    assert not [p for p in (tmp_path/'models').rglob('*') if p.is_file()]


@pytest.mark.parametrize('files',[
    [('files',('model.onnx',b'a')),('files',('MODEL.ONNX',b'b'))],
    [('files',('model.onnx',b'a')),('files',('model.onnx/data',b'b'))],
    [('files',('other.onnx',b'a'))], [('files',('model.onnx',b''))],
])
def test_upload_rejects_duplicate_missing_entry_and_empty(client,auth_headers,tmp_path,files):
    assert send(client,auth_headers,'onnx',files=files).status_code==422
    assert not [p for p in (tmp_path/'models').rglob('*') if p.is_file()]


def test_gpu_metadata_and_format_are_validated(client,auth_headers):
    assert send(client,auth_headers,'tensorrt').status_code==422
    assert send(client,auth_headers,'pt',gpu_model='Tesla T4').status_code==422
    assert send(client,auth_headers,'onnx',entry_file='model.pt').status_code==422
    assert send(client,auth_headers,'tensorrt',gpu_model='T4\nA100').status_code==422


def test_size_and_disk_failures_clean_staging(client,auth_headers,tmp_path,monkeypatch):
    from config.settings import settings
    from services import model_repository as service
    monkeypatch.setattr(settings,'MAX_ARCHIVE_UPLOAD_SIZE',3)
    assert send(client,auth_headers).status_code==413
    monkeypatch.setattr(settings,'MAX_ARCHIVE_UPLOAD_SIZE',1024)
    monkeypatch.setattr(service.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
    assert send(client,auth_headers).status_code==409
    assert not [p for p in (tmp_path/'models').rglob('*') if p.is_file()]


@pytest.mark.asyncio
async def test_tenant_and_role_boundaries(client,auth_headers,test_db,test_user):
    row=send(client,auth_headers).json();url='/api/model-repository/'+row['id']
    test_user.role='annotator';await test_db.commit()
    assert client.get(url,headers=auth_headers).status_code==403
    assert client.get(url+'/download',headers=auth_headers).status_code==403
    assert send(client,auth_headers).status_code==403
    assert client.get('/api/model-repository/sources',headers=auth_headers).status_code==403
    from models import Tenant
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.commit()
    test_user.tenant_id=2;test_user.role='admin';await test_db.commit()
    assert client.get(url,headers=auth_headers).status_code==404
    assert client.get(url+'/download',headers=auth_headers).status_code==404
    assert client.post(url+'/retry',headers=auth_headers).status_code==404
    assert client.get('/api/model-repository',headers=auth_headers).json()['items']==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['yolo26','rfdetr_medium','rfdetr_2xlarge','model_export'])
async def test_real_training_kinds_import_and_protect_source(client,auth_headers,test_db,monkeypatch,tmp_path,kind):
    from services import model_repository as service
    run=await run_record(test_db,kind);scheduled=source_fixture(monkeypatch,tmp_path)
    sources=client.get('/api/model-repository/sources',headers=auth_headers).json()
    assert sources['items'][0]['id']==str(run.id)
    artifacts=client.get('/api/model-repository/sources/'+str(run.id)+'/artifacts',headers=auth_headers).json()
    assert artifacts['items'][0]['format']=='pt'
    response=client.post('/api/model-repository/from-training',headers=auth_headers,json=dict(name='trained',version='v1',format='pt',run_id=str(run.id),artifact_path='best.pt'))
    assert response.status_code==201,response.text
    row=response.json();assert row['status']=='importing' and scheduled==[UUID(row['id'])]
    assert client.get('/api/model-repository/'+row['id']+'/download',headers=auth_headers).status_code==409
    await service.worker(UUID(row['id']),1)
    ready=client.get('/api/model-repository/'+row['id'],headers=auth_headers).json()
    assert ready['status']=='ready' and ready['source']=={'kind':'training','artifact_path':'best.pt'}
    assert client.get('/api/model-repository/'+row['id']+'/download',headers=auth_headers).content==b'checkpoint'
    assert client.delete('/api/training/runs/'+str(run.id),headers=auth_headers).status_code==409


@pytest.mark.asyncio
async def test_training_gpu_cannot_be_overridden_and_onnx_files_preserved(client,auth_headers,test_db,monkeypatch,tmp_path):
    from services import model_repository as service
    run=await run_record(test_db,'model_export')
    source_fixture(monkeypatch,tmp_path,{'model.engine':b'engine'},format='tensorrt',gpu='Tesla T4')
    request=dict(name='trt',version='v1',format='tensorrt',run_id=str(run.id),artifact_path='model.engine',gpu_model='A100')
    assert client.post('/api/model-repository/from-training',headers=auth_headers,json=request).status_code==422
    request.pop('gpu_model');response=client.post('/api/model-repository/from-training',headers=auth_headers,json=request)
    assert response.status_code==201 and response.json()['gpu_model']=='Tesla T4'
    assert response.json()['tensorrt_version']=='10.14.1'
    onnx_input=dict(name='input',shape=['N',3,576,640])
    source_fixture(monkeypatch,tmp_path,{'model.onnx':b'graph','model.data':b'tensors'},format='onnx',onnx_input=onnx_input)
    response=client.post('/api/model-repository/from-training',headers=auth_headers,json=dict(name='onnx',version='v1',format='onnx',run_id=str(run.id),artifact_path='model.onnx'))
    assert response.status_code==201,response.text
    assert response.json()['source']['onnx_input']==onnx_input
    await service.worker(UUID(response.json()['id']),1)
    models=client.get('/api/model-repository?format=onnx',headers=auth_headers).json()['items']
    assert models[0]['source']['onnx_input']==onnx_input
    result=client.get('/api/model-repository/'+response.json()['id']+'/download',headers=auth_headers)
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:assert set(archive.namelist())=={'model.onnx','model.data'}


@pytest.mark.asyncio
async def test_corrupt_transfer_failure_cleanup_and_retry(client,auth_headers,test_db,monkeypatch,tmp_path):
    from services import model_repository as service,training_runs
    run=await run_record(test_db);source_fixture(monkeypatch,tmp_path)
    request=dict(name='trained',version='v1',format='pt',run_id=str(run.id),artifact_path='best.pt')
    row=client.post('/api/model-repository/from-training',headers=auth_headers,json=request).json()
    good_bridge=training_runs.bridge
    monkeypatch.setattr(training_runs,'bridge',lambda server,payload:Path(payload['download']['localPath']).write_bytes(b'wrong'))
    await service.worker(UUID(row['id']),1)
    failed=client.get('/api/model-repository/'+row['id'],headers=auth_headers).json()
    assert failed['status']=='failed' and 'SHA256' in failed['error']
    assert not (tmp_path/'models/repository/1'/row['id']).exists()
    assert client.post('/api/model-repository/'+row['id']+'/retry',headers=auth_headers).status_code==202
    monkeypatch.setattr(training_runs,'bridge',good_bridge);await service.worker(UUID(row['id']),1)
    assert client.get('/api/model-repository/'+row['id'],headers=auth_headers).json()['status']=='ready'


def test_download_detects_disk_corruption(client,auth_headers,tmp_path):
    row=send(client,auth_headers).json()
    (tmp_path/'models/repository/1'/row['id']/'files/best.pt').write_bytes(b'changed')
    assert client.get('/api/model-repository/'+row['id']+'/download',headers=auth_headers).status_code==409


def test_standard_onnx_manifest_requires_every_companion_and_hash(tmp_path):
    from services.training_executor import repository_metadata
    root=tmp_path/'export';root.mkdir();(root/'model.onnx').write_bytes(b'graph');(root/'model.data').write_bytes(b'tensors')
    rows=[dict(path=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.iterdir()]
    (root/'result.json').write_text(json.dumps(dict(format='onnx',files=rows)))
    assert len(repository_metadata(root,'model.onnx')['files'])==2
    (root/'model.data').write_bytes(b'changed')
    with pytest.raises(ValueError,match='清单'):repository_metadata(root,'model.onnx')
    (root/'model.data').unlink()
    with pytest.raises(ValueError):repository_metadata(root,'model.onnx')
    (root/'result.json').unlink()
    with pytest.raises(ValueError):repository_metadata(root,'model.onnx')


@pytest.mark.asyncio
async def test_completed_merge_archives_complete_model(test_db,test_user,monkeypatch,tmp_path):
    from sqlalchemy import select
    from models.model_repository import ModelRepositoryItem
    from services import model_repository as service,training_executor
    run=await run_record(test_db,kind='model_merge')
    files={'merged/config.json':b'{}','merged/model.safetensors':b'weights','merged/tokenizer.json':b'{}'}
    root=tmp_path/'result';(root/'merged').mkdir(parents=True)
    rows=[]
    for name,body in files.items():
        (root/name).write_bytes(body)
        rows.append(dict(path=name,bytes=len(body),sha256=hashlib.sha256(body).hexdigest()))
    (root/'result.json').write_text(json.dumps(dict(format='merge',files=rows)))
    from services import training_runs
    source_fixture(monkeypatch,tmp_path,files,format='huggingface')
    async def metadata(db,run,action,**kwargs):
        path=kwargs['path']
        if action=='repository_metadata':return training_executor.repository_metadata(root,path)
        row=next(row for row in rows if row['path']==path)
        return dict(row,path=run.output_dir+'/result/'+path)
    monkeypatch.setattr(training_runs,'remote',metadata)
    service.schedule_automatic(run,'merged/config.json',test_user.id)
    await asyncio.gather(*list(service._auto_tasks.values()))
    row=await test_db.scalar(select(ModelRepositoryItem))
    assert row and row.format=='huggingface' and row.status=='importing'
    await test_db.commit()
    await service.worker(row.id,1)
    await test_db.refresh(row)
    assert row.status=='ready'
    assert {file['path'] for file in row.files}==set(files)


@pytest.mark.parametrize('dynamic,batch',[(False,1),(True,'N')])
def test_onnx_repository_shape_from_export_manifest(tmp_path,dynamic,batch):
    from services.training_executor import repository_metadata
    model=tmp_path/'model.onnx';model.write_bytes(b'graph')
    files=[dict(path=model.name,bytes=5,sha256=hashlib.sha256(b'graph').hexdigest())]
    (tmp_path/'result.json').write_text(json.dumps(dict(format='tensorrt',files=files,
        options=dict(dynamic_batch=dynamic,batch=1,opt_batch=8),
        onnx=dict(input_name='input',height=576,width=640))))
    metadata=repository_metadata(tmp_path,model.name)
    assert metadata['onnx_input']==dict(name='input',shape=[batch,3,576,640])
    assert metadata['batch_profile']==''


def test_upload_authenticates_before_parsing_and_preserves_openapi(client,auth_headers,monkeypatch):
    from starlette.requests import Request
    from main import app
    schema=app.openapi()['paths']['/api/model-repository/upload']['post']['requestBody']['content']['multipart/form-data']['schema']
    assert schema['properties']['files']['items']['format']=='binary'
    def forbidden(*args,**kwargs):raise AssertionError('Anonymous body must not be parsed')
    monkeypatch.setattr(Request,'form',forbidden)
    assert client.post('/api/model-repository/upload',content=b'not a multipart body').status_code==401


def test_upload_caps_content_length_and_chunked_body_before_spooling(client,auth_headers,monkeypatch,tmp_path):
    from config.settings import settings
    monkeypatch.setattr(settings,'MAX_ARCHIVE_UPLOAD_SIZE',3)
    assert client.post('/api/model-repository/upload',headers={**auth_headers,'Content-Length':str(2*1024**2)},content=b'x').status_code==413
    payload=b'--test\r\nContent-Disposition: form-data; name="files"; filename="a.pt"\r\n\r\n'+b'x'*(1024**2+4)+b'\r\n--test--\r\n'
    response=client.post('/api/model-repository/upload',headers={**auth_headers,'Content-Type':'multipart/form-data; boundary=test'},content=iter([payload]))
    assert response.status_code==413,response.text
    assert not list((tmp_path/'models').rglob('*'))


@pytest.mark.asyncio
async def test_import_disk_failure_and_restart_recovery(client,auth_headers,test_db,monkeypatch,tmp_path):
    from services import model_repository as service
    run=await run_record(test_db);source_fixture(monkeypatch,tmp_path)
    row=client.post('/api/model-repository/from-training',headers=auth_headers,json=dict(name='trained',version='v1',format='pt',run_id=str(run.id),artifact_path='best.pt')).json()
    monkeypatch.setattr(service.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
    await service.worker(UUID(row['id']),1)
    assert client.get('/api/model-repository/'+row['id'],headers=auth_headers).json()['status']=='failed'
    assert not (tmp_path/'models/repository/1'/row['id']).exists()
    assert client.post('/api/model-repository/'+row['id']+'/retry',headers=auth_headers).status_code==202
    partial=tmp_path/'models/repository/1'/row['id']/'partial';partial.mkdir(parents=True);(partial/'best.pt').write_bytes(b'incomplete')
    await service.start_worker()
    assert client.get('/api/model-repository/'+row['id'],headers=auth_headers).json()['status']=='interrupted'
    assert not partial.parent.exists()


@pytest.mark.asyncio
async def test_import_rejects_cross_tenant_or_unfinished_source(client,auth_headers,test_db,monkeypatch,tmp_path,test_user):
    from models import Tenant
    run=await run_record(test_db);source_fixture(monkeypatch,tmp_path)
    request=dict(name='trained',version='v1',format='pt',run_id=str(run.id),artifact_path='best.pt')
    run.status='running';await test_db.commit()
    assert client.post('/api/model-repository/from-training',headers=auth_headers,json=request).status_code==409
    run.status='completed';test_db.add(Tenant(id=2,tenant_name='other'));await test_db.commit();test_user.tenant_id=2;await test_db.commit()
    assert client.post('/api/model-repository/from-training',headers=auth_headers,json=request).status_code==404


@pytest.mark.asyncio
async def test_upload_cancellation_during_commit_keeps_persisted_model(test_db,test_user,monkeypatch,tmp_path):
    from fastapi import UploadFile
    from models.model_repository import ModelRepositoryItem
    from schemas.model_repository import RepositoryMetadata
    from services import model_repository as service
    from sqlalchemy import select
    started=asyncio.Event();release=asyncio.Event();original=service.persist
    async def delayed(db,row):
        started.set();await release.wait();return await original(db,row)
    monkeypatch.setattr(service,'persist',delayed)
    metadata=RepositoryMetadata(name='cancel',version='v1',format='pt',entry_file='best.pt')
    task=asyncio.create_task(service.upload(test_db,test_user,metadata,[UploadFile(filename='best.pt',file=io.BytesIO(b'weights'))]))
    await started.wait();task.cancel();release.set()
    with pytest.raises(asyncio.CancelledError):await task
    row=await test_db.scalar(select(ModelRepositoryItem).where(ModelRepositoryItem.name=='cancel'))
    assert row.status=='ready' and (service.folder(row)/'files/best.pt').read_bytes()==b'weights'


@pytest.mark.asyncio
async def test_training_submission_schedules_even_when_request_is_canceled(test_db,test_user,monkeypatch,tmp_path):
    from services import model_repository as service
    from schemas.model_repository import RepositoryImport
    run=await run_record(test_db);scheduled=source_fixture(monkeypatch,tmp_path)
    original=service.persist;started=asyncio.Event();release=asyncio.Event()
    async def delayed(db,row):
        started.set();await release.wait();return await original(db,row)
    monkeypatch.setattr(service,'persist',delayed)
    request=RepositoryImport(name='cancel',version='v1',format='pt',run_id=run.id,artifact_path='best.pt')
    task=asyncio.create_task(service.create_import(test_db,test_user,request))
    await started.wait();task.cancel();release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert len(scheduled)==1
    assert (await service.scoped(test_db,scheduled[0],1)).status=='importing'
