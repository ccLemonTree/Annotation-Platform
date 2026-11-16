"""
标注相关的数据模型
"""
from pydantic import BaseModel, Field, ConfigDict, StrictInt, field_serializer
from typing import Optional, Dict, Any, Literal
from datetime import datetime, timezone
from uuid import UUID


class AnnotationBase(BaseModel):
    """标注基础模型"""
    annotation_data: Dict[str, Any] = Field(..., description="标注数据(JSON)")


class AnnotationCreate(AnnotationBase):
    """创建标注请求"""
    asset_id: UUID = Field(..., description="资产ID")
    base_version: int = Field(0, ge=0)
    kind: Literal['detection','image'] | None = None


class AnnotationUpdate(BaseModel):
    """更新标注请求"""
    annotation_data: Dict[str, Any]
    base_version: int = Field(..., ge=1)
    kind: Literal['detection','image'] | None = None


class AnnotationReview(BaseModel):
    """标注审核请求"""
    approved: bool = Field(..., description="是否通过审核")
    review_comment: Optional[str] = Field(None, description="审核意见")
    kind: Literal['detection','image'] | None = None


class CurrentImageAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    model_id: UUID
    model_revision: StrictInt = Field(ge=1)
    mode: Literal['attributes','description'] = 'attributes'
    prompt: str = Field(default='',max_length=8000)
    attribute_prompt: str = Field(default='',max_length=8000)


class AnnotationResponse(BaseModel):
    """标注响应"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    annotator_id: Optional[int]
    reviewer_id: Optional[int]
    annotation_data: dict  # 直接是字典，不是字符串
    review_status: Optional[str]
    review_by_kind: dict | None = None
    review_comment: Optional[str]
    version: int
    is_latest: bool
    annotation_source: Optional[str]
    created_at: datetime
    updated_at: datetime
    reviewed_at: Optional[datetime]

    @field_serializer('created_at', 'updated_at', 'reviewed_at')
    def utc_datetime(self, value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
