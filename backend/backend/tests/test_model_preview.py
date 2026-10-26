"""Upload-only detection preview: no projects, human truth, captions or library writes."""
import json
from uuid import UUID
import pytest
import pytest_asyncio
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select,func,update
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_validation import validation_tables


@pytest.fixture
def pclient(validation_tables,test_engine,test_user,tmp_path,monkeypatch):
    from api.model_preview_routes import router
    from api.validation_routes import router as validation_router
    from database import get_db_session
    from config.settings import settings
    from services.storage import reset_storage
    app=FastAPI();app.include_router(router,prefix='/api/model-previews');app.include_router(validation_router,prefix='/api/validation')
    async def isolated_db():
        async with async_sessionmaker(test_engine,expire_on_commit=False)() as db:yield db
    app.dependency_overrides[get_db_session]=isolated_db
    monkeypatch.setattr(settings,'STORAGE_TYPE','local');monkeypatch.setattr(settings,'STORAGE_LOCAL_PATH',str(tmp_path/'preview-storage'))
    reset_storage()
    with TestClient(app) as client:yield client
    reset_storage()


@pytest_asyncio.fixture
async def detector(pclient,test_db,test_user):
    from models import ModelService
    model=ModelService(tenant_id=test_user.tenant_id,key='preview-det',name='Preview Detector',kind='detection',
        protocol='model_service',base_url='http://detector.test',endpoint='/detect',labels=[],label_mapping={},api_key_env='')
    test_db.add(model);await test_db.commit();return model


def upload(client,headers,content,request,count=1):
    return client.post('/api/model-previews',headers=headers,data={'request':json.dumps(request)},
        files=[('files',(f'input-{i}.jpg',content,'image/jpeg')) for i in range(count)])


@pytest.mark.asyncio
async def test_service_upload_queue_results_and_cleanup(pclient,detector,auth_headers,mock_image_data,test_engine,test_db,monkeypatch):
    from models import LibraryImage,Asset,Annotation,ImageCollection
    from services import validation
    from services.storage import get_storage
    calls=[]
    async def infer(config,content,width,height):
        calls.append((config,content,width,height))
        return {'boxes':[dict(label='new-class',x=1,y=2,width=20,height=30,score=.8)],'warnings':[]}
    monkeypatch.setattr(validation,'infer',infer)
    content=mock_image_data.getvalue()
    response=upload(pclient,auth_headers,content,{'model_id':str(detector.id),'confidence':.4,'iou':.6},2)
    assert response.status_code==201,response.text
    run=response.json();url='/api/model-previews/'+run['id']
    assert run['total']==2 and run['status']=='pending'
    assert pclient.get('/api/validation/sets',headers=auth_headers).json()['total']==0
    assert pclient.get('/api/validation/runs',headers=auth_headers).json()['total']==0
    assert pclient.get('/api/validation/runs/'+run['id'],headers=auth_headers).status_code==404
    for _ in range(2):assert await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    detail=pclient.get(url,headers=auth_headers).json()
    assert detail['status']=='completed' and len(detail['items'])==2
    assert 'map50' not in detail['metrics'] and 'fp' not in detail['metrics']
    assert all(item['truth']==[] and item['evaluation']=={} and item['predictions'][0]['label']=='new-class' for item in detail['items'])
    assert calls[0][0].confidence==.4 and calls[0][0].iou==.6
    assert pclient.get(url+'/items/'+detail['items'][0]['id']+'/image',headers=auth_headers).content==content
    for model in [LibraryImage,Asset,Annotation,ImageCollection]:assert await test_db.scalar(select(func.count()).select_from(model))==0
    assert pclient.get('/api/model-previews',headers=auth_headers).json()['total']==1
    from models.validation import ValidationSet
    dataset=await test_db.get(ValidationSet,UUID(run['set_id']));paths=[sample['blob_path'] for sample in dataset.samples]
    assert all(path.startswith('previews/') for path in paths)
    await test_db.commit()
    assert pclient.delete(url,headers=auth_headers).status_code==204
    assert pclient.get(url,headers=auth_headers).status_code==404
    assert not any([await get_storage().file_exists(path) for path in paths])


