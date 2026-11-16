"""Platform/device management uses isolated state and substituted upstream calls."""
import pytest
import threading
from concurrent.futures import Future
from flask import Flask
from sqlalchemy import create_engine
from integrations.smarteye import runtime


@pytest.fixture
def managed(tmp_path, monkeypatch):
    from integrations.smarteye import management as module
    engine=create_engine(f'sqlite:///{tmp_path / "management.db"}')
    runtime.metadata.create_all(engine)
    from models import Base
    Base.metadata.create_all(engine,tables=[Base.metadata.tables[name] for name in ('tenants','image_ingest_settings')])
    monkeypatch.setattr(runtime,'_runtime',runtime.Runtime(engine,tmp_path))
    rows={}
    monkeypatch.setattr(module,'list_platform_configs',lambda:list(rows.values()))
    def write(values,create=False):
        if create and values['plat_id'] in rows:raise ValueError('平台ID已存在')
        rows[values['plat_id']]={**values}
    monkeypatch.setattr(module,'write_platform_config',write)
    monkeypatch.setattr(module,'_batch_slots',threading.BoundedSemaphore(2),raising=False)
    from integrations.smarteye.api import ledger_image_downloader as ledger,ledger_watch_downloader as watch
    monkeypatch.setattr(module,'login_platform',lambda p:{'success':True,'token':'fixture'})
    monkeypatch.setattr(ledger,'_post_with_token',lambda *args,**kwargs:{'code':0,'data':{'records':[],'total':0}})
    monkeypatch.setattr(ledger,'get_all_schedulers',lambda:[])
    monkeypatch.setattr(watch,'get_all_watch_states',lambda:[])
    app=Flask(__name__)
    module.register_routes(app)
    token=runtime.actor.set({'tenant_id':1,'user_id':1,'role':'admin'})
    yield module,app.test_client(),rows
    runtime.actor.reset(token)
    engine.dispose()


def test_platform_credentials_remain_private_and_edits_preserve_blank_password(managed):
    module,client,rows=managed
    payload={'plat_id':'one','platform_name':'平台一','base_url':'http://local.test','username':'user','password':'private'}
    response=client.post('/managed-platforms',json=payload)
    assert response.status_code==200,response.json
    assert 'private' not in str(response.json)
    assert response.json['data']['has_password'] is True
    assert client.put('/managed-platforms/one',json={'platform_name':'新名','password':''}).status_code==200
    assert module.get_platform('one')['password']=='private'
    assert client.delete('/managed-platforms/one').status_code==200
    assert rows['one']['state']=='deleted'
    assert module.get_platform('one') is None


@pytest.mark.parametrize('url',['file:///tmp/a','http://user:secret@local.test','http://local.test/#token'])
def test_management_rejects_unsafe_urls(managed,url):
    _,client,_=managed
    response=client.post('/managed-platforms',json={'plat_id':'one','platform_name':'one','base_url':url})
    assert response.status_code==400


def test_platform_writes_require_admin(managed):
    _,client,_=managed
    token=runtime.actor.set({'tenant_id':1,'user_id':2,'role':'annotator'})
    try:assert client.post('/managed-platforms',json={}).status_code==403
    finally:runtime.actor.reset(token)


def test_platform_scene_counts_are_tenant_scoped(managed):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    for index,status in enumerate(['completed','completed','failed','pending','unclassified']):
        module.update_device(1,'a',str(index),{'scene_status':status,'scene_labels':['工厂'] if status=='completed' else []})
    module.update_device(2,'a','other',{'scene_status':'completed'})
    data=client.get('/managed-platforms').json['data'][0]
    assert (data['device_count'],data['classified_count'],data['scene_failed_count'],data['scene_pending_count'])==(5,2,1,1)


def test_empty_completed_result_is_unmatched_and_not_success(managed):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    module.update_device(1,'a','empty',{'scene_status':'completed','scene_labels':[],'scene_source':'auto'})
    module.update_device(1,'a','hit',{'scene_status':'completed','scene_labels':['工厂'],'scene_source':'auto'})
    page=client.get('/devices',query_string={'scene_status':'unmatched','page_size':1}).json['data']
    assert page['total']==1 and page['items'][0]['device_id']=='empty'
    assert page['items'][0]['scene_status']=='unmatched'
    matched=client.get('/devices',query_string={'scene_status':'completed'}).json['data']
    assert matched['total']==1 and matched['items'][0]['device_id']=='hit'
    platform=client.get('/managed-platforms').json['data'][0]
    assert platform['classified_count']==1 and platform['scene_unmatched_count']==1
    assert module.get_device(1,'a','empty')['scene_status']=='completed'


