"""SHA256 cache uses isolated packages and no real SSH or Docker."""
import contextlib
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import zipfile

import pytest

from services import training_executor as executor


@pytest.fixture
def package(tmp_path,monkeypatch):
    monkeypatch.setattr(executor,'SNAPSHOT_CACHE_ROOT',tmp_path/'cache',raising=False)
    monkeypatch.setattr(executor,'run_lock',lambda path:contextlib.nullcontext())
    archive=tmp_path/'fixture.zip';assets=[]
    with zipfile.ZipFile(archive,'w') as data:
        for split in ('train','val'):
            raw=(split+' image').encode();name=f'images/{split}/a.jpg'
            assets.append(dict(split=split,image_path=name,content_hash=hashlib.sha256(raw).hexdigest()))
            data.writestr(name,raw);data.writestr(f'labels/{split}/a.txt','0 0.5 0.5 0.2 0.2')
        data.writestr('manifest.json',json.dumps(dict(format='yolo',labels=['ele'],assets=assets)))
    return archive,executor.file_hash(archive)


def payload(tmp_path,sha,root='runs'):
    return dict(run_id=str(uuid4()),snapshot_hash=sha,config=dict(output_root=str(tmp_path/root),kind='rfdetr_medium'))


def test_second_task_reuses_cache_across_output_roots_with_independent_datasets(package,tmp_path):
    archive,sha=package;first=payload(tmp_path,sha);second=payload(tmp_path,sha,'other-runs')
    assert not executor.execute(dict(first,action='snapshot_cache'))['hit']
    directory=Path(first['config']['output_root'])/first['run_id']
    (directory/'input.zip').write_bytes(archive.read_bytes())
    executor.execute(dict(first,action='unpack'))
    cached=executor.SNAPSHOT_CACHE_ROOT/sha/'input.zip'
    assert cached.read_bytes()==archive.read_bytes() and not (directory/'input.zip').exists()
    assert executor.execute(dict(second,action='snapshot_cache'))['hit']
    executor.execute(dict(second,action='unpack'))
    other=Path(second['config']['output_root'])/second['run_id']
    assert (other/'dataset/rfdetr/valid/images/a.jpg').read_bytes()==b'val image'
    (other/'dataset/rfdetr/valid/images/a.jpg').write_bytes(b'changed by training')
    assert (directory/'dataset/rfdetr/valid/images/a.jpg').read_bytes()==b'val image'
    assert executor.file_hash(cached)==sha


def test_existing_task_archive_can_seed_cache_without_modifying_source(package,tmp_path):
    archive,sha=package;task=payload(tmp_path,sha)
    result=executor.execute(dict(task,action='snapshot_cache',legacy_archives=[str(tmp_path/'missing.zip'),str(archive)]))
    assert result['hit'] and result['imported']
    assert executor.file_hash(archive)==sha
    assert executor.file_hash(executor.SNAPSHOT_CACHE_ROOT/sha/'input.zip')==sha


def test_corrupt_cache_and_failed_upload_are_never_used(package,tmp_path):
    archive,sha=package;task=payload(tmp_path,sha)
    entry=executor.SNAPSHOT_CACHE_ROOT/sha;entry.mkdir(parents=True)
    (entry/'input.zip').write_bytes(b'corrupt')
    assert not executor.execute(dict(task,action='snapshot_cache'))['hit']
    directory=Path(task['config']['output_root'])/task['run_id']
    (directory/'input.zip').write_bytes(b'partial upload')
    with pytest.raises(ValueError,match='SHA256'):executor.execute(dict(task,action='unpack'))
    assert not (directory/'dataset').exists() and not (directory/'.dataset-ready').exists()
    (directory/'input.zip').write_bytes(archive.read_bytes())
    executor.execute(dict(task,action='unpack'))
    assert executor.file_hash(entry/'input.zip')==sha


@pytest.mark.parametrize('sha',['../escape','a'*63,'G'*64,'a'*65])
def test_cache_rejects_invalid_sha_before_constructing_path(package,tmp_path,sha):
    with pytest.raises(ValueError,match='SHA256'):
        executor.execute(dict(payload(tmp_path,sha),action='snapshot_cache'))


