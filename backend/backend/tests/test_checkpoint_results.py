"""Batch transfer must replace per-image SSH while preserving attempt boundaries."""
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
import zipfile

import pytest
from fastapi import HTTPException

from services import checkpoint_validation as cp, checkpoint_executor as executor, validation, training_runs


@pytest.fixture
def batch(tmp_path, monkeypatch):
    ids = [str(uuid4()), str(uuid4())]
    run = SimpleNamespace(id=uuid4(), tenant_id=1, status='running', model_snapshot=dict(
        execution=dict(id=str(uuid4()), active=True, prepared=True), checkpoint=dict(
            server=dict(id='default', name='fixture'), weight_sha256='a'*64)))
    owner = dict(run_id=str(run.id), execution=run.model_snapshot['execution']['id'], sha256='a'*64)
    predictions = {ids[0]: dict(boxes=[dict(label='fire', x=1, y=2, width=3, height=4, score=.8)]),
        ids[1]: dict(error='fixture image failure')}
    source = tmp_path/'source.zip'
    def write():
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('owner.json', json.dumps(owner))
            for id, result in predictions.items(): archive.writestr(id+'.json', json.dumps(result))
    write()
    calls = []
    async def remote(db, current, action, **kwargs):
        calls.append(action)
        assert action == 'results', 'per-image SSH is forbidden'
        return dict(ready=True, path='/ai/chen/validation/runs/'+owner['execution']+'/results.zip',
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(), size=source.stat().st_size)
    async def connection(*args): return None
    async def validate(*args, **kwargs): return None
    def bridge(server, payload):
        calls.append('download')
        Path(payload['download']['localPath']).write_bytes(source.read_bytes())
        return {}
    class DB:
        async def get(self, *args, **kwargs): return run
        async def scalars(self, query): return SimpleNamespace(all=lambda: [UUID(id) for id in ids])
        async def commit(self): pass
    @asynccontextmanager
    async def sessions(): yield DB()
    monkeypatch.setattr(cp, 'ROOT', tmp_path)
    monkeypatch.setattr(cp, 'remote', remote)
    monkeypatch.setattr(training_runs, 'connection', connection)
    monkeypatch.setattr(training_runs, 'bridge', bridge)
    monkeypatch.setattr(validation, 'validate_context', validate)
    return SimpleNamespace(ids=ids, run=run, owner=owner, predictions=predictions, source=source,
        write=write, calls=calls, sessions=sessions)


@pytest.mark.asyncio
async def test_one_download_serves_all_images_and_isolates_image_errors(batch):
    first = await cp.infer(batch.sessions, batch.run, {'asset_id':batch.ids[0]})
    assert first == batch.predictions[batch.ids[0]]
    with pytest.raises(HTTPException) as error:
        await cp.infer(batch.sessions, batch.run, {'asset_id':batch.ids[1]})
    assert error.value.status_code == 502
    assert await cp.infer(batch.sessions, batch.run, {'asset_id':batch.ids[0]}) == first
    assert batch.calls == ['results', 'download']


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['hash', 'owner', 'missing', 'path'])
async def test_invalid_result_package_is_never_used(batch, monkeypatch, invalid):
    if invalid == 'owner': batch.owner['execution'] = str(uuid4()); batch.write()
    if invalid == 'missing': del batch.predictions[batch.ids[1]]; batch.write()
    if invalid == 'path': batch.predictions['../outside'] = dict(boxes=[]); batch.write()
    if invalid == 'hash':
        original = training_runs.bridge
        def corrupt(server, payload):
            original(server, payload)
            Path(payload['download']['localPath']).write_bytes(b'broken transfer')
        monkeypatch.setattr(training_runs, 'bridge', corrupt)
    with pytest.raises(HTTPException):
        await cp.infer(batch.sessions, batch.run, {'asset_id':batch.ids[0]})


