from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SemanticJobCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    image_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=5000)
    collection_id: UUID | None = None
    caption_model_id: UUID | None = None
    embedding_model_id: UUID | None = None
    dimensions: Literal[2048] = 2048
    prompt: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def selection(self):
        if not self.image_ids and not self.collection_id:
            raise ValueError('请选择图片或图片集')
        if not self.caption_model_id and not self.embedding_model_id:
            raise ValueError('至少选择一个描述或Embedding模型')
        return self


class SemanticAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=5000)


class SemanticSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    embedding_model_id: UUID
    collection_id: UUID | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    image_id: UUID | None = None
    top_k: int = Field(default=20, ge=1, le=100)
    dimensions: Literal[2048] = 2048

    @model_validator(mode='after')
    def query(self):
        if (self.text is None) == (self.image_id is None):
            raise ValueError('请选择文本或一张图片检索')
        return self
