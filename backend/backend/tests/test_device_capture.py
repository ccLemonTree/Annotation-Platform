"""Device capture uses temporary state and never a real upstream."""
import pytest
from flask import Flask
from integrations.smarteye import device_capture as capture
from integrations.smarteye import runtime
from sqlalchemy import create_engine


@pytest.fixture
def capture_client(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, '_platform_tokens', {})
    monkeypatch.setattr(capture, '_platform_token_locks', {})
    engine = create_engine(f"sqlite:///{tmp_path / 'capture.db'}")
    runtime.metadata.create_all(engine)
    monkeypatch.setattr(runtime, '_runtime', runtime.Runtime(engine, tmp_path / 'storage'))
    monkeypatch.setattr(capture, 'get_platform', lambda key: {'plat_id': key, 'platform_name': 'test', 'base_url': 'http://fixture', 'state': '1'})
    monkeypatch.setattr(capture, 'get_device', lambda *args: {'device_id': args[-1]})
    app = Flask(__name__)
    capture.register_routes(app)
    token = runtime.actor.set({'tenant_id': 1, 'user_id': 1, 'role': 'admin'})
    yield app.test_client()
    runtime.actor.reset(token)
    engine.dispose()


def schedule(client, **changes):
    return client.post('/device-capture/schedules', json={
        'name': '定时拉图', 'devices': [{'plat_id': 'p', 'device_id': 'd'}],
        'interval_minutes': 1, 'enabled': False, **changes})


def test_schedule_crud_and_validation(capture_client):
    assert schedule(capture_client, interval_minutes=0).status_code == 400
    assert schedule(capture_client, devices=[]).status_code == 400
    response = schedule(capture_client)
    assert response.status_code == 201
    item = response.json['data']
    assert not item['enabled'] and not item['running']
    assert capture_client.get('/device-capture/schedules').json['data'][0]['sched_id'] == item['sched_id']
    assert capture_client.delete('/device-capture/schedules/' + item['sched_id']).status_code == 200
    assert capture_client.get('/device-capture/schedules').json['data'] == []


@pytest.mark.parametrize('state', ['0', 'deleted', None])
def test_schedule_rejects_disabled_platform(capture_client, monkeypatch, state):
    monkeypatch.setattr(capture, 'get_platform', lambda key: {'plat_id': key, 'state': state})
    response = schedule(capture_client, enabled=True)
    assert response.status_code == 400
    assert capture_client.get('/device-capture/schedules').json['data'] == []


def test_schedule_rejects_name_only_identity(capture_client,monkeypatch):
    monkeypatch.setattr(capture,'get_device',lambda *args:{'identity_kind':'name'})
    assert schedule(capture_client,enabled=True).status_code==400


def test_schedule_admin_and_tenant_isolation(capture_client):
    item = schedule(capture_client).json['data']
    token = runtime.actor.set({'tenant_id': 2, 'user_id': 2, 'role': 'admin'})
    try:
        assert capture_client.get('/device-capture/schedules').json['data'] == []
        assert capture_client.post('/device-capture/schedules/' + item['sched_id'] + '/start').status_code == 404
    finally:
        runtime.actor.reset(token)
    token = runtime.actor.set({'tenant_id': 1, 'user_id': 2, 'role': 'annotator'})
    try:
        assert schedule(capture_client).status_code == 403
    finally:
        runtime.actor.reset(token)


def test_failed_device_does_not_advance_cursor(capture_client, monkeypatch):
    item = schedule(capture_client).json['data']
    monkeypatch.setattr(capture, '_capture_device', lambda *args: (_ for _ in ()).throw(RuntimeError('upstream failed')))
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    assert not result.get('cursors')
    assert result['last_error'] == '设备抓图失败，请检查平台、图片或入库配置'
    assert result['device_results'][0]['status'] == 'failed'
    assert runtime.get_runtime().get(result['last_task_id'])['status'] == 'failed'
    starts = []
    monkeypatch.setattr(capture, '_capture_device', lambda task, owner, device, start, end: starts.append(start) or {'downloaded': 0})
    capture._run_schedule(item['sched_id'])
    assert starts == [result['created_at'] - 60]


