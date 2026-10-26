"""
数据库模型：训练相关表
"""

from datetime import datetime
from sqlalchemy import String, DateTime, func, BigInteger, ForeignKey, Text, Index, Integer, Float, Enum, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List
import enum
import uuid
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


class TrainingStatus(str, enum.Enum):
    """训练状态"""
    PENDING = "pending"  # 待执行
    RUNNING = "running"  # 训练中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


class TrainingTask(Base):
    """训练任务表"""

    __tablename__ = "training_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联项目ID"
    )

    # 任务信息
    task_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务名称")
    framework: Mapped[str] = mapped_column(String(50), nullable=False, comment="训练框架: pytorch/tensorflow")
    model_type: Mapped[str] = mapped_column(String(100), nullable=False, comment="模型类型")

    # 训练配置
    config_yaml: Mapped[str] = mapped_column(Text, nullable=False, comment="训练配置(YAML)")
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False, comment="数据集版本")

    # 资源配置
    gpu_count: Mapped[int] = mapped_column(Integer, default=1, comment="GPU数量")
    gpu_type: Mapped[str] = mapped_column(String(50), default="T4", comment="GPU类型")

    # 任务状态
    status: Mapped[TrainingStatus] = mapped_column(Enum(TrainingStatus), default=TrainingStatus.PENDING, comment="任务状态")
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="训练进度(%)")

    # K8s信息
    k8s_job_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="K8s Job名称")
    k8s_namespace: Mapped[str] = mapped_column(String(100), default="training", comment="K8s命名空间")

    # 训练结果
    best_metric: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最佳指标")
    final_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最终损失")
    model_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="模型存储路径")

    # 错误信息
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="错误信息")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="开始时间")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="完成时间")

    # 关联关系
    project: Mapped["Project"] = relationship("Project")
    metrics: Mapped[List["TrainingMetric"]] = relationship("TrainingMetric", back_populates="task", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        Index('idx_training_task_project', 'project_id'),
        Index('idx_training_task_status', 'status'),
        Index('idx_training_task_created', 'created_at'),
    )

    def __repr__(self):
        return f"<TrainingTask(id={self.id}, name={self.task_name}, status={self.status})>"


class TrainingMetric(Base):
    """训练指标表（时序数据）"""

    __tablename__ = "training_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("training_tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="训练任务ID"
    )

    # 指标数据
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, comment="轮次")
    step: Mapped[int] = mapped_column(Integer, nullable=False, comment="步数")

    # 指标值
    loss: Mapped[float] = mapped_column(Float, nullable=False, comment="损失")
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="准确率")
    learning_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="学习率")

    # 额外指标（JSON）
    extra_metrics: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="额外指标(JSON)")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="记录时间")

    # 关联关系
    task: Mapped["TrainingTask"] = relationship("TrainingTask", back_populates="metrics")

    # 索引
    __table_args__ = (
        Index('idx_training_metric_task', 'task_id'),
        Index('idx_training_metric_epoch', 'task_id', 'epoch'),
        Index('idx_training_metric_created', 'created_at'),
    )

    def __repr__(self):
        return f"<TrainingMetric(task_id={self.task_id}, epoch={self.epoch}, loss={self.loss})>"


class Model(Base):
    """模型表"""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联项目ID"
    )
    training_task_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("training_tasks.id", ondelete="SET NULL"),
        nullable=True,
        comment="训练任务ID"
    )

    # 模型信息
    model_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="模型名称")
    model_version: Mapped[str] = mapped_column(String(50), nullable=False, comment="模型版本")
    model_type: Mapped[str] = mapped_column(String(100), nullable=False, comment="模型类型")
    framework: Mapped[str] = mapped_column(String(50), nullable=False, comment="训练框架")

    # 存储路径
    model_path: Mapped[str] = mapped_column(String(500), nullable=False, comment="模型存储路径")
    model_size: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="模型大小(字节)")

    # 性能指标
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="准确率")
    f1_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="F1分数")
    inference_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="推理时间(ms)")

    # 部署状态
    is_deployed: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已部署")
    deployment_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="部署地址")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关联关系
    project: Mapped["Project"] = relationship("Project")

    # 索引
    __table_args__ = (
        Index('idx_model_project', 'project_id'),
        Index('idx_model_training_task', 'training_task_id'),
        Index('idx_model_deployed', 'is_deployed'),
        Index('idx_model_created', 'created_at'),
    )

    def __repr__(self):
        return f"<Model(id={self.id}, name={self.model_name}, version={self.model_version})>"
