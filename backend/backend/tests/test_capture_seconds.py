import pytest

from integrations.smarteye import device_capture as capture,runtime
from tests.test_device_capture import capture_client,schedule


@pytest.mark.parametrize('seconds',[1,5,60,31536000])
def test_create_capture_interval_in_seconds(capture_client,seconds):
    response=capture_client.post('/device-capture/schedules',json={'name':'秒级抓图',
        'devices':[{'plat_id':'p','device_id':'d'}],'interval_seconds':seconds,'enabled':False})
    assert response.status_code==201
    item=response.json['data']
    assert item['interval_seconds']==seconds
    assert item['interval_minutes']==seconds/60


@pytest.mark.parametrize('seconds',[0,-1,1.5,True,'5',None,31536001])
def test_rejects_invalid_seconds_even_when_legacy_minutes_are_present(capture_client,seconds):
    assert schedule(capture_client,interval_seconds=seconds).status_code==400
    assert capture_client.get('/device-capture/schedules').json['data']==[]


@pytest.mark.parametrize('creation_fails',[False,True])
def test_seconds_drive_next_run_in_success_and_creation_failure(capture_client,monkeypatch,creation_fails):
    monkeypatch.setattr(capture.time,'time',lambda:1000)
    item=schedule(capture_client,interval_seconds=5).json['data']
    if creation_fails:
        def fail(*args):raise RuntimeError('fixture create failure')
        monkeypatch.setattr(runtime.get_runtime(),'create',fail)
    else:monkeypatch.setattr(capture,'_capture_device',lambda *args:{'downloaded':0})
    capture._run_schedule(item['sched_id'])
    assert capture._get(item['sched_id'])['next_run']==1005


def test_existing_minute_payload_keeps_frequency_without_rewriting_on_read(capture_client,monkeypatch):
    monkeypatch.setattr(capture.time,'time',lambda:1000)
    created=schedule(capture_client,interval_minutes=2).json['data']
    original=capture._get(created['sched_id']);original.pop('interval_seconds',None);original['interval_minutes']=2
    capture._save(original)
    public=capture_client.get('/device-capture/schedules').json['data'][0]
    assert public['interval_seconds']==120 and public['interval_minutes']==2
    assert 'interval_seconds' not in capture._get(created['sched_id'])
    monkeypatch.setattr(capture,'_capture_device',lambda *args:{'downloaded':0})
    capture._run_schedule(created['sched_id'])
    assert capture._get(created['sched_id'])['next_run']==1120


def test_one_second_schedule_does_not_overlap_itself(capture_client):
    item=schedule(capture_client,interval_seconds=1).json['data']
    saved=capture._get(item['sched_id']);saved['running']=True;capture._save(saved)
    assert capture._submit(item['sched_id']) is False
