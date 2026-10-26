"""
资产相关的数据模型
"""
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from uuid import UUID


class AssetBase(BaseModel):
    """资产基础模型"""
    original_name: str = Field(..., description="原始文件名")
    blob_path: str = Field(..., description="存储路径")
    content_hash: str = Field(..., description="内容哈希")
    file_size: int = Field(..., ge=0, description="文件大小(字节)")
    width: Optional[int] = Field(None, ge=1, description="图片宽度")
    height: Optional[int] = Field(None, ge=1, description="图片高度")


class AssetCreate(AssetBase):
    """创建资产请求"""
    project_id: UUID = Field(..., description="项目ID")
    source_type: str = Field(..., description="来源类型")
    source_record_id: Optional[UUID] = Field(None, description="来源记录ID")


class AssetResponse(BaseModel):
    """资产响应"""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    image_id: Optional[UUID]
    project_id: UUID
    batch_id: Optional[UUID]
    blob_path: str
    content_hash: str
    original_name: str
    file_size: int
    source_type: str
    source_record_id: Optional[UUID]
    width: Optional[int]
    height: Optional[int]
    format: Optional[str]
    annotation_status: str
    quality_score: Optional[int]
    device_info: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer('created_at','updated_at')
    def utc_datetime(self,value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class AssetUploadResponse(BaseModel):
    """资产上传响应"""
    asset_id: UUID
    original_name: str
    blob_path: str
    file_size: int
