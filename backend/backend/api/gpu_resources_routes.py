"""Administrator-only resource inspection; no remote command input."""
import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_superuser
from database import get_db_session
from models.user import User
from schemas.gpu_server import GpuServerCreate, GpuServerResponse, GpuServerTest, GpuServerUpdate
from services import gpu_servers
from services.gpu_resources import GpuMonitor
from services import gpu_release

router = APIRouter(dependencies=[Depends(get_current_superuser)])
monitor = GpuMonitor()


@router.get('/servers/{server_id}/release/tasks')
async def release_tasks(server_id:str,response:Response,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    response.headers['Cache-Control']='no-store'
    return await gpu_release.tasks(db,user,server_id)


@router.post('/servers/{server_id}/release/preview')
async def preview_release(server_id:str,request:gpu_release.ReleaseTarget,response:Response,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    response.headers['Cache-Control']='no-store'
    return await gpu_release.preview(db,user,server_id,request)


@router.post('/servers/{server_id}/release/confirm')
async def confirm_release(server_id:str,request:gpu_release.ReleaseConfirm,response:Response,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    response.headers['Cache-Control']='no-store'
    try:return await gpu_release.confirm(db,user,server_id,request)
    finally:
        if server_id=='default':
            await asyncio.to_thread(monitor.invalidate)


@router.get('')
async def gpu_resources(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await asyncio.to_thread(monitor.snapshot)


@router.get('/servers', response_model=list[GpuServerResponse])
async def list_servers(response: Response, db: AsyncSession = Depends(get_db_session),
                       user: User = Depends(get_current_superuser)):
    response.headers['Cache-Control'] = 'no-store'
    return await gpu_servers.list_servers(db, user.tenant_id)


@router.post('/servers', response_model=GpuServerResponse, status_code=201)
async def create_server(request: GpuServerCreate, response: Response,
                        db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_superuser)):
    response.headers['Cache-Control'] = 'no-store'
    return await gpu_servers.create_server(db, user.tenant_id, request)


@router.patch('/servers/{server_id}', response_model=GpuServerResponse)
async def update_server(server_id: UUID, request: GpuServerUpdate, response: Response,
                        db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_superuser)):
    response.headers['Cache-Control'] = 'no-store'
    return await gpu_servers.update_server(db, server_id, user.tenant_id, request)


@router.delete('/servers/{server_id}', status_code=204)
async def delete_server(server_id: UUID, db: AsyncSession = Depends(get_db_session),
                        user: User = Depends(get_current_superuser)):
    await gpu_servers.delete_server(db, server_id, user.tenant_id)


@router.post('/servers/test')
async def test_server(request: GpuServerTest, response: Response,
                      db: AsyncSession = Depends(get_db_session)):
    response.headers['Cache-Control'] = 'no-store'
    await db.rollback()
    return await asyncio.to_thread(gpu_servers.collect_snapshot, 'test-' + str(uuid4()), 0, request.name,
        dict(host=request.host, port=request.port, username=request.username,
             password=request.password.get_secret_value()))


@router.get('/servers/{server_id}/resources')
async def server_resources(server_id: UUID, response: Response, db: AsyncSession = Depends(get_db_session),
                           user: User = Depends(get_current_superuser)):
    response.headers['Cache-Control'] = 'no-store'
    tenant_id = user.tenant_id
    row = await gpu_servers.scoped_server(db, server_id, tenant_id)
    if not row.enabled:
        raise HTTPException(409, 'GPU服务器已停用。')
    revision, name, encrypted = row.revision, row.name, row.password_encrypted
    connection = dict(host=row.host, port=row.port, username=row.username)
    await db.rollback()
    connection['password'] = gpu_servers.decrypt_password(encrypted)
    snapshot = await asyncio.to_thread(gpu_servers.collect_snapshot, str(server_id), revision, name, connection)
    current = await gpu_servers.scoped_server(db, server_id, tenant_id)
    if not current.enabled or current.revision != revision:
        raise HTTPException(409, 'GPU服务器配置已更改，请重新采集。')
    return snapshot
