"""WebSocket terminal contracts; owned fake bridge, no real SSH or business writes."""
import asyncio
import base64
import importlib.util
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import pytest_asyncio
from starlette.websockets import WebSocketDisconnect


@pytest_asyncio.fixture(autouse=True)
async def terminal_tables(test_engine):
    from models.gpu_server import GpuServer
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda c:GpuServer.__table__.create(c,checkfirst=True))


@pytest.fixture
def fake_bridge(monkeypatch):
    from services import gpu_terminal
    opened=[]
    class Bridge:
        def __init__(self):
            self.queue=asyncio.Queue();self.queue.put_nowait(dict(event='ready'))
            self.messages=[];self.closed=Event()
        async def next_event(self):return await self.queue.get()
        async def send(self,value):
            self.messages.append(value)
            if value['type']=='input':await self.queue.put(dict(event='output',data=value['data']))
        async def close(self):self.closed.set()
    async def open_bridge(access,cols,rows):
        bridge=Bridge();opened.append(bridge);return bridge
    monkeypatch.setattr(gpu_terminal,'open_bridge',open_bridge)
    return opened


def auth(auth_headers,**values):
    return dict(type='auth',token=auth_headers['Authorization'].split(' ',1)[1],server_id='default',cols=100,rows=30,**values)


@pytest.mark.parametrize('origin',['https://evil.invalid','http://[broken'])
def test_terminal_requires_valid_origin(client,origin):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':origin}):pass


@pytest.mark.asyncio
async def test_bridge_closing_survives_caller_cancellation():
    from services.gpu_terminal import McpTerminal
    class Stream:
        async def read(self,n):await asyncio.sleep(10)
    class Input:
        closed=False
        def close(self):self.closed=True
    class Process:
        stdin=Input();stderr=Stream()
        def __init__(self):self.finished=asyncio.Event()
        async def wait(self):await self.finished.wait();return 0
    process=Process();bridge=McpTerminal(process)
    task=asyncio.create_task(bridge.close());await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    process.finished.set()
    await bridge.close()
    assert process.stdin.closed and bridge.diagnostics.done()


@pytest.mark.asyncio
async def test_revocation_during_connection_never_exposes_terminal(client,auth_headers,fake_bridge,monkeypatch):
    from fastapi import HTTPException
    from services import gpu_terminal
    original=gpu_terminal.load_access;calls=0
    async def check(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls>1:raise HTTPException(403,'权限已撤销')
        return await original(*args,**kwargs)
    monkeypatch.setattr(gpu_terminal,'load_access',check)
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));assert ws.receive_json()['code']==403
    assert fake_bridge[0].closed.wait(2)


def test_terminal_rejects_invalid_token_before_ssh(client,fake_bridge):
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(dict(type='auth',token='invalid',server_id='default',cols=100,rows=30))
        assert ws.receive_json()['type']=='error'
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    assert not fake_bridge


@pytest.mark.asyncio
async def test_terminal_rejects_non_admin(client,auth_headers,test_db,test_user,fake_bridge):
    test_user.role='annotator';await test_db.commit()
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));assert ws.receive_json()['code']==403
    assert not fake_bridge


def test_raw_input_ctrl_c_resize_and_disconnect(client,auth_headers,fake_bridge):
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));assert ws.receive_json()['type']=='ready'
        ws.send_json(dict(type='resize',cols=130,rows=40))
        raw='中文\x03\x1b[A'.encode();ws.send_bytes(raw)
        assert ws.receive_bytes()==raw
        ws.send_json(dict(type='ack',bytes=len(raw)))
        assert fake_bridge[0].messages[0]==dict(type='resize',cols=130,rows=40)
        assert base64.b64decode(fake_bridge[0].messages[1]['data'])==raw
    assert fake_bridge[0].closed.wait(2)


def test_oversized_input_is_rejected_without_forwarding(client,auth_headers,fake_bridge):
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));ws.receive_json();ws.send_bytes(b'x'*8193)
        assert ws.receive_json()['code']==400
    assert not fake_bridge[0].messages


