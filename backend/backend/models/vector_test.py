from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, false, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class VectorTestModel(Base):
    __tablename__ = 'vector_test_models'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('training_runs.id'))
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[dict] = mapped_column(JSONB)
    server: Mapped[dict] = mapped_column(JSONB)
    gpu: Mapped[str | None] = mapped_column(String(80), unique=True)
    port: Mapped[int]
    state: Mapped[str] = mapped_column(String(20), default='queued')
    error: Mapped[str | None] = mapped_column(Text)


class VectorTestIndex(Base):
    __tablename__ = 'vector_test_indexes'

    project_id: Mapped[UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    model_id: Mapped[UUID] = mapped_column(ForeignKey('vector_test_models.id'), primary_key=True)
    legacy_enabled: Mapped[bool] = mapped_column(server_default=true())
    isolated_enabled: Mapped[bool] = mapped_column(server_default=false())


class VectorTestItem(Base):
    __tablename__ = 'vector_test_items'
    __table_args__ = (ForeignKeyConstraint(
        ('project_id', 'model_id'), ('vector_test_indexes.project_id', 'vector_test_indexes.model_id'),
        ondelete='CASCADE'),)

    project_id: Mapped[UUID] = mapped_column(primary_key=True)
    model_id: Mapped[UUID] = mapped_column(primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey('assets.id', ondelete='CASCADE'), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class VectorTestDataset(Base):
    __tablename__ = 'vector_test_datasets'
    project_id: Mapped[UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    detection: Mapped[dict] = mapped_column(JSONB)


class VectorTestSample(Base):
    __tablename__ = 'vector_test_samples'
    __table_args__ = (UniqueConstraint('project_id', 'blob_path'),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey('vector_test_samples.id', ondelete='CASCADE'))
    blob_path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    original_name: Mapped[str] = mapped_column(String(255))
    width: Mapped[int]
    height: Mapped[int]
    format: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(30))
    device_name: Mapped[str | None] = mapped_column(String(255))
    detection_revision: Mapped[UUID | None]
    detection_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class VectorTestSampleItem(Base):
    __tablename__ = 'vector_test_sample_items'
    __table_args__ = (ForeignKeyConstraint(
        ('project_id', 'model_id'), ('vector_test_indexes.project_id', 'vector_test_indexes.model_id'),
        ondelete='CASCADE'),)
    project_id: Mapped[UUID] = mapped_column(primary_key=True)
    model_id: Mapped[UUID] = mapped_column(primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey('vector_test_samples.id', ondelete='CASCADE'), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
