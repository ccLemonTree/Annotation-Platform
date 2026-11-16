"""Device scene contracts use isolated storage and never query a real platform."""
import json
import threading
from concurrent.futures import Future
from io import BytesIO

import httpx
import pytest
from flask import Flask
from PIL import Image
from sqlalchemy import create_engine, insert

from integrations.smarteye import runtime
from integrations.smarteye.management import get_device, update_device
from integrations.smarteye.management_schema import devices


@pytest.fixture
def scenes(tmp_path, monkeypatch):
    from integrations.smarteye import device_scenes as module
    from integrations.smarteye.api import smart_search
    from schemas.image_ingest import ImageIngestConfig
    engine=create_engine(f'sqlite:///{tmp_path / "scenes.db"}')
    runtime.metadata.create_all(engine)
    from models import Base
    Base.metadata.create_all(engine,tables=[Base.metadata.tables[key] for key in ('tenants','images','image_ingest_settings')])
    monkeypatch.setattr(runtime,'_runtime',runtime.Runtime(engine,tmp_path))
    config=ImageIngestConfig(classification_enabled=True)
    monkeypatch.setattr(module,'config_for',lambda tenant_id:config)
    monkeypatch.setattr(module,'get_platform',lambda plat_id:{'plat_id':plat_id,'platform_name':plat_id,'state':'1'})
    monkeypatch.setattr(module,'list_platforms_internal',lambda:[{'plat_id':key,'state':'1'} for key in ('a','0','1','2')])
    monkeypatch.setattr(smart_search,'login_platform',lambda platform:{'success':True,'token':'fixture'})
    content=BytesIO();Image.new('RGB',(32,24),'blue').save(content,format='JPEG')
    calls=[]
    class Model:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append(kwargs)
            return httpx.Response(200,request=httpx.Request('POST',url),json={
                'choices':[{'message':{'content':json.dumps({'labels':['工厂']})}}]})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:Model())
    monkeypatch.setattr(module,'_slots',threading.BoundedSemaphore(2))
    app=Flask(__name__);module.register_routes(app)
    token=runtime.actor.set({'tenant_id':1,'user_id':1,'role':'admin'})
    def run(selected=None,platform_ids=None,task_id='classify'):
        runtime.get_runtime().create(task_id,'device-scenes',{'status':'pending'},runtime.actor.get())
        runtime.tracked_run(module.run_classification,task_id,selected,platform_ids)
        return runtime.get_runtime().get(task_id)
    yield module,app.test_client(),run,calls
    runtime.actor.reset(token)
    engine.dispose()


def test_worker_uses_shared_model_and_preserves_manual_results(scenes):
    _,_,run,calls=scenes
    update_device(1,'a','auto',{'scene_status':'unclassified','device_name':'工厂 大门'})
    update_device(1,'a','manual',{'scene_labels':['河边'],'scene_source':'manual','scene_revision':3})
    task=run()
    automatic=get_device(1,'a','auto');manual=get_device(1,'a','manual')
    assert task['status']=='completed' and task['processed_count']==1 and task['skipped_count']==1
    assert task['platform_results'][0]['success_count']==1
    from datetime import datetime
    assert datetime.fromisoformat(task['updated_at'])>=datetime.fromisoformat(task['created_at'])
    assert automatic['scene_labels']==['工厂'] and automatic['scene_source']=='auto'
    assert 'representative_blob' not in automatic
    assert calls[0]['json']['messages'][0]['content'][0]['type']=='text'
    assert len(calls[0]['json']['messages'][0]['content'])==1
    assert '交通场景' in calls[0]['json']['messages'][0]['content'][0]['text']
    assert manual['scene_labels']==['河边'] and manual['scene_revision']==3 and len(calls)==1


def test_device_with_capture_uses_image_instead_of_name_and_unreadable_is_not_name_fallback(scenes):
    _,_,run,calls=scenes
    r=runtime.get_runtime()
    path=r.root/'captured.png';Image.new('RGB',(12,12),'red').save(path)
    update_device(1,'a','one',{'device_name':'工厂大门','representative_blob':'captured.png'})
    run()
    content=calls[0]['json']['messages'][0]['content']
    assert any(item['type']=='image_url' for item in content)
    assert get_device(1,'a','one')['scene_basis']=='image'
    path.unlink();calls.clear()
    with r.engine.begin() as conn:
        from integrations.smarteye.device_scenes import queue_device_retry
        queue_device_retry(conn,1,[{'plat_id':'a','device_id':'one'}],runtime.actor.get())
    assert run(task_id='missing')['failed_count']==1 and calls==[]


