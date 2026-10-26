"""Platform image ingestion policy, independent of annotation projects."""
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base

class ImageIngestSettings(Base):
    __tablename__='image_ingest_settings'
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),primary_key=True)
    config:Mapped[dict]=mapped_column(JSONB,nullable=False)
    revision:Mapped[int]=mapped_column(Integer,nullable=False,default=1)
