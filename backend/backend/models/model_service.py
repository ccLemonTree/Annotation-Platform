"""Configured inference endpoints, separate from trained model artifacts."""
import uuid
from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ModelService(Base):
    __tablename__ = 'model_services'
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), nullable=False)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))
    protocol: Mapped[str] = mapped_column(String(30))
    base_url: Mapped[str] = mapped_column(String(500))
    endpoint: Mapped[str] = mapped_column(String(200))
    model_name: Mapped[str] = mapped_column(String(200), default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    confidence: Mapped[float] = mapped_column(Float, default=.25)
    iou: Mapped[float] = mapped_column(Float, default=.45)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    label_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    api_key_env: Mapped[str] = mapped_column(String(100), default='')
    chat_prompts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default='{}')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('tenant_id', 'key', name='uq_model_service_tenant_key'),)
