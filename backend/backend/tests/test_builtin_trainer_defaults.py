from datetime import timedelta

import pytest

from tests.test_training_runs import platform_payload, training_tables


@pytest.mark.asyncio
async def test_builtin_defaults_are_validated_and_apply_only_to_new_runs(
    client, auth_headers, platform_payload, test_db,
):
    from models import Tenant, User
    from services.auth import create_access_token

    catalog = '/api/training/catalog'
    defaults = '/api/training/trainers/builtin/yolo26/defaults'
    runs = '/api/training/runs'
    payload = {**platform_payload, 'parameters': {}}
    old = client.post(runs, headers=auth_headers, json=payload).json()
    assert old['config']['parameters']['epochs'] == 300
    for parameters in ({'epochs': 0}, {'epochs': '20'}, {'unknown': 1}):
        assert client.put(defaults, headers=auth_headers, json={'parameters': parameters}).status_code == 422
    result = client.put(defaults, headers=auth_headers, json={'parameters': {'epochs': 20, 'batch': 42}})
    assert result.status_code == 200, result.text
    assert result.json()['defaults']['epochs'] == 20
    entries = client.get(catalog, headers=auth_headers).json()
    assert len(entries) == 6
    assert entries['yolo26']['defaults']['batch'] == 42
    new = client.post(runs, headers=auth_headers, json={
        **payload, 'gpu_uuids': [f'GPU-test-{i}' for i in range(7)], 'parameters': {'epochs': 12},
    })
    assert new.status_code == 201, new.text
    assert new.json()['config']['parameters']['epochs'] == 12
    assert new.json()['config']['parameters']['batch'] == 42
    old_url = f"{runs}/{old['id']}"
    edited = client.put(old_url, headers=auth_headers, json={**payload, 'name': '原草稿'})
    assert edited.status_code == 200, edited.text
    assert edited.json()['config']['parameters']['epochs'] == 300

    test_db.add(Tenant(id=2, tenant_name='Other'))
    await test_db.flush()
    test_db.add(User(id=2, tenant_id=2, username='other', email='other@example.com',
                     password_hash='fixture', role='admin', is_active=True))
    await test_db.commit()
    token = create_access_token({'user_id': 2}, timedelta(minutes=1))
    other = client.get(catalog, headers={'Authorization': f'Bearer {token}'}).json()
    assert other['yolo26']['defaults']['epochs'] == 300

    reset = client.put(defaults, headers=auth_headers, json={'parameters': {}})
    assert reset.status_code == 200 and reset.json()['defaults']['epochs'] == 300


def test_k8s_wemm_keeps_profile_defaults_for_unspecified_parameters(client, auth_headers, monkeypatch):
    from services import training_runs

    monkeypatch.setattr(training_runs, 'bridge', lambda *args: [])
    url = '/api/training/trainers/builtin/wemm/defaults'
    result = client.put(url, headers=auth_headers, json={'parameters': {'save_steps': 250}})
    assert result.status_code == 200, result.text
    info = client.get('/api/training/k8s/nodes', headers=auth_headers).json()['wemm_defaults']
    assert info['parameters']['save_steps'] == 250
    assert info['parameters']['learning_rate'] == 2e-5
