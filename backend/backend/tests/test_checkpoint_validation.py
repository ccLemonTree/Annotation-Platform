"""Offline checkpoint validation uses isolated storage and simulated remote transport."""
from uuid import UUID
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_validation import validation_tables, vclient, ready, create_set


def checkpoint(**changes):
    return {**dict(server_id='default', kind='yolo26', weight_path='/models/best.pt', device='cpu', class_names=[]), **changes}


@pytest.fixture
def remote(monkeypatch):
    from services import checkpoint_validation as service
    calls=[]
    async def probe(db,user,request):
        calls.append(request)
        return dict(source='checkpoint',id='a'*64,revision=1,name='best.pt',
            checkpoint={**request.model_dump(mode='json'),'server':{'id':'default','name':'fixture','revision':0},
            'weight_sha256':'a'*64,'image_digest':'sha256:'+'b'*64},execution={'id':'11111111-1111-4111-8111-111111111111','active':True,'prepared':False})
    monkeypatch.setattr(service,'snapshot',probe)
    return calls


def test_checkpoint_request_is_exclusive_and_validated():
    from schemas.validation import ValidationRunCreate
    from pydantic import ValidationError
    set_id='22222222-2222-4222-8222-222222222222'
    request=ValidationRunCreate(set_id=set_id,checkpoint=checkpoint())
    assert request.model_id is None and request.checkpoint.weight_path=='/models/best.pt'
    for fields in ({}, {'model_id':set_id,'checkpoint':checkpoint()}, {'checkpoint':checkpoint(weight_path='/etc/passwd')},
                   {'checkpoint':checkpoint(kind='swift_sft')}, {'checkpoint':checkpoint(device='0')}):
        with pytest.raises(ValidationError):ValidationRunCreate(set_id=set_id,**fields)


@pytest.mark.asyncio
async def test_checkpoint_creation_and_worker_reuse_metrics(vclient,auth_headers,ready,remote,test_engine,test_db,monkeypatch):
    from services import checkpoint_validation as cp, validation
    from models.validation import ValidationRun
    dataset=create_set(vclient,auth_headers,ready)
    response=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()})
    assert response.status_code==201,response.text
    run=response.json();assert run['model']['source']=='checkpoint' and run['model']['weight_sha256']=='a'*64
    async def infer(sessions,current,sample):
        assert current.model_snapshot['checkpoint']['weight_path']=='/models/best.pt'
        return dict(boxes=[dict(label='person',x=1,y=2,width=10,height=20,score=.95)])
    async def validate(db,current,lock=False):return None
    async def stop(db,current):
        current.model_snapshot={**current.model_snapshot,'execution':{**current.model_snapshot['execution'],'active':False}}
    monkeypatch.setattr(cp,'infer',infer);monkeypatch.setattr(cp,'validate_context',validate);monkeypatch.setattr(cp,'stop',stop)
    assert await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
    assert report['run']['status']=='completed' and report['run']['metrics']['tp']==1
    assert report['run']['metrics']['fp']==report['run']['metrics']['fn']==0
    await test_db.refresh(ready['annotation']);assert ready['annotation'].version==1


@pytest.mark.asyncio
async def test_upload_failure_is_reported_for_the_whole_batch(vclient,auth_headers,ready,remote,test_engine,monkeypatch):
    from uuid import uuid4
    from fastapi import HTTPException
    from models.validation import ValidationSet
    from services import checkpoint_validation as cp,validation
    dataset=create_set(vclient,auth_headers,ready)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    async with sessions() as db:
        row=await db.get(ValidationSet,UUID(dataset['id']))
        row.samples=[row.samples[0],{**row.samples[0],'asset_id':str(uuid4())}]
        row.total=2;await db.commit()
    run=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()}).json()
    message='验证图片包上传失败，尚未启动推理：文件上传超时'
    async def infer(*args):raise HTTPException(504,message)
    async def validate(*args,**kwargs):return None
    async def stop(db,current):
        current.model_snapshot={**current.model_snapshot,'execution':{**current.model_snapshot['execution'],'active':False}}
    monkeypatch.setattr(cp,'infer',infer);monkeypatch.setattr(cp,'validate_context',validate);monkeypatch.setattr(cp,'stop',stop)
    assert await validation.process_one(sessions)
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
    assert report['run']['status']=='failed' and report['run']['error']==message
    assert len(report['items'])==2 and all(item['error']==message for item in report['items'])
    assert not await validation.process_one(sessions)


