"""Durable preparation runs after the HTTP acknowledgement, without real SSH."""
from uuid import UUID
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import async_sessionmaker
from fastapi import HTTPException
from models import TrainingRun
from models.training_run import TrainingGpuHold
from tests.test_training_runs import training_tables,spec


@pytest.mark.asyncio
async def test_start_acknowledges_without_remote_work_and_worker_records_progress(client,auth_headers,test_engine,monkeypatch):
    from services import training_runs
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        assert run.inspection['startup']['state']=='running'
        return dict(ok=True,checks=[]) if action=='precheck' else dict(status='running')
    monkeypatch.setattr(training_runs,'remote',remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    response=client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    assert response.status_code==202,response.text
    assert response.json()['status']=='starting' and response.json()['inspection']['startup']['state']=='queued'
    assert calls==[]
    assert client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers).status_code==409
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    await training_runs.process_start(UUID(run['id']),sessions)
    await training_runs.process_start(UUID(run['id']),sessions)
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='running' and current['inspection']['startup']['state']=='complete'
    assert calls==['precheck','stage','start']
    assert [event['phase'] for event in current['inspection']['startup']['events']]==['queued','precheck','stage','container','complete']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['precheck','ssh'])
async def test_background_failure_is_persisted_and_gpu_holds_stay_safe(client,auth_headers,test_engine,monkeypatch,failure):
    from services import training_runs
    async def remote(db,run,action,**values):
        if failure=='ssh':raise HTTPException(503,'SSH状态待核对')
        return dict(ok=False,checks=[dict(name='GPU',ok=False,detail='busy')])
    monkeypatch.setattr(training_runs,'remote',remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    await training_runs.process_start(UUID(run['id']),sessions)
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['inspection']['startup']['state']=='failed' and current['error']
    assert current['status']==('unknown' if failure=='ssh' else 'draft')
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TrainingGpuHold))==(1 if failure=='ssh' else 0)


@pytest.mark.asyncio
async def test_cancel_queued_start_never_contacts_ssh_or_launches(client,auth_headers,test_engine,monkeypatch):
    from services import training_runs
    async def no_remote(*args,**kwargs):raise AssertionError('queued cancellation must not launch SSH')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    stopped=client.post('/api/training/runs/'+run['id']+'/stop',headers=auth_headers)
    assert stopped.status_code==200 and stopped.json()['status']=='canceled'
    await training_runs.process_start(UUID(run['id']),async_sessionmaker(test_engine,expire_on_commit=False))


@pytest.mark.asyncio
async def test_recovery_does_not_relaunch_an_interrupted_start(client,auth_headers,test_engine):
    from services import training_runs
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    async with sessions() as db:
        current=await db.get(TrainingRun,UUID(run['id']))
        current.inspection={**current.inspection,'startup':{**current.inspection['startup'],'state':'running','phase':'upload'}}
        await db.commit()
    await training_runs.recover_startups(sessions)
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='unknown' and current['inspection']['startup']['state']=='interrupted'
    async with sessions() as db:assert await db.scalar(select(func.count()).select_from(TrainingGpuHold))==1


@pytest.mark.asyncio
async def test_refresh_during_preparation_never_marks_a_missing_container_unknown(client,auth_headers,monkeypatch):
    from services import training_runs
    async def no_remote(*args,**kwargs):raise AssertionError('preparation is not a missing-container failure')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    response=client.post('/api/training/runs/'+run['id']+'/refresh',headers=auth_headers)
    assert response.status_code==200 and response.json()['status']=='starting'


@pytest.mark.asyncio
async def test_stop_during_stage_prevents_late_container_start(client,auth_headers,test_engine,monkeypatch):
    from services import training_runs
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        if action=='precheck':return dict(ok=True,checks=[])
        if action=='stage':
            response=client.post('/api/training/runs/'+str(run.id)+'/stop',headers=auth_headers)
            assert response.status_code==200
            return {}
        if action=='stop':return dict(status='canceled')
        raise AssertionError('late training start after cancellation')
    monkeypatch.setattr(training_runs,'remote',remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    await training_runs.process_start(UUID(run['id']),async_sessionmaker(test_engine,expire_on_commit=False))
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='canceled' and current['inspection']['startup']['state']=='canceled'
    assert 'start' not in calls


@pytest.mark.asyncio
async def test_worker_claims_durable_queue_without_an_http_background_task(client,auth_headers,test_engine,monkeypatch):
    import asyncio
    from services import training_runs
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        return dict(ok=True,checks=[]) if action=='precheck' else dict(status='running')
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    run=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    await training_runs.start_worker()
    try:
        for _ in range(100):
            async with training_runs.AsyncSessionLocal() as db:
                current=await db.get(TrainingRun,UUID(run['id']))
                state=current.status
            if state=='running':break
            await asyncio.sleep(.03)
        assert state=='running' and calls==['precheck','stage','start']
    finally:await training_runs.stop_worker()


@pytest.mark.asyncio
@pytest.mark.parametrize('cache_hit',[False,True])
async def test_snapshot_start_records_transfer_and_verification_before_container(client,auth_headers,test_engine,test_project,tmp_path,monkeypatch,cache_hit):
    from pathlib import Path
    from services import training_runs
    from tests.test_p2_exports import populate
    populate(client,auth_headers,test_project.id)
    snapshot=client.post(f'/api/export/projects/{test_project.id}/snapshots',headers=auth_headers,json={
        'selection':{'ratios':{'train':50,'val':50,'test':0}}}).json()
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        if action=='precheck':return dict(ok=True,checks=[])
        if action=='snapshot_cache':
            assert values['legacy_archives']==[legacy['output_dir']+'/input.zip']
            assert values['snapshot_hash']==snapshot['content_hash']
            return dict(hit=cache_hit,imported=False)
        return dict(status='running') if action=='start' else dict(ok=True)
    def upload(server,value):
        assert Path(value['upload']['localPath']).is_file()
        calls.append('upload');return dict(success=True)
    monkeypatch.setattr(training_runs,'remote',remote);monkeypatch.setattr(training_runs,'bridge',upload)
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    legacy=client.post('/api/training/runs',headers=auth_headers,json=spec(snapshot_id=snapshot['id'],dataset_path='',output_root='/data/old-runs').model_dump(mode='json')).json()
    run=client.post('/api/training/runs',headers=auth_headers,json=spec(snapshot_id=snapshot['id'],dataset_path='').model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    await training_runs.process_start(UUID(run['id']),async_sessionmaker(test_engine,expire_on_commit=False))
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='running',current
    assert calls==['precheck','stage','snapshot_cache']+([] if cache_hit else ['upload'])+['unpack','start']
    progress=current['inspection']['startup']
    assert progress['cache_hit'] is cache_hit
    if cache_hit:
        assert 'upload' not in progress['steps']
        assert any('跳过上传' in row['message'] for row in progress['events'])
    else:
        assert [row['phase'] for row in progress['events']]==['queued','precheck','stage','archive','archive','upload','unpack','container','complete']
        assert progress['archive_bytes']>0