def test_device_group_filter_runs_before_pagination(managed):
    module,client,rows=managed
    from models import ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    config=ImageIngestConfig(scenes=['道路','路口','河边'],scene_groups=[{'name':'交通场景','scenes':['道路','路口']},{'name':'自然环境','scenes':['河边']}])
    with runtime.get_runtime().engine.begin() as conn:
        conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=1,config=config.model_dump()))
    for index,label in enumerate(config.scenes):module.update_device(1,'a',str(index),{'scene_status':'completed','scene_labels':[label]})
    module.update_device(2,'a','foreign',{'scene_status':'completed','scene_labels':['道路']})
    result=client.get('/devices',query_string={'scene':'@group:交通场景','page_size':1}).json['data']
    assert result['total']==2 and len(result['items'])==1
    assert client.get('/devices',query_string={'scene':'道路'}).json['data']['total']==1
    assert client.get('/devices',query_string={'scene':'@group:不存在'}).json['data']['total']==0


def test_device_scene_multiselect_unions_groups_and_leaves_before_pagination(managed):
    module,client,rows=managed
    from models import ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    config=ImageIngestConfig(scenes=['道路','路口','河边'],scene_groups=[{'name':'交通场景','scenes':['道路','路口']},{'name':'自然环境','scenes':['河边']}])
    with runtime.get_runtime().engine.begin() as conn:
        conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=1,config=config.model_dump()))
    for key,labels in [('road',['道路']),('cross',['路口']),('river',['河边']),('both',['道路','河边']),('other',['历史场景'])]:
        module.update_device(1,'a',key,{'scene_status':'completed','scene_labels':labels})
    module.update_device(2,'a','private',{'scene_status':'completed','scene_labels':['道路','河边']})
    query=[('scene','@group:交通场景'),('scene','河边'),('page_size','1')]
    result=client.get('/devices',query_string=query).json['data']
    assert result['total']==4 and len(result['items'])==1
    second=client.get('/devices',query_string=[*query,('page','2')]).json['data']
    assert second['total']==4 and second['items'][0]['device_id']!=result['items'][0]['device_id']
    assert client.get('/devices',query_string=[('scene','道路'),('scene','河边'),('scene','道路')]).json['data']['total']==3
    assert client.get('/devices',query_string=[('scene','@group:不存在'),('scene','河边')]).json['data']['total']==2
    assert client.get('/devices').json['data']['total']==5


def test_device_scene_all_requires_each_selected_scene_before_pagination(managed,monkeypatch):
    from schemas.image_ingest import ImageIngestConfig
    from services import image_ingest
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    config=ImageIngestConfig(scenes=['道路','低位摄像头','工厂'])
    monkeypatch.setattr(image_ingest,'sync_config',lambda *args:(config,1))
    for key,labels in [('both',['道路','低位摄像头']),('road',['道路']),('low',['低位摄像头']),('other',['工厂'])]:
        module.update_device(1,'a',key,{'scene_labels':labels,'scene_status':'completed'})
    params=[('scene','@group:交通场景'),('scene','低位摄像头'),('scene_match','all'),('page_size','1')]
    data=client.get('/devices',query_string=params).json['data']
    assert data['total']==1 and data['items'][0]['device_id']=='both'
    assert client.get('/devices?scene_match=bad').status_code==400


def test_connection_errors_are_safe_and_device_identity_is_platform_scoped(managed,monkeypatch):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','base_url':'http://local.test','username':'u','password':'secret','state':'1'}
    rows['b']={'plat_id':'b','platform_name':'B','state':'1'}
    monkeypatch.setattr(module,'login_platform',lambda p:{'success':False,'msg':'secret upstream body','error_kind':'timeout'})
    response=client.post('/managed-platforms/a/check')
    assert response.json['data']['connection_status']=='error'
    assert 'secret' not in str(response.json)
    module.update_device(1,'a','same',{'device_name':'A','scene_labels':['工厂'],'scene_source':'manual'})
    module.update_device(1,'b','same',{'device_name':'B'})
    module.update_device(1,'a','same',{'device_name':'更新A'})
    result=client.get('/devices?scene=工厂').json['data']
    assert result['total']==1
    assert result['items'][0]['device_name']=='更新A'
    assert module.get_device(1,'b','same')['device_name']=='B'