def test_canceled_task_cannot_publish_or_unpack_cache(package,tmp_path):
    archive,sha=package;task=payload(tmp_path,sha)
    directory,_=executor.run_directory(task);(directory/'.canceled').touch()
    (directory/'input.zip').write_bytes(archive.read_bytes())
    with pytest.raises(ValueError,match='取消'):executor.execute(dict(task,action='unpack'))
    assert not (directory/'dataset').exists()


def test_legacy_bad_hash_and_partial_cache_are_misses(package,tmp_path):
    archive,sha=package;task=payload(tmp_path,sha)
    entry=executor.SNAPSHOT_CACHE_ROOT/sha;entry.mkdir(parents=True)
    (entry/'.publish-incomplete').mkdir()
    wrong=tmp_path/'wrong.zip';wrong.write_bytes(b'wrong data')
    assert not executor.execute(dict(task,action='snapshot_cache',legacy_archives=[str(wrong)]))['hit']
    assert not (entry/'input.zip').exists()
    assert wrong.read_bytes()==b'wrong data'


def test_cache_symlinks_are_rejected(package,tmp_path,monkeypatch):
    archive,sha=package;task=payload(tmp_path,sha)
    original=Path.is_symlink;entry=executor.SNAPSHOT_CACHE_ROOT/sha
    monkeypatch.setattr(Path,'is_symlink',lambda path:path==entry or original(path))
    with pytest.raises(ValueError,match='链接'):executor.execute(dict(task,action='snapshot_cache'))


def test_different_packages_never_hit_each_others_cache(package,tmp_path):
    archive,sha=package;first=payload(tmp_path,sha)
    executor.execute(dict(first,action='snapshot_cache',legacy_archives=[str(archive)]))
    different=hashlib.sha256(b'different snapshot').hexdigest()
    assert not executor.execute(dict(payload(tmp_path,different),action='snapshot_cache'))['hit']
    assert executor.file_hash(executor.SNAPSHOT_CACHE_ROOT/sha/'input.zip')==sha


@pytest.mark.asyncio
async def test_parallel_tasks_on_same_server_upload_only_once(tmp_path,monkeypatch):
    import asyncio
    from io import BytesIO
    from types import SimpleNamespace
    from services import training_runs
    raw=b'isolated frozen package';sha=hashlib.sha256(raw).hexdigest();uploads=[];cached=False
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    class Storage:
        async def get_file(self,path):return BytesIO(raw)
    monkeypatch.setattr(training_runs,'get_storage',lambda:Storage())
    async def ensure(db,run):
        if run.status!='starting':raise ValueError('canceled')
    async def progress(db,run,phase,message,**details):run.inspection['startup'].update(details)
    async def remote(db,run,action,**values):
        nonlocal cached
        await asyncio.sleep(.01)
        if action=='snapshot_cache':return dict(hit=cached)
        if action=='unpack':cached=True;return dict(ok=True)
        raise AssertionError(action)
    async def connection(db,run):return None
    def upload(server,value):uploads.append(value['upload']['remotePath'])
    monkeypatch.setattr(training_runs,'ensure_starting',ensure)
    monkeypatch.setattr(training_runs,'startup_progress',progress)
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'connection',connection)
    monkeypatch.setattr(training_runs,'bridge',upload)
    class DB:
        async def scalars(self,query):return SimpleNamespace(all=lambda:[])
    runs=[SimpleNamespace(id=uuid4(),tenant_id=1,status='starting',server=dict(id='same',name='fixture'),
        source=dict(kind='snapshot',sha256=sha,blob_path='fixture'),output_dir=f'/output/{index}',
        inspection=dict(startup=dict(steps=['archive','upload','unpack']))) for index in range(2)]
    await asyncio.gather(*(training_runs.transfer_snapshot(DB(),run) for run in runs))
    assert len(uploads)==1 and runs[1].inspection['startup']['cache_hit'] is True
    assert runs[0].output_dir!=runs[1].output_dir
