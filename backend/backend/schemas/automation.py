from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AutomationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    confidence: float = Field(default=.25, ge=0, le=1)
    iou: float = Field(default=.45, ge=0, le=1)
    auto_approve_confidence: float = Field(default=.9, ge=0, le=1)
    crop_expansion: float = Field(default=.2, ge=0, le=1)
    auto_review: bool = True
    label_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    labels: list[str] = Field(default_factory=list, max_length=100)
    prompt: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def valid_labels(self):
        values=self.labels+list(self.label_mapping)+list(self.label_mapping.values())
        if any(not value.strip() or len(value)>100 for value in values):
            raise ValueError('模型提示与映射类别须为1至100字符')
        return self


class AutomationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: UUID
    kind: Literal['preannotate', 'review']
    asset_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=10000)
    detector_id: UUID | None = None
    reviewer_id: UUID | None = None
    config: AutomationConfig = Field(default_factory=AutomationConfig)

    @model_validator(mode='after')
    def required_models(self):
        if self.kind=='preannotate' and self.detector_id is None:
            raise ValueError('预标注需要检测模型')
        if (self.config.auto_review or self.kind=='review') and self.reviewer_id is None:
            raise ValueError('AI审核需要OpenAI视觉大模型')
        return self


class ResolveReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: bool
    comment: str = Field(default='', max_length=2000)
