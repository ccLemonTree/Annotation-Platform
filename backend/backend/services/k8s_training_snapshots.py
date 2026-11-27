"""Reuse the platform's frozen-data verifier on the K8s control host."""
import inspect
from pathlib import Path

from services import k8s_training_executor, training_dataset, training_executor


def register_legacy_snapshot(payload, entry, root):
    sha = payload['snapshot_hash']
    config = payload['config']
    pointer = entry / '.verified-run'
    lock = entry / '.unpack'
    if lock.is_symlink() or (lock / '.lock').is_symlink() or pointer.is_symlink():
        raise ValueError('共享冻结数据登记路径不能是链接')
    lock.mkdir(parents=True, exist_ok=True, mode=0o700)
    with run_lock(lock):
        if Path(root, snapshot_mount_paths(root, config, sha)['dataset']).is_dir():
            return
        for candidate in payload.get('legacy_runs', []):
            try:
                if not candidate.startswith('chen/training/'): continue
                uuid.UUID(candidate.rsplit('/', 1)[-1])
                directory = Path(safe_path(root, candidate, True))
                source = verified_snapshot_config(directory, sha, config['snapshot_id'])
                if source.get('execution_backend') != 'k8s': continue
                if ('prepared-dataset' in shared_snapshot_paths(source, sha)
                        and not Path(safe_path(root, candidate + '/prepared-dataset')).is_dir()):
                    continue
            except (OSError, RuntimeError, ValueError, KeyError):
                continue
            with tempfile.TemporaryDirectory(prefix='.register-', dir=entry) as temp:
                record = Path(temp) / pointer.name
                record.write_text(candidate, encoding='utf-8')
                os.replace(record, pointer)
            return


def remote_snapshot_ops(payload, run_dir, cache_dir):
    global SNAPSHOT_CACHE_ROOT
    path = Path(run_dir)
    SNAPSHOT_CACHE_ROOT = Path(cache_dir)
    if (path / '.canceled').exists(): raise ValueError('任务已取消')
    sha = payload['snapshot_hash']
    config = payload['config']
    kind = config['kind']
    paths = shared_snapshot_paths(config, sha)
    entry = SNAPSHOT_CACHE_ROOT / sha
    if SNAPSHOT_CACHE_ROOT.is_symlink() or entry.is_symlink():
        raise ValueError('冻结包缓存目录不能是链接')
    folders = {name: entry / Path(relative).name for name, relative in paths.items()}
    if root := payload.get('mounted_root'):
        if payload['action'] == 'snapshot_directory' and payload.get('legacy_runs'):
            register_legacy_snapshot(payload, entry, root)
        folders = {name: Path(root) / relative for name, relative in snapshot_mount_paths(root, config, sha).items()}
    dataset = folders['dataset']
    if any(folder.is_symlink() for folder in folders.values()):
        raise ValueError('共享数据目录不能是链接')
    if payload['action'] in ('snapshot_directory', 'snapshot_cache'):
        if dataset.is_dir():
            return {'hit': True, 'shared_ready': all(folder.is_dir() for folder in folders.values())}
        if payload['action'] == 'snapshot_directory':
            return {'hit': False}
        result = cached_snapshot(payload, path)
        staged = path / 'input.zip'
        if not result['hit'] and (staged.exists() or staged.is_symlink()):
            if staged.is_symlink() or not staged.is_file() or file_hash(staged) != payload['snapshot_hash']:
                raise ValueError('已有暂存包不匹配，禁止覆盖')
            result = cached_snapshot(payload, path, publish=True)
        result['upload_path'] = str(path / 'input.zip')
        return result
    if payload['action'] != 'unpack': raise ValueError('不支持的冻结数据操作')
    marker = path / '.shared-dataset-ready'
    if marker.is_symlink(): raise ValueError('数据标记不能是符号链接')
    if marker.exists():
        if marker.read_text() != sha or not all(folder.is_dir() for folder in folders.values()):
            raise ValueError('共享冻结数据缺失或版本不匹配')
        return {'ok': True}
    if any(os.path.lexists(path / name) for name in ('dataset', 'prepared-dataset', '.dataset-ready')):
        raise ValueError('存在未完成的数据准备目录，禁止覆盖')
    lock = entry / '.unpack'
    if lock.is_symlink() or (lock / '.lock').is_symlink():
        raise ValueError('共享数据锁不能是链接')
    lock.mkdir(parents=True, exist_ok=True, mode=0o700)
    with run_lock(lock):
        if (path / '.canceled').exists(): raise ValueError('任务已取消')
        if root:
            folders = {name: Path(root) / relative for name, relative in snapshot_mount_paths(root, config, sha).items()}
            dataset = folders['dataset']
        if not dataset.is_dir():
            archive = Path(cached_snapshot(payload, path, publish=True)['path'])
            with tempfile.TemporaryDirectory(prefix='.extract-', dir=entry) as temp:
                extracted = Path(temp) / 'dataset'
                unpack_snapshot(archive, extracted, None)
                os.rename(extracted, dataset)
        prepared = folders.get('prepared-dataset')
        manifest = json.loads((dataset / 'manifest.json').read_text(encoding='utf-8'))
        if ('training' in manifest) != (prepared is None):
            raise ValueError('冻结数据与训练器类型不匹配')
        if prepared is not None and not prepared.is_dir():
            source = dataset / ('data.yaml' if manifest['format'] == 'yolo' and (dataset / 'data.yaml').is_file() else 'rfdetr')
            with tempfile.TemporaryDirectory(prefix='.labels-', dir=entry) as temp:
                target = Path(temp) / 'prepared'
                prepare_detection(str(source), str(target), kind, config.get('selected_labels'), link_image=os.link)
                yaml_path = target / 'data.yaml'
                if yaml_path.is_file():
                    data = json.loads(yaml_path.read_text(encoding='utf-8'))
                    data['path'] = '/output/prepared-dataset'
                    yaml_path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                os.rename(target, prepared)
    if (path / '.canceled').exists(): raise ValueError('任务已取消')
    temporary = marker.with_suffix('.tmp')
    with temporary.open('x') as stream:
        stream.write(sha)
    os.replace(temporary, marker)
    return {'ok': True}


def support_code():
    imports = 'import contextlib, hashlib, json, math, os, re, shutil, stat, tempfile, zipfile\nfrom pathlib import Path, PurePosixPath\n'
    limits = '\n'.join(f'{name} = {getattr(training_executor, name)}' for name in
                       ('MAX_SNAPSHOT_GIB', 'MAX_SNAPSHOT_BYTES', 'MAX_SNAPSHOT_MEMBERS'))
    reused = '\n'.join(inspect.getsource(getattr(training_executor, name)) for name in
                       ('snapshot_entry', 'verified_snapshot_config', 'cached_snapshot', 'file_hash',
                        'safe_child', 'unpack_snapshot', 'run_lock'))
    paths = '\n'.join(inspect.getsource(fn) for fn in
                      (k8s_training_executor.config_hash, k8s_training_executor.safe_path,
                       k8s_training_executor.shared_snapshot_paths, k8s_training_executor.snapshot_mount_paths))
    imports += 'import uuid\n'
    return '\n'.join((imports, limits, inspect.getsource(training_dataset), reused, paths,
                      inspect.getsource(register_legacy_snapshot), inspect.getsource(remote_snapshot_ops)))


def snapshot_ops(payload, run_dir, cache_dir):
    namespace = {}
    exec(support_code(), namespace)
    return namespace['remote_snapshot_ops'](payload, run_dir, cache_dir)
