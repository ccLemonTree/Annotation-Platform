"""Persist short, retry-safe activity intervals; all timestamps are UTC."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WorkActivityInterval(Base):
    __tablename__ = 'work_activity_intervals'

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'))
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('assets.id', ondelete='CASCADE'))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'))
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    activity: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'session_id', 'sequence', name='uq_work_activity_heartbeat'),
        CheckConstraint('sequence > 0', name='ck_work_activity_sequence'),
        CheckConstraint("activity IN ('annotation', 'review')", name='ck_work_activity_kind'),
        CheckConstraint('ended_at > started_at', name='ck_work_activity_time'),
        Index('idx_work_activity_project_user_time', 'project_id', 'user_id', 'started_at', 'ended_at'),
    )
