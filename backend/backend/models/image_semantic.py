"""Durable semantic work and full vectors; registered explicitly by integration."""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, DateTime, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ImageSemanticJob(Base):
    __tablename__ = 'image_semantic_jobs'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), nullable=False, index=True)
    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    caption_model: Mapped[dict | None] = mapped_column(JSONB)
    embedding_model: Mapped[dict | None] = mapped_column(JSONB)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    preprocessing: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImageSemanticItem(Base):
    __tablename__ = 'image_semantic_items'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('image_semantic_jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('images.id', ondelete='SET NULL'))
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    caption: Mapped[str | None] = mapped_column(String(20000))
    error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint('job_id', 'image_id', name='uq_semantic_job_image'),
                      Index('ix_semantic_item_claim', 'status', 'job_id', 'id'))


class ImageSemanticVector(Base):
    __tablename__ = 'image_semantic_vectors'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), nullable=False)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('images.id', ondelete='CASCADE'), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('model_services.id', ondelete='CASCADE'), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    preprocessing: Mapped[str] = mapped_column(String(160), nullable=False)
    vector: Mapped[list] = mapped_column(JSONB, nullable=False)
    caption: Mapped[str | None] = mapped_column(String(20000))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint('image_id', 'model_id', 'revision', 'dimensions', 'preprocessing', name='uq_semantic_vector_space'),
                      Index('ix_semantic_vector_space', 'tenant_id', 'model_id', 'revision', 'dimensions', 'preprocessing'))