@pytest.mark.asyncio
async def test_checkpoint_creation_still_requires_admin(vclient,auth_headers,ready,remote,test_db,test_user):
    dataset=create_set(vclient,auth_headers,ready)
    # Authentication and role checks run before any remote probe.
    assert vclient.post('/api/validation/runs',json={'set_id':dataset['id'],'checkpoint':checkpoint()}).status_code==401
    test_user.role='annotator';await test_db.commit()
    assert vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()}).status_code==403
    assert not remote


@pytest.mark.asyncio
async def test_checkpoint_cancel_discards_late_prediction(vclient,auth_headers,ready,remote,test_engine,monkeypatch):
    from services import checkpoint_validation as cp, validation
    dataset=create_set(vclient,auth_headers,ready)
    run=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()}).json()
    async def validate(db,current,lock=False):return None
    async def stop(db,current):
        current.model_snapshot={**current.model_snapshot,'execution':{**current.model_snapshot['execution'],'active':False}}
    async def infer(sessions,current,sample):
        assert vclient.post('/api/validation/runs/'+run['id']+'/cancel',headers=auth_headers).status_code==200
        return dict(boxes=[dict(label='person',x=1,y=2,width=10,height=20,score=.95)])
    monkeypatch.setattr(cp,'infer',infer);monkeypatch.setattr(cp,'validate_context',validate);monkeypatch.setattr(cp,'stop',stop)
    await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
    assert report['run']['status']=='canceled' and not report['run']['remote_active']
    assert report['items'][0]['status']=='canceled' and not report['items'][0]['predictions']


@pytest.mark.asyncio
async def test_failed_stop_retains_reservation_and_prevents_retry(vclient,auth_headers,ready,remote,monkeypatch):
    from services import checkpoint_validation as cp
    from fastapi import HTTPException
    dataset=create_set(vclient,auth_headers,ready)
    run=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()}).json()
    async def stop(db,current):raise HTTPException(503,'timeout')
    async def validate(db,current,lock=False):return None
    monkeypatch.setattr(cp,'stop',stop);monkeypatch.setattr(cp,'validate_context',validate)
    canceled=vclient.post('/api/validation/runs/'+run['id']+'/cancel',headers=auth_headers).json()
    assert canceled['remote_active'] and canceled['status']=='canceled'
    assert vclient.post('/api/validation/runs/'+run['id']+'/retry',headers=auth_headers).status_code==409


