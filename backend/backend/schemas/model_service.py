import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from schemas.image_ingest import ImageIngestConfig
from schemas.prompts import ChatPrompts


class ModelServiceConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra='forbid', str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=200)
    kind: Literal['detection', 'caption', 'embedding', 'reranker']
    protocol: Literal['model_service', 'openai_chat', 'openai_embedding', 'openai_rerank']
    base_url: str = Field(max_length=500)
    endpoint: str = Field(max_length=200)
    model_name: str = Field(default='', max_length=200)
    enabled: bool = True
    timeout_seconds: int = Field(default=60, ge=1, le=180)
    confidence: float = Field(default=.25, ge=0, le=1, allow_inf_nan=False)
    iou: float = Field(default=.45, ge=0, le=1, allow_inf_nan=False)
    labels: list[str] = Field(default_factory=list, max_length=100)
    label_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    api_key_env: str = Field(default='', max_length=100)
    chat_prompts: ChatPrompts = Field(default_factory=ChatPrompts)

    @property
    def has_confidence(self):
        return not (self.protocol == 'model_service' and self.endpoint == '/detect/grounding')

    @property
    def has_nms(self):
        return self.has_confidence and not self.endpoint.endswith(('/sam3', '_sahi'))

    @field_validator('base_url')
    @classmethod
    def url(cls, value):
        return ImageIngestConfig.valid_url(value)

    @field_validator('endpoint')
    @classmethod
    def endpoint_path(cls, value):
        if not re.fullmatch(r'/[A-Za-z0-9_/-]+', value) or '//' in value or '..' in value:
            raise ValueError('接口路径须为相对服务地址的绝对路径，不含查询、域名或跳级')
        return value

    @field_validator('api_key_env')
    @classmethod
    def key_env(cls, value):
        if value and not (value in ('QWEN_API_KEY', 'EMBEDDING_API_KEY', 'RERANKER_API_KEY') or
                          re.fullmatch(r'MODEL_API_KEY_[A-Z0-9_]+', value)):
            raise ValueError('鉴权仅引用QWEN/EMBEDDING/RERANKER_API_KEY或MODEL_API_KEY_开头的环境变量')
        return value

    @field_validator('labels', 'label_mapping')
    @classmethod
    def label_names(cls, value):
        names = list(value) + list(value.values()) if isinstance(value, dict) else value
        if any(not name.strip() or len(name) > 100 for name in names):
            raise ValueError('提示与类别名称须为1至100字符')
        return value

    @model_validator(mode='after')
    def compatible(self):
        allowed = {'model_service': ('detection', 'caption'), 'openai_chat': ('caption',),
                   'openai_embedding': ('embedding',), 'openai_rerank': ('reranker',)}
        if self.kind not in allowed[self.protocol]:
            raise ValueError('模型用途与接口协议不匹配')
        if self.protocol != 'model_service' and not self.model_name:
            raise ValueError('OpenAI格式服务需配置准确模型ID')
        return self


class ModelServiceResponse(ModelServiceConfig):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revision: int


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: UUID | None = None
    image_id: UUID | None = None
    prompt: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def one_image(self):
        if (self.asset_id is None) == (self.image_id is None):
            raise ValueError('请选择一张项目图片或图片库图片')
        return self