def test_sync_retains_platform_errors_and_manual_scene(managed,monkeypatch):
    module,_,rows=managed
    for key in ('a','b'):rows[key]={'plat_id':key,'platform_name':key,'base_url':'http://local.test','state':'1'}
    module.update_device(1,'a','same',{'scene_labels':['工厂'],'scene_source':'manual'})
    def fetch(platform,token=None):
        if platform['plat_id']=='b':raise ValueError('unsafe details')
        return [{'device_id':'same','device_name':'设备'}]
    monkeypatch.setattr(module,'fetch_platform_devices',fetch)
    owner=runtime.actor.get()
    runtime.get_runtime().create('sync','device-sync',{'status':'pending'},owner)
    runtime.tracked_run(module.run_device_sync,'sync',['a','b'])
    task=runtime.get_runtime().get('sync')
    assert task['status']=='failed'
    assert len(task['platform_results'])==2
    assert 'unsafe' not in str(task)
    assert module.get_device(1,'a','same')['scene_source']=='manual'
    assert module.get_device(1,'a','same')['scene_labels']==['工厂']


@pytest.mark.parametrize('outcome',['image','empty','error','cancel','bad_image','wrong_device','bad_url'])
def test_sync_fetches_one_representative_before_queueing_classification(managed,monkeypatch,outcome):
    from io import BytesIO
    from PIL import Image
    from models import ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    from integrations.smarteye.api import ledger_image_downloader as ledger
    import requests
    module,client,rows=managed;r=runtime.get_runtime();logins=[];queries=[];downloads=[]
    rows['a']={'plat_id':'a','platform_name':'平台A','base_url':'http://fixture.test','state':'1'}
    with r.engine.begin() as conn:conn.execute(ImageIngestSettings.__table__.insert().values(
        tenant_id=1,revision=1,config=ImageIngestConfig(classification_enabled=True).model_dump()))
    def login(p):logins.append(p['plat_id']);return {'success':True,'token':'private'}
    monkeypatch.setattr(module,'login_platform',login)
    monkeypatch.setattr(module,'fetch_platform_devices',lambda p,token=None:[{'device_id':key,'device_name':'工厂'+key} for key in ('one','two')])
    if outcome=='empty':
        for key in ('one','two'):module.update_device(1,'a',key,{'device_name':'工厂'+key,'scene_status':'completed','representative_blob':'images/missing.png'})
    def post(base,path,payload,token,**kwargs):
        assert path=='/api/schedule/capture/stat/v1/log'
        assert payload['current']==1 and payload['size']==1 and payload['sort']=='id' and payload['order']=='descending'
        assert payload['model']['success'] is True
        key=payload['model']['deviceId'];queries.append(key)
        assert module.get_device(1,'a',key)['scene_status']!='pending'
        if outcome=='error':raise requests.Timeout('private failure')
        return {'code':0,'data':{'total':0 if outcome=='empty' else 10000,'records':[] if outcome=='empty' else [{'deviceId':'wrong' if outcome=='wrong_device' else key,'success':True,'backPath':'file:///private' if outcome=='bad_url' else '/capture.jpg'}]}}
    monkeypatch.setattr(ledger,'_post_with_token',post)
    content=BytesIO();Image.new('RGB',(12,12),'red').save(content,format='PNG')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_content(self,*args):yield b'not an image' if outcome=='bad_image' else content.getvalue()
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            downloads.append(url)
            if outcome=='cancel':r.cancel('sync-representatives')
            return Response()
    monkeypatch.setattr(requests,'Session',Session)
    r.create('sync-representatives','device-sync',{'status':'pending'},runtime.actor.get())
    runtime.tracked_run(module.run_device_sync,'sync-representatives',['a'])
    task=r.get('sync-representatives')
    if outcome=='cancel':
        assert task['status']=='cancelled' and queries==['one']
        assert not module.get_device(1,'a','one').get('representative_blob')
        assert module.get_device(1,'a','one')['scene_status']!='pending'
        assert module.get_device(1,'a','one')['representative_status']=='failed'
        return
    assert logins==['a'] and queries==['one','two']
    assert task['status']=='completed' and 'private' not in str(task)
    for key in ('one','two'):
        device=module.get_device(1,'a',key)
        assert device['scene_status']=='pending'
        assert device['representative_status']==('ready' if outcome=='image' else 'missing' if outcome=='empty' else 'failed')
        assert bool(device.get('representative_blob'))==(outcome=='image')
    if outcome=='image':
        assert len(downloads)==2 and len(list((r.root/'images').rglob('*.png')))==1
        calls=len(queries);runtime.tracked_run(module.run_device_sync,'sync-representatives',['a'])
        assert len(queries)==calls and len(downloads)==2
        assert r.get('sync-representatives')['platform_results'][0]['image_reused_count']==2
    else:
        assert len(downloads)==(2 if outcome=='bad_image' else 0)
        assert all(row['representative_error'] for row in client.get('/devices').json['data']['items'])


