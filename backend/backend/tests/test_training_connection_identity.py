"""Connection metadata/credential edits must not redirect old training tasks."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from tests.test_gpu_servers import gpu_table,payload,URL


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'name':'renamed'},{'password':'rotated-fixture'},{'host':'192.0.2.2','password':'new-fixture'}])
async def test_legacy_task_identity_is_pinned_before_connection_edit(client,auth_headers,test_db,test_engine,change):
    from uuid import UUID,uuid4
    from models import Base
    from models.training_run import TrainingRun
    from models.gpu_server import GpuServer
    from models.validation import ValidationSet,ValidationRun
    from services import training_runs,gpu_servers,checkpoint_validation
    async with test_engine.begin() as conn:
        for name in ('training_runs','validation_sets','validation_runs'):
            await conn.run_sync(lambda c:Base.metadata.tables[name].create(c,checkfirst=True))
    server=client.post(URL,headers=auth_headers,json=payload()).json()
    snapshot={key:server[key] for key in ('id','name','revision')}
    row=await test_db.get(GpuServer,UUID(server['id']))
    key=gpu_servers.connection_key(row)
    run=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='legacy',kind='model_export',status='completed',server=snapshot,
        config={'onnx_stage':{'server':snapshot}},source={},output_dir='/fixture')
    stale=TrainingRun(id=uuid4(),tenant_id=1,user_id=1,name='unverified',kind='model_export',status='completed',
        server={**snapshot,'revision':0},config={},source={},output_dir='/fixture-unverified')
    dataset=ValidationSet(id=uuid4(),tenant_id=1,user_id=1,name='fixture',labels=[],samples=[],source={})
    test_db.add(dataset);await test_db.flush()
    validation=ValidationRun(id=uuid4(),set_id=dataset.id,tenant_id=1,user_id=1,name='legacy',status='completed',
        model_snapshot={'checkpoint':{'server':snapshot},'execution':{'active':False}},config={})
    test_db.add_all([run,stale,validation]);await test_db.commit()
    assert client.patch(URL+'/'+server['id'],headers=auth_headers,json=change).status_code==200
    await test_db.refresh(run)
    assert run.server.get('connection_key')==key
    assert run.config['onnx_stage']['server'].get('connection_key')==key
    await test_db.refresh(validation);await test_db.refresh(stale)
    assert validation.model_snapshot['checkpoint']['server'].get('connection_key')==key
    assert not stale.server.get('connection_key')
    if 'host' in change:
        with pytest.raises(HTTPException,match='连接'):await training_runs.connection(test_db,run)
        with pytest.raises(HTTPException,match='连接'):await checkpoint_validation.validate_context(test_db,validation)
    else:
        await training_runs.connection(test_db,run)
        await checkpoint_validation.validate_context(test_db,validation)


@pytest.mark.asyncio
async def test_draft_start_uses_identity_instead_of_metadata_revision(client,auth_headers,test_db,monkeypatch):
    from uuid import UUID,uuid4
    from models.gpu_server import GpuServer
    from services import training_runs,gpu_servers,gpu_reservations
    server=client.post(URL,headers=auth_headers,json=payload()).json()
    snapshot=await training_runs.server_snapshot(test_db,server['id'],1)
    await test_db.rollback()
    assert client.patch(URL+'/'+server['id'],headers=auth_headers,json={'name':'renamed','password':'rotated-fixture'}).status_code==200
    row=await test_db.get(GpuServer,UUID(server['id']),populate_existing=True)
    run=SimpleNamespace(id=uuid4(),status='draft',server=snapshot,tenant_id=1,config={'gpu_uuids':[]})
    async def scoped(*args,**kwargs):return run
    async def server_row(*args,**kwargs):return row
    class ReservationsReached(Exception):pass
    async def free(*args,**kwargs):raise ReservationsReached()
    monkeypatch.setattr(training_runs,'scoped',scoped);monkeypatch.setattr(gpu_servers,'scoped_server',server_row)
    monkeypatch.setattr(gpu_reservations,'assert_free',free)
    with pytest.raises(ReservationsReached):await training_runs.launch(test_db,run,SimpleNamespace(tenant_id=1))


@pytest.mark.asyncio
async def test_snapshot_allows_rename_and_password_rotation_on_same_endpoint(client,auth_headers,test_db):
    from services import training_runs
    response=client.post(URL,headers=auth_headers,json=payload());assert response.status_code==201
    server=response.json();snapshot=await training_runs.server_snapshot(test_db,server['id'],1)
    assert snapshot.get('connection_key')
    run=SimpleNamespace(server=snapshot,tenant_id=1)
    await test_db.rollback()
    assert client.patch(URL+'/'+server['id'],headers=auth_headers,json={'name':'renamed','password':'rotated-fixture'}).status_code==200
    assert (await training_runs.connection(test_db,run))['password']=='rotated-fixture'


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'host':'192.0.2.2'},{'port':2222},{'username':'other'}])
async def test_changed_endpoint_still_refuses_old_task(client,auth_headers,test_db,change):
    from services import training_runs
    server=client.post(URL,headers=auth_headers,json=payload()).json()
    snapshot=await training_runs.server_snapshot(test_db,server['id'],1)
    await test_db.rollback()
    assert client.patch(URL+'/'+server['id'],headers=auth_headers,json={**change,'password':'new-fixture'}).status_code==200
    with pytest.raises(HTTPException,match='连接'):await training_runs.connection(test_db,SimpleNamespace(server=snapshot,tenant_id=1))


@pytest.mark.asyncio
async def test_legacy_snapshot_without_endpoint_proof_remains_protected(client,auth_headers,test_db):
    from services import training_runs
    server=client.post(URL,headers=auth_headers,json=payload()).json()
    assert client.patch(URL+'/'+server['id'],headers=auth_headers,json={'name':'renamed'}).status_code==200
    with pytest.raises(HTTPException,match='旧任务'):await training_runs.connection(test_db,SimpleNamespace(server=server,tenant_id=1))
