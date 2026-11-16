"""Tenant-scoped GPU connection storage and authenticated encryption."""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import func, select, inspect, or_
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from models.gpu_server import GpuServer
from models.tenant import Tenant
from services import gpu_resources


def credential_cipher():
    key = settings.GPU_CREDENTIAL_KEY
    if not key:
        # shortcut: JWT-derived fallback; resave passwords when rotating JWT, or configure a dedicated key.
        key = base64.urlsafe_b64encode(hashlib.sha256(
            b'data-platform-gpu-credentials-v1\0' + settings.JWT_SECRET_KEY.encode()).digest())
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise HTTPException(503, 'GPU凭据加密密钥配置无效，请联系管理员。') from None


def encrypt_password(password):
    return credential_cipher().encrypt(password.encode('utf-8')).decode('ascii')


def decrypt_password(encrypted):
    if not encrypted:
        raise HTTPException(409, '服务器未保存密码，请重填密码。')
    try:
        return credential_cipher().decrypt(encrypted.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeError):
        raise HTTPException(409, 'GPU凭据已失效，请重填密码。') from None


def summary(row):
    return dict(id=row.id, name=row.name, host=row.host, port=row.port, username=row.username,
                enabled=row.enabled, training_enabled=row.training_enabled, revision=row.revision,
                has_password=bool(row.password_encrypted), source='password',
                jump=dict(host=row.jump_host,port=row.jump_port,username=row.jump_username,
                          has_password=bool(row.jump_password_encrypted)) if row.jump_host else None)


def connection(row):
    result=dict(host=row.host,port=row.port,username=row.username,password=decrypt_password(row.password_encrypted))
    if row.jump_host:
        result['jump']=dict(host=row.jump_host,port=row.jump_port,username=row.jump_username,
                            password=decrypt_password(row.jump_password_encrypted))
    return result


def connection_key(row):
    identity=[row.host,row.port,row.username,row.jump_host,row.jump_port,row.jump_username]
    return hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()


def matches_connection(row,snapshot):
    return snapshot['connection_key']==connection_key(row) if snapshot.get('connection_key') else snapshot['revision']==row.revision


async def pin_task_connections(db,server):
    from models.training_run import TrainingRun
    from models.validation import ValidationRun
    key=connection_key(server);id=str(server.id)
    def pin(snapshot):
        # Only the current revision proves an unmarked legacy task's endpoint.
        if snapshot.get('id')==id and not snapshot.get('connection_key') and snapshot.get('revision')==server.revision:
            return {**snapshot,'connection_key':key}
        return snapshot
    conn=await db.connection()
    if await conn.run_sync(lambda c:inspect(c).has_table('training_runs')):
        rows=(await db.scalars(select(TrainingRun).where(TrainingRun.tenant_id==server.tenant_id,or_(
            TrainingRun.server['id'].as_string()==id,TrainingRun.config['onnx_stage']['server']['id'].as_string()==id)).with_for_update())).all()
        for run in rows:
            run.server=pin(run.server)
            stage=run.config.get('onnx_stage')
            if stage and pin(stage['server'])!=stage['server']:run.config={**run.config,'onnx_stage':{**stage,'server':pin(stage['server'])}}
    if await conn.run_sync(lambda c:inspect(c).has_table('validation_runs')):
        rows=(await db.scalars(select(ValidationRun).where(ValidationRun.tenant_id==server.tenant_id,
            ValidationRun.model_snapshot['checkpoint']['server']['id'].as_string()==id).with_for_update())).all()
        for run in rows:
            checkpoint=run.model_snapshot['checkpoint'];snapshot=pin(checkpoint['server'])
            if snapshot!=checkpoint['server']:run.model_snapshot={**run.model_snapshot,'checkpoint':{**checkpoint,'server':snapshot}}


def request_connection(request):
    result={key:getattr(request,key) for key in ('host','port','username')}
    result['password']=request.password.get_secret_value()
    if request.jump:
        result['jump']=request.jump.model_dump(exclude={'password'})
        result['jump']['password']=request.jump.password.get_secret_value()
    return result


def set_jump(row,jump):
    if jump is None:
        for key in ('host','port','username','password_encrypted'):setattr(row,'jump_'+key,None)
        return
    changed=any(getattr(row,'jump_'+key)!=getattr(jump,key) for key in ('host','port','username'))
    if changed or not row.jump_password_encrypted:
        if not jump.password or not jump.password.get_secret_value():raise HTTPException(422,'新增或修改跳板机连接时必须重填跳板机密码。')
    if jump.password and jump.password.get_secret_value():row.jump_password_encrypted=encrypt_password(jump.password.get_secret_value())
    for key in ('host','port','username'):setattr(row,'jump_'+key,getattr(jump,key))


async def scoped_server(db, server_id, tenant_id, lock=False):
    query = select(GpuServer).where(GpuServer.id == server_id, GpuServer.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, 'GPU服务器不存在。')
    return row


