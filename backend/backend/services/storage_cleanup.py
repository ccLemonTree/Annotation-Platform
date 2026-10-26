"""Online read-only inspection and offline reversible quarantine; never unlink originals."""
import asyncio
import hashlib
import json
import os
import re
import stat
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from config.settings import settings
from models import Asset, ExportSnapshot, ImageUpload, LibraryImage, SmarteeyeDownload
from services.storage import LocalStorage, get_storage

GRACE_SECONDS = 7 * 86400
KEY = re.compile(r'images/([0-9a-f]{2})/([0-9a-f]{64})\.(jpg|png|bmp|webp)')
BLOCKED_MESSAGE = '上传、图片注册与SmartEye下载尚未共享维护锁，无法证明隔离归档没有并发竞态；本次未移动或删除文件。'


def valid_key(key):
    match = KEY.fullmatch(key)
    return bool(match and match[1] == match[2][:2])


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _root():
    if settings.STORAGE_TYPE != 'local':
        raise HTTPException(409, {'code': 'local_storage_required', 'message': '仅支持本地存储'})
    configured = _checked_root(settings.STORAGE_LOCAL_PATH)
    storage = get_storage()
    if not isinstance(storage, LocalStorage) or storage.base_path != configured:
        raise HTTPException(409, {'code': 'storage_root_mismatch', 'message': '存储实例与配置不一致'})
    return configured


def _checked_root(path):
    configured = Path(path).absolute()
    if any(p.name.lower() == '.backups' or _linked(p) for p in (configured, *configured.parents)):
        raise HTTPException(409, {'code': 'unsafe_storage_root', 'message': '存储根不能经过链接或.backups'})
    if not configured.is_dir():
        raise HTTPException(409, {'code': 'storage_root_missing', 'message': '本地存储目录不存在'})
    if _linked(configured / 'images'):
        raise HTTPException(409, {'code': 'unsafe_images_root', 'message': 'images目录不能是链接'})
    return configured.resolve()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _remember(target, values, root):
    for value in values:
        value = value.replace('\\', '/')
        path = Path(value)
        if path.is_absolute() and path.is_relative_to(root):
            value = path.relative_to(root).as_posix()
        target.add(value.lower())


def _runtime_references(root):
    from integrations.smarteye.runtime import get_runtime, files, jobs
    runtime = get_runtime()
    if runtime.root != root:
        raise HTTPException(503, {'code': 'runtime_root_mismatch', 'message': 'SmartEye存储根不一致，停止扫描'})
    with runtime.engine.connect() as conn:
        # Query actual runtime tables, including download-only records without LibraryImage rows.
        rows = conn.execute(select(files.c.blob_path, files.c.content_hash, files.c.relative_path)).all()
        active = conn.scalar(select(func.count()).select_from(jobs).where(
            jobs.c.status.not_in(('completed', 'failed', 'cancelled', 'interrupted'))))
    refs = set()
    for row in rows:
        _remember(refs, row, root)
    return refs, active


def _reference_queries(legacy_exists, validation_exists=False, training_exists=False, matches_exists=False):
    queries = [('library_image', select(LibraryImage.blob_path, LibraryImage.content_hash)),
        ('asset', select(Asset.blob_path, Asset.content_hash, Asset.device_info)),
        ('frozen_export', select(ExportSnapshot.blob_path, ExportSnapshot.content_hash, ExportSnapshot.manifest))]
    if legacy_exists:
        queries.append(('smarteye_legacy', select(SmarteeyeDownload.blob_path, SmarteeyeDownload.content_hash)))
    if validation_exists:
        from models.validation import ValidationSet
        queries.append(('validation_set', select(ValidationSet.samples)))
    if training_exists:
        from models.training_run import TrainingRun
        queries.append(('training_run',select(TrainingRun.source)))
    if matches_exists:
        from models.project_work import MatchQuery
        queries.append(('match_query',select(MatchQuery.query,MatchQuery.hits)))
    return queries


async def _references(db, root):
    refs = {}
    try:
        connection = await db.connection()
        legacy_exists = await connection.run_sync(lambda conn: inspect(conn).has_table('smarteye_downloads'))
        validation_exists = await connection.run_sync(lambda conn: inspect(conn).has_table('validation_sets'))
        training_exists = await connection.run_sync(lambda conn: inspect(conn).has_table('training_runs'))
        matches_exists = await connection.run_sync(lambda conn: inspect(conn).has_table('match_queries'))
        for name, query in _reference_queries(legacy_exists, validation_exists, training_exists, matches_exists):
            refs[name] = set()
            for row in await db.execute(query):
                _remember(refs[name], _strings(list(row)), root)
        uploads = await db.scalar(select(func.count()).select_from(ImageUpload).where(ImageUpload.status != 'completed'))
        refs['smarteye_runtime'], downloads = await asyncio.to_thread(_runtime_references, root)
    except (SQLAlchemyError, OSError):
        raise HTTPException(503, {'code': 'reference_check_failed', 'message': '引用表核查失败，停止扫描'}) from None
    return refs, uploads, downloads


