"""Independent originals; project assets are annotation-use references."""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint, Index, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class LibraryImage(Base):
    __tablename__ = 'images'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), nullable=False)
    blob_path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(String(50))
    source_type: Mapped[str] = mapped_column(String(50))
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    batch_id: Mapped[str | None] = mapped_column(String(160))
    capture_time: Mapped[datetime | None] = mapped_column(DateTime)
    device_info: Mapped[dict | None] = mapped_column(JSONB)
    location: Mapped[dict | None] = mapped_column(JSONB)
    quality_score: Mapped[int | None] = mapped_column(Integer)
    quality_metrics: Mapped[dict | None] = mapped_column(JSONB)
    imported_annotation: Mapped[dict | None] = mapped_column(JSONB,nullable=True)
    scene_labels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'"))
    scene_status: Mapped[str] = mapped_column(String(30), nullable=False, default='unclassified', server_default='unclassified')
    scene_error: Mapped[str | None] = mapped_column(String(500))
    scene_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    scene_metadata: Mapped[dict | None] = mapped_column(JSONB)
    scene_config: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint('tenant_id','content_hash',name='uq_image_tenant_content'),
        Index('idx_image_source_batch','tenant_id','source_type','batch_id'),
        Index('idx_image_scene_status','scene_status','created_at'),
        Index('idx_image_created','tenant_id','created_at'))
