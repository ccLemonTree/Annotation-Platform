"""Project preprocessing and query-specific matching labels."""
import uuid
from datetime import datetime
from sqlalchemy import DDL, ForeignKey, String, Integer, DateTime, event, func, UniqueConstraint, Index, Boolean, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ProjectWorkJob(Base):
    __tablename__ = 'project_work_jobs'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    operation: Mapped[str] = mapped_column(String(20))
    follow_uploads: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    model: Mapped[dict] = mapped_column(JSONB)
    options: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (Index('uq_work_follow_uploads', 'project_id', unique=True,
        postgresql_where=text('follow_uploads'), sqlite_where=text('follow_uploads')),)


class ProjectWorkflowTemplate(Base):
    __tablename__ = 'project_workflow_templates'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'))
    name: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint('tenant_id','name',name='uq_workflow_template_tenant_name'),)


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
        Index('ix_project_work_items_asset_id','asset_id'),
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

    @classmethod
    def is_retrieval_query(cls):
        return cls.query['embedding_source'].as_string().is_(None)


for _table, _ddl in (
    (ProjectWorkJob.__table__, "CREATE INDEX IF NOT EXISTS ix_project_work_jobs_workflow ON project_work_jobs (project_id, (options->>'workflow_id'))"),
    (ProjectWorkJob.__table__, "CREATE INDEX IF NOT EXISTS ix_project_work_jobs_parent ON project_work_jobs (project_id, (options->>'parent_job_id'))"),
    (ProjectWorkItem.__table__, "CREATE INDEX IF NOT EXISTS ix_project_work_retry_wait ON project_work_items (((result->>'retry_at')::bigint), job_id, id) WHERE status = 'retry_wait'"),
):
    event.listen(_table, "after_create", DDL(_ddl).execute_if(dialect="postgresql"))
