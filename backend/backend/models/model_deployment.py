"""Distribution audit records are separate from training GPU reservations."""
import uuid
from datetime import datetime
from sqlalchemy import String,Integer,DateTime,ForeignKey,Text,func,Index,text,UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base


class ModelRelease(Base):
    __tablename__='model_releases'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'))
    user_id:Mapped[int]=mapped_column(Integer)
    run_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('training_runs.id',ondelete='RESTRICT'))
    name:Mapped[str]=mapped_column(String(100))
    version:Mapped[str]=mapped_column(String(100))
    runtime:Mapped[str]=mapped_column(String(30))
    status:Mapped[str]=mapped_column(String(30),default='archiving')
    source:Mapped[dict]=mapped_column(JSONB)
    archive:Mapped[dict]=mapped_column(JSONB,default=dict)
    logs:Mapped[list]=mapped_column(JSONB,default=list)
    error:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
    __table_args__=(UniqueConstraint('tenant_id','name','version',name='uq_model_release_version'),)


class DeploymentTarget(Base):
    __tablename__='deployment_targets'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'))
    name:Mapped[str]=mapped_column(String(100))
    kind:Mapped[str]=mapped_column(String(10))
    target_key:Mapped[str]=mapped_column(String(64))
    target:Mapped[dict]=mapped_column(JSONB)
    observation:Mapped[dict]=mapped_column(JSONB,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    __table_args__=(UniqueConstraint('tenant_id','kind','target_key',name='uq_deployment_target'),)


class DeploymentLease(Base):
    __tablename__='deployment_leases'
    resource:Mapped[str]=mapped_column(String(64),primary_key=True)
    job_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),index=True)


class ModelDeployment(Base):
    __tablename__='model_deployments'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'))
    user_id:Mapped[int]=mapped_column(Integer)
    run_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('training_runs.id',ondelete='RESTRICT'),nullable=True)
    release_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('model_releases.id',ondelete='RESTRICT'),nullable=True)
    target_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('deployment_targets.id',ondelete='RESTRICT'),nullable=True)
    batch_id:Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),nullable=True,index=True)
    kind:Mapped[str]=mapped_column(String(10),default='model',server_default='model')
    status:Mapped[str]=mapped_column(String(30),default='draft')
    target_key:Mapped[str]=mapped_column(String(64))
    source:Mapped[dict]=mapped_column(JSONB)
    target:Mapped[dict]=mapped_column(JSONB)
    inspection:Mapped[dict]=mapped_column(JSONB,default=dict)
    logs:Mapped[list]=mapped_column(JSONB,default=list)
    error:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
    __table_args__=(Index('idx_model_deployment_tenant_run','tenant_id','run_id'),
        Index('uq_model_deployment_busy_target','target_key',unique=True,
            postgresql_where=text("status IN ('transferring','activating','rolling_back','unknown')"),
            sqlite_where=text("status IN ('transferring','activating','rolling_back','unknown')")))