def test_first_capture_invalidates_cached_name_result(scenes):
    _,_,run,calls=scenes
    r=runtime.get_runtime()
    from models import ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    with r.engine.begin() as conn:conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=1,config=ImageIngestConfig(classification_enabled=True).model_dump()))
    update_device(1,'a','one',{'device_name':'工厂大门'})
    run();assert len(calls)==1
    Image.new('RGB',(12,12),'red').save(r.root/'first.png')
    update_device(1,'a','one',{'representative_blob':'first.png'})
    assert get_device(1,'a','one')['scene_status']=='pending'
    run(task_id='with-capture')
    assert len(calls)==2 and any(item['type']=='image_url' for item in calls[-1]['json']['messages'][0]['content'])


def test_manual_only_batch_persists_skipped_count(scenes):
    _,_,run,calls=scenes
    update_device(1,'a','manual',{'scene_labels':['河边'],'scene_source':'manual'})
    task=run()
    assert task['processed_count']==0 and task['skipped_count']==1 and not calls
    assert task['platform_results'][0]['skipped_count']==1


def test_empty_model_result_is_counted_as_unmatched_not_success(scenes,monkeypatch):
    module,client,run,_=scenes
    update_device(1,'a','empty',{'device_name':'无法确定'})
    async def infer(*args):return []
    monkeypatch.setattr(module,'infer_device_labels',infer)
    task=run()
    assert task['unmatched_count']==1 and task['failed_count']==0
    assert task['platform_results'][0]['success_count']==0
    assert task['platform_results'][0]['unmatched_count']==1


def test_manual_edit_retains_historical_labels_without_allowing_new_unknown_labels(scenes):
    _,client,_,_=scenes
    update_device(1,'a','old',{'scene_labels':['历史旧场景'],'scene_source':'manual','scene_status':'completed'})
    assert client.put('/devices/a/old/scene',json={'scene_labels':['历史旧场景','工厂']}).status_code==200
    assert client.put('/devices/a/old/scene',json={'scene_labels':['随意新场景']}).status_code==400


def test_model_failure_retains_prior_labels_and_snapshot(scenes,monkeypatch):
    module,_,run,_=scenes
    update_device(1,'a','one',{'device_name':'北门设备','scene_status':'completed','scene_labels':['河边'],'scene_source':'auto','representative_blob':'old.jpg'})
    async def fail(*args):raise RuntimeError('unsafe model response')
    monkeypatch.setattr(module,'infer_device_labels',fail)
    task=run()
    device=get_device(1,'a','one')
    assert task['status']=='failed' and device['scene_status']=='failed'
    result=task['platform_results'][0]
    assert result['failed_count']==1 and result['success_count']==0
    assert result['device_results'][0]['device_id']=='one'
    assert result['device_results'][0]['device_name']=='北门设备'
    assert device['scene_labels']==['河边'] and device['representative_blob']=='old.jpg'
    assert 'unsafe' not in device['scene_error']


def test_restart_marks_processing_failed_without_erasing_results(scenes):
    module,_,_,_=scenes
    update_device(1,'a','one',{'scene_status':'processing','scene_labels':['河边'],'representative_blob':'old.jpg'})
    module.recover()
    device=get_device(1,'a','one')
    assert device['scene_status']=='failed' and device['scene_labels']==['河边'] and device['representative_blob']=='old.jpg'


def test_restart_marks_incomplete_representative_and_preserves_manual_result(scenes):
    module,_,_,_=scenes
    update_device(1,'a','manual',{'scene_source':'manual','scene_status':'completed','scene_labels':['河边'],
        'representative_status':'fetching'})
    module.recover()
    device=get_device(1,'a','manual')
    assert device['representative_status']=='failed' and '中断' in device['representative_error']
    assert device['scene_status']=='completed' and device['scene_labels']==['河边']


def test_platform_failure_detail_is_bounded_without_truncating_counts(scenes):
    _,_,run,_=scenes
    for index in range(105):
        update_device(1,'a',str(index),{'device_name':f'设备{index}','representative_blob':'missing.jpg'})
    task=run()
    result=task['platform_results'][0]
    assert result['processed_count']==result['failed_count']==105
    assert len(result['device_results'])==100 and result['success_count']==0


