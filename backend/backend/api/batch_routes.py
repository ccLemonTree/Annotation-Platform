"""
批量操作API路由
提供资产和标注的批量操作功能
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from typing import List
from uuid import UUID

from database import get_db_session as get_db
from models.user import User
from models.project import Project
from models.asset import Asset
from models.annotation import Annotation
from api.deps import get_current_user, get_tenant_id
from pydantic import BaseModel
from services.annotations import locked_annotation, review, remove


router = APIRouter()


async def scoped_annotation_ids(db, annotation_ids, tenant_id):
    result = await db.execute(select(Annotation.id).join(Asset).join(Project).where(
        Annotation.id.in_(annotation_ids), Project.tenant_id == tenant_id))
    ids = list(result.scalars().all())
    if not ids:
        raise HTTPException(status_code=404, detail="未找到可操作的标注")
    # Acquire every asset before counter updates, matching single-write lock order.
    await db.execute(select(Asset).join(Project).join(Annotation).where(
        Annotation.id.in_(ids), Project.tenant_id == tenant_id)
        .order_by(Asset.project_id, Asset.id).with_for_update(of=Asset))
    return ids


class BatchOperationResponse(BaseModel):
    """批量操作响应"""
    success: bool
    affected_count: int
    message: str


@router.post("/assets/update-status", response_model=BatchOperationResponse)
async def batch_update_asset_status(
    asset_ids: List[UUID],
    new_status: str = Query(..., description="新状态: pending/annotating/completed/reviewed"),
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    批量更新资产状态

    - **asset_ids**: 资产ID列表
    - **new_status**: 新的标注状态
    """
    if not asset_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="资产ID列表不能为空"
        )

    # 验证状态值
    valid_statuses = ["pending", "annotating", "completed", "reviewed"]
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的状态值，支持: {', '.join(valid_statuses)}"
        )

    # 验证资产属于当前租户
    stmt = (
        select(Asset.id)
        .join(Project)
        .where(
            Asset.id.in_(asset_ids),
            Project.tenant_id == tenant_id
        )
    )
    result = await db.execute(stmt)
    valid_asset_ids = [row[0] for row in result.all()]

    if not valid_asset_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到属于当前租户的资产"
        )

    assets = (await db.execute(select(Asset).where(Asset.id.in_(valid_asset_ids))
        .order_by(Asset.project_id, Asset.id).with_for_update(of=Asset))).scalars().all()
    for asset in assets:
        latest = await db.scalar(select(Annotation).where(Annotation.asset_id == asset.id, Annotation.is_latest.is_(True)))
        expected = ('reviewed' if latest.review_status == 'approved' else 'completed') if latest else None
        if (latest and new_status != expected) or (not latest and new_status in ('completed','reviewed')):
            raise HTTPException(409, '资产状态由最新标注和审核决定，请从标注工作台操作')

    # 批量更新状态
    stmt = (
        update(Asset)
        .where(Asset.id.in_(valid_asset_ids))
        .values(annotation_status=new_status)
    )
    result = await db.execute(stmt)
    await db.commit()

    return BatchOperationResponse(
        success=True,
        affected_count=len(valid_asset_ids),
        message=f"成功更新 {len(valid_asset_ids)} 个资产的状态为 {new_status}"
    )


@router.post("/assets/delete", response_model=BatchOperationResponse)
async def batch_delete_assets(
    asset_ids: List[UUID],
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    批量删除资产

    - 会级联删除相关的标注数据
    - 不删除物理文件（零拷贝架构）
    """
    if not asset_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="资产ID列表不能为空"
        )

    # 验证资产属于当前租户
    stmt = (
        select(Asset)
        .join(Project)
        .where(
            Asset.id.in_(asset_ids),
            Project.tenant_id == tenant_id
        )
        .order_by(Asset.project_id, Asset.id)
        .with_for_update(of=Asset)
    )
    result = await db.execute(stmt)
    assets = result.scalars().all()

    if not assets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到属于当前租户的资产"
        )

    # 批量删除
    deleted_count = 0
    for asset in assets:
        await db.delete(asset)
        await db.execute(update(Project).where(Project.id == asset.project_id).values(
            total_assets=Project.total_assets - 1,
            annotated_count=Project.annotated_count - int(asset.annotation_status in ('completed', 'reviewed')),
            reviewed_count=Project.reviewed_count - int(asset.annotation_status == 'reviewed')))
        deleted_count += 1

    await db.commit()

    return BatchOperationResponse(
        success=True,
        affected_count=deleted_count,
        message=f"成功删除 {deleted_count} 个资产"
    )


@router.post("/annotations/approve", response_model=BatchOperationResponse)
async def batch_approve_annotations(
    annotation_ids: List[UUID],
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_tenant_id),
    current_user: User = Depends(get_current_user)
):
    """
    批量审核通过标注

    - **annotation_ids**: 标注ID列表
    """
    if not annotation_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="标注ID列表不能为空"
        )

    annotation_ids = await scoped_annotation_ids(db, annotation_ids, tenant_id)

    annotation_ids = await scoped_annotation_ids(db, annotation_ids, tenant_id)
    for annotation_id in sorted(annotation_ids):
        annotation, asset = await locked_annotation(db, annotation_id, tenant_id)
        await review(db, annotation, asset, True, None, current_user.id)
    await db.commit()
    affected_count = len(annotation_ids)

    return BatchOperationResponse(
        success=True,
        affected_count=affected_count,
        message=f"成功审核通过 {affected_count} 个标注"
    )


@router.post("/annotations/reject", response_model=BatchOperationResponse)
async def batch_reject_annotations(
    annotation_ids: List[UUID],
    reason: str = Query(None, description="拒绝原因"),
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_tenant_id),
    current_user: User = Depends(get_current_user)
):
    """
    批量拒绝标注

    - **annotation_ids**: 标注ID列表
    - **reason**: 拒绝原因
    """
    if not annotation_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="标注ID列表不能为空"
        )

    annotation_ids = await scoped_annotation_ids(db, annotation_ids, tenant_id)
    for annotation_id in sorted(annotation_ids):
        annotation, asset = await locked_annotation(db, annotation_id, tenant_id)
        await review(db, annotation, asset, False, reason, current_user.id)
    await db.commit()
    affected_count = len(annotation_ids)

    return BatchOperationResponse(
        success=True,
        affected_count=affected_count,
        message=f"成功拒绝 {affected_count} 个标注"
    )


@router.post("/annotations/delete", response_model=BatchOperationResponse)
async def batch_delete_annotations(
    annotation_ids: List[UUID],
    db: AsyncSession = Depends(get_db),
    tenant_id: int = Depends(get_tenant_id),
    current_user: User = Depends(get_current_user)
):
    """
    批量删除标注

    - **annotation_ids**: 标注ID列表
    """
    if not annotation_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="标注ID列表不能为空"
        )

    annotation_ids = await scoped_annotation_ids(db, annotation_ids, tenant_id)

    for annotation_id in sorted(annotation_ids):
        annotation, asset = await locked_annotation(db, annotation_id, tenant_id)
        await remove(db, annotation, asset, current_user.id)
    await db.commit()
    deleted_count = len(annotation_ids)

    return BatchOperationResponse(
        success=True,
        affected_count=deleted_count,
        message=f"成功删除 {deleted_count} 个标注"
    )
