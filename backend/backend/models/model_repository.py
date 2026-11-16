"""Immutable, tenant-owned model files independent of deployment targets."""
import uuid
from datetime import datetime
from sqlalchemy import String,Integer,BigInteger,DateTime,ForeignKey,Text,UniqueConstraint,func
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base


class ModelRepositoryItem(Base):
    __tablename__='model_repository_items'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),index=True)
    user_id:Mapped[int]=mapped_column(Integer)
    run_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('training_runs.id',ondelete='RESTRICT'),nullable=True)
    name:Mapped[str]=mapped_column(String(100))
    group_name:Mapped[str]=mapped_column(String(100),default='',server_default='')
    version:Mapped[str]=mapped_column(String(100))
    description:Mapped[str]=mapped_column(Text,default='',server_default='')
    format:Mapped[str]=mapped_column(String(20))
    gpu_model:Mapped[str]=mapped_column(String(200),default='')
    tensorrt_version:Mapped[str]=mapped_column(String(100),default='')
    batch_profile:Mapped[str]=mapped_column(String(64),default='',server_default='')
    entry_file:Mapped[str]=mapped_column(String(500))
    file_size:Mapped[int]=mapped_column(BigInteger,default=0)
    files:Mapped[list]=mapped_column(JSONB,default=list)
    source:Mapped[dict]=mapped_column(JSONB,default=dict)
    status:Mapped[str]=mapped_column(String(20),default='importing')
    error:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
    __table_args__=(UniqueConstraint('tenant_id','name','version','format','gpu_model','batch_profile',name='uq_repository_model_variant'),)