@pytest.mark.parametrize('winner',['manual','new_revision','renamed'])
def test_late_inference_preserves_newer_result_or_snapshot(scenes,monkeypatch,winner):
    module,_,run,_=scenes
    update_device(1,'a','one',{'scene_status':'unclassified','scene_labels':['河边'],'representative_blob':'old.jpg'})
    Image.new('RGB',(12,12),'red').save(runtime.get_runtime().root/'old.jpg')
    async def infer(*args):
        current=get_device(1,'a','one')
        changes={'scene_labels':['山上'],'representative_blob':'new.jpg'}
        if winner=='manual':changes.update(scene_source='manual',scene_status='completed',scene_revision=current['scene_revision']+1)
        elif winner=='new_revision':changes.update(scene_source='auto',scene_status='completed',scene_revision=current['scene_revision']+1)
        else:changes.update(device_name='改名后的设备')
        update_device(1,'a','one',changes)
        return ['工厂']
    monkeypatch.setattr(module,'infer_device_labels',infer)
    monkeypatch.setattr('services.image_scenes.infer_scene_labels',infer)
    run()
    device=get_device(1,'a','one')
    assert device['scene_labels']==([] if winner=='renamed' else ['山上']) and device['representative_blob']=='new.jpg'


def test_all_targets_page_across_platforms_and_isolate_tenants(scenes):
    module,_,_,_=scenes
    with runtime.get_runtime().engine.begin() as conn:
        conn.execute(insert(devices),[{'tenant_id':1,'plat_id':str(i//80),'device_id':str(i),'payload':{}} for i in range(205)]+
                     [{'tenant_id':2,'plat_id':'other','device_id':'private','payload':{}}])
    all_devices=list(module._targets(1))
    assert len(all_devices)==205 and {row['plat_id'] for row in all_devices}=={'0','1','2'}
    assert len(list(module._targets(1,platform_ids=['1'])))==80


def test_failed_retry_spans_pages_and_preserves_other_results(scenes,monkeypatch):
    module,client,_,calls=scenes
    with runtime.get_runtime().engine.begin() as conn:
        conn.execute(insert(devices),[{'tenant_id':1,'plat_id':'a','device_id':f'{i:03}',
            'payload':{'device_name':'工厂','scene_status':'failed','scene_error':'旧错误'}} for i in range(205)])
    for tenant,platform,key,status,source in [(1,'a','done','completed','auto'),(1,'a','manual','failed','manual'),
            (1,'a','new','pending',''),(1,'a','empty','completed','auto'),(1,'1','other','failed',''),(2,'a','private','failed','')]:
        update_device(tenant,platform,key,{'device_name':'工厂','scene_status':status,'scene_source':source,'scene_labels':[]})
    preserved={(tenant,platform,key):get_device(tenant,platform,key) for tenant,platform,key in
        [(1,'a','done'),(1,'a','manual'),(1,'a','new'),(1,'a','empty'),(1,'1','other'),(2,'a','private')]}
    def submit(fn,*args):
        fn(*args);future=Future();future.set_result(None);return future
    monkeypatch.setattr(module._executor,'submit',submit)
    response=client.post('/devices/classify',json={'platform_ids':['a'],'only_failed':True})
    assert response.status_code==200
    task=runtime.get_runtime().get(response.json['data']['task_id'])
    assert task['status']=='completed' and task['processed_count']==205 and task['failed_count']==0
    assert len(calls)==205
    assert all(get_device(1,'a',f'{i:03}')['scene_status']=='completed' for i in range(205))
    for key,value in preserved.items():assert get_device(*key)==value


@pytest.mark.parametrize('value',['true',1,None,[],{}])
def test_failed_retry_requires_boolean(scenes,value):
    _,client,_,_=scenes
    assert client.post('/devices/classify',json={'only_failed':value}).status_code==400


def test_failed_retry_rechecks_selected_device_before_execution(scenes,monkeypatch):
    module,client,_,calls=scenes
    update_device(1,'a','one',{'device_name':'工厂','scene_status':'failed'})
    def submit(fn,*args):
        update_device(1,'a','one',{'scene_status':'completed','scene_labels':['河边']})
        fn(*args);future=Future();future.set_result(None);return future
    monkeypatch.setattr(module._executor,'submit',submit)
    response=client.post('/devices/classify',json={'devices':[{'plat_id':'a','device_id':'one'}],'only_failed':True})
    assert response.status_code==200 and not calls
    assert get_device(1,'a','one')['scene_labels']==['河边']


def test_nonadmin_and_other_tenant_cannot_write_or_submit(scenes):
    _,client,_,_=scenes
    update_device(1,'a','one',{'scene_labels':['河边']})
    token=runtime.actor.set({'tenant_id':1,'user_id':2,'role':'annotator'})
    try:
        assert client.put('/devices/a/one/scene',json={'scene_labels':['工厂']}).status_code==403
        assert client.post('/devices/classify',json={'devices':[{'plat_id':'a','device_id':'one'}]}).status_code==403
    finally:runtime.actor.reset(token)
    token=runtime.actor.set({'tenant_id':2,'user_id':3,'role':'admin'})
    try:
        assert client.put('/devices/a/one/scene',json={'scene_labels':['工厂']}).status_code==404
        assert client.post('/devices/classify',json={'devices':[{'plat_id':'a','device_id':'one'}]}).status_code==404
    finally:runtime.actor.reset(token)
    assert get_device(1,'a','one')['scene_labels']==['河边']


def test_cancelled_before_worker_start_releases_submission_slots(scenes,monkeypatch):
    module,client,_,calls=scenes
    update_device(1,'a','one',{})
    def submit(fn,worker,task_id,*args):
        runtime.get_runtime().cancel(task_id)
        fn(worker,task_id,*args)
        future=Future();future.set_result(None);return future
    monkeypatch.setattr(module._executor,'submit',submit)
    for _ in range(3):
        response=client.post('/devices/classify',json={'devices':[{'plat_id':'a','device_id':'one'}]})
        assert response.status_code==200
        assert runtime.get_runtime().get(response.json['data']['task_id'])['status']=='cancelled'
    assert not calls


def test_all_targets_exclude_disabled_and_deleted_platforms(scenes,monkeypatch):
    module,_,_,_=scenes
    for plat_id in ('active','disabled','deleted'):update_device(1,plat_id,'one',{})
    monkeypatch.setattr(module,'list_platforms_internal',lambda:[{'plat_id':'active','state':'1'},{'plat_id':'disabled','state':'0'}])
    assert list(module._targets(1))==[{'plat_id':'active','device_id':'one'}]
    assert list(module._targets(1,platform_ids=['deleted']))==[]
    monkeypatch.setattr(module,'list_platforms_internal',lambda:[])
    assert list(module._targets(1))==[]


def test_selected_devices_from_disabled_platform_rejected(scenes,monkeypatch):
    module,client,_,_=scenes
    update_device(1,'disabled','one',{})
    monkeypatch.setattr(module,'get_platform',lambda plat_id:{'plat_id':plat_id,'state':'0'})
    assert client.post('/devices/classify',json={'devices':[{'plat_id':'disabled','device_id':'one'}]}).status_code==400


def test_cancel_during_commit_lock_wait_cannot_write_snapshot(scenes,monkeypatch):
    module,_,run,_=scenes
    update_device(1,'a','one',{'scene_labels':['河边'],'representative_blob':'old.jpg'})
    Image.new('RGB',(12,12),'red').save(runtime.get_runtime().root/'old.jpg')
    original_lock=runtime.get_runtime().lock
    armed={'value':False}
    async def infer(*args):
        armed['value']=True
        return ['工厂']
    monkeypatch.setattr(module,'infer_device_labels',infer)
    monkeypatch.setattr('services.image_scenes.infer_scene_labels',infer)
    # Cancel before acquiring the commit lock; cancel itself uses the real lock.
    class Lock:
        def __enter__(self):
            if armed['value']:
                armed['value']=False
                runtime.get_runtime().cancel('classify')
            original_lock.acquire()
        def __exit__(self,*args):original_lock.release()
    monkeypatch.setattr(runtime.get_runtime(),'lock',Lock())
    task=run()
    device=get_device(1,'a','one')
    assert task['status']=='cancelled' and device['scene_labels']==['河边']
    assert device['representative_blob']=='old.jpg'
    assert not list(runtime.get_runtime().root.glob('smarteye/device-scenes/*.jpg'))




def test_preview_preserves_captured_png_content_type(scenes):
    _,client,_,_=scenes
    path=runtime.get_runtime().root/'preview.png'
    Image.new('RGB',(8,8),'blue').save(path,format='PNG')
    update_device(1,'a','one',{'representative_blob':'preview.png'})
    response=client.get('/devices/a/one/preview')
    assert response.status_code==200 and response.mimetype=='image/png'














def test_device_scene_config_requires_login_and_reuses_scene_template(client, auth_headers):
    assert client.get('/api/smarteye/device-scenes/config').status_code == 401
    response = client.get('/api/smarteye/device-scenes/config', headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()['data']['scenes']


def test_manual_device_scene_requires_existing_device(client, auth_headers):
    response = client.put('/api/smarteye/devices/missing/device/scene',
                          headers=auth_headers, json={'scene_labels': ['工厂']})
    assert response.status_code == 404, response.text


def test_manual_scene_is_validated_and_late_inference_cannot_overwrite(client, auth_headers):
    from integrations.smarteye.management import update_device, get_device
    from integrations.smarteye import device_scenes
    update_device(1, 'platform', 'same-id', {'device_name': '设备', 'scene_status': 'processing',
                  'scene_source': 'auto', 'scene_revision': 1})
    invalid = client.put('/api/smarteye/devices/platform/same-id/scene', headers=auth_headers,
                         json={'scene_labels': ['不存在的场景']})
    assert invalid.status_code == 400
    response = client.put('/api/smarteye/devices/platform/same-id/scene', headers=auth_headers,
                          json={'scene_labels': ['工厂']})
    assert response.status_code == 200, response.text
    assert not device_scenes.finish_classification(1, 'platform', 'same-id', 1, ['河边'])
    result = get_device(1, 'platform', 'same-id')
    assert result['scene_labels'] == ['工厂'] and result['scene_source'] == 'manual'


def test_device_preview_is_authenticated_and_scoped(client, auth_headers, tmp_path):
    from integrations.smarteye.management import update_device
    from integrations.smarteye.runtime import get_runtime
    root = get_runtime().root
    root.mkdir(exist_ok=True)
    (root / 'preview.jpg').write_bytes(b'fixture-image')
    update_device(1, 'platform', 'device', {'representative_blob': 'preview.jpg'})
    url = '/api/smarteye/devices/platform/device/preview'
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth_headers).content == b'fixture-image'
    update_device(1, 'platform', 'device', {'representative_blob': '../outside.jpg'})
    assert client.get(url, headers=auth_headers).status_code == 404


def test_name_classification_reuses_completed_device_for_later_batches(scenes):
    _,_,run,calls=scenes
    update_device(1,'a','one',{'device_name':'工厂 大门'})
    run(task_id='first-name')
    run(task_id='second-name')
    assert len(calls)==1
    assert '工厂 大门' in calls[0]['json']['messages'][0]['content'][0]['text']


def test_name_result_and_manual_override_propagate_to_original(scenes):
    from models import Base, LibraryImage
    from sqlalchemy import select
    module,client,run,calls=scenes
    r=runtime.get_runtime()
    Base.metadata.create_all(r.engine,tables=[Base.metadata.tables[key] for key in ('image_collections','image_collection_members')])
    content=BytesIO();Image.new('RGB',(12,12),'red').save(content,format='PNG')
    r.create('capture','device-capture',{'status':'running'},runtime.actor.get())
    r.save_image('capture',r.root/'smarteye/capture/test.png',content.getvalue(),1,
        device_info={'plat_id':'a','device_id':'one','device_name':'工厂大门','platform_name':'A'})
    run()
    with r.engine.connect() as conn:
        assert conn.execute(select(LibraryImage.__table__.c.scene_labels)).scalar_one()==['工厂']
    response=client.put('/devices/a/one/scene',json={'scene_labels':['河边']})
    assert response.status_code==200
    with r.engine.connect() as conn:
        assert conn.execute(select(LibraryImage.__table__.c.scene_labels)).scalar_one()==['河边']


def test_failed_nonperiodic_ingest_is_cached_across_identical_images_until_explicit_retry(scenes,monkeypatch):
    from models import Base,ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    module,_,run,_=scenes
    r=runtime.get_runtime()
    Base.metadata.create_all(r.engine,tables=[Base.metadata.tables[key] for key in ('image_collections','image_collection_members')])
    with r.engine.begin() as conn:
        conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=1,
            config=ImageIngestConfig(classification_enabled=True).model_dump()))
    attempts=[]
    async def fail(*args):attempts.append(1);raise RuntimeError('model failed')
    monkeypatch.setattr('services.image_scenes.infer_scene_labels',fail)
    content=BytesIO();Image.new('RGB',(12,12),'red').save(content,format='PNG')
    for task in ('first','second','third'):
        r.create(task,'ledger',{'status':'running'},runtime.actor.get())
        r.save_image(task,r.root/'smarteye'/task/'test.png',content.getvalue(),1,
            device_info={'plat_id':'a','device_id':'one','device_name':'工厂大门','platform_name':'A'})
        run(task_id='classification-'+task)
    assert len(attempts)==1 and get_device(1,'a','one')['scene_status']=='failed'
    with r.engine.begin() as conn:
        module.queue_device_retry(conn,1,[{'plat_id':'a','device_id':'one'}],runtime.actor.get())
    run(task_id='explicit-retry')
    assert len(attempts)==2


def test_pending_devices_use_one_shared_batch_for_repeated_images(scenes,monkeypatch):
    from models import Base,ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    module,_,_,calls=scenes
    r=runtime.get_runtime()
    Base.metadata.create_all(r.engine,tables=[Base.metadata.tables[key] for key in ('image_collections','image_collection_members')])
    with r.engine.begin() as conn:
        conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=1,
            config=ImageIngestConfig(classification_enabled=True).model_dump()))
    class InlineExecutor:
        def submit(self,fn,*args,**kwargs):
            future=Future()
            try:future.set_result(fn(*args,**kwargs))
            except Exception as exc:future.set_exception(exc);raise
            return future
    monkeypatch.setattr(module,'_executor',InlineExecutor())
    content=BytesIO();Image.new('RGB',(12,12),'red').save(content,format='PNG')
    for task in ('first','second','third'):
        r.create(task,'ledger',{'status':'running'},runtime.actor.get())
        r.save_image(task,r.root/'smarteye'/task/'test.png',content.getvalue(),1,
            device_info={'plat_id':'a','device_id':'one','device_name':'工厂大门','platform_name':'A'})
    module.process_pending();module.process_pending()
    assert len(calls)==1 and len(r.list(kind='device-scenes'))==1