@pytest.mark.asyncio
async def test_grounding_preview_labels_and_absent_scores(pclient,detector,auth_headers,mock_image_data,test_engine,test_db,monkeypatch):
    from models import ModelService
    from services import validation
    await test_db.execute(update(ModelService).where(ModelService.id==detector.id).values(endpoint='/detect/grounding',labels=['person']))
    await test_db.commit()
    calls=[]
    async def infer(config,*args):
        calls.append(config)
        return {'boxes':[dict(label='electric bicycle',x=1,y=2,width=20,height=30)],'score_type':'uncalibrated','warnings':['未提供置信度']}
    monkeypatch.setattr(validation,'infer',infer)
    request={'model_id':str(detector.id),'labels':['electric bicycle'],'confidence':.99,'iou':.01}
    response=upload(pclient,auth_headers,mock_image_data.getvalue(),request)
    assert response.status_code==201,response.text
    run=response.json()
    assert run['config']['labels']==['electric bicycle'] and run['model']['score_type']=='uncalibrated'
    assert await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    detail=pclient.get('/api/model-previews/'+run['id'],headers=auth_headers).json()
    assert detail['status']=='completed',detail
    assert len(detail['items'][0]['predictions'])==1 and 'score' not in detail['items'][0]['predictions'][0]
    assert calls[0].labels==['electric bicycle']
    await test_db.refresh(detector)
    assert detector.labels==['person']
    for labels in [[''],['x'*101],['car']*101]:
        assert upload(pclient,auth_headers,mock_image_data.getvalue(),{**request,'labels':labels}).status_code==422


@pytest.mark.asyncio
async def test_invalid_files_or_parameters_leave_no_preview(pclient,detector,auth_headers,mock_image_data,test_db,tmp_path,monkeypatch):
    from models.validation import ValidationSet
    from config.settings import settings
    request={'model_id':str(detector.id)}
    for changes in [{'confidence':2},{'iou':-1},{'model_id':None},{'confidence':float('nan')},{'prompt':'not allowed'}]:
        assert upload(pclient,auth_headers,mock_image_data.getvalue(),{**request,**changes}).status_code==422
    assert upload(pclient,auth_headers,b'invalid',request).status_code==400
    assert upload(pclient,auth_headers,mock_image_data.getvalue(),request,5001).status_code==400
    response=pclient.post('/api/model-previews',headers=auth_headers,data={'request':json.dumps(request)},files=[
        ('files',('valid.jpg',mock_image_data.getvalue(),'image/jpeg')),('files',('broken.jpg',b'broken','image/jpeg'))])
    assert response.status_code==400
    monkeypatch.setattr(settings,'MAX_UPLOAD_SIZE',10)
    assert upload(pclient,auth_headers,mock_image_data.getvalue(),request).status_code==413
    assert await test_db.scalar(select(func.count()).select_from(ValidationSet))==0
    assert not [path for path in (tmp_path/'preview-storage').rglob('*') if path.is_file()]


def test_preview_archive_reports_corrupt_member(pclient,detector,auth_headers,mock_image_data):
    from tests.test_resumable_upload import archive
    data=archive('zip',[('bad.jpg',b'damaged member'),('good.jpg',mock_image_data.getvalue())])
    data=data.replace(b'damaged member',b'Damaged member',1)
    response=pclient.post('/api/model-previews',headers=auth_headers,data={'request':json.dumps({'model_id':str(detector.id)})},
        files=[('files',('images.zip',data,'application/zip'))])
    assert response.status_code==400 and 'bad.jpg' in response.json()['detail'],response.text


@pytest.mark.asyncio
async def test_changed_model_fails_without_inference(pclient,detector,auth_headers,mock_image_data,test_db,test_engine,monkeypatch):
    from models import ModelService
    from services import validation
    async def forbidden(*args):raise AssertionError('stale model invoked')
    monkeypatch.setattr(validation,'infer',forbidden)
    run=upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)}).json()
    await test_db.execute(update(ModelService).where(ModelService.id==detector.id).values(revision=2));await test_db.commit()
    await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    detail=pclient.get('/api/model-previews/'+run['id'],headers=auth_headers,params={'page_size':100}).json()
    assert detail['status']=='failed' and detail['items'][0]['error'] and 'map50' not in detail['metrics']


