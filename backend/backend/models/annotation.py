"""
数据库模型：标注表
"""

from datetime import datetime
from sqlalchemy import DDL, String, Integer, DateTime, event, func, BigInteger, ForeignKey, Text, Index, Boolean, text, or_
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from typing import Optional
import uuid

from .base import Base


def _asset_project_id(context):
    if context is None:
        return None
    params = context.get_current_parameters() or {}
    if params.get("project_id") is not None:
        return params["project_id"]
    asset_id = params.get("asset_id")
    conn = getattr(context, "connection", None)
    if asset_id is None or conn is None:
        return None
    from sqlalchemy import select
    from .asset import Asset
    return conn.scalar(select(Asset.project_id).where(Asset.id == asset_id))


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
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id',ondelete='CASCADE'),default=_asset_project_id)

    # 标注数据
    annotation_data: Mapped[dict] = mapped_column(type_=JSONB, nullable=False, comment="标注数据")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="版本号")
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否最新版本")
    annotation_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="标注来源")

    # 标注人信息
    annotator_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="标注人ID")

    # 审核信息
    review_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="审核状态")
    review_by_kind: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewer_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="审核人ID")
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="审核意见")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="审核时间")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    # 关联关系
    asset: Mapped["Asset"] = relationship("Asset", back_populates="annotations")
    _asset_project_id = staticmethod(_asset_project_id)

    @validates("asset")
    def _sync_asset_project_id(self, _key, asset):
        if asset is not None and getattr(asset, "project_id", None) is not None and self.project_id is None:
            self.project_id = asset.project_id
        return asset

    # 索引
    __table_args__ = (
        Index('uq_annotation_asset_version', 'asset_id', 'version', unique=True),
        Index('uq_annotation_latest', 'asset_id', unique=True,
              postgresql_where=text('is_latest = true'), sqlite_where=text('is_latest = 1')),
        Index('ix_annotations_project_id', 'project_id'),
        Index('idx_annotation_annotator', 'annotator_id'),
        Index('idx_annotation_reviewer', 'reviewer_id'),
        Index('idx_annotation_status', 'review_status'),
        Index('idx_annotation_created', 'created_at'),
        Index('ix_annotations_project_latest', 'project_id', text('created_at DESC'), text('version DESC'), 'id',
              postgresql_where=text('is_latest = true'), sqlite_where=text('is_latest = 1')),
        Index('ix_annotations_project_review', 'project_id', 'review_status', text('created_at DESC'), text('version DESC'), 'id',
              postgresql_where=text('is_latest = true'), sqlite_where=text('is_latest = 1')),
    )

    def __repr__(self):
        return f"<Annotation(id={self.id}, asset_id={self.asset_id}, version={self.version})>"

    def status_for(self, kind):
        return (self.review_by_kind or {}).get(kind, self.review_status)

    @classmethod
    def status_for_query(cls, kind):
        return func.coalesce(cls.review_by_kind[kind].as_string(), cls.review_status)

    @classmethod
    def has_kind(cls, kind):
        data=cls.annotation_data
        if kind=='detection':return data['boxes'].as_string().is_not(None)
        return or_(func.trim(data['description'].as_string())!='',data['attributes'].as_string().is_not(None),
            data['labels'].as_string().not_in(('','[]')),data['skipped'].as_boolean().is_(True))

    @classmethod
    def has_project_format(cls, task_type):
        if task_type=='detection':return cls.has_kind('detection')
        data=cls.annotation_data
        skipped=data['skipped'].as_boolean().is_(True)
        if task_type=='classification':return or_(data['labels'].as_string().not_in(('','[]')),skipped)
        return cls.has_kind('image') if task_type in ('caption','vector') else or_(
            func.trim(data['description'].as_string())!='',data['attributes'].as_string().is_not(None),skipped)

    @classmethod
    def ai_pending(cls):
        from .annotation_event import AnnotationEvent
        from sqlalchemy import select
        return (cls.status_for_query('detection')=='pending') & or_(
            cls.review_by_kind['ai_needs_review'].as_boolean().is_(True),
            select(AnnotationEvent.id).where(AnnotationEvent.project_id==cls.project_id,
                AnnotationEvent.asset_id==cls.asset_id,AnnotationEvent.action=='ai_review',
                AnnotationEvent.detail['version'].as_integer()==cls.version,
                AnnotationEvent.detail['decision'].as_string()=='needs_review').exists())


for _ddl in (
    "CREATE INDEX IF NOT EXISTS ix_annotations_project_review_detection ON annotations (project_id, COALESCE(review_by_kind->>'detection', review_status), created_at DESC, version DESC, id) WHERE is_latest = true",
    "CREATE INDEX IF NOT EXISTS ix_annotations_project_review_image ON annotations (project_id, COALESCE(review_by_kind->>'image', review_status), created_at DESC, version DESC, id) WHERE is_latest = true",
    "CREATE INDEX IF NOT EXISTS ix_annotations_data_gin ON annotations USING gin (annotation_data jsonb_path_ops) WHERE is_latest = true",
):
    event.listen(Annotation.__table__, "after_create", DDL(_ddl).execute_if(dialect="postgresql"))
