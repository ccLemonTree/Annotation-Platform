"""Project preprocessing and query-specific matching labels."""
import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, String, Integer, DateTime, func, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ProjectWorkJob(Base):
    __tablename__ = 'project_work_jobs'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    operation: Mapped[str] = mapped_column(String(20))
    model: Mapped[dict] = mapped_column(JSONB)
    options: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProjectWorkItem(Base):
    __tablename__ = 'project_work_items'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project_work_jobs.id', ondelete='CASCADE'), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('assets.id', ondelete='SET NULL'))
    content_hash: Mapped[str] = mapped_column(String(100))
    base_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint('job_id','asset_id',name='uq_project_work_asset'),
        Index('ix_project_work_claim','status','job_id','id'))


class MatchQuery(Base):
    __tablename__ = 'match_queries'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    query: Mapped[dict] = mapped_column(JSONB)
    space: Mapped[dict] = mapped_column(JSONB)
    hits: Mapped[list] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
