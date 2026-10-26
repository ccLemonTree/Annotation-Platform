"""Cleanup tests use only isolated SQLite databases and temporary storage."""
import os
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.fixture
def cleanup_client(test_engine, test_user, tmp_path, monkeypatch):
    from api.storage_cleanup_routes import router
    from config.settings import settings
    from database import get_db_session
    from integrations.smarteye import runtime
    from services.storage import reset_storage

    root = tmp_path / 'storage'
    root.mkdir()
    monkeypatch.setattr(settings, 'STORAGE_TYPE', 'local')
    monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(root))
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    runtime.metadata.create_all(engine)
    monkeypatch.setattr(runtime, '_runtime', runtime.Runtime(engine, root))
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)

    async def isolated_db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix='/api/storage-maintenance')
    app.dependency_overrides[get_db_session] = isolated_db
    reset_storage()
    with TestClient(app) as client:
        yield client, root, engine
    reset_storage()
    engine.dispose()


def original(root, char='a', *, old=True):
    key = f'images/{char * 2}/{char * 64}.jpg'
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'original')
    if old:
        before = time.time() - 9 * 86400
        os.utime(path, (before, before))
    return key


def preview(fixture, headers):
    return fixture[0].get('/api/storage-maintenance/preview', headers=headers)


def execute(fixture, headers, paths):
    return fixture[0].post('/api/storage-maintenance/execute', headers=headers,
                           json={'paths': paths, 'confirmation': 'QUARANTINE'})


