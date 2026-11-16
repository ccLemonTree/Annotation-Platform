"""Typed inputs for training runs and registered trainers."""
import json
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID
from datetime import datetime,timezone,timedelta
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

DETECTION_IMAGE='aisfh5.com:9091/ai/jashan_train:yolo-rfdetr'
SWIFT_IMAGE='modelscope-registry.cn-hangzhou.cr.aliyuncs.com/modelscope-repo/modelscope:ubuntu22.04-cuda13.0.3-py312-torch2.13.0-vllm0.28.0-modelscope1.40.0-swift4.5.3'
LIGHTLY_IMAGE='lightly/train:latest'
K8S_NFS_ROOT='/adb-ssd-mount/ai/chen/training'
K8S_WEMM_DATA_ROOT='/adb-ssd-mount/ai/chen/train/wemm/dataset'


class K8sTraining(BaseModel):
    model_config=ConfigDict(extra='forbid')
    nodes:list[str]=Field(min_length=1,max_length=32)
    gpus_per_node:int=Field(ge=1,le=16,strict=True)

    @field_validator('nodes')
    @classmethod
    def valid_nodes(cls,value):
        import re
        if len(set(value))!=len(value) or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',node) for node in value):
            raise ValueError('请选择不重复的有效K8s节点')
        return value


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

TrainerDatasetFormat = Literal['yolo', 'coco', 'swift_sft', 'infonce', 'contrastive',
                               'online_contrastive', 'cosine_similarity']


def trainer_parameters(value):
    if 'val_dataset' in value:
        raise ValueError('val_dataset 是平台保留字段；请从冻结包读取验证数据')
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (ValueError, RecursionError) as error:
        raise ValueError('训练参数必须是有效的 JSON 对象') from error
    if size > 16000:
        raise ValueError('训练参数不能超过 16000 字节')
    return value


class TrainingTrainerInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    image: str = Field(min_length=1, max_length=500, pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$')
    module: str = Field(max_length=200, pattern=r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$')
    dataset_format: TrainerDatasetFormat
    parameters: dict = Field(default_factory=dict)

    @field_validator('parameters')
    @classmethod
    def valid_parameters(cls, value):
        return trainer_parameters(value)


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


class BuiltinTrainerDefaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parameters: dict


class TrainingRunInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=200)
    kind:Literal['yolo26','rfdetr_medium','rfdetr_2xlarge','swift_sft','wemm','dinov3_vits16_ltdetr','custom']
    trainer_id:UUID|None=None
    server_id:str=Field(default='default',max_length=36)
    execution_backend:Literal['docker','k8s']='docker'
    k8s:K8sTraining|None=None
    image:str=Field(default='',max_length=500,pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$|^$')
    gpu_uuids:list[str]=Field(default_factory=list,max_length=16)
    dataset_path:str=Field(default='',max_length=500)
    snapshot_id:UUID|None=None
    model_path:str=Field(default='',max_length=500)
    repository_model_id:UUID|None=None
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


class TrainingCreate(TrainingRunInput):
    @model_validator(mode='after')
    def recipe(self,info:ValidationInfo):
        import re
        cluster=self.execution_backend=='k8s'
        custom=self.kind=='custom'
        if self.repository_model_id and self.kind not in ('swift_sft','wemm'):
            raise ValueError('只有大模型和向量训练支持从模型仓库选择完整模型')
        if custom != bool(self.trainer_id):
            raise ValueError('请选择已登记的训练器')
        if custom and (not self.snapshot_id or self.mounts):
            raise ValueError('登记训练器使用平台冻结数据，禁止附加主机挂载')
        if custom and PurePosixPath('/output') in (PurePosixPath(self.model_path), *PurePosixPath(self.model_path).parents):
            raise ValueError('基础权重路径不能覆盖容器输出目录')
        if cluster:
            if self.k8s is None or self.gpu_uuids:raise ValueError('K8s请选择节点和每节点GPU数量，不使用GPU UUID')
            if self.kind not in ('yolo26','rfdetr_medium','rfdetr_2xlarge','wemm'):raise ValueError('此训练器尚未接入K8s')
            if self.server_id!='default' or self.mounts:raise ValueError('K8s使用嘉善集群，不支持其它连接或附加挂载')
            for field in ('dataset_path','model_path','output_root'):
                value=getattr(self,field)
                if value:
                    value=absolute_path(value)
                    if any(part in ('','.') for part in value.split('/')[1:]):raise ValueError('K8s路径不能包含空路径段或当前目录段')
                    setattr(self,field,value)
            data_roots=(K8S_NFS_ROOT+'/',K8S_WEMM_DATA_ROOT+'/') if self.kind=='wemm' else (K8S_NFS_ROOT+'/',)
            if self.dataset_path and not self.dataset_path.startswith(data_roots):raise ValueError('K8s数据必须位于169的训练目录；WeMM也支持既有chen/train/wemm/dataset目录')
            if not self.output_root.startswith(K8S_NFS_ROOT+'/'):raise ValueError('K8s输出必须位于169的chen/training目录下')
            if self.model_path and not absolute_path(self.model_path).startswith('/adb-ssd-mount/ai/chen/'):
                raise ValueError('K8s基础模型必须位于169的ai/chen目录下')
            count=self.k8s.gpus_per_node
        else:
            if self.k8s is not None or not self.gpu_uuids:raise ValueError('Docker请选择物理GPU UUID，不能携带K8s配置')
            count=len(self.gpu_uuids)
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
        if custom and self.model_path:
            self.model_path=str(PurePosixPath(absolute_path(self.model_path)))
        if (cluster or self.kind not in ('rfdetr_2xlarge','custom')) and not (self.model_path or self.repository_model_id):raise ValueError('请填写服务器已有基础权重绝对路径')
        defaults=dict(learning_rate=2e-5,save_steps=1000) if cluster and self.kind=='wemm' else {}
        defaults.update((info.context or {}).get('parameters',{}))
        if cluster and self.kind!='wemm':
            defaults['device' if self.kind=='yolo26' else 'devices']='auto'
        self.parameters=(trainer_parameters(self.parameters) if custom else
                         PARAMETERS[self.kind].model_validate({**defaults,**self.parameters}).model_dump())
        if self.kind.startswith('rfdetr'):
            devices=self.parameters['devices']
            if devices!='auto' and (isinstance(devices,bool) or not isinstance(devices,int) or not 1<=devices<=count):
                raise ValueError('devices须为auto或不超过所选GPU数的正整数')
            if cluster and devices not in ('auto',count):raise ValueError('K8s devices须为auto或等于每节点GPU数量')
        if self.kind=='yolo26' and self.parameters['device']!='auto':
            device=self.parameters['device'].split(',')
            if len(set(device))!=len(device) or any(not d.isdecimal() or int(d)>=count for d in device):
                raise ValueError('device使用容器内从0开始的GPU编号，不得超出所选卡')
        if cluster and self.kind=='yolo26' and self.parameters['device']!='auto':raise ValueError('K8s YOLO使用device=auto，由分布式拓扑分配GPU')
        if self.kind=='yolo26':
            used=count*len(self.k8s.nodes) if cluster else count if self.parameters['device']=='auto' else len(self.parameters['device'].split(','))
            if self.parameters['batch']%used:raise ValueError('YOLO总batch必须能被实际使用的GPU数量整除')
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
        processes=self.environment.get('NPROC_PER_NODE','auto')
        if processes!='auto' and (not processes.isdecimal() or int(processes)!=count):
            raise ValueError('NPROC_PER_NODE必须与所选GPU数量一致')
        visible=self.environment.get('CUDA_VISIBLE_DEVICES','auto')
        if visible!='auto' and (sorted(visible.split(','))!=sorted(str(i) for i in range(count))):
            raise ValueError('CUDA_VISIBLE_DEVICES须覆盖所选卡在容器内的0起始编号')
        if self.kind in ('swift_sft','wemm'):
            val=self.parameters['val_dataset']
            if val:absolute_path(val)
            if cluster and val and not val.startswith(data_roots):raise ValueError('K8s验证数据必须位于169的训练数据目录下')
            if self.parameters['eval_strategy']!='no' and not val:raise ValueError('启用评估需要服务器验证JSONL路径')
            if self.parameters['task_type']=='causal_lm' and self.parameters['loss_type']:raise ValueError('向量损失函数只适用于embedding任务')
        if not custom:
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
