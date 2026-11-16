from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from schemas.model_service import DEFAULT_DETECTION_CONFIDENCE


class AutomationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    confidence: float = Field(default=DEFAULT_DETECTION_CONFIDENCE, ge=0, le=1)
    iou: float = Field(default=.45, ge=0, le=1)
    auto_approve_confidence: float = Field(default=.9, ge=0, le=1)
    crop_expansion: float = Field(default=.2, ge=0, le=1)
    auto_review: bool = True
    label_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    labels: list[str] = Field(default_factory=list, max_length=100)
    prompt: str = Field(default='', max_length=2000)
    review_prompt: str = Field(default='', max_length=8000)
    preannotation_prompt: str = Field(default='', max_length=8000)
    detection_prompt: str = Field(default='', max_length=8000)

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
    follow_uploads: bool = False
    asset_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=10000)
    detector_id: UUID | None = None
    preannotation_model_id: UUID | None = None
    example_asset_id: UUID | None = None
    example_annotation_version: StrictInt | None = Field(default=None,ge=1)
    reviewer_id: UUID | None = None
    config: AutomationConfig = Field(default_factory=AutomationConfig)

    @model_validator(mode='after')
    def required_models(self):
        if (self.example_asset_id is None)!=(self.example_annotation_version is None):raise ValueError('正确范例需同时提供图片和标注版本')
        if self.preannotation_model_id or self.example_asset_id:
            if self.kind!='preannotate' or not self.preannotation_model_id or not self.example_asset_id or self.detector_id:
                raise ValueError('范例预标注需选择视觉大模型和正确范例')
        if self.kind=='preannotate' and self.detector_id is None and self.preannotation_model_id is None:
            raise ValueError('预标注需要检测模型')
        if self.preannotation_model_id:self.config.auto_review=False
        if (self.config.preannotation_prompt.strip() or self.config.detection_prompt.strip()) and not self.preannotation_model_id:
            raise ValueError('范例预标注提示词仅适用于视觉大模型范例预标注')
        if self.config.review_prompt.strip() and not self.config.auto_review:
            raise ValueError('审核核心提示词仅适用于已启用AI审核的任务')
        if (self.config.auto_review or self.kind=='review') and self.reviewer_id is None:
            raise ValueError('AI审核需要OpenAI视觉大模型')
        return self


class ResolveReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved: bool
    comment: str = Field(default='', max_length=2000)


class FollowUploads(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
