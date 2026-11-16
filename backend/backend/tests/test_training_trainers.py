"""Exercise the custom trainer contract with isolated data and no real GPU."""
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tests.test_training_runs import platform_payload, training_tables


@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['yolo', 'coco', 'swift_sft', 'infonce'])
async def test_registered_trainer_uses_frozen_data_and_saved_definition(
    client, auth_headers, test_db, test_engine, platform_payload, format, monkeypatch,
):
    from models.annotation_event import ExportSnapshot
    from models.training_run import TrainingTrainer
    from services.training_config import build_script

    async with test_engine.begin() as conn:
        await conn.run_sync(TrainingTrainer.__table__.create)
    snapshot = await test_db.get(ExportSnapshot, UUID(platform_payload['snapshot_id']))
    snapshot.export_format = format
    if format in ('swift_sft', 'infonce'):
        snapshot.manifest = {'training': {'format': format, 'samples': 1}}
    await test_db.commit()

    url = '/api/training/trainers'
    definition = dict(name='Fixture', image='fixture/training:v1', module='example.train',
                      dataset_format=format, parameters={'epochs': 2, 'layers': [1, 2]})
    assert client.post(url, headers=auth_headers, json={**definition, 'module': 'a; b'}).status_code == 422
    response = client.post(url, headers=auth_headers, json=definition)
    assert response.status_code == 201, response.text
    trainer_id = response.json()['id']
    assert client.get(url, headers=auth_headers).json()[0]['id'] == trainer_id
    payload = {**platform_payload, 'kind': 'custom', 'trainer_id': trainer_id,
               'parameters': {'epochs': 3}, 'image': ''}
    other_format = 'coco' if format == 'yolo' else 'yolo'
    assert client.put(f'{url}/{trainer_id}', headers=auth_headers,
                      json={**definition, 'dataset_format': other_format}).status_code == 200
    assert client.post('/api/training/runs', headers=auth_headers, json=payload).status_code == 400
    assert client.put(f'{url}/{trainer_id}', headers=auth_headers, json=definition).status_code == 200
    assert client.post('/api/training/runs', headers=auth_headers,
                       json={**payload, 'parameters': {'epochs': '3'}}).status_code == 422
    assert client.post('/api/training/runs', headers=auth_headers,
                       json={**payload, 'image': 'another:latest'}).status_code == 422
    response = client.post('/api/training/runs', headers=auth_headers, json=payload)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run['config']['image'] == definition['image']
    assert run['config']['parameters'] == {'epochs': 3, 'layers': [1, 2]}
    assert run['config']['runtime_dataset'] == {
        'yolo': '/output/prepared-dataset/data.yaml', 'coco': '/output/prepared-dataset',
        'swift_sft': '/output/dataset/train.jsonl', 'infonce': '/output/dataset/train.jsonl',
    }[format]
    assert client.put(f'{url}/{trainer_id}', headers=auth_headers,
                      json={**definition, 'module': 'updated.train'}).status_code == 200
    assert client.delete(f'{url}/{trainer_id}', headers=auth_headers).status_code == 204
    assert client.post('/api/training/runs', headers=auth_headers, json=payload).status_code == 404
    run_url = f"/api/training/runs/{run['id']}"
    assert client.put(run_url, headers=auth_headers, json=payload).status_code == 200
    config = client.get(run_url, headers=auth_headers).json()['config']
    assert config == run['config']
    script = build_script(config)
    if format in ('yolo', 'coco'):
        assert '    prepare_detection(' in script
    else:
        calls = []
        monkeypatch.setattr(subprocess, 'run', lambda args, **kw: calls.append((args, kw)))
        exec(compile(script, 'train.py', 'exec'), {'__name__': '__main__'})
        assert calls[0][0][1:] == ['-I', '-u', '-m', 'example.train', '--config', '/output/run_config.json']
        assert calls[0][1]['check'] is True