def test_success_advances_cursor_only_after_full_device(capture_client, monkeypatch):
    item = schedule(capture_client).json['data']
    monkeypatch.setattr(capture, '_capture_device', lambda *args: {'downloaded': 2, 'representative_blob_path': 'images/test.png'})
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    assert result['cursors']['["p","d"]'] == result['last_run']
    assert result['device_results'][0]['downloaded'] == 2
    assert runtime.get_runtime().get(result['last_task_id'])['status'] == 'completed'


def test_restart_disables_schedule_and_active_platform_reference(capture_client):
    item = schedule(capture_client, enabled=True).json['data']
    assert capture.platform_in_use('p')
    capture.start_worker()
    try:
        result = capture._get(item['sched_id'])
        assert not result['enabled'] and not result['running']
        assert '重启' in result['last_error']
        assert not capture.platform_in_use('p')
    finally:
        capture.shutdown_worker()


def test_destination_must_exist_in_actor_tenant(capture_client, monkeypatch):
    import uuid
    from models import Base, Tenant, Project, ImageCollection
    engine = runtime.get_runtime().engine
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in ('tenants', 'projects', 'image_collections')])
    assert schedule(capture_client, project_id='invalid').status_code == 400
    assert schedule(capture_client, project_id='00000000-0000-0000-0000-000000000001').status_code == 400
    assert schedule(capture_client, collection_id='00000000-0000-0000-0000-000000000001').status_code == 400
    project_id, collection_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(Tenant.__table__.insert().values(id=2, tenant_name='other'))
        conn.execute(Project.__table__.insert().values(id=project_id, tenant_id=2, project_name='foreign', task_type='detection'))
        conn.execute(ImageCollection.__table__.insert().values(id=collection_id, tenant_id=2, name='foreign'))
    assert schedule(capture_client, project_id=str(project_id)).status_code == 400
    assert schedule(capture_client, collection_id=str(collection_id)).status_code == 400


def test_latest_capture_does_not_page_through_history(capture_client, monkeypatch):
    from integrations.smarteye.api import ledger_image_downloader as ledger, smart_search
    monkeypatch.setattr(smart_search, 'login_platform', lambda platform: {'success': True})
    calls, saves = [], []
    def query(base, token, device, start, end, current, page_size=100, **kwargs):
        calls.append(current)
        return ([{'backPath': '/image.jpg'}], 101) if current == 1 else ([], 101)
    monkeypatch.setattr(ledger, 'query_ledger_record_page', query)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(capture.requests, 'Session', Session)
    monkeypatch.setattr(capture, 'response_bytes', lambda response: b'image')
    monkeypatch.setattr(runtime.get_runtime(), 'save_image', lambda *args, **kwargs: saves.append(args) or 'images/a.jpg')
    item = schedule(capture_client).json['data']
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    assert calls == [1] and len(saves) == 1
    assert result['cursors'] and not result['last_error']


def test_stop_cancels_active_task_and_prevents_late_cursor(capture_client, monkeypatch):
    item = schedule(capture_client).json['data']
    def cancelled(task_id, *args):
        response = capture_client.post('/device-capture/schedules/' + item['sched_id'] + '/stop')
        assert response.status_code == 200
        assert capture_client.post('/device-capture/schedules/' + item['sched_id'] + '/start').status_code == 200
        assert not capture._submit(item['sched_id'])
        return {'downloaded': 1}
    monkeypatch.setattr(capture, '_capture_device', cancelled)
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    assert not result['cursors'] and result['enabled'] and not result['running']
    assert runtime.get_runtime().get(result['last_task_id'])['status'] == 'cancelled'


