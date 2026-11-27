"""Durable preparation runs after the HTTP acknowledgement, without real SSH."""
from uuid import UUID
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import async_sessionmaker
from fastapi import HTTPException
from models import TrainingRun
from models.training_run import TrainingGpuHold
from services.training_runs import transfer_snapshot
from tests.test_training_runs import training_tables,spec,platform_payload,platform_wemm_payload


@pytest.mark.asyncio
@pytest.mark.parametrize('backend',['docker','k8s'])
@pytest.mark.parametrize('outcome',['ready','failed','canceled'])
async def test_async_unpack_controls_training_start(monkeypatch,backend,outcome):
    from types import SimpleNamespace
    from services import training_runs
    run=SimpleNamespace(status='starting',config={'execution_backend':backend},source={'kind':'snapshot','sha256':'a'*64},inspection={'startup':{'state':'running','phase':'unpack'}},error=None)
    class DB:
        async def refresh(self,run): pass
        async def commit(self): pass
        async def rollback(self): pass
    calls=[];states=iter(['running','running',outcome])
    async def remote(db,run,action,**values):
        calls.append(action)
        if action=='unpack_begin':return {'state':'running'}
        if action=='unpack_status':return {'state':next(states),'error':'准备失败'}
        if action=='start':return {'status':'running'}
        return {}
    async def ensure(db,run):
        if run.status!='starting':raise HTTPException(409,'已取消')
    async def sleep(seconds):
        if outcome=='canceled':run.status='canceled'
    async def transfer(db,run):await training_runs.prepare_snapshot(db,run)
    async def progress(*args,**kwargs):pass
    async def check(*args):return {'ok':True}
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'ensure_starting',ensure)
    monkeypatch.setattr(training_runs,'transfer_snapshot',transfer)
    monkeypatch.setattr(training_runs,'startup_progress',progress)
    monkeypatch.setattr(training_runs,'check',check)
    monkeypatch.setattr(training_runs,'build_script',lambda config:'script')
    monkeypatch.setattr(training_runs.asyncio,'sleep',sleep)
    if outcome=='ready':
        await training_runs.prepare_start(DB(),run)
        assert calls.count('unpack_status')==3 and calls.count('start')==1
    else:
        with pytest.raises(HTTPException):await training_runs.prepare_start(DB(),run)
        assert 'start' not in calls
        if outcome=='canceled':assert run.status=='canceled'


@pytest.mark.asyncio
async def test_docker_unpack_keeps_preparing_during_long_wait_and_lost_replies(monkeypatch):
    from types import SimpleNamespace
    from services import training_runs
    calls=[]
    run=SimpleNamespace(status='starting',config={'execution_backend':'docker'},
        source={'kind':'snapshot','sha256':'a'*64},inspection={'startup':{}})
    simulated_seconds=0
    async def remote(db,run,action,**values):
        calls.append(action)
        assert values['snapshot_hash']=='a'*64
        if len(calls) in (1,52):
            raise HTTPException(503,'SSH/MCP执行失败，执行状态需要核对')
        return {'state':'ready' if simulated_seconds>310 else 'running'}
    async def progress(db,run,phase,message,**values):
        run.inspection['startup'].update(values,phase=phase)
    async def ensure(db,run):assert run.status=='starting'
    async def sleep(seconds):
        nonlocal simulated_seconds
        simulated_seconds+=seconds
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'startup_progress',progress)
    monkeypatch.setattr(training_runs,'ensure_starting',ensure)
    monkeypatch.setattr(training_runs.asyncio,'sleep',sleep)
    await training_runs.prepare_snapshot(None,run)
    assert calls[:3]==['unpack_begin','unpack_begin','unpack_status']
    assert calls.count('unpack_begin')==2 and simulated_seconds>310
    assert run.inspection['startup']['async_unpack'] is True