@pytest.mark.asyncio
@pytest.mark.parametrize('manual',[False,True])
async def test_main_image_retry_queues_shared_device_and_visual_worker_excludes_it(client,auth_headers,test_engine,test_db,mock_image_data,manual):
    from tests.test_scene_worker import queued
    from models import LibraryImage
    from sqlalchemy import select,update
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from services.image_scenes import classify_one
    image=queued(client,auth_headers,mock_image_data)
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync:devices.create(sync,checkfirst=True))
        if manual:
            await conn.execute(insert(devices).values(tenant_id=1,plat_id='a',device_id='one',payload={
                'scene_source':'manual','scene_status':'completed','scene_labels':['工厂'],'device_name':'工厂大门'}))
    import uuid
    await test_db.execute(update(LibraryImage).where(LibraryImage.id==uuid.UUID(image['id'])).values(
        device_info={'plat_id':'a','device_id':'one','device_name':'工厂大门'},
        scene_metadata={'basis':'device_name'},scene_status='pending'))
    await test_db.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(_ for _ in ()).throw(AssertionError('visual request forbidden')))) as model:
        assert not await classify_one(async_sessionmaker(test_engine,expire_on_commit=False),model)
    response=client.post('/api/images/scene-classify',headers=auth_headers,json={
        'image_ids':[image['id']],'collection_id':image['collection_id']})
    assert response.status_code==200,response.text
    payload=(await test_db.execute(select(devices.c.payload))).scalar_one()
    assert payload['scene_status']==('completed' if manual else 'pending') and payload['device_name']=='工厂大门'
    row=(await test_db.execute(select(LibraryImage.scene_status,LibraryImage.scene_metadata))).one()
    assert row.scene_status==('completed' if manual else 'unclassified')
    assert row.scene_metadata['basis']=='device_name'
