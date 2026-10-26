"""
数据库模型：资源表（零拷贝核心）
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func, ForeignKey, Text, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
import uuid

from .base import Base


class Asset(Base):
    """资源表（核心：支持零拷贝引用）"""

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属项目ID"
    )
    image_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('images.id', ondelete='RESTRICT'), nullable=True, index=True)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="批次ID"
    )

    # 存储信息（零拷贝关键字段）
    blob_path: Mapped[str] = mapped_column(String(500), nullable=False, comment="存储路径")
    content_hash: Mapped[str] = mapped_column(String(100), nullable=False, comment="内容哈希(SHA256)")

    # 来源信息（零拷贝关联）
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="来源类型")
    source_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="关联的源记录ID"
    )

    # 文件元数据
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="文件大小(字节)")

    # 图像信息
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="图片宽度")
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="图片高度")
    format: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="图片格式")

    # 元数据
    capture_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="拍摄时间")
    device_info: Mapped[Optional[dict]] = mapped_column(type_=JSONB, nullable=True, comment="设备信息")
    location: Mapped[Optional[dict]] = mapped_column(type_=JSONB, nullable=True, comment="位置信息")

    # 质量评估
    quality_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="质量评分(0-100)")
    quality_metrics: Mapped[Optional[dict]] = mapped_column(type_=JSONB, nullable=True, comment="质量指标")

    # 标注状态
    annotation_status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
        comment="标注状态"
    )

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关联关系
    project: Mapped["Project"] = relationship("Project", back_populates="assets")
    annotations: Mapped[list["Annotation"]] = relationship("Annotation", back_populates="asset", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        UniqueConstraint("project_id", "content_hash", name="uq_asset_project_content"),
        Index('idx_asset_project', 'project_id'),
        Index('idx_asset_blob_path', 'blob_path'),  # 零拷贝查询关键
        Index('idx_asset_content_hash', 'content_hash'),  # 去重关键
        Index('idx_asset_source', 'source_type', 'source_record_id'),  # 零拷贝关联
        Index('idx_asset_status', 'annotation_status'),
        Index('idx_asset_created', 'created_at'),
    )

    def __repr__(self):
        return f"<Asset(id={self.id}, name={self.original_name}, source={self.source_type})>"