@pytest.mark.asyncio
async def test_cancel_during_remote_wait_does_not_download_results(batch, monkeypatch):
    async def waiting(db, run, action, **kwargs):
        batch.calls.append(action); batch.run.status='canceled'
        return dict(ready=False)
    monkeypatch.setattr(cp, 'remote', waiting)
    with pytest.raises(HTTPException) as error:
        await cp.infer(batch.sessions, batch.run, {'asset_id':batch.ids[0]})
    assert error.value.status_code == 409 and batch.calls == ['results']


@pytest.mark.asyncio
async def test_cancel_during_download_discards_local_package(batch, monkeypatch):
    original = training_runs.bridge
    def cancel(server, payload):
        original(server, payload); batch.run.status='canceled'
    monkeypatch.setattr(training_runs, 'bridge', cancel)
    with pytest.raises(HTTPException) as error:
        await cp.infer(batch.sessions, batch.run, {'asset_id':batch.ids[0]})
    assert error.value.status_code == 409 and not cp.result_directory(batch.run).exists()


@pytest.mark.asyncio
async def test_stop_cleans_only_current_attempt_local_results(batch, monkeypatch):
    await cp.infer(batch.sessions,batch.run,{'asset_id':batch.ids[0]})
    directory=cp.result_directory(batch.run);other=directory.parent/str(uuid4());other.mkdir()
    (other/'keep.json').write_text('{}')
    async def stop(db,run,action,**kwargs):
        assert action=='stop';return dict(stopped=True)
    monkeypatch.setattr(cp,'remote',stop)
    await cp.stop(None,batch.run)
    assert not batch.run.model_snapshot['execution']['active'] and not directory.exists()
    assert (other/'keep.json').read_text()=='{}'


def test_executor_waits_for_batch_exit_before_export(tmp_path, monkeypatch):
    import pathlib
    monkeypatch.setattr(executor,'Path',lambda value:tmp_path if str(value)=='/ai/chen/validation/runs' else pathlib.Path(value))
    monkeypatch.setattr(executor,'state',lambda *args:dict(State=dict(Running=True)))
    monkeypatch.setattr(executor.time,'sleep',lambda seconds:None)
    result=executor.execute(dict(action='results',run_id=str(uuid4()),execution=str(uuid4()),
        config=dict(weight_path='/models/best.pt',weight_sha256='a'*64)))
    assert result==dict(ready=False) and not list(tmp_path.rglob('results.zip'))


def test_executor_exports_completed_batch_and_missing_results_as_errors(tmp_path, monkeypatch):
    import pathlib, sys
    execution=str(uuid4());run_id=str(uuid4());ids=[str(uuid4()),str(uuid4())]
    payload=dict(action='stage',run_id=run_id,execution=execution,
        config=dict(weight_path='/models/best.pt',weight_sha256='a'*64))
    monkeypatch.setattr(executor,'Path',lambda value:tmp_path if str(value)=='/ai/chen/validation/runs' else pathlib.Path(value))
    monkeypatch.setattr(executor,'state',lambda *args:dict(State=dict(Running=False,ExitCode=1)))
    monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(LOCK_EX=2,flock=lambda *args:None))
    root=Path(executor.execute(payload)['directory'])
    (root/'input').mkdir();(root/'input/manifest.json').write_text(json.dumps([dict(asset_id=id) for id in ids]))
    (root/'results').mkdir();(root/'results'/(ids[0]+'.json')).write_text(json.dumps(dict(boxes=[])))
    result=executor.execute({**payload,'action':'results'})
    assert result['ready'] and result['sha256']==hashlib.sha256(Path(result['path']).read_bytes()).hexdigest()
    with zipfile.ZipFile(result['path']) as archive:
        assert json.loads(archive.read(ids[0]+'.json'))==dict(boxes=[])
        assert json.loads(archive.read(ids[1]+'.json'))['error']
        assert json.loads(archive.read('owner.json'))['run_id']==run_id
