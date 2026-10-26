"""
数据库模型 - 基类
"""

from datetime import datetime
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()


class TimestampMixin:
    """时间戳Mixin"""
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UUIDMixin:
    """UUID主键Mixin"""
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
