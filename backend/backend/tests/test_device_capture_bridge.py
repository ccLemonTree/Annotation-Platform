"""Exercise the real authentication bridge and legacy hooks around new routes."""


def test_schedule_actions_are_not_mistaken_for_download_task_ids(client, auth_headers, monkeypatch):
    from integrations.smarteye import device_capture
    monkeypatch.setattr(device_capture, 'get_platform', lambda key: {'plat_id': key, 'state': '1'})
    monkeypatch.setattr(device_capture, 'get_device', lambda *args: {'device_id': 'camera'})
    response = client.post('/api/smarteye/device-capture/schedules', headers=auth_headers,
        json={'name': '设备抓图', 'devices': [{'plat_id': 'platform', 'device_id': 'camera'}], 'interval_minutes': 10})
    assert response.status_code == 201, response.text
    path = '/api/smarteye/device-capture/schedules/' + response.json()['data']['sched_id']
    assert client.post(path + '/start', headers=auth_headers).status_code == 200
    assert client.post(path + '/stop', headers=auth_headers).status_code == 200
    assert client.delete(path, headers=auth_headers).status_code == 200


def test_device_task_cancel_requires_admin(client, auth_headers):
    from main import app
    from api.deps import get_current_user
    from types import SimpleNamespace
    from integrations.smarteye.runtime import get_runtime
    get_runtime().create('device_job', 'device-scenes', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2, tenant_id=1, role='annotator')
    try:
        response = client.post('/api/smarteye/tasks/device_job/cancel', headers=auth_headers)
        assert response.status_code == 403, response.text
        assert get_runtime().get('device_job')['status'] == 'running'
    finally:
        app.dependency_overrides.pop(get_current_user)
