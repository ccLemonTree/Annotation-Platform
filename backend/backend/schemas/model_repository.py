from pathlib import PurePosixPath
import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from services.model_deployment_executor import relative

EXTENSIONS={'pt':('.pt','.pth'),'onnx':('.onnx',),'tensorrt':('.engine','.plan'),'python':('.py',),'huggingface':('.json',)}


def repository_path(value):
    relative(value)
    for part in value.split('/'):
        if part.rstrip(' .')!=part or re.search(r'[<>"|?*\x7f]',part) or re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])',part.split('.')[0],re.I):
            raise ValueError('模型文件名包含跨平台不支持的字符或保留名称')
    return value


class RepositoryMetadata(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100,pattern=r'^[^\x00-\x1f\x7f]+$')
    group_name:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')
    version:str=Field(min_length=1,max_length=100,pattern=r'^[^\x00-\x1f\x7f]+$')
    format:Literal['pt','onnx','tensorrt','python','huggingface']
    gpu_model:str=Field(default='',max_length=200,pattern=r'^[^\x00-\x1f\x7f]*$')
    tensorrt_version:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')
    batch_profile:str=Field(default='',max_length=64)
    entry_file:str=Field(min_length=1,max_length=500)

    @field_validator('entry_file')
    @classmethod
    def path(cls,value):return repository_path(value)

    @field_validator('batch_profile')
    @classmethod
    def batch(cls,value):
        if not value:return ''
        parts=value.split(':')
        if parts[0] not in ('fixed','dynamic') or len(parts)!=(2 if parts[0]=='fixed' else 4) or any(not re.fullmatch('[0-9]{1,4}',p) for p in parts[1:]):raise ValueError('batch规格无效')
        values=[int(p) for p in parts[1:]]
        if any(not 1<=n<=1024 for n in values) or values!=sorted(values):raise ValueError('batch必须为1至1024，且min ≤ opt ≤ max')
        return ':'.join([parts[0],*[str(n) for n in values]])

    @model_validator(mode='after')
    def artifact(self):
        if PurePosixPath(self.entry_file).suffix.lower() not in EXTENSIONS[self.format]:raise ValueError('主文件扩展名与模型格式不匹配')
        if self.format=='huggingface' and PurePosixPath(self.entry_file).name not in ('config.json','adapter_config.json'):
            raise ValueError('完整模型或 LoRA 必须包含对应的配置文件')
        if self.format=='python' and (PurePosixPath(self.entry_file).name!='model.py' or not re.fullmatch('[0-9]+',PurePosixPath(self.entry_file).parent.name)):
            raise ValueError('Python backend主文件须为数字版本目录下的model.py，例如1/model.py')
        if self.format!='tensorrt' and (self.gpu_model or self.tensorrt_version):raise ValueError('仅TensorRT模型可指定GPU型号和TensorRT版本')
        if self.format!='tensorrt' and self.batch_profile:raise ValueError('仅TensorRT模型可指定batch规格')
        return self


class RepositoryImport(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100)
    group_name:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')
    version:str=Field(min_length=1,max_length=100)
    format:Literal['pt','onnx','tensorrt','huggingface']
    gpu_model:str=Field(default='',max_length=200)
    run_id:UUID
    artifact_path:str=Field(min_length=1,max_length=500)

    @model_validator(mode='after')
    def metadata(self):
        RepositoryMetadata(name=self.name,version=self.version,format=self.format,gpu_model=self.gpu_model,entry_file=self.artifact_path)
        return self


class RepositoryUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100,pattern=r'^[^\x00-\x1f\x7f]+$')
    group_name:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')
    version:str=Field(min_length=1,max_length=100,pattern=r'^[^\x00-\x1f\x7f]+$')
    description:str=Field(default='',max_length=2000,pattern=r'^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$')
    gpu_model:str=Field(default='',max_length=200,pattern=r'^[^\x00-\x1f\x7f]*$')
    tensorrt_version:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')


class RepositoryDownloadFilters(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    q:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')
    group_name:str=Field(default='',max_length=100,pattern=r'^[^\x00-\x1f\x7f]*$')
    format:Literal['pt','onnx','tensorrt','python','huggingface']|None=None
    gpu_model:str=Field(default='',max_length=200,pattern=r'^[^\x00-\x1f\x7f]*$')


class RepositoryBatchDownload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ids:list[UUID]|None=Field(default=None,min_length=1,max_length=100)
    filters:RepositoryDownloadFilters|None=None

    @model_validator(mode='after')
    def selection(self):
        if (self.ids is None)==(self.filters is None):raise ValueError('请选择版本ID或筛选条件，不能同时提交')
        if self.ids is not None and len(set(self.ids))!=len(self.ids):raise ValueError('模型版本ID不能重复')
        return self
