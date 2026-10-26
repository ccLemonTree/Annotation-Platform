"""Validated options for independent export/merge jobs."""
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from schemas.training_run import absolute_path,default_output_root

EXPORT_IMAGE='aisfh5.com:9091/triton-server/triton-server:25.11_py3_vllm'
OUTPUT_KINDS=('model_export','model_merge')


class ModelOutputCreate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    format:Literal['onnx','tensorrt','merge']
    server_id:str=Field(default='default',max_length=36)
    gpu_uuid:str=Field(pattern=r'^GPU-[A-Za-z0-9-]{1,80}$')
    source_gpu_uuid:str=Field(default='',pattern=r'^GPU-[A-Za-z0-9-]{1,80}$|^$')
    onnx_image:str=Field(default='',max_length=500,pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$|^$')
    source_path:str=Field(min_length=2,max_length=500)
    base_model_path:str=Field(default='',max_length=500)
    output_root:str=Field(default_factory=default_output_root,max_length=450)
    image:str=Field(default='',max_length=500,pattern=r'^[A-Za-z0-9][A-Za-z0-9./_:@-]*$|^$')
    batch:int=Field(default=1,ge=1,le=1024,strict=True)
    dynamic_batch:bool=False
    min_batch:int=Field(default=1,ge=1,le=1024,strict=True)
    opt_batch:int=Field(default=1,ge=1,le=1024,strict=True)
    max_batch:int=Field(default=8,ge=1,le=1024,strict=True)
    image_size:int|None=Field(default=None,ge=32,le=8192,strict=True)
    fp16:bool=True
    opset:int=Field(default=13,ge=13,le=21,strict=True)
    simplify:bool=True
    input_name:str=Field(default='input',pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
    output_name:str=Field(default='output',pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
    trust_checkpoint:bool=False
    model_type:str=Field(default='',max_length=100,pattern=r'^[A-Za-z0-9_-]*$')
    torch_dtype:Literal['float16','bfloat16','float32']='float16'

    @field_validator('source_path','base_model_path','output_root')
    @classmethod
    def path(cls,value):
        if not value:return value
        value=absolute_path(value)
        if value=='/' or PurePosixPath(value).parent==PurePosixPath('/'):
            raise ValueError('请使用具体数据目录，不能挂载主机根目录')
        if value=='/output' or value.startswith('/output/'):raise ValueError('不能覆盖容器任务输出目录')
        return value

    @model_validator(mode='after')
    def valid(self):
        if self.server_id!='default':UUID(self.server_id)
        if self.dynamic_batch and not self.min_batch<=self.opt_batch<=self.max_batch:
            raise ValueError('动态batch须满足 min ≤ opt ≤ max')
        if self.format=='merge' and self.dynamic_batch:raise ValueError('LoRA合并不使用动态batch')
        if self.input_name==self.output_name:raise ValueError('输入与输出名称不能相同')
        return self
