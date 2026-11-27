import hashlib
import json
import os
import zipfile
import sys
import types
from pathlib import Path

import pytest

from services import k8s_training_snapshots as snapshots


@pytest.fixture(autouse=True)
def fake_windows_fcntl(monkeypatch):
    if sys.platform == 'win32':
        monkeypatch.setitem(sys.modules, 'fcntl', types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=1))
        monkeypatch.setattr(Path, 'symlink_to', lambda self, target: os.link(target, self))


@pytest.fixture
def package(tmp_path):
    archive = tmp_path / 'input.zip'
    assets = []
    with zipfile.ZipFile(archive, 'w') as data:
        for split in ('train', 'val'):
            raw = (split + ' image').encode()
            name = 'images/' + split + '/a.jpg'
            assets.append(dict(split=split, image_path=name, content_hash=hashlib.sha256(raw).hexdigest()))
            data.writestr(name, raw)
            data.writestr('labels/' + split + '/a.txt', '0 0.5 0.5 0.2 0.2')
        data.writestr('manifest.json', json.dumps(dict(format='yolo', labels=['ele', 'other'], assets=assets)))
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_cache_and_prepare_snapshot_once(package, tmp_path):
    archive, sha = package
    task = tmp_path / 'run'; task.mkdir()
    cache = tmp_path / 'cache'
    payload = dict(action='snapshot_cache', snapshot_hash=sha, config=dict(kind='yolo26', selected_labels=['ele']))
    assert not snapshots.snapshot_ops(payload, str(task), str(cache))['hit']
    (task / 'input.zip').write_bytes(archive.read_bytes())
    payload['action'] = 'unpack'
    assert snapshots.snapshot_ops(payload, str(task), str(cache))['ok']
    assert (task / '.shared-dataset-ready').read_text() == sha
    prepared, = (cache / sha).glob('prepared-*')
    yaml = json.loads((prepared / 'data.yaml').read_text())
    assert yaml['path'] == '/output/prepared-dataset' and yaml['names'] == ['ele']
    image = next((prepared / 'train/images').iterdir())
    assert not image.is_symlink() and image.read_bytes() == b'train image'
    assert snapshots.snapshot_ops(payload, str(task), str(cache))['ok']
    payload['action'] = 'snapshot_cache'
    assert snapshots.snapshot_ops(payload, str(task), str(cache))['hit']


def test_second_task_reuses_extracted_files_across_detection_trainers(package, tmp_path):
    archive, sha = package
    cache = tmp_path / 'cache'
    first = tmp_path / 'first'; first.mkdir()
    second = tmp_path / 'second'; second.mkdir()
    (first / 'input.zip').write_bytes(archive.read_bytes())
    payload = dict(action='unpack', snapshot_hash=sha, config=dict(kind='yolo26', selected_labels=['ele']))
    assert snapshots.snapshot_ops(payload, first, cache)['ok']
    (cache / sha / 'input.zip').unlink()
    payload['config']['kind'] = 'rfdetr_2xlarge'
    assert snapshots.snapshot_ops(dict(payload, action='snapshot_cache'), second, cache)['hit']
    assert snapshots.snapshot_ops(payload, second, cache)['ok']
    for run in (first, second):
        assert (run / '.shared-dataset-ready').read_text() == sha
        assert not (run / 'dataset').exists()
    source = cache / sha / 'dataset/images/train/a.jpg'
    prepared = list((cache / sha).glob('prepared-*/train/images/*'))
    assert len(prepared) == 2 and all(os.path.samefile(image, source) for image in prepared)