async def list_servers(db, tenant_id):
    rows = (await db.scalars(select(GpuServer).where(GpuServer.tenant_id == tenant_id)
                            .order_by(GpuServer.created_at, GpuServer.id))).all()
    return [dict(id='default', name=settings.GPU_SERVER_NAME, host=None, port=None, username=None,
                 enabled=True, training_enabled=True, revision=0, has_password=False, source='ssh_manager',jump=None),
            *(summary(row) for row in rows)]


async def reserve_quota(db, tenant_id, amount):
    # Serialize the per-tenant quota check across concurrent PostgreSQL requests.
    await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    count = await db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id == tenant_id))
    if count + amount > 200:
        raise HTTPException(409, '每个租户最多保存200台GPU服务器。')


def new_server(tenant_id, request):
    row=GpuServer(tenant_id=tenant_id, **request.model_dump(exclude={'password','jump'}),
                     password_encrypted=encrypt_password(request.password.get_secret_value()))
    set_jump(row,request.jump)
    return row


async def create_server(db, tenant_id, request):
    await reserve_quota(db, tenant_id, 1)
    row = new_server(tenant_id, request)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return summary(row)


async def import_servers(db, tenant_id, requests):
    await reserve_quota(db, tenant_id, len(requests))
    existing = {(host,port,user,jhost or '',jport or 0,juser or '') for host,port,user,jhost,jport,juser in
        (await db.execute(select(GpuServer.host,GpuServer.port,GpuServer.username,GpuServer.jump_host,GpuServer.jump_port,GpuServer.jump_username)
        .where(GpuServer.tenant_id == tenant_id))).all()}
    seen = set()
    for index, request in enumerate(requests, start=1):
        jump=request.jump
        key = (request.host, request.port, request.username,jump.host if jump else '',jump.port if jump else 0,jump.username if jump else '')
        if key in seen:
            raise HTTPException(409, f'第{index}项连接与本批其他项重复，请移除重复项。')
        if key in existing:
            raise HTTPException(409, f'第{index}项连接已在当前租户保存，批量导入仅新增，不覆盖已有连接。')
        seen.add(key)
    try:
        rows = [new_server(tenant_id, request) for request in requests]
        db.add_all(rows)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise HTTPException(503, 'GPU服务器批量导入失败，请检查凭据加密配置后重试。') from None
    return dict(imported=len(rows), items=[summary(row) for row in rows])


async def update_server(db, server_id, tenant_id, request):
    row = await scoped_server(db, server_id, tenant_id, lock=True)
    if request.model_fields_set == {'training_enabled'}:
        row.training_enabled = request.training_enabled
        await db.commit()
        await db.refresh(row)
        return summary(row)
    await assert_not_training(db,server_id)
    await pin_task_connections(db,row)
    values = request.model_dump(exclude_unset=True, exclude={'password', 'clear_password','jump'})
    if any(key in values and values[key] != getattr(row, key) for key in ('host', 'port', 'username')):
        if not request.password or not request.password.get_secret_value():
            raise HTTPException(422, '修改连接目标时必须重填密码。')
    if request.clear_password:
        row.password_encrypted = None
    elif request.password and request.password.get_secret_value():
        row.password_encrypted = encrypt_password(request.password.get_secret_value())
    if 'jump' in request.model_fields_set:set_jump(row,request.jump)
    for key, value in values.items():
        setattr(row, key, value)
    row.revision += 1
    await db.commit()
    gpu_resources.invalidate_connection(str(server_id))
    await db.refresh(row)
    return summary(row)


async def delete_server(db, server_id, tenant_id):
    row=await scoped_server(db,server_id,tenant_id,lock=True)
    await assert_not_training(db,server_id)
    await db.delete(row)
    await db.commit()
    gpu_resources.invalidate_connection(str(server_id))


async def assert_not_training(db,server_id):
    from models.training_run import TrainingRun
    conn=await db.connection()
    if await conn.run_sync(lambda c:inspect(c).has_table('training_runs')):
        active=await db.scalar(select(TrainingRun.id).where(TrainingRun.server['id'].as_string()==str(server_id),
            TrainingRun.status.in_(('starting','running','stopping','unknown'))).limit(1))
        if active:raise HTTPException(409,'此服务器有活动训练或状态待核对任务，请先停止并确认资源释放后修改连接')
    if await conn.run_sync(lambda c:inspect(c).has_table('validation_runs')):
        from models.validation import ValidationRun
        active=await db.scalar(select(ValidationRun.id).where(
            ValidationRun.model_snapshot['checkpoint']['server']['id'].as_string()==str(server_id),
            ValidationRun.model_snapshot['execution']['active'].as_boolean().is_(True)).limit(1))
        if active:raise HTTPException(409,'此服务器有权重验证或状态待核对任务，请先确认验证已停止')


def collect_snapshot(server_id, revision, name, connection):
    try:
        return gpu_resources.snapshot_connection(server_id, revision, name, connection)
    except HTTPException as error:
        status = 504 if error.status_code == 504 else 503
        raise HTTPException(status, 'GPU采集超时，请稍后重试。' if status == 504 else
                            'GPU采集失败，请核对连接、Python3及nvidia-smi。') from None
    except Exception:
        raise HTTPException(503, 'GPU采集失败，请核对连接、Python3及nvidia-smi。') from None