def test_preview_is_read_only_and_honors_grace(cleanup_client, auth_headers):
    client, root, _ = cleanup_client
    key = original(root)
    original(root, 'b', old=False)
    (root / '.backups').mkdir()
    (root / '.backups' / 'keep.jpg').write_bytes(b'backup')
    (root / '.incoming').mkdir()
    (root / '.incoming' / 'keep.part').write_bytes(b'upload')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    response = preview(cleanup_client, auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [item['path'] for item in data['items']] == [key]
    assert data['candidate_count'] == 1
    assert data['protected_counts']['grace_period'] == 1
    assert data['execution_available'] is False
    assert data['blocked_reason'] == 'concurrency_guard_unavailable'
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.asyncio
async def test_cross_tenant_library_and_asset_references(cleanup_client, auth_headers, test_db, test_project):
    from models import Asset, LibraryImage, Tenant
    root = cleanup_client[1]
    first, second = original(root), original(root, 'b')
    test_db.add(Tenant(id=2, tenant_name='foreign'))
    await test_db.flush()
    test_db.add(LibraryImage(tenant_id=2, blob_path=first, content_hash='a' * 64,
        original_name='foreign.jpg', file_size=8, source_type='upload'))
    test_db.add(Asset(project_id=test_project.id, blob_path=str(root / second), content_hash='b' * 64,
        original_name='legacy.jpg', file_size=8, source_type='upload'))
    await test_db.commit()
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['protected_counts']['library_image'] == 1
    assert data['protected_counts']['asset'] == 1


def test_runtime_download_without_library_registration_is_protected(cleanup_client, auth_headers):
    from integrations.smarteye.runtime import files
    _, root, engine = cleanup_client
    key = original(root)
    with engine.begin() as conn:
        conn.execute(insert(files).values(id=str(uuid4()), task_id='download-only', relative_path='smarteye/a.jpg',
            blob_path=key, content_hash='a' * 64, metadata={}))
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['protected_counts']['smarteye_runtime'] == 1


@pytest.mark.asyncio
async def test_snapshot_manifest_hash_protects_original(cleanup_client, auth_headers, test_db, test_project):
    from models import ExportSnapshot
    original(cleanup_client[1])
    test_db.add(ExportSnapshot(project_id=test_project.id, user_id=1, export_format='yolo',
        blob_path='exports/frozen.zip', content_hash='f' * 64,
        manifest={'assets': [{'content_hash': 'a' * 64}]}))
    await test_db.commit()
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['protected_counts']['frozen_export'] == 1


@pytest.mark.asyncio
async def test_legacy_download_hash_protects_shared_content(cleanup_client, auth_headers, test_db):
    from models import SmarteeyeDownload
    original(cleanup_client[1])
    conn = await test_db.connection()
    await conn.run_sync(lambda connection: SmarteeyeDownload.__table__.create(connection))
    test_db.add(SmarteeyeDownload(id=1, task_id=1, blob_path='smarteye/legacy/a.jpg', content_hash='a' * 64,
        file_name='a.jpg', file_size=8, mime_type='image/jpeg', source_url='https://example.test/a.jpg', platform='test'))
    await test_db.commit()
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['protected_counts']['smarteye_legacy'] == 1


@pytest.mark.asyncio
async def test_main_reference_schema_failure_is_not_a_safe_empty_result(cleanup_client, auth_headers, test_db):
    original(cleanup_client[1])
    await test_db.execute(text('ALTER TABLE images RENAME COLUMN blob_path TO absent'))
    await test_db.commit()
    response = preview(cleanup_client, auth_headers)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'reference_check_failed'


def test_hardlinks_and_unknown_layout_are_never_candidates(cleanup_client, auth_headers):
    _, root, _ = cleanup_client
    key = original(root)
    os.link(root / key, root / 'shared.jpg')
    (root / 'images' / 'aa' / 'unknown.jpg').write_bytes(b'unknown')
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['protected_counts']['hardlink'] == 1


def test_missing_runtime_tables_fail_closed(cleanup_client, auth_headers):
    _, root, engine = cleanup_client
    key = original(root)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE smarteye_files'))
    response = preview(cleanup_client, auth_headers)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'reference_check_failed'
    assert (root / key).exists()


def test_runtime_schema_drift_fails_closed(cleanup_client, auth_headers):
    with cleanup_client[2].begin() as conn:
        conn.execute(text('DROP TABLE smarteye_files'))
        conn.execute(text('CREATE TABLE smarteye_files (blob_path TEXT)'))
    response = preview(cleanup_client, auth_headers)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'reference_check_failed'


def test_active_download_protects_even_unregistered_files(cleanup_client, auth_headers):
    from integrations.smarteye.runtime import jobs
    original(cleanup_client[1])
    with cleanup_client[2].begin() as conn:
        conn.execute(insert(jobs).values(task_id='running', kind='detection', tenant_id=2,
            user_id=2, attempt=1, status='running', payload={}))
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['active_downloads'] == 1
    assert data['protected_counts']['active_downloads'] == 1


@pytest.mark.asyncio
async def test_active_foreign_upload_protects_all_originals(cleanup_client, auth_headers, test_db, test_tenant, test_user):
    from models import ImageCollection, ImageUpload, Tenant, User
    original(cleanup_client[1])
    test_db.add(Tenant(id=2, tenant_name='foreign'))
    await test_db.flush()
    test_db.add(User(id=2, tenant_id=2, username='other', email='other@example.test', password_hash='unused', role='user'))
    group = ImageCollection(tenant_id=2, name='upload')
    test_db.add(group)
    await test_db.flush()
    test_db.add(ImageUpload(tenant_id=2, user_id=2, collection_id=group.id, filename='a.zip', size=8,
        chunk_hashes=['a' * 64], resume_key='a' * 64, min_long_edge=0, status='processing'))
    await test_db.commit()
    data = preview(cleanup_client, auth_headers).json()
    assert data['candidate_count'] == 0
    assert data['active_uploads'] == 1
    assert data['protected_counts']['active_uploads'] == 1


def test_symlinked_hash_directory_is_not_traversed(cleanup_client, auth_headers, tmp_path):
    root = cleanup_client[1]
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / ('a' * 64 + '.jpg')).write_bytes(b'outside')
    (root / 'images').mkdir()
    try:
        (root / 'images' / 'aa').symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows directory junctions do not require symlink privileges.
        import subprocess
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root / 'images' / 'aa'), str(outside)],
                                capture_output=True)
        assert result.returncode == 0, result.stderr
    assert preview(cleanup_client, auth_headers).json()['scanned_count'] == 0
    response = execute(cleanup_client, auth_headers, ['images/aa/' + 'a' * 64 + '.jpg'])
    assert response.status_code == 409
    assert response.json()['detail']['revalidated'][0]['reasons'] == ['linked_path']
    assert (outside / ('a' * 64 + '.jpg')).read_bytes() == b'outside'