@pytest.mark.asyncio
async def test_checkpoint_preview_reuses_runner_and_stops(pclient,auth_headers,mock_image_data,test_engine,monkeypatch):
    from services import checkpoint_validation as cp,validation
    stopped=[]
    async def snapshot(db,user,request,*,preview=False):
        assert preview
        return {'source':'checkpoint','id':'a'*64,'revision':1,'name':'best.pt','checkpoint':{**request.model_dump(),'weight_sha256':'a'*64,'image_digest':'sha256:'+'b'*64},'execution':{'id':'owned','active':True,'prepared':False}}
    async def validate(*args,**kwargs):return None
    async def infer(sessions,run,sample):return {'boxes':[]}
    async def stop(db,run):
        stopped.append(run.id);run.model_snapshot={**run.model_snapshot,'execution':{**run.model_snapshot['execution'],'active':False}}
    monkeypatch.setattr(cp,'snapshot',snapshot);monkeypatch.setattr(cp,'validate_context',validate);monkeypatch.setattr(cp,'infer',infer);monkeypatch.setattr(cp,'stop',stop)
    response=upload(pclient,auth_headers,mock_image_data.getvalue(),{'checkpoint':{'kind':'yolo26','weight_path':'/models/best.pt'}})
    assert response.status_code==201,response.text
    run=response.json();await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    detail=pclient.get('/api/model-previews/'+run['id'],headers=auth_headers).json()
    assert detail['model']['source']=='checkpoint' and detail['status']=='completed' and detail['remote_active'] is False
    assert len(stopped)==1 and detail['items'][0]['predictions']==[] and detail['metrics']['coverage']['complete']


@pytest.mark.asyncio
async def test_normal_user_service_preview_but_not_checkpoint(pclient,detector,auth_headers,mock_image_data,test_db,test_user,test_engine,monkeypatch):
    from services import validation
    async def infer(*args):return {'boxes':[],'warnings':[]}
    monkeypatch.setattr(validation,'infer',infer)
    test_user.role='annotator';await test_db.commit()
    response=upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)})
    assert response.status_code==201,response.text
    await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert pclient.get('/api/model-previews/'+response.json()['id'],headers=auth_headers).json()['status']=='completed'
    assert upload(pclient,auth_headers,mock_image_data.getvalue(),{'checkpoint':{'kind':'yolo26','weight_path':'/models/best.pt'}}).status_code==403
    assert pclient.get('/api/model-previews').status_code==401


@pytest.mark.asyncio
async def test_preview_is_scoped_to_creator(pclient,detector,auth_headers,mock_image_data,test_db):
    from models import User
    from services.auth import create_access_token
    run=upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)}).json()
    test_db.add(User(id=55,tenant_id=1,username='other-preview',email='other-preview@test.local',password_hash='unused',role='admin',is_active=True));await test_db.commit()
    headers={'Authorization':'Bearer '+create_access_token({'user_id':55,'tenant_id':1,'username':'other-preview'})}
    url='/api/model-previews/'+run['id']
    assert pclient.get('/api/model-previews',headers=headers).json()['total']==0
    assert pclient.get(url,headers=headers).status_code==404
    assert pclient.post(url+'/cancel',headers=headers).status_code==404
    assert pclient.delete(url,headers=headers).status_code==404


@pytest.mark.asyncio
async def test_failed_preview_retry_preserves_no_truth_metrics(pclient,detector,auth_headers,mock_image_data,test_engine,monkeypatch):
    from services import validation
    calls=[]
    async def infer(*args):
        calls.append(1)
        if len(calls)==1:raise HTTPException(504,'timeout')
        return {'boxes':[],'warnings':[]}
    monkeypatch.setattr(validation,'infer',infer)
    run=upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)}).json();url='/api/model-previews/'+run['id']
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    await validation.process_one(sessions)
    assert pclient.get(url,headers=auth_headers).json()['status']=='failed'
    assert pclient.get(url,headers=auth_headers).json()['items'][0]['error']=='timeout'
    assert pclient.post(url+'/retry',headers=auth_headers).status_code==200
    await validation.process_one(sessions)
    detail=pclient.get(url,headers=auth_headers).json()
    assert detail['status']=='completed' and 'map50' not in detail['metrics']


