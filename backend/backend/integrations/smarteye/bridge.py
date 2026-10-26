"""All legacy SmartEye routes run behind the main FastAPI identity checks."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.middleware.wsgi import WSGIMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user
from api.asset_routes import scoped_project
from database import get_db_session
from models import User
from integrations.smarteye.runtime import actor

router = APIRouter()

class SmartEyeResponse(Response):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    async def __call__(self, scope, receive, send):
        from integrations.smarteye.app import app
        from integrations.smarteye.hooks import install_hooks
        install_hooks(app)
        token = actor.set(self.owner)
        try:
            scope = {**scope, 'root_path': scope.get('root_path', '') + '/api/smarteye'}
            await WSGIMiddleware(app)(scope, receive, send)
        finally:
            actor.reset(token)

async def dispatch(path: str, request: Request, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db_session)):
    from config.settings import settings
    if user.tenant_id != settings.DEFAULT_TENANT_ID:
        raise HTTPException(403, 'SmartEye第一版仅对主租户开放')
    from services.image_ingest import get_config
    minimum=request.headers.get('X-SmartEye-Min-Long-Edge')
    if minimum is not None:
        try:minimum=int(minimum)
        except ValueError:raise HTTPException(400,'分辨率阈值须为整数')
        if not 0<=minimum<=32768:raise HTTPException(400,'分辨率阈值须为0至32768')
    owner = {'tenant_id': user.tenant_id, 'user_id': user.id}
    if minimum is not None:owner['min_long_edge']=minimum
    collection=request.headers.get('X-SmartEye-Collection')
    if collection:
        try:collection_id=UUID(collection)
        except ValueError:raise HTTPException(400,'请选择有效图片集')
        await get_config(db,user.tenant_id,collection_id)
        owner['collection_id']=str(collection_id)
    purpose = request.headers.get('X-SmartEye-Purpose', 'download')
    if purpose not in ('download', 'annotation'):
        raise HTTPException(400, '无效用途')
    if purpose == 'annotation':
        try:
            project_id = UUID(request.headers.get('X-SmartEye-Project', ''))
        except ValueError:
            raise HTTPException(400, '请选择有效标注项目')
        await scoped_project(db, project_id, user.tenant_id)
        owner['project_id'] = str(project_id)
    return SmartEyeResponse(owner)

for method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'):
    router.add_api_route('/{path:path}', dispatch, methods=[method])
