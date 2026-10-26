"""GPU connections use isolated SQLite and a stubbed MCP collector only."""
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker


URL = '/api/gpu-resources/servers'
PASSWORD = '  p@ss\"\\特殊\n$(literal)  '


def payload(**changes):
    return {**dict(name='lab GPU', host='2001:0db8:0:0::1', password=PASSWORD), **changes}


@pytest_asyncio.fixture(autouse=True)
async def gpu_table(test_engine):
    from models.gpu_server import GpuServer
    async with test_engine.begin() as connection:
        await connection.run_sync(GpuServer.__table__.create)


def create(client, headers, **changes):
    response = client.post(URL, headers=headers, json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def fake_snapshot(server_id, revision, name, connection):
    return dict(server_name=name, collected_at='2026-10-16T00:00:00+00:00', cached=False,
                gpus=[], processes=[], processes_complete=True, warnings=[])


def test_default_server_is_read_only_and_redacted(client, auth_headers):
    response = client.get(URL, headers=auth_headers)
    assert response.status_code == 200, response.text
    from config.settings import settings
    assert response.json() == [dict(id='default', name=settings.GPU_SERVER_NAME,
        host=None, port=None, username=None, enabled=True, revision=0,
        has_password=False, source='ssh_manager')]
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_create_encrypts_password_and_returns_summary(client, auth_headers, test_db):
    from models.gpu_server import GpuServer
    from services.gpu_servers import decrypt_password
    result = create(client, auth_headers)
    assert UUID(result['id'])
    assert result['host'] == '2001:db8::1'
    assert result['port'] == 22 and result['username'] == 'root'
    assert result['source'] == 'password' and result['revision'] == 1
    assert result['has_password'] is True and result['enabled'] is True
    assert 'password' not in result and 'password_encrypted' not in result
    row = await test_db.get(GpuServer, UUID(result['id']))
    assert row.password_encrypted and PASSWORD not in row.password_encrypted
    assert decrypt_password(row.password_encrypted) == PASSWORD
    assert PASSWORD not in repr(row) and row.password_encrypted not in repr(row)
    assert row.created_at and row.updated_at
    listed = client.get(URL, headers=auth_headers)
    assert listed.json()[1] == result
    assert PASSWORD not in listed.text and row.password_encrypted not in listed.text


@pytest.mark.parametrize('changes', [
    {'host': 'ssh://127.0.0.1'}, {'host': 'localhost'}, {'host': ' 127.0.0.1'},
    {'host': '127.0.0.1 '}, {'host': 'fe80::1%eth0'}, {'host': '[::1]'},
    {'host': '127.0.0.1\n'}, {'host': 1234}, {'port': 0}, {'port': 65536},
    {'port': True}, {'port': '22'}, {'username': 'root;id'}, {'username': '-root'},
    {'username': 'root user'}, {'username': 'root\n'}, {'username': 'x' * 65},
    {'password': ''}, {'password': None}, {'password': 123}, {'password': 'p' * 1025},
    {'password': 'no\0nul'}, {'host': '127.0.0.1\0'}, {'username': '1root'},
    {'name': ''}, {'name': '   '}, {'name': 'x' * 201}, {'enabled': 'false'},
    {'command': 'nvidia-smi'}, {'tenant_id': 2}, {'revision': 99},
])
def test_strict_create_and_unsaved_validation(client, auth_headers, changes):
    for url in (URL, URL + '/test'):
        response = client.post(url, headers=auth_headers, json=payload(**changes))
        assert response.status_code == 422, response.text
        assert 'p@ss' not in response.text


def test_create_and_test_require_password(client, auth_headers):
    value = payload(); value.pop('password')
    for url in (URL, URL + '/test'):
        assert client.post(url, headers=auth_headers, json=value).status_code == 422


@pytest.mark.asyncio
async def test_edit_retains_replaces_clears_and_invalidates(client, auth_headers, test_db, monkeypatch):
    from models.gpu_server import GpuServer
    from services import gpu_resources
    from services.gpu_servers import decrypt_password
    invalidated = []
    monkeypatch.setattr(gpu_resources, 'invalidate_connection', invalidated.append, raising=False)
    result = create(client, auth_headers, host='127.0.0.1')
    server_id = result['id']; url = URL + '/' + server_id
    for revision, change in enumerate(({'name': 'renamed'}, {'password': ''},
        {'password': ' new secret '}, {'clear_password': True}), start=2):
        response = client.patch(url, headers=auth_headers, json=change)
        assert response.status_code == 200, response.text
        assert response.json()['revision'] == revision
        assert response.json()['has_password'] is (revision != 5)
        row = await test_db.get(GpuServer, UUID(server_id), populate_existing=True)
        if revision == 5:
            assert row.password_encrypted is None
        else:
            assert decrypt_password(row.password_encrypted) == (' new secret ' if revision == 4 else PASSWORD)
        await test_db.rollback()
    assert invalidated == [server_id] * 4
    assert client.get(url + '/resources', headers=auth_headers).status_code == 409
    assert client.delete(url, headers=auth_headers).status_code == 204
    assert client.patch(url, headers=auth_headers, json={'name': 'gone'}).status_code == 404
    assert invalidated == [server_id] * 5


@pytest.mark.parametrize('change', [{'host': '10.0.0.2'}, {'port': 2222}, {'username': 'gpu-user'}])
def test_target_edit_requires_fresh_password(client, auth_headers, change):
    result = create(client, auth_headers)
    url = URL + '/' + result['id']
    for extra in ({}, {'password': ''}, {'clear_password': True}):
        response = client.patch(url, headers=auth_headers, json={**change, **extra})
        assert response.status_code == 422, response.text
    response = client.patch(url, headers=auth_headers, json={**change, 'password': 'fresh'})
    assert response.status_code == 200 and response.json()['revision'] == 2


@pytest.mark.parametrize('change', [
    {'password': 'new', 'clear_password': True}, {'password': '', 'clear_password': True},
    {'name': None}, {'host': None}, {'port': None}, {'username': None}, {'enabled': None},
    {'clear_password': 'true'}, {'clear_password': None}, {'password_encrypted': 'injected'},
    {'host': 'localhost'}, {'port': '22'}, {'username': '-root'}, {'password': 'a' * 1025},
    {'password': 'no\0nul'},
])
def test_strict_update_validation(client, auth_headers, change):
    result = create(client, auth_headers)
    response = client.patch(URL + '/' + result['id'], headers=auth_headers, json=change)
    assert response.status_code == 422, response.text
    assert PASSWORD not in response.text


@pytest.mark.asyncio
async def test_admin_only_and_tenant_isolation(client, auth_headers, test_db, test_user):
    from models import Tenant
    from models.gpu_server import GpuServer
    from services.auth import create_access_token
    foreign_id = uuid4()
    test_db.add(Tenant(id=2, tenant_name='foreign')); await test_db.commit()
    test_db.add(GpuServer(id=foreign_id, tenant_id=2, name='foreign', host='10.0.0.1',
                         username='root', password_encrypted=None))
    await test_db.commit()
    assert len(client.get(URL, headers=auth_headers).json()) == 1
    for method, url, body in [('get', f'{URL}/{foreign_id}/resources', None),
        ('patch', f'{URL}/{foreign_id}', {'name': 'stolen'}), ('delete', f'{URL}/{foreign_id}', None)]:
        kwargs = {'headers': auth_headers}
        if body is not None: kwargs['json'] = body
        assert getattr(client, method)(url, **kwargs).status_code == 404
    test_user.role = 'annotator'; await test_db.commit()
    headers = {'Authorization': 'Bearer ' + create_access_token({'user_id': test_user.id})}
    for method, url, body in [('get', URL, None), ('post', URL, payload()),
        ('post', URL + '/test', payload()), ('get', f'{URL}/{foreign_id}/resources', None),
        ('patch', f'{URL}/{foreign_id}', {'enabled': False}), ('delete', f'{URL}/{foreign_id}', None)]:
        kwargs = {} if body is None else {'json': body}
        assert getattr(client, method)(url, headers=headers, **kwargs).status_code == 403
        assert getattr(client, method)(url, **kwargs).status_code == 401


@pytest.mark.asyncio
async def test_limit_is_per_tenant(client, auth_headers, test_db):
    from models import Tenant
    from models.gpu_server import GpuServer
    test_db.add(Tenant(id=2, tenant_name='another tenant'))
    await test_db.commit()
    test_db.add_all([GpuServer(tenant_id=2, name=f'foreign-{n}', host='127.0.0.1') for n in range(200)])
    test_db.add_all([GpuServer(tenant_id=1, name=f'gpu-{n}', host='127.0.0.1') for n in range(199)])
    await test_db.commit()
    create(client, auth_headers)
    assert client.post(URL, headers=auth_headers, json=payload()).status_code == 409
    assert await test_db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id == 1)) == 200
    assert await test_db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id == 2)) == 200