@pytest.mark.asyncio
async def test_exif_orientation_matches_preview_and_inference(pclient,detector,auth_headers,test_engine,monkeypatch):
    from io import BytesIO
    from PIL import Image
    from services import validation
    output=BytesIO();image=Image.new('RGB',(40,20));exif=Image.Exif();exif[274]=6;image.save(output,format='JPEG',exif=exif)
    async def infer(config,content,width,height):
        with Image.open(BytesIO(content)) as picture:assert picture.size==(20,40) and picture.getexif().get(274,1)==1
        assert (width,height)==(20,40)
        return {'boxes':[],'warnings':[]}
    monkeypatch.setattr(validation,'infer',infer)
    run=upload(pclient,auth_headers,output.getvalue(),{'model_id':str(detector.id)}).json()
    await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    detail=pclient.get('/api/model-previews/'+run['id'],headers=auth_headers).json()
    assert detail['status']=='completed' and (detail['items'][0]['width'],detail['items'][0]['height'])==(20,40)


@pytest.mark.asyncio
async def test_unconfirmed_remote_stop_keeps_preview_and_reservation(pclient,auth_headers,mock_image_data,test_db,monkeypatch):
    from services import checkpoint_validation as cp
    from models.validation import ValidationRun,ValidationSet
    async def snapshot(db,user,request,*,preview=False):
        assert preview
        return {'source':'checkpoint','id':'a'*64,'revision':1,'name':'best.pt','checkpoint':{**request.model_dump(),'weight_sha256':'a'*64,'image_digest':'sha256:'+'b'*64},'execution':{'id':'owned','active':True,'prepared':False}}
    async def stop(db,run):raise HTTPException(503,'not confirmed')
    monkeypatch.setattr(cp,'snapshot',snapshot);monkeypatch.setattr(cp,'stop',stop)
    run=upload(pclient,auth_headers,mock_image_data.getvalue(),{'checkpoint':{'kind':'yolo26','weight_path':'/models/best.pt'}}).json();url='/api/model-previews/'+run['id']
    assert pclient.delete(url,headers=auth_headers).status_code==409
    current=await test_db.get(ValidationRun,UUID(run['id']))
    assert current.model_snapshot['execution']['active'] is True
    dataset=await test_db.get(ValidationSet,current.set_id)
    from services.storage import get_storage
    assert await get_storage().file_exists(dataset.samples[0]['blob_path'])


@pytest.mark.asyncio
async def test_non_detection_or_disabled_models_are_rejected(pclient,detector,auth_headers,mock_image_data,test_db):
    from models import ModelService
    await test_db.execute(update(ModelService).where(ModelService.id==detector.id).values(kind='caption'));await test_db.commit()
    assert upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)}).status_code==400
    await test_db.execute(update(ModelService).where(ModelService.id==detector.id).values(kind='detection',enabled=False));await test_db.commit()
    assert upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)}).status_code==400


def preview_archive(kind,items):
    import io,zipfile,tarfile
    output=io.BytesIO()
    if kind=='zip':
        with zipfile.ZipFile(output,'w') as archive:
            for name,content in items:archive.writestr(name,content)
    else:
        with tarfile.open(fileobj=output,mode='w:gz' if kind in ('tar.gz','tgz') else 'w') as archive:
            for name,content in items:
                entry=tarfile.TarInfo(name);entry.size=len(content);archive.addfile(entry,io.BytesIO(content))
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['zip','tar','tar.gz','tgz'])
async def test_preview_archive_expands_nested_images_without_library(pclient,detector,auth_headers,mock_image_data,test_db,kind):
    from models import LibraryImage,Annotation
    from models.validation import ValidationSet
    content=preview_archive(kind,[(f'images/nested/{i}.jpg',mock_image_data.getvalue()) for i in range(21)]+[('labels/example.txt',b'ignored')])
    response=pclient.post('/api/model-previews',headers=auth_headers,data={'request':json.dumps({'model_id':str(detector.id)})},files={'files':('batch.'+kind,content,'application/octet-stream')})
    assert response.status_code==201,response.text
    run=response.json();assert run['total']==21
    detail=pclient.get('/api/model-previews/'+run['id'],headers=auth_headers,params={'page_size':100}).json()
    assert all('images/nested/' in item['original_name'] for item in detail['items'])
    assert len({item['original_name'] for item in detail['items']})==21
    dataset=await test_db.get(ValidationSet,UUID(run['set_id']))
    assert all(sample['blob_path'].startswith('previews/') for sample in dataset.samples)
    assert await test_db.scalar(select(func.count()).select_from(LibraryImage))==0
    assert await test_db.scalar(select(func.count()).select_from(Annotation))==0