def test_sync_endpoint_creates_durable_task_and_rejects_unknown_platform(managed,monkeypatch):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','base_url':'http://local.test','state':'1'}
    submitted=[]
    def submit(*args):
        submitted.append(args)
        future=Future();future.set_result(None)
        return future
    monkeypatch.setattr(module.executor,'submit',submit)
    response=client.post('/devices/sync',json={'platform_ids':['a']})
    assert response.status_code==200
    task=runtime.get_runtime().get(response.json['data']['task_id'])
    assert task['kind']=='device-sync' and task['status']=='pending'
    assert task['tenant_id']==1 and submitted
    assert client.post('/devices/sync',json={'platform_ids':['missing']}).status_code==400


def test_batch_connection_check_is_admin_only_and_single_active_batch(managed,monkeypatch):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    pending=Future()
    monkeypatch.setattr(module.executor,'submit',lambda *args:pending)
    monkeypatch.setattr(module,'_check_batch_slots',threading.BoundedSemaphore(1),raising=False)
    actor=runtime.actor.set({'tenant_id':1,'user_id':2,'role':'annotator'})
    try:assert client.post('/managed-platforms/check-all').status_code==403
    finally:runtime.actor.reset(actor)
    created=client.post('/managed-platforms/check-all')
    assert created.status_code==200,created.json
    assert created.json['data']['kind']=='platform-check'
    assert created.json['data']['total_count']==1
    assert client.post('/managed-platforms/check-all').status_code==429
    assert client.get('/managed-platforms/check-all').json['data']['task_id']==created.json['data']['task_id']
    pending.set_result(None)


def test_batch_connection_check_includes_disabled_and_isolates_errors(managed,monkeypatch):
    module,client,rows=managed
    for key,state in [('ok','1'),('bad','0'),('unset','1')]:
        rows[key]={'plat_id':key,'platform_name':key,'state':state,'base_url':'http://fixture.test','username':'u','password':'private-secret'}
    rows['unset']['password']=''
    rows['deleted']={'plat_id':'deleted','platform_name':'deleted','state':'deleted'}
    calls=[]
    def login(platform):
        calls.append(platform['plat_id'])
        if platform['plat_id']=='bad':raise RuntimeError('private-secret')
        return {'success':True,'token':'private-token'}
    monkeypatch.setattr(module,'login_platform',login)
    runtime.get_runtime().create('checks','platform-check',{'status':'pending','total_count':3},runtime.actor.get())
    runtime.tracked_run(module.run_platform_checks,'checks',['ok','bad','unset'])
    job=runtime.get_runtime().get('checks')
    assert job['status']=='completed' and job['processed_count']==3
    assert (job['online_count'],job['error_count'],job['unconfigured_count'])==(1,1,1)
    assert set(calls)=={'ok','bad'}
    assert 'private-secret' not in str(job) and 'private-token' not in str(job)
    assert module._status(1,'bad')['connection_status']=='error'
    assert module._status(1,'unset')['connection_status']=='unconfigured'
    assert client.get('/devices').json['data']['total']==0


def test_cancelled_batch_does_not_publish_late_connection_result(managed,monkeypatch):
    module,_,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1','base_url':'http://fixture.test','username':'u','password':'private'}
    def login(platform):
        runtime.get_runtime().cancel('checks-cancel')
        return {'success':True,'token':'private-token'}
    monkeypatch.setattr(module,'login_platform',login)
    runtime.get_runtime().create('checks-cancel','platform-check',{'status':'pending'},runtime.actor.get())
    runtime.tracked_run(module.run_platform_checks,'checks-cancel',['a'])
    assert runtime.get_runtime().get('checks-cancel')['status']=='cancelled'
    assert module._status(1,'a')=={}


