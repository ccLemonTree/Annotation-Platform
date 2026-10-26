from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation: Literal['crop','caption','index']
    model_id: UUID
    asset_ids: list[UUID] = Field(min_length=1,max_length=5000)
    prompt: str = Field(default='',max_length=2000)
    labels: list[str] = Field(default_factory=list,max_length=100)
    confidence: float = Field(default=.4,ge=0,le=1)
    expansion: float = Field(default=.2,ge=0,le=1)
    crops_only: bool = True

    @model_validator(mode='after')
    def targets(self):
        if any(not label.strip() or len(label)>100 for label in self.labels):
            raise ValueError('裁剪目标必须为有效类别')
        self.labels=list(dict.fromkeys(label.strip() for label in self.labels))
        return self


class MatchSearch(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    model_id: UUID
    text: str | None = Field(default=None,min_length=1,max_length=2000)
    image_id: UUID | None = None
    top_k: int = Field(default=20,ge=1,le=100)

    @model_validator(mode='after')
    def query(self):
        if (self.text is None)==(self.image_id is None):
            raise ValueError('请选择文本或图片作为查询')
        return self


class MatchJudgment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: UUID
    judgment: Literal['correct','incorrect','unknown']
    base_version: int = Field(ge=1)


class MatchReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: UUID
    approved: bool
    base_version: int = Field(ge=1)


class MatchReviewBatch(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    model_id: UUID
    query_ids: list[UUID] | None = Field(default=None,min_length=1,max_length=1000)
    prompt: str = Field(default='',max_length=2000)
