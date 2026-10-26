"""
认证API路由
用户登录、注册、令牌刷新
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime

from database import get_db_session
from models import User
from schemas.user import UserLogin, Token, UserCreate, UserResponse
from services.auth import verify_password, get_password_hash, create_access_token
from api.deps import get_current_user
from config.settings import settings


router = APIRouter()


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db_session)
):
    """
    用户登录

    - **username**: 用户名
    - **password**: 密码

    返回JWT访问令牌
    """
    # 查询用户
    stmt = select(User).where(User.username == form_data.username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # 验证用户名和密码
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查用户状态
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )

    # 更新最后登录时间
    stmt = update(User).where(User.id == user.id).values(last_login_at=datetime.utcnow())
    await db.execute(stmt)
    await db.commit()

    # 生成JWT令牌
    access_token = create_access_token(
        data={
            "user_id": user.id,
            "username": user.username,
            "tenant_id": user.tenant_id
        }
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db_session)
):
    """
    用户注册

    - **username**: 用户名（唯一）
    - **email**: 邮箱（唯一）
    - **password**: 密码（至少6位）
    - **tenant_id**: 租户ID
    """
    if user_data.tenant_id is not None and user_data.tenant_id != settings.DEFAULT_TENANT_ID:
        raise HTTPException(status_code=400, detail="当前平台使用单租户，不能选择其他租户")

    # 检查用户名是否已存在
    stmt = select(User).where(User.username == user_data.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )

    # 检查邮箱是否已存在
    stmt = select(User).where(User.email == user_data.email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已存在"
        )

    # 创建用户
    db_user = User(
        tenant_id=settings.DEFAULT_TENANT_ID,
        username=user_data.username,
        email=user_data.email,
        full_name=user_data.full_name,
        password_hash=get_password_hash(user_data.password),
        role="user",
        is_active=True
    )

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)

    return db_user


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    获取当前登录用户信息

    需要在请求头中携带JWT令牌：
    Authorization: Bearer <token>
    """
    return current_user