def test_batch_connection_check_has_two_request_limit_and_latest_is_tenant_scoped(managed,monkeypatch):
    module,client,rows=managed
    for key in ('a','b','c','d'):rows[key]={'plat_id':key,'platform_name':key,'state':'1','base_url':'http://fixture.test','username':'u','password':'private'}
    barrier=threading.Barrier(2);lock=threading.Lock();active=peak=0
    def login(platform):
        nonlocal active,peak
        with lock:active+=1;peak=max(active,peak)
        try:barrier.wait(timeout=3);return {'success':True,'token':'private-token'}
        finally:
            with lock:active-=1
    monkeypatch.setattr(module,'login_platform',login)
    runtime.get_runtime().create('bounded-checks','platform-check',{'status':'pending'},runtime.actor.get())
    runtime.tracked_run(module.run_platform_checks,'bounded-checks',list(rows))
    assert peak==2 and runtime.get_runtime().get('bounded-checks')['processed_count']==4
    actor=runtime.actor.set({'tenant_id':2,'user_id':2,'role':'admin'})
    try:assert client.get('/managed-platforms/check-all').json['data'] is None
    finally:runtime.actor.reset(actor)


def test_device_upstream_pages_are_fully_read_and_identity_kept(managed,monkeypatch):
    module,_,_=managed
    from integrations.smarteye.api import ledger_image_downloader as ledger
    monkeypatch.setattr(module,'login_platform',lambda p:{'success':True,'token':'test'})
    pages=[]
    def post(base,path,payload,token):
        assert path=='/api/alarm/device/device/config/page'
        assert payload['model']=={'alarmTypeId':'','scene':None,'deviceId':'','deviceName':None,
            'areaCode':None,'lastSnapTime':None,'alarmNumStatus':False,'startTime':None,'endTime':None,
            'captureType':None,'activeStatus':1,'orderByTime':True}
        assert payload['total']==0
        pages.append(payload['current'])
        start=(payload['current']-1)*100
        return {'code':0,'data':{'total':201,'records':[{'deviceId':str(i),'activeStatus':1,
            'scene':'上游单场景','scenes':['上游多场景'],'status':0} for i in range(start+1,min(start+100,201)+1)]}}
    monkeypatch.setattr(ledger,'_post_with_token',post)
    rows=module.fetch_platform_devices({'base_url':'http://local.test'})
    assert pages==[1,2,3]
    assert rows[-1]['device_id']=='201' and rows[-1]['upstream_active_status']==1
    assert rows[-1]['upstream_scene']=='上游单场景' and rows[-1]['upstream_scenes']==['上游多场景']
    assert rows[-1]['upstream_status']==0 and 'scene_labels' not in rows[-1]


def test_legacy_sync_sql_protects_managed_and_deleted_configs(monkeypatch):
    from integrations.smarteye.api import db_utils,platform_enum
    statements=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,params=None):statements.append(sql)
    class Connection:
        def cursor(self,**kwargs):return Cursor()
        def commit(self):pass
        def rollback(self):pass
        def close(self):pass
    monkeypatch.setattr(db_utils,'get_db_connection',lambda:Connection())
    platform_enum._PLATFORM_CACHE={'stale':{}}
    db_utils.sync_remote_platforms([{'id':'remote','name':'remote','baseUrl':'http://local.test'}])
    upsert=next(sql for sql in statements if 'INSERT INTO platforms' in sql)
    assert "platforms.state <> 'deleted' AND NOT platforms.managed" in upsert
    assert platform_enum._PLATFORM_CACHE is None


def test_connection_check_hides_unexpected_exception_details(managed,monkeypatch):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','base_url':'http://local.test','username':'u','password':'secret','state':'1'}
    def fail(platform):raise TimeoutError('secret body')
    monkeypatch.setattr(module,'login_platform',fail)
    response=client.post('/managed-platforms/a/check')
    assert response.json['data']['connection_status']=='error'
    assert 'secret' not in str(response.json)


def test_login_cannot_report_online_without_token(monkeypatch):
    from integrations.smarteye.api import smart_search
    class Response:
        content=b'captcha'
        def raise_for_status(self):pass
        def json(self):return {'code':0,'data':{'tenantId':'one'}}
    monkeypatch.setattr(smart_search.requests,'get',lambda *args,**kwargs:Response())
    monkeypatch.setattr(smart_search.requests,'post',lambda *args,**kwargs:Response())
    monkeypatch.setattr(smart_search,'recognize_captcha',lambda content:'1234')
    assert smart_search.login_platform({'base_url':'http://local.test'})['success'] is False


def test_non_object_request_is_rejected(managed):
    _,client,_=managed
    assert client.post('/devices/sync',json=['one']).status_code==400