@pytest.mark.asyncio
async def test_manual_checkpoint_probes_hash_and_freezes_server(test_db,test_user,monkeypatch):
    from services import checkpoint_validation as cp
    from schemas.validation import CheckpointInput
    async def probe(db,run,action):
        assert action=='probe' and run.model_snapshot['checkpoint']['weight_path']=='/models/best.pt'
        return dict(weight_sha256='c'*64,image_digest='sha256:'+'d'*64)
    monkeypatch.setattr(cp,'remote',probe)
    snapshot=await cp.snapshot(test_db,test_user,CheckpointInput(**checkpoint()))
    assert snapshot['checkpoint']['weight_sha256']=='c'*64 and snapshot['execution']['active']
    assert snapshot['checkpoint']['server']['id']=='default'


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['running','starting','stopping','unknown','draft','completed','failed','canceled'])
async def test_active_training_checkpoint_is_preview_only(status,monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from schemas.validation import CheckpointInput
    from services import checkpoint_validation as cp,training_runs
    user=SimpleNamespace(tenant_id=1)
    training=SimpleNamespace(status=status,output_dir='/runs/owned',kind='yolo26',server={'id':'default'},
        config={'output_subdir':'train','image':'fixture-image'})
    calls=[]
    async def scoped(*args):return training
    async def server(*args):return {'id':'default','name':'fixture','revision':0}
    async def probe(db,run,action):
        calls.append(action)
        assert run.model_snapshot['checkpoint']['image']=='fixture-image'
        return dict(weight_sha256='c'*64,image_digest='sha256:'+'d'*64)
    monkeypatch.setattr(training_runs,'scoped',scoped);monkeypatch.setattr(training_runs,'server_snapshot',server)
    monkeypatch.setattr(cp,'remote',probe)
    request=CheckpointInput(**checkpoint(training_run_id='11111111-1111-4111-8111-111111111111',weight_path='/runs/owned/train/best.pt'))
    if status in ('draft',*training_runs.ACTIVE):
        with pytest.raises(HTTPException) as error:await cp.snapshot(None,user,request)
        assert error.value.status_code==409 and not calls
    if status=='draft':
        with pytest.raises(HTTPException) as error:await cp.snapshot(None,user,request,preview=True)
        assert error.value.status_code==409 and not calls
    else:
        result=await cp.snapshot(None,user,request,preview=True)
        assert result['checkpoint']['weight_sha256']=='c'*64 and calls==['probe']
        for changes in ({'weight_path':'/other/best.pt'},{'kind':'rfdetr_medium','class_names':['fire']},{'server_id':'other'}):
            with pytest.raises(HTTPException) as error:await cp.snapshot(None,user,request.model_copy(update=changes),preview=True)
            assert error.value.status_code==400 and calls==['probe']


def test_predictor_loads_weights_once_for_batch_and_keeps_class_mapping(tmp_path,monkeypatch):
    import json,sys,types
    from PIL import Image
    from services import checkpoint_predict as cp
    root=tmp_path;inputs=root/'input';inputs.mkdir()
    samples=[]
    for i in range(2):
        image=inputs/(str(i)+'.png');Image.new('RGB',(32,24)).save(image)
        samples.append(dict(asset_id=str(i),file=image.name,sha256=cp.sha(image),width=32,height=24))
    (inputs/'manifest.json').write_text(json.dumps(samples))
    (root/'config.json').write_text(json.dumps(dict(kind='yolo26',weight_path='/weights/best.pt',weight_sha256='weight',
        device='cpu',class_names=[],policy=dict(inference_confidence=.001,nms_iou=.45,labels=[],label_mapping={'person':'人'}))))
    original_sha=cp.sha;monkeypatch.setattr(cp,'sha',lambda path:'weight' if str(path).startswith('/weights/') else original_sha(path))
    loaded=[]
    class Tensor:
        def __init__(self,value):self.value=value
        def cpu(self):return self
        def tolist(self):return self.value
    class YOLO:
        names={0:'person'};task='detect'
        def __init__(self,path):loaded.append(path)
        def predict(self,*args,**kwargs):return [types.SimpleNamespace(boxes=types.SimpleNamespace(xyxy=Tensor([[1,2,11,12]]),conf=Tensor([.9]),cls=Tensor([0])))]
    monkeypatch.setitem(sys.modules,'ultralytics',types.SimpleNamespace(YOLO=YOLO))
    cp.predict_batch(root)
    assert len(loaded)==1
    assert all(json.loads((root/'results'/(str(i)+'.json')).read_text())['boxes'][0]['label']=='人' for i in range(2))


@pytest.mark.asyncio
async def test_prepare_transfers_verified_batch_and_persists_attempt(vclient,auth_headers,ready,remote,test_engine,monkeypatch):
    import json,zipfile
    from services import checkpoint_validation as cp,training_runs
    from models.validation import ValidationRun,ValidationSet
    from uuid import uuid4
    dataset=create_set(vclient,auth_headers,ready)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False);actions=[];uploads=[];checks=[]
    async with sessions() as db:
        row=await db.get(ValidationSet,UUID(dataset['id']))
        row.samples=[{**row.samples[0],'asset_id':str(uuid4())} for _ in range(25)]
        row.total=25;await db.commit()
    created=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()}).json()
    async def validate(db,current,lock=False):return None
    async def call(db,current,action,**values):
        actions.append(action)
        if action=='stage':return {'directory':'/owned/validation/attempt'}
        assert action=='start' and len(values['archive_sha256'])==64 and 'from rfdetr import RFDETR' in values['script']
        return {'started':True}
    async def connection(db,run):return None
    def upload(server,payload):
        with zipfile.ZipFile(payload['upload']['localPath']) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            assert len(manifest)==25 and archive.read(manifest[0]['file'])==ready['content']
        uploads.append(payload['upload']['remotePath'])
    monkeypatch.setattr(cp,'validate_context',validate);monkeypatch.setattr(cp,'remote',call)
    monkeypatch.setattr(training_runs,'connection',connection);monkeypatch.setattr(training_runs,'bridge',upload)
    original=cp.active
    async def active(*args,**kwargs):
        checks.append(1)
        return await original(*args,**kwargs)
    monkeypatch.setattr(cp,'active',active);monkeypatch.setattr(cp,'monotonic',lambda:0,raising=False)
    async with sessions() as db:run=await db.get(ValidationRun,UUID(created['id']))
    await cp.prepare(sessions,run)
    async with sessions() as db:
        current=await db.get(ValidationRun,run.id)
        assert current.model_snapshot['execution']['prepared']
    assert actions==['stage','start'] and uploads==['/owned/validation/attempt/input.zip']
    assert len(checks)<=5, 'Preparing each image must not query the run context separately'


