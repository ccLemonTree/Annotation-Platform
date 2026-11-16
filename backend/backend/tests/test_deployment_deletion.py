from uuid import uuid4
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def deployment_tables(test_engine):
    from models import Base
    async with test_engine.begin() as conn:
        for name in ('training_runs','model_repository_items','model_releases','deployment_targets','model_deployments','deployment_leases'):
            await conn.run_sync(lambda c:Base.metadata.tables[name].create(c,checkfirst=True))


async def record(db,user,status='draft',inspection=None):
    from models.model_deployment import ModelDeployment
    row=ModelDeployment(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,kind='model',status=status,target_key=str(uuid4()),source={},target={},inspection=inspection or {},logs=[])
    db.add(row);await db.commit();return row


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['draft','checked','staged','transfer_failed','activation_failed'])
async def test_delete_unapplied_records(deployment_tables,client,auth_headers,test_db,test_user,status):
    row=await record(test_db,test_user,status)
    response=client.request('DELETE','/api/model-deployments',headers=auth_headers,json={'ids':[str(row.id)]})
    assert response.status_code==200,response.text
    assert response.json()['deleted']==[str(row.id)]
    assert client.get('/api/model-deployments/'+str(row.id),headers=auth_headers).status_code==404


@pytest.mark.asyncio
@pytest.mark.parametrize('status,inspection',[('active',{}),('rolled_back',{}),('unknown',{}),('transferring',{}),('activation_failed',{'mutation_started':True}),('staged',{'apply_started':True})])
async def test_delete_rejects_running_or_recovery_records_atomically(deployment_tables,client,auth_headers,test_db,test_user,status,inspection):
    good=await record(test_db,test_user)
    blocked=await record(test_db,test_user,status,inspection)
    response=client.request('DELETE','/api/model-deployments',headers=auth_headers,json={'ids':[str(good.id),str(blocked.id)]})
    assert response.status_code==409,response.text
    assert client.get('/api/model-deployments/'+str(good.id),headers=auth_headers).status_code==200


@pytest.mark.asyncio
async def test_delete_respects_lease_tenant_and_role(deployment_tables,client,auth_headers,test_db,test_user):
    from models.model_deployment import DeploymentLease
    row=await record(test_db,test_user)
    test_db.add(DeploymentLease(resource='locked',job_id=row.id));await test_db.commit()
    assert client.request('DELETE','/api/model-deployments',headers=auth_headers,json={'ids':[str(row.id)]}).status_code==409
    assert client.request('DELETE','/api/model-deployments',headers=auth_headers,json={'ids':[str(uuid4())]}).status_code==404
    from models import Tenant
    from types import SimpleNamespace
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.commit()
    foreign=await record(test_db,SimpleNamespace(tenant_id=2,id=test_user.id))
    assert client.request('DELETE','/api/model-deployments',headers=auth_headers,json={'ids':[str(foreign.id)]}).status_code==404
    test_user.role='annotator';await test_db.commit()
    assert client.request('DELETE','/api/model-deployments',headers=auth_headers,json={'ids':[str(row.id)]}).status_code==403
