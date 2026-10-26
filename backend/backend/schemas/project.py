"""
项目相关的数据模型
"""
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
from models.project import TaskType, ProjectStatus


def validate_label_schema(value):
    if value and value.get('annotation_scope','all') not in ('all','crops'):
        raise ValueError('标注范围必须为all或crops')
    if value and value.get('classification_mode', 'single') not in ('single', 'multi'):
        raise ValueError('分类方式必须为single或multi')
    if value is None or 'labels' not in value:
        return value
    labels = value['labels']
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError('类别必须是非空名称列表')
    labels = [label.strip() for label in labels]
    if len(labels) != len(set(labels)):
        raise ValueError('类别名称不能重复')
    return {**value, 'labels': labels}


class ProjectBase(BaseModel):
    """项目基础模型"""
    project_name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    task_type: TaskType = Field(..., description="任务类型")
    description: Optional[str] = Field(None, description="项目描述")
    label_schema: Optional[dict] = None
    _labels = field_validator('label_schema')(validate_label_schema)


class ProjectCreate(ProjectBase):
    """创建项目请求"""
    pass


class ProjectUpdate(BaseModel):
    """更新项目请求"""
    project_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[ProjectStatus] = None
    label_schema: Optional[dict] = None
    _labels = field_validator('label_schema')(validate_label_schema)
    label_renames: dict[str, str] = Field(default_factory=dict)
    base_label_schema: Optional[dict] = None


class ProjectResponse(BaseModel):
    """项目响应"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: int
    project_name: str
    task_type: TaskType
    status: ProjectStatus
    description: Optional[str]
    label_schema: Optional[dict]
    total_assets: int
    annotated_count: int
    reviewed_count: int
    created_at: datetime
    updated_at: datetime


class ProjectStats(BaseModel):
    """项目统计信息"""
    project_id: UUID
    total_assets: int
    annotated_count: int
    reviewed_count: int
    annotation_progress: float = Field(..., description="标注进度百分比")
    review_progress: float = Field(..., description="复核进度百分比")


class ProjectLabelRemovalPreview(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    label:str=Field(min_length=1,max_length=100)


class ProjectLabelRemoval(ProjectLabelRemovalPreview):
    preview_token:str=Field(pattern=r'^[a-f0-9]{64}$')
