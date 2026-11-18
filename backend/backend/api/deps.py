"""
API通用依赖注入
"""
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db_session as get_db
from models.user import User
from services.auth import decode_access_token
from schemas.user import TokenData


# OAuth2认证
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Exact route templates: new management endpoints stay administrator-only by default.
ANNOTATION_ROUTES = {
    'GET': {'/api/auth/me','/api/projects','/api/projects/{project_id}',
        '/api/projects/{project_id}/stats','/api/projects/{project_id}/annotation-models',
        '/api/projects/{project_id}/attributes','/api/projects/{project_id}/attribute-options','/api/projects/{project_id}/crop-labels',
        '/api/projects/{project_id}/used-labels',
        '/api/assets','/api/assets/selection','/api/assets/collections','/api/assets/{asset_id}',
        '/api/assets/{asset_id}/content','/api/assets/{asset_id}/neighbors','/api/assets/{asset_id}/source','/api/assets/{asset_id}/source/content',
        '/api/annotations','/api/annotations/{annotation_id}','/api/annotations/assets/{asset_id}/events',
        '/api/annotations/assets/{asset_id}/analysis-models',
        '/api/projects/{project_id}/matches','/api/projects/{project_id}/matches/smart-search/platforms',
        '/api/projects/{project_id}/matches/{query_id}/source/{asset_id}',
        '/api/images','/api/images/{image_id}/content'},
    'POST': {'/api/annotations','/api/annotations/{annotation_id}/review','/api/annotations/assets/{asset_id}/analyze',
        '/api/batch/annotations/approve','/api/batch/annotations/reject','/api/analytics/activity',
        '/api/projects/{project_id}/matches/search','/api/projects/{project_id}/matches/smart-search',
        '/api/projects/{project_id}/matches/{query_id}/review'},
    'PUT': {'/api/annotations/{annotation_id}','/api/projects/{project_id}/matches/{query_id}'},
}


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
    request: Request = None
) -> User:
    """
    获取当前登录用户

    Args:
        token: JWT令牌
        db: 数据库会话

    Returns:
        当前用户对象

    Raises:
        HTTPException: 认证失败
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 解码令牌
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user_id: int = payload.get("user_id")
    if user_id is None:
        raise credentials_exception

    # 查询用户
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )

    if request is not None:
        route=request.scope.get('route')
        if user.role!='admin' and getattr(route,'path',None) not in ANNOTATION_ROUTES.get(request.method,set()):
            raise HTTPException(status_code=403,detail='普通用户仅可操作已分配项目的人工标注和审核')
        # The database dependency creates a fresh session for each request; never cache identity globally.
        db.info['current_user']={'id':user.id,'tenant_id':user.tenant_id,'role':user.role}
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    获取当前活跃用户

    Args:
        current_user: 当前用户

    Returns:
        活跃用户对象

    Raises:
        HTTPException: 用户未激活
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户未激活"
        )
    return current_user


async def get_current_superuser(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    获取当前超级管理员

    Args:
        current_user: 当前用户

    Returns:
        超级管理员对象

    Raises:
        HTTPException: 权限不足
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足"
        )
    return current_user


def get_tenant_id(current_user: User = Depends(get_current_user)) -> int:
    """
    获取当前用户的租户ID

    Args:
        current_user: 当前用户

    Returns:
        租户ID
    """
    return current_user.tenant_id
