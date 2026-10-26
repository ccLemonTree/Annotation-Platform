"""Bounded administrator PTYs over WebSocket and the existing ssh-manager MCP."""
import asyncio
import base64
import contextlib
import json
import hashlib
import logging
from pathlib import Path
import subprocess
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID,uuid4

from fastapi import HTTPException,WebSocket,WebSocketDisconnect
from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator,model_validator
from api.deps import get_current_user,get_current_superuser
from config.settings import settings
from services import gpu_servers

ROOT=Path(__file__).resolve().parents[1]
AUTH_INTERVAL=10
IDLE_SECONDS=20*60
# shortcut: process-local limits; use shared session ownership before running multiple backends.
active_sessions={}
logger=logging.getLogger(__name__)


class TerminalAuth(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    type:Literal['auth']
    token:str=Field(min_length=1,max_length=4096)
    server_id:str=Field(default='default',max_length=36)
    ssh_server:str|None=Field(default=None,min_length=1,max_length=100,pattern=r'^[^\x00-\x1f\x7f]+$')
    cols:int=Field(ge=2,le=400)
    rows:int=Field(ge=2,le=200)

    @field_validator('server_id')
    @classmethod
    def server(cls,value):
        if value!='default':UUID(value)
        return value

    @model_validator(mode='after')
    def one_server(self):
        if self.ssh_server and self.server_id!='default':raise ValueError('请选择一种服务器来源')
        return self


def allowed_origin(ws):
    origin=ws.headers.get('origin','')
    try:parsed=urlsplit(origin)
    except ValueError:return False
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.path not in ('','/') or parsed.query or parsed.fragment:return False
    return origin in settings.CORS_ORIGINS_LIST or parsed.netloc==ws.headers.get('host')


async def load_access(db,auth,include_connection=False):
    try:
        user=await get_current_user(auth.token,db)
        await get_current_superuser(user)
        result=dict(user_id=user.id,tenant_id=user.tenant_id,server_id=auth.server_id,revision=0,
            name=settings.GPU_SERVER_NAME,server=settings.GPU_SERVER_NAME,connection=None)
        if auth.ssh_server:
            from services.model_deployments import servers
            server=next((row for row in await servers() if row['name']==auth.ssh_server),None)
            if not server:raise HTTPException(404,'SSH服务器不存在或已移除')
            signature={key:server.get(key) for key in ('name','host','user','port')}
            result.update(name=server['name'],server=server['name'],server_id='ssh:'+server['name'],
                revision=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest())
        elif auth.server_id!='default':
            row=await gpu_servers.scoped_server(db,UUID(auth.server_id),user.tenant_id)
            if not row.enabled:raise HTTPException(409,'GPU服务器连接已停用')
            if not row.password_encrypted:raise HTTPException(409,'GPU服务器未保存密码')
            result.update(revision=row.revision,name=row.name,server='platform_terminal')
            if include_connection:result['connection']=dict(host=row.host,port=row.port,username=row.username,
                password=gpu_servers.decrypt_password(row.password_encrypted))
        return result
    finally:
        await db.rollback()


class McpTerminal:
    def __init__(self,process):
        self.process=process;self.closing=None
        self.diagnostics=asyncio.create_task(self.drain_diagnostics())

    async def drain_diagnostics(self):
        while await self.process.stderr.read(16384):pass

    async def send(self,value):
        data=json.dumps(value,ensure_ascii=False).encode()+b'\n'
        if len(data)>20000:raise ValueError('Input too large')
        self.process.stdin.write(data);await self.process.stdin.drain()

    async def next_event(self):
        line=await self.process.stdout.readline()
        if not line:return dict(event='closed')
        return json.loads(line)

    async def close(self):
        if self.closing is None:self.closing=asyncio.create_task(self.reap())
        await asyncio.shield(self.closing)

    async def reap(self):
        self.process.stdin.close()
        try:await asyncio.wait_for(self.process.wait(),5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):self.process.kill()
            await self.process.wait()
        self.diagnostics.cancel()
        await asyncio.gather(self.diagnostics,return_exceptions=True)


async def open_bridge(access,cols,rows):
    entry=Path(settings.GPU_SSH_MANAGER_ENTRY)
    if not settings.GPU_SSH_MANAGER_ENTRY or not entry.is_absolute() or not entry.is_file():raise HTTPException(503,'未配置ssh-manager MCP入口')
    process=await asyncio.create_subprocess_exec(settings.GPU_SSH_NODE_BINARY,str(ROOT/'scripts/terminal_mcp_bridge.mjs'),str(entry),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=160000,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0)
    bridge=McpTerminal(process)
    try:await bridge.send(dict(server=access['server'],connection=access['connection'],cols=cols,rows=rows))
    except BaseException:
        await bridge.close();raise
    return bridge


def bridge_error(value):
    return {'extension_missing':'ssh-manager缺少PTY扩展，请运行平台的扩展安装脚本',
        'policy':'此ssh-manager连接为只读或受限模式，不能打开完整交互终端',
        'platform':'当前SSH终端仅支持Linux服务器'}.get(value.get('code'),'SSH终端连接或传输失败，请核对服务器连接')


async def serve(ws:WebSocket,db):
    if not allowed_origin(ws):
        await ws.close(code=1008);return
    await ws.accept()
    bridge=None;session_id=None;tasks=[];close_code=1000
    try:
        first=await asyncio.wait_for(ws.receive(),10)
        raw=first.get('text')
        if not isinstance(raw,str) or len(raw)>8192:raise HTTPException(400,'无效的终端鉴权消息')
        auth=TerminalAuth.model_validate_json(raw)
        access=await load_access(db,auth,include_connection=True)
        owner=(access['tenant_id'],access['user_id'])
        if len(active_sessions)>=8 or sum(s['owner']==owner for s in active_sessions.values())>=2:
            raise HTTPException(429,'终端会话已达上限：每用户2个，总计8个')
        session_id=str(uuid4());active_sessions[session_id]=dict(owner=owner,bridge=None)
        bridge=await open_bridge(access,auth.cols,auth.rows)
        active_sessions[session_id]['bridge']=bridge
        initial=await asyncio.wait_for(bridge.next_event(),45)
        if initial.get('event')!='ready':raise HTTPException(503,bridge_error(initial))
        identity=tuple(access[k] for k in ('user_id','tenant_id','server_id','revision','name'))
        current=await load_access(db,auth)
        if tuple(current[k] for k in ('user_id','tenant_id','server_id','revision','name'))!=identity:
            raise HTTPException(409,'用户权限或服务器配置已变化，终端未开放')
        await ws.send_json(dict(type='ready',server_id=access['server_id'],server_name=access['name']))
        logger.info('SSH terminal opened user=%s tenant=%s server=%s session=%s',access['user_id'],access['tenant_id'],auth.server_id,session_id)
        access.pop('connection',None)
        last_input=monotonic();expected_ack=0;acknowledged=asyncio.Event()

        async def receive_input():
            nonlocal last_input,expected_ack
            while True:
                frame=await ws.receive()
                if frame['type']=='websocket.disconnect':return
                data=frame.get('bytes')
                if data is not None:
                    if not 1<=len(data)<=8192:raise HTTPException(400,'终端输入超出8192字节限制')
                    last_input=monotonic();await bridge.send(dict(type='input',data=base64.b64encode(data).decode()))
                    continue
                text=frame.get('text','')
                if len(text)>1000:raise HTTPException(400,'终端控制消息过大')
                value=json.loads(text)
                if not isinstance(value,dict):raise ValueError('Invalid control frame')
                if value.get('type')=='ack':
                    size=value.get('bytes')
                    if type(size) is not int or not 1<=size<=expected_ack:raise HTTPException(400,'无效的终端输出确认')
                    expected_ack-=size
                    if not expected_ack:acknowledged.set()
                elif value.get('type')=='resize':
                    cols,rows=value.get('cols'),value.get('rows')
                    if type(cols) is not int or type(rows) is not int or not 2<=cols<=400 or not 2<=rows<=200:raise HTTPException(400,'终端尺寸无效')
                    await bridge.send(dict(type='resize',cols=cols,rows=rows))
                else:raise HTTPException(400,'不支持的终端控制消息')

        async def forward_output():
            nonlocal expected_ack
            while True:
                value=await bridge.next_event()
                if value.get('event')=='output':
                    data=base64.b64decode(value['data'],validate=True)
                    if not 1<=len(data)<=65536:raise ValueError('Invalid output')
                    expected_ack=len(data);acknowledged.clear()
                    await ws.send_bytes(data)
                    await asyncio.wait_for(acknowledged.wait(),30)
                elif value.get('event')=='closed':
                    await ws.send_json(dict(type='closed'));return
                else:raise HTTPException(503,bridge_error(value))

        async def verify_access():
            while True:
                await asyncio.sleep(AUTH_INTERVAL)
                if monotonic()-last_input>IDLE_SECONDS:raise HTTPException(408,'终端20分钟无输入，已断开')
                current=await load_access(db,auth)
                if tuple(current[k] for k in ('user_id','tenant_id','server_id','revision','name'))!=identity:
                    raise HTTPException(409,'用户权限或服务器配置已变化，终端已断开')

        tasks=[asyncio.create_task(coro()) for coro in (receive_input,forward_output,verify_access)]
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in done:task.result()
    except WebSocketDisconnect:pass
    except (HTTPException,ValidationError,ValueError,TimeoutError) as error:
        code=error.status_code if isinstance(error,HTTPException) else 408 if isinstance(error,TimeoutError) else 400
        message=str(error.detail) if isinstance(error,HTTPException) else '终端消息无效或等待超时，请重新连接'
        close_code=1008
        with contextlib.suppress(RuntimeError,WebSocketDisconnect):await ws.send_json(dict(type='error',code=code,message=message))
    except Exception:
        close_code=1011
        with contextlib.suppress(RuntimeError,WebSocketDisconnect):await ws.send_json(dict(type='error',code=503,message='SSH终端连接失败，请核对连接与MCP扩展'))
    finally:
        async def cleanup():
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            try:
                if bridge:await bridge.close()
            finally:
                if session_id:
                    active_sessions.pop(session_id,None)
                    logger.info('SSH terminal closed session=%s',session_id)
            with contextlib.suppress(RuntimeError,WebSocketDisconnect):await ws.close(code=close_code)
        await asyncio.shield(asyncio.create_task(cleanup()))


async def close_all():
    await asyncio.gather(*(s['bridge'].close() for s in list(active_sessions.values()) if s['bridge']),return_exceptions=True)