@pytest.mark.asyncio
async def test_start_acknowledges_without_remote_work_and_worker_records_progress(client,auth_headers,test_engine,monkeypatch,platform_payload):
    from services import training_runs
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        assert run.inspection['startup']['state']=='running'
        return dict(ok=True,checks=[]) if action=='precheck' else dict(status='running')
    monkeypatch.setattr(training_runs,'remote',remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
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
async def test_background_failure_is_persisted_and_gpu_holds_stay_safe(client,auth_headers,test_engine,monkeypatch,failure,platform_payload):
    from services import training_runs
    async def remote(db,run,action,**values):
        if failure=='ssh':raise HTTPException(503,'SSH状态待核对')
        return dict(ok=False,checks=[dict(name='GPU',ok=False,detail='busy')])
    monkeypatch.setattr(training_runs,'remote',remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    await training_runs.process_start(UUID(run['id']),sessions)
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['inspection']['startup']['state']=='failed' and current['error']
    assert current['status']==('unknown' if failure=='ssh' else 'draft')
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TrainingGpuHold))==(1 if failure=='ssh' else 0)


@pytest.mark.asyncio
async def test_cancel_queued_start_never_contacts_ssh_or_launches(client,auth_headers,test_engine,monkeypatch,platform_payload):
    from services import training_runs
    async def no_remote(*args,**kwargs):raise AssertionError('queued cancellation must not launch SSH')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    stopped=client.post('/api/training/runs/'+run['id']+'/stop',headers=auth_headers)
    assert stopped.status_code==200 and stopped.json()['status']=='canceled'
    await training_runs.process_start(UUID(run['id']),async_sessionmaker(test_engine,expire_on_commit=False))


@pytest.mark.asyncio
async def test_recovery_does_not_relaunch_an_interrupted_start(client,auth_headers,test_engine,platform_payload):
    from services import training_runs
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
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
async def test_recovery_resumes_docker_unpack_without_reupload(client,auth_headers,test_engine,platform_payload,monkeypatch):
    from services import training_runs
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    key=UUID(run['id'])
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    async with sessions() as db:
        current=await db.get(TrainingRun,key)
        current.inspection={'startup':{**current.inspection['startup'],'state':'running',
            'phase':'unpack','async_unpack':True}}
        await db.commit()
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        return {'state':'ready'} if action=='unpack_begin' else {'status':'running'}
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'transfer_snapshot',transfer_snapshot)
    await training_runs.recover_startups(sessions)
    await training_runs.process_start(key,sessions)
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='running'
    assert calls==['unpack_begin','start']
    async with sessions() as db:assert await db.scalar(select(func.count()).select_from(TrainingGpuHold))==1


@pytest.mark.asyncio
async def test_refresh_during_preparation_never_marks_a_missing_container_unknown(client,auth_headers,monkeypatch,platform_payload):
    from services import training_runs
    async def no_remote(*args,**kwargs):raise AssertionError('preparation is not a missing-container failure')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    response=client.post('/api/training/runs/'+run['id']+'/refresh',headers=auth_headers)
    assert response.status_code==200 and response.json()['status']=='starting'


@pytest.mark.asyncio
@pytest.mark.parametrize('remote_failure',[False,True])
async def test_inflight_refresh_does_not_overwrite_resumed_preparation(monkeypatch,remote_failure):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from services import training_runs
    class DB:
        async def refresh(self,run):
            run.status='starting'
            run.inspection={'startup':{'state':'running'}}
        async def commit(self): pytest.fail('stale status must not be saved')
    async def remote(db,run,action):
        assert run.status=='unknown' and action=='status'
        if remote_failure:raise HTTPException(503,'通信失败')
        return {'status':'missing'}
    monkeypatch.setattr(training_runs,'remote',remote)
    run=SimpleNamespace(status='unknown',config={'execution_backend':'k8s'},inspection={},error=None)
    await training_runs.refresh(DB(),run)
    assert run.status=='starting' and run.inspection['startup']['state']=='running'