@pytest.mark.asyncio
async def test_unsaved_test_uses_fresh_ids_and_does_not_write(client, auth_headers, test_db, monkeypatch):
    from models.gpu_server import GpuServer
    from services import gpu_resources
    calls = []
    def collect(*args):
        calls.append(args)
        assert args[1:3] == (0, 'lab GPU')
        assert args[3] == dict(host='2001:db8::1', port=22, username='root', password=PASSWORD)
        return fake_snapshot(*args)
    monkeypatch.setattr(gpu_resources, 'snapshot_connection', collect, raising=False)
    for _ in range(2):
        response = client.post(URL + '/test', headers=auth_headers, json=payload())
        assert response.status_code == 200, response.text
        assert response.json()['server_name'] == 'lab GPU'
        assert response.headers['cache-control'] == 'no-store'
        assert PASSWORD not in response.text
    assert calls[0][0].startswith('test-') and calls[0][0] != calls[1][0]
    assert await test_db.scalar(select(func.count()).select_from(GpuServer)) == 0


def test_saved_resources_use_captured_configuration(client, auth_headers, monkeypatch):
    from services import gpu_resources
    result = create(client, auth_headers)
    calls = []
    def collect(*args):
        calls.append(args)
        return fake_snapshot(*args)
    monkeypatch.setattr(gpu_resources, 'snapshot_connection', collect, raising=False)
    response = client.get(URL + '/' + result['id'] + '/resources', headers=auth_headers)
    assert response.status_code == 200, response.text
    assert calls[0][:3] == (result['id'], 1, 'lab GPU')
    assert calls[0][3]['password'] == PASSWORD
    assert response.headers['cache-control'] == 'no-store'
    response = client.patch(URL + '/' + result['id'], headers=auth_headers, json={'enabled': False})
    assert response.status_code == 200
    assert client.get(URL + '/' + result['id'] + '/resources', headers=auth_headers).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['edit', 'disable', 'delete'])
