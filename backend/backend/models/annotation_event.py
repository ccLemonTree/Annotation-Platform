"""Business evidence and immutable export references; originals remain shared."""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class AnnotationEvent(Base):
    __tablename__ = 'annotation_events'
    __table_args__ = (
        Index('ix_ann_events_project_created', 'project_id', text('created_at DESC'), text('id DESC')),
        Index('ix_ann_events_ai_pending', 'project_id', 'asset_id', text("((detail->>'version')::int)"),
              postgresql_where=text("action = 'ai_review' AND detail->>'decision' = 'needs_review'"))
        .ddl_if(dialect='postgresql'),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class ExportSnapshot(Base):
    __tablename__ = 'export_snapshots'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    export_format: Mapped[str] = mapped_column(String(20))
    blob_path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    manifest_path: Mapped[str | None] = mapped_column(String(500))
    list_metadata: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class ExportSnapshotJob(Base):
    __tablename__ = 'export_snapshot_jobs'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    request: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    packed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
