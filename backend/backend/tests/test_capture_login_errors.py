from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_device_capture import capture_client,schedule
from tests.test_smarteye_login import _response


def test_multiplatform_http400_captcha_rejection_is_not_generic_response_error(monkeypatch):
    from integrations.smarteye.api import smart_search
    monkeypatch.setattr(smart_search.requests,'get',Mock(return_value=Mock(content=b'captcha')))
    monkeypatch.setattr(smart_search,'recognize_captcha',Mock(return_value='5'))
    post=Mock(return_value=_response(400,{'code':-9,'msg':'验证码不正确'}))
    monkeypatch.setattr(smart_search.requests,'post',post)
    result=smart_search.login_platform({'base_url':'http://fixture','username':'test','password':'private'})
    assert not result['success'] and result['error_kind']=='captcha'
    assert post.call_count==3


@pytest.mark.parametrize('content',['9 - 7 = 2','答案是2','<think>2</think>',''])
def test_non_numeric_captcha_model_output_is_not_sent_as_login_code(monkeypatch,content):
    from integrations.smarteye.api import login_utils
    monkeypatch.setattr(login_utils.requests,'post',Mock(return_value=SimpleNamespace(
        raise_for_status=lambda:None,json=lambda:{'choices':[{'message':{'content':content}}]})))
    assert login_utils.recognize_captcha(b'captcha')==''


@pytest.mark.parametrize('kind,reason',[('captcha','验证码'),('timeout','超时'),
    ('connection','无法连接'),('authentication','账号'),('response','响应'),('unknown','登录配置')])
def test_capture_login_error_exposes_only_fixed_reason(capture_client,monkeypatch,kind,reason):
    from integrations.smarteye import device_capture as capture,runtime
    from integrations.smarteye.api import smart_search
    monkeypatch.setattr(smart_search,'login_platform',lambda _:{'success':False,'error_kind':kind,'msg':'password=private; token=private'})
    runtime.get_runtime().create('login','device-capture',{'status':'running'},runtime.actor.get())
    with pytest.raises(capture.CaptureError) as failure:
        runtime.tracked_run(lambda task:capture._capture_device(task,runtime.actor.get(),{'plat_id':'p','device_id':'d'},0,60),'login')
    assert reason in str(failure.value)
    assert 'private' not in str(failure.value)


def test_capture_failure_message_is_compact_and_repeated_reasons_are_counted(capture_client,monkeypatch):
    from integrations.smarteye import device_capture as capture,runtime
    from integrations.smarteye.management_schema import devices as devices_table
    devices=[{'plat_id':'p','device_id':str(i)} for i in range(30)]
    with runtime.get_runtime().engine.begin() as conn:
        conn.execute(devices_table.insert(), [dict(d, tenant_id=1, payload={}) for d in devices])
    item=schedule(capture_client,devices=devices).json['data']
    def fail(*args):raise capture.CaptureError('平台登录失败：验证码校验失败')
    monkeypatch.setattr(capture,'_capture_device',fail)
    capture._run_schedule(item['sched_id'])
    saved=capture._get(item['sched_id']);job=runtime.get_runtime().get(saved['last_task_id'])
    assert job['status']=='failed' and len(job['device_results'])==30
    assert len(job['message'])<100 and '30' in job['message']
    assert saved['last_error'].count('验证码校验失败')==1 and '30' in saved['last_error']
    assert all(row['error']=='平台登录失败：验证码校验失败' for row in job['device_results'])
