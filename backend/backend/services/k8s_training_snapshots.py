"""Reuse the platform's frozen-data verifier on the K8s control host."""
import inspect
from pathlib import Path

from services import training_dataset, training_executor


def remote_snapshot_ops(payload, run_dir, cache_dir):
    global SNAPSHOT_CACHE_ROOT
    path = Path(run_dir)
    SNAPSHOT_CACHE_ROOT = Path(cache_dir)
    if (path / '.canceled').exists(): raise ValueError('任务已取消')
    if payload['action'] == 'snapshot_cache':
        result = cached_snapshot(payload, path)
        staged = path / 'input.zip'
        if not result['hit'] and (staged.exists() or staged.is_symlink()):
            if staged.is_symlink() or not staged.is_file() or file_hash(staged) != payload['snapshot_hash']:
                raise ValueError('已有暂存包不匹配，禁止覆盖')
            result = cached_snapshot(payload, path, publish=True)
        result['upload_path'] = str(path / 'input.zip')
        return result
    if payload['action'] != 'unpack': raise ValueError('不支持的冻结数据操作')
    marker = path / '.dataset-ready'
    if marker.is_symlink(): raise ValueError('数据标记不能是符号链接')
    if marker.exists():
        if marker.read_text() != payload['snapshot_hash']: raise ValueError('冻结数据版本不匹配')
        return {'ok': True}
    dataset = path / 'dataset'; prepared = path / 'prepared-dataset'
    if dataset.exists() or dataset.is_symlink() or prepared.exists() or prepared.is_symlink():
        raise ValueError('存在未完成的数据准备目录，禁止覆盖')
    archive = Path(cached_snapshot(payload, path, publish=True)['path'])
    unpack_snapshot(archive, dataset, payload['config']['kind'])
    kind = payload['config']['kind']
    if kind not in ('swift_sft', 'wemm'):
        source = dataset / ('data.yaml' if kind == 'yolo26' else 'rfdetr')
        prepare_detection(str(source), str(prepared), kind, payload['config'].get('selected_labels'))
        for directory, dirs, names in os.walk(str(prepared), followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in dirs): raise ValueError('准备目录不能包含目录链接')
            for name in names:
                target = Path(directory) / name
                if not target.is_symlink(): continue
                source_path = target.resolve()
                if not source_path.is_relative_to(dataset.resolve()) or not source_path.is_file():
                    raise ValueError('准备数据链接越界')
                target.unlink(); os.link(source_path, target)
        yaml_path = prepared / 'data.yaml'
        if yaml_path.is_file():
            data = json.loads(yaml_path.read_text(encoding='utf-8'))
            data['path'] = '/output/prepared-dataset'
            yaml_path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    if (path / '.canceled').exists(): raise ValueError('任务已取消')
    temporary = path / '.dataset-ready.tmp'
    if temporary.exists() or temporary.is_symlink(): raise ValueError('存在未完成的数据标记')
    temporary.write_text(payload['snapshot_hash'])
    os.replace(temporary, marker)
    return {'ok': True}


def support_code():
    imports = 'import contextlib, hashlib, json, math, os, shutil, stat, tempfile, zipfile\nfrom pathlib import Path, PurePosixPath\n'
    reused = '\n'.join(inspect.getsource(getattr(training_executor, name)) for name in
                       ('cached_snapshot', 'file_hash', 'safe_child', 'unpack_snapshot', 'run_lock'))
    return imports + inspect.getsource(training_dataset) + '\n' + reused + '\n' + inspect.getsource(remote_snapshot_ops)


def snapshot_ops(payload, run_dir, cache_dir):
    namespace = {}
    exec(support_code(), namespace)
    return namespace['remote_snapshot_ops'](payload, run_dir, cache_dir)