def test_idle_terminal_is_closed(client,auth_headers,fake_bridge,monkeypatch):
    from services import gpu_terminal
    monkeypatch.setattr(gpu_terminal,'AUTH_INTERVAL',0.01)
    monkeypatch.setattr(gpu_terminal,'IDLE_SECONDS',-1)
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));ws.receive_json();assert ws.receive_json()['code']==408
    assert fake_bridge[0].closed.wait(2)


def test_bridge_failure_does_not_leak_credentials(client,auth_headers,monkeypatch):
    from services import gpu_terminal
    async def fail(*args):raise RuntimeError('secret-password-from-provider')
    monkeypatch.setattr(gpu_terminal,'open_bridge',fail)
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));error=ws.receive_json()
        assert error['code']==503 and 'secret-password' not in str(error)
    assert not gpu_terminal.active_sessions


@pytest.mark.parametrize('message',[{'type':'resize','cols':0,'rows':25},{'type':'unknown'}, {'type':'ack','bytes':999999}])
def test_invalid_control_frames_close_session(client,auth_headers,fake_bridge,message):
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));ws.receive_json();ws.send_json(message)
        assert ws.receive_json()['type']=='error'
    assert fake_bridge[0].closed.wait(2)


def test_session_limit_is_released(client,auth_headers,fake_bridge):
    from services import gpu_terminal
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as first:
        first.send_json(auth(auth_headers));first.receive_json()
        with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as second:
            second.send_json(auth(auth_headers));second.receive_json()
            with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as extra:
                extra.send_json(auth(auth_headers));assert extra.receive_json()['code']==429
    assert len(fake_bridge)==2
    assert all(bridge.closed.wait(2) for bridge in fake_bridge)
    assert not gpu_terminal.active_sessions


@pytest.mark.asyncio
async def test_other_tenant_server_cannot_open(client,auth_headers,test_db,fake_bridge):
    from models import Tenant
    from models.gpu_server import GpuServer
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush()
    server=GpuServer(id=uuid4(),tenant_id=2,name='private',host='127.0.0.1',port=22,username='root',enabled=True,password_encrypted='not-used')
    test_db.add(server);await test_db.commit()
    value=auth(auth_headers);value['server_id']=str(server.id)
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(value);assert ws.receive_json()['code']==404
    assert not fake_bridge


@pytest.mark.asyncio
async def test_role_revocation_disconnects_existing_terminal(client,auth_headers,test_db,test_user,fake_bridge,monkeypatch):
    from services import gpu_terminal
    monkeypatch.setattr(gpu_terminal,'AUTH_INTERVAL',0.05)
    with client.websocket_connect('/api/gpu-terminal/ws',headers={'origin':'http://testserver'}) as ws:
        ws.send_json(auth(auth_headers));ws.receive_json()
        test_user.role='annotator';await test_db.commit()
        assert ws.receive_json()['code']==403
    assert fake_bridge[0].closed.wait(2)


def test_pty_installer_preserves_bytes_and_is_idempotent(tmp_path):
    file=Path(__file__).resolve().parents[3]/'scripts/install_ssh_terminal_extension.py'
    spec=importlib.util.spec_from_file_location('pty_installer',file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=b"import { fileURLToPath } from 'url';\r\nconst server = new McpServer({});\r\nasync function getConnection(a) {}\r\nasync function getServerConfig(a) {}\r\nconst connectionTimestamps = new Map();\r\n// Start the server\r\nasync function main() {}\r\n"
    entry=tmp_path/'index.js';entry.write_bytes(original)
    result=module.install(entry,tmp_path/'backup')
    assert result['changed'] and Path(result['backup']).read_bytes()==original
    changed=entry.read_bytes();assert b'PLATFORM_SSH_TERMINAL_EXTENSION' in changed and b'\r\n' in changed
    assert not module.install(entry,tmp_path/'backup')['changed']
    assert entry.read_bytes()==changed


@pytest.mark.parametrize('value', ['http://localhost:3000,http://localhost:5173','["http://localhost:3000", "http://localhost:5173"]'])
def test_configured_browser_origins_support_csv_and_json(value):
    from config.settings import Settings
    assert Settings(CORS_ORIGINS=value).CORS_ORIGINS_LIST==['http://localhost:3000','http://localhost:5173']
