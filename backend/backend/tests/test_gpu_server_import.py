"""Batch connections are atomic, tenant-scoped and never expose credentials."""
from uuid import UUID
import pytest
from sqlalchemy import select,func
from tests.test_gpu_servers import gpu_table,payload,PASSWORD,URL


@pytest.mark.asyncio
async def test_batch_import_encrypts_and_returns_safe_summaries(client,auth_headers,test_db,caplog):
    from models.gpu_server import GpuServer
    from services.gpu_servers import decrypt_password
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(),payload(name='second',host='192.0.2.2',enabled=False)]})
    assert response.status_code==201,response.text
    result=response.json();assert result['imported']==2 and len(result['items'])==2
    assert response.headers['cache-control']=='no-store'
    assert result['items'][0]['host']=='2001:db8::1' and result['items'][1]['enabled'] is False
    for item in result['items']:
        assert item['revision']==1 and item['has_password'] is True
        assert 'password' not in item and 'password_encrypted' not in item
        row=await test_db.get(GpuServer,UUID(item['id']))
        assert row.tenant_id==1 and decrypt_password(row.password_encrypted)==PASSWORD
        assert row.password_encrypted not in response.text
    assert PASSWORD not in response.text and PASSWORD not in caplog.text


@pytest.mark.asyncio
async def test_invalid_entry_rejects_whole_batch_without_password_echo(client,auth_headers,test_db,caplog):
    from models.gpu_server import GpuServer
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(),payload(name='',password='private-import-marker')]})
    assert response.status_code==422
    assert response.json()['data']['errors'][0]['loc']==['body','servers',1,'name']
    assert 'private-import-marker' not in response.text and 'private-import-marker' not in caplog.text
    assert await test_db.scalar(select(func.count()).select_from(GpuServer))==0


@pytest.mark.parametrize('count',[0,201])
def test_import_count_is_bounded_without_echoing_nested_credentials(client,auth_headers,count):
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload() for _ in range(count)]})
    assert response.status_code==422 and 'p@ss' not in response.text


@pytest.mark.asyncio
async def test_duplicate_connections_are_rejected_without_overwriting(client,auth_headers,test_db):
    from models.gpu_server import GpuServer
    duplicate=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(),payload(name='alias',host='2001:db8::1')]})
    assert duplicate.status_code==409 and '第2项' in duplicate.text
    assert await test_db.scalar(select(func.count()).select_from(GpuServer))==0
    await test_db.rollback()
    original=client.post(URL,headers=auth_headers,json=payload()).json()
    stored=await test_db.get(GpuServer,UUID(original['id']));cipher=stored.password_encrypted
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(host='192.0.2.2'),payload(name='overwrite',password='different')]})
    assert response.status_code==409 and '覆盖' in response.text
    assert await test_db.scalar(select(func.count()).select_from(GpuServer))==1
    await test_db.refresh(stored)
    assert stored.name==original['name'] and stored.password_encrypted==cipher and stored.revision==1


@pytest.mark.asyncio
async def test_batch_quota_and_duplicate_checks_are_tenant_scoped(client,auth_headers,test_db):
    from models import Tenant
    from models.gpu_server import GpuServer
    test_db.add(Tenant(id=2,tenant_name='foreign'));await test_db.commit()
    test_db.add_all([GpuServer(tenant_id=2,name=f'foreign-{i}',host='192.0.2.2') for i in range(200)])
    test_db.add_all([GpuServer(tenant_id=1,name=f'existing-{i}',host='192.0.2.1') for i in range(199)])
    await test_db.commit()
    batch=[payload(host='192.0.2.2'),payload(host='192.0.2.3')]
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':batch})
    assert response.status_code==409 and '200' in response.text
    assert await test_db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id==1))==199
    await test_db.rollback()
    assert client.post(URL+'/import',headers=auth_headers,json={'servers':batch[:1]}).status_code==201
    assert await test_db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id==1))==200
    assert await test_db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id==2))==200


@pytest.mark.asyncio
async def test_import_requires_admin_and_refuses_tenant_injection(client,auth_headers,test_db,test_user):
    batch={'servers':[payload()]}
    assert client.post(URL+'/import',json=batch).status_code==401
    assert client.post(URL+'/import',headers=auth_headers,json={**batch,'tenant_id':2}).status_code==422
    assert client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(tenant_id=2)]}).status_code==422
    test_user.role='annotator';await test_db.commit()
    assert client.post(URL+'/import',headers=auth_headers,json=batch).status_code==403


@pytest.mark.asyncio
async def test_encryption_failure_is_atomic_and_sanitized(client,auth_headers,test_db,monkeypatch,caplog):
    from models.gpu_server import GpuServer
    from services import gpu_servers
    original=gpu_servers.encrypt_password;calls=[]
    def encrypt(secret):
        calls.append(secret)
        if len(calls)==2:raise RuntimeError('private-encryption-marker')
        return original(secret)
    monkeypatch.setattr(gpu_servers,'encrypt_password',encrypt)
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(),payload(host='192.0.2.2')]})
    assert response.status_code==503 and 'private-encryption-marker' not in response.text
    assert 'private-encryption-marker' not in caplog.text and 'p@ss' not in caplog.text
    assert await test_db.scalar(select(func.count()).select_from(GpuServer))==0


@pytest.mark.asyncio
async def test_database_failure_rolls_back_flushed_batch_and_hides_error(client,auth_headers,test_db,monkeypatch,caplog):
    from models.gpu_server import GpuServer
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import AsyncSession
    original=AsyncSession.commit
    async def fail_commit(session):
        if any(isinstance(row,GpuServer) for row in session.new):
            await session.flush()
            raise SQLAlchemyError('private-database-marker')
        await original(session)
    monkeypatch.setattr(AsyncSession,'commit',fail_commit)
    response=client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(),payload(host='192.0.2.2')]})
    assert response.status_code==500
    assert 'private-database-marker' not in response.text and 'private-database-marker' not in caplog.text
    assert await test_db.scalar(select(func.count()).select_from(GpuServer))==0
