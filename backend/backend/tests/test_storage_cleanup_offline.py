"""Offline archive/restore only ever touches owned disposable PostgreSQL + tmp_path."""
import hashlib
import json
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


def test_offline_requires_explicit_confirmation(tmp_path):
    from services import storage_cleanup as cleanup
    assert hasattr(cleanup, 'offline_cleanup'), 'offline archive and restore are not implemented'
    with pytest.raises(ValueError, match='confirm-writers-stopped'):
        cleanup.offline_cleanup('archive', 'postgresql://invalid/unused', str(tmp_path), False)


def test_cli_requires_stop_confirmation_before_connecting(tmp_path):
    from services.storage_cleanup import main
    with pytest.raises(SystemExit) as error:
        main(['archive', '--database-url', 'postgresql://invalid/unused', '--storage', str(tmp_path)])
    assert error.value.code == 2


def test_storage_maintenance_lock_is_exclusive(tmp_path):
    from services.storage_cleanup import _storage_lock
    with _storage_lock(tmp_path):
        with pytest.raises(OSError):
            with _storage_lock(tmp_path):
                pytest.fail('second cleanup acquired the same storage lock')


@pytest.fixture
def offline_db(tmp_path):
    if os.environ.get('P0_STORAGE_CLEANUP_POSTGRES') != '1':
        pytest.skip('explicit isolated PostgreSQL tests required')
    from config.settings import settings
    from integrations.smarteye.runtime import metadata
    from models import Base, Tenant
    name = 'codex_cleanup_' + uuid4().hex
    control_url = make_url(settings.DATABASE_URL).set(drivername='postgresql+psycopg2', database='postgres')
    control = create_engine(control_url, isolation_level='AUTOCOMMIT', poolclass=NullPool)
    engine = None
    created = False
    try:
        with control.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        url = control_url.set(database=name)
        engine = create_engine(url, poolclass=NullPool)
        tables = [Base.metadata.tables[key] for key in ('tenants', 'users', 'projects', 'images',
            'image_collections', 'image_collection_members', 'image_ingest_settings', 'image_uploads',
            'image_upload_items', 'assets', 'annotations', 'annotation_events', 'export_snapshots', 'training_tasks')]
        Base.metadata.create_all(engine, tables=tables)
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(insert(Tenant).values(id=1, tenant_name='isolated'))
        root = tmp_path / 'storage'
        root.mkdir()
        yield url, root, engine
    finally:
        if engine:
            engine.dispose()
        if created:
            assert name.startswith('codex_cleanup_') and name not in ('postgres', settings.DB_NAME)
            with control.connect() as conn:
                conn.exec_driver_sql(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        control.dispose()


def image(root, content=b'orphan', *, old=True):
    digest = hashlib.sha256(content).hexdigest()
    key = f'images/{digest[:2]}/{digest}.jpg'
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if old:
        before = time.time() - 9 * 86400
        os.utime(path, (before, before))
    return key


def run(fixture, action='archive', **kwargs):
    from services.storage_cleanup import offline_cleanup
    return offline_cleanup(action, fixture[0], str(fixture[1]), True, **kwargs)


def test_archive_and_restore_preserve_references_and_backups(offline_db):
    from integrations.smarteye.runtime import files
    from models import ExportSnapshot, LibraryImage, Project, Tenant, TaskType
    _, root, engine = offline_db
    orphan = image(root)
    library = image(root, b'library')
    download = image(root, b'download')
    frozen = image(root, b'frozen')
    shared = image(root, b'hardlink')
    young = image(root, b'young', old=False)
    os.link(root / shared, root / 'shared.jpg')
    (root / '.backups').mkdir()
    (root / '.backups' / 'keep').write_bytes(b'backup')
    (root / '.incoming').mkdir()
    (root / '.incoming' / 'keep').write_bytes(b'staging')
    with engine.begin() as conn:
        conn.execute(insert(Tenant).values(id=2, tenant_name='other'))
        conn.execute(insert(LibraryImage).values(id=uuid4(), tenant_id=2, blob_path=library,
            content_hash=hashlib.sha256(b'library').hexdigest(), original_name='a.jpg', file_size=7, source_type='upload'))
        conn.execute(insert(files).values(id=str(uuid4()), task_id='download', relative_path='smarteye/a.jpg',
            blob_path=download, content_hash=hashlib.sha256(b'download').hexdigest(), metadata={}))
        project = uuid4()
        conn.execute(insert(Project).values(id=project, tenant_id=1, project_name='test', task_type=TaskType.DETECTION))
        conn.execute(insert(ExportSnapshot).values(id=uuid4(), project_id=project, user_id=1, export_format='yolo',
            blob_path='exports/frozen.zip', content_hash='f' * 64, manifest={'assets': [{'content_hash': hashlib.sha256(b'frozen').hexdigest()}]}))
    result = run(offline_db)
    assert result['moved_count'] == 1
    run_dir = root / '.quarantine' / result['run_id']
    assert not (root / orphan).exists()
    assert (run_dir / orphan).read_bytes() == b'orphan'
    for key in (library, download, frozen, shared, young):
        assert (root / key).exists()
    assert (root / '.backups' / 'keep').read_bytes() == b'backup'
    assert (root / '.incoming' / 'keep').read_bytes() == b'staging'
    manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert [entry['path'] for entry in manifest['files']] == [orphan]
    assert 'password' not in json.dumps(manifest).lower()
    restored = run(offline_db, 'restore', run_id=result['run_id'])
    assert restored['moved_count'] == 1
    assert (root / orphan).read_bytes() == b'orphan'
    assert run(offline_db, 'restore', run_id=result['run_id'])['moved_count'] == 0


def test_existing_idle_business_connection_refuses_archive(offline_db):
    from services.storage_cleanup import MaintenanceRefused
    key = image(offline_db[1])
    with offline_db[2].connect():
        with pytest.raises(MaintenanceRefused, match='connections'):
            run(offline_db)
    assert (offline_db[1] / key).exists()
    assert not (offline_db[1] / '.quarantine').exists()


def test_database_fence_rejects_new_connections_and_recovers(offline_db):
    from services.storage_cleanup import _offline_database
    from sqlalchemy.exc import OperationalError
    with _offline_database(offline_db[0]) as conn:
        assert conn.scalar(text('SELECT current_database()')).startswith('codex_cleanup_')
        with pytest.raises(OperationalError):
            with offline_db[2].connect():
                pytest.fail('database accepted a new connection while fenced')
    with offline_db[2].connect() as conn:
        assert conn.scalar(text('SELECT 1')) == 1


@pytest.mark.parametrize('kind', ['download', 'scene', 'upload', 'training'])
def test_pending_tasks_refuse_archive(offline_db, kind):
    from integrations.smarteye.runtime import jobs
    from models import ImageCollection, ImageUpload, LibraryImage, Project, TaskType, TrainingTask, TrainingStatus, User
    from services.storage_cleanup import MaintenanceRefused
    key = image(offline_db[1])
    with offline_db[2].begin() as conn:
        if kind == 'download':
            conn.execute(insert(jobs).values(task_id='pending', kind='detection', tenant_id=1, user_id=1,
                attempt=1, status='pending', payload={}))
        elif kind == 'scene':
            conn.execute(insert(LibraryImage).values(id=uuid4(), tenant_id=1, blob_path='other.jpg',
                content_hash='other', original_name='a.jpg', file_size=1, source_type='upload', scene_status='processing'))
        elif kind == 'upload':
            group = uuid4()
            conn.execute(insert(User).values(id=1, tenant_id=1, username='test', email='test@example.test', password_hash='unused'))
            conn.execute(insert(ImageCollection).values(id=group, tenant_id=1, name='test'))
            conn.execute(insert(ImageUpload).values(id=uuid4(), tenant_id=1, user_id=1, collection_id=group,
                filename='a.zip', size=1, chunk_hashes=['a' * 64], resume_key='a' * 64, min_long_edge=0, status='processing'))
        else:
            project = uuid4()
            conn.execute(insert(Project).values(id=project, tenant_id=1, project_name='test', task_type=TaskType.DETECTION))
            conn.execute(insert(TrainingTask).values(id=1, project_id=project, task_name='test', framework='test',
                model_type='test', config_yaml='', dataset_version='test', status=TrainingStatus.RUNNING))
    with pytest.raises(MaintenanceRefused, match='tasks'):
        run(offline_db)
    assert (offline_db[1] / key).exists()


def test_missing_runtime_table_refuses_without_file_moves(offline_db):
    key = image(offline_db[1])
    with offline_db[2].begin() as conn:
        conn.execute(text('DROP TABLE smarteye_files'))
    from sqlalchemy.exc import SQLAlchemyError
    with pytest.raises(SQLAlchemyError):
        run(offline_db)
    assert (offline_db[1] / key).exists()
    with offline_db[2].connect() as conn:
        assert conn.scalar(text('SELECT 1')) == 1


def test_interrupted_archive_has_recoverable_manifest(offline_db, monkeypatch):
    from services import storage_cleanup as cleanup
    keys = [image(offline_db[1], b'first'), image(offline_db[1], b'second')]
    rename = cleanup.os.rename
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('injected move failure')
        return rename(source, target)

    monkeypatch.setattr(cleanup.os, 'rename', fail_second)
    with pytest.raises(OSError, match='injected'):
        run(offline_db)
    monkeypatch.setattr(cleanup.os, 'rename', rename)
    run_id = next((offline_db[1] / '.quarantine').iterdir()).name
    assert run(offline_db, 'restore', run_id=run_id)['moved_count'] == 1
    assert all((offline_db[1] / key).exists() for key in keys)


def test_restore_never_overwrites_or_accepts_tampered_paths(offline_db):
    from services.storage_cleanup import MaintenanceRefused
    root = offline_db[1]
    key = image(root)
    result = run(offline_db)
    (root / key).write_bytes(b'new-original')
    with pytest.raises(MaintenanceRefused, match='exists'):
        run(offline_db, 'restore', run_id=result['run_id'])
    assert (root / key).read_bytes() == b'new-original'
    manifest_path = root / '.quarantine' / result['run_id'] / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files'][0]['path'] = '../outside'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(MaintenanceRefused, match='manifest'):
        run(offline_db, 'restore', run_id=result['run_id'])


def test_manifest_write_failure_does_not_move_any_original(offline_db, monkeypatch):
    from services import storage_cleanup as cleanup
    key = image(offline_db[1])

    def fail_write(*args, **kwargs):
        raise OSError('injected manifest write failure')

    monkeypatch.setattr(cleanup.json, 'dump', fail_write)
    with pytest.raises(OSError, match='manifest write failure'):
        run(offline_db)
    assert (offline_db[1] / key).read_bytes() == b'orphan'
    with offline_db[2].connect() as conn:
        assert conn.scalar(text('SELECT 1')) == 1


def test_hash_mismatch_refuses_archive_before_any_move(offline_db):
    from services.storage_cleanup import MaintenanceRefused
    key = image(offline_db[1])
    (offline_db[1] / key).write_bytes(b'wrong-content')
    before = time.time() - 9 * 86400
    os.utime(offline_db[1] / key, (before, before))
    with pytest.raises(MaintenanceRefused, match='hash'):
        run(offline_db)
    assert (offline_db[1] / key).read_bytes() == b'wrong-content'
    assert not (offline_db[1] / '.quarantine').exists()