@pytest.mark.asyncio
@pytest.mark.parametrize(('backend','startup','previous_error','remote_status','expected'),[
    ('k8s',{'state':'failed','events':[{'phase':'stage','message':'SSH/MCP通信失败'}]},'容器不存在','missing','SSH/MCP通信失败'),
    ('k8s',{'state':'interrupted','events':[]},'启动准备中断','created','启动准备中断'),
    ('k8s',{},None,'missing','训练Job/Pod不存在或尚未启动，请点击停止核对'),
    ('docker',{'state':'failed','events':[{'phase':'unpack','message':'冻结包哈希不匹配'}]},None,'missing','冻结包哈希不匹配'),
    ('docker',{},None,'missing','容器不存在或尚未启动，请点击停止核对并释放占用'),
])
async def test_refresh_missing_preserves_startup_error_and_uses_backend_name(monkeypatch,backend,startup,previous_error,remote_status,expected):
    from types import SimpleNamespace
    from services import training_runs
    class DB:
        async def refresh(self,run): pass
        async def commit(self): pass
    async def remote(db,run,action,**kwargs):
        assert action=='status'
        return {'status':remote_status}
    async def release(*args): pytest.fail('missing state must not release resources')
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'release',release)
    run=SimpleNamespace(status='unknown',config={'execution_backend':backend},
        inspection={'startup':startup},error=previous_error)
    await training_runs.refresh(DB(),run)
    assert run.status=='unknown' and run.error==expected


@pytest.mark.asyncio
async def test_stop_during_stage_prevents_late_container_start(client,auth_headers,test_engine,monkeypatch,platform_payload):
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
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    await training_runs.process_start(UUID(run['id']),async_sessionmaker(test_engine,expire_on_commit=False))
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='canceled' and current['inspection']['startup']['state']=='canceled'
    assert 'start' not in calls


@pytest.mark.asyncio
async def test_worker_claims_durable_queue_without_an_http_background_task(client,auth_headers,test_engine,monkeypatch,platform_payload):
    import asyncio
    from services import training_runs
    calls=[]
    async def remote(db,run,action,**values):
        calls.append(action)
        return dict(ok=True,checks=[]) if action=='precheck' else dict(status='running')
    monkeypatch.setattr(training_runs,'remote',remote)
    monkeypatch.setattr(training_runs,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    run=client.post('/api/training/runs',headers=auth_headers,json=dict(platform_payload)).json()
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
@pytest.mark.parametrize('cache_hit',[False,True,'shared'])
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
        if action=='snapshot_directory':
            assert cache_hit=='shared' and values['legacy_runs']==[legacy['output_dir']]
            assert values['snapshot_hash']==snapshot['content_hash']
            return dict(hit=True,shared_ready=True)
        if action=='snapshot_cache':
            assert cache_hit!='shared'
            assert values['legacy_archives']==[legacy['output_dir']+'/input.zip']
            assert values['snapshot_hash']==snapshot['content_hash']
            return dict(hit=cache_hit,imported=False)
        if action=='unpack_begin':return dict(state='ready')
        return dict(status='running') if action=='start' else dict(ok=True)
    def upload(server,value):
        assert Path(value['upload']['localPath']).is_file()
        calls.append('upload');return dict(success=True)
    monkeypatch.setattr(training_runs,'remote',remote);monkeypatch.setattr(training_runs,'bridge',upload)
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    legacy=client.post('/api/training/runs',headers=auth_headers,json=spec(snapshot_id=snapshot['id'],dataset_path='',output_root='/data/old-runs').model_dump(mode='json')).json()
    if cache_hit=='shared':
        async with async_sessionmaker(test_engine,expire_on_commit=False)() as db:
            row=await db.get(training_runs.TrainingRun,UUID(legacy['id']))
            row.status='running'
            await db.commit()
    run=client.post('/api/training/runs',headers=auth_headers,json=spec(snapshot_id=snapshot['id'],dataset_path='').model_dump(mode='json')).json()
    client.post('/api/training/runs/'+run['id']+'/start',headers=auth_headers)
    await training_runs.process_start(UUID(run['id']),async_sessionmaker(test_engine,expire_on_commit=False))
    current=client.get('/api/training/runs/'+run['id'],headers=auth_headers).json()
    assert current['status']=='running',current
    probe='snapshot_directory' if cache_hit=='shared' else 'snapshot_cache'
    assert calls==['precheck','stage',probe]+([] if cache_hit else ['upload'])+['unpack_begin','start']
    progress=current['inspection']['startup']
    assert progress['cache_hit'] is bool(cache_hit)
    if cache_hit:
        assert 'upload' not in progress['steps']
        message='直接只读挂载' if cache_hit=='shared' else '跳过上传'
        assert any(message in row['message'] for row in progress['events'])
    else:
        assert [row['phase'] for row in progress['events']]==['queued','precheck','stage','archive','archive','upload','unpack','unpack','container','complete']
        assert progress['archive_bytes']>0
