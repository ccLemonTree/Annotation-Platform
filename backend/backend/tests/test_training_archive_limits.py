import hashlib
import json
from types import SimpleNamespace
import zipfile

import pytest

from services import k8s_training_snapshots, training_executor


@pytest.mark.parametrize('count,accepted', [(100000, True), (100001, False)])
def test_archive_entry_limit_before_extraction(monkeypatch, tmp_path, count, accepted):
    row = SimpleNamespace(file_size=0, filename='image.jpg', external_attr=0)
    class Archive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def infolist(self): return [row] * count
    monkeypatch.setattr(training_executor.zipfile, 'ZipFile', lambda *args: Archive())
    def checked_path(*args): raise RuntimeError('entry limit passed')
    monkeypatch.setattr(training_executor, 'safe_child', checked_path)
    with pytest.raises(RuntimeError if accepted else ValueError, match='entry limit passed' if accepted else '100000'):
        training_executor.unpack_snapshot(tmp_path / 'source.zip', tmp_path / 'dataset', 'yolo26')


@pytest.mark.parametrize('count,accepted', [(100000, True), (100001, False)])
def test_snapshot_support_uses_updated_entry_limit(monkeypatch, tmp_path, count, accepted):
    namespace = {}
    exec(k8s_training_snapshots.support_code(), namespace)
    row = SimpleNamespace(file_size=0, filename='image.jpg', external_attr=0)
    class Archive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def infolist(self): return [row] * count
    monkeypatch.setattr(namespace['zipfile'], 'ZipFile', lambda *args: Archive())
    def checked_path(*args): raise RuntimeError('entry limit passed')
    namespace['safe_child'] = checked_path
    with pytest.raises(RuntimeError if accepted else ValueError, match='entry limit passed' if accepted else '100000'):
        namespace['unpack_snapshot'](tmp_path / 'source.zip', tmp_path / 'dataset', 'yolo26')


def test_expanded_size_limit_remains_40_gib(monkeypatch, tmp_path):
    row = SimpleNamespace(file_size=40 * 1024 ** 3 + 1)
    class Archive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def infolist(self): return [row]
    monkeypatch.setattr(training_executor.zipfile, 'ZipFile', lambda *args: Archive())
    with pytest.raises(ValueError, match='40GiB'):
        training_executor.unpack_snapshot(tmp_path / 'source.zip', tmp_path / 'dataset', 'yolo26')


def test_traversal_and_bad_image_hash_remain_rejected(tmp_path):
    archive = tmp_path / 'input.zip'
    with zipfile.ZipFile(archive, 'w') as data:
        data.writestr('../escape', 'bad')
    with pytest.raises(ValueError):
        training_executor.unpack_snapshot(archive, tmp_path / 'first', 'yolo26')
    raw = b'image'
    with zipfile.ZipFile(archive, 'w') as data:
        data.writestr('images/train/a.jpg', raw)
        data.writestr('manifest.json', json.dumps(dict(assets=[dict(split='train', image_path='images/train/a.jpg', content_hash=hashlib.sha256(b'other').hexdigest())])))
    with pytest.raises(ValueError, match='哈希'):
        training_executor.unpack_snapshot(archive, tmp_path / 'second', 'yolo26')