@pytest.mark.asyncio
async def test_preview_folder_keeps_relative_paths_for_same_basenames(pclient,detector,auth_headers,mock_image_data):
    response=pclient.post('/api/model-previews',headers=auth_headers,data={'request':json.dumps({'model_id':str(detector.id)})},files=[
        ('files',('folder/one/a.jpg',mock_image_data.getvalue(),'image/jpeg')),
        ('files',('folder/two/a.jpg',mock_image_data.getvalue(),'image/jpeg'))])
    assert response.status_code==201,response.text
    detail=pclient.get('/api/model-previews/'+response.json()['id'],headers=auth_headers).json()
    assert {item['original_name'] for item in detail['items']}=={'folder/one/a.jpg','folder/two/a.jpg'}


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['traversal','corrupt','empty','broken-image','over-count','symlink','oversize-image','expanded-size'])
async def test_invalid_preview_archives_are_atomic(pclient,detector,auth_headers,mock_image_data,test_db,tmp_path,monkeypatch,case):
    from models.validation import ValidationSet
    from config.settings import settings
    import io,zipfile,stat
    items=[('ok.jpg',mock_image_data.getvalue())]
    if case=='traversal':items.append(('../escape.jpg',mock_image_data.getvalue()))
    if case=='empty':items=[('readme.txt',b'not an image')]
    if case=='broken-image':items.append(('bad.jpg',b'broken'))
    if case=='over-count':items=[(str(i)+'.jpg',mock_image_data.getvalue()) for i in range(5001)]
    if case=='oversize-image':monkeypatch.setattr(settings,'MAX_UPLOAD_SIZE',10)
    if case=='expanded-size':monkeypatch.setattr(settings,'MAX_ARCHIVE_EXPANDED_SIZE',10)
    content=preview_archive('zip',items)
    if case=='corrupt':content=b'not a ZIP'
    if case=='symlink':
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            entry=zipfile.ZipInfo('link.jpg');entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16;archive.writestr(entry,b'outside.jpg')
        content=output.getvalue()
    response=pclient.post('/api/model-previews',headers=auth_headers,data={'request':json.dumps({'model_id':str(detector.id)})},files={'files':('test.zip',content,'application/zip')})
    assert response.status_code in (400,413),response.text
    assert await test_db.scalar(select(func.count()).select_from(ValidationSet))==0
    assert not [path for path in (tmp_path/'preview-storage').rglob('*') if path.is_file()]


@pytest.mark.asyncio
async def test_preview_5000_folder_images_and_paginated_results(pclient,detector,auth_headers,mock_image_data):
    response=pclient.post('/api/model-previews',headers=auth_headers,data={'request':json.dumps({'model_id':str(detector.id)})},
        files=[('files',(f'folder/{i:05}.jpg',mock_image_data.getvalue(),'image/jpeg')) for i in range(5000)])
    assert response.status_code==201,response.text
    run=response.json();assert run['total']==5000
    url='/api/model-previews/'+run['id']
    first=pclient.get(url,headers=auth_headers,params={'page':1,'page_size':20}).json()
    last=pclient.get(url,headers=auth_headers,params={'page':250,'page_size':20}).json()
    assert len(first['items'])==len(last['items'])==20
    assert first['page']==1 and last['page']==250
    assert {item['id'] for item in first['items']}.isdisjoint({item['id'] for item in last['items']})


@pytest.mark.asyncio
async def test_preview_limits_and_upload_size_guard(pclient,detector,auth_headers,mock_image_data,test_db,monkeypatch,tmp_path):
    from config.settings import settings
    from models.validation import ValidationSet
    config=pclient.get('/api/model-previews/config',headers=auth_headers).json()
    assert config['max_images']==5000 and config['max_upload_size']==settings.MAX_ARCHIVE_UPLOAD_SIZE and config['max_expanded_size']==settings.MAX_ARCHIVE_EXPANDED_SIZE
    monkeypatch.setattr(settings,'MAX_ARCHIVE_UPLOAD_SIZE',10)
    assert upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id)}).status_code==413
    assert upload(pclient,auth_headers,b'x'*(17*1024**2),{'model_id':str(detector.id)}).status_code==413
    assert await test_db.scalar(select(func.count()).select_from(ValidationSet))==0
    assert not [path for path in (tmp_path/'preview-storage').rglob('*') if path.is_file()]
