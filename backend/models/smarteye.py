"""
SmartEye相关数据模型
"""

from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime

from .base import Base, UUIDMixin, TimestampMixin


class Platform(Base, UUIDMixin, TimestampMixin):
    """平台配置表"""
    __tablename__ = "platforms"

    platform_name = Column(String(100), unique=True, nullable=False)
    plat_id = Column(String(100), unique=True, nullable=False)
    api_endpoint = Column(String(500))
    description = Column(Text)
    enabled = Column(Boolean, default=True)
    auth_config = Column(JSONB)  # 认证配置（加密存储）

    # 关系
    alarm_types = relationship("AlarmType", back_populates="platform", cascade="all, delete-orphan")
    smarteye_tasks = relationship("SmartEyeTask", back_populates="platform")


class AlarmType(Base, UUIDMixin):
    """告警类型表"""
    __tablename__ = "alarm_types"

    platform_id = Column(UUID(as_uuid=True), ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    alarm_name = Column(String(100), nullable=False)
    alarm_code = Column(String(100), nullable=False)
    description = Column(Text)
    color = Column(String(20))
    enabled = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")

    # 关系
    platform = relationship("Platform", back_populates="alarm_types")


class SmartEyeTask(Base, UUIDMixin, TimestampMixin):
    """SmartEye下载任务表"""
    __tablename__ = "smarteye_tasks"

    task_name = Column(String(200))
    platform_id = Column(UUID(as_uuid=True), ForeignKey("platforms.id", ondelete="SET NULL"))

    # 查询条件（JSONB存储）
    query_params = Column(JSONB, nullable=False)

    # 任务状态
    status = Column(String(50), default="pending", nullable=False)  # pending/running/completed/failed/cancelled
    progress = Column(Integer, default=0)

    # 统计信息
    total_count = Column(Integer, default=0)
    downloaded_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)

    # 存储位置
    storage_prefix = Column(String(500))

    # 时间戳
    started_at = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))

    # 错误信息
    error_message = Column(Text)

    # 关系
    platform = relationship("Platform", back_populates="smarteye_tasks")
    downloads = relationship("SmartEyeDownload", back_populates="task", cascade="all, delete-orphan")


class SmartEyeDownload(Base, UUIDMixin):
    """SmartEye下载记录表"""
    __tablename__ = "smarteye_downloads"

    task_id = Column(UUID(as_uuid=True), ForeignKey("smarteye_tasks.id", ondelete="CASCADE"), nullable=False)

    # 原始数据
    source_id = Column(String(200), nullable=False)  # 平台原始记录ID
    platform = Column(String(100))
    device_name = Column(String(200))
    device_id = Column(String(200))
    target_type = Column(String(100))  # 告警类型/目标类型
    timestamp = Column(TIMESTAMP(timezone=True))

    # 存储信息 ⭐ 核心字段
    blob_path = Column(String(500), nullable=False)  # 实际存储路径
    content_hash = Column(String(64), nullable=False)  # SHA256哈希
    file_size = Column(Integer)
    original_url = Column(Text)

    # 图片元数据
    width = Column(Integer)
    height = Column(Integer)
    format = Column(String(20))

    # 下载状态
    download_status = Column(String(50), default="pending")  # pending/completed/failed
    error_message = Column(Text)

    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")

    # 关系
    task = relationship("SmartEyeTask", back_populates="downloads")