def test_verified_legacy_dataset_is_reused_without_archive(package, tmp_path):
    _, sha = package
    training = tmp_path / 'chen/training'
    old = training / 'runs/9a6f50a6-7bf2-4354-bbd2-5468a08eb1ce'
    (old / 'dataset').mkdir(parents=True)
    (old / 'dataset/manifest.json').write_text('{"format":"yolo"}')
    (old / 'prepared-dataset').mkdir()
    (old / '.dataset-ready').write_text(sha)
    config = dict(kind='rfdetr_2xlarge', snapshot_id='version', selected_labels=['ele'])
    (old / 'run_config.json').write_text(json.dumps(config))
    cache = training / 'dataset-cache'
    (cache / sha).mkdir(parents=True)
    (cache / sha / '.verified-run').write_text(old.name)
    task = tmp_path / 'current'; task.mkdir()
    payload = dict(action='snapshot_cache', snapshot_hash=sha, config=config, mounted_root=str(tmp_path))
    assert snapshots.snapshot_ops(payload, task, cache)['shared_ready']
    assert snapshots.snapshot_ops(dict(payload, action='unpack'), task, cache)['ok']
    assert (task / '.shared-dataset-ready').read_text() == sha
    assert sorted(p.name for p in task.iterdir()) == ['.shared-dataset-ready']
    (old / '.dataset-ready').write_text('0' * 64)
    with pytest.raises(ValueError, match='摘要不匹配'):
        snapshots.snapshot_ops(payload, task, cache)


def test_discovers_verified_history_even_in_another_output_root(package, tmp_path):
    archive, sha = package
    training = tmp_path / 'chen/training'
    cache = training / 'dataset-cache'
    config = dict(kind='yolo26', snapshot_id='version', selected_labels=['ele'], execution_backend='k8s')
    runs = []
    for uid in ('9a6f50a6-7bf2-4354-bbd2-5468a08eb1ce', '08a17603-1221-4633-845d-8244bacc3cff'):
        run = training / 'another-root' / uid
        (run / 'prepared-dataset').mkdir(parents=True)
        (run / 'run_config.json').write_text(json.dumps(dict(config, output_root='/adb-ssd-mount/ai/chen/training/another-root')))
        runs.append(run)
    from services.training_executor import unpack_snapshot
    unpack_snapshot(archive, runs[1] / 'dataset', 'yolo26')
    (runs[1] / '.dataset-ready').write_text(sha)
    task = tmp_path / 'next'; task.mkdir()
    payload = dict(action='snapshot_directory', snapshot_hash=sha, config=config, mounted_root=str(tmp_path),
                   legacy_runs=['chen/training/another-root/'+run.name for run in runs])
    assert snapshots.snapshot_ops(payload, task, cache)['shared_ready']
    assert snapshots.snapshot_ops(dict(payload, action='unpack'), task, cache)['ok']
    assert snapshots.snapshot_ops(dict(payload, action='snapshot_directory', legacy_runs=[]), task, cache)['hit']
    assert not (cache / sha / 'input.zip').exists()


def test_cancel_and_bad_archive_never_publish_ready(package, tmp_path):
    archive, sha = package
    task = tmp_path / 'run'; task.mkdir()
    payload = dict(action='unpack', snapshot_hash=sha, config=dict(kind='yolo26', selected_labels=None))
    (task / '.canceled').touch()
    with pytest.raises(ValueError, match='取消'): snapshots.snapshot_ops(payload, str(task), str(tmp_path / 'cache'))
    (task / '.canceled').unlink()
    (task / 'input.zip').write_bytes(b'bad')
    with pytest.raises(ValueError): snapshots.snapshot_ops(payload, str(task), str(tmp_path / 'cache'))
    assert not (task / '.shared-dataset-ready').exists()


