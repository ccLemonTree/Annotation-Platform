"""
数据库模型定义
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, ForeignKey, JSON, Enum as SQLEnum, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from database import Base
import uuid
import enum

class TenantStatus(str, enum.Enum):
    """租户状态"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"

class ProjectType(str, enum.Enum):
    """项目类型"""
    DETECTION = "detection"
    CLASSIFICATION = "classification"
    SEGMENTATION = "segmentation"

class AssetStatus(str, enum.Enum):
    """资源状态"""
    PENDING = "pending"
    ANNOTATED = "annotated"
    REVIEWED = "reviewed"

class TaskStatus(str, enum.Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Tenant(Base):
    """租户表"""
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True, index=True)
    contact_email = Column(String(255))
    status = Column(SQLEnum(TenantStatus), default=TenantStatus.ACTIVE, nullable=False)
    settings = Column(JSON, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('idx_tenant_name_status', 'name', 'status'),
    )

class Project(Base):
    """项目表"""
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    project_type = Column(SQLEnum(ProjectType), nullable=False)
    labels = Column(JSON, default=[])
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('idx_project_tenant_type', 'tenant_id', 'project_type'),
    )

class Asset(Base):
    """资源表"""
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_size = Column(Integer)
    mime_type = Column(String(100))
    status = Column(SQLEnum(AssetStatus), default=AssetStatus.PENDING, nullable=False, index=True)
    metadata = Column(JSON, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('idx_asset_project_status', 'project_id', 'status'),
    )

class Annotation(Base):
    """标注表"""
    __tablename__ = "annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey('assets.id', ondelete='CASCADE'), nullable=False, index=True)
    label = Column(String(100), nullable=False, index=True)
    coordinates = Column(JSON)
    confidence = Column(Integer)
    annotator = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('idx_annotation_asset_label', 'asset_id', 'label'),
    )

class TrainingTask(Base):
    """训练任务表"""
    __tablename__ = "training_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    config = Column(JSON, nullable=False)
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.PENDING, nullable=False, index=True)
    progress = Column(Integer, default=0)
    result = Column(JSON)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('idx_training_project_status', 'project_id', 'status'),
    )

class Model(Base):
    """模型表"""
    __tablename__ = "models"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    training_task_id = Column(UUID(as_uuid=True), ForeignKey('training_tasks.id', ondelete='SET NULL'))
    name = Column(String(200), nullable=False)
    version = Column(String(50), nullable=False)
    model_path = Column(String(500), nullable=False)
    metrics = Column(JSON)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('idx_model_project_active', 'project_id', 'is_active'),
    )