def _record(root, key, refs, uploads, downloads, now):
    path = root / key
    reasons = []
    if _linked(path.parent) or _linked(path):
        return {'path': key, 'size': 0, 'reasons': ['linked_path']}
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'path': key, 'size': 0, 'reasons': ['missing']}
    if not stat.S_ISREG(info.st_mode):
        reasons.append('not_regular')
    if info.st_nlink != 1:
        reasons.append('hardlink')
    if now - info.st_mtime < GRACE_SECONDS:
        reasons.append('grace_period')
    digest = KEY.fullmatch(key)[2]
    for name, values in refs.items():
        if key in values or digest in values:
            reasons.append(name)
    if uploads:
        reasons.append('active_uploads')
    if downloads:
        reasons.append('active_downloads')
    return {'path': key, 'size': info.st_size, 'modified_at': info.st_mtime, 'reasons': reasons}


def _keys(root):
    images = root / 'images'
    if not images.exists():
        return
    for prefix in sorted(images.iterdir()):
        if not re.fullmatch('[0-9a-f]{2}', prefix.name) or _linked(prefix) or not prefix.is_dir():
            continue
        for path in sorted(prefix.iterdir()):
            key = path.relative_to(root).as_posix()
            if valid_key(key):
                yield key


async def _audit(db, paths=None):
    try:
        root = _root()
        refs, uploads, downloads = await _references(db, root)
        now = time.time()
        records = [_record(root, key, refs, uploads, downloads, now)
                   for key in (_keys(root) if paths is None else paths)]
    except OSError:
        raise HTTPException(503, {'code': 'filesystem_check_failed', 'message': '文件系统核查失败，停止扫描'}) from None
    return records, uploads, downloads


async def preview_cleanup(db, limit=200):
    records, uploads, downloads = await _audit(db)
    candidates = [row for row in records if not row['reasons']]
    return {'items': candidates[:limit], 'candidate_count': len(candidates),
        'candidate_bytes': sum(row['size'] for row in candidates), 'scanned_count': len(records),
        'protected_counts': dict(Counter(reason for row in records for reason in row['reasons'])),
        'active_uploads': uploads, 'active_downloads': downloads, 'grace_days': GRACE_SECONDS // 86400,
        'truncated': len(candidates) > limit, 'execution_available': False,
        'blocked_reason': 'concurrency_guard_unavailable', 'message': BLOCKED_MESSAGE}


async def execute_cleanup(db, paths):
    if not paths or len(paths) > 100 or any(not valid_key(key) for key in paths):
        raise HTTPException(422, '仅允许最多100个已知images哈希路径')
    records, _, _ = await _audit(db, paths=list(dict.fromkeys(paths)))
    # shortcut: no writer-wide guard exists; add a shared guard to every writer before enabling quarantine.
    raise HTTPException(409, {'code': 'concurrency_guard_unavailable', 'message': BLOCKED_MESSAGE,
        'execution_available': False, 'revalidated': records})


class MaintenanceRefused(ValueError):
    """An offline prerequisite or integrity check failed; preserve every file."""


@contextmanager
def _offline_database(database_url):
    url = make_url(database_url)
    if url.drivername not in ('postgresql', 'postgresql+psycopg2') or not url.host or not url.database:
        raise MaintenanceRefused('explicit PostgreSQL host and database are required')
    if url.database in ('postgres', 'template0', 'template1'):
        raise MaintenanceRefused('system databases cannot be cleanup targets')
    engine = create_engine(url.set(drivername='postgresql+psycopg2'), poolclass=NullPool,
        isolation_level='AUTOCOMMIT', hide_parameters=True,
        connect_args={'connect_timeout': 5, 'application_name': 'storage_cleanup_offline'})
    control_engine = create_engine(url.set(drivername='postgresql+psycopg2', database='postgres'), poolclass=NullPool,
        isolation_level='AUTOCOMMIT', hide_parameters=True,
        connect_args={'connect_timeout': 5, 'application_name': 'storage_cleanup_control'})
    try:
        with engine.connect() as conn, control_engine.connect() as control:
            if not conn.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')):
                raise MaintenanceRefused('superuser required to fence connections and inspect every session')
            name = conn.scalar(text('SELECT current_database()'))
            if name != url.database:
                raise MaintenanceRefused('target database mismatch')
            cluster_query = text('SELECT system_identifier FROM pg_control_system()')
            if conn.scalar(cluster_query) != control.scalar(cluster_query):
                raise MaintenanceRefused('control connection targets a different PostgreSQL cluster')
            _reject_connections(conn)
            identifier = conn.dialect.identifier_preparer.quote_identifier(name)
            control.exec_driver_sql(f'ALTER DATABASE {identifier} ALLOW_CONNECTIONS false')
            try:
                _reject_connections(conn)
                if conn.scalar(text('SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database()')):
                    raise MaintenanceRefused('prepared transactions require operator resolution')
                yield conn
            finally:
                # SIGKILL/power loss needs the documented recovery from the postgres database.
                control.exec_driver_sql(f'ALTER DATABASE {identifier} ALLOW_CONNECTIONS true')
    finally:
        engine.dispose()
        control_engine.dispose()