def test_backup_root_is_rejected(cleanup_client, auth_headers, monkeypatch):
    from config.settings import settings
    backup = cleanup_client[1] / '.backups'
    backup.mkdir()
    original(backup)
    monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(backup))
    response = preview(cleanup_client, auth_headers)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'unsafe_storage_root'


def test_nonlocal_storage_is_rejected(cleanup_client, auth_headers, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, 'STORAGE_TYPE', 'eos')
    assert preview(cleanup_client, auth_headers).status_code == 409


def test_bounded_preview_and_confirmation(cleanup_client, auth_headers):
    original(cleanup_client[1])
    original(cleanup_client[1], 'b')
    response = cleanup_client[0].get('/api/storage-maintenance/preview?limit=1', headers=auth_headers)
    assert response.json()['candidate_count'] == 2
    assert response.json()['truncated'] is True
    assert len(response.json()['items']) == 1
    response = cleanup_client[0].post('/api/storage-maintenance/execute', headers=auth_headers,
        json={'paths': ['images/aa/' + 'a' * 64 + '.jpg']})
    assert response.status_code == 422
    response = cleanup_client[0].post('/api/storage-maintenance/execute', headers=auth_headers,
        json={'paths': ['images/aa/' + 'a' * 64 + '.jpg'], 'confirmation': 'QUARANTINE', 'root': '/tmp'})
    assert response.status_code == 422


@pytest.mark.parametrize('path', ['../outside.jpg', '/tmp/a.jpg', 'C:/a.jpg', '.backups/a.jpg',
    'images/aa/unknown.jpg', 'images/ab/' + 'a' * 64 + '.jpg', 'images\\aa\\' + 'a' * 64 + '.jpg'])
def test_execute_rejects_untrusted_paths(cleanup_client, auth_headers, path):
    response = execute(cleanup_client, auth_headers, [path])
    assert response.status_code == 422


def test_execute_refuses_uncoordinated_concurrency_without_writes(cleanup_client, auth_headers):
    _, root, _ = cleanup_client
    key = original(root)
    response = execute(cleanup_client, auth_headers, [key])
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'concurrency_guard_unavailable'
    assert response.json()['detail']['revalidated'][0]['reasons'] == []
    assert (root / key).read_bytes() == b'original'
    assert not (root / '.quarantine').exists()


def test_execute_rechecks_new_runtime_reference(cleanup_client, auth_headers):
    from integrations.smarteye.runtime import files
    _, root, engine = cleanup_client
    key = original(root)
    assert preview(cleanup_client, auth_headers).json()['candidate_count'] == 1
    with engine.begin() as conn:
        conn.execute(insert(files).values(id=str(uuid4()), task_id='late', relative_path='smarteye/a.jpg',
            blob_path=key, content_hash='a' * 64, metadata={}))
    response = execute(cleanup_client, auth_headers, [key])
    assert response.status_code == 409
    assert 'smarteye_runtime' in response.json()['detail']['revalidated'][0]['reasons']
    assert (root / key).exists()


@pytest.mark.asyncio
async def test_execute_rechecks_new_library_registration(cleanup_client, auth_headers, test_db, test_tenant):
    from models import LibraryImage
    key = original(cleanup_client[1])
    assert preview(cleanup_client, auth_headers).json()['candidate_count'] == 1
    test_db.add(LibraryImage(tenant_id=test_tenant.id, blob_path=key, content_hash='a' * 64,
        original_name='registered-later.jpg', file_size=8, source_type='upload'))
    await test_db.commit()
    response = execute(cleanup_client, auth_headers, [key])
    assert response.status_code == 409
    assert 'library_image' in response.json()['detail']['revalidated'][0]['reasons']
    assert (cleanup_client[1] / key).exists()


@pytest.mark.parametrize('method', ['get', 'post'])
def test_authentication_required(cleanup_client, method):
    client = cleanup_client[0]
    if method == 'get':
        response = client.get('/api/storage-maintenance/preview')
    else:
        response = client.post('/api/storage-maintenance/execute', json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ordinary_user_forbidden(cleanup_client, auth_headers, test_db, test_user):
    test_user.role = 'annotator'
    await test_db.commit()
    assert preview(cleanup_client, auth_headers).status_code == 403
    assert execute(cleanup_client, auth_headers, ['images/aa/' + 'a' * 64 + '.jpg']).status_code == 403