@pytest.mark.parametrize('mode', ['path', 'image_hash', 'selected_labels'])
def test_bad_snapshot_or_filter_never_marks_ready(mode, tmp_path):
    task = tmp_path / 'run'; task.mkdir()
    archive = task / 'input.zip'
    assets = []
    with zipfile.ZipFile(archive, 'w') as data:
        if mode == 'path': data.writestr('../escape', 'bad')
        for split in ('train', 'val'):
            raw = split.encode(); name = 'images/' + split + '/a.jpg'
            assets.append(dict(split=split, image_path=name, content_hash='0' * 64 if mode == 'image_hash' else hashlib.sha256(raw).hexdigest()))
            data.writestr(name, raw)
            data.writestr('labels/' + split + '/a.txt', '0 0.5 0.5 0.2 0.2')
        data.writestr('manifest.json', json.dumps(dict(format='yolo', labels=['ele', 'other'], assets=assets)))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    payload = dict(action='unpack', snapshot_hash=sha, config=dict(kind='yolo26', selected_labels=['other'] if mode == 'selected_labels' else None))
    with pytest.raises(ValueError): snapshots.snapshot_ops(payload, str(task), str(tmp_path / 'cache'))
    assert not (task / '.shared-dataset-ready').exists()


def test_support_code_is_bounded_and_standalone():
    source = snapshots.support_code()
    assert len(source.encode('utf-8')) < 40000
    namespace = {}
    exec(source, namespace)
    assert callable(namespace['remote_snapshot_ops'])


def test_wemm_snapshot_rewrites_images_and_uses_no_detection_preparation(tmp_path):
    task = tmp_path / 'run'; task.mkdir()
    archive = task / 'input.zip'; image = b'image'
    manifest = dict(training=dict(samples=1, format='infonce'), assets=[dict(image='images/a.jpg', content_hash=hashlib.sha256(image).hexdigest())])
    with zipfile.ZipFile(archive, 'w') as data:
        data.writestr('manifest.json', json.dumps(manifest))
        data.writestr('images/a.jpg', image)
        data.writestr('train.jsonl', json.dumps(dict(images=['images/a.jpg'])) + '\n')
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    snapshots.snapshot_ops(dict(action='unpack', snapshot_hash=sha, config=dict(kind='wemm')), str(task), str(tmp_path / 'cache'))
    assert json.loads((tmp_path / 'cache' / sha / 'dataset/train.jsonl').read_text())['images'] == ['/output/dataset/images/a.jpg']
    assert not list((tmp_path / 'cache' / sha).glob('prepared-*'))


def test_rfdetr_coco_selects_and_remaps_categories_with_hardlinked_images(tmp_path):
    task = tmp_path / 'run'; task.mkdir()
    archive = task / 'input.zip'; assets = []
    with zipfile.ZipFile(archive, 'w') as data:
        for split in ('train', 'val'):
            raw = (split + ' image').encode(); image_path = 'images/' + split + '/a.jpg'
            assets.append(dict(split=split, image_path=image_path, content_hash=hashlib.sha256(raw).hexdigest()))
            data.writestr(image_path, raw)
            document = dict(images=[dict(id=1, file_name=image_path)], categories=[dict(id=3, name='person'), dict(id=17, name='smoke')], annotations=[dict(id=1, image_id=1, category_id=3), dict(id=2, image_id=1, category_id=17)])
            data.writestr('annotations/instances_' + split + '.json', json.dumps(document))
        data.writestr('manifest.json', json.dumps(dict(format='coco', labels=['person', 'smoke'], assets=assets)))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    payload = dict(action='unpack', snapshot_hash=sha, config=dict(kind='rfdetr_medium', selected_labels=['smoke']))
    assert snapshots.snapshot_ops(payload, str(task), str(tmp_path / 'cache'))['ok']
    assert (task / '.shared-dataset-ready').read_text() == sha
    entry = tmp_path / 'cache' / sha
    prepared, = entry.glob('prepared-*')
    for split in ('train', 'valid'):
        folder = prepared / split
        document = json.loads((folder / '_annotations.coco.json').read_text())
        assert document['categories'] == [dict(id=0, name='smoke')]
        assert [annotation['category_id'] for annotation in document['annotations']] == [0]
        image_name = document['images'][0]['file_name']
        assert '/' not in image_name and '\\' not in image_name
        image = folder / image_name
        assert not image.is_symlink() and os.path.samefile(image, entry / 'dataset/rfdetr' / split / 'a.jpg')
