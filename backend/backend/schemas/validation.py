from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from schemas.training_run import absolute_path, DETECTION_IMAGE, LIGHTLY_IMAGE, SWIFT_IMAGE


class CheckpointInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    server_id: str = Field(default='default', max_length=36)
    training_run_id: UUID | None = None
    kind: Literal['yolo26', 'rfdetr_medium', 'rfdetr_2xlarge', 'dinov3_vits16_ltdetr', 'caption', 'embedding']
    weight_path: str = Field(min_length=2, max_length=500)
    image: str = Field(default='', max_length=500, pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$|^$')
    device: str = Field(default='cpu', pattern=r'^(cpu|GPU-[A-Za-z0-9-]{1,80})$')
    class_names: list[str] = Field(default_factory=list, max_length=100)
    accept_platform_model_license: bool = False
    base_model_path: str = Field(default='',max_length=500)
    model_type: str = Field(default='',max_length=100,pattern=r'^[A-Za-z0-9_-]*$')

    @field_validator('weight_path')
    @classmethod
    def path(cls, value):
        value = absolute_path(value)
        if len(value.split('/')) < 3:raise ValueError('请选择服务器上的模型路径')
        return value

    @model_validator(mode='after')
    def valid_checkpoint(self):
        multimodal=self.kind in ('caption','embedding')
        self.image=self.image or (SWIFT_IMAGE if multimodal else LIGHTLY_IMAGE if self.kind=='dinov3_vits16_ltdetr' else DETECTION_IMAGE)
        if not multimodal and (not self.weight_path.lower().endswith(('.pt','.pth','.ckpt')) or self.base_model_path or self.model_type):
            raise ValueError('检测任务需要检测权重文件，不支持基础模型目录参数')
        if self.base_model_path:
            self.base_model_path=absolute_path(self.base_model_path)
            if len(self.base_model_path.split('/'))<3:raise ValueError('基础模型路径过于宽泛')
        if self.server_id != 'default': UUID(self.server_id)
        if len(set(self.class_names)) != len(self.class_names) or any(not v.strip() or len(v)>100 for v in self.class_names):
            raise ValueError('训练类别顺序不能为空或重复')
        if self.kind.startswith('rfdetr') and not self.class_names: raise ValueError('RF-DETR需要填写训练时的类别顺序')
        if self.kind=='rfdetr_2xlarge' and not self.accept_platform_model_license: raise ValueError('2XLarge需要确认Platform Model License')
        return self


class ValidationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    inference_confidence: float = Field(default=.001, ge=0, le=1)
    confidence: float = Field(default=.25, ge=0, le=1)
    match_iou: float = Field(default=.5, ge=0, le=1)
    target_overlap: float = Field(default=.2, gt=0, le=1)
    nms_iou: float = Field(default=.45, ge=0, le=1)
    label_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    labels: list[str] = Field(default_factory=list, max_length=100)
    prompt: str = Field(default='',max_length=8000)
    max_tokens: int = Field(default=512,ge=1,le=4096)

    @model_validator(mode='after')
    def valid_policy(self):
        if self.confidence < self.inference_confidence:
            raise ValueError('工作点置信度不得低于推理门槛')
        names = self.labels + list(self.label_mapping) + list(self.label_mapping.values())
        if any(not name.strip() or len(name) > 100 for name in names):
            raise ValueError('提示与映射类别须为1至100字符')
        return self


class ValidationSetCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    project_id: UUID
    snapshot_id: UUID | None = None
    split: Literal['val', 'test'] = 'val'
    asset_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=5000)


class ValidationView(BaseModel):
    mode: Literal['standard', 'target'] = 'standard'
    target_overlap: float | None = Field(default=None, gt=0, le=1)


class CaptionReview(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    verdict: Literal['correct','incorrect','uncertain']
    comment: str = Field(default='',max_length=2000)


class ValidationRunCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    set_id: UUID
    model_id: UUID | None = None
    checkpoint: CheckpointInput | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: ValidationConfig = Field(default_factory=ValidationConfig)

    @model_validator(mode='after')
    def one_source(self):
        if (self.model_id is None) == (self.checkpoint is None): raise ValueError('请选择训练权重或检测服务之一')
        return self
