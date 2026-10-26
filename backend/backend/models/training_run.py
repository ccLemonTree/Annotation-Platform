"""Single-host Docker runs, separate from unused legacy Kubernetes task records."""
import uuid
from datetime import datetime
from sqlalchemy import String,Integer,DateTime,ForeignKey,Text,func,Index
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base


class TrainingRun(Base):
    __tablename__='training_runs'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'))
    user_id:Mapped[int]=mapped_column(Integer)
    name:Mapped[str]=mapped_column(String(200))
    kind:Mapped[str]=mapped_column(String(30))
    status:Mapped[str]=mapped_column(String(30),default='draft')
    config:Mapped[dict]=mapped_column(JSONB)
    source:Mapped[dict]=mapped_column(JSONB)
    server:Mapped[dict]=mapped_column(JSONB)
    output_dir:Mapped[str]=mapped_column(String(500))
    inspection:Mapped[dict]=mapped_column(JSONB,default=dict)
    error:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
    __table_args__=(Index('idx_training_run_tenant_created','tenant_id','created_at'),)


class TrainingGpuHold(Base):
    __tablename__='training_gpu_holds'
    gpu_uuid:Mapped[str]=mapped_column(String(100),primary_key=True)
    run_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('training_runs.id',ondelete='RESTRICT'),index=True)


class TrainingAnalysis(Base):
    __tablename__='training_analyses'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'))
    run_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('training_runs.id',ondelete='CASCADE'),index=True)
    user_id:Mapped[int]=mapped_column(Integer)
    evidence:Mapped[dict]=mapped_column(JSONB)
    model:Mapped[dict]=mapped_column(JSONB)
    prompt:Mapped[str]=mapped_column(Text)
    analysis:Mapped[dict]=mapped_column(JSONB)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
