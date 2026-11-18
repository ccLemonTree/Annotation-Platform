import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from integrations.smarteye import device_capture as capture,runtime
from integrations.smarteye.api import ledger_image_downloader as ledger,smart_search
from tests.test_device_capture import capture_client,schedule


@pytest.fixture
def tokens(capture_client,monkeypatch):
    login=Mock(side_effect=lambda platform:{'success':True,'token':'private-'+platform['plat_id'],'expire_time':'2000-01-01 00:00:00'})
    query=Mock(return_value=([],0))
    monkeypatch.setattr(smart_search,'login_platform',login)
    monkeypatch.setattr(ledger,'query_ledger_record_page',query)
    return login,query


def collect(task,owner=None,platform='p'):
    owner=owner or runtime.actor.get()
    runtime.get_runtime().create(task,'device-capture',{'status':'running'},owner)
    return runtime.tracked_run(lambda current:capture._capture_device(current,owner,{'plat_id':platform,'device_id':'d'},0,60),task)


def test_reuses_platform_tokens_across_devices_and_batches_without_persisting_secrets(capture_client,tokens,monkeypatch):
    from integrations.smarteye.management_schema import devices
    login,query=tokens
    selection=[{'plat_id':'p','device_id':'one'}, {'plat_id':'p','device_id':'two'}, {'plat_id':'other','device_id':'three'}]
    monkeypatch.setattr(capture,'list_platforms_internal',lambda:[capture.get_platform(key) for key in ('p','other')])
    with runtime.get_runtime().engine.begin() as conn:
        conn.execute(devices.insert(),[dict(device,tenant_id=1,payload={}) for device in selection])
    item=schedule(capture_client,devices=selection).json['data']
    for _ in range(2):capture._run_schedule(item['sched_id'])
    assert login.call_count==2 and query.call_count==6
    saved=capture._get(item['sched_id']);job=runtime.get_runtime().get(saved['last_task_id'])
    assert 'private-' not in json.dumps([saved,job])


def test_expired_token_refreshes_once_and_retries_current_query(tokens):
    login,query=tokens
    login.side_effect=[{'success':True,'token':'old'},{'success':True,'token':'new'}]
    query.side_effect=[ledger._TOKEN_EXPIRED,([],0),([],0)]
    assert collect('first')['downloaded']==collect('second')['downloaded']==0
    assert login.call_count==2
    assert [call.args[1]['token'] for call in query.call_args_list]==['old','new','new']


def test_repeated_token_rejection_is_bounded_and_does_not_keep_invalid_token(tokens):
    login,query=tokens
    query.return_value=ledger._TOKEN_EXPIRED
    with pytest.raises(capture.CaptureError,match='重新登录'):
        collect('rejected')
    assert login.call_count==query.call_count==2
    query.return_value=([],0)
    assert collect('retry')['downloaded']==0
    assert login.call_count==3


def test_query_failure_does_not_trigger_unnecessary_relogin(tokens):
    login,query=tokens
    query.side_effect=RuntimeError('temporary query failure')
    for task in ('first','second'):
        with pytest.raises(RuntimeError,match='temporary query failure'):collect(task)
    assert login.call_count==1 and query.call_count==2


def test_platform_tenant_and_user_tokens_are_isolated(tokens):
    login,_=tokens
    for index,(tenant,user,platform) in enumerate([(1,1,'p'),(1,1,'other'),(1,2,'p'),(2,1,'p'),(1,1,'p')]):
        collect('owner-'+str(index),{'tenant_id':tenant,'user_id':user,'role':'admin'},platform)
    assert login.call_count==4


@pytest.mark.parametrize('field,value',[('base_url','http://other'),('username','new-user'),
    ('password','new-password'),('login_url','/new-login')])
def test_login_configuration_changes_do_not_reuse_old_token(tokens,monkeypatch,field,value):
    login,_=tokens
    platform={'plat_id':'p','platform_name':'test','base_url':'http://fixture','state':'1','username':'old','password':'old'}
    monkeypatch.setattr(capture,'get_platform',lambda _:dict(platform))
    collect('before');platform[field]=value;collect('after')
    assert login.call_count==2


def test_concurrent_expiry_responses_share_one_refresh(tokens):
    login,query=tokens
    login.side_effect=[{'success':True,'token':'old'},{'success':True,'token':'new'}]
    collect('prime')
    barrier=Barrier(2)
    def expired(base,token,*args,**kwargs):
        if token['token']=='old':barrier.wait(timeout=3);return ledger._TOKEN_EXPIRED
        return [],0
    query.side_effect=expired
    owner=dict(runtime.actor.get())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda task:collect(task,owner),['parallel-one','parallel-two']))
    assert all(result['downloaded']==0 for result in results)
    assert login.call_count==2


@pytest.mark.parametrize('status,body',[(401,{}),(200,{'code':40005}),
    (200,{'code':-1,'msg':'解析用户身份错误'})])
def test_business_or_http_token_expiry_is_recognized(monkeypatch,status,body):
    from tests.test_smarteye_login import _response
    response=_response(status,body)
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):return response
        def get(self,*args,**kwargs):return response
    monkeypatch.setattr(ledger,'_make_no_proxy_session',Session)
    for request in (ledger._post_with_token,ledger._get_with_token):
        args=('http://fixture','/query',{}, {}) if request is ledger._post_with_token else ('http://fixture','/query',{})
        assert request(*args) is ledger._TOKEN_EXPIRED
