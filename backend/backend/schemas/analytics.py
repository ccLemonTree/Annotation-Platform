"""Analytics request boundaries. Datetimes on the wire must include a timezone."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('时间必须包含时区')
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class AnalyticsRange(BaseModel):
    start: datetime | None = None
    end: datetime | None = None

    @field_validator('start', 'end')
    @classmethod
    def aware_time(cls, value):
        return utc_naive(value) if value is not None else None

    @model_validator(mode='after')
    def ordered(self):
        if self.start and self.end and self.start >= self.end:
            raise ValueError('开始时间必须早于结束时间')
        return self


class ActivityHeartbeat(BaseModel):
    model_config = ConfigDict(extra='forbid')

    project_id: UUID
    asset_id: UUID
    session_id: UUID
    sequence: int = Field(gt=0, le=2147483647, strict=True)
    activity: Literal['annotation', 'review']
    started_at: datetime
    ended_at: datetime

    @field_validator('started_at', 'ended_at')
    @classmethod
    def aware_time(cls, value):
        return utc_naive(value)

    @model_validator(mode='after')
    def ordered(self):
        if self.started_at >= self.ended_at:
            raise ValueError('活跃区间结束必须晚于开始')
        return self


class ActivityReceipt(BaseModel):
    duplicate: bool
    accepted_seconds: float