@pytest.mark.asyncio
async def test_restart_stops_old_attempt_and_never_relaunches(vclient,auth_headers,ready,remote,test_engine,monkeypatch):
    import asyncio
    import database
    from services import checkpoint_validation as cp,validation
    dataset=create_set(vclient,auth_headers,ready)
    run=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'checkpoint':checkpoint()}).json()
    stopped=[]
    async def stop(db,current):
        stopped.append(str(current.id));current.model_snapshot={**current.model_snapshot,'execution':{**current.model_snapshot['execution'],'active':False}}
    async def idle(sessions):await asyncio.Event().wait()
    monkeypatch.setattr(cp,'stop',stop);monkeypatch.setattr(validation,'run_worker',idle);monkeypatch.setattr(validation,'_worker',None)
    monkeypatch.setattr(database,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    try:
        await validation.start_worker()
        report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
        assert stopped==[run['id']] and report['run']['status']=='failed' and not report['run']['remote_active']
        assert report['items'][0]['status']=='failed'
    finally:await validation.stop_worker()


def test_executor_stop_cleans_only_owned_temporary_inputs(tmp_path,monkeypatch):
    import pathlib,sys,types
    from services import checkpoint_executor as executor
    execution='44444444-4444-4444-8444-444444444444'
    payload=dict(action='stage',run_id='33333333-3333-4333-8333-333333333333',execution=execution,
        config=dict(weight_path='/models/best.pt',weight_sha256='a'*64))
    monkeypatch.setattr(executor,'Path',lambda value:tmp_path if str(value)=='/ai/chen/validation/runs' else pathlib.Path(value))
    monkeypatch.setattr(executor,'state',lambda *args:None)
    monkeypatch.setitem(sys.modules,'fcntl',types.SimpleNamespace(LOCK_EX=2,flock=lambda *args:None))
    root=pathlib.Path(executor.execute(payload)['directory'])
    (root/'input').mkdir();(root/'input'/'sample.img').write_bytes(b'image')
    (root/'input.zip').write_bytes(b'archive');(root/'results').mkdir()
    (root/'results'/'sample.json').write_text('{}')
    payload['action']='stop'
    assert executor.execute(payload)==dict(stopped=True)
    assert not (root/'input').exists() and not (root/'input.zip').exists()
    assert (root/'results'/'sample.json').read_text()=='{}' and (root/'owner.json').exists() and (root/'.canceled').exists()
