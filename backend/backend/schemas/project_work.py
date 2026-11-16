from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from schemas.object_attributes import AttributeSchema
from schemas.model_service import DEFAULT_DETECTION_CONFIDENCE
from schemas.automation import FollowUploads


class WorkFollowUploads(FollowUploads):
    uploaded_crop_job_id: UUID | None = None

FOLLOW_UPLOAD_OPERATIONS = ('crop', 'caption', 'index', 'detect_attributes', 'attributes', 'quality')

class WorkCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation: Literal['crop','caption','index','detect_attributes','attributes','quality']
    follow_uploads: bool = False
    model_id: UUID
    attribute_model_id: UUID | None = None
    example_asset_id: UUID | None = None
    example_annotation_version: StrictInt | None = Field(default=None,ge=1)
    asset_ids: list[UUID] = Field(min_length=1,max_length=100000)
    prompt: str = Field(default='',max_length=8000)
    attribute_prompt: str = Field(default='',max_length=8000)
    detection_prompt: str = Field(default='',max_length=8000)
    labels: list[str] = Field(default_factory=list,max_length=100)
    confidence: float = Field(default=DEFAULT_DETECTION_CONFIDENCE,ge=0,le=1)
    expansion: float = Field(default=.2,ge=0,le=1)
    crops_only: bool = Field(default=True, deprecated=True, description='兼容旧客户端。请在标注准备中单独修改项目标注范围。')
    min_crop_long_edge: StrictInt = Field(default=0,ge=0,le=32768)

    @model_validator(mode='after')
    def targets(self):
        if self.operation!='crop' and len(self.asset_ids)>5000:
            raise ValueError('此处理方式单批最多 5000 张')
        if self.follow_uploads and self.operation not in FOLLOW_UPLOAD_OPERATIONS:
            raise ValueError('此处理方式不支持接收新上传图片')
        if self.attribute_prompt and self.operation not in ('attributes','detect_attributes'):
            raise ValueError('属性核心提示词仅适用于目标属性分析')
        if self.detection_prompt and self.operation not in ('crop','detect_attributes'):
            raise ValueError('检测提示词仅适用于检测切图')
        if self.operation=='quality' and not self.prompt:
            raise ValueError('请填写图片质量检查要求')
        if self.operation=='quality' and len(self.prompt)>2000:
            raise ValueError('图片质量检查要求不超过2000字符')
        if (self.example_asset_id is None)!=(self.example_annotation_version is None):
            raise ValueError('正确范例需同时提供图片和标注版本')
        if self.example_asset_id is not None and self.operation not in ('attributes','detect_attributes'):
            raise ValueError('正确范例适用于大模型属性预标注')
        if self.operation=='detect_attributes' and self.attribute_model_id is None:
            raise ValueError('请选择属性分析模型')
        if self.operation!='detect_attributes' and self.attribute_model_id is not None:
            raise ValueError('只有检测后属性分析需要第二个模型')
        if any(not label.strip() or len(label)>100 for label in self.labels):
            raise ValueError('裁剪目标必须为有效类别')
        self.labels=list(dict.fromkeys(label.strip() for label in self.labels))
        return self


class WorkflowAttributeTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    config: AttributeSchema


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    operation: Literal['crop','detect','preannotate','ai_review','filter','quality','caption','attributes']
    model_id: UUID
    prompt: str = Field(default='', max_length=8000)
    attribute_prompt: str = Field(default='', max_length=8000)
    attribute_templates: list[WorkflowAttributeTemplate] | None = Field(default=None, min_length=1, max_length=30)
    labels: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=DEFAULT_DETECTION_CONFIDENCE, ge=0, le=1)
    expansion: float = Field(default=.2, ge=0, le=1)
    min_crop_long_edge: StrictInt = Field(default=0, ge=0, le=32768)
    save_input_boxes: bool | None = None
    auto_approve_confidence: float = Field(default=.9, ge=0, le=1)

    @model_validator(mode='after')
    def validate_operation(self):
        if self.attribute_prompt and self.operation!='attributes':
            raise ValueError('属性核心提示词仅适用于目标属性分析')
        if self.attribute_templates:
            if self.operation!='attributes':raise ValueError('按类别应用模板仅适用于目标属性分析')
            ids=[template.id for template in self.attribute_templates]
            labels=[label.casefold() for template in self.attribute_templates for label in template.config.target_labels]
            if len(set(ids))!=len(ids) or len(set(labels))!=len(labels):
                raise ValueError('同一类别只能配置一个属性模板')
        if self.operation=='filter' and not 1<=len(self.prompt)<=2000:
            raise ValueError('请填写 1 至 2000 字的筛选条件')
        if self.operation=='quality' and not 1<=len(self.prompt)<=2000:
            raise ValueError('请填写 1 至 2000 字的图片质量要求')
        if any(not label or len(label)>100 for label in self.labels):
            raise ValueError('请输入有效检测类别')
        return self


class WorkflowTemplateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=20)
    attribute_schema: AttributeSchema | None = None


class WorkflowCreate(WorkflowTemplateCreate):
    name: str = Field(default='批量处理', min_length=1, max_length=100)
    source_job_id: UUID | None = None
    image_kind: Literal['originals','crops','all','uploads'] = 'originals'
    follow_uploads: bool = False

    @model_validator(mode='after')
    def upload_source(self):
        if self.image_kind=='uploads' and (not self.follow_uploads or self.source_job_id):
            raise ValueError('仅接收新上传图片时须开启自动接收')
        if self.follow_uploads and self.source_job_id:
            raise ValueError('当前图片来源不支持自动接收上传')
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
    review_prompt: str = Field(default='',max_length=8000)