def _reject_connections(conn):
    if conn.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()')):
        raise MaintenanceRefused('other database connections exist; stop all API/worker/SmartEye processes')


@contextmanager
def _storage_lock(root):
    path = _offline_path(root, '.storage-cleanup.lock')
    with path.open('a+b') as file:
        if os.fstat(file.fileno()).st_nlink != 1:
            raise MaintenanceRefused('shared storage lock file')
        if os.name == 'nt':
            import msvcrt
            if not file.seek(0, 2):
                file.write(b'0'); file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _offline_path(root, relative):
    path = root / relative
    if not path.is_relative_to(root) or '..' in path.parts:
        raise MaintenanceRefused('path leaves storage')
    for part in (path, *path.parents):
        if _linked(part) or part.name.lower() == '.backups':
            raise MaintenanceRefused('linked or backup path')
        if part == root:
            break
    return path


def _offline_references(conn, root):
    from integrations.smarteye.runtime import files, jobs
    from models import SmarteyeTask, TaskStatus, TrainingTask, TrainingStatus
    tables = set(inspect(conn).get_table_names())
    refs = {}
    queries = _reference_queries('smarteye_downloads' in tables, 'validation_sets' in tables, 'training_runs' in tables,'match_queries' in tables)
    queries.append(('smarteye_runtime', select(files.c.blob_path, files.c.content_hash, files.c.relative_path)))
    for name, query in queries:
        refs[name] = set()
        for row in conn.execute(query):
            _remember(refs[name], _strings(list(row)), root)
    active = [select(func.count()).select_from(ImageUpload).where(ImageUpload.status != 'completed'),
        select(func.count()).select_from(jobs).where(jobs.c.status.not_in(('completed', 'failed', 'cancelled', 'interrupted'))),
        select(func.count()).select_from(LibraryImage).where(LibraryImage.scene_status.in_(('pending', 'processing')))]
    if 'training_runs' in tables:
        from models.training_run import TrainingRun
        active.append(select(func.count()).select_from(TrainingRun).where(TrainingRun.status.in_(('starting','running','stopping','unknown'))))
    for model, statuses in ((SmarteyeTask, (TaskStatus.PENDING, TaskStatus.RUNNING)),
                            (TrainingTask, (TrainingStatus.PENDING, TrainingStatus.RUNNING))):
        if model.__tablename__ in tables:
            active.append(select(func.count()).select_from(model).where(model.status.in_(statuses)))
    if any(conn.scalar(query) for query in active):
        raise MaintenanceRefused('unfinished business tasks exist; resolve or cancel them before maintenance')
    return refs


