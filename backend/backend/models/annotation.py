"""
数据库模型：标注表
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func, BigInteger, ForeignKey, Text, Index, Boolean, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
import uuid

from .base import Base


class Annotation(Base):
    """标注表"""

    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        comment="资产ID"
    )

    # 标注数据
    annotation_data: Mapped[dict] = mapped_column(type_=JSONB, nullable=False, comment="标注数据")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="版本号")
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否最新版本")
    annotation_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="标注来源")

    # 标注人信息
    annotator_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="标注人ID")

    # 审核信息
    review_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="审核状态")
    reviewer_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="审核人ID")
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="审核意见")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="审核时间")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关联关系
    asset: Mapped["Asset"] = relationship("Asset", back_populates="annotations")

    # 索引
    __table_args__ = (
        Index('uq_annotation_asset_version', 'asset_id', 'version', unique=True),
        Index('uq_annotation_latest', 'asset_id', unique=True,
              postgresql_where=text('is_latest = true'), sqlite_where=text('is_latest = 1')),
        Index('idx_annotation_asset', 'asset_id'),
        Index('idx_annotation_annotator', 'annotator_id'),
        Index('idx_annotation_reviewer', 'reviewer_id'),
        Index('idx_annotation_status', 'review_status'),
        Index('idx_annotation_latest', 'is_latest'),
        Index('idx_annotation_created', 'created_at'),
    )

    def __repr__(self):
        return f"<Annotation(id={self.id}, asset_id={self.asset_id}, version={self.version})>"