def test_inflight_change_rejects_old_snapshot(client, auth_headers, test_engine, monkeypatch, mutation):
    import asyncio
    from models.gpu_server import GpuServer
    from services import gpu_resources
    result = create(client, auth_headers)
    async def mutate():
        async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
            row = await session.get(GpuServer, UUID(result['id']))
            if mutation == 'delete':
                await session.delete(row)
            else:
                row.revision += 1
                row.enabled = mutation != 'disable'
            await session.commit()
    def collect(*args):
        asyncio.run(mutate())
        return fake_snapshot(*args)
    monkeypatch.setattr(gpu_resources, 'snapshot_connection', collect, raising=False)
    response = client.get(URL + '/' + result['id'] + '/resources', headers=auth_headers)
    assert response.status_code == (404 if mutation == 'delete' else 409), response.text
    assert 'collected_at' not in response.text


@pytest.mark.parametrize('failure', [RuntimeError(PASSWORD), HTTPException(503, PASSWORD), HTTPException(504, PASSWORD)])
def test_collection_errors_do_not_expose_password(client, auth_headers, monkeypatch, failure, caplog):
    from services import gpu_resources
    def failed(*args): raise failure
    monkeypatch.setattr(gpu_resources, 'snapshot_connection', failed, raising=False)
    result = create(client, auth_headers)
    for method, url, kwargs in [('post', URL + '/test', {'json': payload()}),
        ('get', URL + '/' + result['id'] + '/resources', {})]:
        response = getattr(client, method)(url, headers=auth_headers, **kwargs)
        assert response.status_code == (504 if isinstance(failure, HTTPException) and failure.status_code == 504 else 503)
        assert 'p@ss' not in response.text and 'p@ss' not in caplog.text


def test_key_derivation_explicit_key_and_rotation(monkeypatch):
    import base64
    import hashlib
    from types import SimpleNamespace
    from cryptography.fernet import Fernet
    from fastapi import HTTPException
    from services import gpu_servers
    config = SimpleNamespace(JWT_SECRET_KEY='j' * 40, GPU_CREDENTIAL_KEY='')
    monkeypatch.setattr(gpu_servers, 'settings', config)
    encrypted = gpu_servers.encrypt_password(PASSWORD)
    key = base64.urlsafe_b64encode(hashlib.sha256(
        b'data-platform-gpu-credentials-v1\0' + config.JWT_SECRET_KEY.encode()).digest())
    assert Fernet(key).decrypt(encrypted.encode()).decode() == PASSWORD
    config.GPU_CREDENTIAL_KEY = Fernet.generate_key().decode()
    explicit = gpu_servers.encrypt_password(PASSWORD)
    assert Fernet(config.GPU_CREDENTIAL_KEY.encode()).decrypt(explicit.encode()).decode() == PASSWORD
    with pytest.raises(HTTPException) as error: gpu_servers.decrypt_password(encrypted)
    assert error.value.status_code == 409 and encrypted not in str(error.value)
    config.GPU_CREDENTIAL_KEY = PASSWORD
    with pytest.raises(HTTPException) as error: gpu_servers.encrypt_password(PASSWORD)
    assert error.value.status_code == 503 and PASSWORD not in str(error.value)


def test_secret_schema_repr_and_validation_errors_hide_password():
    from schemas.gpu_server import GpuServerCreate
    from pydantic import SecretStr, ValidationError
    request = GpuServerCreate(**payload())
    assert isinstance(request.password, SecretStr) and PASSWORD not in repr(request)
    with pytest.raises(ValidationError) as error:
        GpuServerCreate(**payload(password='private' * 200))
    assert 'private' not in str(error.value)
