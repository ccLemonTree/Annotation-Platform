"""GPU release checks use isolated task rows and simulated remote transport."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select,func
from tests.test_validation import validation_tables


def target(**changes):
    return {**dict(pid=42,gpu_uuid='GPU-aaa',name='python',gpu_uuids=['GPU-aaa'],start='100',boot='fixture',container=None),**changes}


@pytest.fixture
def remote(monkeypatch):
    from services import gpu_release as service
    calls=[]
    async def access(db,user,server_id):return {'id':server_id,'name':'fixture-server','revision':0,'connection':None}
    async def execute(access,action,**values):
        calls.append((action,values))
        return target() if action=='inspect' else {'stopped':True}
    monkeypatch.setattr(service,'access',access);monkeypatch.setattr(service,'remote',execute)
    return calls


@pytest.mark.asyncio
async def test_process_preview_never_stops_and_confirmation_is_bound_to_user(remote):
    from services import gpu_release as service
    user=SimpleNamespace(id=1,tenant_id=1)
    preview=await service.preview(None,user,'default',service.ReleaseTarget(pid=42,gpu_uuid='GPU-aaa'))
    assert preview['target']['pid']==42 and [c[0] for c in remote]==['inspect']
    assert 'token' in preview and 'connection' not in preview
    for wrong in [SimpleNamespace(id=2,tenant_id=1),SimpleNamespace(id=1,tenant_id=2)]:
        with pytest.raises(HTTPException) as error:await service.confirm(None,wrong,'default',service.ReleaseConfirm(token=preview['token']))
        assert error.value.status_code==409
    result=await service.confirm(None,user,'default',service.ReleaseConfirm(token=preview['token']))
    assert result['stopped'] and remote[-1][0]=='stop' and remote[-1][1]['target']['start']=='100'


@pytest.mark.asyncio
async def test_changed_server_or_tampered_confirmation_cannot_stop(remote,monkeypatch):
    from services import gpu_release as service
    user=SimpleNamespace(id=1,tenant_id=1)
    preview=await service.preview(None,user,'default',service.ReleaseTarget(pid=42,gpu_uuid='GPU-aaa'))
    async def changed(*args):return {'id':'default','name':'fixture-server','revision':1,'connection':None}
    monkeypatch.setattr(service,'access',changed)
    for token in [preview['token'],preview['token']+'broken']:
        with pytest.raises(HTTPException):await service.confirm(None,user,'default',service.ReleaseConfirm(token=token))
    assert len(remote)==1


def test_remote_pid_reuse_or_managed_container_never_receives_raw_signal(monkeypatch):
    from services import gpu_release_executor as executor
    signaled=[]
    monkeypatch.setattr(executor,'inspect_process',lambda pid,gpu:target(start='200'))
    monkeypatch.setattr(executor,'stop_process',lambda *args:signaled.append(args))
    with pytest.raises(ValueError,match='变化'):executor.execute({'action':'stop','target':target(),'force':False})
    managed=target(container={'id':'a'*64,'labels':{'platform.training.id':str(uuid4())},'name':'owned'})
    monkeypatch.setattr(executor,'inspect_process',lambda pid,gpu:managed)
    with pytest.raises(ValueError,match='平台任务'):executor.execute({'action':'stop','target':managed,'force':False})
    assert not signaled


def test_release_inputs_reject_arbitrary_commands_pid_one_and_unbound_task():
    from pydantic import ValidationError
    from services.gpu_release import ReleaseTarget,ReleaseConfirm
    for value in [{'pid':1,'gpu_uuid':'GPU-aaa'},{'pid':True,'gpu_uuid':'GPU-aaa'},
                  {'pid':42},{'pid':42,'gpu_uuid':'GPU-aaa','command':'kill -9 1'},
                  {'task_kind':'training'},{'pid':42,'gpu_uuid':'GPU-aaa','task_kind':'training','task_id':str(uuid4())}]:
        with pytest.raises(ValidationError):ReleaseTarget(**value)
    with pytest.raises(ValidationError):ReleaseConfirm(token='x',force='true')
    id=uuid4()
    assert ReleaseTarget.model_validate({'task_kind':'training','task_id':str(id)}).task_id==id


@pytest.mark.asyncio
async def test_release_endpoints_are_admin_only(client,auth_headers,test_db,test_user,monkeypatch):
    from services import gpu_release as service
    calls=[]
    async def inspect(*args):calls.append('preview');return {'token':'fixture','target':target()}
    monkeypatch.setattr(service,'preview',inspect)
    url='/api/gpu-resources/servers/default/release/preview'
    body={'pid':42,'gpu_uuid':'GPU-aaa'}
    assert client.post(url,json=body).status_code==401
    response=client.post(url,headers=auth_headers,json=body)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    test_user.role='annotator';await test_db.commit()
    assert client.post(url,headers=auth_headers,json=body).status_code==403
    assert calls==['preview']


@pytest_asyncio.fixture
async def task_rows(validation_tables,test_engine,test_db,test_user):
    from models.training_run import TrainingRun,TrainingGpuHold
    from models.validation import ValidationSet,ValidationRun
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn:[table.create(conn,checkfirst=True) for table in (TrainingRun.__table__,TrainingGpuHold.__table__)])
    server={'id':'default','name':'fixture-server','revision':0}
    training=TrainingRun(tenant_id=test_user.tenant_id,user_id=test_user.id,name='Owned training',kind='yolo26',status='running',
        config={'gpu_uuids':['GPU-aaa','GPU-bbb']},source={},server=server,output_dir='/owned',inspection={})
    dataset=ValidationSet(tenant_id=test_user.tenant_id,user_id=test_user.id,name='Owned set',labels=[],samples=[],source={},total=0)
    test_db.add_all([training,dataset]);await test_db.flush()
    test_db.add(TrainingGpuHold(gpu_uuid='GPU-aaa',run_id=training.id))
    validation=ValidationRun(tenant_id=test_user.tenant_id,user_id=test_user.id,set_id=dataset.id,name='Owned preview',status='running',
        model_snapshot={'source':'checkpoint','preview':True,'checkpoint':{'server':server,'device':'GPU-bbb'},'execution':{'id':str(uuid4()),'active':True}},config={},total=0)
    test_db.add(validation);await test_db.commit()
    return training,validation


@pytest.mark.asyncio
async def test_training_task_stop_updates_state_and_releases_only_after_confirmation(remote,task_rows,test_db,test_user,monkeypatch):
    from services import gpu_release as service,training_runs
    from models.training_run import TrainingGpuHold
    training,_=task_rows;actions=[]
    async def execute(db,run,action,**values):actions.append(action);return {'status':'canceled'}
    monkeypatch.setattr(training_runs,'remote',execute)
    tasks=await service.tasks(test_db,test_user,'default')
    assert {t['kind'] for t in tasks}=={'training','validation'}
    preview=await service.preview(test_db,test_user,'default',service.ReleaseTarget(task_kind='training',task_id=training.id))
    assert preview['target']['gpu_uuids']==['GPU-aaa','GPU-bbb'] and not actions
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==1
    result=await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token']))
    assert result['stopped'] and actions==['stop']
    await test_db.refresh(training)
    assert training.status=='canceled' and await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==0


@pytest.mark.asyncio
async def test_training_stop_failure_keeps_hold_and_foreign_tenant_cannot_inspect(remote,task_rows,test_db,test_user,monkeypatch):
    from services import gpu_release as service,training_runs
    from models.training_run import TrainingGpuHold
    training,_=task_rows
    async def failed(*args,**kwargs):raise HTTPException(503,'未确认容器停止')
    monkeypatch.setattr(training_runs,'remote',failed)
    request=service.ReleaseTarget(task_kind='training',task_id=training.id)
    with pytest.raises(HTTPException) as error:await service.preview(test_db,SimpleNamespace(id=1,tenant_id=2),'default',request)
    assert error.value.status_code==404
    preview=await service.preview(test_db,test_user,'default',request)
    with pytest.raises(HTTPException):await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token']))
    await test_db.refresh(training)
    assert training.status=='unknown' and await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==1


@pytest.mark.asyncio
async def test_validation_stop_uncertainty_keeps_reservation_and_changed_attempt_rejected(remote,task_rows,test_db,test_user,monkeypatch):
    from services import gpu_release as service,checkpoint_validation as cp
    _,run=task_rows
    async def failed(*args):raise HTTPException(503,'未确认停止')
    monkeypatch.setattr(cp,'stop',failed)
    request=service.ReleaseTarget(task_kind='validation',task_id=run.id)
    preview=await service.preview(test_db,test_user,'default',request)
    with pytest.raises(HTTPException) as error:await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token']))
    assert error.value.status_code==409
    await test_db.refresh(run);assert run.model_snapshot['execution']['active']
    run.model_snapshot={**run.model_snapshot,'execution':{**run.model_snapshot['execution'],'id':str(uuid4())}}
    await test_db.commit()
    with pytest.raises(HTTPException,match=''):await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token']))


@pytest.mark.asyncio
async def test_managed_process_maps_to_owned_training_task(remote,task_rows,test_db,test_user,monkeypatch):
    from services import gpu_release as service
    training,_=task_rows
    async def inspect(*args,**kwargs):return target(container={'id':'a'*64,'name':'owned','labels':{'platform.training.id':str(training.id)}})
    monkeypatch.setattr(service,'remote',inspect)
    preview=await service.preview(test_db,test_user,'default',service.ReleaseTarget(pid=42,gpu_uuid='GPU-aaa'))
    assert preview['target']['kind']=='training' and preview['target']['id']==str(training.id)


@pytest.mark.parametrize('force',[False,True])
def test_raw_process_force_is_opt_in_and_pidfd_is_closed(monkeypatch,force):
    from services import gpu_release_executor as executor
    signals=[];closed=[]
    monkeypatch.setattr(executor.os,'pidfd_open',lambda pid:99,raising=False)
    monkeypatch.setattr(executor,'process_start',lambda pid:'100')
    monkeypatch.setattr(executor.os,'close',closed.append)
    monkeypatch.setattr(executor.signal,'pidfd_send_signal',lambda fd,sig:signals.append(sig),raising=False)
    monkeypatch.setattr(executor.signal,'SIGKILL',9,raising=False)
    ready=iter([([],[],[]),([99],[],[])])
    monkeypatch.setattr(executor.select,'select',lambda *args:next(ready))
    result=executor.stop_process(target(),force)
    assert result['stopped']==force and len(signals)==(2 if force else 1) and closed==[99]


def test_process_inspection_rejects_kubernetes_workload(monkeypatch):
    from services import gpu_release_executor as executor
    monkeypatch.setattr(executor,'command',lambda args:'GPU-aaa, 42, python\n')
    monkeypatch.setattr(executor,'process_start',lambda pid:'100')
    monkeypatch.setattr(executor.Path,'read_text',lambda path:'0::/kubepods/pod-owned/container')
    with pytest.raises(ValueError,match='Kubernetes'):executor.inspect_process(42,'GPU-aaa')


def test_monitor_invalidation_recollects_after_release(monkeypatch):
    from services.gpu_resources import GpuMonitor
    from tests.test_gpu_resources import sample
    calls=[]
    monitor=GpuMonitor()
    def read():calls.append(1);return sample()
    monitor.snapshot(reader=read);assert monitor.snapshot(reader=read)['cached']
    monitor.invalidate();assert not monitor.snapshot(reader=read)['cached'] and len(calls)==2


@pytest.mark.asyncio
async def test_confirmed_validation_stop_clears_active_reservation(remote,task_rows,test_db,test_user,monkeypatch):
    from services import gpu_release as service,checkpoint_validation as cp
    _,run=task_rows
    async def stop(db,run):run.model_snapshot={**run.model_snapshot,'execution':{**run.model_snapshot['execution'],'active':False}}
    monkeypatch.setattr(cp,'stop',stop)
    preview=await service.preview(test_db,test_user,'default',service.ReleaseTarget(task_kind='validation',task_id=run.id))
    assert (await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token'])))['stopped']
    assert not run.model_snapshot['execution']['active']
    assert all(task['id']!=str(run.id) for task in await service.tasks(test_db,test_user,'default'))


@pytest.mark.asyncio
async def test_stale_training_hold_is_removed_only_after_remote_absence_proof(remote,task_rows,test_db,test_user,monkeypatch):
    from services import gpu_release as service,training_runs
    from models.training_run import TrainingGpuHold
    training,_=task_rows;training.status='completed';await test_db.commit()
    preview=await service.preview(test_db,test_user,'default',service.ReleaseTarget(task_kind='training',task_id=training.id))
    async def running(*args,**kwargs):return {'status':'running'}
    monkeypatch.setattr(training_runs,'remote',running)
    with pytest.raises(HTTPException):await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token']))
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==1
    async def absent(*args,**kwargs):return {'status':'missing'}
    monkeypatch.setattr(training_runs,'remote',absent)
    assert (await service.confirm(test_db,test_user,'default',service.ReleaseConfirm(token=preview['token'])))['stopped']
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==0


@pytest.mark.asyncio
async def test_expired_confirmation_never_signals_process(remote,monkeypatch):
    from services import gpu_release as service
    from itsdangerous.timed import TimestampSigner
    user=SimpleNamespace(id=1,tenant_id=1)
    preview=await service.preview(None,user,'default',service.ReleaseTarget(pid=42,gpu_uuid='GPU-aaa'))
    now=TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner,'get_timestamp',lambda self:now(self)+121)
    with pytest.raises(HTTPException) as error:await service.confirm(None,user,'default',service.ReleaseConfirm(token=preview['token']))
    assert error.value.status_code==409 and len(remote)==1
