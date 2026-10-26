"""Typed inputs for the supplied training recipes."""
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID
from datetime import datetime,timezone,timedelta
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DETECTION_IMAGE='aisfh5.com:9091/ai/jashan_train:yolo-rfdetr'
SWIFT_IMAGE='modelscope-registry.cn-hangzhou.cr.aliyuncs.com/modelscope-repo/modelscope:ubuntu22.04-cuda13.0.3-py312-torch2.13.0-vllm0.28.0-modelscope1.40.0-swift4.5.3'
LIGHTLY_IMAGE='lightly/train:latest'


def default_output_root():
    return '/ai/chen/training/runs/'+datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')


def absolute_path(value):
    if not value.startswith('/') or value.startswith('//') or any(c in value for c in ('\0','\n','\r',':','\\',',')) or '..' in value.split('/'):
        raise ValueError('必须为无跳级的Linux绝对路径，不含冒号、逗号或控制字符')
    value=value.rstrip('/') or '/'
    if any(value==p or value.startswith(p+'/') for p in ('/proc','/sys','/dev','/etc','/var/run','/run')):
        raise ValueError('禁止使用系统目录作为训练数据或输出')
    return value


class Parameters(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class YoloParameters(Parameters):
    epochs:int=Field(default=300,ge=1,le=100000)
    batch:int=Field(default=48,ge=1,le=65536)
    patience:int=Field(default=15,ge=0,le=100000)
    save_period:int=Field(default=10,ge=1,le=100000)
    device:str='auto'
    seed:int=Field(default=42,ge=0,le=2147483647)


class RfdetrParameters(Parameters):
    epochs:int=Field(default=100,ge=1,le=100000)
    batch_size:int=Field(default=4,ge=1,le=65536)
    grad_accum_steps:int=Field(default=4,ge=1,le=65536)
    lr:float=Field(default=1e-4,gt=0,le=1,allow_inf_nan=False)
    devices:str|int='auto'


class RfdetrXLParameters(RfdetrParameters):
    batch_size:int=Field(default=1,ge=1,le=65536)
    grad_accum_steps:int=Field(default=2,ge=1,le=65536)


class Dinov3Parameters(Parameters):
    steps:int=Field(default=1000,ge=1,le=1000000)
    batch_size:int=Field(default=4,ge=1,le=65536)
    gradient_accumulation_steps:int=Field(default=1,ge=1,le=65536)
    lr:float=Field(default=1e-4,gt=0,le=1,allow_inf_nan=False)
    backbone_lr_factor:float=Field(default=.01,ge=0,le=1,allow_inf_nan=False)
    backbone_freeze:bool=False
    warmup_steps:int=Field(default=100,ge=0,le=1000000)
    save_steps:int=Field(default=100,ge=1,le=1000000)
    precision:Literal['16-mixed','bf16-mixed','32-true']='16-mixed'
    num_workers:int=Field(default=2,ge=0,le=128)
    seed:int=Field(default=42,ge=0,le=2147483647)


class SwiftParameters(Parameters):
    task_type:Literal['causal_lm','embedding']='causal_lm'
    loss_type:Literal['infonce','contrastive','online_contrastive','cosine_similarity']|None=None
    tuner_type:Literal['lora','full']='lora'
    lora_rank:int=Field(default=8,ge=1,le=1024)
    lora_alpha:int=Field(default=32,ge=1,le=4096)
    torch_dtype:Literal['float16','bfloat16','float32']='float16'
    num_train_epochs:float=Field(default=1,gt=0,le=100000,allow_inf_nan=False)
    per_device_train_batch_size:int=Field(default=1,ge=1,le=65536)
    gradient_accumulation_steps:int=Field(default=1,ge=1,le=65536)
    learning_rate:float=Field(default=5e-5,gt=0,le=1,allow_inf_nan=False)
    max_length:int=Field(default=2048,ge=1,le=1048576)
    lazy_tokenize:bool=True
    dataloader_num_workers:int=Field(default=2,ge=0,le=128)
    logging_steps:int=Field(default=10,ge=1,le=100000)
    save_steps:int=Field(default=100,ge=1,le=1000000)
    save_total_limit:int=Field(default=3,ge=1,le=10000)
    eval_strategy:Literal['no','steps','epoch']='no'
    val_dataset:str=''
    dataloader_drop_last:bool=True
    freeze_vit:bool=True
    freeze_aligner:bool=False


class WemmParameters(SwiftParameters):
    task_type:Literal['embedding']='embedding'
    loss_type:Literal['infonce','contrastive','online_contrastive','cosine_similarity']='infonce'


PARAMETERS={'yolo26':YoloParameters,'rfdetr_medium':RfdetrParameters,'rfdetr_2xlarge':RfdetrXLParameters,
            'swift_sft':SwiftParameters,'wemm':WemmParameters,'dinov3_vits16_ltdetr':Dinov3Parameters}
ENVIRONMENT={'MAX_PIXELS':'100352','PYTORCH_CUDA_ALLOC_CONF':'expandable_segments:True',
    'TOKENIZERS_PARALLELISM':'false','NPROC_PER_NODE':'auto','CUDA_VISIBLE_DEVICES':'auto',
    'INFONCE_USE_BATCH':'false','INFONCE_HARD_NEGATIVES':'4','IMAGE_MAX_TOKEN_NUM':''}


class TrainingMount(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str=Field(max_length=500)
    target:str=Field(max_length=500)
    read_only:bool=True

    @field_validator('source','target')
    @classmethod
    def path(cls,value):
        value=absolute_path(value)
        if value=='/' or value=='/output' or value.startswith('/output/') or any(value==p or value.startswith(p+'/') for p in ('/proc','/sys','/dev','/etc','/var/run','/run')):
            raise ValueError('禁止挂载系统目录、Docker套接字或覆盖任务输出')
        return value


class TrainingCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=200)
    kind:Literal['yolo26','rfdetr_medium','rfdetr_2xlarge','swift_sft','wemm','dinov3_vits16_ltdetr']
    server_id:str=Field(default='default',max_length=36)
    image:str=Field(default='',max_length=500,pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$|^$')
    gpu_uuids:list[str]=Field(min_length=1,max_length=16)
    dataset_path:str=Field(default='',max_length=500)
    snapshot_id:UUID|None=None
    model_path:str=Field(default='',max_length=500)
    output_root:str=Field(default_factory=default_output_root,min_length=2,max_length=450)
    selected_labels:list[str]|None=Field(default=None,min_length=1,max_length=1000)
    output_subdir:str=Field(default='train',min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    shm_size_gb:int=Field(default=16,ge=1,le=1024,strict=True)
    accept_platform_model_license:bool=False
    parameters:dict=Field(default_factory=dict)
    environment:dict[str,str]=Field(default_factory=dict)
    mounts:list[TrainingMount]=Field(default_factory=list,max_length=30)

    @field_validator('output_root')
    @classmethod
    def root(cls,value):
        value=absolute_path(value)
        if value=='/':raise ValueError('输出不能为根目录')
        return value

    @model_validator(mode='after')
    def recipe(self):
        import re
        if self.server_id!='default':UUID(self.server_id)
        if len(set(self.gpu_uuids))!=len(self.gpu_uuids) or any(not re.fullmatch(r'GPU-[A-Za-z0-9-]{1,80}',gpu) for gpu in self.gpu_uuids):
            raise ValueError('请选择不重复的物理GPU UUID')
        if bool(self.snapshot_id)==bool(self.dataset_path):raise ValueError('请选择冻结导出或服务器数据路径之一')
        if self.selected_labels is not None:
            if not self.snapshot_id:raise ValueError('仅平台冻结标注数据支持选择训练标签；服务器数据沿用原配置')
            if self.kind in ('swift_sft','wemm'):raise ValueError('检测标签选择不适用于Swift/WeMM JSONL')
            if any(not v.strip() or len(v)>200 for v in self.selected_labels) or len(set(self.selected_labels))!=len(self.selected_labels):
                raise ValueError('训练标签不能为空或重复')
        for value in (self.dataset_path,self.model_path):
            if value:
                absolute_path(value)
                if PurePosixPath(value).parent==PurePosixPath('/'):raise ValueError('训练数据或权重不能位于根目录，避免挂载整个主机')
        if self.kind!='rfdetr_2xlarge' and not self.model_path:raise ValueError('请填写服务器已有基础权重绝对路径')
        self.parameters=PARAMETERS[self.kind].model_validate(self.parameters).model_dump()
        if self.kind.startswith('rfdetr'):
            devices=self.parameters['devices']
            if devices!='auto' and (isinstance(devices,bool) or not isinstance(devices,int) or not 1<=devices<=len(self.gpu_uuids)):
                raise ValueError('devices须为auto或不超过所选GPU数的正整数')
        if self.kind=='yolo26' and self.parameters['device']!='auto':
            device=self.parameters['device'].split(',')
            if len(set(device))!=len(device) or any(not d.isdecimal() or int(d)>=len(self.gpu_uuids) for d in device):
                raise ValueError('device使用容器内从0开始的GPU编号，不得超出所选卡')
        if self.kind=='yolo26':
            count=len(self.gpu_uuids) if self.parameters['device']=='auto' else len(self.parameters['device'].split(','))
            if self.parameters['batch']%count:raise ValueError('YOLO总batch必须能被实际使用的GPU数量整除')
        if self.kind=='dinov3_vits16_ltdetr':
            if self.parameters['batch_size']%len(self.gpu_uuids):raise ValueError('DINOv3总batch必须能被所选GPU数量整除')
            if self.parameters['warmup_steps']>=self.parameters['steps']:raise ValueError('学习率预热步数必须小于总训练步数')
        if self.environment.keys()-ENVIRONMENT.keys():raise ValueError('包含不支持的环境变量')
        if any(len(v)>300 or any(c in v for c in ('\0','\r','\n')) for v in self.environment.values()):raise ValueError('环境变量值无效')
        for key in ('INFONCE_USE_BATCH','TOKENIZERS_PARALLELISM'):
            if key in self.environment and self.environment[key] not in ('true','false'):raise ValueError(key+'须为true或false')
        for key in ('MAX_PIXELS','IMAGE_MAX_TOKEN_NUM','INFONCE_HARD_NEGATIVES'):
            value=self.environment.get(key,'')
            if value and (not value.isdecimal() or int(value)<(0 if key=='INFONCE_HARD_NEGATIVES' else 1)):raise ValueError(key+'须为有效整数')
        count=self.environment.get('NPROC_PER_NODE','auto')
        if count!='auto' and (not count.isdecimal() or int(count)!=len(self.gpu_uuids)):
            raise ValueError('NPROC_PER_NODE必须与所选GPU数量一致')
        visible=self.environment.get('CUDA_VISIBLE_DEVICES','auto')
        if visible!='auto' and (sorted(visible.split(','))!=sorted(str(i) for i in range(len(self.gpu_uuids)))):
            raise ValueError('CUDA_VISIBLE_DEVICES须覆盖所选卡在容器内的0起始编号')
        if self.kind in ('swift_sft','wemm'):
            val=self.parameters['val_dataset']
            if val:absolute_path(val)
            if self.parameters['eval_strategy']!='no' and not val:raise ValueError('启用评估需要服务器验证JSONL路径')
            if self.parameters['task_type']=='causal_lm' and self.parameters['loss_type']:raise ValueError('向量损失函数只适用于embedding任务')
        self.image=self.image or (LIGHTLY_IMAGE if self.kind=='dinov3_vits16_ltdetr' else SWIFT_IMAGE if self.kind in ('swift_sft','wemm') else DETECTION_IMAGE)
        return self


class TrainingDatasetInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    server_id:str=Field(default='default',max_length=36)
    kind:Literal['yolo26','rfdetr_medium','rfdetr_2xlarge','dinov3_vits16_ltdetr']
    dataset_path:str=Field(min_length=2,max_length=500)
    image:str=Field(default='',max_length=500,pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$|^$')
    mounts:list[TrainingMount]=Field(default_factory=list,max_length=30)

    @field_validator('dataset_path')
    @classmethod
    def path(cls,value):
        value=absolute_path(value)
        if PurePosixPath(value).parent==PurePosixPath('/'):raise ValueError('请提供数据集内的路径')
        return value

    @model_validator(mode='after')
    def image_default(self):
        self.image=self.image or (LIGHTLY_IMAGE if self.kind=='dinov3_vits16_ltdetr' else DETECTION_IMAGE)
        return self