def _verify_original(path, digest, size=None):
    info = path.lstat()
    if _linked(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MaintenanceRefused('linked, shared or non-regular file')
    with path.open('rb') as file:
        actual = hashlib.file_digest(file, 'sha256').hexdigest()
        after = os.fstat(file.fileno())
    current = path.lstat()
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
    if any(identity != (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)
           for item in (after, current)):
        raise MaintenanceRefused('file changed during integrity check')
    if actual != digest or (size is not None and info.st_size != size):
        raise MaintenanceRefused('file hash or size mismatch')
    return info.st_size


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _move_original(root, source, target, entry):
    _offline_path(root, source.relative_to(root))
    _offline_path(root, target.relative_to(root))
    if target.exists():
        raise MaintenanceRefused('destination exists; refusing to overwrite')
    _verify_original(source, entry['sha256'], entry['size'])
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != target.parent.stat().st_dev:
        raise MaintenanceRefused('quarantine must be on the same filesystem')
    os.rename(source, target)
    _sync_directory(source.parent)
    for parent in (target.parent, *target.parent.parents):
        _sync_directory(parent)
        if parent == root:
            break


def offline_cleanup(action, database_url, storage_root, confirm_writers_stopped, run_id=None):
    """Offline only: operators stop every writer; database fencing is an additional check."""
    if not confirm_writers_stopped:
        raise ValueError('--confirm-writers-stopped is required after stopping all writers')
    if action not in ('archive', 'restore') or (action == 'restore') != bool(run_id):
        raise ValueError('archive takes no run-id; restore requires run-id')
    if not Path(storage_root).is_absolute():
        raise MaintenanceRefused('an explicit absolute storage directory is required')
    try:
        root = _checked_root(storage_root)
    except HTTPException as error:
        raise MaintenanceRefused(error.detail['code']) from None
    with _storage_lock(root), _offline_database(database_url) as conn:
        refs = _offline_references(conn, root)
        identity = {'database': conn.scalar(text('SELECT current_database()')),
            'database_oid': conn.scalar(text('SELECT oid FROM pg_database WHERE datname=current_database()')),
            'cluster': str(conn.scalar(text('SELECT system_identifier FROM pg_control_system()')))}
        if action == 'archive':
            entries = []
            now = time.time()
            for key in _keys(root):
                if not _record(root, key, refs, 0, 0, now)['reasons']:
                    digest = KEY.fullmatch(key)[2]
                    size = _verify_original(_offline_path(root, key), digest)
                    entries.append({'path': key, 'sha256': digest, 'size': size})
            run_id = uuid4().hex
            run_dir = _offline_path(root, f'.quarantine/{run_id}')
            run_dir.mkdir(parents=True, exist_ok=False)
            manifest = {'version': 1, 'run_id': run_id, 'storage_root': str(root),
                'database': identity, 'created_at': time.time(), 'files': entries}
            with (run_dir / 'manifest.json').open('x', encoding='utf-8') as file:
                json.dump(manifest, file, ensure_ascii=False, indent=2)
                file.flush(); os.fsync(file.fileno())
            _sync_directory(run_dir); _sync_directory(run_dir.parent); _sync_directory(root)
        else:
            if UUID(run_id).hex != run_id:
                raise MaintenanceRefused('invalid run-id')
            run_dir = _offline_path(root, f'.quarantine/{run_id}')
            manifest_path = _offline_path(root, f'.quarantine/{run_id}/manifest.json')
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if (not isinstance(manifest, dict) or manifest.get('version') != 1 or manifest.get('run_id') != run_id
                    or manifest.get('storage_root') != str(root) or manifest.get('database') != identity):
                raise MaintenanceRefused('manifest target mismatch')
            entries = manifest.get('files')
            if not isinstance(entries, list):
                raise MaintenanceRefused('invalid manifest files')
            seen = set()
            for entry in entries:
                if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                        or not valid_key(entry['path']) or entry['path'] in seen
                        or entry.get('sha256') != KEY.fullmatch(entry['path'])[2]
                        or type(entry.get('size')) is not int or entry['size'] < 0):
                    raise MaintenanceRefused('invalid manifest entry')
                seen.add(entry['path'])
        moved = 0
        for entry in entries:
            original = _offline_path(root, entry['path'])
            archived = _offline_path(root, f'.quarantine/{run_id}/{entry["path"]}')
            if action == 'archive':
                if _record(root, entry['path'], refs, 0, 0, time.time())['reasons']:
                    raise MaintenanceRefused('original no longer eligible; restore this run before retrying')
                _move_original(root, original, archived, entry)
            elif archived.exists():
                _move_original(root, archived, original, entry)
            else:
                # A planned rename may not have happened, or this entry was already restored.
                _verify_original(original, entry['sha256'], entry['size'])
                continue
            moved += 1
        return {'run_id': run_id, 'action': action, 'moved_count': moved,
            'manifest': str(run_dir / 'manifest.json')}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Offline originals quarantine/restore; stop every writer first.')
    parser.add_argument('action', choices=('archive', 'restore'))
    parser.add_argument('--database-url', required=True, help='Explicit PostgreSQL URL; never inferred from .env')
    parser.add_argument('--storage', required=True, help='Explicit absolute local storage directory')
    parser.add_argument('--confirm-writers-stopped', action='store_true', required=True)
    parser.add_argument('--run-id', help='Archive run-id to restore')
    args = parser.parse_args(argv)
    try:
        result = offline_cleanup(args.action, args.database_url, args.storage, args.confirm_writers_stopped, args.run_id)
    except (ValueError, OSError, SQLAlchemyError, HTTPException) as error:
        # Driver exceptions can contain connection URLs; never echo credentials.
        message = str(error) if isinstance(error, MaintenanceRefused) else type(error).__name__
        parser.exit(2, f'Maintenance refused or interrupted: {message}. Keep writers stopped; inspect the manifest before retrying.\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
