"""Tenant-scoped quality and human activity endpoints. Parent mounts /api/analytics."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from database import get_db_session
from models.user import User
from schemas.analytics import ActivityHeartbeat, ActivityReceipt, AnalyticsRange
from services.annotation_analytics import annotation_analytics, record_activity

router = APIRouter()


@router.get('')
async def analytics(project_id: UUID | None = None, start: datetime | None = None, end: datetime | None = None,
                    db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    try:
        window = AnalyticsRange(start=start, end=end)
    except ValueError:
        raise HTTPException(422, '时间范围无效：请指定时区，且开始必须早于结束')
    return await annotation_analytics(db, user, project_id, window)


@router.post('/activity', response_model=ActivityReceipt)
async def activity(data: ActivityHeartbeat, db: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    result = await record_activity(db, user, data)
    await db.commit()
    return result