@pytest.mark.asyncio
async def test_trainer_registration_is_admin_and_tenant_scoped(client, auth_headers, test_db, test_engine):
    from models import Tenant, User
    from models.training_run import TrainingTrainer
    from services.auth import create_access_token

    async with test_engine.begin() as conn:
        await conn.run_sync(TrainingTrainer.__table__.create)
    test_db.add(Tenant(id=2, tenant_name='Other tenant'))
    await test_db.flush()
    test_db.add_all([
        User(id=2, tenant_id=2, username='other-admin', email='other@example.com',
             password_hash='fixture', role='admin', is_active=True),
        User(id=3, tenant_id=1, username='member', email='member@example.com',
             password_hash='fixture', role='user', is_active=True),
    ])
    await test_db.commit()

    def headers(id, tenant, name):
        token = create_access_token(data={'user_id': id, 'tenant_id': tenant, 'username': name})
        return {'Authorization': f'Bearer {token}'}

    url = '/api/training/trainers'
    definition = dict(name='Fixture', image='fixture/training:v1', module='example.train', dataset_format='yolo')
    assert client.post(url, headers=headers(3, 1, 'member'), json=definition).status_code == 403
    response = client.post(url, headers=auth_headers, json=definition)
    assert response.status_code == 201
    other = headers(2, 2, 'other-admin')
    assert client.get(url, headers=other).json() == []
    assert client.put(f"{url}/{response.json()['id']}", headers=other, json=definition).status_code == 404


def test_custom_executor_probes_module_before_launch_and_mounts_only_the_weight(monkeypatch, tmp_path):
    from services import training_executor as executor

    weights = tmp_path / 'models' / 'fixture.pt'
    weights.parent.mkdir()
    weights.write_bytes(b'weights')
    config = dict(kind='custom', trainer=dict(module='example.train', dataset_format='swift_sft'),
                  image='fixture/training:v1', gpu_uuids=['GPU-fixture'], model_path=str(weights),
                  snapshot_id=str(uuid4()), output_root=str(tmp_path / 'runs'), output_subdir='train',
                  parameters={}, environment={}, mounts=[], shm_size_gb=1)
    payload = dict(run_id=str(uuid4()), config=config, action='precheck')
    calls = []
    missing = True

    def docker(args, **kwargs):
        calls.append(args)
        if args[:2] == ['image', 'inspect']:
            return SimpleNamespace(returncode=0, stdout='[{"Id":"sha256:fixture"}]')
        if args[0] == 'run':
            assert args[args.index('--network') + 1] == 'none'
            assert '--mount' not in args and '--gpus' not in args
            assert args[-2:] == ['example.train', 'swift_sft']
            if missing:
                raise RuntimeError('训练模块未安装')
        return SimpleNamespace(returncode=0, stdout='')

    monkeypatch.setattr(executor, 'docker', docker)
    monkeypatch.setattr(executor, 'container_state', lambda run_id: None)
    monkeypatch.setattr(executor, 'run_lock', lambda path: nullcontext())
    monkeypatch.setattr(executor, 'SNAPSHOT_CACHE_ROOT', tmp_path / 'cache')
    monkeypatch.setattr(executor.subprocess, 'run', lambda args, **kw: SimpleNamespace(
        returncode=0, stdout='0, GPU-fixture, Test GPU, 10000, Disabled' if 'index,uuid' in args[1] else ''))
    result = executor.precheck(payload)
    assert not result['ok'] and any(row['detail'] == '训练模块未安装' for row in result['checks'])
    missing = False
    staged = executor.execute({**payload, 'action': 'stage', 'script': '# fixture'})
    (Path(staged['directory']) / '.dataset-ready').touch()
    assert executor.execute({**payload, 'action': 'start'})['status'] == 'running'
    create = next(args for args in calls if args[0] == 'create')
    assert f'type=bind,src={weights},dst={weights},readonly' in create
    assert not any(arg.startswith(f'type=bind,src={weights.parent},') for arg in create)
    assert create[-3:] == ['sha256:fixture', '-u', '/output/train.py']
