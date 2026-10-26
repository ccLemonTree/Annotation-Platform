"""
数据库模型：SmartEye下载记录（零拷贝源）
"""

from datetime import datetime
from sqlalchemy import String, DateTime, func, BigInteger, Text, Index, Integer, Enum
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
import enum

from .base import Base


class TaskStatus(str, enum.Enum):
    """任务状态"""
    PENDING = "pending"  # 待执行
    RUNNING = "running"  # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败


class SmarteyeTask(Base):
    """SmartEye下载任务表"""

    __tablename__ = "smarteye_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="租户ID")

    # 任务信息
    task_name: Mapped[str] = mapped_column(String(200), nullable=False, comment="任务名称")
    platform: Mapped[str] = mapped_column(String(50), nullable=False, comment="平台名称")
    keyword: Mapped[str] = mapped_column(String(500), nullable=False, comment="搜索关键词")
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="目标下载数")

    # 任务状态
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING, comment="任务状态")
    downloaded_count: Mapped[int] = mapped_column(Integer, default=0, comment="已下载数")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, comment="失败数")

    # 错误信息
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="错误信息")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="开始时间")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="完成时间")

    # 索引
    __table_args__ = (
        Index('idx_smarteye_task_tenant', 'tenant_id'),
        Index('idx_smarteye_task_status', 'status'),
        Index('idx_smarteye_task_platform', 'platform'),
        Index('idx_smarteye_task_created', 'created_at'),
    )

    def __repr__(self):
        return f"<SmarteeyeTask(id={self.id}, name={self.task_name}, status={self.status})>"


class SmarteeyeDownload(Base):
    """SmartEye下载记录表（零拷贝数据源）"""

    __tablename__ = "smarteye_downloads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="任务ID")

    # 存储信息（零拷贝关键字段）
    blob_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True, comment="存储路径")
    content_hash: Mapped[str] = mapped_column(String(100), nullable=False, comment="内容哈希")

    # 文件元数据
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="文件名")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="文件大小")
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, comment="MIME类型")

    # 图像信息
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="图片宽度")
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="图片高度")

    # 来源信息
    source_url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始URL")
    platform: Mapped[str] = mapped_column(String(50), nullable=False, comment="平台名称")

    # 引用计数（零拷贝追踪）
    reference_count: Mapped[int] = mapped_column(Integer, default=0, comment="被引用次数")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")

    # 索引
    __table_args__ = (
        Index('idx_smarteye_download_task', 'task_id'),
        Index('idx_smarteye_download_blob', 'blob_path'),  # 零拷贝查询关键
        Index('idx_smarteye_download_hash', 'content_hash'),
        Index('idx_smarteye_download_platform', 'platform'),
        Index('idx_smarteye_download_created', 'created_at'),
    )

    def __repr__(self):
        return f"<SmarteeyeDownload(id={self.id}, name={self.file_name}, refs={self.reference_count})>"
