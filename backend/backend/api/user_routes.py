"""
用户管理API路由
用户的增删改查
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from typing import List

from database import get_db_session as get_db
from models.user import User
from schemas.user import UserResponse, UserUpdate
from schemas.common import PaginatedResponse, PaginationParams
from api.deps import get_current_user, get_current_superuser, get_tenant_id
from services.auth import get_password_hash


router = APIRouter()


@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    pagination: PaginationParams = Depends(),
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取用户列表（分页）

    - 仅返回当前租户下的用户
    - 支持分页参数
    """
    # 查询总数
    count_stmt = select(func.count(User.id)).where(User.tenant_id == tenant_id)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar()

    # 查询用户列表
    stmt = (
        select(User)
        .where(User.tenant_id == tenant_id)
        .offset(pagination.skip)
        .limit(pagination.limit)
        .order_by(User.created_at.desc())
    )
    result = await db.execute(stmt)
    users = result.scalars().all()

    return PaginatedResponse(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        items=users
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取指定用户信息

    - 只能查看本租户下的用户
    """
    stmt = select(User).where(
        User.id == user_id,
        User.tenant_id == tenant_id
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    return user


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新用户信息

    - 只能更新本租户下的用户
    - 普通用户只能更新自己的信息
    - 超级管理员可以更新所有用户
    """
    # 查询用户
    stmt = select(User).where(
        User.id == user_id,
        User.tenant_id == tenant_id
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    # 权限检查：只能更新自己或管理员权限
    if user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足"
        )

    # 更新用户信息
    update_data = user_data.model_dump(exclude_unset=True)
    if update_data:
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(**update_data)
        )
        await db.execute(stmt)
        await db.commit()
        await db.refresh(user)

    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_superuser)
):
    """
    删除用户

    - 仅超级管理员可用
    - 只能删除本租户下的用户
    - 不能删除自己
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己"
        )

    # 查询用户
    stmt = select(User).where(
        User.id == user_id,
        User.tenant_id == tenant_id
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    # 删除用户
    stmt = delete(User).where(User.id == user_id)
    await db.execute(stmt)
    await db.commit()

    return None
