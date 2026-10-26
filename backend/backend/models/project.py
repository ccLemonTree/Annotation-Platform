"""
数据库模型：项目表
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func, BigInteger, ForeignKey, Text, Index, Enum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List
import enum
import uuid

from .base import Base


class TaskType(str, enum.Enum):
    """任务类型枚举"""
    DETECTION = "detection"  # 目标检测
    CLASSIFICATION = "classification"  # 图像分类
    SEGMENTATION = "segmentation"  # 语义分割
    CAPTION = "caption"
    VECTOR = "vector"


class ProjectStatus(str, enum.Enum):
    """项目状态枚举"""
    DRAFT = "draft"  # 草稿
    ACTIVE = "active"  # 进行中
    COMPLETED = "completed"  # 已完成
    ARCHIVED = "archived"  # 已归档


class Project(Base):
    """项目表"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, comment="租户ID")
    project_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="项目名称")

    # 任务信息
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, values_callable=lambda x: [e.value for e in x]), nullable=False, comment="任务类型")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="项目描述")

    # 标签配置
    label_schema: Mapped[Optional[dict]] = mapped_column(type_=JSONB, nullable=True, comment="标签模板(JSON)")

    # 状态
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, values_callable=lambda x: [e.value for e in x]),
        default=ProjectStatus.ACTIVE,
        comment="项目状态"
    )

    # 统计信息
    total_assets: Mapped[int] = mapped_column(Integer, default=0, comment="总资源数")
    annotated_count: Mapped[int] = mapped_column(Integer, default=0, comment="已标注数")
    reviewed_count: Mapped[int] = mapped_column(Integer, default=0, comment="已审核数")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关联关系
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="projects")
    assets: Mapped[List["Asset"]] = relationship("Asset", back_populates="project", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        Index('idx_project_tenant', 'tenant_id'),
        Index('idx_project_status', 'status'),
        Index('idx_project_task_type', 'task_type'),
        Index('idx_project_created', 'created_at'),
    )

    def __repr__(self):
        return f"<Project(id={self.id}, name={self.project_name}, type={self.task_type})>"
