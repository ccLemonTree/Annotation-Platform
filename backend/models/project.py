"""
标注项目相关数据模型
"""

from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, Text, TIMESTAMP, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship
import enum

from .base import Base, UUIDMixin, TimestampMixin


class TaskType(str, enum.Enum):
    """任务类型枚举"""
    DETECTION = "detection"  # 目标检测
    CLASSIFICATION = "classification"  # 图像分类
    SEGMENTATION = "segmentation"  # 语义分割


class ProjectStatus(str, enum.Enum):
    """项目状态枚举"""
    DRAFT = "draft"  # 草稿
    ACTIVE = "active"  # 进行中
    COMPLETED = "completed"  # 已完成
    ARCHIVED = "archived"  # 已归档


class Project(Base, UUIDMixin, TimestampMixin):
    """标注项目表"""
    __tablename__ = "projects"

    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    project_name = Column(String(200), nullable=False)
    description = Column(Text)

    # 任务配置
    task_type = Column(SQLEnum(TaskType), nullable=False)
    label_schema = Column(JSONB)  # 标签配置

    # 状态
    status = Column(SQLEnum(ProjectStatus), default=ProjectStatus.DRAFT)

    # 统计
    total_assets = Column(Integer, default=0)
    annotated_count = Column(Integer, default=0)
    reviewed_count = Column(Integer, default=0)

    # 关系
    tenant = relationship("Tenant", back_populates="projects")
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    datasets = relationship("Dataset", back_populates="project", cascade="all, delete-orphan")


class ImportBatch(Base, UUIDMixin, TimestampMixin):
    """导入批次表"""
    __tablename__ = "import_batches"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))

    # 来源信息
    source_type = Column(String(50), nullable=False)  # smarteye/upload/api
    source_name = Column(String(200))
    source_config = Column(JSONB)  # 导入配置（查询条件等）

    # 统计
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)

    # 关系
    assets = relationship("Asset", back_populates="batch")


class Asset(Base, UUIDMixin, TimestampMixin):
    """资产表（图片记录）"""
    __tablename__ = "assets"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("import_batches.id", ondelete="SET NULL"))

    # 来源信息 ⭐ 核心字段
    source_type = Column(String(50), nullable=False)  # smarteye/upload
    source_record_id = Column(UUID(as_uuid=True))  # 关联smarteye_downloads.id

    # 存储信息 ⭐ 核心字段
    blob_path = Column(String(500), nullable=False)  # 存储路径（可能是零拷贝引用）
    content_hash = Column(String(64), nullable=False)

    # 图片基本信息
    original_name = Column(String(500))
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    format = Column(String(20))

    # EXIF和元数据
    capture_time = Column(TIMESTAMP(timezone=True))
    device_info = Column(JSONB)  # 设备信息
    location = Column(JSONB)  # GPS信息

    # 质量评分
    quality_score = Column(Integer)  # 0-100
    quality_metrics = Column(JSONB)  # 详细质量指标

    # 标注状态
    annotation_status = Column(String(50), default="pending")  # pending/annotated/reviewed/rejected

    # 关系
    project = relationship("Project", back_populates="assets")
    batch = relationship("ImportBatch", back_populates="assets")
    annotations = relationship("Annotation", back_populates="asset", cascade="all, delete-orphan")


class Annotation(Base, UUIDMixin, TimestampMixin):
    """标注记录表"""
    __tablename__ = "annotations"

    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)

    # 标注数据
    annotation_data = Column(JSONB, nullable=False)  # 标注内容（框、分类等）

    # 版本管理
    version = Column(Integer, default=1)
    is_latest = Column(Boolean, default=True)

    # 来源
    annotation_source = Column(String(50))  # manual/auto/import
    annotator_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))

    # 审核状态
    review_status = Column(String(50), default="pending")  # pending/approved/rejected
    reviewer_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    review_comment = Column(Text)
    reviewed_at = Column(TIMESTAMP(timezone=True))

    # 关系
    asset = relationship("Asset", back_populates="annotations")
    annotator = relationship("User", foreign_keys=[annotator_id], back_populates="annotations")
    reviewer = relationship("User", foreign_keys=[reviewer_id])


class Dataset(Base, UUIDMixin, TimestampMixin):
    """数据集版本表"""
    __tablename__ = "datasets"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # 数据集信息
    dataset_name = Column(String(200), nullable=False)
    version = Column(String(50), nullable=False)
    description = Column(Text)

    # 数据集配置
    split_config = Column(JSONB)  # 训练集/验证集/测试集划分
    asset_ids = Column(ARRAY(UUID(as_uuid=True)))  # 包含的资产ID列表

    # 导出信息
    export_format = Column(String(50))  # yolo/coco/voc
    export_path = Column(String(500))
    exported_at = Column(TIMESTAMP(timezone=True))

    # 关系
    project = relationship("Project", back_populates="datasets")
