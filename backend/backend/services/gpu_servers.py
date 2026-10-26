"""Tenant-scoped GPU connection storage and authenticated encryption."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import func, select, inspect

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
                enabled=row.enabled, revision=row.revision,
                has_password=bool(row.password_encrypted), source='password')


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
                 enabled=True, revision=0, has_password=False, source='ssh_manager'),
            *(summary(row) for row in rows)]


async def create_server(db, tenant_id, request):
    # Serialize the per-tenant quota check across concurrent PostgreSQL requests.
    await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    count = await db.scalar(select(func.count()).select_from(GpuServer).where(GpuServer.tenant_id == tenant_id))
    if count >= 200:
        raise HTTPException(409, '每个租户最多保存200台GPU服务器。')
    row = GpuServer(tenant_id=tenant_id, **request.model_dump(exclude={'password'}),
                    password_encrypted=encrypt_password(request.password.get_secret_value()))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return summary(row)


async def update_server(db, server_id, tenant_id, request):
    row = await scoped_server(db, server_id, tenant_id, lock=True)
    await assert_not_training(db,server_id)
    values = request.model_dump(exclude_unset=True, exclude={'password', 'clear_password'})
    if any(key in values and values[key] != getattr(row, key) for key in ('host', 'port', 'username')):
        if not request.password or not request.password.get_secret_value():
            raise HTTPException(422, '修改连接目标时必须重填密码。')
    if request.clear_password:
        row.password_encrypted = None
    elif request.password and request.password.get_secret_value():
        row.password_encrypted = encrypt_password(request.password.get_secret_value())
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
