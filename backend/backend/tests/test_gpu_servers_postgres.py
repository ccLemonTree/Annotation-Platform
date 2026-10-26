"""GPU credential schema changes use only disposable PostgreSQL databases."""
from pathlib import Path
import importlib.util
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.test_database_closeout_postgres import isolated_pg, pre_013, bootstrap_module


@pytest_asyncio.fixture
async def test_engine(isolated_pg):
    sync_engine, _ = isolated_pg
    with sync_engine.begin() as conn:
        bootstrap_module().bootstrap_database(conn)
    engine = create_async_engine(sync_engine.url.set(drivername='postgresql+asyncpg'), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


def test_gpu_server_migration_is_additive():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/017_gpu_servers.py'
    assert path.is_file(), '缺少GPU连接配置增量迁移'
    spec = importlib.util.spec_from_file_location('gpu_server_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    assert migration.revision == '017' and migration.down_revision == '016'


def test_017_preserves_rows_and_matches_gpu_model(isolated_pg):
    from models.gpu_server import GpuServer
    engine, config = isolated_pg
    pre_013(engine, config)
    command.upgrade(config, '016')
    with engine.connect() as conn:
        before = conn.scalar(text('SELECT tenant_name FROM tenants WHERE id=1'))
    command.upgrade(config, '017')
    with engine.connect() as conn:
        inspector = inspect(conn)
        expected = GpuServer.__table__
        columns = inspector.get_columns('gpu_servers')
        assert {column['name'] for column in columns} == set(expected.c.keys())
        for column in columns:
            assert column['type'].compile(dialect=conn.dialect) == expected.c[column['name']].type.compile(dialect=conn.dialect)
            assert column['nullable'] == expected.c[column['name']].nullable
        assert {item['name'] for item in inspector.get_indexes('gpu_servers') if not item.get('duplicates_constraint')} == {index.name for index in expected.indexes}
        assert len(inspector.get_foreign_keys('gpu_servers')) == len(expected.foreign_key_constraints)
        assert conn.scalar(text('SELECT tenant_name FROM tenants WHERE id=1')) == before
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '017'
    with pytest.raises(RuntimeError, match='禁止|refus'):
        command.downgrade(config, '016')


def test_017_refuses_existing_table_without_overwriting(isolated_pg):
    engine, config = isolated_pg
    pre_013(engine, config)
    command.upgrade(config, '016')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE gpu_servers (marker TEXT)'))
        conn.execute(text("INSERT INTO gpu_servers VALUES ('preserve')"))
    with pytest.raises(RuntimeError, match='已有|覆盖|refus'):
        command.upgrade(config, '017')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT marker FROM gpu_servers')) == 'preserve'
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '016'


@pytest.mark.asyncio
async def test_pg_password_crud_and_collection(client, auth_headers, test_db, monkeypatch):
    from uuid import UUID
    from models.gpu_server import GpuServer
    from services import gpu_servers
    from tests.test_gpu_servers import create, PASSWORD
    from tests.test_gpu_resources import sample
    from services.gpu_resources import parse_sample
    calls = []
    def collect(id, revision, name, connection):
        calls.append(connection)
        return dict(server_name=name, collected_at='2026-10-16T00:00:00Z', cached=False, **parse_sample(sample()))
    monkeypatch.setattr(gpu_servers, 'collect_snapshot', collect)
    created = create(client, auth_headers, host='192.0.2.60')
    path = '/api/gpu-resources/servers/' + created['id']
    row = await test_db.get(GpuServer, UUID(created['id']))
    original_cipher = row.password_encrypted
    assert PASSWORD not in original_cipher
    response = client.get(path + '/resources', headers=auth_headers)
    assert response.status_code == 200 and calls[-1]['password'] == PASSWORD
    assert PASSWORD not in response.text and original_cipher not in response.text
    renamed = client.patch(path, headers=auth_headers, json={'name': 'PG changed'})
    assert renamed.status_code == 200 and renamed.json()['revision'] == 2
    await test_db.refresh(row)
    assert row.password_encrypted == original_cipher
    assert client.patch(path, headers=auth_headers, json={'host': '192.0.2.61'}).status_code == 422
    changed = client.patch(path, headers=auth_headers, json={'host': '192.0.2.61', 'password': 'next-fixture-password'})
    assert changed.status_code == 200 and changed.json()['revision'] == 3
    await test_db.refresh(row)
    assert row.password_encrypted != original_cipher
    assert gpu_servers.decrypt_password(row.password_encrypted) == 'next-fixture-password'
    assert client.patch(path, headers=auth_headers, json={'clear_password': True}).json()['has_password'] is False
    count = len(calls)
    assert client.get(path + '/resources', headers=auth_headers).status_code == 409
    assert len(calls) == count
    assert client.delete(path, headers=auth_headers).status_code == 204
    assert client.get(path + '/resources', headers=auth_headers).status_code == 404
    assert len(client.get('/api/gpu-resources/servers', headers=auth_headers).json()) == 1


def test_pg_config_edit_rejects_inflight_old_result(client, auth_headers, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from services import gpu_servers
    from tests.test_gpu_servers import create, fake_snapshot
    started, release = Event(), Event()
    created = create(client, auth_headers, host='192.0.2.62')
    path = '/api/gpu-resources/servers/' + created['id']
    def delayed(*args):
        started.set()
        assert release.wait(10)
        return fake_snapshot(*args)
    monkeypatch.setattr(gpu_servers, 'collect_snapshot', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(client.get, path + '/resources', headers=auth_headers)
        try:
            assert started.wait(5)
            assert client.patch(path, headers=auth_headers, json={'name': 'new snapshot target'}).status_code == 200
        finally:
            release.set()
        result = request.result(timeout=10)
    assert result.status_code == 409 and 'gpus' not in result.json()
