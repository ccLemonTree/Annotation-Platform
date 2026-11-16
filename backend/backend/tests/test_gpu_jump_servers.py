"""Jump credentials use isolated SQLite, fake collection and fake terminal bridges."""
from uuid import UUID
import pytest
from tests.test_gpu_servers import gpu_table,payload,URL,PASSWORD

JUMP={'host':'192.0.2.5','port':2222,'username':'gateway','password':'jump-secret-fixture'}


@pytest.mark.asyncio
async def test_create_jump_encrypts_both_passwords_and_never_returns_them(client,auth_headers,test_db):
    from models.gpu_server import GpuServer
    from services import gpu_servers
    response=client.post(URL,headers=auth_headers,json=payload(jump=JUMP))
    assert response.status_code==201,response.text
    result=response.json();assert result['jump']==dict(host=JUMP['host'],port=2222,username='gateway',has_password=True)
    assert JUMP['password'] not in response.text and PASSWORD not in response.text
    row=await test_db.get(GpuServer,UUID(result['id']))
    assert row.jump_password_encrypted!=JUMP['password']
    assert gpu_servers.connection(row)['jump']==JUMP


@pytest.mark.asyncio
async def test_edit_preserves_jump_secret_requires_new_secret_for_new_jump_and_can_remove(client,auth_headers,test_db):
    response=client.post(URL,headers=auth_headers,json=payload(jump=JUMP));assert response.status_code==201,response.text
    id=response.json()['id'];jump={k:v for k,v in JUMP.items() if k!='password'}
    response=client.patch(URL+'/'+id,headers=auth_headers,json={'name':'renamed','jump':jump})
    assert response.status_code==200 and response.json()['jump']['has_password']
    assert client.patch(URL+'/'+id,headers=auth_headers,json={'jump':dict(jump,host='192.0.2.6')}).status_code==422
    assert client.patch(URL+'/'+id,headers=auth_headers,json={'enabled':False}).json()['jump']['host']==JUMP['host']
    assert client.patch(URL+'/'+id,headers=auth_headers,json={'jump':None}).json()['jump'] is None
    from models.gpu_server import GpuServer
    row=await test_db.get(GpuServer,UUID(id));await test_db.refresh(row)
    assert row.jump_host is None and row.jump_password_encrypted is None


@pytest.mark.parametrize('jump',[{},dict(JUMP,host='localhost'),dict(JUMP,port=0),dict(JUMP,password=''),dict(JUMP,jump=JUMP)])
def test_invalid_jump_is_rejected_and_errors_do_not_echo_password(client,auth_headers,jump):
    response=client.post(URL,headers=auth_headers,json=payload(jump=jump))
    assert response.status_code==422
    assert JUMP['password'] not in response.text


def test_import_allows_same_private_address_through_different_jumps_and_rejects_exact_route_duplicate(client,auth_headers):
    batch={'servers':[payload(jump=JUMP),payload(name='other-site',jump=dict(JUMP,host='192.0.2.6'))]}
    response=client.post(URL+'/import',headers=auth_headers,json=batch)
    assert response.status_code==201 and response.json()['imported']==2,response.text
    assert client.post(URL+'/import',headers=auth_headers,json={'servers':[payload(jump=JUMP)]}).status_code==409


def test_unsaved_and_saved_gpu_collection_receive_jump_only_in_private_connection(client,auth_headers,monkeypatch):
    from services import gpu_servers
    from tests.test_gpu_servers import fake_snapshot
    seen=[]
    def collect(id,rev,name,connection):
        seen.append(connection);return fake_snapshot(id,rev,name,connection)
    monkeypatch.setattr(gpu_servers,'collect_snapshot',collect)
    assert client.post(URL+'/test',headers=auth_headers,json=payload(jump=JUMP)).status_code==200
    saved=client.post(URL,headers=auth_headers,json=payload(jump=JUMP)).json()
    assert client.get(URL+'/'+saved['id']+'/resources',headers=auth_headers).status_code==200
    assert len(seen)==2 and all(c['jump']==JUMP for c in seen)


@pytest.mark.asyncio
async def test_training_release_and_terminal_share_jump_connection(client,auth_headers,test_db):
    from services import training_runs,gpu_release,gpu_terminal
    from models import User
    from types import SimpleNamespace
    response=client.post(URL,headers=auth_headers,json=payload(jump=JUMP));assert response.status_code==201,response.text
    data=response.json();actor=await test_db.get(User,1)
    conn=await training_runs.connection(test_db,SimpleNamespace(server=data,tenant_id=actor.tenant_id))
    assert conn['jump']==JUMP
    access=await gpu_release.access(test_db,actor,data['id']);assert access['connection']['jump']==JUMP
    auth=gpu_terminal.TerminalAuth(type='auth',token=auth_headers['Authorization'].split()[1],server_id=data['id'],cols=80,rows=24)
    access=await gpu_terminal.load_access(test_db,auth,include_connection=True);assert access['connection']['jump']==JUMP