def test_deleted_platform_devices_hidden_but_history_preserved(managed):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    rows['deleted']={'plat_id':'deleted','platform_name':'gone','state':'deleted'}
    module.update_device(1,'a','one',{'device_name':'available'})
    module.update_device(1,'deleted','one',{'device_name':'history'})
    assert client.get('/devices').json['data']['total']==1
    assert module.get_device(1,'deleted','one')['device_name']=='history'


def test_image_presence_filter_is_paginated_and_tenant_scoped(managed):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    module.update_device(1,'a','with',{'representative_blob':'images/local.jpg','representative_url':'/preview'})
    module.update_device(1,'a','without',{'device_name':'无图'})
    module.update_device(2,'a','private',{'representative_blob':'images/private.jpg'})
    for value,expected in [('yes','with'),('no','without')]:
        data=client.get('/devices?has_image='+value+'&page_size=1').json['data']
        assert data['total']==1 and data['items'][0]['device_id']==expected
        assert data['items'][0]['has_image']==(value=='yes')
    assert client.get('/devices?has_image=invalid').status_code==400


@pytest.mark.parametrize('kind',['scheduler','job','watch'])
def test_active_legacy_references_block_platform_deletion(managed,monkeypatch,kind):
    module,client,rows=managed
    from integrations.smarteye.api import ledger_image_downloader as ledger,ledger_watch_downloader as watch
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    monkeypatch.setattr(ledger,'get_all_schedulers',lambda:[{'enabled':True,'platform_ids':['a']}] if kind=='scheduler' else [])
    monkeypatch.setattr(watch,'get_all_watch_states',lambda:[{'is_running':True,'platform_ids':[]}] if kind=='watch' else [])
    if kind=='job':runtime.get_runtime().create('active','detection',{'status':'running','params':{'platNames':['A']}},runtime.actor.get())
    assert client.delete('/managed-platforms/a').status_code==400
    assert rows['a']['state']=='1'


def test_sync_backpressure_releases_slot_when_queued_future_cancelled(managed,monkeypatch):
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    pending=[]
    def submit(*args):
        future=Future();pending.append(future);return future
    monkeypatch.setattr(module.executor,'submit',submit)
    assert client.post('/devices/sync',json={}).status_code==200
    assert client.post('/devices/sync',json={}).status_code==200
    assert client.post('/devices/sync',json={}).status_code==429
    pending[0].cancel()
    assert client.post('/devices/sync',json={}).status_code==200
    for future in pending:future.cancel()


@pytest.mark.parametrize('failure',['repeated','truncated','zero_with_records','missing_id'])
def test_upstream_incomplete_or_repeated_device_pages_fail(managed,monkeypatch,failure):
    module,_,_=managed
    from integrations.smarteye.api import ledger_image_downloader as ledger
    monkeypatch.setattr(module,'login_platform',lambda p:{'success':True,'token':'test'})
    def post(base,path,payload,token):
        page=payload['current']
        records=[{'deviceId':str(i)} for i in range(100)]
        if failure=='truncated' and page==2:records=[]
        if failure=='missing_id':records=[{'deviceName':'bad'}]
        return {'code':0,'data':{'total':0 if failure=='zero_with_records' else 201,'records':records}}
    monkeypatch.setattr(ledger,'_post_with_token',post)
    with pytest.raises(ValueError):module.fetch_platform_devices({'base_url':'http://local.test'})


def test_cancelled_sync_stops_before_next_upstream_page_and_device_write(managed,monkeypatch):
    module,_,rows=managed
    from integrations.smarteye.api import ledger_image_downloader as ledger
    rows['a']={'plat_id':'a','platform_name':'A','base_url':'http://local.test','state':'1'}
    monkeypatch.setattr(module,'login_platform',lambda p:{'success':True,'token':'test'})
    calls=[]
    def post(base,path,payload,token):
        calls.append(payload['current'])
        runtime.get_runtime().cancel('cancel-sync')
        return {'code':0,'data':{'total':201,'records':[{'deviceId':str(i)} for i in range(100)]}}
    monkeypatch.setattr(ledger,'_post_with_token',post)
    runtime.get_runtime().create('cancel-sync','device-sync',{'status':'pending'},runtime.actor.get())
    runtime.tracked_run(module.run_device_sync,'cancel-sync',['a'])
    assert calls==[1]
    assert runtime.get_runtime().get('cancel-sync')['status']=='cancelled'
    assert module.get_device(1,'a','1') is None