def test_compound_device_cursor_keys_do_not_collide(capture_client, monkeypatch):
    item = schedule(capture_client, devices=[{'plat_id': 'a:b', 'device_id': 'c'}, {'plat_id': 'a', 'device_id': 'b:c'}]).json['data']
    monkeypatch.setattr(capture, '_capture_device', lambda *args: {'downloaded': 1})
    capture._run_schedule(item['sched_id'])
    assert len(capture._get(item['sched_id'])['cursors']) == 2


@pytest.mark.parametrize('failure', [capture.requests.Timeout('https://fixture/?signature=SECRET'), capture.requests.HTTPError('https://fixture/?token=SECRET'), RuntimeError('password=SECRET')])
def test_upstream_error_never_exposes_sensitive_details(capture_client, monkeypatch, failure):
    item = schedule(capture_client).json['data']
    monkeypatch.setattr(capture, '_capture_device', lambda *args: (_ for _ in ()).throw(failure))
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    task = runtime.get_runtime().get(result['last_task_id'])
    assert 'SECRET' not in str(result) + str(task)


def test_collector_filters_latest_without_falling_back_to_older_images(capture_client, monkeypatch):
    from integrations.smarteye.api import ledger_image_downloader as ledger, smart_search
    monkeypatch.setattr(smart_search, 'login_platform', lambda platform: {'success': True})
    windows = []
    def query(base, token, device, start, end, current, page_size=100, **kwargs):
        windows.append((start, end))
        return ([{'backPath': '/image-' + str(i) + '.jpg'} for i in range(100)], 101) if current == 1 else ([{'backPath': '/older.jpg'}], 101)
    monkeypatch.setattr(ledger, 'query_ledger_record_page', query)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(capture.requests, 'Session', Session)
    monkeypatch.setattr(capture, 'response_bytes', lambda response: b'image')
    saves = iter([None, 'images/newest.jpg'] + ['images/older.jpg'] * 99)
    monkeypatch.setattr(runtime.get_runtime(), 'save_image', lambda *args, **kwargs: next(saves))
    runtime.get_runtime().create('fixed-time', 'device-capture', {'status': 'pending'}, runtime.actor.get())
    result = runtime.tracked_run(lambda task: capture._capture_device(task, runtime.actor.get(), {'plat_id': 'p', 'device_id': 'd'}, 0, 60), 'fixed-time')
    assert windows == [(None, None)]
    assert result['downloaded'] == 0 and result['filtered'] == 1


@pytest.mark.parametrize('record,expected_status', [({'success': False}, 'completed'), ({'success': True}, 'failed'), ({}, 'failed')])
def test_failed_capture_is_skipped_but_success_without_path_fails(capture_client, monkeypatch, record, expected_status):
    from integrations.smarteye.api import ledger_image_downloader as ledger, smart_search
    monkeypatch.setattr(smart_search, 'login_platform', lambda platform: {'success': True})
    monkeypatch.setattr(ledger, 'query_ledger_record_page', lambda *args, **kwargs: ([record], 1))
    item = schedule(capture_client).json['data']
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    assert runtime.get_runtime().get(result['last_task_id'])['status'] == expected_status
    device_result = result['device_results'][0]
    if expected_status == 'completed':
        assert device_result['downloaded'] == 0 and device_result['filtered'] == 0
        assert device_result['skipped'] == 1
        assert result['cursors']
    else:
        assert not result['cursors']


def test_latest_capture_never_scans_older_failed_records(capture_client, monkeypatch):
    from integrations.smarteye.api import ledger_image_downloader as ledger, smart_search
    monkeypatch.setattr(smart_search, 'login_platform', lambda platform: {'success': True})
    calls = []
    def page(*args, **kwargs):
        current = args[-2]
        calls.append(current)
        return ([{'success': False}] * (100 if current == 1 else 1), 101)
    monkeypatch.setattr(ledger, 'query_ledger_record_page', page)
    item = schedule(capture_client).json['data']
    capture._run_schedule(item['sched_id'])
    result = capture._get(item['sched_id'])
    assert calls == [1]
    assert result['device_results'][0]['skipped'] == 1 and result['cursors']
