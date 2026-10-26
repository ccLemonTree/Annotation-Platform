"""Explicit deployment targets; browser requests never contain SSH credentials."""
from pathlib import PurePosixPath
import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from schemas.training_run import absolute_path


class DeploymentCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    run_id:UUID
    artifact_path:str=Field(min_length=1,max_length=500)
    environment:Literal['k8s','docker','docker_kafka']='docker'
    runtime:Literal['triton_plan','triton_vllm','vllm']='triton_plan'
    file_server:str=Field(min_length=1,max_length=100)
    control_server:str=Field(min_length=1,max_length=100)
    repository:str=Field(min_length=3,max_length=450)
    model_name:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$')
    containers:list[str]=Field(default_factory=list,max_length=100)
    namespace:str=Field(default='ai',pattern=r'^[a-z0-9][a-z0-9.-]{0,62}$')
    workload_kind:Literal['StatefulSet','Deployment']='StatefulSet'
    workload:str=Field(default='',max_length=253)
    container:str=Field(default='triton-server',pattern=r'^[a-z0-9][a-z0-9.-]{0,62}$')
    triton_config:str=Field(default='',max_length=64000)
    runtime_config:dict=Field(default_factory=dict)

    @field_validator('repository')
    @classmethod
    def repository_path(cls,value):
        value=absolute_path(value)
        if len(PurePosixPath(value).parts)<3:raise ValueError('请选择具体模型仓库，不能使用根目录或一级目录')
        return value

    @field_validator('artifact_path')
    @classmethod
    def relative_path(cls,value):
        if '\\' in value or ':' in value or any(ord(c)<32 for c in value) or PurePosixPath(value).is_absolute() or any(p in ('','.','..') for p in value.split('/')):
            raise ValueError('请选择任务产物内的相对文件或完整模型目录')
        return value

    @field_validator('containers')
    @classmethod
    def container_names(cls,value):
        if len(value)!=len(set(value)) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',name) for name in value):
            raise ValueError('Docker容器名称无效或重复')
        return value

    @model_validator(mode='after')
    def selected_target(self):
        if any(any(ord(c)<32 for c in name) for name in (self.file_server,self.control_server)):
            raise ValueError('SSH服务器名称无效')
        if self.environment=='k8s':
            if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',self.workload):raise ValueError('请选择指定K8s工作负载')
            if self.containers:raise ValueError('K8s不使用Docker容器列表')
        elif not self.containers or self.file_server!=self.control_server:
            raise ValueError('Docker请选择至少一个容器，文件与控制服务器必须一致')
        if self.environment=='docker_kafka' and self.runtime=='vllm':
            raise ValueError('Kafka worker目标使用Triton；向量vLLM请选择独立部署目标')
        if len(str(self.runtime_config))>64000:raise ValueError('推理配置过大')
        return self


class ReleaseCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    run_id:UUID
    name:str=Field(min_length=1,max_length=100)
    version:str=Field(min_length=1,max_length=100)
    runtime:Literal['triton_plan','triton_vllm','vllm']
    artifact_path:str=Field(min_length=1,max_length=500)
    _relative=field_validator('artifact_path')(DeploymentCreate.relative_path.__func__)


class ImageTarget(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    environment:Literal['k8s','docker','docker_kafka']='docker'
    control_server:str=Field(min_length=1,max_length=100,pattern=r'^[^\x00-\x1f]+$')
    containers:list[str]=Field(default_factory=list,max_length=100)
    namespace:str=Field(default='ai',pattern=r'^[a-z0-9][a-z0-9.-]{0,62}$')
    workload_kind:Literal['StatefulSet','Deployment']='StatefulSet'
    workload:str=Field(default='',max_length=253)
    container:str=Field(default='nexusai',pattern=r'^[a-z0-9][a-z0-9.-]{0,62}$')
    preload_playbook:str=Field(default='',max_length=500)
    preload_inventory:str=Field(default='',max_length=500)
    _names=field_validator('containers')(DeploymentCreate.container_names.__func__)

    @field_validator('preload_playbook','preload_inventory')
    @classmethod
    def preload_path(cls,value):return absolute_path(value) if value else value

    @model_validator(mode='after')
    def selected(self):
        if self.environment=='k8s':
            if self.containers or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',self.workload):raise ValueError('请选择明确的K8s工作负载和容器')
        elif not self.containers:raise ValueError('请选择全部目标Docker容器')
        return self


class TargetCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100)
    kind:Literal['model','image']
    target:dict

    @model_validator(mode='after')
    def config(self):
        if self.kind=='model':
            value=DeploymentCreate(run_id=UUID(int=0),artifact_path='model.engine',**self.target)
            self.target=value.model_dump(mode='json',exclude={'run_id','artifact_path'})
        else:self.target=ImageTarget(**self.target).model_dump(mode='json')
        return self


class BatchCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    kind:Literal['model','image']
    target_ids:list[UUID]=Field(min_length=1,max_length=50)
    release_id:UUID|None=None
    image:str=Field(default='',max_length=500)

    @model_validator(mode='after')
    def selection(self):
        if len(self.target_ids)!=len(set(self.target_ids)):raise ValueError('服务器目标不能重复选择')
        if self.kind=='model':
            if not self.release_id or self.image:raise ValueError('模型下发请选择一个已归档版本')
        elif self.release_id or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:@-]{0,499}',self.image) or not (':' in self.image.rsplit('/',1)[-1] or '@sha256:' in self.image):
            raise ValueError('镜像须含明确的版本tag或sha256摘要，不能包含命令或凭据')
        return self
